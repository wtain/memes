import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from Backend.app.batch_subprocess import build_log_path, spawn_and_track
from config.settings import settings
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _load_job_configs(cfg=settings) -> list[dict]:
    if not cfg.SCHEDULER.ENABLED:
        return []
    result = []
    for job in cfg.SCHEDULER.JOBS:
        try:
            if not job.get("enabled", True):
                continue
            result.append({
                "name": job["name"],
                "script": job["script"],
                # Mandatory: _initial_delay/_should_run both key off this to query
                # BatchRun history. A job that doesn't write BatchRun rows can't be
                # scheduled through this mechanism yet -- see "Jobs without BatchRun
                # integration" in the design spec.
                "batch_run_kind": job["batch_run_kind"],
                # max(..., 1) guards against a misconfigured 0 (or negative) interval
                # turning into a tight asyncio.sleep(0) loop hammering the DB every tick.
                "interval_minutes": max(job["interval_minutes"], 1),
                "max_runtime_minutes": job["max_runtime_minutes"],
            })
        except KeyError as exc:
            # A malformed entry (typo'd/missing required key in settings.yaml) must not
            # crash the whole backend at boot -- this is a supposedly-optional background
            # feature. Skip just this entry and keep loading the rest.
            logger.error(
                "scheduler: skipping malformed job config (name=%s): missing key %s",
                job.get("name", "<unknown>"), exc,
            )
    return result


async def _initial_delay(repo: BatchRunRepository, job: dict) -> float:
    """Seconds to wait before this job's first fire, restart-safe.

    If the most recent run for job['batch_run_kind'] (any status) is younger
    than the configured interval, delay only the remainder rather than firing
    immediately -- so a backend restart (e.g. dev --reload) doesn't re-fire a
    job ahead of schedule.
    """
    interval_seconds = job["interval_minutes"] * 60
    last_run = await repo.get_most_recent_run(job["batch_run_kind"])
    if last_run is None:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - last_run.created_at).total_seconds()
    return max(interval_seconds - elapsed, 0.0)


async def _should_run(repo: BatchRunRepository, job: dict) -> bool:
    """Whether this tick should spawn a new run.

    Recovers from orphaned runs (process/machine killed without reaching
    commit()/fail()): a still-"active" run older than max_runtime_minutes is
    marked failed here so it stops blocking the schedule. Does not commit --
    the caller owns the session/commit.
    """
    active = await repo.get_active_run(job["batch_run_kind"])
    if active is None:
        return True

    age_seconds = (datetime.now(timezone.utc) - active.created_at).total_seconds()
    if age_seconds < job["max_runtime_minutes"] * 60:
        return False

    logger.warning(
        "scheduler: job %s (kind=%s) run %s exceeded max_runtime_minutes=%s -- "
        "marking failed as orphaned",
        job["name"], job["batch_run_kind"], active.run_id, job["max_runtime_minutes"],
    )
    await repo.fail(active.run_id, error="orphaned: presumed crashed or killed")
    return True


async def _run_tick(job: dict, app_env: str) -> None:
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_now = await _should_run(repo, job)
        await session.commit()
    if run_now:
        await _spawn(job, app_env)


async def _safe_tick(job: dict, app_env: str) -> None:
    try:
        await _run_tick(job, app_env)
    except Exception:
        logger.exception("scheduler: job %s tick failed", job["name"])


async def _spawn(job: dict, app_env: str) -> None:
    log_path = build_log_path(app_env, job["script"])
    args = [sys.executable, "-m", "batch.run_wrapper",
            "--script", job["script"], "--env", app_env, "--trigger", "scheduled"]
    await spawn_and_track(args, log_path)


async def _safe_initial_delay(job: dict) -> float:
    """_initial_delay, guarded: a startup-time DB hiccup (not reachable yet,
    mid-restart, ...) must not kill this job's task before it ever enters its
    loop -- fall back to an immediate first tick instead of restart-safe timing
    for this one time, and log so the failure isn't silent.
    """
    try:
        async with AsyncSessionLocal() as session:
            return await _initial_delay(BatchRunRepository(session), job)
    except Exception:
        logger.exception(
            "scheduler: job %s failed to compute initial delay -- falling back to an "
            "immediate first tick",
            job["name"],
        )
        return 0.0


async def _job_loop(job: dict, app_env: str) -> None:
    delay = await _safe_initial_delay(job)

    interval_seconds = job["interval_minutes"] * 60
    while True:
        await asyncio.sleep(delay)
        delay = interval_seconds
        await _safe_tick(job, app_env)


async def start_scheduler() -> list[asyncio.Task]:
    app_env = os.environ.get("APP_ENV", "general")
    jobs = _load_job_configs()
    logger.info(
        "scheduler: starting %d job(s): %s",
        len(jobs), ", ".join(job["name"] for job in jobs) or "<none>",
    )
    return [
        asyncio.create_task(_job_loop(job, app_env), name=job["name"]) for job in jobs
    ]


async def stop_scheduler(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # A job-loop task that died for some other reason must not propagate out of
            # lifespan and take the rest of shutdown down with it.
            logger.exception(
                "scheduler: job-loop task %s ended with an unexpected error", task.get_name()
            )
