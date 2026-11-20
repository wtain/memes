"""Domain metrics (O3): image, search, pool and limiter metrics. No database; metrics are switched on by patching `enabled()`."""
import threading
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import QueuePool
from starlette.requests import Request

from Backend.app import observability
from Backend.app.api import images as images_api
from Backend.app.api.images import get_image_service, router as images_router
from Backend.app.observability import metrics
from Backend.app.observability.pool import install_pool_metrics
from Backend.app.services import abuse
from Backend.app.services.meme_from_text_service import MemeFromTextService, UNAVAILABLE_META
from Backend.app.types.generated.memefromtextrequest import Schema as MemeFromTextRequest
from Storage.db import get_async_db
from parse.prompt import PARSER_VERSION
from retrieval.pipeline import SearchResult
from taxonomy.loader import load

SECRET = "SECRET-TEXT-42"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(observability, "enabled", lambda: True)


def total(metric, name_suffix="", **labels):
    """Sum of the samples of `metric` whose labels contain `labels`."""
    out = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if not sample.name.endswith("_created") and not sample.name.endswith("_bucket") and sample.name.endswith(name_suffix) and all(sample.labels.get(k) == v for k, v in labels.items()):
                out += sample.value
    return out


def exposition() -> str:
    from prometheus_client import REGISTRY, generate_latest
    text = generate_latest(REGISTRY).decode()
    return "\n".join(line for line in text.splitlines() if not line.startswith(("python_", "process_", "# HELP python_", "# TYPE python_")))


# --- images --------------------------------------------------------------------------------------------------------------------------

def image_client(monkeypatch, tmp_path, filename):
    class FakeRepo:
        def __init__(self, db):
            pass

        async def get_filename(self, image_id):
            return filename

    path = tmp_path / "tiny.png"
    path.write_bytes(b"\x89PNG-not-really" * 10)
    monkeypatch.setattr(images_api, "ImageRepository", FakeRepo)
    monkeypatch.setattr(images_api, "image_exists", lambda name: name == "tiny.png")
    monkeypatch.setattr(images_api, "get_image_path", lambda name: path)
    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.dependency_overrides[get_async_db] = lambda: MagicMock()
    return TestClient(app, raise_server_exceptions=False), path


def test_image_served_counts_2xx_bytes_and_time(on, monkeypatch, tmp_path):
    client, path = image_client(monkeypatch, tmp_path, "tiny.png")
    ok = total(metrics.IMAGE_SERVED, "_total", status_class="2xx")
    nbytes = total(metrics.IMAGE_BYTES, "_total")
    reads = total(metrics.IMAGE_READ_SECONDS, "_count")
    response = client.get(f"/api/images/{uuid.uuid4()}")
    assert response.status_code == 200 and response.content == path.read_bytes()
    assert total(metrics.IMAGE_SERVED, "_total", status_class="2xx") == ok + 1
    assert total(metrics.IMAGE_BYTES, "_total") == nbytes + len(path.read_bytes())
    assert total(metrics.IMAGE_READ_SECONDS, "_count") == reads + 1


def test_image_missing_counts_4xx_without_the_id(on, monkeypatch, tmp_path):
    client, _ = image_client(monkeypatch, tmp_path, None)
    miss = total(metrics.IMAGE_SERVED, "_total", status_class="4xx")
    image_id = str(uuid.uuid4())
    assert client.get(f"/api/images/{image_id}").status_code == 404
    assert total(metrics.IMAGE_SERVED, "_total", status_class="4xx") == miss + 1
    assert image_id not in exposition()
    client2, _ = image_client(monkeypatch, tmp_path, "gone.png")   # in the database, not on disk
    assert client2.get(f"/api/images/{image_id}").status_code == 404
    assert total(metrics.IMAGE_SERVED, "_total", status_class="4xx") == miss + 2


def test_disabled_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(observability, "enabled", lambda: False)
    client, _ = image_client(monkeypatch, tmp_path, "tiny.png")
    before = exposition()
    assert client.get(f"/api/images/{uuid.uuid4()}").status_code == 200
    metrics.record_search("smart", "total", 1.0)
    metrics.record_rate_limited("saves")
    assert exposition() == before


# --- searches ------------------------------------------------------------------------------------------------------------------------

