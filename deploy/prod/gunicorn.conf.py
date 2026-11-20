"""gunicorn configuration of the prod backend (used with `gunicorn -c deploy/prod/gunicorn.conf.py`; the wiring is task O6a).

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


def on_starting(server):
    # --- O6a: prometheus multiprocess slot ---------------------------------------------------------------------------------------
    # Before the first prometheus_client import: PROMETHEUS_MULTIPROC_DIR must be set; empty the directory here (stale files of a
    # previous master must not be aggregated).
    # -----------------------------------------------------------------------------------------------------------------------------
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


# --- O6a: prometheus multiprocess slot (child_exit -> multiprocess.mark_process_dead(worker.pid)) --------------------------------
