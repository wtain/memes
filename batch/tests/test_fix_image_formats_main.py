"""
Unit tests for batch/fix_image_formats.py's main() contract and registry entry. No real DB.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.fix_image_formats as module
from batch.registry import BatchRegistry


def _session_factory():
    session = MagicMock()
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _metrics():
    metrics = MagicMock()
    metrics.counters_dict.return_value = {"fixed": 1}
    return metrics


@pytest.mark.asyncio
async def test_main_with_run_id_finishes_the_existing_run():
    run_id = uuid.uuid4()
    finish = MagicMock()
    finish.return_value.__aenter__ = AsyncMock(return_value=None)
    finish.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(module, "finish_existing_run", finish), \
         patch.object(module, "AsyncSessionLocal", _session_factory()), \
         patch.object(module, "BatchRunRepository", return_value=MagicMock(update_stats=AsyncMock())), \
         patch.object(module, "run", AsyncMock(return_value=_metrics())):
        await module.main(trigger="manual", run_id=run_id)

    finish.assert_called_once_with(run_id)


def test_registered_in_batch_registry():
    entry = BatchRegistry().get("fix_image_formats")

    assert entry == {"module": "batch.fix_image_formats", "kind": "fix_image_formats"}
