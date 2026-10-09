from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin_profiles import get_profile_coverage_service, router
from Backend.app.services.meme_profile_service import ProfileValidationError

COVERAGE = {"active": 10, "profiled": 8, "withoutProfile": 2, "humanVerified": 1, "needsReview": 3,
            "staleTaxonomy": 0, "failed": 1, "pendingVector": 2, "currentTaxonomyVersion": 2,
            "lastRuns": {"profiles": None, "embeddings": None}}
PAGE = {"items": [{"id": "i1", "imageUrl": "/api/images/i1", "humanVerified": False}], "nextCursor": None, "total": 1}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.coverage = AsyncMock(return_value=COVERAGE)
    service.images = AsyncMock(return_value=PAGE)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_profile_coverage_service] = lambda: service
    return TestClient(app), service


def test_counts_endpoint_returns_the_dict(client_and_service):
    client, _ = client_and_service
    response = client.get("/api/admin/profile-coverage")
    assert response.status_code == 200 and response.json() == COVERAGE


def test_images_endpoint_passes_the_arguments_through(client_and_service):
    client, service = client_and_service
    response = client.get("/api/admin/profile-coverage/images?category=failed&cursor=abc&limit=5")
    assert response.status_code == 200 and response.json() == PAGE
    service.images.assert_awaited_once_with("failed", "abc", 5)


def test_images_defaults(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/profile-coverage/images?category=without_profile")
    service.images.assert_awaited_once_with("without_profile", None, 48)


@pytest.mark.parametrize("query", ["category=nope", "category=failed&limit=0", "category=failed&limit=101", ""])
def test_bad_parameters_are_422(client_and_service, query):
    client, _ = client_and_service
    assert client.get(f"/api/admin/profile-coverage/images?{query}").status_code == 422


def test_a_bad_cursor_is_a_422(client_and_service):
    client, service = client_and_service
    service.images.side_effect = ProfileValidationError("cursor is not valid")
    response = client.get("/api/admin/profile-coverage/images?category=failed&cursor=zz")
    assert response.status_code == 422 and response.json() == {"detail": "cursor is not valid"}
