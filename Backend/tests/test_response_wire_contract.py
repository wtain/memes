"""
Characterization tests: the exact JSON of the ingestion and admin-batch responses (keys, null handling,
defaults), pinned before their hand-written response models were replaced by the schema-generated ones
(audit finding DUP-1). Plus a guard against the class of bug in CLAUDE.md's gotcha: a router's
response_model silently dropping a field that exists in the schema-generated twin.
"""
import typing
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from Backend.app.api import admin as admin_api
from Backend.app.api import ingestion as ingestion_api
from Backend.app.types.generated import (
    batchmetricsresponse,
    batchnamesresponse,
    ingestionclusterpage,
    ingestionresolveresponse,
    ingestionrunstatus,
    ingestiontierbreviewpage,
    ingestionundorejectresponse,
    runlistresponse,
    runstatusresponse,
    runtriggerresponse,
)

TS = datetime(2026, 11, 1, 12, 30, 0, tzinfo=timezone.utc)
TS_JSON = "2026-11-01T12:30:00Z"
IMG_A = str(uuid.uuid4())
IMG_B = str(uuid.uuid4())

app = FastAPI()
app.include_router(ingestion_api.router, prefix="/api")
app.include_router(admin_api.router, prefix="/api")


@pytest.fixture
def ingestion_service():
    svc = AsyncMock()
    app.dependency_overrides[ingestion_api.get_ingestion_service] = lambda: svc
    yield svc
    app.dependency_overrides.clear()


@pytest.fixture
def admin_service():
    svc = AsyncMock()
    app.dependency_overrides[admin_api.get_admin_batch_service] = lambda: svc
    app.dependency_overrides[admin_api.get_business_metrics_service] = lambda: svc
    yield svc
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_ingestion_run_json(client, ingestion_service):
    ingestion_service.get_run_status.return_value = {
        "run_id": "r1", "status": "started", "stage": None, "stats": {"a": 1},
        "tier_remaining": None, "blocked_total": 3, "created_at": TS, "completed_at": None,
    }
    assert client.get("/api/ingestion/run").json() == {
        "run_id": "r1", "status": "started", "stage": None, "stats": {"a": 1},
        "tier_remaining": None, "blocked_total": 3, "created_at": TS_JSON, "completed_at": None,
    }


def test_ingestion_pending_json(client, ingestion_service):
    ingestion_service.list_pending.return_value = [{"image_id": IMG_A, "filename": "a.jpg", "created_at": TS}]
    assert client.get("/api/ingestion/pending").json() == [
        {"image_id": IMG_A, "filename": "a.jpg", "created_at": TS_JSON}
    ]


def test_ingestion_clusters_json_text_heavy_defaults_false(client, ingestion_service):
    ingestion_service.list_clusters.return_value = {
        "items": [{
            "members": [
                {"image_id": IMG_A, "filename": "a.jpg", "status": "pending", "ocr_text": None},
                {"image_id": IMG_B, "filename": "b.jpg", "status": "active", "ocr_text": "hi", "text_heavy": True},
            ],
            "edges": [{"image_id1": IMG_A, "image_id2": IMG_B, "distance": 0.04,
                       "match_source": "in_batch", "distance_source": None}],
            "total_members": 2,
        }],
        "next_cursor": None, "has_next": False,
    }
    assert client.get("/api/ingestion/clusters/tier_a").json() == {
        "items": [{
            "members": [
                {"image_id": IMG_A, "filename": "a.jpg", "status": "pending", "ocr_text": None, "text_heavy": False},
                {"image_id": IMG_B, "filename": "b.jpg", "status": "active", "ocr_text": "hi", "text_heavy": True},
            ],
            "edges": [{"image_id1": IMG_A, "image_id2": IMG_B, "distance": 0.04,
                       "match_source": "in_batch", "distance_source": None}],
            "total_members": 2,
        }],
        "next_cursor": None, "has_next": False,
    }


def test_ingestion_tier_b_json(client, ingestion_service):
    member = {"image_id": IMG_B, "filename": "b.jpg", "status": "active", "ocr_text": "x"}
    ingestion_service.list_tier_b_review.return_value = {
        "items": [{"image": {"image_id": IMG_A, "filename": "a.jpg", "status": "pending", "ocr_text": "y"},
                   "candidates": [{"member": member, "distance": 0.1, "match_source": None,
                                   "distance_source": "ocr_text"}],
                   "total_candidates": 1}],
        "next_cursor": "c", "has_next": True,
    }
    assert client.get("/api/ingestion/review/tier_b").json() == {
        "items": [{"image": {"image_id": IMG_A, "filename": "a.jpg", "status": "pending", "ocr_text": "y",
                             "text_heavy": False},
                   "candidates": [{"member": {**member, "text_heavy": False}, "distance": 0.1,
                                   "match_source": None, "distance_source": "ocr_text"}],
                   "total_candidates": 1}],
        "next_cursor": "c", "has_next": True,
    }


