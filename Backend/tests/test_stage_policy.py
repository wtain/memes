"""Route policy of the backend (P5a-T1): completeness guard, and proof that dev behaviour is unchanged.

Everything here runs with APP_STAGE unset (dev). Prod behaviour is tested in test_prod_stage.py.
"""
import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import Backend.app.main as main
import config.stage
from Backend.app import stage_policy
from Backend.app.stage_policy import (
    API_PREFIX, DEV_ONLY, DEV_ONLY_ROUTER_MODULES, READER, ROUTE_POLICY, StageRoute, make_router,
)

ROUTER_MODULES = [
    "admin", "admin_discovery", "admin_profiles", "bug_reports", "comics", "concepts", "diagnostics", "history",
    "images", "ingestion", "meme_from_text", "meme_profiles", "meme_text_ratings", "recommendations", "recut",
    "trends", "uploads",
]
SNAPSHOT = Path(__file__).parent / "data" / "dev_route_snapshot.json"
ADDED_BY_P5A = {("GET", "/api/diagnostics/capabilities")}


def _routers():
    found = {name: importlib.import_module(f"Backend.app.api.{name}").router for name in ROUTER_MODULES}
    found["main"] = main.root_router
    return found


def _declared_routes():
    declared = {}
    for name, router in _routers().items():
        for route in router.routes:
            for method in route.methods:
                declared.setdefault((method, route.mount_prefix + route.path), []).append(name)
    return declared


def _served_routes(app):
    rows = set()
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            rows |= {(m, route.path) for m in route.methods}
        else:
            prefix = route.include_context.prefix
            for inner in route.original_router.routes:
                rows |= {(m, prefix + inner.path) for m in inner.methods}
    return rows


def test_every_route_has_a_verdict_and_every_verdict_a_route():
    declared = _declared_routes()
    assert set(declared) == set(ROUTE_POLICY), {
        "no verdict": sorted(set(declared) - set(ROUTE_POLICY)),
        "no route": sorted(set(ROUTE_POLICY) - set(declared)),
    }
    assert all(len(owners) == 1 for owners in declared.values()), "a (method, path) is declared twice"


def test_verdict_counts_are_22_reader_and_69_dev_only_of_91():
    counts = {verdict: sum(1 for v in ROUTE_POLICY.values() if v == verdict) for verdict in (READER, DEV_ONLY)}
    assert counts == {READER: 22, DEV_ONLY: 69}
    assert len(ROUTE_POLICY) == 91


def test_every_router_is_made_by_make_router_and_every_route_is_a_stage_route():
    for name, router in _routers().items():
        assert router.routes, name
        for route in router.routes:
            assert isinstance(route, StageRoute), (name, route.path)


def test_routers_are_mounted_under_the_prefix_their_routes_assume():
    for route in main.app.routes:
        if hasattr(route, "include_context"):
            inner = route.original_router.routes
            assert {r.mount_prefix for r in inner} == {route.include_context.prefix}, route.include_context.prefix
    assert API_PREFIX == "/api"


def test_fully_dev_only_routers_carry_only_dev_only_verdicts_and_mixed_ones_do_not():
    declared = _declared_routes()
    by_module = {}
    for key, owners in declared.items():
        by_module.setdefault(owners[0], []).append(ROUTE_POLICY[key])
    for module in DEV_ONLY_ROUTER_MODULES:
        assert set(by_module[module]) == {DEV_ONLY}, module
    for module in ("images", "history", "diagnostics", "meme_from_text"):
        assert module not in DEV_ONLY_ROUTER_MODULES
        assert READER in by_module[module], module
    assert sum(len(by_module[m]) for m in DEV_ONLY_ROUTER_MODULES) == 51   # design: 51 routes never mounted on prod


def test_dev_route_set_is_exactly_what_it_was_before_the_stage_mechanism():
    """Snapshot taken before P5a: the (method, path) set of the app on dev, docs routes included."""
    before = {tuple(row) for row in json.loads(SNAPSHOT.read_text(encoding="utf-8"))}
    assert len(before) == 98
    now = _served_routes(main.app)
    assert now - before == ADDED_BY_P5A
    assert before - now == set()


def test_dev_stage_serves_dev_only_routes_and_docs():
    assert not config.stage.is_prod()
    served = _served_routes(main.app)
    assert ("GET", "/api/images/flagged") in served and ("GET", "/docs") in served
    assert main.app.docs_url == "/docs" and main.app.openapi_url == "/openapi.json"
    assert main.app.router.redirect_slashes is True


def _mini_app(monkeypatch, stage: str):
    monkeypatch.setattr(config.stage, "STAGE", stage)
    router = make_router(prefix="/images", tags=["images"])

    @router.get("/flagged")
    async def flagged():
        return {"literal": True}

    @router.get("/{image_id}")
    async def image(image_id: str):
        return {"image_id": image_id}

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def test_dev_handler_is_the_stock_handler(monkeypatch):
    client = TestClient(_mini_app(monkeypatch, "dev"))
    assert client.get("/api/images/flagged").json() == {"literal": True}
    assert client.get("/api/images/abc").json() == {"image_id": "abc"}


def test_tombstone_wins_over_the_parameter_route_on_prod(monkeypatch):
    client = TestClient(_mini_app(monkeypatch, "prod"))
    for path in ("/api/images/flagged", "/api/images/%66lagged"):
        response = client.get(path)
        assert response.status_code == 404 and response.json() == {"detail": "Not Found"}, path
    assert client.get("/api/images/abc").json() == {"image_id": "abc"}


def test_a_route_without_a_verdict_makes_the_prod_import_fail(monkeypatch):
    monkeypatch.setattr(config.stage, "STAGE", "prod")
    router = make_router(prefix="/not-in-the-policy")
    with pytest.raises(RuntimeError, match="no verdict"):
        @router.get("/x")
        async def x():
            return {}


def test_the_same_route_on_dev_needs_no_verdict(monkeypatch):
    monkeypatch.setattr(config.stage, "STAGE", "dev")
    router = make_router(prefix="/not-in-the-policy")

    @router.get("/x")
    async def x():
        return {}

    assert router.routes


def test_make_router_refuses_a_route_class():
    with pytest.raises(TypeError):
        make_router(route_class=object)


def test_verdict_lookup_is_exact_and_case_sensitive():
    assert stage_policy.verdict_for("get", "/api/images/trending") == READER
    assert stage_policy.verdict_for("GET", "/API/images/trending") is None
    assert stage_policy.verdict_for("GET", "/api/images/trending/") is None


def test_capabilities_on_dev_lists_every_group_as_served():
    client = TestClient(main.app)
    response = client.get("/api/diagnostics/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "dev" and body["environment"] == "general"
    assert set(body) == {"stage", "environment", "features"}
    features = body["features"]
    assert set(features) == {"memeFromText", "concepts", "trends", "comics", "ratings", "statistics", "uploads",
                             "history", "admin"}
    assert all(isinstance(value, bool) for value in features.values())
    assert all(features[name] for name in ("concepts", "trends", "comics", "ratings", "statistics", "uploads",
                                           "history", "admin"))
