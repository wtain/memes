import uuid
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from Backend.app.api import meme_from_text, meme_text_ratings
from Backend.app.error_handlers import validation_error_handler

IMAGE = str(uuid.uuid4())
BODY = {"text": "hello", "imageId": IMAGE, "grade": 2, "rank": 1, "reading": "parsed", "parserVersion": "p3-x",
        "taxonomyVersion": 2, "degraded": False, "fallback": "none", "language": "en"}


class Item(BaseModel):
    name: str


def _client():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(meme_text_ratings.router, prefix="/api")
    app.include_router(meme_from_text.router, prefix="/api")
    app.dependency_overrides[meme_text_ratings.get_rating_service] = lambda: AsyncMock()
    app.dependency_overrides[meme_from_text.get_meme_from_text_service] = lambda: AsyncMock()

    @app.post("/api/other")
    async def other(item: Item):
        return item

    return TestClient(app)


def _assert_clean(response):
    assert response.status_code == 422
    assert "SECRET-MESSAGE" not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}


def test_a_rejected_rating_never_echoes_the_text():
    _assert_clean(_client().put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE", "grade": 3}))


def test_an_over_long_rating_text_never_echoes_it():
    _assert_clean(_client().put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE" + "x" * 8000}))


def test_a_rejected_search_never_echoes_the_text():
    _assert_clean(_client().post("/api/meme-from-text/search", json={"text": "SECRET-MESSAGE", "limit": 999}))


def test_other_routes_keep_the_default_body_with_input():
    response = _client().post("/api/other", json={"name": 5, "extra": "SECRET-MESSAGE"})
    assert response.status_code == 422
    assert "input" in response.json()["detail"][0]


def test_main_registers_the_handler():
    from Backend.app.main import app
    assert app.exception_handlers[RequestValidationError] is validation_error_handler
