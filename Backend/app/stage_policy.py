"""Route policy of the backend, one verdict per (method, full path), and the router machinery that enforces it.

Design: docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p5-prod-provisioning-and-hardening-design.md (P5a).

The production backend is this same code base started with APP_STAGE=prod. On prod a route whose verdict is
not READER answers 404 {"detail": "Not Found"}, exactly what the router answers for a path that does not exist.
The gate is the route itself (`StageRoute.get_route_handler`), not a middleware: the router decides which route
matched, so a literal path declared before a parameter path (`/images/flagged` vs `/images/{image_id}`) cannot
fall through to the parameter route, and a CORS preflight is answered by CORSMiddleware before routing.

With APP_STAGE unset or `dev` nothing here changes behaviour: `get_route_handler` returns the stock handler.

How to add a route: create it on a router made with `make_router(...)` and add one line to ROUTE_POLICY. On prod
a route without a verdict makes the import raise (default deny); on dev `Backend/tests/test_stage_policy.py`
fails instead.
"""
from functools import lru_cache
from typing import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from config.stage import is_prod

READER = "reader"        # served on prod
DEV_ONLY = "dev_only"    # 404 on prod (and, for the fully dev-only routers, not even imported there)

API_PREFIX = "/api"      # the prefix main.py includes every router under (a guard test checks it)

