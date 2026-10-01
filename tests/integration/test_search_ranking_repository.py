"""
Integration tests: ImageRepository.search ranks text queries by relevance (recency as tie-breaker),
pages a ranked listing with a score cursor, leaves no-q browsing untouched and logs the scale guard.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import logging
import uuid
from datetime import datetime, timedelta

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from repository.search_ranking import RankingWeights
from Storage.models import DescriptionNoteLemma, Image, ImageTag, OCRLemma

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image(db_session, minutes=0):
    image = Image(filename=f"{uuid.uuid4()}.jpg", created_at=_T0 + timedelta(minutes=minutes))
    db_session.add(image)
    await db_session.flush()
    return image


async def _search(repo, q, **kw):
    return await repo.search(
        q=q, tags=kw.pop("tags", {}), cursor_created_at=kw.pop("cursor_created_at", None),
        cursor_id=kw.pop("cursor_id", None), limit=kw.pop("limit", 50), **kw,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_note_hit_outranks_ocr_hit_outranks_older_equal_scores_by_recency(db_session):
    older_note = await _image(db_session, minutes=0)
    newer_ocr = await _image(db_session, minutes=10)
    newest_ocr = await _image(db_session, minutes=20)
    db_session.add_all([
        DescriptionNoteLemma(image_id=older_note.id, lemma="zebracorn"),
        OCRLemma(image_id=newer_ocr.id, lemma="zebracorn"),
        OCRLemma(image_id=newest_ocr.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    ordered = [r.id for r in rows]
    # note (1.0) first even though it is the oldest; the two equal ocr hits (0.9) fall back to recency
    assert ordered == [older_note.id, newest_ocr.id, newer_ocr.id]
    assert rows[0].score > rows[1].score == rows[2].score


@pytest.mark.asyncio(loop_scope="session")
async def test_no_q_keeps_recency_order_and_plain_rows(db_session):
    older = await _image(db_session, minutes=0)
    newer = await _image(db_session, minutes=5)

    rows, _ = await _search(ImageRepository(db_session), None)

    ids = [r.id for r in rows]
    assert ids.index(newer.id) < ids.index(older.id)
    assert not hasattr(rows[0], "score")


@pytest.mark.asyncio(loop_scope="session")
async def test_ranked_listing_pages_every_match_once_in_non_increasing_score_order(db_session):
    images = []
    for i in range(7):
        image = await _image(db_session, minutes=i)
        images.append(image)
        # alternate sources so scores differ: note (1.0) vs ocr (0.9)
        if i % 2 == 0:
            db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="zebracorn"))
        else:
            db_session.add(OCRLemma(image_id=image.id, lemma="zebracorn"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    limit = 3
    seen, scores = [], []
    cursor = {}
    for _ in range(10):
        rows, _ = await _search(repo, "zebracorn", limit=limit, **cursor)
        page = rows[:limit]
        seen += [r.id for r in page]
        scores += [r.score for r in page]
        if len(rows) <= limit:
            break
        last = page[-1]
        cursor = {"cursor_score": last.score, "cursor_created_at": last.created_at, "cursor_id": last.id}

    assert sorted(seen) == sorted(i.id for i in images)         # every match exactly once
    assert len(seen) == len(set(seen)) == 7
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_ranked_query_ignores_a_recency_format_cursor_and_restarts(db_session):
    first = await _image(db_session, minutes=1)
    second = await _image(db_session, minutes=2)
    db_session.add_all([
        OCRLemma(image_id=first.id, lemma="zebracorn"),
        OCRLemma(image_id=second.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(
        ImageRepository(db_session), "zebracorn", cursor_created_at=second.created_at, cursor_id=second.id,
    )

    assert {r.id for r in rows} == {first.id, second.id}   # cursor without a score: restart at page 1


@pytest.mark.asyncio(loop_scope="session")
async def test_unranked_query_ignores_a_ranked_cursor(db_session):
    image = await _image(db_session, minutes=3)

    rows, _ = await _search(
        ImageRepository(db_session), None,
        cursor_score=0.9, cursor_created_at=image.created_at - timedelta(days=3650), cursor_id=uuid.uuid4(),
    )

    assert image.id in {r.id for r in rows}                # would be filtered out if the cursor were applied


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_facet_filter_intersects_with_scored_matches_and_facets_unchanged(db_session):
    tagged = await _image(db_session, minutes=1)
    untagged = await _image(db_session, minutes=2)
    db_session.add_all([
        OCRLemma(image_id=tagged.id, lemma="zebracorn"),
        OCRLemma(image_id=untagged.id, lemma="zebracorn"),
        ImageTag(image_id=tagged.id, key="animal", value="cat", source="rules"),
    ])
    await db_session.flush()

    rows, facets = await _search(ImageRepository(db_session), "zebracorn", tags={"animal": {"cat"}})

    assert {r.id for r in rows} == {tagged.id}
    assert facets["animal"]["cat"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_scale_guard_logs_a_warning_above_warn_match_count(db_session, monkeypatch, caplog):
    for i in range(3):
        image = await _image(db_session, minutes=i)
        db_session.add(OCRLemma(image_id=image.id, lemma="zebracorn"))
    await db_session.flush()
    tiny = RankingWeights(
        source={"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6},
        tier={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4},
        warn_match_count=2,
    )
    monkeypatch.setattr("Backend.app.repositories.image_repository.get_weights", lambda: tiny)
    monkeypatch.setattr("repository.ocr_lemmas.get_weights", lambda: tiny)

    with caplog.at_level(logging.WARNING, logger="Backend.app.repositories.image_repository"):
        await _search(ImageRepository(db_session), "zebracorn")

    messages = [r.getMessage() for r in caplog.records if "ranked search matched" in r.getMessage()]
    assert len(messages) == 1
    assert "matched 3 images" in messages[0]
    assert "query length 9" in messages[0]      # len("zebracorn")
    assert "ms" in messages[0]
