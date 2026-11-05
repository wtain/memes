import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.comics import get_comics_service, router
from repository.comics import ComicsError

CID = str(uuid.uuid4())
PID = str(uuid.uuid4())
IID = str(uuid.uuid4())
DETAIL = {"id": CID, "name": "Story", "state": "draft", "builtImageId": None, "builtAt": None,
          "panels": [{"id": PID, "position": 0, "imageId": IID, "sourceFilename": "a.jpg", "kept": False,
                      "sourceImageId": IID}]}
SUMMARY = {"id": CID, "name": "Story", "state": "draft", "panelCount": 1, "coverImageId": IID,
           "createdAt": "2026-11-05T10:00:00+00:00", "builtAt": None}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.list = AsyncMock(return_value={"items": [SUMMARY], "hasMore": False})
    for name in ("create", "detail", "rename", "add_panel", "reorder"):
        setattr(service, name, AsyncMock(return_value=DETAIL))
    service.delete = AsyncMock(return_value=None)
    service.remove_panel = AsyncMock(return_value=None)
    service.addability = AsyncMock(return_value={"items": [
        {"imageId": IID, "addable": True, "reason": None, "comicsId": None, "comicsName": None}]})
    service.by_image = AsyncMock(return_value={"comics": None})
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_comics_service] = lambda: service
    return TestClient(app), service


def test_list_returns_page_and_passes_defaults(client_and_service):
    client, service = client_and_service
    response = client.get("/api/comics")
    assert response.status_code == 200 and response.json() == {"items": [SUMMARY], "hasMore": False}
    service.list.assert_awaited_once_with(None, 50, 0)


def test_list_passes_query(client_and_service):
    client, service = client_and_service
    client.get("/api/comics?q=abc&limit=10&offset=5")
    service.list.assert_awaited_once_with("abc", 10, 5)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_list_bad_parameters_are_422(client_and_service, query):
    client, service = client_and_service
    assert client.get(f"/api/comics?{query}").status_code == 422
    service.list.assert_not_awaited()


def test_create_is_201_with_detail(client_and_service):
    client, service = client_and_service
    response = client.post("/api/comics", json={"name": "Story"})
    assert response.status_code == 201 and response.json() == DETAIL
    service.create.assert_awaited_once_with("Story")


def test_detail_rename_delete(client_and_service):
    client, service = client_and_service
    assert client.get(f"/api/comics/{CID}").json() == DETAIL
    assert client.patch(f"/api/comics/{CID}", json={"name": "New"}).json() == DETAIL
    service.rename.assert_awaited_once_with(uuid.UUID(CID), "New")
    response = client.delete(f"/api/comics/{CID}")
    assert response.status_code == 204 and response.content == b""
    service.delete.assert_awaited_once_with(uuid.UUID(CID))


def test_add_panel_is_201_and_move_defaults_false(client_and_service):
    client, service = client_and_service
    response = client.post(f"/api/comics/{CID}/panels", json={"imageId": IID})
    assert response.status_code == 201 and response.json() == DETAIL
    service.add_panel.assert_awaited_once_with(uuid.UUID(CID), uuid.UUID(IID), False)
    client.post(f"/api/comics/{CID}/panels", json={"imageId": IID, "move": True})
    assert service.add_panel.await_args.args[2] is True


def test_add_panel_malformed_image_id_is_422(client_and_service):
    client, _ = client_and_service
    assert client.post(f"/api/comics/{CID}/panels", json={"imageId": "nope"}).status_code == 422


def test_remove_panel_is_204(client_and_service):
    client, service = client_and_service
    response = client.delete(f"/api/comics/{CID}/panels/{PID}")
    assert response.status_code == 204
    service.remove_panel.assert_awaited_once_with(uuid.UUID(CID), uuid.UUID(PID))


def test_reorder_returns_detail(client_and_service):
    client, service = client_and_service
    response = client.put(f"/api/comics/{CID}/panels/order", json={"panelIds": [PID]})
    assert response.status_code == 200 and response.json() == DETAIL
    service.reorder.assert_awaited_once_with(uuid.UUID(CID), [uuid.UUID(PID)])


def test_reorder_with_stale_ids_is_422_and_applies_nothing(client_and_service):
    client, service = client_and_service
    service.reorder.side_effect = ComicsError("reorder_mismatch")
    response = client.put(f"/api/comics/{CID}/panels/order", json={"panelIds": [PID]})
    assert response.status_code == 422 and response.json() == {"detail": {"code": "reorder_mismatch"}}


def test_addability_parses_ids(client_and_service):
    client, service = client_and_service
    other = str(uuid.uuid4())
    response = client.get(f"/api/comics/addability?imageIds={IID},{other}")
    assert response.status_code == 200 and response.json()["items"][0]["imageId"] == IID
    service.addability.assert_awaited_once_with([uuid.UUID(IID), uuid.UUID(other)])


@pytest.mark.parametrize("ids", ["", "nope", ",".join(str(uuid.uuid4()) for _ in range(101))])
def test_addability_bad_ids_are_422(client_and_service, ids):
    client, service = client_and_service
    assert client.get(f"/api/comics/addability?imageIds={ids}").status_code == 422
    service.addability.assert_not_awaited()


def test_addability_without_ids_is_422(client_and_service):
    client, _ = client_and_service
    assert client.get("/api/comics/addability").status_code == 422


def test_addability_accepts_100_ids(client_and_service):
    client, service = client_and_service
    ids = ",".join(str(uuid.uuid4()) for _ in range(100))
    assert client.get(f"/api/comics/addability?imageIds={ids}").status_code == 200


def test_by_image_for_retired_panel_is_null_comics(client_and_service):
    client, service = client_and_service
    response = client.get(f"/api/comics/by-image/{IID}")
    assert response.status_code == 200 and response.json() == {"comics": None}
    service.by_image.assert_awaited_once_with(uuid.UUID(IID))


def test_by_image_with_comics(client_and_service):
    client, service = client_and_service
    body = {"comics": {"id": CID, "name": "Story", "state": "draft", "position": 2}}
    service.by_image.return_value = body
    assert client.get(f"/api/comics/by-image/{IID}").json() == body


@pytest.mark.parametrize("error,status,body", [
    (ComicsError("not_found"), 404, {"code": "not_found"}),
    (ComicsError("name_taken"), 409, {"code": "name_taken"}),
    (ComicsError("built"), 409, {"code": "built"}),
    (ComicsError("image_in_comics", comics_id=uuid.UUID(CID), comics_name="Story"), 409,
     {"code": "image_in_comics", "comicsId": CID, "comicsName": "Story"}),
    (ComicsError("image_not_addable", reason="flagged"), 409, {"code": "image_not_addable", "reason": "flagged"}),
    (ComicsError("image_not_addable", reason="not_found"), 404,
     {"code": "image_not_addable", "reason": "not_found"}),
    (ComicsError("name_invalid", reason="empty"), 422, {"code": "name_invalid", "reason": "empty"}),
    (ComicsError("not_buildable", reason="too_few_panels"), 409,
     {"code": "not_buildable", "reason": "too_few_panels"}),
])
def test_errors_map_to_status_and_code(client_and_service, error, status, body):
    client, service = client_and_service
    service.create.side_effect = error
    response = client.post("/api/comics", json={"name": "x"})
    assert response.status_code == status and response.json() == {"detail": body}
