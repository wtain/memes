import math
import uuid

import pytest
from sqlalchemy import func, select

from batch.detect_comics_candidates import DETECTOR_VERSION, run
from repository.comics import ComicsRepository
from repository.comics_candidates import ComicsCandidatesRepository
from Storage.models import (CandidateState, Comics, ComicsCandidate, ComicsCandidateImage, ComicsPanel, Embedding,
                            Image, ImageExtras)

SETTINGS = dict(gap_seconds=5.0, min_distance=0.06, max_distance=0.15, max_group=6, max_candidates=500)


def _vector(distance_from_axis_0: float) -> list[float]:
    """A unit vector whose cosine distance to e0 is the given value."""
    cosine = 1.0 - distance_from_axis_0
    vec = [0.0] * 512
    vec[0] = cosine
    vec[1] = math.sqrt(1.0 - cosine * cosine)
    return vec


async def _image(s, name, distance=0.0, status="active", embedded=True):
    img = Image(filename=name, status=status, width=100, height=80)
    s.add(img)
    await s.flush()
    if embedded:
        s.add(Embedding(image_id=img.id, embedding=_vector(distance)))
        await s.flush()
    return img


async def _go(s, **overrides):
    metrics = await run(s, **{**SETTINGS, **overrides})
    await s.flush()
    return metrics.counters_dict()


async def _candidates(s):
    return (await s.execute(select(ComicsCandidate))).scalars().all()


async def _pair(s, tag, second=0, distance=0.0998):
    """The known story: two files 2 s apart, near each other in CLIP space (the second on the other axis side)."""
    a = await _image(s, f"IMG_2020{tag}_110706_156.jpg", 0.0)
    b = await _image(s, f"IMG_2020{tag}_1107{second + 6:02d}_392.jpg", distance)
    return a, b


@pytest.mark.asyncio(loop_scope="session")
async def test_known_pair_becomes_one_open_candidate_with_its_signals(db_session):
    a, b = await _pair(db_session, "0923", second=2)
    stats = await _go(db_session)
    (candidate,) = await _candidates(db_session)
    assert candidate.state == CandidateState.OPEN and candidate.detector_version == str(DETECTOR_VERSION)
    assert candidate.max_gap_seconds == pytest.approx(2.0) and candidate.max_distance == pytest.approx(0.0998, abs=1e-4)
    rows = (await db_session.execute(
        select(ComicsCandidateImage.image_id).order_by(ComicsCandidateImage.position))).scalars().all()
    assert rows == [a.id, b.id]
    assert stats["groups_inserted"] == 1 and stats["pairs_linked"] == 1 and stats["images_considered"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_inserts_nothing_new(db_session):
    await _pair(db_session, "0923", second=2)
    await _go(db_session)
    stats = await _go(db_session)
    assert stats["groups_inserted"] == 0 and stats["groups_skipped_existing"] == 1
    assert len(await _candidates(db_session)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_ineligible_images_are_not_considered(db_session):
    await _pair(db_session, "0923", second=2)
    flagged_a, flagged_b = await _pair(db_session, "0924", second=2)
    db_session.add(ImageExtras(image_id=flagged_a.id, flagged=True))
    pending_a, _ = await _pair(db_session, "0925", second=2)
    pending_a.status = "pending"
    in_comics_a, _ = await _pair(db_session, "0926", second=2)
    comics = await ComicsRepository(db_session).create(f"c-{uuid.uuid4()}")
    db_session.add(ComicsPanel(comics_id=comics.id, image_id=in_comics_a.id, position=0,
                               source_image_id=in_comics_a.id, source_filename=in_comics_a.filename))
    a = await _image(db_session, "IMG_20200927_110706_1.jpg", 0.0, embedded=False)  # no embedding
    await _image(db_session, "IMG_20200927_110707_2.jpg", 0.1)
    await _image(db_session, "no_timestamp_a.jpg", 0.0)
    await _image(db_session, "no_timestamp_b.jpg", 0.1)
    await db_session.flush()
    stats = await _go(db_session)
    assert stats["groups_inserted"] == 1
    assert stats["images_considered"] == 6  # the first pair, the three unflagged/active/free partners, one timestamped
    assert a.id not in (await db_session.execute(select(ComicsCandidateImage.image_id))).scalars().all()


@pytest.mark.asyncio(loop_scope="session")
async def test_distance_comes_from_embeddings_only_inside_the_window(db_session):
    await _image(db_session, "IMG_20200923_110706_1.jpg", 0.0)
    await _image(db_session, "IMG_20200923_110720_2.jpg", 0.1)  # 14 s later: outside the window, never compared
    await _image(db_session, "IMG_20200923_110707_3.jpg", 0.0)  # distance 0: a duplicate, below min_distance
    stats = await _go(db_session)
    assert stats["groups_found"] == 0 and stats["pairs_linked"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_group_is_dropped(db_session):
    for i in range(4):
        await _image(db_session, f"IMG_20200923_11070{i}_x.jpg", 0.1 if i % 2 else 0.0)
    stats = await _go(db_session, max_group=3)
    assert stats["groups_dropped_oversized"] == 1 and stats["groups_inserted"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_group_overlapping_a_decided_candidate_is_skipped(db_session):
    a, b = await _pair(db_session, "0923", second=2)
    repo = ComicsCandidatesRepository(db_session)
    candidate_id = await repo.insert_group([a.id, b.id], 2.0, 0.0998, "0")
    await repo.decide(candidate_id, CandidateState.DISMISSED)
    c = await _image(db_session, "IMG_20200923_110709_9.jpg", 0.1)  # joins the dismissed pair
    stats = await _go(db_session)
    assert stats["groups_skipped_decided"] == 1 and stats["groups_inserted"] == 0
    assert len(await _candidates(db_session)) == 1
    assert c.id not in (await db_session.execute(select(ComicsCandidateImage.image_id))).scalars().all()


@pytest.mark.asyncio(loop_scope="session")
async def test_cap_limits_insertions_and_keeps_the_strongest(db_session):
    await _pair(db_session, "0921", second=2, distance=0.14)
    await _pair(db_session, "0922", second=2, distance=0.07)
    await _pair(db_session, "0923", second=2, distance=0.11)
    stats = await _go(db_session, max_candidates=2)
    assert stats["groups_inserted"] == 2
    kept = sorted(c.max_distance for c in await _candidates(db_session))
    assert kept == [pytest.approx(0.07, abs=1e-4), pytest.approx(0.11, abs=1e-4)]


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_else_in_the_database_changes(db_session):
    a, b = await _pair(db_session, "0923", second=2)
    before = [(await db_session.execute(select(func.count()).select_from(t))).scalar_one()
              for t in (Image, ImageExtras, Embedding, Comics, ComicsPanel)]
    await _go(db_session)
    after = [(await db_session.execute(select(func.count()).select_from(t))).scalar_one()
             for t in (Image, ImageExtras, Embedding, Comics, ComicsPanel)]
    assert before == after
    await db_session.refresh(a)
    assert a.status == "active"