def test_smart_and_clip_search_total_recorded(on):
    service = AsyncMock()
    service.search.return_value = {"items": [], "hasNext": False, "facets": []}
    service.get_similar.return_value = {"items": [], "hasNext": False, "facets": []}
    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.dependency_overrides[get_image_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)
    smart = total(metrics.SEARCH_SECONDS, "_count", kind="smart", stage="total")
    clip = total(metrics.SEARCH_SECONDS, "_count", kind="clip", stage="total")
    client.get("/api/images", params={"q": SECRET})
    client.get("/api/images")                      # browse feed: not a search
    client.get(f"/api/images/{uuid.uuid4()}/similar")
    client.get(f"/api/images/{uuid.uuid4()}/similar", params={"source": "description"})   # not CLIP
    assert total(metrics.SEARCH_SECONDS, "_count", kind="smart", stage="total") == smart + 1
    assert total(metrics.SEARCH_SECONDS, "_count", kind="clip", stage="total") == clip + 1
    assert SECRET not in exposition()


def reply_service(meta, fallback, degraded):
    tax = load()
    profile = {"taxonomyVersion": tax.version, "language": "en", "parserVersion": PARSER_VERSION, "degraded": degraded,
               "emotion": [], "stance": [], "topic": [], "tone": []}
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=SearchResult([], {}, None, False, profile, degraded, fallback, meta))
    return MemeFromTextService(pipeline, MagicMock(), MagicMock())


def reply_request():
    return MemeFromTextRequest.model_validate({"text": SECRET})


@pytest.mark.asyncio
async def test_meme_from_text_records_stages_total_and_degraded(on):
    meta = {**UNAVAILABLE_META, "available": True, "parseMs": 120, "embedMs": 30, "retrievalMs": 50}
    service = reply_service(meta, "timeout+embed_unavailable", True)
    count = {s: total(metrics.SEARCH_SECONDS, "_count", kind="meme_from_text", stage=s)
             for s in ("total", "parse", "embed", "retrieve")}
    timeout = total(metrics.SEARCH_DEGRADED, "_total", reason="timeout")
    embed = total(metrics.SEARCH_DEGRADED, "_total", reason="embed_unavailable")
    await service.search(reply_request())
    for stage in count:
        assert total(metrics.SEARCH_SECONDS, "_count", kind="meme_from_text", stage=stage) == count[stage] + 1
    assert total(metrics.SEARCH_SECONDS, "_sum", kind="meme_from_text", stage="parse") >= 0.12
    assert total(metrics.SEARCH_DEGRADED, "_total", reason="timeout") == timeout + 1
    assert total(metrics.SEARCH_DEGRADED, "_total", reason="embed_unavailable") == embed + 1
    assert SECRET not in exposition()


@pytest.mark.asyncio
async def test_meme_from_text_unknown_reason_is_other_and_cache_hit_adds_no_stages(on):
    service = reply_service({**UNAVAILABLE_META, "available": True, "cacheHit": True}, "weird-" + SECRET, True)
    parse = total(metrics.SEARCH_SECONDS, "_count", kind="meme_from_text", stage="parse")
    other = total(metrics.SEARCH_DEGRADED, "_total", reason="other")
    await service.search(reply_request())
    assert total(metrics.SEARCH_SECONDS, "_count", kind="meme_from_text", stage="parse") == parse
    assert total(metrics.SEARCH_DEGRADED, "_total", reason="other") == other + 1
    assert SECRET not in exposition()


@pytest.mark.asyncio
async def test_meme_from_text_without_pipeline_counts_unavailable(on):
    service = MemeFromTextService(None, MagicMock(), MagicMock())
    before = total(metrics.SEARCH_DEGRADED, "_total", reason="unavailable")
    await service.search(reply_request())
    assert total(metrics.SEARCH_DEGRADED, "_total", reason="unavailable") == before + 1


def test_search_helpers_ignore_unknown_labels_and_never_raise(on, monkeypatch):
    before = exposition()
    metrics.record_search("other-kind", "total", 1.0)
    metrics.record_search("smart", "weird-stage", 1.0)
    metrics.record_rate_limited("weird-scope")
    assert exposition() == before

    class Broken:
        def labels(self, *args):
            raise RuntimeError("down")

    monkeypatch.setattr(metrics, "SEARCH_SECONDS", Broken())
    metrics.record_search("smart", "total", 1.0)   # no exception


