"""Discovery report, memes view (epic meme-popularity, S4): which memes keep coming back and how visible they are."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, insert, text

from repository.discovery_report import DEFAULT_SOURCES, MEME_SORTS, memes_report
from repository.meme_impressions import MemeImpressionRepository
from repository.popularity import PopularityRepository
from Storage.models import Image, ImageExtras, MemeImpression, MemeSave, PopularityAbsorption
from tests.integration.test_popularity_repository import make_image

pytestmark = pytest.mark.asyncio(loop_scope="session")

HASH = "hash_cross_corpus"


def ago(**delta):
    return datetime.now(timezone.utc) - timedelta(**delta)


async def edge(session, winner, loser=None, source=HASH, weight=1, created_at=None):
    values = dict(kind="absorb", source=source, winner_image_id=winner.id,
                  loser_image_id=None if loser is None else loser.id, weight=weight)
    if created_at is not None:
        values["created_at"] = created_at
    await session.execute(insert(PopularityAbsorption).values(**values))


async def report(session, **kwargs):
    kwargs.setdefault("window_days", 30)
    rows, available = await memes_report(session, **kwargs)
    return {row.image_id: row for row in rows}, available


async def test_counts_a_re_drop_for_the_winner(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    rows, available = await report(db_session)
    assert available is True
    row = rows[img.id]
    assert row.redrops == 1 and row.sources == {HASH: 1} and row.filename == "a.jpg"
    assert row.popularity == 1


async def test_a_chain_is_credited_to_the_final_root(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    await edge(db_session, b, c, "ingest_tier_a")
    await edge(db_session, a, b, "ingest_tier_b")
    rows, _ = await report(db_session)
    assert set(rows) == {a.id}
    assert rows[a.id].redrops == 2
    assert rows[a.id].sources == {"ingest_tier_a": 1, "ingest_tier_b": 1}


async def test_a_tree_below_an_excluded_edge_is_still_found(db_session):
    root, lower, other = [await make_image(db_session, f"{n}.jpg") for n in ("r", "l", "m")]
    await edge(db_session, root, lower, "cluster_review")
    await edge(db_session, lower, other, HASH)
    rows, _ = await report(db_session, sources=(HASH,))
    assert set(rows) == {root.id}
    assert rows[root.id].redrops == 1 and rows[root.id].sources == {HASH: 1}


async def test_adjustment_is_excluded_by_default_and_other_sources_are_counted(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img, None, "adjustment", weight=5)
    await edge(db_session, img, None, "file_duplicates")
    assert "adjustment" not in DEFAULT_SOURCES
    rows, _ = await report(db_session)
    assert rows[img.id].redrops == 1 and rows[img.id].sources == {"file_duplicates": 1}
    rows, _ = await report(db_session, sources=("adjustment",))
    assert rows[img.id].redrops == 5


async def test_weight_zero_recut_edges_add_nothing(db_session):
    source, cut = await make_image(db_session, "s.jpg"), await make_image(db_session, "c.jpg")
    await edge(db_session, cut, source, "recut", weight=0)
    rows, _ = await report(db_session)
    assert rows == {}


async def test_reversed_edges_do_not_count(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(l.id, w.id, "ingest_tier_a")
    rows, _ = await report(db_session)
    assert rows[w.id].redrops == 1
    await pop.reverse_open_edge(l.id, "unflag")
    rows, _ = await report(db_session)
    assert w.id not in rows


async def test_window_split(db_session):
    img = await make_image(db_session, "a.jpg")
    newest = ago(days=5)
    await edge(db_session, img, created_at=newest)
    await edge(db_session, img, created_at=ago(days=29, hours=23))
    await edge(db_session, img, created_at=ago(days=40))
    rows, _ = await report(db_session, window_days=30)
    row = rows[img.id]
    assert (row.redrops, row.redrops_window) == (3, 2)
    assert abs((row.last_back - newest).total_seconds()) < 1


async def test_flagged_non_active_and_deleted_roots_are_not_listed(db_session):
    flagged, pending, gone, shown = [await make_image(db_session, f"{n}.jpg") for n in ("f", "p", "g", "s")]
    for image in (flagged, pending, gone, shown):
        await edge(db_session, image)
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    pending.status = "pending"
    await db_session.flush()
    await db_session.execute(delete(Image).where(Image.id == gone.id))
    rows, _ = await report(db_session)
    assert set(rows) == {shown.id}


async def test_saves_and_impressions_include_the_whole_tree(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await edge(db_session, w, l, "ingest_tier_a")
    db_session.add_all([MemeSave(image_id=w.id), MemeSave(image_id=l.id)])
    await MemeImpressionRepository(db_session).record_many("search", [l.id, w.id])
    await db_session.execute(insert(MemeImpression).values(
        image_id=w.id, rank=1, surface="search", shown_at=ago(days=40)))
    rows, _ = await report(db_session)
    row = rows[w.id]
    assert row.saves == 2 and row.impressions == 3 and row.impressions_window == 2


async def test_per_100_is_inf_zero_or_a_ratio(db_session):
    never_shown, only_old, ratio = [await make_image(db_session, f"{n}.jpg") for n in ("n", "o", "r")]
    for _ in range(2):
        await edge(db_session, never_shown, created_at=ago(days=1))
    await edge(db_session, only_old, created_at=ago(days=60))
    for _ in range(3):
        await edge(db_session, ratio, created_at=ago(days=1))
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [only_old.id] * 5)
    await repo.record_many("search", [ratio.id] * 6)
    rows, _ = await report(db_session, min_redrops=0)
    assert rows[never_shown.id].per_100 == float("inf")
    assert rows[only_old.id].per_100 == 0.0
    assert rows[ratio.id].per_100 == pytest.approx(50.0)


async def test_min_redrops_max_impressions_and_limit(db_session):
    one, two, three = [await make_image(db_session, f"{n}.jpg") for n in ("1", "2", "3")]
    await edge(db_session, one)
    for _ in range(2):
        await edge(db_session, two)
    for _ in range(3):
        await edge(db_session, three)
    await MemeImpressionRepository(db_session).record_many("search", [three.id])
    rows, _ = await report(db_session, min_redrops=2)
    assert set(rows) == {two.id, three.id}
    rows, _ = await report(db_session, max_impressions=0)
    assert set(rows) == {one.id, two.id}
    rows, _ = await report(db_session, limit=1)
    assert len(rows) == 1


async def test_every_sort_orders_descending_and_the_default_is_per_100(db_session):
    # Four images whose six sort keys all give a different order (found by search), so a sort mapped to the wrong
    # key fails. Per image: (re-drops older than the window, re-drops in the window, popularity, saves,
    # impressions in the window). Window re-drops, saves and impressions are placed inside the window.
    spec = [(3, 2, 5, 4, 3), (0, 4, 2, 3, 11), (0, 1, 7, 2, 19), (0, 3, 4, 0, 5)]
    images = []
    for index, (old, recent, popularity, saves, shown) in enumerate(spec):
        image = await make_image(db_session, f"{index}.jpg")
        images.append(image)
        for _ in range(old):
            await edge(db_session, image, created_at=ago(days=40))
        for _ in range(recent):
            await edge(db_session, image, created_at=ago(days=1))
        await db_session.execute(text("UPDATE images SET popularity = :p WHERE id = :i"),
                                 {"p": popularity, "i": image.id})
        db_session.add_all([MemeSave(image_id=image.id) for _ in range(saves)])
        await MemeImpressionRepository(db_session).record_many("search", [image.id] * shown)
    await db_session.flush()
    by_index = {
        "per_100": (0, 3, 1, 2),
        "redrops": (0, 1, 3, 2),
        "redrops_window": (1, 3, 0, 2),
        "popularity": (2, 0, 3, 1),
        "saves": (0, 1, 2, 3),
        "impressions": (2, 1, 3, 0),
    }
    assert set(by_index) == set(MEME_SORTS) and len(set(by_index.values())) == 6
    for name, order in by_index.items():
        rows, _ = await memes_report(db_session, window_days=30, sort=name)
        assert [r.image_id for r in rows] == [images[i].id for i in order], name
    rows, _ = await memes_report(db_session, window_days=30)
    assert [r.image_id for r in rows] == [images[i].id for i in by_index["per_100"]]


async def test_without_the_impressions_table_the_columns_are_n_a_and_the_sort_falls_back(db_session):
    few, many = await make_image(db_session, "f.jpg"), await make_image(db_session, "m.jpg")
    await edge(db_session, few)
    for _ in range(3):
        await edge(db_session, many)
    await db_session.execute(text("DROP TABLE meme_impressions"))
    rows, available = await memes_report(db_session, window_days=30)
    assert available is False
    assert [r.image_id for r in rows] == [many.id, few.id]
    assert all(r.impressions is None and r.impressions_window is None and r.per_100 is None for r in rows)
    with pytest.raises(ValueError):
        await memes_report(db_session, window_days=30, max_impressions=1)


async def test_an_explicit_sort_that_needs_no_impressions_is_kept_without_the_table(db_session):
    popular, often = await make_image(db_session, "p.jpg"), await make_image(db_session, "o.jpg")
    for _ in range(3):
        await edge(db_session, often)
    await edge(db_session, popular)
    await db_session.execute(text("UPDATE images SET popularity = 9 WHERE id = :i"), {"i": popular.id})
    await db_session.execute(text("DROP TABLE meme_impressions"))
    for name, order in (("popularity", [popular.id, often.id]), ("redrops_window", [often.id, popular.id]),
                        ("impressions", [often.id, popular.id]), ("per_100", [often.id, popular.id])):
        rows, available = await memes_report(db_session, window_days=30, sort=name)
        assert available is False
        assert [r.image_id for r in rows] == order, name


async def test_adjustments_count_with_their_sign_when_asked_for(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img, None, "adjustment", weight=5)
    await edge(db_session, img, None, "adjustment", weight=-3)
    rows, _ = await report(db_session, sources=("adjustment",))
    assert rows[img.id].redrops == 2


async def test_invalid_arguments_raise_before_any_sql(db_session):
    with pytest.raises(ValueError):
        await memes_report(db_session, window_days=30, sources=("nope",))
    with pytest.raises(ValueError):
        await memes_report(db_session, window_days=30, sort="nope")
    with pytest.raises(ValueError):
        await memes_report(db_session, window_days=30, limit=0)
