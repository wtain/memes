"""
Unit tests for batch/run_wrapper.py's argument resolution and dispatch. Mocks
importlib.import_module and BatchRegistry -- no real batch script or DB involved.
"""
import logging
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.run_wrapper as run_wrapper

# Captured at import time, before the autouse fixture below replaces the module attribute.
_REAL_REFRESH = run_wrapper._refresh_statistics_best_effort


@pytest.fixture(autouse=True)
def refresh_hook_mock():
    """Every test in this file runs the real main(); without this, the post-batch hook would
    try to reach a real database after each fake script."""
    with patch("batch.run_wrapper._refresh_statistics_best_effort", new=AsyncMock()) as mock:
        yield mock


class TestRunWrapperMain:
    @pytest.mark.asyncio
    async def test_resolves_registry_entry_and_calls_module_main_no_run_id(self):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["trends_batch"]
        fake_registry.get.return_value = {"module": "batch.trends_batch", "kind": "trends"}

        argv = ["--script", "trends_batch", "--env", "metal", "--trigger", "scheduled"]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch("batch.run_wrapper.load_env") as mock_load_env, \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module) as mock_import, \
             patch.object(sys, "argv", ["run_wrapper.py"] + argv):
            await run_wrapper.main()

        mock_load_env.assert_called_once_with("metal")
        mock_import.assert_called_once_with("batch.trends_batch")
        fake_module.main.assert_awaited_once_with(trigger="scheduled", run_id=None)

    @pytest.mark.asyncio
    async def test_passes_through_run_id_when_given(self):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["move_flagged"]
        fake_registry.get.return_value = {"module": "batch.move_flagged", "kind": "move_flagged"}
        run_id = uuid.uuid4()

        argv = ["--script", "move_flagged", "--env", "general", "--trigger", "manual",
                "--run-id", str(run_id)]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", ["run_wrapper.py"] + argv):
            await run_wrapper.main()

        fake_module.main.assert_awaited_once_with(trigger="manual", run_id=run_id)

    @pytest.mark.asyncio
    async def test_unknown_script_name_is_rejected_by_argparse(self):
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["trends_batch"]

        argv = ["--script", "not_a_real_script", "--env", "metal", "--trigger", "scheduled"]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch.object(sys, "argv", ["run_wrapper.py"] + argv):
            with pytest.raises(SystemExit):
                await run_wrapper.main()


def _run_argv(script: str) -> list[str]:
    return ["run_wrapper.py", "--script", script, "--env", "metal", "--trigger", "scheduled"]


def _registry_for(script: str) -> MagicMock:
    registry = MagicMock()
    registry.all_names.return_value = [script]
    registry.get.return_value = {"module": f"batch.{script}", "kind": script}
    return registry


class TestPostBatchStatisticsRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_after_a_successful_script(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("trends_batch")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("trends_batch")):
            await run_wrapper.main()

        refresh_hook_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_does_not_refresh_when_the_script_fails(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("trends_batch")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("trends_batch")):
            with pytest.raises(RuntimeError, match="boom"):
                await run_wrapper.main()

        refresh_hook_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_refresh_after_build_statistics_itself(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("build_statistics")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("build_statistics")):
            await run_wrapper.main()

        refresh_hook_mock.assert_not_awaited()


class TestRefreshStatisticsBestEffort:
    @pytest.mark.asyncio
    async def test_calls_build_statistics_refresh_snapshot(self):
        with patch("batch.build_statistics.refresh_snapshot", new=AsyncMock()) as refresh_mock:
            await _REAL_REFRESH()

        refresh_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_swallows_refresh_failures(self, caplog):
        with caplog.at_level(logging.WARNING, logger="batch.run_wrapper"), \
             patch("batch.build_statistics.refresh_snapshot",
                   new=AsyncMock(side_effect=RuntimeError("db down"))):
            await _REAL_REFRESH()  # must not raise

        warnings = [r for r in caplog.records
                    if r.name == "batch.run_wrapper" and r.levelno == logging.WARNING
                    and "post-batch statistics refresh failed" in r.getMessage()]
        assert len(warnings) == 1
