"""gunicorn configuration of the prod backend (copied to /app/gunicorn.conf.py in the image; gunicorn loads
./gunicorn.conf.py from its working directory /app by default, so the command passes no -c).

Logging (observability design section 6): each worker writes `backend-<slot>.log` (slot w0 / w1, a worker keeps the lowest free slot
across restarts, so the files stay bounded at two) through Backend/app/observability/logfiles.py, which main.py installs in the worker
from the env var MEMES_LOG_SLOT set here. The master process writes `backend-master.log` (5 MB x 2) with gunicorn's own messages.
The directory comes from settings observability.log_file.dir. A missing or unwritable directory is never fatal.
"""
import logging
import os
import sys

MASTER_MAX_BYTES = 5 * 1024 * 1024
MASTER_BACKUPS = 2
_MASTER_HANDLERS: list = []


def _log_dir():
    try:
        sys.path.insert(0, os.getcwd())
        from config.settings import settings
        return settings.get("observability.log_file.dir")
    except Exception:
        return None


def _metrics_dir():
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR") or None


def _reset_metrics_dir():
    path = _metrics_dir()
    if not path:
        return
    try:
        os.makedirs(path, exist_ok=True)
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) or os.path.islink(full):
                os.remove(full)
    except Exception:
        pass


def on_starting(server):
    # Metrics are optional: PROMETHEUS_MULTIPROC_DIR (set in the environment before the first prometheus_client import, which
    # happens in the workers) names the directory of the multiprocess files. Stale files of a previous master are removed here.
    _reset_metrics_dir()
    try:
        from Backend.app.observability import logfiles
        handlers = logfiles.install(_log_dir(), "master", MASTER_MAX_BYTES, MASTER_BACKUPS)
        fmt = logging.Formatter("%(asctime)s %(levelname)-8s [pid:%(process)d] %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
        for handler in handlers:
            handler.setFormatter(fmt)
            server.log.error_log.addHandler(handler)
            _MASTER_HANDLERS.append(handler)
        server.log.info("gunicorn master starting workers=%s", server.num_workers)
    except Exception:
        pass


def pre_fork(server, worker):
    """Runs in the master: the lowest slot not held by a living worker (reaped workers have left server.WORKERS)."""
    used = {getattr(w, "memes_slot", None) for w in server.WORKERS.values()}
    slot = next((f"w{i}" for i in range(max(int(server.num_workers), 1) + len(used)) if f"w{i}" not in used), "w0")
    worker.memes_slot = slot


def post_fork(server, worker):
    """Runs in the new worker: the log slot for main.py, and no master file handles in the worker."""
    os.environ["MEMES_LOG_SLOT"] = getattr(worker, "memes_slot", "w0")
    for handler in _MASTER_HANDLERS:
        try:
            server.log.error_log.removeHandler(handler)
            handler.close()
        except Exception:
            pass
    _MASTER_HANDLERS.clear()


def child_exit(server, worker):
    """Runs in the master when a worker has exited: its live gauges must stop counting (no-op without metrics)."""
    if not _metrics_dir():
        return
    try:
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(worker.pid)
    except Exception:
        pass