# (method, full path including "/api" and the router prefix) -> verdict. One line per route, grouped by file.
ROUTE_POLICY: dict[tuple[str, str], str] = {
    # admin.py
    ("GET", "/api/admin/batches/metrics"): DEV_ONLY,
    ("GET", "/api/admin/batches/names"): DEV_ONLY,
    ("GET", "/api/admin/batches/runs"): DEV_ONLY,
    ("GET", "/api/admin/batches/runs/{run_id}"): DEV_ONLY,
    ("POST", "/api/admin/batches/{batch_name}/run"): DEV_ONLY,
    # admin_discovery.py
    ("GET", "/api/admin/discovery/memes"): DEV_ONLY,
    ("GET", "/api/admin/discovery/misses"): DEV_ONLY,
    ("GET", "/api/admin/discovery/queries"): DEV_ONLY,
    # admin_profiles.py
    ("GET", "/api/admin/profile-coverage"): DEV_ONLY,
    ("GET", "/api/admin/profile-coverage/images"): DEV_ONLY,
    # telemetry.py
    ("POST", "/api/telemetry/pageview"): READER,
    # bug_reports.py
    ("POST", "/api/bug-reports"): READER,
    # comics.py (decision 22: comics are not shipped)
    ("GET", "/api/comics"): DEV_ONLY,
    ("POST", "/api/comics"): DEV_ONLY,
    ("GET", "/api/comics/addability"): DEV_ONLY,
    ("GET", "/api/comics/by-image/{image_id}"): DEV_ONLY,
    ("GET", "/api/comics/candidates"): DEV_ONLY,
    ("POST", "/api/comics/candidates/{candidate_id}/accept"): DEV_ONLY,
    ("POST", "/api/comics/candidates/{candidate_id}/dismiss"): DEV_ONLY,
    ("GET", "/api/comics/{comics_id}"): DEV_ONLY,
    ("PATCH", "/api/comics/{comics_id}"): DEV_ONLY,
    ("DELETE", "/api/comics/{comics_id}"): DEV_ONLY,
    ("POST", "/api/comics/{comics_id}/build"): DEV_ONLY,
    ("POST", "/api/comics/{comics_id}/panels"): DEV_ONLY,
    ("PUT", "/api/comics/{comics_id}/panels/order"): DEV_ONLY,
    ("DELETE", "/api/comics/{comics_id}/panels/{panel_id}"): DEV_ONLY,
    ("GET", "/api/comics/{comics_id}/preview"): DEV_ONLY,
    # concepts.py
    ("GET", "/api/concepts"): READER,
    ("GET", "/api/concepts/for-image"): READER,
    ("GET", "/api/concepts/top-images"): READER,
    ("GET", "/api/concepts/{concept_id}"): READER,
    # diagnostics.py
    ("GET", "/api/diagnostics/capabilities"): READER,
    ("GET", "/api/diagnostics/health"): READER,
    ("GET", "/api/diagnostics/statistics"): DEV_ONLY,       # decision 25
    # history.py
    ("GET", "/api/history"): DEV_ONLY,                      # decision 10
    ("POST", "/api/history/{search_id}/not-found"): READER,
    # images.py
    ("GET", "/api/images"): READER,
    ("GET", "/api/images/duplicates"): DEV_ONLY,
    ("POST", "/api/images/duplicates/clusters/{cluster_id}/dismiss"): DEV_ONLY,
    ("GET", "/api/images/duplicates/clusters/{cluster_id}/similarity"): DEV_ONLY,
    ("GET", "/api/images/duplicates/decisions"): DEV_ONLY,
    ("POST", "/api/images/duplicates/pairs/undo-dismiss"): DEV_ONLY,
    ("GET", "/api/images/flagged"): DEV_ONLY,
    ("GET", "/api/images/meme/{image_id}"): READER,
    ("GET", "/api/images/meme/{image_id}/get_flagged"): DEV_ONLY,
    ("GET", "/api/images/meme/{image_id}/impressions"): READER,
    ("PUT", "/api/images/meme/{image_id}/mark_flagged"): DEV_ONLY,
    ("GET", "/api/images/meme/{image_id}/popularity"): READER,
    ("PUT", "/api/images/meme/{image_id}/popularity"): DEV_ONLY,
    ("GET", "/api/images/meme/{image_id}/saves"): READER,
    ("POST", "/api/images/meme/{image_id}/saves"): READER,
    ("PUT", "/api/images/meme/{image_id}/unmark_flagged"): DEV_ONLY,
    ("GET", "/api/images/no-ocr"): DEV_ONLY,
    ("GET", "/api/images/trending"): READER,
    ("GET", "/api/images/untagged"): DEV_ONLY,
    ("GET", "/api/images/{image_id}"): READER,
    ("PUT", "/api/images/{image_id}/description-note"): DEV_ONLY,
    ("DELETE", "/api/images/{image_id}/description-note"): DEV_ONLY,
    ("GET", "/api/images/{image_id}/descriptions"): READER,
    ("PUT", "/api/images/{image_id}/descriptions/{prompt_key}/approve"): DEV_ONLY,
    ("PUT", "/api/images/{image_id}/descriptions/{prompt_key}/reject"): DEV_ONLY,
    ("GET", "/api/images/{image_id}/similar"): READER,
    # ingestion.py
    ("GET", "/api/ingestion/clusters/{tier}"): DEV_ONLY,
    ("POST", "/api/ingestion/clusters/{tier}/resolve"): DEV_ONLY,
    ("POST", "/api/ingestion/images/{image_id}/undo-reject"): DEV_ONLY,
    ("GET", "/api/ingestion/pending"): DEV_ONLY,
    ("GET", "/api/ingestion/review/tier_b"): DEV_ONLY,
    ("GET", "/api/ingestion/run"): DEV_ONLY,
    # meme_from_text.py
    ("POST", "/api/meme-from-text/search"): READER,
    ("GET", "/api/meme-from-text/taxonomy"): READER,
    # meme_profiles.py
    ("GET", "/api/meme-from-text/profiles/{image_id}"): DEV_ONLY,
    ("PUT", "/api/meme-from-text/profiles/{image_id}"): DEV_ONLY,
    ("DELETE", "/api/meme-from-text/profiles/{image_id}"): DEV_ONLY,
    ("POST", "/api/meme-from-text/profiles/{image_id}/confirm"): DEV_ONLY,
    # meme_text_ratings.py (decision 24)
    ("GET", "/api/meme-from-text/ratings"): DEV_ONLY,
    ("PUT", "/api/meme-from-text/ratings"): DEV_ONLY,
    ("DELETE", "/api/meme-from-text/ratings"): DEV_ONLY,
    ("DELETE", "/api/meme-from-text/ratings/text"): DEV_ONLY,
    # recommendations.py
    ("GET", "/api/recommendations"): READER,
    # recut.py
    ("GET", "/api/recut/proposals"): DEV_ONLY,
    ("GET", "/api/recut/proposals/{proposal_id}"): DEV_ONLY,
    ("POST", "/api/recut/proposals/{proposal_id}/adjust"): DEV_ONLY,
    ("POST", "/api/recut/proposals/{proposal_id}/approve"): DEV_ONLY,
    ("POST", "/api/recut/proposals/{proposal_id}/reject"): DEV_ONLY,
    ("GET", "/api/recut/stats"): DEV_ONLY,
    # trends.py
    ("GET", "/api/trends/dates"): DEV_ONLY,
    ("GET", "/api/trends/dates/{run_date}/runs"): DEV_ONLY,
    ("GET", "/api/trends/dates/{run_date}/runs/latest"): DEV_ONLY,
    ("GET", "/api/trends/history"): DEV_ONLY,
    ("GET", "/api/trends/runs/{run_id}"): DEV_ONLY,
    # uploads.py
    ("POST", "/api/uploads"): DEV_ONLY,
    # main.py (root router, mounted without a prefix)
    ("GET", "/health"): READER,
}

