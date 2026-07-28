"""Popen+daemon-thread subprocess spawn/wait mechanism, extracted from
Backend/app/scheduler.py so a later admin HTTP endpoint can reuse it without
reaching into scheduler internals. See scheduler.py's git history (this
module's direct ancestor) for the full two-round history of why this is built
the way it is -- a killed-child bug and a blocked-shutdown bug were both found
and fixed there.
"""
import asyncio
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_in_flight: set[asyncio.Task] = set()


def build_log_path(env: str, script: str) -> Path:
    """One file per invocation, nested by environment. No PID, no run_id in the name --
    both would create a chicken-and-egg problem (Popen's stdout= file must be opened
    before the child exists, so the name can't depend on anything only the child would
    know), and there is deliberately no link back to a specific batch_runs.run_id -- an
    operator correlates by script name and timestamp against a run's created_at instead.
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    return Path("logs") / env / f"{script}_{timestamp}.log"


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


async def spawn_and_track(args: list[str], log_path: Path) -> int:
    """Spawn args via Popen (see this module's git-history ancestor, scheduler.py's
    _spawn docstring, for why Popen specifically -- its __del__ never kills the child,
    unlike asyncio's own subprocess transport), redirect stdout/stderr to log_path,
    await completion via _wait_for_process's daemon thread (survives cancellation and
    doesn't block shutdown), log the exit code, and return it. Caller decides whether
    to await this inline (the scheduler) or via fire_and_forget (the admin endpoint).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("batch_subprocess: launching %s", args)
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(args, stdout=log_file, stderr=log_file)
        returncode = await _wait_for_process(proc)

    if returncode == 0:
        logger.info("batch_subprocess: %s exited with code %s", args[0], returncode)
    else:
        logger.warning("batch_subprocess: %s exited with code %s", args[0], returncode)
    return returncode


def _on_fire_and_forget_done(task: asyncio.Task) -> None:
    _in_flight.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("batch_subprocess: fire-and-forget task failed", exc_info=exc)


async def fire_and_forget(coro) -> None:
    """Creates an asyncio.Task for coro, holding a strong reference in a module-level
    set until it's done (a bare asyncio.create_task with no reference kept anywhere
    can be garbage-collected before it ever runs), and logs -- rather than silently
    swallows -- any exception the task raises. For callers that must not block on the
    task's completion (the admin endpoint's HTTP response)."""
    task = asyncio.create_task(coro)
    _in_flight.add(task)
    task.add_done_callback(_on_fire_and_forget_done)
