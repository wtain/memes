"""Database pool metrics: connections checked out, overflow in use and the time a caller waited for a connection.

Installed on a SQLAlchemy pool (Storage/db.py does it for the engine pool when metrics are on). Counting is done here (not read from
the pool, whose checkin event fires before the slot is released) and the gauges are set to absolute values, so they cannot drift.
Everything is swallowed on failure: the pool behaves exactly as without this module.

Limit: the listeners and the wrapped `connect` belong to this pool object. `pool.recreate()` (and `engine.dispose()`, which swaps in
a recreated pool) returns a new pool without the `connect` wrapper, so after one the wait histogram stops and the checkout gauges may
stop too. The application never disposes its engine while serving, so this is accepted; re-install after a dispose if that changes.
"""
import threading
import time

from sqlalchemy import event

from Backend.app.observability import metrics


def install_pool_metrics(pool) -> None:
    lock = threading.Lock()
    state = {"out": 0}

    def publish(delta: int) -> None:
        try:
            with lock:
                state["out"] = max(0, state["out"] + delta)
                out = state["out"]
            metrics.POOL_CHECKED_OUT.set(out)
            size = getattr(pool, "size", None)   # connections beyond pool_size are the overflow ones
            metrics.POOL_OVERFLOW.set(max(0, out - size()) if callable(size) else 0)
        except Exception:
            pass

    event.listen(pool, "checkout", lambda *args, **kwargs: publish(1))
    event.listen(pool, "checkin", lambda *args, **kwargs: publish(-1))

    original = pool.connect

    def timed_connect():
        started = time.perf_counter()
        try:
            return original()
        finally:
            try:
                metrics.POOL_WAIT_SECONDS.observe(time.perf_counter() - started)
            except Exception:
                pass

    pool.connect = timed_connect
