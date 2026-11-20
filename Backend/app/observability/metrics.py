"""Metric objects of the backend, created lazily on first use (the first `enabled()` caller, i.e. the middleware).

Labels are bounded by construction: `route` is a route template or "unmatched", `method` one of KNOWN_METHODS or "OTHER",
`status_class` one of 1xx..5xx or "other". No query string, path parameter, header, body or client address is ever a label.
In multiprocess mode (PROMETHEUS_MULTIPROC_DIR set before the first import of prometheus_client) the values live in files under that
directory and the exporter (exporter.py) aggregates them.
"""
import os

KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
UNMATCHED = "unmatched"
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_NAMES = ("HTTP_REQUESTS", "HTTP_DURATION", "HTTP_IN_FLIGHT", "BUILD_INFO")


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
    from prometheus_client import Counter, Gauge, Histogram

    g = globals()
    g["HTTP_REQUESTS"] = Counter("http_requests_total", "HTTP requests by route template, method and status class",
                                 ["route", "method", "status_class"])
    g["HTTP_DURATION"] = Histogram("http_request_duration_seconds", "HTTP request duration by route template and method",
                                   ["route", "method"], buckets=DURATION_BUCKETS)
    g["HTTP_IN_FLIGHT"] = Gauge("http_requests_in_flight", "HTTP requests being served", multiprocess_mode="livesum")
    build_info = Gauge("build_info", "Build of the running backend (value is always 1)", ["git_sha", "stage"],
                       multiprocess_mode="max")
    build_info.labels(git_sha=os.environ.get("GIT_SHA", "unknown")[:40], stage=os.environ.get("APP_STAGE", "dev")).set(1)
    g["BUILD_INFO"] = build_info


def __getattr__(name):
    # Module-level lazy creation: the objects exist only once someone reads one of the four names.
    if name in _NAMES:
        _create()
        return globals()[name]
    raise AttributeError(name)
