"""Page-visit beacon (O4a): POST /api/telemetry/pageview. No database; metrics are switched on by patching `enabled()`."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app import observability
from Backend.app.api.telemetry import router as telemetry_router
from Backend.app.middleware import BodyLimitMiddleware, ClientContextMiddleware
from Backend.app.observability import metrics
from Backend.app.services import abuse
from Backend.app.stage_policy import ROUTE_POLICY, READER

URL = "/api/telemetry/pageview"
SECRET = "SECRET-PAGE-42"


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(observability, "enabled", lambda: True)


@pytest.fixture
def limit(monkeypatch):
    def apply(per_min):
        monkeypatch.setattr(abuse, "settings", FakeSettings({"abuse.telemetry_per_min": per_min}))
    abuse.reset_limiters()
    yield apply
    abuse.reset_limiters()


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, path=URL, limit_bytes=1024, charge=False)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(ClientContextMiddleware)
    app.include_router(telemetry_router, prefix="/api")
    return TestClient(app)


def count(metric, **labels):
    out = 0.0
    for family in metric.collect():
        for s in family.samples:
            if s.name.endswith("_total") and all(s.labels.get(k) == v for k, v in labels.items()):
                out += s.value
    return out


def series(metric):
    return {tuple(sorted(s.labels.items())) for f in metric.collect() for s in f.samples if s.name.endswith("_total")}


def test_valid_beacon_counts(client, on):
    before = count(metrics.PAGEVIEWS, client="web", page="/explore")
    r = client.post(URL, json={"client": "web", "page": "/explore"})
    assert r.status_code == 204 and r.content == b""
    assert count(metrics.PAGEVIEWS, client="web", page="/explore") == before + 1


def test_android_page_counts(client, on):
    before = count(metrics.PAGEVIEWS, client="android", page="statistics")
    assert client.post(URL, json={"client": "android", "page": "statistics"}).status_code == 204
    assert count(metrics.PAGEVIEWS, client="android", page="statistics") == before + 1


def test_unknown_page_and_client_become_other(client, on):
    before = count(metrics.PAGEVIEWS, client="other", page="other")
    assert client.post(URL, json={"client": "toaster", "page": "/nope/" + SECRET}).status_code == 204
    assert count(metrics.PAGEVIEWS, client="other", page="other") == before + 1
    assert not any(SECRET in str(s) for s in series(metrics.PAGEVIEWS))


def test_unicode_and_long_page_become_other(client, on):
    for page in ("/эксплор", "‮/explore", "x" * 900):
        assert client.post(URL, json={"client": "web", "page": page}).status_code == 204
    labels = {dict(s).get("page") for s in series(metrics.PAGEVIEWS)}
    assert labels <= metrics.PAGE_ALLOWLIST | {"other"}


def test_huge_body_413(client, on):
    r = client.post(URL, content=b'{"client":"web","page":"' + b"a" * 10240 + b'"}', headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_huge_body_without_content_length_413(client, on):
    def gen():
        for _ in range(10):
            yield b"a" * 1024
    r = client.post(URL, content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("kwargs", [
    {"content": b"not json", "headers": {"content-type": "application/json"}},
    {"content": b"", "headers": {"content-type": "application/json"}},
    {"content": b'{"client":"web","page":"/explore"}', "headers": {"content-type": "text/plain"}},
    {"content": b'[1,2]', "headers": {"content-type": "application/json"}},
    {"content": b'{"client":1,"page":["x"]}', "headers": {"content-type": "application/json"}},
    {"content": b'{"client":"web"}', "headers": {"content-type": "application/json"}},
])
def test_malformed_bodies_no_new_label(client, on, kwargs):
    before = series(metrics.PAGEVIEWS)
    r = client.post(URL, **kwargs)
    assert r.status_code in (204, 422)
    assert series(metrics.PAGEVIEWS) == before


def test_error_body_does_not_echo_input(client, on):
    r = client.post(URL, content=b'{"client":1,"page":"' + SECRET.encode() + b'"}', headers={"content-type": "application/json"})
    assert r.status_code == 422 and SECRET not in r.text


def test_rate_limit_429_and_counter(client, on, limit):
    limit(2)
    before = count(metrics.RATE_LIMITED, scope="telemetry")
    codes = [client.post(URL, json={"client": "web", "page": "/"}).status_code for _ in range(4)]
    assert codes == [204, 204, 429, 429]
    assert count(metrics.RATE_LIMITED, scope="telemetry") == before + 2   # counted once per refusal


def test_off_by_default_never_limits(client, on, limit):
    limit(0)
    assert all(client.post(URL, json={"client": "web", "page": "/"}).status_code == 204 for _ in range(50))


def test_metrics_off_is_noop_204(client, monkeypatch):
    monkeypatch.setattr(observability, "enabled", lambda: False)
    assert client.post(URL, json={"client": "web", "page": "/"}).status_code == 204


def test_nothing_logged(client, on, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post(URL, json={"client": "web", "page": SECRET})
        client.post(URL, content=SECRET.encode(), headers={"content-type": "application/json"})
    assert SECRET not in caplog.text


def test_policy_line_is_reader():
    assert ROUTE_POLICY[("POST", URL)] == READER


def test_allowlist_contents():
    a = metrics.PAGE_ALLOWLIST
    assert {"/", "/explore", "/search", "/reply", "/memes/:id", "/concepts", "/concepts/:id", "/upload", "/trends",
            "/recommendations", "/statistics", "search", "detail", "settings"} <= a
    assert not any(p.startswith("/admin") for p in a)
