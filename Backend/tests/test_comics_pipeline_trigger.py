"""start_comics_pipeline (epic multi-image-comics, C5): the build's best-effort start of comics_pipeline through
AdminBatchService.trigger_run, with its own session. Session, repository and spawn are mocked."""
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from Backend.app.services import comics_pipeline_trigger
from Backend.app.services.comics_pipeline_trigger import start_comics_pipeline
from repository.batch_runs import BatchAlreadyRunningError

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def real_registry(monkeypatch):
    # BatchRegistry reads environments/batch_registry.yaml relative to the cwd, which is the repo root in production.
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def session(monkeypatch):
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(comics_pipeline_trigger, "AsyncSessionLocal", factory)
    return session


@pytest.fixture
def repo(monkeypatch):
    repo = AsyncMock()
    monkeypatch.setattr(comics_pipeline_trigger, "BatchRunRepository", MagicMock(return_value=repo))
    return repo


@pytest.fixture
def spawn(monkeypatch):
    calls = []

    async def fake_fire_and_forget(coro):
        calls.append(coro.cr_frame.f_locals["args"])
        coro.close()  # never awaited: no real subprocess
    monkeypatch.setattr("Backend.app.services.admin_batch_service.fire_and_forget", fake_fire_and_forget)
    return calls


async def test_returns_run_id_on_start(session, repo, spawn):
    run_id = uuid.uuid4()
    repo.create_run.return_value = run_id

    assert await start_comics_pipeline() == str(run_id)

    repo.create_run.assert_awaited_once_with(kind="comics_pipeline", trigger="manual")
    session.commit.assert_awaited_once()
    assert len(spawn) == 1
    args = spawn[0]
    assert args[args.index("--script") + 1] == "comics_pipeline"
    assert args[args.index("--run-id") + 1] == str(run_id)


async def test_busy_run_is_swallowed(session, repo, spawn, caplog):
    repo.create_run.side_effect = BatchAlreadyRunningError("comics_pipeline")

    with caplog.at_level(logging.INFO, logger=comics_pipeline_trigger.logger.name):
        assert await start_comics_pipeline() is None

    assert spawn == []
    records = [r for r in caplog.records if r.name == comics_pipeline_trigger.logger.name]
    assert [r.levelno for r in records] == [logging.INFO]


async def test_spawn_failure_is_logged_not_raised(session, repo, monkeypatch, caplog):
    repo.create_run.return_value = uuid.uuid4()

    async def broken(coro):
        coro.close()
        raise OSError("cannot spawn")
    monkeypatch.setattr("Backend.app.services.admin_batch_service.fire_and_forget", broken)

    with caplog.at_level(logging.INFO, logger=comics_pipeline_trigger.logger.name):
        assert await start_comics_pipeline() is None

    records = [r for r in caplog.records if r.name == comics_pipeline_trigger.logger.name]
    assert [r.levelno for r in records] == [logging.WARNING]


async def test_session_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(comics_pipeline_trigger, "AsyncSessionLocal", MagicMock(side_effect=RuntimeError("no db")))

    with caplog.at_level(logging.INFO, logger=comics_pipeline_trigger.logger.name):
        assert await start_comics_pipeline() is None

    records = [r for r in caplog.records if r.name == comics_pipeline_trigger.logger.name]
    assert [r.levelno for r in records] == [logging.WARNING]
