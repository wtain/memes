"""Abuse bounds of the public write and recording paths (P5a-5, decision 23).

Everything here is driven by tracked settings (`abuse.*`, `history.record_browse`, `popularity.impressions.max_rows_per_call`,
`bug_reports.*`) read at call time. The developer defaults switch every limit off or leave it generous, so a dev backend behaves as
before; settings.prod.yaml (and the per-environment prod overlays) set the real values.

The client of a request is `scope["client"]`. Behind the production proxy that value is the real address because the server is started
with proxy headers trusted only from the proxy (uvicorn `--proxy-headers --forwarded-allow-ips`, gunicorn `forwarded_allow_ips`), which
rewrites the client from `X-Forwarded-For`; this module never parses the header itself, so a stranger cannot pick its own bucket.

State is process-local: the effective limit is workers x the configured rate.
"""
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar

from fastapi import HTTPException, Request

from config.settings import settings

UNKNOWN_CLIENT = "unknown"

# The client of the request being served, set by ClientContextMiddleware so that best-effort recorders deep in the call chain can charge
# the right bucket without every signature carrying the request.
current_client: ContextVar[str] = ContextVar("current_client", default=UNKNOWN_CLIENT)


class TokenBucketLimiter:
    """Token buckets keyed by client. Capacity = `rate_per_min`, refilled continuously. Bounded: at most `max_keys` buckets; buckets that
    have refilled to full carry no information and are swept (at most every `sweep_seconds`); if the table is still full the
    least-recently-used bucket is dropped, so memory stays bounded however many addresses a flood uses."""

    def __init__(self, max_keys: int = 10000, sweep_seconds: float = 10.0, clock=time.monotonic):
        self._max_keys = max_keys
        self._sweep_seconds = sweep_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: "OrderedDict[str, list[float]]" = OrderedDict()   # key -> [tokens, last_refill]
        self._last_sweep = clock()

    def __len__(self) -> int:
        return len(self._buckets)

    def allow(self, key: str, rate_per_min: float, cost: float = 1.0, max_keys: int | None = None) -> bool:
        """True and `cost` tokens spent, or False. `rate_per_min <= 0` means unlimited (nothing is stored)."""
        if rate_per_min <= 0:
            return True
        capacity = float(rate_per_min)
        per_second = capacity / 60.0
        limit = max(1, max_keys if max_keys is not None else self._max_keys)
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                if now - self._last_sweep >= self._sweep_seconds or len(self._buckets) >= limit:
                    self._sweep(now, per_second, capacity)
                while len(self._buckets) >= limit:
                    self._buckets.popitem(last=False)
                bucket = [capacity, now]
                self._buckets[key] = bucket
            else:
                bucket[0] = min(capacity, bucket[0] + (now - bucket[1]) * per_second)
                bucket[1] = now
                self._buckets.move_to_end(key)
            if cost > bucket[0]:
                return False
            bucket[0] -= cost
            return True

    def _sweep(self, now: float, per_second: float, capacity: float) -> None:
        self._last_sweep = now
        full = [k for k, (tokens, last) in self._buckets.items() if tokens + (now - last) * per_second >= capacity]
        for k in full:
            del self._buckets[k]

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


_LIMITERS = {name: TokenBucketLimiter() for name in ("saves", "not_found", "bug_reports", "recording")}
_SETTING_KEYS = {"saves": "abuse.saves_per_min", "not_found": "abuse.not_found_per_min",
                 "bug_reports": "abuse.bug_reports_per_min", "recording": "abuse.recording_per_min"}


def _number(key: str, default: float) -> float:
    raw = settings.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise ValueError(f"{key} must be a number >= 0, got {raw!r}")
    return raw


def _int_setting(key: str, default: int) -> int:
    return int(_number(key, default))


def limiter(name: str) -> TokenBucketLimiter:
    return _LIMITERS[name]


def reset_limiters() -> None:
    for item in _LIMITERS.values():
        item.clear()


def client_key(request: Request) -> str:
    return request.client.host if request.client else UNKNOWN_CLIENT


def _allow(name: str, key: str, cost: float = 1.0) -> bool:
    return _LIMITERS[name].allow(key, _number(_SETTING_KEYS[name], 0), cost, _int_setting("abuse.max_clients", 10000))


def enforce(name: str, request: Request) -> None:
    """Raise 429 when the client's bucket `name` (saves, not_found, bug_reports) is empty; a no-op when its rate is 0 (off)."""
    if not _allow(name, client_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests, try again in a minute", headers={"Retry-After": "60"})


def recording_allowed(cost: float = 1.0) -> bool:
    """One recording budget per client per minute for history rows and impression rows together. False means: skip the recording
    (it is best effort) and still serve the response."""
    return _allow("recording", current_client.get(), cost)


# --- read-side caps of GET /api/images ----------------------------------------------------------------------------------------------

def check_search_inputs(q: str | None, raw_facets: str | None) -> None:
    """422 when `q` or the facet list exceed `abuse.max_q_chars`, `abuse.max_facets`, `abuse.max_facet_value_chars` (0 = off)."""
    max_q = _int_setting("abuse.max_q_chars", 0)
    if max_q and q is not None and len(q) > max_q:
        raise HTTPException(status_code=422, detail=f"q is too long (at most {max_q} characters)")
    max_facets = _int_setting("abuse.max_facets", 0)
    max_value = _int_setting("abuse.max_facet_value_chars", 0)
    if raw_facets and (max_facets or max_value):
        values = raw_facets.split(",")
        if max_facets and len(values) > max_facets:
            raise HTTPException(status_code=422, detail=f"too many facet values (at most {max_facets})")
        if max_value and any(len(v) > max_value for v in values):
            raise HTTPException(status_code=422, detail=f"a facet value is too long (at most {max_value} characters)")


def record_browse_enabled() -> bool:
    """history.record_browse: whether the browse feed (no q, no facets) writes a search_history row. True on dev, false on prod."""
    raw = settings.get("history.record_browse", True)
    if not isinstance(raw, bool):
        raise ValueError(f"history.record_browse must be a bool, got {raw!r}")
    return raw


def impression_rows_cap() -> int:
    """popularity.impressions.max_rows_per_call: most impression rows one call writes (100 = the largest page, i.e. no cap)."""
    return max(1, _int_setting("popularity.impressions.max_rows_per_call", 100))


# --- bug reports --------------------------------------------------------------------------------------------------------------------

def bug_report_body_limit_bytes() -> int:
    """bug_reports.max_body_mb as bytes; 0 = the BodyLimit middleware is off."""
    return int(_number("bug_reports.max_body_mb", 0) * 1024 * 1024)


def bug_report_dir_caps() -> tuple[int, int]:
    """(bug_reports.max_files, bug_reports.max_total_mb in bytes); 0 = no cap."""
    return _int_setting("bug_reports.max_files", 0), int(_number("bug_reports.max_total_mb", 0) * 1024 * 1024)
