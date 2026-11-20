"""Metric objects of the backend, created lazily on first use (the first `enabled()` caller, i.e. the middleware).

Labels are bounded by construction: `route` is a route template or "unmatched", `method` one of KNOWN_METHODS or "OTHER",
`status_class` one of 1xx..5xx or "other", search `kind`/`stage`, degraded `reason` and limiter `scope` are fixed sets. No query
string, path parameter, header, body or client address is ever a label.
In multiprocess mode (PROMETHEUS_MULTIPROC_DIR set before the first import of prometheus_client) the values live in files under that
directory and the exporter (exporter.py) aggregates them.
"""
import os
import threading
import time

KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
UNMATCHED = "unmatched"
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_NAMES = ("HTTP_REQUESTS", "HTTP_DURATION", "HTTP_IN_FLIGHT", "BUILD_INFO", "IMAGE_SERVED", "IMAGE_BYTES", "IMAGE_READ_SECONDS",
          "SEARCH_SECONDS", "SEARCH_DEGRADED", "POOL_CHECKED_OUT", "POOL_OVERFLOW", "POOL_WAIT_SECONDS", "RATE_LIMITED", "PAGEVIEWS")
SEARCH_KINDS = frozenset({"smart", "clip", "meme_from_text"})
SEARCH_STAGES = frozenset({"total", "parse", "embed", "retrieve"})
DEGRADED_REASONS = frozenset({"unavailable", "timeout", "invalid_output", "breaker_open", "embed_unavailable", "empty_input"})
RATE_LIMITED_SCOPES = frozenset({"saves", "not_found", "bug_reports", "recording", "telemetry", "meme_search", "body_limit"})
PAGEVIEW_CLIENTS = frozenset({"web", "android"})
# Web route templates of Frontend/memes-frontend/src/app/router.tsx (every non-admin one) and the Android destination names of
# AndroidClient/.../ui/NavGraph.kt (route without its arguments). Anything else is counted as "other".
PAGE_ALLOWLIST = frozenset({
    "/", "/explore", "/untagged", "/duplicates", "/no-ocr", "/flagged", "/trending-memes", "/trends", "/trends/date/:date",
    "/trends/history/:label/:name", "/reply", "/search", "/recommendations", "/concepts", "/upload", "/statistics", "/ingestion",
    "/memes/:id", "/concepts/:id",
    "search", "detail", "flagged", "environments", "sync", "upload", "settings", "about", "trends", "statistics", "recommendations",
})
SEARCH_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
IMAGE_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5)
POOL_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 30)
_create_lock = threading.Lock()
_create_failed = False


