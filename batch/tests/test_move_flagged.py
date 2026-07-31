"""
Unit tests for batch/move_flagged.py's run() (per-file resilience + metrics) and main()
(stats persistence + chained unregister_deleted_images call). No real DB -- session/repo
interactions are mocked, matching batch/tests/test_run_tracking.py's style. run()'s
filesystem behavior uses real tmp_path files, matching
batch/tests/test_move_reference_duplicates.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.move_flagged import main, run
from repository.batch_runs import BatchAlreadyRunningError


def _mock_session(filenames):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=[(f,) for f in filenames])
    return session


def _ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_file_does_not_abort_remaining_moves(self, tmp_path):
        (tmp_path / "b.jpg").write_bytes(b"x")
        session = _mock_session(["missing.jpg", "b.jpg"])

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"error.file_not_found": 1, "moved": 1}
        assert (tmp_path / "excluded" / "b.jpg").exists()

    @pytest.mark.asyncio
    async def test_other_move_error_is_counted_and_does_not_abort(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg", "b.jpg"])

        import batch.move_flagged as module
        real_move = module.shutil.move

        def fake_move(src, dst):
            if str(src).endswith("a.jpg"):
                raise PermissionError("locked")
            return real_move(src, dst)

        monkeypatch.setattr(module.shutil, "move", fake_move)

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"error.move_failed": 1, "moved": 1}
        assert (tmp_path / "excluded" / "b.jpg").exists()
        assert not (tmp_path / "excluded" / "a.jpg").exists()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_persists_stats_and_chains_unregister(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock()

        class _FakeTrackedRun:
            async def __aenter__(self_inner):
                return "run-1"

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_FakeTrackedRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="scheduled")

        repo.update_stats.assert_awaited_once_with("run-1", moved=1)
        unregister_main.assert_awaited_once_with(trigger="scheduled")

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_persists_stats_and_chains_unregister(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock()

        class _FakeFinishExistingRun:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "finish_existing_run", return_value=_FakeFinishExistingRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="manual", run_id="existing-run-1")

        repo.update_stats.assert_awaited_once_with("existing-run-1", moved=1)
        unregister_main.assert_awaited_once_with(trigger="manual")

    @pytest.mark.asyncio
    async def test_no_chain_skips_unregister(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock()

        class _FakeTrackedRun:
            async def __aenter__(self_inner):
                return "run-1"

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_FakeTrackedRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="scheduled", chain=False)

        repo.update_stats.assert_awaited_once_with("run-1", moved=1)
        unregister_main.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chained_batch_already_running_error_is_swallowed(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock(side_effect=BatchAlreadyRunningError("unregister_deleted_images"))

        class _FakeTrackedRun:
            async def __aenter__(self_inner):
                return "run-1"

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_FakeTrackedRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="scheduled")  # must not raise

        repo.update_stats.assert_awaited_once_with("run-1", moved=1)
        unregister_main.assert_awaited_once_with(trigger="scheduled")
