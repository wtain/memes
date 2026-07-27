import asyncio
import functools
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _load_job_configs(cfg=settings) -> list[dict]:
    if not cfg.SCHEDULER.ENABLED:
        return []
    result = []
    for job in cfg.SCHEDULER.JOBS:
        if not job.get("enabled", True):
            continue
        result.append({
            "name": job["name"],
            "module": job["module"],
            "batch_run_kind": job["batch_run_kind"],
            "interval_minutes": job["interval_minutes"],
            "max_runtime_minutes": job["max_runtime_minutes"],
        })
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

    await repo.fail(active.run_id, error="orphaned: presumed crashed or killed")
    return True


# Strong references to in-flight "wait for this spawned subprocess to exit" tasks.
#
# _spawn is deliberately NOT awaited inline by _run_tick (see below) -- it is launched as
# its own asyncio.Task via asyncio.create_task and tracked here. This is required, not
# cosmetic: the job-loop task (the one create_task'd in start_scheduler, and the one
# stop_scheduler cancels on shutdown) must never be the task that's blocked inside
# _spawn's `await proc.wait()`. If it were, cancelling it would unwind that coroutine
# frame, drop the last reference to the asyncio subprocess transport before it has
# observed the child exit, and CPython's BaseSubprocessTransport kills the still-running
# child during its GC/close() path -- violating "in-flight subprocesses are never killed
# on backend shutdown". Keeping a strong reference here (removed via the done-callback
# once the subprocess actually exits) keeps the transport alive independent of whatever
# happens to the job-loop task, so cancelling the scheduler can never reach into an
# in-flight subprocess wait.
_in_flight_spawns: set[asyncio.Task] = set()


def _on_spawn_done(job_name: str, task: asyncio.Task) -> None:
    """Done-callback for a detached _spawn task: untrack it, and -- since nothing
    else awaits this task -- surface any exception it raised (e.g. a bad module
    path, or an OSError opening the log file) the same way _safe_tick surfaces a
    _run_tick failure: logged via this module's own logger, with the job name for
    context. Without this, such an exception would otherwise only ever reach
    asyncio's generic "Task exception was never retrieved" handler at GC time --
    no job-name context, wrong logger, non-deterministic timing.
    """
    _in_flight_spawns.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.exception(
            "scheduler: job %s spawn failed", job_name, exc_info=task.exception()
        )


async def _run_tick(job: dict, app_env: str) -> None:
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_now = await _should_run(repo, job)
        await session.commit()
    if run_now:
        spawn_task = asyncio.create_task(_spawn(job, app_env))
        _in_flight_spawns.add(spawn_task)
        spawn_task.add_done_callback(functools.partial(_on_spawn_done, job["name"]))


async def _safe_tick(job: dict, app_env: str) -> None:
    try:
        await _run_tick(job, app_env)
    except Exception:
        logger.exception("scheduler: job %s tick failed", job["name"])


async def _spawn(job: dict, app_env: str) -> None:
    log_path = Path("logs") / f"scheduler-{job['name']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", job["module"], "--env", app_env,
            stdout=log_file, stderr=log_file,
        )
        await proc.wait()


async def _job_loop(job: dict, app_env: str) -> None:
    async with AsyncSessionLocal() as session:
        delay = await _initial_delay(BatchRunRepository(session), job)

    interval_seconds = job["interval_minutes"] * 60
    while True:
        await asyncio.sleep(delay)
        delay = interval_seconds
        await _safe_tick(job, app_env)


async def start_scheduler() -> list[asyncio.Task]:
    app_env = os.environ.get("APP_ENV", "general")
    return [asyncio.create_task(_job_loop(job, app_env)) for job in _load_job_configs()]


async def stop_scheduler(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
