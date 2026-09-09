"""Unit tests for split_for_review -- pure, no DB. Ids are ints here; production uses UUIDs."""
from Backend.app.services.cluster_splitting import split_for_review


def _symmetric(pairs):
    """[(a, b, dist), ...] -> {id: [(neighbor, dist), ...]} symmetric."""
    out = {}
    for a, b, d in pairs:
        out.setdefault(a, []).append((b, d))
        out.setdefault(b, []).append((a, d))
    return out


def _assert_partition(groups, members):
    flat = [m for g in groups for m in g]
    assert sorted(flat) == sorted(members), "every member appears exactly once"
    assert len(flat) == len(set(flat)), "no member in two groups"


class TestSplitForReview:
    def test_at_or_below_max_size_returns_whole(self):
        members = [1, 2, 3]
        assert split_for_review(members, {}, start=0.05, decrement=0.01, floor=0.01, max_size=3) == [members]

    def test_splits_a_loose_chain_into_tight_halves(self):
        # 1-2-3  ~loose~  4-5-6 : the 3-4 link is 0.045, everything else 0.02
        members = [1, 2, 3, 4, 5, 6]
        pairs = _symmetric([(1, 2, 0.02), (2, 3, 0.02), (3, 4, 0.045),
                            (4, 5, 0.02), (5, 6, 0.02)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert sorted(sorted(g) for g in groups) == [[1, 2, 3], [4, 5, 6]]

    def test_loose_member_attaches_to_nearest_core(self):
        # 1-2-3-4 tight core; 5 hangs off 4 at 0.048 (dropped by resolve_cluster at 0.04)
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (3, 4, 0.01), (4, 5, 0.048)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=4)
        _assert_partition(groups, members)
        # 5 is not dropped -- it rides along with the {1,2,3,4} core
        the_group_with_5 = next(g for g in groups if 5 in g)
        assert set(the_group_with_5) == {1, 2, 3, 4, 5}

    def test_chained_loose_members_all_attach(self):
        # tight {1,2,3}; 4 hangs off 3 at 0.047, 5 hangs off 4 at 0.046
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (3, 4, 0.047), (4, 5, 0.046)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert {4, 5}.issubset(next(g for g in groups if 1 in g))

    def test_blob_looser_than_first_step_returns_whole(self):
        # every edge in [start-decrement, start) -> resolve_cluster finds no core, nothing
        # attaches, the residual union-find reconnects them -> one group, nothing dropped
        members = [1, 2, 3, 4]
        pairs = _symmetric([(1, 2, 0.045), (2, 3, 0.045), (3, 4, 0.045)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert sorted(groups[0]) == [1, 2, 3, 4] and len(groups) == 1

    def test_disjoint_residual_component_emitted_as_its_own_group(self):
        # {1,2,3} tight core; {4,5} connected only to each other at 0.048 (no edge to the core)
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (4, 5, 0.048)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert {4, 5} in [set(g) for g in groups]

    def test_oversized_core_at_floor_is_returned_not_dropped(self):
        # 4 members all mutually 0.005 -- tighter than floor; resolve_cluster gives up oversized
        members = [1, 2, 3, 4]
        pairs = _symmetric([(1, 2, 0.005), (2, 3, 0.005), (3, 4, 0.005), (1, 3, 0.005),
                            (1, 4, 0.005), (2, 4, 0.005)])
        groups = split_for_review(members, pairs, start=0.02, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert len(groups) == 1 and sorted(groups[0]) == [1, 2, 3, 4]
