import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.create_recut_cuts as module


def _session_factory():
    session = MagicMock()
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _metrics():
    m = MagicMock()
    m.counters_dict.return_value = {"cut": 1}
    return m


@pytest.mark.asyncio
async def test_main_with_run_id_finishes_the_existing_run():
    run_id = uuid.uuid4()
    finish = MagicMock()
    finish.return_value.__aenter__ = AsyncMock(return_value=None)
    finish.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(module, "finish_existing_run", finish), \
         patch.object(module, "AsyncSessionLocal", _session_factory()), \
         patch.object(module, "BatchRunRepository", return_value=MagicMock(update_stats=AsyncMock())), \
         patch.object(module, "load_corpus_ids", return_value=set()), \
         patch.object(module, "settings", MagicMock()), \
         patch.object(module, "run", AsyncMock(return_value=_metrics())):
        await module.main(trigger="manual", run_id=run_id)
    finish.assert_called_once_with(run_id)


@pytest.mark.asyncio
async def test_main_without_run_id_tracks_its_own_run_and_stores_the_stats():
    new_id = uuid.uuid4()
    tracked = MagicMock()
    tracked.return_value.__aenter__ = AsyncMock(return_value=new_id)
    tracked.return_value.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock(update_stats=AsyncMock())
    with patch.object(module, "tracked_run", tracked), \
         patch.object(module, "AsyncSessionLocal", _session_factory()), \
         patch.object(module, "BatchRunRepository", return_value=repo), \
         patch.object(module, "load_corpus_ids", return_value=set()), \
         patch.object(module, "settings", MagicMock()), \
         patch.object(module, "run", AsyncMock(return_value=_metrics())):
        await module.main(trigger="manual")
    tracked.assert_called_once_with(kind="create_recut_cuts", trigger="manual")
    repo.update_stats.assert_awaited_once_with(new_id, cut=1)