def test_ingestion_resolve_and_undo_json(client, ingestion_service):
    ingestion_service.resolve.return_value = {
        "rejected": [IMG_A], "kept": [IMG_B],
        "failed": [{"image_id": "x", "decision": "keep", "error": "e"}],
        "move_failed": [{"image_id": "y", "error": "m"}],
    }
    r = client.post("/api/ingestion/clusters/tier_a/resolve",
                    json={"decisions": [{"image_id": IMG_A, "decision": "reject"}]})
    assert r.json() == {"rejected": [IMG_A], "kept": [IMG_B],
                        "failed": [{"image_id": "x", "decision": "keep", "error": "e"}],
                        "move_failed": [{"image_id": "y", "error": "m"}]}
    # request validation stays strict: image_id must be a UUID, decision a known value
    assert client.post("/api/ingestion/clusters/tier_a/resolve",
                       json={"decisions": [{"image_id": "nope", "decision": "reject"}]}).status_code == 422
    assert client.post("/api/ingestion/clusters/tier_a/resolve",
                       json={"decisions": [{"image_id": IMG_A, "decision": "maybe"}]}).status_code == 422
    ingestion_service.undo_reject.return_value = {"image_id": IMG_A, "status": "pending"}
    assert client.post(f"/api/ingestion/images/{IMG_A}/undo-reject").json() == {
        "image_id": IMG_A, "status": "pending"}


RUN = {"run_id": "r", "batch_name": "b", "trigger": "manual", "status": "completed",
       "created_at": TS, "completed_at": None, "error": None}
RUN_JSON = {**RUN, "created_at": TS_JSON}


def test_admin_batch_json(client, admin_service):
    admin_service.list_batch_names.return_value = {"names": ["a", "b"]}
    assert client.get("/api/admin/batches/names").json() == {"names": ["a", "b"]}
    admin_service.trigger_run.return_value = {"run_id": "r", "status": "started"}
    assert client.post("/api/admin/batches/x/run").json() == {"run_id": "r", "status": "started"}
    admin_service.get_run.return_value = RUN
    assert client.get(f"/api/admin/batches/runs/{uuid.uuid4()}").json() == RUN_JSON
    admin_service.list_runs.return_value = {"items": [RUN], "total": 1}
    assert client.get("/api/admin/batches/runs").json() == {"items": [RUN_JSON], "total": 1}


def test_admin_metrics_json(client, admin_service):
    admin_service.batch_metrics.return_value = {
        "process": "p",
        "items": [{"run_id": "r", "run_created_at": TS, "counts": {"k": 1}, "metrics": {"m": None, "n": 0.5}}],
        "unattributed": {"counts": {}, "metrics": {}},
    }
    assert client.get("/api/admin/batches/metrics?process=p").json() == {
        "process": "p",
        "items": [{"run_id": "r", "run_created_at": TS_JSON, "counts": {"k": 1}, "metrics": {"m": None, "n": 0.5}}],
        "unattributed": {"counts": {}, "metrics": {}},
    }


# (router, method, path as registered on the router) -> schema-generated twin of the response model
TWINS = [
    (ingestion_api.router, "GET", "/ingestion/run", ingestionrunstatus.Schema),
    (ingestion_api.router, "GET", "/ingestion/clusters/{tier}", ingestionclusterpage.Schema),
    (ingestion_api.router, "GET", "/ingestion/review/tier_b", ingestiontierbreviewpage.Schema),
    (ingestion_api.router, "POST", "/ingestion/clusters/{tier}/resolve", ingestionresolveresponse.Schema),
    (ingestion_api.router, "POST", "/ingestion/images/{image_id}/undo-reject",
     ingestionundorejectresponse.Schema),
    (admin_api.router, "GET", "/admin/batches/names", batchnamesresponse.Schema),
    (admin_api.router, "POST", "/admin/batches/{batch_name}/run", runtriggerresponse.Schema),
    (admin_api.router, "GET", "/admin/batches/runs/{run_id}", runstatusresponse.Schema),
    (admin_api.router, "GET", "/admin/batches/runs", runlistresponse.Schema),
    (admin_api.router, "GET", "/admin/batches/metrics", batchmetricsresponse.Schema),
]


def _flatten(t):
    args = typing.get_args(t)
    if not args:
        return [t]
    res = []
    for a in args:
        res.extend(_flatten(a))
    return res


def _walk(model):
    """Map a model and every nested pydantic model it references to its field-name set, keyed by field path."""
    out = {}

    def visit(m, path):
        out[path] = set(m.model_fields)
        for name, f in m.model_fields.items():
            for arg in _flatten(f.annotation):
                if isinstance(arg, type) and issubclass(arg, BaseModel):
                    visit(arg, f"{path}.{name}")

    visit(model, "$")
    return out


@pytest.mark.parametrize("router,method,path,twin", TWINS, ids=[f"{m} {p}" for _, m, p, _ in TWINS])
def test_response_model_keeps_every_field_of_its_generated_twin(router, method, path, twin):
    routes = {(m, r.path): r for r in router.routes for m in r.methods}
    model = routes[(method, path)].response_model
    have, want = _walk(model), _walk(twin)
    for where, fields in want.items():
        assert where in have, f"{where} missing from response_model of {method} {path}"
        missing = fields - have[where]
        assert not missing, f"{method} {path} {where}: response_model drops {sorted(missing)}"
