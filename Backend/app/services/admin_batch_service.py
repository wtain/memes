import logging
import os
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.types.generated import batchprogress
from Backend.app.batch_subprocess import build_log_path, fire_and_forget, spawn_and_track
from Storage.db import AsyncSessionLocal
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from batch.registry import BatchRegistry

_logger = logging.getLogger(__name__)

_PROGRESS_DEFAULTS = {"label": None, "unit": None, "done": None, "total": None,
                      "rate_per_min": None, "step": None}


def _normalise_progress(raw) -> dict | None:
    """Fills keys a reporter omitted (e.g. no `step`) and validates against the generated model.
    A malformed stored value becomes None so one bad row can never 500 the runs list; only the
    exception class is logged (no values)."""
    if raw is None:
        return None
    try:
        value = {**_PROGRESS_DEFAULTS, **raw}
        batchprogress.Schema.model_validate(value)
        return value
    except Exception as exc:
        _logger.warning("ignoring malformed batch progress: %s", type(exc).__name__)
        return None


# Drivers run their steps in their own process, each step self-tracked under its own kind. When the driver's process
# dies, those rows would stay `started` for good and make the next run of the step skip as busy, so they are failed
# with the driver. Keep in sync with the STEPS of batch/<driver>.py.
DRIVER_CHILD_KINDS = {
    "description_pipeline": [
        "build_tags_from_ocr", "build_description_note_lemmas", "build_description_note_embeddings",
        "build_tags_from_notes", "build_image_descriptions", "build_image_description_embeddings",
        "build_description_lemmas", "build_tags_from_descriptions", "build_meme_profiles",
        "build_meme_profile_embeddings",
    ],
    "comics_pipeline": [
        "extract_text_from_memes", "build_image_embeddings", "build_ocr_lemmas", "build_tags_from_ocr",
        "classify_text_heavy", "build_ocr_text_embeddings",
    ],
    "recut_pipeline": ["create_recut_cuts", "build_image_embeddings", "refresh_recut_cuts"],
}

_STATUS_MAP = {"started": "running", "completed": "completed", "failed": "failed"}
_STORED_STATUS = {api: stored for stored, api in _STATUS_MAP.items()}


async def fail_run_if_still_started(run_id: uuid.UUID, reason: str) -> None:
    """Marks the run failed when its child never did (spawn error, argparse/import error,
    hard kill). Transitions only from `started`, so a run the child already finished
    (completed/failed/aborted) is never overwritten. Own session: the request's is gone."""
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run = await repo.get_run(run_id, with_progress=False)  # works before the progress migration
        if run is None or run.status != "started":
            return
        await repo.fail(run_id, error=f"orphaned: {reason}")
        children = DRIVER_CHILD_KINDS.get(run.kind)
        if children:
            await repo.fail_started_of_kinds(children, since=run.created_at, error=f"orphaned: {reason} (driver {run.kind})")
        await session.commit()


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
        async def _on_abnormal_exit(reason: str) -> None:
            await fail_run_if_still_started(run_id, reason)

        await fire_and_forget(
            spawn_and_track(args, log_path, label=batch_name, on_abnormal_exit=_on_abnormal_exit)
        )

        return {"run_id": str(run_id), "status": "running"}

    async def get_run(self, run_id: uuid.UUID) -> dict:
        run = await self.repo.get_run(run_id)
        if run is None or run.kind not in self._admin_kinds():
            raise HTTPException(status_code=404, detail="Run not found")
        return self._to_response(run)

    async def list_runs(self, limit: int, offset: int, status: str | None = None, name: str | None = None) -> dict:
        """`status` is the API value (running|completed|failed|aborted); `name` a case-insensitive part of the batch
        name. Filtering happens in the query so the page and `total` describe the filtered list."""
        kinds = self._admin_kinds(name)
        if name and not kinds:
            return {"items": [], "total": 0}
        stored = _STORED_STATUS.get(status, status)
        items, total = await self.repo.list_runs(kinds=kinds, limit=limit, offset=offset, status=stored)
        return {"items": [self._to_response(run) for run in items], "total": total}

    async def list_batch_names(self) -> dict:
        return {"names": self.registry.all_names()}

    def _admin_kinds(self, name_part: str | None = None) -> list[str]:
        """Every registered batch's kind, in one place -- the registry (fresh-read on
        every call, see BatchRegistry) is the single source of truth for which batches
        are admin-triggerable, so this needs no separate hardcoded list to stay in sync."""
        needle = name_part.strip().lower() if name_part else ""
        return [self.registry.get(name)["kind"] for name in self.registry.all_names() if needle in name.lower()]

    def _to_response(self, run) -> dict:
        return {
            "run_id": str(run.run_id),
            "batch_name": self.registry.name_for_kind(run.kind),
            "trigger": run.trigger,
            "status": _STATUS_MAP.get(run.status, run.status),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "error": run.error,
            "progress": _normalise_progress(run.progress),
        }
