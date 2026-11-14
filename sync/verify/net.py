"""P6 HTTPS checks: S3 (health and capabilities), S7 (stage policy: dev-only routes answer 404), A4 (abuse caps) (design P6 "Checks").

Everything goes through one injectable callable `http(method, url, headers, timeout) -> HttpResult`, so the checks are unit tested with
fakes and the real command uses `default_http` (standard library `http.client`, no redirects, no body sent, at most 64 KiB read). The
requests are the harmless ones of the design (GET, and a POST with no body to a route that must answer 404). A missing `sync.prod.api_url`,
an unreachable host or an endpoint prod does not serve yet makes the dependent check `skipped` with a reason. Nothing here has been
exercised against a real prod stack (no VPS exists yet): mock-only, first exercised in the P7 acceptance run.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from sync import connect
from sync.verify.model import Check, fail, ok, skipped, token

HEALTH_PATH = "/api/diagnostics/health"
CAPABILITIES_PATH = connect.CAPABILITIES_PATH
SEARCH_PATH = "/api/images"
BUG_REPORT_PATH = "/api/bug-reports"
STAGE_FEATURES = ("comics", "trends", "ratings", "statistics", "uploads", "history", "admin")
DRIVER_WORDS = ("asyncpg", "psycopg", "sqlalchemy", "postgres", "traceback", "connection refused")
# route id -> (method, path) of the S7 sample: dev-only routes that a prod stage answers 404 (design S7)
STAGE_ROUTES = {
    "admin_batches": ("GET", "/api/admin/batches"), "docs": ("GET", "/docs"), "openapi": ("GET", "/openapi.json"),
    "uploads": ("POST", "/api/uploads"), "comics": ("GET", "/api/comics"), "statistics": ("GET", "/api/diagnostics/statistics"),
    "ratings": ("GET", "/api/meme-from-text/ratings"),
}
BURST = 40
OVERSIZE_Q_CHARS = 4000
FIX_STAGE = "owner: check APP_STAGE=prod and the route tombstones of the backend, redeploy"
FIX_ABUSE = "owner: check the rate limit and size caps at Caddy and in the backend"


class NetUnavailable(Exception):
    """The host did not answer (code in `.code`)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class HttpResult:
    status: int
    headers: dict = field(default_factory=dict)  # lower-case names
    body: bytes = b""


Http = Callable[..., HttpResult]


def default_http(method: str, url: str, headers: dict | None = None, timeout: float = 10.0) -> HttpResult:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise NetUnavailable("api_url_invalid")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.putrequest(method, (parts.path or "/") + (("?" + parts.query) if parts.query else ""), skip_accept_encoding=True)
        for k, v in {"User-Agent": "memes-sync-verify", "Accept": "application/json", **(headers or {})}.items():
            conn.putheader(k, v)
        conn.endheaders()
        resp = conn.getresponse()
        return HttpResult(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read(65536))
    except (OSError, http.client.HTTPException):
        raise NetUnavailable("unreachable") from None
    finally:
        conn.close()


def _url(base: str, path: str, query: str = "") -> str:
    return base.rstrip("/") + path + (("?" + query) if query else "")


def _json(result: HttpResult | None):
    if result is None or result.status != 200:
        return None
    try:
        data = json.loads(result.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _try(http: Http, method: str, url: str, headers: dict | None = None) -> HttpResult | None:
    try:
        return http(method, url, headers)
    except NetUnavailable:
        return None


# ---------------------------------------------------------------------------------------------------------------------------------
# S3

def check_s3(health: HttpResult | None, caps: dict | None, env: str) -> Check:
    if health is None:
        return skipped("S3", "error", "api_unreachable")
    problems = []
    if health.status != 200:
        problems.append(f"health_http_{health.status}")
    else:
        text = health.body.decode("utf-8", "replace").lower()
        if any(w in text for w in DRIVER_WORDS):
            problems.append("health_leaks_driver")
    if caps is None:
        problems.append("capabilities_unavailable")
    else:
        if caps.get("stage") != "prod":
            problems.append("stage_not_prod")
        if caps.get("environment") != env:
            problems.append("environment_mismatch")
        features = caps.get("features")
        if isinstance(features, dict):
            on = sorted(f for f in STAGE_FEATURES if features.get(f) is not False)
            if on:
                problems.append("features_on=" + ".".join(on[:3]))
        else:
            problems.append("features_absent")
    return fail("S3", "error", ",".join(problems), FIX_STAGE) if problems else ok("S3", "error", "stage=prod,features=off")


def gather_s3(http: Http, base: str) -> tuple:
    return _try(http, "GET", _url(base, HEALTH_PATH)), _json(_try(http, "GET", _url(base, CAPABILITIES_PATH)))


# ---------------------------------------------------------------------------------------------------------------------------------
# S7

def gather_s7(http: Http, base: str, skip_routes: tuple = ()) -> dict:
    """Route id -> status (None: no answer)."""
    out = {}
    for rid, (method, path) in STAGE_ROUTES.items():
        if rid in skip_routes:
            continue
        r = _try(http, method, _url(base, path), {"Content-Length": "0"} if method == "POST" else None)
        out[rid] = None if r is None else r.status
    return out


def check_s7(statuses: dict) -> Check:
    if not statuses or all(s is None for s in statuses.values()):
        return skipped("S7", "error", "api_unreachable")
    wrong = sorted((rid, s) for rid, s in statuses.items() if s != 404)
    if wrong:
        shown = ".".join(f"{token(r)}:{s if s is not None else 'none'}" for r, s in wrong[:3])
        return fail("S7", "error", f"not_404={len(wrong)},{shown}", FIX_STAGE)
    return ok("S7", "error", f"routes={len(statuses)},all_404")


# ---------------------------------------------------------------------------------------------------------------------------------
# A4

def gather_a4(http: Http, base: str, *, deep: bool, bug_cap_bytes: int, q_chars: int = OVERSIZE_Q_CHARS, burst: int = BURST) -> dict:
    q = _try(http, "GET", _url(base, SEARCH_PATH, "q=" + "a" * q_chars + "&limit=1"))
    out: dict = {"q_status": None if q is None else q.status}
    if deep:
        declared = _try(http, "POST", _url(base, BUG_REPORT_PATH), {"Content-Length": str(bug_cap_bytes + 1), "Content-Type": "text/plain"})
        out["declared_status"] = None if declared is None else declared.status
        limited, hits = False, 0
        for _ in range(burst):
            r = _try(http, "GET", _url(base, HEALTH_PATH))
            if r is None:
                break
            hits += 1
            if r.status == 429 or any(h.startswith(("x-ratelimit", "ratelimit", "retry-after")) for h in r.headers):
                limited = True
        out["burst"] = {"sent": hits, "limited": limited}
    return out


def check_a4(facts: dict) -> Check:
    if facts.get("q_status") is None and "declared_status" not in facts:
        return skipped("A4", "error", "api_unreachable")
    problems = []
    if facts.get("q_status") != 422:
        problems.append(f"oversize_q_{facts.get('q_status') if facts.get('q_status') is not None else 'none'}")
    if "declared_status" in facts:
        ds = facts["declared_status"]
        if ds is None or not (400 <= ds < 500):
            problems.append("declared_length_accepted")
        b = facts.get("burst") or {}
        if not b.get("limited"):
            problems.append("no_rate_limit_seen")
    detail = "q=422" + (",deep=checked" if "declared_status" in facts else ",deep=off")
    return fail("A4", "error", ",".join(problems), FIX_ABUSE) if problems else ok("A4", "error", detail)
