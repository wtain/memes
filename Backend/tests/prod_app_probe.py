"""Runs the real application with APP_STAGE=prod in a child process and prints what it observed as JSON.

`APP_STAGE` is read once at import time (config/stage.py) and the route gate is built when the routers are
imported, so prod behaviour cannot be tested by flipping a flag inside the pytest process. test_prod_stage.py starts
this script with a prepared environment (dummy DATABASE_URL, a temporary BASE_PATH, APP_ENV, APP_STAGE=prod) and
asserts on its output. No database, no network: sessions are fakes.

Usage: python Backend/tests/prod_app_probe.py   (from the repo root; environment prepared by the caller)
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

import Backend.app.main as main  # noqa: E402
from Backend.app.stage_policy import DEV_ONLY, READER, ROUTE_POLICY  # noqa: E402
from Storage.db import get_async_db  # noqa: E402

app = main.app
out: dict = {"stage_env": {"APP_STAGE": os.environ.get("APP_STAGE"), "APP_ENV": os.environ.get("APP_ENV")}}


def mounted_routes():
    rows = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            rows += [[m, route.path] for m in sorted(route.methods)]
        else:
            prefix = route.include_context.prefix
            for inner in route.original_router.routes:
                rows += [[m, prefix + inner.path] for m in sorted(inner.methods)]
    return sorted(rows)


out["mounted_routes"] = mounted_routes()
out["dev_only_modules_imported"] = sorted(
    name for name in sys.modules
    if re.fullmatch(r"Backend\.app\.api\.(admin|admin_discovery|admin_profiles|ingestion|recut|comics|meme_profiles"
                    r"|trends|uploads|meme_text_ratings)", name)
)
out["docs_urls"] = [app.docs_url, app.redoc_url, app.openapi_url]
out["redirect_slashes"] = app.router.redirect_slashes


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeSession:
    def __init__(self, marker=None, fail=False):
        self.marker, self.fail = marker, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _statement):
        if self.fail:
            raise RuntimeError("password authentication failed for user 'secret_user' at 10.1.2.3")
        return _Result(self.marker)


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


client = TestClient(app, raise_server_exceptions=False)


def call(method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return {"status": response.status_code, "body": body, "allow": response.headers.get("allow"),
            "acao": response.headers.get("access-control-allow-origin"),
            "acac": response.headers.get("access-control-allow-credentials")}


# every dev_only (method, path) answers 404 {"detail": "Not Found"}; the reader routes are not probed here
out["dev_only_calls"] = {
    f"{method} {path}": call(method, fill(path))
    for (method, path), verdict in sorted(ROUTE_POLICY.items()) if verdict == DEV_ONLY
}
out["reader_keys"] = sorted(f"{m} {p}" for (m, p), v in ROUTE_POLICY.items() if v == READER)

extra = {}
for name, (method, path) in {
    "docs": ("GET", "/docs"), "redoc": ("GET", "/redoc"), "openapi": ("GET", "/openapi.json"),
    "slash_literal": ("GET", "/api/images/flagged/"), "slash_reader": ("GET", "/api/images/trending/"),
    "encoded_literal": ("GET", "/api/images/%66lagged"), "upper": ("GET", "/API/images/flagged"),
    "double_slash": ("GET", "//api/images/flagged"), "encoded_slash": ("GET", "/api%2Fimages%2Fflagged"),
    "head_reader": ("HEAD", "/api/images/trending"), "options_plain": ("OPTIONS", "/api/images/trending"),
    "wrong_method_dev_only": ("DELETE", "/api/images/flagged"),
    "wrong_method_reader": ("DELETE", "/api/images/trending"),
    "unmounted_prefix": ("GET", "/api/admin/batches/runs"), "unmounted_post": ("POST", "/api/uploads"),
    "unmounted_comics_by_image": ("GET", "/api/comics/by-image/x"),
}.items():
    extra[name] = call(method, path)
out["extra_calls"] = extra

out["cors"] = {
    "allowed_origin": call("OPTIONS", "/api/images/trending", headers={
        "Origin": "https://memes.example", "Access-Control-Request-Method": "GET"}),
    "localhost": call("OPTIONS", "/api/images/trending", headers={
        "Origin": "http://localhost", "Access-Control-Request-Method": "GET"}),
    "localhost_5174": call("OPTIONS", "/api/images/trending", headers={
        "Origin": "http://localhost:5174", "Access-Control-Request-Method": "GET"}),
    "put_preflight": call("OPTIONS", "/api/images/meme/x/popularity", headers={
        "Origin": "https://memes.example", "Access-Control-Request-Method": "PUT"}),
    "simple_get": call("GET", "/api/images/flagged", headers={"Origin": "https://memes.example"}),
}

# /health: a fixed detail, never the exception text
app.dependency_overrides[get_async_db] = lambda: FakeSession(marker=1)
out["health_ok"] = call("GET", "/health")
app.dependency_overrides[get_async_db] = lambda: FakeSession(fail=True)
out["health_fail"] = call("GET", "/health")
app.dependency_overrides.clear()

out["capabilities"] = call("GET", "/api/diagnostics/capabilities")

# abuse bounds through the full prod app (P5a-T6): the refusals happen before any database access
out["abuse"] = {
    "oversize_q": call("GET", "/api/images", params={"q": "a" * 513}),
    "many_facets": call("GET", "/api/images", params={"facets": ",".join(f"topic:v{i}" for i in range(11))}),
    "long_facet": call("GET", "/api/images", params={"facets": "topic:" + "x" * 70}),
    "declared_body": call("POST", "/api/bug-reports", content=b"x" * (11 * 1024 * 1024 + 1),
                          headers={"Content-Type": "multipart/form-data; boundary=zz"}),
}


# image file containment through the full prod app: PROBE_NAMES maps an image id to the file name the (fake)
# database returns for it; an id that is not listed is returned as the file name itself (the id comes from the URL)
class FakeImageRepository:
    names = json.loads(os.environ.get("PROBE_NAMES", "{}"))

    def __init__(self, _db):
        pass

    async def get_filename(self, image_id):
        return self.names.get(image_id, image_id)


def file_call(path):
    response = client.get(path)
    return {"status": response.status_code, "body": response.text[:200]}


with patch("Backend.app.api.images.ImageRepository", FakeImageRepository):
    app.dependency_overrides[get_async_db] = lambda: FakeSession(marker=1)
    out["files_by_id"] = {image_id: file_call("/api/images/" + image_id) for image_id in FakeImageRepository.names}
    out["files_by_url"] = {
        raw: file_call("/api/images/" + raw)
        for raw in json.loads(os.environ.get("PROBE_URLS", "[]"))
    }
    app.dependency_overrides.clear()


# lifespan: no scheduler, and the environment marker decides whether the app starts
async def lifespan_probe(marker):
    calls = {"scheduler": 0}

    async def fake_start():
        calls["scheduler"] += 1
        return []

    async def fake_stop(_tasks):
        return None

    with patch.object(main, "_configure_logging", lambda: None), \
            patch.object(main, "get_query_parser", lambda: None), \
            patch.object(main, "get_pipeline", lambda: None), \
            patch.object(main, "start_scheduler", fake_start), \
            patch.object(main, "stop_scheduler", fake_stop), \
            patch.object(main, "AsyncSessionLocal", lambda: FakeSession(marker=marker)):
        try:
            async with main.lifespan(app):
                return {"started": True, "scheduler_calls": calls["scheduler"]}
        except RuntimeError as error:
            return {"started": False, "error": str(error), "scheduler_calls": calls["scheduler"]}


app_env = os.environ["APP_ENV"]
out["lifespan_match"] = asyncio.run(lifespan_probe(app_env))
out["lifespan_wrong_marker"] = asyncio.run(lifespan_probe("metal" if app_env != "metal" else "it"))
out["lifespan_no_marker"] = asyncio.run(lifespan_probe(None))

from Backend.app.services.query_parser import get_query_parser  # noqa: E402
from config.settings import settings  # noqa: E402

out["parser"] = get_query_parser() is None
out["settings"] = {
    "stage": settings.get("stage"),
    "scheduler_enabled": settings.get("scheduler.enabled"),
    "parse_model": settings.get("parse.model"),
    "cors_origins": list(settings.get("cors.origins") or []),
    "cors_allow_credentials": settings.get("cors.allow_credentials"),
}

print("PROBE_JSON=" + json.dumps(out, default=str))
