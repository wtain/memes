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


def test_an_invalid_edited_profile_is_a_422_with_the_errors(client_and_service):
    client, service = client_and_service
    service.search.side_effect = InvalidProfile(["emotion: unknown key 'x'"])

    response = client.post("/api/meme-from-text/search", json={"text": "hello", "profile": PROFILE})

    assert response.status_code == 422 and "unknown key" in str(response.json()["detail"])


def test_the_route_is_post_only(client_and_service):
    client, _service = client_and_service
    assert client.get("/api/meme-from-text/search").status_code == 405
