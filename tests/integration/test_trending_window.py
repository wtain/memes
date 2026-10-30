"""
Integration tests: ImageRepository.get_trending_window (S3, "Last 30 days" view of the Trending memes).
Spec: docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-30-s3-trending-window-design.md.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.

Gains here are in the thousands so rows other data in the test database cannot interleave with them;
assertions look only at the ids a test created.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import Image, ImageExtras, PopularityAbsorption

_T0 = datetime(2026, 1, 1, 12, 0, 0)
_NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


async def _image(db_session, minutes=0, status="active", flagged=None, popularity=1):
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


async def _edge(db_session, winner, loser_id=None, weight=1, days_ago=1, source="cluster_review", reverse=False):
    """An absorb edge of the log (loser_id defaults to a fresh id with no images row, as for a deleted loser)."""
    row = PopularityAbsorption(
        kind="absorb", source=source, loser_image_id=loser_id or uuid.uuid4(),
        winner_image_id=winner.id if hasattr(winner, "id") else winner, weight=weight,
        created_at=_NOW() - timedelta(days=days_ago),
    )
    db_session.add(row)
    await db_session.flush()
    if reverse:
        rev = PopularityAbsorption(
            kind="reverse", source=source, loser_image_id=row.loser_image_id, winner_image_id=row.winner_image_id,
            weight=weight, reverses_id=row.id, reason="unflag",
        )
        db_session.add(rev)
        await db_session.flush()
        row.reversed_by_id = rev.id
        await db_session.flush()
    return row


async def _window(db_session, ids, limit=100):
    """The listed rows among `ids`, in order, as (id, gain)."""
    rows = await ImageRepository(db_session).get_trending_window(None, None, None, limit)
    mine = {i.id if hasattr(i, "id") else i for i in ids}
    return [(r.id, r.gain) for r in rows if r.id in mine]


@pytest.mark.asyncio(loop_scope="session")
async def test_edge_inside_window_counts_and_31_days_old_does_not(db_session):
    root = await _image(db_session)
    await _edge(db_session, root, weight=3000, days_ago=29)
    await _edge(db_session, root, weight=500, days_ago=31)
    assert await _window(db_session, [root]) == [(root.id, 3000)]


@pytest.mark.asyncio(loop_scope="session")
async def test_reversed_edge_does_not_count(db_session):
    root = await _image(db_session)
    await _edge(db_session, root, weight=3001)
    await _edge(db_session, root, weight=500, reverse=True)
    assert await _window(db_session, [root]) == [(root.id, 3001)]


@pytest.mark.asyncio(loop_scope="session")
async def test_adjustment_edge_does_not_count(db_session):
    root = await _image(db_session)
    await _edge(db_session, root, weight=3002)
    await _edge(db_session, root, weight=500, source="adjustment")
    assert await _window(db_session, [root]) == [(root.id, 3002)]


@pytest.mark.asyncio(loop_scope="session")
async def test_zero_weight_edge_adds_nothing(db_session):
    only_zero = await _image(db_session)
    await _edge(db_session, only_zero, weight=0)
    with_zero = await _image(db_session)
    await _edge(db_session, with_zero, weight=3003)
    await _edge(db_session, with_zero, weight=0)
    assert await _window(db_session, [only_zero, with_zero]) == [(with_zero.id, 3003)]


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_credits_final_root_with_both_levels(db_session):
    a, c = await _image(db_session), await _image(db_session)
    b = await _image(db_session)
    await _edge(db_session, a, loser_id=b.id, weight=2000, days_ago=10)
    await _edge(db_session, c, loser_id=a.id, weight=1001, days_ago=2)
    assert await _window(db_session, [a, b, c]) == [(c.id, 3001)]


@pytest.mark.asyncio(loop_scope="session")
async def test_reversed_upper_edge_stops_the_climb(db_session):
    a, b, c = await _image(db_session), await _image(db_session), await _image(db_session)
    await _edge(db_session, a, loser_id=b.id, weight=2000)
    await _edge(db_session, c, loser_id=a.id, weight=1001, reverse=True)
    assert await _window(db_session, [a, b, c]) == [(a.id, 2000)]


@pytest.mark.asyncio(loop_scope="session")
async def test_two_equal_edges_into_one_winner_both_count(db_session):
    root = await _image(db_session)
    await _edge(db_session, root, weight=1500)
    await _edge(db_session, root, weight=1500)
    assert await _window(db_session, [root]) == [(root.id, 3000)]


@pytest.mark.asyncio(loop_scope="session")
async def test_edge_of_deleted_loser_still_counts(db_session):
    root = await _image(db_session)
    loser = await _image(db_session)
    await _edge(db_session, root, loser_id=loser.id, weight=3004)
    await db_session.execute(delete(Image).where(Image.id == loser.id))
    assert await _window(db_session, [root]) == [(root.id, 3004)]


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_nonactive_and_absorbed_roots_are_not_listed(db_session):
    listed = await _image(db_session)
    flagged = await _image(db_session, flagged=True)
    pending = await _image(db_session, status="pending")
    absorbed = await _image(db_session)
    for img in (listed, flagged, pending, absorbed):
        await _edge(db_session, img, weight=3005)
    await _edge(db_session, listed, loser_id=absorbed.id, weight=1)  # absorbed is now a loser, not a root
    # absorbed's own 3005 climbs into listed: 3005 + 3005 + 1
    assert await _window(db_session, [listed, flagged, pending, absorbed]) == [(listed.id, 6011)]


@pytest.mark.asyncio(loop_scope="session")
async def test_root_with_gain_reduced_to_zero_by_reversal_is_not_listed(db_session):
    root = await _image(db_session)
    await _edge(db_session, root, weight=3007, reverse=True)
    assert await _window(db_session, [root]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_extras_row_counts_as_not_flagged(db_session):
    no_row = await _image(db_session)
    unflagged = await _image(db_session, flagged=False)
    await _edge(db_session, no_row, weight=3008)
    await _edge(db_session, unflagged, weight=3008)
    rows = await ImageRepository(db_session).get_trending_window(None, None, None, 100)
    flags = {r.id: r.flagged for r in rows}
    assert no_row.id in flags and unflagged.id in flags
    assert not flags[no_row.id] and not flags[unflagged.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_order_is_gain_then_created_at_then_id(db_session):
    low = await _image(db_session, minutes=9)
    old_high = await _image(db_session, minutes=1)
    new_high = await _image(db_session, minutes=2)
    top = await _image(db_session, minutes=0)
    await _edge(db_session, low, weight=3009)
    await _edge(db_session, old_high, weight=3010)
    await _edge(db_session, new_high, weight=3010)
    await _edge(db_session, top, weight=3011)
    got = await _window(db_session, [low, old_high, new_high, top])
    assert [i for i, _ in got] == [top.id, new_high.id, old_high.id, low.id]
    # same created_at and gain: id descending
    a = await _image(db_session, minutes=5)
    b = await _image(db_session, minutes=5)
    await _edge(db_session, a, weight=3012)
    await _edge(db_session, b, weight=3012)
    got = await _window(db_session, [a, b])
    assert [i for i, _ in got] == sorted([a.id, b.id], reverse=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_keyset_pages_equal_gains_without_repeats_or_gaps(db_session):
    memes = [await _image(db_session, minutes=m % 3) for m in range(8)]  # equal gains, repeated created_at
    for m in memes:
        await _edge(db_session, m, weight=3013)
    expected = [r.id for r in await ImageRepository(db_session).get_trending_window(None, None, None, 1000)
                if r.id in {m.id for m in memes}]
    assert len(expected) == 8

    repo, seen, cursor = ImageRepository(db_session), [], (None, None, None)
    for _ in range(500):
        rows = await repo.get_trending_window(*cursor, 3)
        page = rows[:3]
        seen += [r.id for r in page]
        if len(rows) <= 3:
            break
        last = page[-1]
        cursor = (last.gain, last.created_at, last.id)
    mine = {m.id for m in memes}
    assert [i for i in seen if i in mine] == expected
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio(loop_scope="session")
async def test_small_total_with_gain_is_listed(db_session):
    root = await _image(db_session, popularity=1)  # below search.trending.min_popularity
    await _edge(db_session, root, weight=3014)
    assert await _window(db_session, [root]) == [(root.id, 3014)]


@pytest.mark.asyncio(loop_scope="session")
async def test_popularity_column_is_returned(db_session):
    root = await _image(db_session, popularity=777)
    await _edge(db_session, root, weight=3015)
    rows = await ImageRepository(db_session).get_trending_window(None, None, None, 1000)
    row = next(r for r in rows if r.id == root.id)
    assert row.popularity == 777 and row.gain == 3015
