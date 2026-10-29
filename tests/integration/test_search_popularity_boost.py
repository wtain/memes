"""
Integration tests: the capped popularity term in text-search ranking (P5,
docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p5-ranking-and-trending-design.md).
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta

import pytest

import Backend.app.repositories.image_repository as image_repository
from Backend.app.repositories.image_repository import ImageRepository
from repository.search_ranking import PopularityBoost, get_popularity_boost, get_weights
from Storage.models import DescriptionNoteLemma, ImageTag, Image, OCRLemma

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image(db_session, popularity=1, minutes=0):
    image = Image(
        filename=f"{uuid.uuid4()}.jpg", created_at=_T0 + timedelta(minutes=minutes), popularity=popularity,
    )
    db_session.add(image)
    await db_session.flush()
    return image


async def _search(repo, q, **kw):
    return await repo.search(
        q=q, tags=kw.pop("tags", {}), cursor_created_at=kw.pop("cursor_created_at", None),
        cursor_id=kw.pop("cursor_id", None), limit=kw.pop("limit", 50), **kw,
    )


def _weight(monkeypatch, weight):
    boost = PopularityBoost(weight=weight, max_log2=get_popularity_boost().max_log2)
    monkeypatch.setattr(image_repository, "get_popularity_boost", lambda: boost)


@pytest.mark.asyncio(loop_scope="session")
async def test_more_popular_of_two_equal_matches_ranks_first_despite_being_older(db_session):
    popular = await _image(db_session, popularity=8, minutes=0)
    plain = await _image(db_session, popularity=1, minutes=10)
    db_session.add_all([OCRLemma(image_id=popular.id, lemma="zebracorn"), OCRLemma(image_id=plain.id, lemma="zebracorn")])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert [r.id for r in rows] == [popular.id, plain.id]
    assert rows[0].score == round(0.9 + get_popularity_boost().weight * 3, 6)
    assert rows[1].score == 0.9


@pytest.mark.asyncio(loop_scope="session")
async def test_weight_zero_restores_the_unboosted_order(db_session, monkeypatch):
    popular = await _image(db_session, popularity=8, minutes=0)
    newer = await _image(db_session, popularity=1, minutes=10)
    db_session.add_all([OCRLemma(image_id=popular.id, lemma="zebracorn"), OCRLemma(image_id=newer.id, lemma="zebracorn")])
    await db_session.flush()
    _weight(monkeypatch, 0)

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert [r.id for r in rows] == [newer.id, popular.id]          # recency tie-break only
    assert rows[0].score == rows[1].score == 0.9


@pytest.mark.asyncio(loop_scope="session")
async def test_moderately_popular_ocr_hit_stays_below_a_note_exact_hit(db_session):
    # Scores only: 0.9 + 0.03 * 3 = 0.99 < 1.0. This does NOT exercise the cap (see the tests below).
    note = await _image(db_session, popularity=1, minutes=0)
    popular = await _image(db_session, popularity=8, minutes=10)
    db_session.add_all([
        DescriptionNoteLemma(image_id=note.id, lemma="zebracorn"),
        OCRLemma(image_id=popular.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert [r.id for r in rows] == [note.id, popular.id]
    assert rows[1].score == round(0.9 + get_popularity_boost().weight * 3, 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_cap_keeps_a_viral_tag_hit_below_a_note_exact_hit(db_session):
    # Capped: 0.8 + 0.03 * 5 = 0.95 < 1.0. Uncapped (popularity 2**20 = 20 doublings): 0.8 + 0.6 = 1.4 would win.
    note = await _image(db_session, popularity=1, minutes=10)
    viral = await _image(db_session, popularity=2 ** 20, minutes=0)
    db_session.add_all([
        DescriptionNoteLemma(image_id=note.id, lemma="zebracorn"),
        ImageTag(image_id=viral.id, key="animal", value="zebracorn", source="rules"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert [r.id for r in rows] == [note.id, viral.id]
    assert rows[1].score == round(0.8 + 0.03 * 5, 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_cap_keeps_two_viral_ocr_tokens_below_two_note_tokens(db_session):
    # Capped: 2 * 0.9 + 0.15 = 1.95 < 2.0. Uncapped (20 doublings): 1.8 + 0.6 = 2.4 would win.
    notes = await _image(db_session, popularity=1, minutes=10)
    viral = await _image(db_session, popularity=2 ** 20, minutes=0)
    db_session.add_all([
        DescriptionNoteLemma(image_id=notes.id, lemma="zebracorn"),
        DescriptionNoteLemma(image_id=notes.id, lemma="flamingo"),
        OCRLemma(image_id=viral.id, lemma="zebracorn"),
        OCRLemma(image_id=viral.id, lemma="flamingo"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn flamingo")

    assert [r.id for r in rows] == [notes.id, viral.id]
    assert rows[1].score == round(1.8 + 0.03 * 5, 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_exact_hit_at_the_cap_may_overtake_a_note_exact_hit_of_popularity_one(db_session):
    # Spec L1: near-equal hits reorder. 0.9 + 0.15 = 1.05 > 1.0.
    note = await _image(db_session, popularity=1, minutes=10)
    viral = await _image(db_session, popularity=2 ** 20, minutes=0)
    db_session.add_all([
        DescriptionNoteLemma(image_id=note.id, lemma="zebracorn"),
        OCRLemma(image_id=viral.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert [r.id for r in rows] == [viral.id, note.id]
    assert rows[0].score == round(0.9 + 0.03 * 5, 6)


def test_the_cap_cannot_lift_a_fuzzy_ocr_hit_above_a_note_exact_hit():
    # Review Focus 1, worst case from the shipped weights: ocr fuzzy + full cap < note exact.
    weights, boost = get_weights(), get_popularity_boost()
    assert weights.hit("ocr", "fuzzy") + boost.weight * boost.max_log2 < weights.hit("note", "exact")


@pytest.mark.asyncio(loop_scope="session")
async def test_popularity_beyond_the_cap_changes_nothing(db_session):
    capped = await _image(db_session, popularity=2 ** 5, minutes=0)
    huge = await _image(db_session, popularity=2 ** 20, minutes=10)
    db_session.add_all([OCRLemma(image_id=capped.id, lemma="zebracorn"), OCRLemma(image_id=huge.id, lemma="zebracorn")])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    assert rows[0].score == rows[1].score
    assert [r.id for r in rows] == [huge.id, capped.id]            # equal scores: recency decides


@pytest.mark.asyncio(loop_scope="session")
async def test_equal_boosted_scores_page_without_repeats_or_gaps(db_session):
    images = []
    for i in range(7):
        image = await _image(db_session, popularity=8 if i % 2 else 1, minutes=i)
        images.append(image)
        db_session.add(OCRLemma(image_id=image.id, lemma="zebracorn"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    limit = 2
    seen, cursor = [], {}
    for _ in range(10):
        rows, _ = await _search(repo, "zebracorn", limit=limit, **cursor)
        page = rows[:limit]
        seen += [r.id for r in page]
        if len(rows) <= limit:
            break
        last = page[-1]
        cursor = {"cursor_score": last.score, "cursor_created_at": last.created_at, "cursor_id": last.id}

    assert len(seen) == len(set(seen)) == 7
    # popular ones (odd minutes) first, newest first within equal scores, then the rest
    assert seen == [images[i].id for i in (5, 3, 1, 6, 4, 2, 0)]


@pytest.mark.asyncio(loop_scope="session")
async def test_cursor_from_page_one_reproduces_the_exact_float_on_page_two(db_session):
    first = await _image(db_session, popularity=3, minutes=1)     # 0.9 + 0.03 * log2(3): not a clean binary float
    second = await _image(db_session, popularity=3, minutes=2)
    db_session.add_all([OCRLemma(image_id=first.id, lemma="zebracorn"), OCRLemma(image_id=second.id, lemma="zebracorn")])
    await db_session.flush()
    repo = ImageRepository(db_session)

    rows, _ = await _search(repo, "zebracorn", limit=1)
    page_one = rows[0]
    rows, _ = await _search(
        repo, "zebracorn", limit=1,
        cursor_score=page_one.score, cursor_created_at=page_one.created_at, cursor_id=page_one.id,
    )

    assert page_one.id == second.id
    assert [r.id for r in rows] == [first.id]
    assert rows[0].score == page_one.score == round(0.9 + get_popularity_boost().weight * 1.5849625007211563, 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_query_without_text_is_unchanged(db_session):
    older_popular = await _image(db_session, popularity=100, minutes=0)
    newer = await _image(db_session, popularity=1, minutes=5)

    rows, _ = await _search(ImageRepository(db_session), None)

    ids = [r.id for r in rows]
    assert ids.index(newer.id) < ids.index(older_popular.id)
    assert not hasattr(rows[0], "score")