def route_label(scope) -> str:
    """The route template of a matched route (`/api/images/{image_id}`), otherwise "unmatched".

    A StageRoute (Backend/app/stage_policy.py) carries the router-local path, because include_router keeps the route object and
    only remembers the mount prefix, so the prefix it names (`mount_prefix`, "/api") goes in front, as the route policy does."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if not isinstance(path, str) or not path:
        return UNMATCHED
    prefix = getattr(route, "mount_prefix", "")
    return prefix + path if isinstance(prefix, str) else path


def status_class(code: int) -> str:
    if isinstance(code, int) and 100 <= code <= 599:
        return f"{code // 100}xx"
    return "other"


def method_label(method) -> str:
    return method if method in KNOWN_METHODS else "OTHER"


def _create() -> None:
    """Creates every metric object once. Idempotent and thread-safe; a failure is remembered, so later accesses raise at once instead
    of retrying (and registering duplicates)."""
    global _create_failed
    with _create_lock:
        if "HTTP_REQUESTS" in globals():
            return
        if _create_failed:
            raise RuntimeError("metrics could not be created")
        try:
            _create_all()
        except Exception:
            _create_failed = True
            raise


def _create_all() -> None:
    from prometheus_client import Counter, Gauge, Histogram

    g = {}
    g["HTTP_REQUESTS"] = Counter("http_requests_total", "HTTP requests by route template, method and status class",
                                 ["route", "method", "status_class"])
    g["HTTP_DURATION"] = Histogram("http_request_duration_seconds", "HTTP request duration by route template and method",
                                   ["route", "method"], buckets=DURATION_BUCKETS)
    g["HTTP_IN_FLIGHT"] = Gauge("http_requests_in_flight", "HTTP requests being served", multiprocess_mode="livesum")
    build_info = Gauge("build_info", "Build of the running backend (value is always 1)", ["git_sha", "stage"],
                       multiprocess_mode="max")
    build_info.labels(git_sha=os.environ.get("GIT_SHA", "unknown")[:40], stage=os.environ.get("APP_STAGE", "dev")).set(1)
    g["BUILD_INFO"] = build_info
    g["IMAGE_SERVED"] = Counter("image_served_total", "Image file responses by status class", ["status_class"])
    g["IMAGE_BYTES"] = Counter("image_served_bytes_total", "Body bytes of image file responses")
    g["IMAGE_READ_SECONDS"] = Histogram("image_read_seconds", "Time to send one image file response", buckets=IMAGE_BUCKETS)
    g["SEARCH_SECONDS"] = Histogram("search_seconds", "Search duration by kind and stage", ["kind", "stage"],
                                    buckets=SEARCH_BUCKETS)
    g["SEARCH_DEGRADED"] = Counter("search_degraded_total", "Meme-from-text searches answered degraded, by reason", ["reason"])
    g["POOL_CHECKED_OUT"] = Gauge("db_pool_checked_out", "Database connections checked out of the pool",
                                  multiprocess_mode="livesum")
    g["POOL_OVERFLOW"] = Gauge("db_pool_overflow", "Database pool connections beyond pool_size", multiprocess_mode="livesum")
    g["POOL_WAIT_SECONDS"] = Histogram("db_pool_wait_seconds", "Time to get a connection from the pool", buckets=POOL_BUCKETS)
    g["RATE_LIMITED"] = Counter("rate_limited_total", "Requests refused by an abuse limit, by scope", ["scope"])
    g["PAGEVIEWS"] = Counter("pageviews_total", "Page visits reported by the clients, by client and page template",
                             ["client", "page"])
    globals().update(g)   # all or nothing: a half-built set is never visible


def __getattr__(name):
    # Module-level lazy creation: the objects exist only once someone reads one of the names.
    if name in _NAMES:
        _create()
        return globals()[name]
    raise AttributeError(name)


# --- recording helpers: never raise, no-ops when metrics are off --------------------------------------------------------------------

def _on() -> bool:
    from Backend.app.observability import enabled
    return enabled()


def record_search(kind: str, stage: str, seconds: float) -> None:
    try:
        if kind in SEARCH_KINDS and stage in SEARCH_STAGES and _on():
            SEARCH_SECONDS.labels(kind, stage).observe(max(0.0, seconds))
    except Exception:
        pass


def record_search_degraded(fallback) -> None:
    """`fallback` is the pipeline's fallback string ("timeout", "timeout+embed_unavailable", ...): each known part counts once,
    any other part is "other"."""
    try:
        if not _on() or not isinstance(fallback, str):
            return
        for part in fallback.split("+"):
            if part in ("", "none", "retry"):
                continue
            SEARCH_DEGRADED.labels(part if part in DEGRADED_REASONS else "other").inc()
    except Exception:
        pass


def record_rate_limited(scope: str) -> None:
    try:
        if scope in RATE_LIMITED_SCOPES and _on():
            RATE_LIMITED.labels(scope).inc()
    except Exception:
        pass


def record_pageview(client, page) -> None:
    """Counts one page visit. `client` and `page` come from a request body: anything outside the fixed sets is "other"."""
    try:
        if not _on():
            return
        c = client if isinstance(client, str) and client in PAGEVIEW_CLIENTS else "other"
        p = page if isinstance(page, str) and page in PAGE_ALLOWLIST else "other"
        PAGEVIEWS.labels(c, p).inc()
    except Exception:
        pass


def record_image(code: int, nbytes: int = 0, seconds: float | None = None) -> None:
    try:
        if not _on():
            return
        IMAGE_SERVED.labels(status_class(code)).inc()
        if nbytes > 0:
            IMAGE_BYTES.inc(nbytes)
        if seconds is not None:
            IMAGE_READ_SECONDS.observe(max(0.0, seconds))
    except Exception:
        pass


_TIMED_FILE_RESPONSE = None  # the FileResponse subclass, created once on first use (starlette is not imported while metrics are off)


def _timed_file_response_class():
    global _TIMED_FILE_RESPONSE
    if _TIMED_FILE_RESPONSE is None:
        from starlette.responses import FileResponse

        class _Timed(FileResponse):
            async def __call__(self, scope, receive, send):
                started = time.perf_counter()
                state = {"code": 500, "bytes": 0}

                async def counting_send(message):
                    if message["type"] == "http.response.start":
                        state["code"] = message["status"]
                    elif message["type"] == "http.response.body":
                        state["bytes"] += len(message.get("body", b""))
                    await send(message)

                try:
                    await super().__call__(scope, receive, counting_send)
                finally:
                    record_image(state["code"], state["bytes"], time.perf_counter() - started)

        _TIMED_FILE_RESPONSE = _Timed
    return _TIMED_FILE_RESPONSE


def timed_file_response(path, headers):
    """A FileResponse that counts its status, body bytes and send time (the actual read happens while it is sent).

    Under a server that offers the ASGI `http.response.pathsend` extension (not uvicorn or gunicorn's uvicorn worker today), the
    file is sent as one pathsend message with no body bytes, so IMAGE_BYTES reads 0 for those responses while the status and time
    are still counted."""
    return _timed_file_response_class()(path, headers=headers)
