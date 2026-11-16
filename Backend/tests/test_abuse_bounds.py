"""Abuse bounds (P5a-T6): limiter, q/facet caps, impression cap, browse feed, body limit, directory cap, and dev behaviour unchanged.

Settings are swapped by replacing `abuse.settings` with a dict-backed fake (`.get(key, default)`): an absent key answers its default, which is the
developer default, so a test that sets nothing is a dev backend. No database.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api import bug_reports as bug_reports_module
from Backend.app.api.bug_reports import router as bug_reports_router
from Backend.app.api.history import router as history_router
from Backend.app.api.images import get_image_service, router as images_router
from Backend.app.middleware import BodyLimitMiddleware, ClientContextMiddleware
from Backend.app.services import abuse, history_recorder, image_store, impression_recorder
from Backend.app.services.image_service import ImageService
from Storage.db import get_async_db


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


PROD = {
    "abuse.max_clients": 10000, "abuse.saves_per_min": 30, "abuse.not_found_per_min": 20, "abuse.bug_reports_per_min": 5,
    "abuse.recording_per_min": 1200, "abuse.max_q_chars": 512, "abuse.max_facets": 10, "abuse.max_facet_value_chars": 64,
    "history.record_browse": False, "popularity.impressions.max_rows_per_call": 50,
    "bug_reports.max_body_mb": 11, "bug_reports.max_files": 200, "bug_reports.max_total_mb": 200,
}


@pytest.fixture
def use_settings(monkeypatch):
    def apply(values):
        monkeypatch.setattr(abuse, "settings", FakeSettings(dict(values)))
    abuse.reset_limiters()
    yield apply
    abuse.reset_limiters()


# --- the token-bucket limiter -------------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_bucket_spends_burst_then_refuses_then_refills():
    clock = Clock()
    bucket = abuse.TokenBucketLimiter(clock=clock)
    assert [bucket.allow("a", 3) for _ in range(4)] == [True, True, True, False]
    assert bucket.allow("b", 3) is True                       # another client has its own bucket
    clock.now += 20                                           # 3 per minute = one token per 20 s
    assert bucket.allow("a", 3) is True and bucket.allow("a", 3) is False


def test_rate_zero_is_unlimited_and_stores_nothing():
    bucket = abuse.TokenBucketLimiter()
    assert all(bucket.allow(f"client-{i}", 0) for i in range(100))
    assert len(bucket) == 0


def test_a_cost_above_the_capacity_is_always_refused_and_costs_nothing():
    bucket = abuse.TokenBucketLimiter()
    assert bucket.allow("a", 5, cost=6) is False
    assert bucket.allow("a", 5, cost=5) is True


def test_the_table_is_bounded_whatever_number_of_clients_arrive():
    bucket = abuse.TokenBucketLimiter(max_keys=50)
    for i in range(5000):
        bucket.allow(f"10.0.{i // 250}.{i % 250}", 1)         # every client spends its only token: no bucket is idle
        assert len(bucket) <= 50


def test_full_buckets_are_evicted_by_the_sweep():
    clock = Clock()
    bucket = abuse.TokenBucketLimiter(max_keys=1000, sweep_seconds=10, clock=clock)
    for i in range(100):
        bucket.allow(f"c{i}", 60)                              # each spent one of 60 tokens
    assert len(bucket) == 100
    clock.now += 5                                             # refilled by 5 tokens: not yet full, no sweep due
    bucket.allow("new", 60)
    assert len(bucket) == 101
    clock.now += 10                                            # everyone is back to full and the sweep interval has passed
    bucket.allow("newer", 60)
    assert len(bucket) == 1                                    # only the bucket just created is left


def test_the_least_recently_used_bucket_goes_first():
    bucket = abuse.TokenBucketLimiter(max_keys=2)
    bucket.allow("a", 1)
    bucket.allow("b", 1)
    bucket.allow("a", 1)                                       # a is newest
    bucket.allow("c", 1)                                       # evicts b
    assert bucket.allow("a", 1) is False                       # a kept its (empty) bucket
    assert bucket.allow("b", 1) is True                        # b starts afresh


# --- GET /api/images caps -----------------------------------------------------------------------------------------------------------

def images_client(service):
    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.dependency_overrides[get_image_service] = lambda: service
    return TestClient(app)


@pytest.fixture
def service():
    mock = AsyncMock()
    mock.search.return_value = {"items": [], "hasNext": False, "facets": []}
    return mock


def test_oversize_q_is_422_on_prod_and_accepted_on_dev(use_settings, service):
    client = images_client(service)
    use_settings(PROD)
    assert client.get("/api/images", params={"q": "a" * 512}).status_code == 200
    refused = client.get("/api/images", params={"q": "a" * 513})
    assert refused.status_code == 422
    service.search.reset_mock()
    use_settings({})                                           # dev defaults: no cap
    assert client.get("/api/images", params={"q": "a" * 5000}).status_code == 200


def test_the_refused_request_never_reaches_the_service(use_settings, service):
    use_settings(PROD)
    images_client(service).get("/api/images", params={"q": "a" * 600})
    service.search.assert_not_called()


def test_facet_count_and_length_caps(use_settings, service):
    client = images_client(service)
    use_settings(PROD)
    ten = ",".join(f"topic:v{i}" for i in range(10))
    assert client.get("/api/images", params={"facets": ten}).status_code == 200
    assert client.get("/api/images", params={"facets": ten + ",topic:v10"}).status_code == 422
    assert client.get("/api/images", params={"facets": "topic:" + "x" * 58}).status_code == 200   # 64 characters
    assert client.get("/api/images", params={"facets": "topic:" + "x" * 59}).status_code == 422   # 65 characters
    use_settings({})
    assert client.get("/api/images", params={"facets": ten + ",topic:v10,topic:" + "x" * 500}).status_code == 200


# --- recording budget, impression cap, browse feed ------------------------------------------------------------------------------------

async def test_over_the_budget_the_history_write_is_skipped_not_failed(use_settings, monkeypatch):
    use_settings({"abuse.recording_per_min": 2})
    opened = MagicMock(side_effect=RuntimeError("must not be reached once the budget is spent"))
    monkeypatch.setattr(history_recorder, "AsyncSessionLocal", opened)
    for _ in range(2):
        assert await history_recorder.record_history(query="q", client="web", result_count=0, tags=[]) is None  # fails inside: swallowed
    assert opened.call_count == 2
    assert await history_recorder.record_history(query="q", client="web", result_count=0, tags=[]) is None
    assert opened.call_count == 2                              # the third was skipped before opening a session


async def test_impression_rows_are_capped_per_call_and_share_the_budget_with_history(use_settings, monkeypatch):
    use_settings({"abuse.recording_per_min": 60, "popularity.impressions.max_rows_per_call": 50})
    monkeypatch.setattr(impression_recorder, "impressions_enabled", lambda: True)
    written = []

    class Repo:
        def __init__(self, session):
            pass

        async def record_many(self, surface, ids, history_id):
            written.append(len(ids))

    monkeypatch.setattr(impression_recorder, "MemeImpressionRepository", Repo)
    session = MagicMock()
    session.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    ids = [str(uuid.uuid4()) for _ in range(100)]
    await impression_recorder.record_impressions(session, "search", ids)
    assert written == [50]                                     # capped at 50 rows (the 100 of a full page on dev)
    assert abuse.recording_allowed(10) is True                 # 60 - 50 left
    await impression_recorder.record_impressions(session, "search", ids)   # needs 50, 0 left: skipped
    assert written == [50]


async def test_dev_keeps_every_impression_row_and_has_no_budget(use_settings, monkeypatch):
    use_settings({})
    monkeypatch.setattr(impression_recorder, "impressions_enabled", lambda: True)
    written = []

    class Repo:
        def __init__(self, session):
            pass

        async def record_many(self, surface, ids, history_id):
            written.append(len(ids))

    monkeypatch.setattr(impression_recorder, "MemeImpressionRepository", Repo)
    session = MagicMock()
    session.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    ids = [str(uuid.uuid4()) for _ in range(100)]
    for _ in range(3):
        await impression_recorder.record_impressions(session, "search", ids)
    assert written == [100, 100, 100]


@pytest.fixture
def wired_service(monkeypatch):
    repo = AsyncMock()
    repo.search.return_value = ([], {})
    monkeypatch.setattr(ImageService, "_fill_texts_and_tags", AsyncMock())
    history = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(ImageService, "_record_history", history)
    monkeypatch.setattr("Backend.app.services.image_service.record_impressions", AsyncMock())
    return ImageService(repo, AsyncMock(), AsyncMock()), history


async def test_the_browse_feed_writes_no_history_row_on_prod_but_a_search_does(use_settings, wired_service):
    service, history = wired_service
    use_settings(PROD)
    await service.search(q=None, raw_facets=None, cursor=None, limit=10)
    await service.search(q="  ", raw_facets=None, cursor=None, limit=10)
    history.assert_not_called()
    await service.search(q="cat", raw_facets=None, cursor=None, limit=10)
    await service.search(q=None, raw_facets="topic:work", cursor=None, limit=10)
    assert history.call_count == 2


async def test_the_browse_feed_is_recorded_on_dev(use_settings, wired_service):
    service, history = wired_service
    use_settings({})
    await service.search(q=None, raw_facets=None, cursor=None, limit=10)
    history.assert_called_once()


# --- 429 paths ------------------------------------------------------------------------------------------------------------------------

def test_saves_answer_429_over_the_limit_and_204_on_dev(use_settings, service):
    client = images_client(service)
    url = f"/api/images/meme/{uuid.uuid4()}/saves"
    use_settings({"abuse.saves_per_min": 3})
    assert [client.post(url).status_code for _ in range(5)] == [204, 204, 204, 429, 429]
    assert client.post(url).headers["retry-after"] == "60"
    assert service.record_save.await_count == 3
    abuse.reset_limiters()
    use_settings({})
    assert {client.post(url).status_code for _ in range(50)} == {204}


def test_not_found_answers_429_over_the_limit(use_settings, monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr("Backend.app.api.history.record_not_found", recorded)
    app = FastAPI()
    app.include_router(history_router, prefix="/api")
    app.dependency_overrides[get_async_db] = lambda: object()
    client = TestClient(app)
    use_settings({"abuse.not_found_per_min": 2})
    url = f"/api/history/{uuid.uuid4()}/not-found"
    assert [client.post(url).status_code for _ in range(4)] == [204, 204, 429, 429]
    assert recorded.await_count == 2
    abuse.reset_limiters()
    use_settings({})
    assert {client.post(url).status_code for _ in range(30)} == {204}


@pytest.fixture
def bug_store(tmp_path, monkeypatch):
    directory = tmp_path / "bug-reports"
    monkeypatch.setattr(image_store, "_BUG_REPORTS_DIR", directory)
    return directory


def bug_app():
    app = FastAPI()
    app.include_router(bug_reports_router, prefix="/api")
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(ClientContextMiddleware)
    return app


def post_report(client, content=b"report text", name="r.txt"):
    return client.post("/api/bug-reports", files={"file": (name, content, "text/plain")})


def test_bug_reports_answer_429_over_the_limit(use_settings, bug_store):
    client = TestClient(bug_app())
    use_settings({"abuse.bug_reports_per_min": 2})
    assert [post_report(client).status_code for _ in range(4)] == [200, 200, 429, 429]


def test_bug_reports_keep_their_dev_limit_of_twenty_a_minute(use_settings, bug_store):
    client = TestClient(bug_app())
    use_settings({"abuse.bug_reports_per_min": 20})            # settings.yaml's developer default
    assert [post_report(client).status_code for _ in range(21)] == [200] * 20 + [429]


# --- bug report body limit, chunked read, directory cap ---------------------------------------------------------------------------------

def test_a_declared_oversize_body_is_413_before_the_multipart_parser_runs(use_settings, bug_store, monkeypatch):
    from starlette.formparsers import MultiPartParser
    parsed = []
    original = MultiPartParser.parse

    async def spy(self):
        parsed.append(True)
        return await original(self)

    monkeypatch.setattr(MultiPartParser, "parse", spy)
    use_settings(PROD)
    client = TestClient(bug_app())
    body = b"x" * (11 * 1024 * 1024 + 1)
    response = client.post("/api/bug-reports", content=body, headers={"Content-Type": "multipart/form-data; boundary=zz"})
    assert response.status_code == 413
    assert parsed == []                                        # the parser was never invoked
    assert not bug_store.exists()


def test_a_streamed_oversize_body_without_content_length_is_413_and_the_endpoint_never_runs(use_settings, bug_store, monkeypatch):
    use_settings({"bug_reports.max_body_mb": 1})
    reached = []
    monkeypatch.setattr(bug_reports_module, "save_bug_report", lambda *a: reached.append(a))

    def chunks():
        for _ in range(40):
            yield b"y" * 65536                                 # 2.5 MB in all, no Content-Length

    response = TestClient(bug_app()).post(
        "/api/bug-reports", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=zz"})
    assert response.status_code == 413
    assert reached == []


def test_a_body_inside_the_limit_passes_and_the_limit_is_off_on_dev(use_settings, bug_store):
    use_settings(PROD)
    assert post_report(TestClient(bug_app())).status_code == 200
    use_settings({"abuse.bug_reports_per_min": 0})
    big = b"z" * (3 * 1024 * 1024)
    assert post_report(TestClient(bug_app()), big).status_code == 200   # no body bound on dev


def test_the_endpoint_still_refuses_a_file_over_ten_megabytes(use_settings, bug_store):
    use_settings({"abuse.bug_reports_per_min": 0})
    response = post_report(TestClient(bug_app()), b"z" * (10 * 1024 * 1024 + 1))
    assert response.status_code == 422
    assert not bug_store.exists() or not list(bug_store.iterdir())


def test_over_the_file_count_cap_is_507(use_settings, bug_store):
    bug_store.mkdir()
    for i in range(2):
        (bug_store / f"old{i}.txt").write_text("x")
    use_settings({"bug_reports.max_files": 2})
    client = TestClient(bug_app())
    assert post_report(client).status_code == 507
    use_settings({"bug_reports.max_files": 3})
    assert post_report(client).status_code == 200
    assert post_report(client).status_code == 507


def test_over_the_total_size_cap_is_507(use_settings, bug_store):
    bug_store.mkdir()
    (bug_store / "old.txt").write_bytes(b"x" * (1024 * 1024 - 10))
    use_settings({"bug_reports.max_total_mb": 1})
    client = TestClient(bug_app())
    assert post_report(client, b"y" * 11).status_code == 507
    assert post_report(client, b"y" * 10).status_code == 200


def test_the_directory_is_uncapped_by_default(use_settings, bug_store):
    use_settings({})
    client = TestClient(bug_app())
    assert [post_report(client).status_code for _ in range(5)] == [200] * 5


async def test_the_client_context_middleware_exposes_the_client_address_to_the_recorders():
    seen = []

    async def inner(scope, receive, send):
        seen.append(abuse.current_client.get())

    middleware = ClientContextMiddleware(inner)
    await middleware({"type": "http", "client": ("203.0.113.9", 4000)}, None, None)
    await middleware({"type": "http", "client": None}, None, None)
    assert seen == ["203.0.113.9", "unknown"]
    assert abuse.current_client.get() == "unknown"             # reset after the request


def test_each_client_has_its_own_recording_budget(use_settings):
    use_settings({"abuse.recording_per_min": 1})
    token = abuse.current_client.set("198.51.100.1")
    try:
        assert abuse.recording_allowed(1) is True and abuse.recording_allowed(1) is False
    finally:
        abuse.current_client.reset(token)
    token = abuse.current_client.set("198.51.100.2")
    try:
        assert abuse.recording_allowed(1) is True
    finally:
        abuse.current_client.reset(token)
