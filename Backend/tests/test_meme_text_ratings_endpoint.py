import logging
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.meme_text_ratings import get_rating_service, router
from Backend.app.services.meme_text_rating_service import RatingValidationError, UnknownImageError
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse

IMAGE = str(uuid.uuid4())
HASH = "a" * 64
BODY = {"text": "hello", "imageId": IMAGE, "grade": 2, "rank": 1, "reading": "parsed", "parserVersion": "p3-x",
        "taxonomyVersion": 2, "degraded": False, "fallback": "none", "language": "en"}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.put = AsyncMock(return_value=RatingResponse(queryHash=HASH, imageId=IMAGE, grade=2))
    service.delete = AsyncMock(return_value=None)
    service.list = AsyncMock(return_value=RatingsResponse.model_validate(
        {"queryHash": HASH, "ratings": [{"imageId": IMAGE, "grade": 2}]}))
    service.forget_text = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_rating_service] = lambda: service
    return TestClient(app), service


def test_put_returns_the_stored_rating(client_and_service):
    client, service = client_and_service
    response = client.put("/api/meme-from-text/ratings", json=BODY)
    assert response.status_code == 200
    assert response.json() == {"queryHash": HASH, "imageId": IMAGE, "grade": 2}
    assert service.put.await_args.args[0].text == "hello"


@pytest.mark.parametrize("patch", [{"grade": 3}, {"grade": -1}, {"rank": 0}, {"reading": "guessed"},
                                   {"text": ""}, {"language": "fr"}, {"text": "x" * 8001}])
def test_a_malformed_request_is_a_422(client_and_service, patch):
    client, _ = client_and_service
    assert client.put("/api/meme-from-text/ratings", json={**BODY, **patch}).status_code == 422


def test_validation_and_unknown_image_errors_map_to_422_and_404_without_the_text(client_and_service):
    client, service = client_and_service
    service.put.side_effect = RatingValidationError("the message is empty")
    assert client.put("/api/meme-from-text/ratings", json=BODY).status_code == 422
    service.put.side_effect = UnknownImageError()
    response = client.put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE"})
    assert response.status_code == 404 and "SECRET-MESSAGE" not in response.text


def test_delete_and_forget_answer_204(client_and_service):
    client, service = client_and_service
    assert client.delete(f"/api/meme-from-text/ratings?queryHash={HASH}&imageId={IMAGE}").status_code == 204
    assert client.delete(f"/api/meme-from-text/ratings/text?queryHash={HASH}").status_code == 204
    service.delete.assert_awaited_once_with(HASH, IMAGE)
    service.forget_text.assert_awaited_once_with(HASH)


def test_get_lists_the_grades_of_one_message(client_and_service):
    client, _ = client_and_service
    response = client.get(f"/api/meme-from-text/ratings?queryHash={HASH}")
    assert response.status_code == 200
    assert response.json() == {"queryHash": HASH, "ratings": [{"imageId": IMAGE, "grade": 2}]}


def test_a_bad_hash_is_a_422_and_a_missing_query_param_too(client_and_service):
    client, service = client_and_service
    service.list.side_effect = RatingValidationError("bad hash")
    assert client.get("/api/meme-from-text/ratings?queryHash=short").status_code == 422
    assert client.get("/api/meme-from-text/ratings").status_code == 422


def test_an_unexpected_error_is_a_generic_500_that_never_contains_the_text(client_and_service, caplog):
    client, service = client_and_service
    service.put.side_effect = RuntimeError("boom SECRET-MESSAGE")
    caplog.set_level(logging.DEBUG)

    response = client.put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE"})

    assert response.status_code == 500
    assert "SECRET-MESSAGE" not in response.text and "SECRET-MESSAGE" not in caplog.text
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)


async def test_the_real_dependency_binds_the_history_repository_to_the_same_session():
    from unittest.mock import MagicMock
    from Backend.app.repositories.history_repository import HistoryRepository
    session = MagicMock()
    generator = get_rating_service(session)
    service = await generator.__anext__()
    assert isinstance(service.history_repository, HistoryRepository)
    assert service.history_repository.session is session
    assert service.repository.session is session
    await generator.aclose()


def test_forget_text_deletes_the_rating_text_and_the_query_log_rows():
    from unittest.mock import MagicMock
    from sqlalchemy.dialects import postgresql
    from Storage.db import get_async_db
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    async def override():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_async_db] = override
    response = TestClient(app).delete(f"/api/meme-from-text/ratings/text?queryHash={HASH}")
    assert response.status_code == 204
    statements = [str(call.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
                  for call in session.execute.await_args_list]
    assert any(s.startswith("UPDATE meme_text_ratings") and "query_text" in s for s in statements)
    deletes = [s for s in statements if s.startswith("DELETE FROM search_history")]
    assert len(deletes) == 1 and "'meme_text'" in deletes[0] and HASH in deletes[0]
