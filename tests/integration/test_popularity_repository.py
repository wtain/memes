import uuid

import pytest
from sqlalchemy import func, select, update

from repository.popularity import AlreadyDiscardedError, CycleError, PopularityRepository
from Storage.models import Image, PopularityAbsorption


async def make_image(session, filename):
    img = Image(filename=filename, status="active")
    session.add(img)
    await session.flush()
    return img


async def pop(session, image):
    return (await session.execute(select(Image.popularity).where(Image.id == image.id))).scalar_one()


async def edge_count(session):
    return (await session.execute(select(func.count()).select_from(PopularityAbsorption))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_absorb_moves_the_loser_into_the_winner(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    row = await PopularityRepository(db_session).absorb(b.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 2
    assert await pop(db_session, b) == 0
    assert row.weight == 1 and row.kind == "absorb" and row.loser_filename == "b.jpg"


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_of_three(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await repo.absorb(a.id, c.id, "cluster_review")
    assert (await pop(db_session, c), await pop(db_session, a), await pop(db_session, b)) == (3, 0, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_naming_a_discarded_image_credits_its_root(db_session):
    a, b, c, d = [await make_image(db_session, f"{n}.jpg") for n in "abcd"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await repo.absorb(a.id, c.id, "cluster_review")
    row = await repo.absorb(d.id, b.id, "cluster_review")
    assert await pop(db_session, c) == 4
    assert row.winner_image_id == b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_recut_edge_gives_the_cut_exactly_the_sources_popularity(db_session):
    a, x, y, cut = [await make_image(db_session, f"{n}.jpg") for n in ("a", "x", "y", "cut")]
    repo = PopularityRepository(db_session)
    await repo.absorb(x.id, a.id, "cluster_review")
    await repo.absorb(y.id, a.id, "cluster_review")
    row = await repo.absorb(a.id, cut.id, "recut")
    assert row.weight == 0
    assert (await pop(db_session, cut), await pop(db_session, a)) == (3, 0)
    s, cut2 = await make_image(db_session, "s.jpg"), await make_image(db_session, "cut2.jpg")
    await repo.absorb(s.id, cut2.id, "recut")
    assert await pop(db_session, cut2) == 1
    assert await pop(db_session, s) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_retry_returns_the_same_row_and_changes_nothing(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    first = await repo.absorb(b.id, a.id, "cluster_review")
    again = await repo.absorb(b.id, a.id, "cluster_review")
    assert again.id == first.id
    assert await pop(db_session, a) == 2
    await repo.absorb(a.id, c.id, "cluster_review")
    third = await repo.absorb(b.id, a.id, "cluster_review")
    assert third.id == first.id
    assert (await pop(db_session, c), await pop(db_session, a)) == (3, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_absorbing_into_a_different_tree_raises_already_discarded(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    with pytest.raises(AlreadyDiscardedError) as exc:
        await repo.absorb(b.id, c.id, "cluster_review")
    assert exc.value.existing.winner_image_id == a.id
    assert (await pop(db_session, a), await pop(db_session, b), await pop(db_session, c)) == (2, 0, 1)
    assert await edge_count(db_session) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cycle_is_refused(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    with pytest.raises(CycleError):
        await repo.absorb(a.id, a.id, "cluster_review")
    assert await edge_count(db_session) == 0
    assert (await pop(db_session, a), await pop(db_session, b)) == (1, 1)
    await repo.absorb(b.id, a.id, "cluster_review")
    with pytest.raises(CycleError):
        await repo.absorb(a.id, b.id, "cluster_review")
    assert await edge_count(db_session) == 1
    assert (await pop(db_session, a), await pop(db_session, b)) == (2, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_ghost_winner_is_accepted(db_session):
    b = await make_image(db_session, "b.jpg")
    ghost = uuid.uuid4()
    row = await PopularityRepository(db_session).absorb(b.id, ghost, "cluster_review")
    assert row.id is not None
    assert row.winner_image_id == ghost and row.winner_content_hash is None
    assert await pop(db_session, b) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_drifted_loser_cache_does_not_spread(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    await db_session.execute(update(Image).where(Image.id == b.id).values(popularity=50))
    await PopularityRepository(db_session).absorb(b.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_loser_raises_value_error(db_session):
    a = await make_image(db_session, "a.jpg")
    with pytest.raises(ValueError):
        await PopularityRepository(db_session).absorb(uuid.uuid4(), a.id, "cluster_review")
    assert await edge_count(db_session) == 0


async def add_leaf_edge(session, winner, key):
    session.add(PopularityAbsorption(kind="absorb", source="hash_in_batch", loser_image_id=None,
                                     winner_image_id=winner.id, weight=1, dedupe_key=key))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_tree_weight_counts_leaf_edges_and_they_travel_with_the_tree(db_session):
    a, w = await make_image(db_session, "a.jpg"), await make_image(db_session, "w.jpg")
    repo = PopularityRepository(db_session)
    await add_leaf_edge(db_session, w, "leaf-1")
    await add_leaf_edge(db_session, w, "leaf-2")
    await db_session.execute(update(Image).where(Image.id == w.id).values(popularity=3))
    assert await repo.tree_weight(w.id) == 2
    await repo.absorb(w.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 4
    assert await pop(db_session, w) == 0


# ---- record_dropped_copies and reverse ----

from sqlalchemy import delete  # noqa: E402

from repository.popularity import DroppedCopy  # noqa: E402
from Storage.models import BatchRun  # noqa: E402


async def make_run(session):
    run = BatchRun(kind="ingestion", trigger="manual", status="completed")
    session.add(run)
    await session.flush()
    return run.run_id


def copies(*names, dest="/dest"):
    return [DroppedCopy(filename=n, content_hash=f"h-{n}", destination=dest) for n in names]


@pytest.mark.asyncio(loop_scope="session")
async def test_dropped_copies_raise_the_keepers_popularity(db_session):
    keeper = await make_image(db_session, "k.jpg")
    run = await make_run(db_session)
    n = await PopularityRepository(db_session).record_dropped_copies(keeper.id, copies("x", "y", "z"), run)
    assert n == 3
    assert await pop(db_session, keeper) == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_the_same_destination_in_the_same_batch_counts_once(db_session):
    keeper = await make_image(db_session, "k.jpg")
    repo = PopularityRepository(db_session)
    run = await make_run(db_session)
    assert await repo.record_dropped_copies(keeper.id, copies("x"), run) == 1
    assert await repo.record_dropped_copies(keeper.id, copies("x"), run) == 0
    assert await pop(db_session, keeper) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_same_filename_at_a_different_destination_counts_again(db_session):
    keeper = await make_image(db_session, "k.jpg")
    repo = PopularityRepository(db_session)
    run = await make_run(db_session)
    await repo.record_dropped_copies(keeper.id, copies("x", dest="/d1"), run)
    await repo.record_dropped_copies(keeper.id, copies("x", dest="/d2"), run)
    assert await pop(db_session, keeper) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_list_and_duplicate_destination_in_one_call(db_session):
    keeper = await make_image(db_session, "k.jpg")
    repo = PopularityRepository(db_session)
    assert await repo.record_dropped_copies(keeper.id, [], await make_run(db_session)) == 0
    assert await edge_count(db_session) == 0
    dup = [DroppedCopy("x", "h", "/d"), DroppedCopy("x2", "h", "/d")]
    assert await repo.record_dropped_copies(keeper.id, dup, await make_run(db_session)) == 1
    assert await pop(db_session, keeper) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_non_hash_source_is_rejected_for_copies(db_session):
    keeper = await make_image(db_session, "k.jpg")
    with pytest.raises(ValueError):
        await PopularityRepository(db_session).record_dropped_copies(
            keeper.id, copies("x"), uuid.uuid4(), source="cluster_review")
    assert await edge_count(db_session) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_restores_the_loser(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    rev = await repo.reverse(row.id, "undo_reject")
    assert (await pop(db_session, a), await pop(db_session, b)) == (1, 1)
    await db_session.refresh(row)
    assert row.reversed_by_id == rev.id
    assert rev.kind == "reverse" and rev.reverses_id == row.id and rev.reason == "undo_reject"


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_in_the_middle_of_a_chain(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    eb = await repo.absorb(b.id, a.id, "cluster_review")
    ea = await repo.absorb(a.id, c.id, "cluster_review")
    await repo.reverse(eb.id, "correction")
    # A is still discarded into C, so its own cache stays 0 (the brief said 1; the algorithm gives 0)
    assert (await pop(db_session, c), await pop(db_session, a), await pop(db_session, b)) == (2, 0, 1)
    await repo.reverse(ea.id, "correction")
    assert (await pop(db_session, c), await pop(db_session, a)) == (1, 1)


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_in_the_other_order(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    eb = await repo.absorb(b.id, a.id, "cluster_review")
    ea = await repo.absorb(a.id, c.id, "cluster_review")
    await repo.reverse(ea.id, "correction")
    assert (await pop(db_session, a), await pop(db_session, c)) == (2, 1)
    await repo.reverse(eb.id, "correction")
    assert (await pop(db_session, a), await pop(db_session, b)) == (1, 1)


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_takes_the_subtree_with_it(db_session):
    a, b, d = [await make_image(db_session, f"{n}.jpg") for n in "abd"]
    repo = PopularityRepository(db_session)
    await repo.absorb(d.id, b.id, "cluster_review")
    eb = await repo.absorb(b.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 3
    await repo.reverse(eb.id, "correction")
    assert (await pop(db_session, b), await pop(db_session, a)) == (2, 1)


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_a_leaf_edge_lowers_the_root(db_session):
    keeper = await make_image(db_session, "k.jpg")
    repo = PopularityRepository(db_session)
    await repo.record_dropped_copies(keeper.id, copies("x", "y"), await make_run(db_session))
    leaf = (await db_session.execute(
        select(PopularityAbsorption).where(PopularityAbsorption.loser_image_id.is_(None)).limit(1))).scalar_one()
    await repo.reverse(leaf.id, "correction")
    assert await pop(db_session, keeper) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_when_the_loser_row_is_gone(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    await db_session.execute(delete(Image).where(Image.id == b.id))
    await repo.reverse(row.id, "abort")
    assert await pop(db_session, a) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_with_a_ghost_root_does_not_fail(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    await db_session.execute(delete(Image).where(Image.id == a.id))
    await repo.reverse(row.id, "abort")
    assert await pop(db_session, b) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_double_reverse_and_reverse_of_a_reverse_row_raise_value_error(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    rev = await repo.reverse(row.id, "correction")
    with pytest.raises(ValueError):
        await repo.reverse(row.id, "correction")
    with pytest.raises(ValueError):
        await repo.reverse(rev.id, "correction")
    with pytest.raises(ValueError):
        await repo.reverse(uuid.uuid4(), "correction")


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unknown_reason_raises(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    with pytest.raises(ValueError):
        await repo.reverse(row.id, "because")
    assert await pop(db_session, a) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_after_a_reversal_the_loser_can_be_absorbed_again(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await repo.absorb(b.id, a.id, "cluster_review")
    await repo.reverse(row.id, "undo_reject")
    again = await repo.absorb(b.id, a.id, "cluster_review")
    assert again.id != row.id
    assert (await pop(db_session, a), await pop(db_session, b)) == (2, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_recut_with_children_then_reverse(db_session):
    a, x, y, cut = [await make_image(db_session, f"{n}.jpg") for n in ("a", "x", "y", "cut")]
    repo = PopularityRepository(db_session)
    await repo.absorb(x.id, a.id, "cluster_review")
    await repo.absorb(y.id, a.id, "cluster_review")
    recut = await repo.absorb(a.id, cut.id, "recut")
    assert (await pop(db_session, cut), await pop(db_session, a)) == (3, 0)
    await repo.reverse(recut.id, "correction")
    assert (await pop(db_session, cut), await pop(db_session, a)) == (1, 3)