# --- pool ----------------------------------------------------------------------------------------------------------------------------

def make_pool(size=1, overflow=0, timeout=5):
    pool = QueuePool(lambda: MagicMock(), pool_size=size, max_overflow=overflow, timeout=timeout)
    install_pool_metrics(pool)
    return pool


def test_pool_gauges_and_wait_histogram(on):
    pool = make_pool(size=1, overflow=2)
    waits = total(metrics.POOL_WAIT_SECONDS, "_count")
    first = pool.connect()
    assert total(metrics.POOL_CHECKED_OUT) == 1
    second = pool.connect()
    assert total(metrics.POOL_CHECKED_OUT) == 2 and total(metrics.POOL_OVERFLOW) == 1
    first.close()
    second.close()
    assert total(metrics.POOL_CHECKED_OUT) == 0 and total(metrics.POOL_OVERFLOW) == 0
    assert total(metrics.POOL_WAIT_SECONDS, "_count") == waits + 2


def test_pool_wait_is_observed_when_the_pool_is_exhausted(on):
    pool = make_pool(size=1, overflow=0)
    held = pool.connect()
    before = total(metrics.POOL_WAIT_SECONDS, "_sum")
    threading.Timer(0.25, held.close).start()
    pool.connect().close()
    assert total(metrics.POOL_WAIT_SECONDS, "_sum") - before >= 0.2


def test_pool_metric_failure_does_not_break_the_pool(on, monkeypatch):
    class Broken:
        def set(self, *args):
            raise RuntimeError("down")

        def observe(self, *args):
            raise RuntimeError("down")

    pool = make_pool()
    monkeypatch.setattr(metrics, "POOL_CHECKED_OUT", Broken())
    monkeypatch.setattr(metrics, "POOL_WAIT_SECONDS", Broken())
    conn = pool.connect()
    conn.close()


# --- limiters ------------------------------------------------------------------------------------------------------------------------

def request_from(host):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": (host, 1234), "query_string": b""})


def test_enforce_rejection_counts_scope(on, monkeypatch):
    monkeypatch.setattr(abuse, "_number", lambda key, default: 1 if key.endswith("_per_min") else default)
    abuse.reset_limiters()
    request = request_from("203.0.113.9")
    before = total(metrics.RATE_LIMITED, "_total", scope="saves")
    abuse.enforce("saves", request)
    assert total(metrics.RATE_LIMITED, "_total", scope="saves") == before
    with pytest.raises(HTTPException) as error:
        abuse.enforce("saves", request)
    assert error.value.status_code == 429
    assert total(metrics.RATE_LIMITED, "_total", scope="saves") == before + 1
    assert "203.0.113.9" not in exposition()
    abuse.reset_limiters()


def test_recording_refusal_and_body_limit_count(on, monkeypatch):
    monkeypatch.setattr(abuse, "_number", lambda key, default: 1 if key.endswith("_per_min") else default)
    abuse.reset_limiters()
    before = total(metrics.RATE_LIMITED, "_total", scope="recording")
    assert abuse.recording_allowed() is True
    assert abuse.recording_allowed() is False
    assert total(metrics.RATE_LIMITED, "_total", scope="recording") == before + 1
    abuse.reset_limiters()

    import asyncio
    from Backend.app.middleware import _respond_413
    sent = []

    async def send(message):
        sent.append(message)

    body = total(metrics.RATE_LIMITED, "_total", scope="body_limit")
    asyncio.run(_respond_413(send))
    assert total(metrics.RATE_LIMITED, "_total", scope="body_limit") == body + 1


def test_scope_set_matches_the_contract():
    assert metrics.RATE_LIMITED_SCOPES == {"saves", "not_found", "bug_reports", "recording", "telemetry", "body_limit"}


def test_failed_creation_is_remembered(monkeypatch):
    monkeypatch.setattr(metrics, "_create_failed", True)
    monkeypatch.delitem(metrics.__dict__, "HTTP_REQUESTS", raising=False)
    with pytest.raises(RuntimeError):
        metrics._create()
