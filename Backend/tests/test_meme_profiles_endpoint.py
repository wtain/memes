import logging
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from Backend.app.api.meme_profiles import get_profile_service, router
from Backend.app.error_handlers import validation_error_handler
from Backend.app.services.meme_profile_service import (
    ProfileConflictError, ProfileValidationError, UnknownImageError,
)

IMAGE = str(uuid.uuid4())
URL = f"/api/meme-from-text/profiles/{IMAGE}"
PROFILE = {"imageId": IMAGE, "exists": True, "humanVerified": True, "needsReview": False, "taxonomyVersion": 2,
           "currentTaxonomyVersion": 2, "situation": "s", "situationConfidence": None, "useWhen": None,
           "values": {"emotion": [{"key": "joy", "confidence": 1.0, "source": "human"}], "stance": [], "topic": [], "tone": []},
           "vector": "fresh", "extractedAt": "2026-10-09T10:00:00", "updatedAt": "2026-10-09T10:00:00"}
BODY = {"situation": "s", "useWhen": None, "values": {"emotion": ["joy"], "stance": [], "topic": [], "tone": []}}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.get = AsyncMock(return_value=PROFILE)
    service.save = AsyncMock(return_value=PROFILE)
    service.confirm = AsyncMock(return_value=PROFILE)
    service.reset = AsyncMock(return_value=None)
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_profile_service] = lambda: service
    return TestClient(app), service


def test_get_returns_the_profile(client_and_service):
    client, service = client_and_service
    response = client.get(URL)
    assert response.status_code == 200 and response.json() == PROFILE
    service.get.assert_awaited_once_with(IMAGE)


def test_put_passes_the_parsed_body_and_returns_the_profile(client_and_service):
    client, service = client_and_service
    response = client.put(URL, json=BODY)
    assert response.status_code == 200 and response.json() == PROFILE
    args = service.save.await_args.args
    assert args[0] == IMAGE and args[1].situation == "s" and args[1].values.emotion == ["joy"]


def test_confirm_returns_the_profile(client_and_service):
    client, service = client_and_service
    response = client.post(URL + "/confirm")
    assert response.status_code == 200 and response.json() == PROFILE
    service.confirm.assert_awaited_once_with(IMAGE)


def test_delete_answers_204_with_an_empty_body(client_and_service):
    client, service = client_and_service
    response = client.delete(URL)
    assert response.status_code == 204 and response.content == b""
    service.reset.assert_awaited_once_with(IMAGE)


@pytest.mark.parametrize("patch", [
    {"situation": "SECRET-SITUATION" + "x" * 161},
    {"values": {"stance": [], "topic": [], "tone": []}},
    {"values": {"emotion": "joy", "stance": [], "topic": [], "tone": []}},
])
def test_a_malformed_body_is_a_422_that_never_echoes_the_text(client_and_service, patch):
    client, service = client_and_service
    response = client.put(URL, json={**BODY, "situation": "SECRET-SITUATION", **patch})
    assert response.status_code == 422
    assert "SECRET-SITUATION" not in response.text
    service.save.assert_not_awaited()


def test_service_errors_map_to_fixed_statuses(client_and_service):
    client, service = client_and_service
    service.save.side_effect = ProfileValidationError("unknown key in emotion")
    response = client.put(URL, json=BODY)
    assert response.status_code == 422 and response.json() == {"detail": "unknown key in emotion"}
    service.save.side_effect = UnknownImageError()
    response = client.put(URL, json=BODY)
    assert response.status_code == 404 and response.json() == {"detail": "image not found"}
    service.save.side_effect = ProfileConflictError("the profile has a removed key")
    response = client.put(URL, json=BODY)
    assert response.status_code == 409 and response.json() == {"detail": "the profile has a removed key"}


def test_an_unexpected_error_is_a_generic_500_that_never_leaks(client_and_service, caplog):
    client, service = client_and_service
    service.save.side_effect = RuntimeError("boom SECRET")
    caplog.set_level(logging.DEBUG)

    response = client.put(URL, json={**BODY, "situation": "SECRET"})

    assert response.status_code == 500 and response.json() == {"detail": "profile request failed"}
    assert "SECRET" not in response.text and "SECRET" not in caplog.text
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)
