"""
Unit tests for batch/run_wrapper.py's argument resolution and dispatch. Mocks
importlib.import_module and BatchRegistry -- no real batch script or DB involved.
"""
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.run_wrapper as run_wrapper


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
