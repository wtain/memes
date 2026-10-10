import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.history import get_history_repo, router


def _row():
    return SimpleNamespace(
        id=uuid.uuid4(), searched_at=datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc), query="q", client="web",
        result_count=3, tags=[SimpleNamespace(category="topic", value="work")], kind="meme_text", language="en",
        query_hash="a" * 64, situation="s", degraded=False, fallback=None, parse_ms=5, retrieval_ms=7)


@pytest.fixture
def client_and_repo():
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[_row()])
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_history_repo] = lambda: repo
    return TestClient(app), repo


def test_default_kind_is_search_and_extra_fields_are_returned(client_and_repo):
    client, repo = client_and_repo
    response = client.get("/api/history")
    assert response.status_code == 200
    assert repo.list.await_args.kwargs["kind"] == "search"
    item = response.json()["items"][0]
    assert item["kind"] == "meme_text" and item["queryHash"] == "a" * 64 and item["parseMs"] == 5
    assert item["retrievalMs"] == 7 and item["situation"] == "s" and item["degraded"] is False


def test_kind_meme_text_and_all_are_passed_through(client_and_repo):
    client, repo = client_and_repo
    client.get("/api/history?kind=meme_text")
    assert repo.list.await_args.kwargs["kind"] == "meme_text"
    client.get("/api/history?kind=all")
    assert repo.list.await_args.kwargs["kind"] == "all"


def test_an_unknown_kind_is_a_422(client_and_repo):
    client, _ = client_and_repo
    assert client.get("/api/history?kind=bogus").status_code == 422
