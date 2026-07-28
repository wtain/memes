import asyncio
import logging
import os
import subprocess
import sys
import threading
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


async def _wait_for_process(proc: subprocess.Popen) -> int:
    """Wait for proc to exit without blocking backend shutdown on it.

    Deliberately NOT asyncio.to_thread(proc.wait): that dispatches to the event
    loop's *default* ThreadPoolExecutor, whose worker threads are non-daemon --
    both asyncio.Runner.close() (via shutdown_default_executor(), which has no
    timeout on Python 3.11, the version this project targets) and the
    interpreter's own atexit hook (concurrent.futures.thread._python_exit)
    block waiting for that thread to finish before letting the process exit.
    Net effect, empirically confirmed: the backend would hang on shutdown for
    as long as a scheduled job is still running (up to max_runtime_minutes) --
    directly contradicting the intent, which is for the backend to exit
    promptly while the child subprocess keeps running as an orphan.

    A manually-created daemon thread (not obtained from any ThreadPoolExecutor)
    sidesteps this: it's invisible to both Runner.close()'s
    shutdown_default_executor() (which only touches the loop's *default*
    executor) and to concurrent.futures.thread._python_exit's atexit join
    (which only tracks threads it created itself) -- so the interpreter can
    exit immediately even while this thread is still blocked in proc.wait().
    This is safe for the child: a thread blocked in proc.wait() is only
    polling/blocking on the child's exit status, not holding anything that
    keeps the child alive or sends it any signal -- abandoning the thread at
    interpreter exit does not touch the child OS process at all, so it keeps
    running exactly as intended either way (see _spawn's docstring for why
    subprocess.Popen itself is what actually guarantees that).
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[int] = loop.create_future()

    def _deliver_result(fut: "asyncio.Future[int]", result: int) -> None:
        if not fut.done():
            fut.set_result(result)

    def _wait_in_thread() -> None:
        returncode = proc.wait()
        try:
            loop.call_soon_threadsafe(_deliver_result, future, returncode)
        except RuntimeError:
            # Loop already closed (e.g. interpreter shutting down) -- nobody is
            # awaiting this future anymore, so there's nothing to deliver to.
            # Cancellation of the coroutine that originally awaited this future
            # doesn't need to (and can't) stop this thread either -- it just
            # keeps running harmlessly in the background, exactly like the
            # child process itself.
            pass

    threading.Thread(target=_wait_in_thread, daemon=True).start()
    return await future


async def _spawn(job: dict, app_env: str) -> None:
    """Launch job['script'] (a registry-resolvable name) via batch/run_wrapper.py
    as a subprocess and wait for it to exit.

    Uses subprocess.Popen (not asyncio.create_subprocess_exec) to create the
    subprocess. This is deliberate, not a style choice: asyncio's own
    subprocess transport (BaseSubprocessTransport) kills its child during its
    GC/close() path if the transport is collected before the child has been
    reaped -- which happens whenever the coroutine awaiting proc.wait() is
    cancelled and nothing else references the transport. That's exactly what
    happens on real backend shutdown: uvicorn's actual teardown path
    (Server.run() -> asyncio.run() -> `with asyncio.Runner(...)` ->
    Runner.close() -> _cancel_all_tasks(loop)) cancels EVERY remaining task,
    not just the ones stop_scheduler explicitly cancels -- so no amount of
    "detach this into its own task and hold a strong reference" (see git
    history: this module briefly did exactly that) survives a real shutdown,
    since Runner.close() cancels the detached task too, moments later.
    subprocess.Popen sidesteps the whole problem: its __del__ never kills the
    child (it only emits a ResourceWarning about an unreaped process), so
    there is no GC/cancellation path on the asyncio side that can reach it at
    all, regardless of which task (if any) is cancelled or when. This makes
    "in-flight subprocesses are never killed on backend shutdown" hold
    unconditionally rather than depending on transport-GC timing or which task
    tree the wait happens to run inside.

    Waits for completion via _wait_for_process (a manually-created daemon
    thread), not asyncio.to_thread -- see that function's docstring for why:
    to_thread's non-daemon executor thread would otherwise block backend
    shutdown for as long as this subprocess is still running.
    """
    log_path = Path("logs") / f"scheduler-{job['name']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("scheduler: job %s launching subprocess", job["name"])
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "batch.run_wrapper",
             "--script", job["script"], "--env", app_env, "--trigger", "scheduled"],
            stdout=log_file, stderr=log_file,
        )
        returncode = await _wait_for_process(proc)

    if returncode == 0:
        logger.info("scheduler: job %s exited with code %s", job["name"], returncode)
    else:
        logger.warning("scheduler: job %s exited with code %s", job["name"], returncode)


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
