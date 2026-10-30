"""
Integration tests: GET /api/images/trending data path (ImageService.get_trending over the real repository).
Spec: docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p5-ranking-and-trending-design.md.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from Storage.models import Image, ImageExtras, PopularityAbsorption

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image(db_session, popularity, minutes=0, status="active", flagged=None):
    image = Image(
        filename=f"{uuid.uuid4()}.jpg", created_at=_T0 + timedelta(minutes=minutes),
        popularity=popularity, status=status,
    )
    db_session.add(image)
    await db_session.flush()
    if flagged is not None:
        db_session.add(ImageExtras(image_id=image.id, flagged=flagged))
        await db_session.flush()
    return image


def _service(db_session):
    return ImageService(ImageRepository(db_session), AsyncMock(), AsyncMock())


async def _all_pages(service, limit):
    ids, cursor = [], None
    for _ in range(500):
        page = await service.get_trending(cursor=cursor, limit=limit)
        ids += [i.meme.id for i in page.items]
        if not page.hasNext:
            return ids
        cursor = page.nextCursor
    raise AssertionError("paging did not terminate")


@pytest.mark.asyncio(loop_scope="session")
async def test_orders_by_popularity_then_created_at_then_id(db_session):
    low = await _image(db_session, 3, minutes=0)
    old_high = await _image(db_session, 90001, minutes=1)
    new_high = await _image(db_session, 90001, minutes=2)
    top = await _image(db_session, 90002, minutes=0)
    service = _service(db_session)

    page = await service.get_trending(cursor=None, limit=100)

    ids = [i.meme.id for i in page.items]
    mine = [str(x.id) for x in (top, new_high, old_high, low)]
    assert [i for i in ids if i in mine] == mine
    by_id = {i.meme.id: i.popularity for i in page.items}
    assert by_id[str(top.id)] == 90002 and by_id[str(low.id)] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_same_timestamp_ties_break_by_id_descending(db_session):
    a = await _image(db_session, 90003, minutes=5)
    b = await _image(db_session, 90003, minutes=5)
    service = _service(db_session)

    ids = await _all_pages(service, 100)

    pair = sorted([a.id, b.id], reverse=True)
    assert [i for i in ids if i in {str(a.id), str(b.id)}] == [str(x) for x in pair]


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_flagged_pending_discarded_and_below_threshold(db_session):
    kept = await _image(db_session, 4, minutes=1)
    no_extras_row = await _image(db_session, 4, minutes=2)
    unflagged_row = await _image(db_session, 4, minutes=3, flagged=False)
    at_threshold = await _image(db_session, 2, minutes=4)
    flagged = await _image(db_session, 99, minutes=5, flagged=True)
    pending = await _image(db_session, 99, minutes=6, status="pending")
    cut = await _image(db_session, 99, minutes=7, status="cut_proposed")
    discarded = await _image(db_session, 0, minutes=8)
    below = await _image(db_session, 1, minutes=9)
    service = _service(db_session)

    ids = set(await _all_pages(service, 100))

    for image in (kept, no_extras_row, unflagged_row, at_threshold):
        assert str(image.id) in ids
    for image in (flagged, pending, cut, discarded, below):
        assert str(image.id) not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_paging_over_equal_popularity_yields_every_image_exactly_once(db_session):
    mine = [await _image(db_session, 90010, minutes=m % 3) for m in range(7)]   # equal popularity, repeated times
    service = _service(db_session)

    ids = await _all_pages(service, 2)

    assert len(ids) == len(set(ids))
    assert {str(m.id) for m in mine} <= set(ids)
    assert ids == await _all_pages(service, 100)        # same order whatever the page size


@pytest.mark.asyncio(loop_scope="session")
async def test_bogus_or_relevance_cursor_returns_page_one(db_session):
    await _image(db_session, 90020)
    service = _service(db_session)
    first = await service.get_trending(cursor=None, limit=3)
    relevance = ImageService._encode_cursor1(_T0, uuid.uuid4(), 1.5)

    for cursor in ("garbage", first.nextCursor[:-5], relevance):
        page = await service.get_trending(cursor=cursor, limit=3)
        assert [i.meme.id for i in page.items] == [i.meme.id for i in first.items]


@pytest.mark.asyncio(loop_scope="session")
async def test_reads_only(db_session):
    await _image(db_session, 90030)
    service = _service(db_session)
    queries = (select(func.count()).select_from(Image), select(func.count()).select_from(PopularityAbsorption))
    before = [(await db_session.execute(q)).scalar_one() for q in queries]

    await service.get_trending(cursor=None, limit=10)

    after = [(await db_session.execute(q)).scalar_one() for q in queries]
    assert before == after


# ---- window="30d" through ImageService with the real repository (S3) ----

async def _edge(db_session, winner, weight, days_ago=1):
    db_session.add(PopularityAbsorption(
        kind="absorb", source="cluster_review", loser_image_id=uuid.uuid4(), winner_image_id=winner.id,
        weight=weight, created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    ))
    await db_session.flush()


async def _gain_memes(db_session):
    """Four memes with very large gains (they head the list): 910004, 910003, then a tie of 910002."""
    top = await _image(db_session, 5, minutes=0)
    second = await _image(db_session, 6, minutes=0)
    tie_old = await _image(db_session, 7, minutes=1)
    tie_new = await _image(db_session, 8, minutes=2)
    for meme, gain in ((top, 910004), (second, 910003), (tie_old, 910002), (tie_new, 910002)):
        await _edge(db_session, meme, gain)
    return top, second, tie_old, tie_new


@pytest.mark.asyncio(loop_scope="session")
async def test_window_30d_orders_items_and_carries_window_gain(db_session):
    top, second, tie_old, tie_new = await _gain_memes(db_session)
    service = _service(db_session)

    page = await service.get_trending(cursor=None, limit=4, window="30d")

    assert [(i.meme.id, i.windowGain, i.popularity) for i in page.items] == [
        (str(top.id), 910004, 5), (str(second.id), 910003, 6),
        (str(tie_new.id), 910002, 8), (str(tie_old.id), 910002, 7),
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_window_30d_next_cursor_continues_without_repeats_or_gaps(db_session):
    top, second, tie_old, tie_new = await _gain_memes(db_session)
    service = _service(db_session)
    mine = [str(x.id) for x in (top, second, tie_new, tie_old)]

    first = await service.get_trending(cursor=None, limit=3, window="30d")
    assert [i.meme.id for i in first.items] == mine[:3] and first.hasNext is True
    second_page = await service.get_trending(cursor=first.nextCursor, limit=3, window="30d")

    assert second_page.items[0].meme.id == mine[3]
    assert second_page.items[0].windowGain == 910002
    assert not {i.meme.id for i in first.items} & {i.meme.id for i in second_page.items}


@pytest.mark.asyncio(loop_scope="session")
async def test_all_window_serialised_response_has_no_window_gain(db_session):
    from Backend.app.api.images import TrendingPageWire
    await _image(db_session, 90010, minutes=0)
    service = _service(db_session)

    page = await service.get_trending(cursor=None, limit=5)
    wire = TrendingPageWire.model_validate(page.model_dump()).model_dump(mode="json")

    assert wire["items"] and all("windowGain" not in item for item in wire["items"])
    assert "nextCursor" in wire and "hasNext" in wire


@pytest.mark.asyncio(loop_scope="session")
async def test_cursor_from_the_other_window_restarts_at_page_one(db_session):
    await _gain_memes(db_session)
    await _image(db_session, 90011, minutes=0)
    service = _service(db_session)

    month_first = await service.get_trending(cursor=None, limit=2, window="30d")
    all_first = await service.get_trending(cursor=None, limit=2)
    all_with_month_cursor = await service.get_trending(cursor=month_first.nextCursor, limit=2)
    month_with_all_cursor = await service.get_trending(cursor=all_first.nextCursor, limit=2, window="30d")

    assert [i.meme.id for i in all_with_month_cursor.items] == [i.meme.id for i in all_first.items]
    assert [i.meme.id for i in month_with_all_cursor.items] == [i.meme.id for i in month_first.items]
