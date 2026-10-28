import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.recut import get_recut_repository, router

ITEM = {"id": "c1", "sourceImageId": "s1", "cutImageId": "i1", "confidence": 0.93,
        "box": {"x": 10, "y": 20, "w": 300, "h": 200}}
DETAIL = {**ITEM, "state": "proposed", "detector": "margin_trim", "detectorVersion": "1", "detectRunId": None,
          "createdAt": "2026-10-28T10:00:00+00:00", "previousId": None, "nextId": "c2"}


@pytest.fixture
def client_and_repo():
    repo = type("R", (), {})()
    repo.list_proposed = AsyncMock(return_value=([ITEM], 7))
    repo.get = AsyncMock(return_value=DETAIL)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_recut_repository] = lambda: repo
    return TestClient(app), repo


def test_list_returns_the_page_and_total(client_and_repo):
    client, repo = client_and_repo
    response = client.get("/api/recut/proposals")
    assert response.status_code == 200
    assert response.json() == {"items": [ITEM], "total": 7}
    repo.list_proposed.assert_awaited_once_with(0, 50)


def test_list_passes_offset_and_limit_through(client_and_repo):
    client, repo = client_and_repo
    client.get("/api/recut/proposals?offset=100&limit=200")
    repo.list_proposed.assert_awaited_once_with(100, 200)


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "limit=abc"])
def test_list_bad_parameters_are_422(client_and_repo, query):
    client, repo = client_and_repo
    assert client.get(f"/api/recut/proposals?{query}").status_code == 422
    repo.list_proposed.assert_not_awaited()


def test_detail_returns_the_record(client_and_repo):
    client, repo = client_and_repo
    cut_id = uuid.uuid4()
    response = client.get(f"/api/recut/proposals/{cut_id}")
    assert response.status_code == 200 and response.json() == DETAIL
    repo.get.assert_awaited_once_with(cut_id)


def test_detail_unknown_is_404(client_and_repo):
    client, repo = client_and_repo
    repo.get.return_value = None
    response = client.get(f"/api/recut/proposals/{uuid.uuid4()}")
    assert response.status_code == 404 and response.json() == {"detail": "Proposal not found"}


def test_detail_with_an_id_that_is_not_a_uuid_is_422(client_and_repo):
    client, repo = client_and_repo
    assert client.get("/api/recut/proposals/not-a-uuid").status_code == 422
    repo.get.assert_not_awaited()
