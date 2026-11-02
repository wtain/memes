from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin_discovery import get_discovery_service, router
from repository.discovery_report import DEFAULT_SOURCES

MEMES = {"windowDays": 30, "sources": list(DEFAULT_SOURCES), "impressionsAvailable": True, "items": []}
QUERIES = {"kind": "search", "items": []}
MISSES = {"status": "ok", "cutoff": None, "gapMinutes": 5, "kind": "search", "items": []}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.memes = AsyncMock(return_value=MEMES)
    service.queries = AsyncMock(return_value=QUERIES)
    service.misses = AsyncMock(return_value=MISSES)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_discovery_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False), service


def test_memes_defaults_equal_the_cli(client_and_service, monkeypatch):
    monkeypatch.setattr("Backend.app.api.admin_discovery.window_days", lambda: 30)
    client, service = client_and_service
    response = client.get("/api/admin/discovery/memes")
    assert response.status_code == 200 and response.json() == MEMES
    assert "no-store" in response.headers["cache-control"]
    service.memes.assert_awaited_once_with(window_days=30, sources=DEFAULT_SOURCES, min_redrops=1,
                                           max_impressions=None, sort="per_100", limit=50)


def test_memes_parameters_reach_the_service(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/memes?windowDays=7&sources=recut,hash_cross_corpus&minRedrops=2"
               "&maxImpressions=0&sort=saves&limit=200")
    service.memes.assert_awaited_once_with(window_days=7, sources=("recut", "hash_cross_corpus"), min_redrops=2,
                                           max_impressions=0, sort="saves", limit=200)


def test_queries_and_misses_parameters_reach_the_service(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/queries?kind=all&maxResults=0&includeDegraded=true&sinceDays=3&minTimes=2"
               "&sort=min_results&limit=100")
    service.queries.assert_awaited_once_with(kind="all", max_results=0, include_degraded=True, since_days=3,
                                             min_times=2, sort="min_results", limit=100)
    client.get("/api/admin/discovery/misses?gapMinutes=10&kind=meme_text&sinceDays=5&minTimes=2&explicitOnly=true"
               "&sort=explicit&limit=25")
    service.misses.assert_awaited_once_with(gap_minutes=10, kind="meme_text", since_days=5, min_times=2,
                                            explicit_only=True, sort="explicit", limit=25)


def test_queries_and_misses_defaults(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/queries")
    service.queries.assert_awaited_once_with(kind="search", max_results=3, include_degraded=False, since_days=None,
                                             min_times=1, sort="times", limit=50)
    client.get("/api/admin/discovery/misses")
    service.misses.assert_awaited_once_with(gap_minutes=5, kind="search", since_days=None, min_times=1,
                                            explicit_only=False, sort="times", limit=50)


@pytest.mark.parametrize("path", [
    "memes?limit=0", "memes?limit=201", "memes?sort=nope", "memes?windowDays=0", "memes?minRedrops=-1",
    "queries?kind=nope", "queries?sort=nope", "queries?maxResults=-1", "queries?minTimes=0",
    "misses?gapMinutes=0", "misses?sort=nope", "misses?kind=nope", "misses?limit=0",
])
def test_bad_parameters_are_422(client_and_service, path):
    client, _ = client_and_service
    assert client.get(f"/api/admin/discovery/{path}").status_code == 422


@pytest.mark.parametrize("value", ["", ",", " , "])
def test_an_empty_source_list_is_a_422_not_every_source(client_and_service, value):
    client, service = client_and_service
    response = client.get("/api/admin/discovery/memes", params={"sources": value})
    assert response.status_code == 422 and "source" in response.json()["detail"]
    service.memes.assert_not_awaited()


def test_source_names_are_stripped(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/memes", params={"sources": "recut, hash_cross_corpus"})
    assert service.memes.await_args.kwargs["sources"] == ("recut", "hash_cross_corpus")


def test_an_unknown_source_is_a_422_with_our_message(client_and_service):
    client, service = client_and_service
    service.memes.side_effect = ValueError("unknown popularity source(s): nope")
    response = client.get("/api/admin/discovery/memes?sources=nope")
    assert response.status_code == 422 and "nope" in response.json()["detail"]


def test_a_service_value_error_is_a_422(client_and_service):
    client, service = client_and_service
    service.memes.side_effect = ValueError("max_impressions needs the meme_impressions table, which does not exist yet")
    response = client.get("/api/admin/discovery/memes?maxImpressions=1")
    assert response.status_code == 422 and "meme_impressions" in response.json()["detail"]


def test_an_unexpected_failure_is_a_500_without_details(client_and_service):
    client, service = client_and_service
    service.queries.side_effect = RuntimeError("SECRET-QUERY database blew up")
    response = client.get("/api/admin/discovery/queries")
    assert response.status_code == 500 and "SECRET-QUERY" not in response.text
