import uuid

import pytest
from sqlalchemy import delete, select

from repository.comics import ComicsRepository
from repository.comics_candidates import CandidateError, ComicsCandidatesRepository, candidate_key
from Storage.models import (CandidateState, Comics, ComicsCandidate, ComicsPanel, Image, ImageExtras)


async def _image(s, status="active", name=None):
    img = Image(filename=name or f"{uuid.uuid4()}.png", status=status, width=100, height=80)
    s.add(img)
    await s.flush()
    return img


async def _group(s, repo, count=2, distance=0.1):
    images = [await _image(s) for _ in range(count)]
    candidate_id = await repo.insert_group([i.id for i in images], 3.0, distance, "v1")
    return candidate_id, images


async def _listed(repo):
    return [row.id for row in await repo.list_open(0, 100)]


def test_candidate_key_ignores_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert candidate_key([a, b]) == candidate_key([b, a])
    assert candidate_key([a, b]) != candidate_key([a])
    assert len(candidate_key([a, b])) == 32


@pytest.mark.asyncio(loop_scope="session")
async def test_insert_group_stores_images_in_order_and_a_repeat_returns_none(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, images = await _group(db_session, repo, count=3)
    assert candidate_id is not None
    stored = await repo.get(candidate_id)
    assert stored.state == CandidateState.OPEN and stored.decided_at is None and stored.comics_id is None
    assert [r.image_id for r in stored.images] == [i.id for i in images]
    assert await repo.insert_group([i.id for i in reversed(images)], 1.0, 0.2, "v2") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_overlaps_decided(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, b) = await _group(db_session, repo)
    c = await _image(db_session)
    assert await repo.overlaps_decided([a.id, b.id]) is False  # still open
    await repo.decide(candidate_id, CandidateState.DISMISSED)
    assert await repo.overlaps_decided([a.id, b.id]) is True  # the same pair
    assert await repo.overlaps_decided([a.id, b.id, c.id]) is True  # a superset
    assert await repo.overlaps_decided([a.id, c.id]) is False  # one shared image only
    assert await repo.overlaps_decided([c.id, (await _image(db_session)).id]) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_overlaps_decided_counts_accepted_too(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, b) = await _group(db_session, repo)
    await repo.decide(candidate_id, CandidateState.ACCEPTED)
    assert await repo.overlaps_decided([b.id, a.id]) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_carries_images_in_order(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, images = await _group(db_session, repo, count=3)
    row = next(r for r in await repo.list_open(0, 100) if r.id == candidate_id)
    assert [i.id for i in row.images] == [i.id for i in images]
    assert (row.images[0].filename, row.images[0].width, row.images[0].height) == (images[0].filename, 100, 80)
    assert (row.max_gap_seconds, row.max_distance, row.detector_version) == (3.0, 0.1, "v1")


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_orders_by_max_distance_and_pages(db_session):
    repo = ComicsCandidatesRepository(db_session)
    weak, _ = await _group(db_session, repo, distance=0.14)
    strong, _ = await _group(db_session, repo, distance=0.07)
    mid, _ = await _group(db_session, repo, distance=0.1)
    ids = await _listed(repo)
    assert [i for i in ids if i in (weak, strong, mid)] == [strong, mid, weak]
    first_two = [r.id for r in await repo.list_open(0, 2)]
    rest = [r.id for r in await repo.list_open(2, 100)]
    assert first_two + rest == ids


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_hides_decided_candidates(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, _ = await _group(db_session, repo)
    await repo.decide(candidate_id, CandidateState.DISMISSED)
    assert candidate_id not in await _listed(repo)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_hides_a_group_with_a_flagged_image(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, _) = await _group(db_session, repo)
    assert candidate_id in await _listed(repo)
    db_session.add(ImageExtras(image_id=a.id, flagged=True))
    await db_session.flush()
    assert candidate_id not in await _listed(repo)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_shows_a_group_whose_image_has_an_unflagged_extras_row(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, _) = await _group(db_session, repo)
    db_session.add(ImageExtras(image_id=a.id, flagged=False))
    await db_session.flush()
    assert candidate_id in await _listed(repo)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_hides_a_group_with_an_inactive_image(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, _) = await _group(db_session, repo)
    a.status = "rejected"
    await db_session.flush()
    assert candidate_id not in await _listed(repo)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_hides_a_group_with_an_image_in_a_comics(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, _) = await _group(db_session, repo)
    comics = await ComicsRepository(db_session).create("Holds one")
    db_session.add(ComicsPanel(comics_id=comics.id, image_id=a.id, position=0, source_image_id=a.id,
                               source_filename=a.filename))
    await db_session.flush()
    assert candidate_id not in await _listed(repo)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_open_hides_a_group_with_a_deleted_image(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, (a, _) = await _group(db_session, repo)
    await db_session.execute(delete(Image).where(Image.id == a.id))
    await db_session.flush()
    assert candidate_id not in await _listed(repo)
    # the candidate row stays, with its one remaining image
    assert (await db_session.execute(select(ComicsCandidate.id).where(ComicsCandidate.id == candidate_id))).first()


@pytest.mark.asyncio(loop_scope="session")
async def test_decide_records_state_time_and_comics(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, _ = await _group(db_session, repo)
    comics = await ComicsRepository(db_session).create("Made from candidate")
    decided = await repo.decide(candidate_id, CandidateState.ACCEPTED, comics.id)
    assert decided.state == CandidateState.ACCEPTED
    assert decided.comics_id == comics.id
    assert decided.decided_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_the_comics_keeps_the_decision(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, _ = await _group(db_session, repo)
    comics = await ComicsRepository(db_session).create("Short lived")
    await repo.decide(candidate_id, CandidateState.ACCEPTED, comics.id)
    await db_session.execute(delete(Comics).where(Comics.id == comics.id))
    await db_session.flush()
    kept = await repo.get(candidate_id)
    assert kept.state == CandidateState.ACCEPTED and kept.comics_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_decide_twice_raises_candidate_decided(db_session):
    repo = ComicsCandidatesRepository(db_session)
    candidate_id, _ = await _group(db_session, repo)
    await repo.decide(candidate_id, CandidateState.DISMISSED)
    with pytest.raises(CandidateError) as exc:
        await repo.decide(candidate_id, CandidateState.ACCEPTED)
    assert exc.value.code == "candidate_decided"


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_candidate_raises_not_found(db_session):
    repo = ComicsCandidatesRepository(db_session)
    for call in (repo.get(uuid.uuid4()), repo.decide(uuid.uuid4(), CandidateState.DISMISSED)):
        with pytest.raises(CandidateError) as exc:
            await call
        assert exc.value.code == "not_found"
