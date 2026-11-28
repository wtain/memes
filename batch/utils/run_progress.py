"""Live progress of a tracked batch run, stored best effort on ``batch_runs.progress``.

See docs/superpowers/specs/2026-11-23-batch-progress-reporting-design.md ("Reporting side").

``report_progress`` and ``report_step`` are plain functions: they never raise, never block and
do nothing outside a tracked run (``current_run_id`` unset) or without a running event loop.
Writes are throttled per run (``batch_progress.min_interval_seconds``; the first report, the first
report with ``done >= total`` and every step pass), at most one write is in flight per run, and a
report that arrives while one is in flight replaces the value written next (latest wins). A new
step index clears the previous step's counts. A value that is not a valid count (``'x'``, ``inf``)
is dropped and logged by exception class only. The tracking context calls ``drain_progress`` on
exit so the last value lands and no task is left pending.

Only counts, a unit from ``PROGRESS_UNITS`` and fixed labels chosen by code are stored (no file
names, ids or text), and a failed write is logged without data.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger(__name__)

PROGRESS_UNITS: frozenset[str] = frozenset(
    {"images", "pairs", "descriptions", "profiles", "files", "items"}
)
_DEFAULT_UNIT = "items"
_DEFAULT_LABEL = "batch"
_MAX_LABEL_LEN = 64
_CANCEL_GRACE_SECONDS = 1.0  # drain waits this long for a cancelled write to end, then gives up

current_run_id: ContextVar[uuid.UUID | None] = ContextVar("batch_current_run_id", default=None)

# Test seams: the throttle clock and the config reader.
_clock = time.monotonic


def _config() -> tuple[bool, float]:
    from config.settings import settings

    return bool(settings.BATCH_PROGRESS.ENABLED), float(settings.BATCH_PROGRESS.MIN_INTERVAL_SECONDS)


@dataclass
class _RunState:
    progress: dict | None = None  # label, unit, done, total, rate_per_min of the latest report
    step: dict | None = None  # index, count, label of the latest step
    final: bool = False  # the latest reported value had done >= total
    dirty: bool = False  # the latest value has not been written yet
    urgent: bool = False  # the latest value is due (interval elapsed, final, step or drain)
    last_write: float | None = None  # _clock() when the last write started
    task: asyncio.Task | None = None  # the in-flight write, if any


_states: dict[uuid.UUID, _RunState] = {}
_tasks: set[asyncio.Task] = set()  # strong references to every write task


# --- the value ------------------------------------------------------------------------------


def _label(value) -> str:
    if not isinstance(value, str) or not value:
        return _DEFAULT_LABEL
    return value[:_MAX_LABEL_LEN]


def _count(value) -> int | None:
    """A non-negative int, or None. Raises on a non-numeric or non-finite value (``int(inf)``
    is an OverflowError, ``int(nan)`` and ``int('x')`` a ValueError)."""
    if value is None:
        return None
    return max(0, int(value))


def _rate(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return round(max(0.0, value), 1)


def build_value(label, unit, done, total, rate_per_min, step) -> dict:
    """The progress value to store: unit validated (unknown -> ``items``), ``done`` clamped to
    ``total``, ``total`` kept ``None`` when unknown, negative numbers dropped to 0."""
    done_v = _count(done)
    total_v = _count(total)
    if done_v is not None and total_v is not None:
        done_v = min(done_v, total_v)
    value = {
        "label": _label(label),
        "unit": unit if unit in PROGRESS_UNITS else _DEFAULT_UNIT,
        "done": done_v,
        "total": total_v,
        "rate_per_min": _rate(rate_per_min),
    }
    if step is not None:
        value["step"] = {
            "index": _count(step.get("index")) or 0,
            "count": _count(step.get("count")) or 0,
            "label": _label(step.get("label")),
        }
    return value


def _compose(state: _RunState) -> dict:
    if state.progress is not None:
        p = state.progress
        return build_value(p["label"], p["unit"], p["done"], p["total"], p["rate_per_min"], state.step)
    step = state.step or {}
    return build_value(step.get("label"), _DEFAULT_UNIT, None, None, None, state.step)


# --- the write ------------------------------------------------------------------------------


async def write_progress(run_id: uuid.UUID, value: dict) -> None:
    """One UPDATE on its own short session (a finished run is left untouched)."""
    from repository.batch_runs import BatchRunRepository
    from Storage.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await BatchRunRepository(session).set_progress(run_id, value)
        await session.commit()


_writer = write_progress


async def _flush(run_id: uuid.UUID, state: _RunState) -> None:
    # Loops while a due report arrived during the previous write: latest wins, one write at a
    # time. A report that was not due only marks the value dirty; it waits for the next due
    # report or for drain_progress, so the throttle also holds while a write is in flight.
    try:
        while state.urgent:
            state.urgent = False
            state.dirty = False
            try:
                value = _compose(state)
            except Exception as e:  # a value that cannot be built is dropped, not retried
                log.warning("batch progress value dropped (%s)", type(e).__name__)
                state.progress = None
                state.step = None
                continue
            state.last_write = _clock()
            try:
                await _writer(run_id, value)
            except Exception as e:  # D3: best effort, logged without data
                log.warning("batch progress write failed (%s)", type(e).__name__)
    finally:
        if state.task is asyncio.current_task():
            state.task = None


def _ensure_write(run_id: uuid.UUID, state: _RunState, loop: asyncio.AbstractEventLoop) -> None:
    state.dirty = True
    state.urgent = True
    if state.task is not None and not state.task.done():
        return  # the in-flight task writes the latest value when it finishes
    task = loop.create_task(_flush(run_id, state))
    state.task = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _context() -> tuple[uuid.UUID, asyncio.AbstractEventLoop, float] | None:
    run_id = current_run_id.get()
    if run_id is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    enabled, interval = _config()
    if not enabled:
        return None
    return run_id, loop, interval


# --- public API -----------------------------------------------------------------------------


def report_progress(
    label: str | None,
    unit: str,
    done: int,
    total: int | None,
    rate_per_min: float | None = None,
) -> None:
    """Report ``done`` of ``total`` for the current run. Sync, never raises."""
    try:
        ctx = _context()
        if ctx is None:
            return
        run_id, loop, interval = ctx
        # Validate before touching the state: a bad value is dropped here (logged by class
        # name only) and the previous good value stays the one written next.
        build_value(label, unit, done, total, rate_per_min, None)
        state = _states.setdefault(run_id, _RunState())
        state.progress = {
            "label": label, "unit": unit, "done": done, "total": total, "rate_per_min": rate_per_min,
        }
        final = total is not None and done is not None and done >= total
        # Only the first report that reaches the total bypasses the throttle; repeats with
        # done >= total (an estimated total) follow the interval like any other report.
        first_final = final and not state.final
        state.final = final
        due = state.last_write is None or _clock() - state.last_write >= interval
        if first_final or due:
            _ensure_write(run_id, state, loop)
        else:
            state.dirty = True  # throttled: written by a later due report or by drain_progress
    except Exception as e:
        log.warning("batch progress report failed (%s)", type(e).__name__)


def report_step(index: int, count: int, label: str) -> None:
    """Report step ``index`` (1-based) of ``count`` for the current (driver) run. Bypasses the
    throttle. Sync, never raises."""
    try:
        ctx = _context()
        if ctx is None:
            return
        run_id, loop, _ = ctx
        _count(index), _count(count)  # validate before touching the state
        state = _states.setdefault(run_id, _RunState())
        if state.step is not None and state.step.get("index") != index:
            # A new step: the previous step's counts must not show on the driver's row.
            state.progress = None
            state.final = False
        state.step = {"index": index, "count": count, "label": label}
        _ensure_write(run_id, state, loop)
    except Exception as e:
        log.warning("batch progress step report failed (%s)", type(e).__name__)


async def drain_progress(run_id: uuid.UUID, timeout: float = 5.0) -> None:
    """Await the run's in-flight write and one final write of the latest unwritten value (at
    most ``timeout`` seconds; a write still running then is cancelled and given one more second), then forget the run.
    Never raises (except the caller's own cancellation)."""
    state = _states.get(run_id)
    if state is None:
        return
    try:
        if state.dirty:
            # One final write of the latest value, after the in-flight one if any.
            _ensure_write(run_id, state, asyncio.get_running_loop())
        task = state.task
        if task is not None:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                task.cancel()
                # A writer that ignores the cancellation must not hold the run's exit: wait a
                # little, then give up (the task stays referenced in _tasks until it ends).
                await asyncio.wait({task}, timeout=_CANCEL_GRACE_SECONDS)
                log.warning("batch progress drain timed out")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("batch progress drain failed (%s)", type(e).__name__)
    finally:
        if _states.get(run_id) is state:
            del _states[run_id]
