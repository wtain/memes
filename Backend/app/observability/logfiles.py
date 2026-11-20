"""Bounded log files, the request-id log filter and sampled log lines (design section 6).

Nothing here may stop the backend: a missing, unwritable or full log directory is reported once on stdout and the process keeps
logging to stdout only. Nothing here depends on `observability.metrics.enabled`.
"""
import contextvars
import logging
import logging.handlers
import os
import re
import sys
import threading
import time

NO_REQUEST = "-"
# The request id of the request being served; set by RequestIdMiddleware, read by the log filter. Sync endpoints run in a copy of
# the context, so they see it too.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("memes_request_id", default=NO_REQUEST)

FILE_HANDLER_MARK = "_memes_log_file"
_SLOT_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class RequestIdFilter(logging.Filter):
    """Adds `%(request_id)s` to every record (a dash outside a request). Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


REQUEST_ID_FILTER = RequestIdFilter()


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A full disk or a vanished directory while logging must not raise into the request: the record is dropped."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 (stdlib name)
        return None

    def doRollover(self) -> None:  # noqa: N802
        try:
            super().doRollover()
        except Exception:
            # Rotation failed (permissions, full disk): keep appending to the current file rather than losing the handler.
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    self.stream = None


def _warn(message: str) -> None:
    try:
        print(f"memes logging: {message}", file=sys.stdout, flush=True)
    except Exception:
        pass


def install(directory: str | None, slot: str, max_bytes: int, backup_count: int) -> list[logging.Handler]:
    """File handlers for `backend-<slot>.log` in `directory`, bounded to `max_bytes` x (1 + `backup_count`) per slot.

    Returns [] (and prints one warning on stdout, except for an unset directory, which is simply "files off") when the directory is
    missing, unwritable or full. Never raises. The handlers carry the request-id filter but no formatter: the caller sets it."""
    try:
        if not directory:
            return []
        if not isinstance(slot, str) or not _SLOT_RE.match(slot):
            _warn(f"invalid log slot {slot!r}: log files off")
            return []
        if not os.path.isdir(directory):
            _warn("log directory does not exist: log files off")
            return []
        max_bytes = int(max_bytes)
        backup_count = int(backup_count)
        if max_bytes <= 0 or backup_count < 0:
            _warn("invalid log file bounds: log files off")
            return []
        path = os.path.join(directory, f"backend-{slot}.log")
        handler = SafeRotatingFileHandler(path, mode="a", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        # A full disk accepts the open but fails the first write: probe once so "full" is reported now, not silently later.
        handler.stream.write("")
        handler.stream.flush()
        setattr(handler, FILE_HANDLER_MARK, True)
        handler.addFilter(REQUEST_ID_FILTER)
        return [handler]
    except Exception as error:
        _warn(f"log files off ({type(error).__name__})")
        return []


def remove_installed(loggers) -> None:
    """Closes and detaches the file handlers `install` made (a second lifespan in one process, tests)."""
    for lgr in loggers:
        for handler in list(lgr.handlers):
            if getattr(handler, FILE_HANDLER_MARK, False):
                lgr.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass


# --- sampled lines ------------------------------------------------------------------------------------------------------------------

_sample_lock = threading.Lock()
_last_logged: dict[str, float] = {}
_suppressed: dict[str, int] = {}
_MAX_KEYS = 256


def log_sampled(logger: logging.Logger, key: str, message: str, *args, interval_s: float = 60.0, level: int = logging.WARNING) -> bool:
    """Logs `message` at most once per `interval_s` per `key` (a fixed, bounded vocabulary: a scope or a route template), adding the
    number of lines suppressed since the last one. Arguments must already be safe (scopes, templates, numbers)."""
    try:
        now = time.monotonic()
        with _sample_lock:
            last = _last_logged.get(key)
            if last is not None and now - last < interval_s:
                _suppressed[key] = _suppressed.get(key, 0) + 1
                return False
            if last is None and len(_last_logged) >= _MAX_KEYS:
                return False
            _last_logged[key] = now
            skipped = _suppressed.pop(key, 0)
        logger.log(level, message + (" (+%d similar suppressed)" if skipped else ""), *args, *((skipped,) if skipped else ()))
        return True
    except Exception:
        return False


def reset_sampling() -> None:
    with _sample_lock:
        _last_logged.clear()
        _suppressed.clear()


def safe_text(value, limit: int = 64) -> str:
    """A client-controlled string made safe to log: repr() escapes newlines and control characters (no log forging), cut to `limit`."""
    try:
        text = repr(str(value))
        return text if len(text) <= limit + 2 else text[:limit] + "..." + text[0]
    except Exception:
        return "<unprintable>"
