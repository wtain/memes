from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.meme_from_text import get_meme_from_text_service, router
from Backend.app.types.generated.memefromtextresponse import Schema as Response
from retrieval.pipeline import InvalidProfile

PROFILE = {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False,
           "emotion": [], "stance": [], "topic": [], "tone": []}
RESPONSE = Response.model_validate({
    "items": [], "hasNext": False, "degraded": False, "fallback": "none", "profile": PROFILE,
    "meta": {"available": True, "parseMs": 0, "embedMs": 0, "retrievalMs": 0, "candidates": 0, "cacheHit": False,
             "parserVersion": "p3-x", "model": "m"}})


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.search = AsyncMock(return_value=RESPONSE)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_meme_from_text_service] = lambda: service
    return TestClient(app), service


def test_post_search_returns_the_generated_response_shape(client_and_service):
    client, service = client_and_service

    response = client.post("/api/meme-from-text/search", json={"text": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"items", "hasNext", "profile", "degraded", "fallback", "meta"}
    assert body["meta"]["available"] is True and body["fallback"] == "none"
    assert service.search.await_args.args[0].text == "hello"
    assert response.headers["cache-control"].startswith("no-")


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "x", "limit": 0}, {"text": "x", "limit": 51},
                                     {"text": "x", "languageHint": "fr"}, {"text": "x", "filters": {"emotion": "joy"}}])
def test_a_malformed_request_is_a_422(client_and_service, payload):
    client, _service = client_and_service
    assert client.post("/api/meme-from-text/search", json=payload).status_code == 422


@pytest.mark.parametrize("payload", [
    {"text": "x" * 8001},
    {"text": "x", "cursor": "c" * 513},
    {"text": "x", "filters": {"emotion": ["joy"] * 65}},
    {"text": "x", "filters": {"emotion": ["j" * 129]}},
    {"text": "x", "filters": {"tags": [{"key": "k", "value": "v"}] * 65}},
    {"text": "x", "filters": {"tags": [{"key": "k" * 129, "value": "v"}]}},
])
def test_an_oversized_request_is_a_422(client_and_service, payload):
    client, service = client_and_service
    assert client.post("/api/meme-from-text/search", json=payload).status_code == 422
    service.search.assert_not_awaited()


def test_a_request_at_the_limits_is_accepted(client_and_service):
    client, _service = client_and_service
    payload = {"text": "x" * 8000, "cursor": "c" * 512, "filters": {"emotion": ["j" * 128] * 64}}
    assert client.post("/api/meme-from-text/search", json=payload).status_code == 200


def test_an_invalid_edited_profile_is_a_422_with_the_errors(client_and_service):
    client, service = client_and_service
    service.search.side_effect = InvalidProfile(["emotion: unknown key 'x'"])

    response = client.post("/api/meme-from-text/search", json={"text": "hello", "profile": PROFILE})

    assert response.status_code == 422 and "unknown key" in str(response.json()["detail"])


def test_the_route_is_post_only(client_and_service):
    client, _service = client_and_service
    assert client.get("/api/meme-from-text/search").status_code == 405


# ---- privacy of failures, and the router end to end with a real service

import logging
from unittest.mock import MagicMock

from sqlalchemy.exc import DBAPIError

from Backend.app.services.meme_from_text_service import MemeFromTextService
from retrieval.facets import RankedItem
from retrieval.model import Candidate, ScoredCandidate
from retrieval.pipeline import SearchResult

MARKER = "SECRET-USER-LEMMA-MARKER"
IMAGE_ID = "11111111-1111-1111-1111-111111111111"


def test_a_database_error_is_a_generic_500_that_never_carries_the_users_text(client_and_service, caplog):
    client, service = client_and_service
    service.search.side_effect = DBAPIError(f"SELECT ... WHERE lemma = $1", (MARKER, "other"), Exception(MARKER))

    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/meme-from-text/search", json={"text": f"hello {MARKER}"})

    assert response.status_code == 500
    assert response.json() == {"detail": "meme-from-text search failed"}
    assert MARKER not in response.text and MARKER not in caplog.text
    ours = [r for r in caplog.records if r.name == "Backend.app.api.meme_from_text"]
    assert len(ours) == 1 and ours[0].levelno == logging.WARNING and "DBAPIError" in ours[0].getMessage()
    assert all(MARKER not in str(r.exc_info) for r in caplog.records)


def _real_service(pipeline):
    retrieval_repository = MagicMock()
    retrieval_repository.load_basics = AsyncMock(return_value={IMAGE_ID: ("a.jpg", False)})
    image_repository = MagicMock()
    image_repository.get_texts = AsyncMock(return_value=[])
    image_repository.get_tags = AsyncMock(return_value=[])
    return MemeFromTextService(pipeline, retrieval_repository, image_repository)


def _client_for(service):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_meme_from_text_service] = lambda: service
    return TestClient(app)


def test_an_environment_without_a_pipeline_answers_unavailable_with_200():
    client = _client_for(_real_service(None))

    response = client.post("/api/meme-from-text/search", json={"text": "my friend got promoted"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [] and body["degraded"] is True and body["fallback"] == "unavailable"
    assert body["meta"]["available"] is False and "nextCursor" not in body


def _pipeline_returning(cluster_id, cluster_size, next_cursor, situation_text, use_when):
    candidate = Candidate(IMAGE_ID, situation_text, use_when, 0.8, {}, 0.0, True, cluster_id)
    result = SearchResult(
        items=[RankedItem(ScoredCandidate(candidate, 0.75, ()), cluster_size)],
        facets={"emotion": {}, "stance": {}, "topic": {}, "tone": {}}, next_cursor=next_cursor,
        has_next=next_cursor is not None, profile={**PROFILE, "situation": None}, degraded=False, fallback="none",
        meta={"available": True, "parseMs": 1, "embedMs": 2, "retrievalMs": 3, "candidates": 1, "cacheHit": False,
              "parserVersion": "p3-x", "model": "m"})
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=result)
    return pipeline


def test_null_fields_are_omitted_from_a_real_response_and_present_when_set():
    bare = _client_for(_real_service(_pipeline_returning(None, None, None, None, None)))
    full = _client_for(_real_service(_pipeline_returning(4, 2, "cur", "A situation.", "Use when it fits.")))

    body = bare.post("/api/meme-from-text/search", json={"text": "hello"}).json()
    item = body["items"][0]
    assert item["meme"]["id"] == IMAGE_ID
    assert "clusterId" not in item and "clusterSize" not in item and "nextCursor" not in body
    assert "situationText" not in item["explanation"] and "useWhen" not in item["explanation"]
    assert "situation" not in body["profile"]

    body = full.post("/api/meme-from-text/search", json={"text": "hello"}).json()
    item = body["items"][0]
    assert item["clusterId"] == 4 and item["clusterSize"] == 2 and body["nextCursor"] == "cur"
    assert item["explanation"]["situationText"] == "A situation."
    assert item["explanation"]["useWhen"] == "Use when it fits."