# Routers that exist only on dev: main.py neither imports nor mounts them on prod (their modules pull batch code
# the prod image does not carry), so their routes are 404 there because no route matches at all. Their routes also
# carry DEV_ONLY verdicts above, as a second layer in case a module is ever mounted by mistake.
DEV_ONLY_ROUTER_MODULES = frozenset({
    "admin", "admin_discovery", "admin_profiles", "ingestion", "recut", "comics",
    "meme_profiles", "trends", "uploads", "meme_text_ratings",
})


def verdict_for(method: str, full_path: str) -> str | None:
    return ROUTE_POLICY.get((method.upper(), full_path))


async def _not_found(_request: Request) -> Response:
    # Same status and body the router produces for a path it does not have.
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# Full paths on which at least one method is served on prod. A wrong method on such a path keeps the router's own answer
# (405 + Allow); on any other path of ROUTE_POLICY (dev-only everywhere) every method answers like a missing path.
READER_PATHS = frozenset(path for (_method, path), verdict in ROUTE_POLICY.items() if verdict == READER)


class StageRoute(APIRoute):
    """APIRoute that, on prod, answers 404 unless the policy says READER; a route without a verdict is an import error.

    `mount_prefix` is the prefix main.py includes the router under; the route itself only knows its path inside
    the router.
    """

    mount_prefix = API_PREFIX

    async def handle(self, scope, receive, send) -> None:
        # The router calls handle() of the first route that matches the path but not the method (a "partial" match) and
        # Route.handle answers 405 + Allow, which would reveal that a dev-only path exists. On prod such a path answers
        # 404 for every method, exactly like a missing one.
        if is_prod() and (self.mount_prefix + self.path) not in READER_PATHS                 and scope["method"] not in (self.methods or ()):
            response = await _not_found(None)
            await response(scope, receive, send)
            return
        await super().handle(scope, receive, send)

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        if not is_prod():
            return super().get_route_handler()
        verdicts = set()
        for method in sorted(self.methods or ()):
            verdict = verdict_for(method, self.mount_prefix + self.path)
            if verdict is None:
                raise RuntimeError(
                    f"route {method} {self.mount_prefix + self.path} has no verdict in Backend/app/stage_policy.py "
                    "(default deny: add a line to ROUTE_POLICY)"
                )
            verdicts.add(verdict)
        if len(verdicts) > 1:
            raise RuntimeError(f"route {self.mount_prefix + self.path} has different verdicts for its methods")
        if verdicts == {READER}:
            return super().get_route_handler()
        return _not_found


@lru_cache(maxsize=None)
def _route_class_for(mount_prefix: str) -> type[StageRoute]:
    if mount_prefix == API_PREFIX:
        return StageRoute
    return type("StageRoute", (StageRoute,), {"mount_prefix": mount_prefix})


def make_router(*, mount_prefix: str = API_PREFIX, **kwargs) -> APIRouter:
    """APIRouter whose routes are StageRoutes. Every router of the backend is created through this factory."""
    if "route_class" in kwargs:
        raise TypeError("make_router sets route_class itself")
    return APIRouter(route_class=_route_class_for(mount_prefix), **kwargs)
