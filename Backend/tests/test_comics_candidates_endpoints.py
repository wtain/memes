import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.comics import get_candidates_service, get_comics_service, router
from repository.comics import ComicsError
from repository.comics_candidates import CandidateError

KID = str(uuid.uuid4())
IID = str(uuid.uuid4())
IID2 = str(uuid.uuid4())
CID = str(uuid.uuid4())
DETAIL = {"id": CID, "name": "Comics a", "state": "draft", "builtImageId": None, "builtAt": None, "panels": []}
PAGE = {"items": [{"id": KID, "maxDistance": 0.1, "maxGapSeconds": 2.0,
                   "images": [{"id": IID, "filename": "a.jpg", "width": 10, "height": None}]}], "hasMore": True}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.list = AsyncMock(return_value=PAGE)
    service.accept = AsyncMock(return_value=DETAIL)
    service.dismiss = AsyncMock(return_value=None)
    comics = type("C", (), {})()
    comics.detail = AsyncMock(return_value=DETAIL)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_candidates_service] = lambda: service
    app.dependency_overrides[get_comics_service] = lambda: comics
    return TestClient(app), service, comics


def test_list_passes_defaults_and_returns_the_page(client_and_service):
    client, service, _ = client_and_service
    response = client.get("/api/comics/candidates")
    assert response.status_code == 200 and response.json() == PAGE
    service.list.assert_awaited_once_with(0, 20)


def test_list_passes_offset_and_limit(client_and_service):
    client, service, _ = client_and_service
    client.get("/api/comics/candidates?offset=5&limit=7")
    service.list.assert_awaited_once_with(5, 7)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_list_bad_parameters_are_422(client_and_service, query):
    client, service, _ = client_and_service
    assert client.get(f"/api/comics/candidates?{query}").status_code == 422
    service.list.assert_not_awaited()


def test_candidates_route_is_not_captured_by_comics_id(client_and_service):
    client, service, comics = client_and_service
    client.get("/api/comics/candidates")
    service.list.assert_awaited_once()
    comics.detail.assert_not_awaited()  # /{comics_id} would have failed uuid parsing with 422 otherwise


def test_accept_without_body_is_201_and_passes_none(client_and_service):
    client, service, _ = client_and_service
    response = client.post(f"/api/comics/candidates/{KID}/accept")
    assert response.status_code == 201 and response.json() == DETAIL
    service.accept.assert_awaited_once_with(uuid.UUID(KID), None)


def test_accept_with_empty_object_means_all(client_and_service):
    client, service, _ = client_and_service
    client.post(f"/api/comics/candidates/{KID}/accept", json={})
    service.accept.assert_awaited_once_with(uuid.UUID(KID), None)


def test_accept_with_subset_keeps_the_order(client_and_service):
    client, service, _ = client_and_service
    client.post(f"/api/comics/candidates/{KID}/accept", json={"imageIds": [IID2, IID]})
    service.accept.assert_awaited_once_with(uuid.UUID(KID), [uuid.UUID(IID2), uuid.UUID(IID)])


def test_accept_with_a_bad_image_id_is_422(client_and_service):
    client, service, _ = client_and_service
    assert client.post(f"/api/comics/candidates/{KID}/accept", json={"imageIds": ["x"]}).status_code == 422
    service.accept.assert_not_awaited()


def test_bad_candidate_id_is_422(client_and_service):
    client, _, _ = client_and_service
    assert client.post("/api/comics/candidates/nope/accept").status_code == 422
    assert client.post("/api/comics/candidates/nope/dismiss").status_code == 422


def test_dismiss_is_204_without_body(client_and_service):
    client, service, _ = client_and_service
    response = client.post(f"/api/comics/candidates/{KID}/dismiss")
    assert response.status_code == 204 and response.content == b""
    service.dismiss.assert_awaited_once_with(uuid.UUID(KID))


@pytest.mark.parametrize("error, status, body", [
    (CandidateError("not_found"), 404, {"code": "not_found"}),
    (CandidateError("candidate_decided", state="accepted"), 409, {"code": "candidate_decided", "state": "accepted"}),
    (CandidateError("candidate_incomplete", size=3, remaining=2), 409,
     {"code": "candidate_incomplete", "size": 3, "remaining": 2}),
    (CandidateError("subset_invalid", reason="too_few_images"), 422,
     {"code": "subset_invalid", "reason": "too_few_images"}),
])
def test_candidate_errors_map_to_http(client_and_service, error, status, body):
    client, service, _ = client_and_service
    service.accept.side_effect = error
    service.dismiss.side_effect = error
    for response in (client.post(f"/api/comics/candidates/{KID}/accept"),
                     client.post(f"/api/comics/candidates/{KID}/dismiss")):
        assert response.status_code == status and response.json() == {"detail": body}


def test_ineligible_image_is_409_with_the_addable_body(client_and_service):
    client, service, _ = client_and_service
    service.accept.side_effect = ComicsError("image_not_addable", reason="flagged", image_id=uuid.UUID(IID))
    response = client.post(f"/api/comics/candidates/{KID}/accept")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "image_not_addable", "reason": "flagged", "imageId": IID}}
