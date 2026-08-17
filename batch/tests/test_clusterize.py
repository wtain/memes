"""
Unit tests for batch/clusterize.py: main()'s self-tracking contract (_process() itself is
mocked there -- its DB glue has no dedicated coverage to preserve, mirrors
batch/tests/test_build_concept_embeddings_main.py's style for a non-chaining self-tracked
script) and resolve_cluster()'s oversized-cluster splitting / singleton-dropping logic,
which is pure and DB-free.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.clusterize import main, resolve_cluster


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path(self):
        process_mock = AsyncMock()
        import batch.clusterize as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="clusterize", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.clusterize as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()


def _symmetric_pairs(edges: list[tuple[int, int, float]]) -> dict[int, list[tuple[int, float]]]:
    pairs_by_member: dict[int, list[tuple[int, float]]] = {}
    for a, b, distance in edges:
        pairs_by_member.setdefault(a, []).append((b, distance))
        pairs_by_member.setdefault(b, []).append((a, distance))
    return pairs_by_member


class TestResolveCluster:
    def test_under_limit_passes_through_unchanged(self):
        result = resolve_cluster(
            [1, 2, 3], pairs_by_member={}, threshold=0.05, decrement=0.01, floor=0.01, max_size=3,
        )
        assert result == [[1, 2, 3]]

    def test_singleton_input_is_dropped(self):
        result = resolve_cluster(
            [1], pairs_by_member={}, threshold=0.05, decrement=0.01, floor=0.01, max_size=12,
        )
        assert result == []

    def test_splits_cleanly_after_one_decrement(self):
        # Chain 1-2-3-4 at 0.05; the middle edge (2-3) drops out at the 0.04 retry,
        # leaving two clean pairs.
        pairs_by_member = _symmetric_pairs([
            (1, 2, 0.02),
            (2, 3, 0.045),
            (3, 4, 0.02),
        ])
        result = resolve_cluster(
            [1, 2, 3, 4], pairs_by_member, threshold=0.05, decrement=0.01, floor=0.01, max_size=2,
        )
        assert sorted(sorted(group) for group in result) == [[1, 2], [3, 4]]

    def test_multi_level_split_drops_implicit_singletons(self):
        # Chain 1..6 at 0.05 threshold. First retry (0.04) splits into {1,2,3} and
        # {4,5,6} (both still oversized); second retry (0.03) drops the 2-3 and 5-6
        # edges too, leaving {1,2} + implicit singleton 3, and {4,5} + implicit
        # singleton 6.
        pairs_by_member = _symmetric_pairs([
            (1, 2, 0.02),
            (2, 3, 0.035),
            (3, 4, 0.045),
            (4, 5, 0.02),
            (5, 6, 0.035),
        ])
        result = resolve_cluster(
            [1, 2, 3, 4, 5, 6], pairs_by_member, threshold=0.05, decrement=0.01, floor=0.01, max_size=2,
        )
        assert sorted(sorted(group) for group in result) == [[1, 2], [4, 5]]

    def test_gives_up_oversized_once_next_threshold_hits_floor(self):
        result = resolve_cluster(
            [1, 2, 3], pairs_by_member={}, threshold=0.02, decrement=0.02, floor=0.01, max_size=2,
        )
        assert result == [[1, 2, 3]]
