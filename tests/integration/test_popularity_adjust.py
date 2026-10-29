"""P4: manual adjustment ("set popularity to N") and the floor of 1 (design H1-H3)."""
import uuid

import pytest
from sqlalchemy import delete, func, select, update

from repository.popularity import AlreadyDiscardedError, PopularityRepository, WinnerGoneError
from Storage.models import Image, ImageExtras, PopularityAbsorption

from tests.integration.test_popularity_repository import copies, make_image, make_run, pop


async def adjustment_rows(session, image):
    return (await session.execute(
        select(PopularityAbsorption).where(PopularityAbsorption.source == "adjustment",
                                           PopularityAbsorption.winner_image_id == image.id)
    )).scalars().all()


async def edge_count(session):
    return (await session.execute(select(func.count()).select_from(PopularityAbsorption))).scalar_one()


async def with_losers(session, root, n, tag="c"):
    repo = PopularityRepository(session)
    edges = []
    for k in range(n):
        loser = await make_image(session, f"{tag}{k}.jpg")
        edges.append(await repo.absorb(loser.id, root.id, "cluster_review"))
    return edges


async def floored_root(session, name="r.jpg"):
    """A root with two absorbed copies and a -4 adjustment: raw -1, shown 1. Returns (root, open copy edges)."""
    repo = PopularityRepository(session)
    root = await make_image(session, name)
    edges = await with_losers(session, root, 4, tag=f"{name}-c")
    row = await repo.adjust(root.id, 1)
    assert row.weight == -4
    await repo.reverse(edges[0].id, "correction")
    await repo.reverse(edges[1].id, "correction")
    assert await repo.tree_weight(root.id) == -2
    return root, edges[2:]


# ---- adjust ----


@pytest.mark.asyncio(loop_scope="session")
async def test_set_1_to_40_writes_one_adjustment_edge(db_session):
    a = await make_image(db_session, "a.jpg")
    a.content_hash = "hash-a"
    await db_session.flush()
    repo = PopularityRepository(db_session)
    row = await repo.adjust(a.id, 40)
    assert row is not None
    assert (row.kind, row.source, row.weight) == ("absorb", "adjustment", 39)
    assert row.loser_image_id is None and row.winner_image_id == a.id
    assert row.winner_content_hash == "hash-a"
    assert (row.dedupe_key, row.reason, row.batch_run_id, row.loser_filename) == (None, None, None, None)
    assert await pop(db_session, a) == 40
    assert await repo.popularity_of(a.id) == 40
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_the_same_target_again_writes_nothing(db_session):
    a = await make_image(db_session, "a.jpg")
    repo = PopularityRepository(db_session)
    await repo.adjust(a.id, 40)
    before = await edge_count(db_session)
    assert await repo.adjust(a.id, 40) is None
    assert await edge_count(db_session) == before
    assert await pop(db_session, a) == 40


