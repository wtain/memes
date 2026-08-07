import os
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.batch_subprocess import build_log_path, fire_and_forget, spawn_and_track
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from batch.registry import BatchRegistry

_ADMIN_KINDS = ["trends", "move_flagged", "unregister_deleted_images"]

_STATUS_MAP = {"started": "running", "completed": "completed", "failed": "failed"}


class AdminBatchService:
    def __init__(self, repo: BatchRunRepository, session: AsyncSession, registry: BatchRegistry | None = None):
        self.repo = repo
        # Accepted directly (not read off repo._session) so trigger_run's explicit
        # early commit -- see below -- doesn't reach into the repository's internals.
        # The router constructs both from the same Depends(get_async_db) session, so
        # this doesn't complicate the dependency-injection wiring at all.
        self.session = session
        self.registry = registry or BatchRegistry()

    async def trigger_run(self, batch_name: str) -> dict:
        entry = self.registry.get(batch_name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Unknown batch: {batch_name}")

        try:
            run_id = await self.repo.create_run(kind=entry["kind"], trigger="manual")
        except BatchAlreadyRunningError as e:
            raise HTTPException(
                status_code=409, detail=f"{batch_name} is already running"
            ) from e

        # Deliberate exception to the usual get_async_db convention: the spawned
        # subprocess is a separate OS process with its own DB connection and must
        # see this row the instant it starts, not whenever the request handler
        # eventually returns and get_async_db's post-handler commit fires.
        await self.session.commit()

        app_env = os.environ.get("APP_ENV", "general")
        log_path = build_log_path(app_env, batch_name)
        args = [sys.executable, "-m", "batch.run_wrapper",
                "--script", batch_name, "--env", app_env, "--trigger", "manual",
                "--run-id", str(run_id)]
        await fire_and_forget(spawn_and_track(args, log_path, label=batch_name))

        return {"run_id": str(run_id), "status": "running"}

    async def get_run(self, run_id: uuid.UUID) -> dict:
        run = await self.repo.get_run(run_id)
        if run is None or run.kind not in _ADMIN_KINDS:
            raise HTTPException(status_code=404, detail="Run not found")
        return self._to_response(run)

    async def list_runs(self, limit: int, offset: int) -> dict:
        items, total = await self.repo.list_runs(kinds=_ADMIN_KINDS, limit=limit, offset=offset)
        return {"items": [self._to_response(run) for run in items], "total": total}

    def _to_response(self, run) -> dict:
        return {
            "run_id": str(run.run_id),
            "batch_name": self.registry.name_for_kind(run.kind),
            "trigger": run.trigger,
            "status": _STATUS_MAP.get(run.status, run.status),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "error": run.error,
        }