@pytest.mark.asyncio(loop_scope="session")
async def test_set_a_root_with_eleven_copies_to_1(db_session):
    a = await make_image(db_session, "a.jpg")
    await with_losers(db_session, a, 11)
    repo = PopularityRepository(db_session)
    assert await pop(db_session, a) == 12
    row = await repo.adjust(a.id, 1)
    assert row.weight == -11
    assert await pop(db_session, a) == 1
    assert await repo.popularity_of(a.id) == 1
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_later_copies_count_on_top_of_the_adjustment(db_session):
    a = await make_image(db_session, "a.jpg")
    repo = PopularityRepository(db_session)
    await repo.adjust(a.id, 40)
    await with_losers(db_session, a, 3)
    assert await pop(db_session, a) == 43
    assert await repo.popularity_of(a.id) == 43
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_discarded_image_raises_already_discarded(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    edge = await repo.absorb(b.id, a.id, "cluster_review")
    before = await edge_count(db_session)
    with pytest.raises(AlreadyDiscardedError) as exc:
        await repo.adjust(b.id, 10)
    assert exc.value.existing.id == edge.id
    assert await edge_count(db_session) == before
    assert (await pop(db_session, a), await pop(db_session, b)) == (2, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_missing_image_raises_winner_gone(db_session):
    with pytest.raises(WinnerGoneError):
        await PopularityRepository(db_session).adjust(uuid.uuid4(), 5)
    assert await edge_count(db_session) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_ghost_root_raises_winner_gone(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await db_session.execute(delete(Image).where(Image.id == a.id))
    before = await edge_count(db_session)
    with pytest.raises(WinnerGoneError):
        await repo.adjust(a.id, 5)
    assert await edge_count(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("target", [0, -3])
async def test_a_target_below_1_raises_value_error(db_session, target):
    a = await make_image(db_session, "a.jpg")
    with pytest.raises(ValueError):
        await PopularityRepository(db_session).adjust(a.id, target)
    assert await edge_count(db_session) == 0
    assert await pop(db_session, a) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_breakdown_sums_stacked_adjustments_into_one_entry(db_session):
    a = await make_image(db_session, "a.jpg")
    await with_losers(db_session, a, 2)
    repo = PopularityRepository(db_session)
    await repo.adjust(a.id, 40)
    await repo.adjust(a.id, 25)
    assert sorted(r.weight for r in await adjustment_rows(db_session, a)) == [-15, 37]
    parts = await repo.breakdown(a.id)
    assert parts == {"own": 1, "cluster_review": 2, "adjustment": 22}
    assert sum(parts.values()) == await pop(db_session, a) == 25


@pytest.mark.asyncio(loop_scope="session")
async def test_adjust_diffs_against_the_derived_value_so_a_drifted_cache_heals(db_session):
    a = await make_image(db_session, "a.jpg")
    repo = PopularityRepository(db_session)
    await db_session.execute(update(Image).where(Image.id == a.id).values(popularity=7))
    row = await repo.adjust(a.id, 7)
    assert row is not None and row.weight == 6
    assert await pop(db_session, a) == 7
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_adjust_on_an_absorbed_winner_is_refused_but_its_root_works(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await repo.absorb(a.id, c.id, "cluster_review")
    with pytest.raises(AlreadyDiscardedError):
        await repo.adjust(a.id, 10)
    row = await repo.adjust(c.id, 10)
    assert row.weight == 7
    assert (await pop(db_session, c), await pop(db_session, a), await pop(db_session, b)) == (10, 0, 0)
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flagged_active_image_can_be_adjusted(db_session):
    a = await make_image(db_session, "a.jpg")
    db_session.add(ImageExtras(image_id=a.id, flagged=True, flagged_reason="duplicate_review"))
    await db_session.flush()
    repo = PopularityRepository(db_session)
    await repo.adjust(a.id, 9)
    assert await pop(db_session, a) == 9
    assert await repo.verify() == []


# ---- the floor of 1 (H3) ----


@pytest.mark.asyncio(loop_scope="session")
async def test_negative_adjustment_then_reversals_never_go_below_1(db_session):
    root, open_edges = await floored_root(db_session)
    repo = PopularityRepository(db_session)
    assert await pop(db_session, root) == 1
    assert await repo.popularity_of(root.id) == 1
    assert await repo.verify() == []
    await repo.reverse(open_edges[0].id, "correction")
    assert await repo.tree_weight(root.id) == -3
    assert await pop(db_session, root) == 1
    assert await repo.popularity_of(root.id) == 1
    assert await repo.verify() == []
    parts = await repo.breakdown(root.id)
    assert parts == {"own": 1, "cluster_review": 1, "adjustment": -4}
    assert sum(parts.values()) == -2


@pytest.mark.asyncio(loop_scope="session")
async def test_absorbing_a_floored_root_moves_its_raw_sum_and_reversing_restores(db_session):
    root, _ = await floored_root(db_session)
    w = await make_image(db_session, "w.jpg")
    repo = PopularityRepository(db_session)
    await repo.record_dropped_copies(w.id, copies("x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9"),
                                     await make_run(db_session))
    assert await pop(db_session, w) == 10
    edge = await repo.absorb(root.id, w.id, "cluster_review")
    # weight 1 + tree_weight(root) -2: the winner moves by -1 (raw), not by +1 + (shown 1 - 1)
    assert await pop(db_session, w) == 9
    assert await pop(db_session, root) == 0
    assert await repo.popularity_of(root.id) == 0
    assert await repo.verify() == []
    await repo.reverse(edge.id, "correction")
    assert await pop(db_session, root) == 1  # max(1, 1 + -2)
    assert await pop(db_session, w) == 10
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_absorbing_a_floored_root_into_a_small_winner_floors_the_winner(db_session):
    root, _ = await floored_root(db_session)
    w = await make_image(db_session, "w.jpg")
    repo = PopularityRepository(db_session)
    edge = await repo.absorb(root.id, w.id, "cluster_review")
    assert await repo.tree_weight(w.id) == -1  # 1 (edge) + -2 (root's tree)
    assert await pop(db_session, w) == 1
    assert await pop(db_session, root) == 0
    assert await repo.verify() == []
    await repo.reverse(edge.id, "correction")
    assert (await pop(db_session, w), await pop(db_session, root)) == (1, 1)
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_an_adjusted_meme_carries_its_adjustment_when_absorbed(db_session):
    a, w = await make_image(db_session, "a.jpg"), await make_image(db_session, "w.jpg")
    repo = PopularityRepository(db_session)
    await repo.adjust(a.id, 40)
    edge = await repo.absorb(a.id, w.id, "cluster_review")
    assert await pop(db_session, w) == 41
    assert (await repo.breakdown(w.id))["adjustment"] == 39
    assert await repo.verify() == []
    await repo.reverse(edge.id, "correction")
    assert (await pop(db_session, w), await pop(db_session, a)) == (1, 40)
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_leaf_edges_under_a_negative_adjustment_stay_floored(db_session):
    k = await make_image(db_session, "k.jpg")
    repo = PopularityRepository(db_session)
    await repo.record_dropped_copies(k.id, copies("x", "y"), await make_run(db_session))
    await repo.adjust(k.id, 1)  # -2
    leaf = (await db_session.execute(select(PopularityAbsorption).where(
        PopularityAbsorption.winner_image_id == k.id, PopularityAbsorption.loser_image_id.is_(None),
        PopularityAbsorption.source == "hash_in_batch").limit(1))).scalar_one()
    await repo.reverse(leaf.id, "correction")
    assert await repo.tree_weight(k.id) == -1
    assert await pop(db_session, k) == 1
    assert await repo.verify() == []
    run = await make_run(db_session)
    await repo.record_dropped_copies(k.id, copies("p"), run)
    assert await pop(db_session, k) == 1  # raw 1 + 0
    await repo.record_dropped_copies(k.id, copies("q"), run)
    assert await pop(db_session, k) == 2
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_reversing_an_adjustment_edge_recomputes_with_the_floor(db_session):
    a = await make_image(db_session, "a.jpg")
    edges = await with_losers(db_session, a, 3)
    repo = PopularityRepository(db_session)
    adj = await repo.adjust(a.id, 1)  # -3
    await repo.reverse(edges[0].id, "correction")
    assert await pop(db_session, a) == 1
    await repo.reverse(adj.id, "correction")
    assert await pop(db_session, a) == 3
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_releasing_a_loser_that_carries_an_adjustment_from_a_ghost_root(db_session):
    root, _ = await floored_root(db_session)
    g = await make_image(db_session, "g.jpg")
    repo = PopularityRepository(db_session)
    edge = await repo.absorb(root.id, g.id, "cluster_review")
    await db_session.execute(delete(Image).where(Image.id == g.id))
    await repo.reverse(edge.id, "abort")
    assert await pop(db_session, root) == 1
    assert await repo.verify() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_verify_floors_a_negative_root_and_reports_a_cache_below_1(db_session):
    root, _ = await floored_root(db_session)
    repo = PopularityRepository(db_session)
    await db_session.execute(update(Image).where(Image.id == root.id).values(popularity=0))
    drifts = await repo.verify([root.id])
    assert [(d.cached, d.expected) for d in drifts] == [(0, 1)]
