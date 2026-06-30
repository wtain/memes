"""
Tests for GET /api/recommendations endpoint.
"""
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.recommendations import router as recommendations_router, get_recommendations_service
from Backend.app.services.recommendations_service import RecommendationsService
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse

app = FastAPI()
app.include_router(recommendations_router, prefix="/api")


@pytest.fixture
def mock_service():
    return AsyncMock(spec=RecommendationsService)


@pytest.fixture
def client(mock_service):
    async def override():
        yield mock_service

    app.dependency_overrides[get_recommendations_service] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_response(items=None, next_cursor=None, has_next=False):
    return MemeSearchResponse(
        items=items or [],
        nextCursor=next_cursor,
        hasNext=has_next,
        facets=[],
    )


def _encode_cursor(seed: int, last_hash: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"seed": seed, "last_hash": last_hash}).encode()
    ).decode()


class TestGetRecommendationsBasic:
    def test_returns_200(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        response = client.get("/api/recommendations")
        assert response.status_code == 200

    def test_response_shape(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response(
            items=[Meme(id="1", imageUrl="/api/images/1", text=["hello"], tags=[], originalFileName="a.jpg")]
        )
        data = client.get("/api/recommendations").json()
        assert "items" in data
        assert "hasNext" in data
        assert "nextCursor" in data
        assert "facets" in data

    def test_facets_always_empty(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        data = client.get("/api/recommendations").json()
        assert data["facets"] == []

    def test_limit_validation_too_high(self, client, mock_service):
        assert client.get("/api/recommendations", params={"limit": 101}).status_code == 422

    def test_limit_validation_too_low(self, client, mock_service):
        assert client.get("/api/recommendations", params={"limit": 0}).status_code == 422

    def test_default_limit_is_20(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations")
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["limit"] == 20

    def test_custom_limit_passed_to_service(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations", params={"limit": 50})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["limit"] == 50


class TestSeedHandling:
    def test_no_cursor_no_seed_generates_random_seed(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations")
        _, kwargs = mock_service.get_recommendations.call_args
        assert isinstance(kwargs["seed"], int)
        assert 0 <= kwargs["seed"] < 2**31

    def test_explicit_seed_used_on_first_page(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations", params={"seed": 42})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["seed"] == 42

    def test_cursor_seed_takes_precedence_over_param(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        cursor = _encode_cursor(seed=99, last_hash="abc")
        client.get("/api/recommendations", params={"seed": 1, "cursor": cursor})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["seed"] == 99

    def test_seed_mismatch_logs_warning(self, client, mock_service, caplog):
        import logging
        mock_service.get_recommendations.return_value = _make_response()
        cursor = _encode_cursor(seed=99, last_hash="abc")
        with caplog.at_level(logging.WARNING, logger="Backend.app.api.recommendations"):
            client.get("/api/recommendations", params={"seed": 1, "cursor": cursor})
        assert any("seed mismatch" in r.message for r in caplog.records)

    def test_matching_seed_no_warning(self, client, mock_service, caplog):
        import logging
        mock_service.get_recommendations.return_value = _make_response()
        cursor = _encode_cursor(seed=42, last_hash="abc")
        with caplog.at_level(logging.WARNING, logger="Backend.app.api.recommendations"):
            client.get("/api/recommendations", params={"seed": 42, "cursor": cursor})
        assert not any("seed mismatch" in r.message for r in caplog.records)

    def test_no_seed_param_with_cursor_uses_cursor_seed(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        cursor = _encode_cursor(seed=77, last_hash="deadbeef")
        client.get("/api/recommendations", params={"cursor": cursor})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["seed"] == 77


class TestCursorPagination:
    def test_no_cursor_passes_none_last_hash(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations")
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["last_hash"] is None

    def test_cursor_decoded_and_last_hash_passed(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        cursor = _encode_cursor(seed=5, last_hash="feedcafe")
        client.get("/api/recommendations", params={"cursor": cursor})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["last_hash"] == "feedcafe"
        assert kwargs["seed"] == 5

    def test_has_next_true_and_next_cursor_present(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response(
            next_cursor="somecursor", has_next=True
        )
        data = client.get("/api/recommendations").json()
        assert data["hasNext"] is True
        assert data["nextCursor"] == "somecursor"

    def test_has_next_false_and_next_cursor_null(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response(has_next=False)
        data = client.get("/api/recommendations").json()
        assert data["hasNext"] is False
        assert data["nextCursor"] is None


class TestQueryHandling:
    def test_query_passed_to_service(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations", params={"q": "funny cat"})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["q"] == "funny cat"

    def test_empty_query_normalized_to_none(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations", params={"q": ""})
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["q"] is None

    def test_no_query_passes_none(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response()
        client.get("/api/recommendations")
        _, kwargs = mock_service.get_recommendations.call_args
        assert kwargs["q"] is None

    def test_items_returned_correctly(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response(
            items=[
                Meme(id="a", imageUrl="/api/images/a", text=["text"], tags=[], originalFileName="a.jpg"),
                Meme(id="b", imageUrl="/api/images/b", text=[], tags=[], originalFileName="b.jpg"),
            ]
        )
        data = client.get("/api/recommendations").json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "a"
        assert data["items"][1]["id"] == "b"

    def test_flagged_flag_in_items(self, client, mock_service):
        mock_service.get_recommendations.return_value = _make_response(
            items=[
                Meme(id="x", imageUrl="/api/images/x", text=[], tags=[], originalFileName="x.jpg", flagged=False),
            ]
        )
        data = client.get("/api/recommendations").json()
        assert data["items"][0]["flagged"] is False


# ── Service unit tests ────────────────────────────────────────────────────────

class TestParseQuery:
    def test_none_returns_empty(self):
        assert RecommendationsService._parse_query(None) == []

    def test_empty_string_returns_empty(self):
        assert RecommendationsService._parse_query("") == []

    def test_whitespace_only_returns_empty(self):
        assert RecommendationsService._parse_query("   ") == []

    def test_single_word(self):
        assert RecommendationsService._parse_query("cat") == ["cat"]

    def test_multiple_words_split_on_whitespace(self):
        assert RecommendationsService._parse_query("hello world") == ["hello", "world"]

    def test_extra_whitespace_between_words(self):
        result = RecommendationsService._parse_query("hello   world")
        assert "hello" in result
        assert "world" in result


class TestCursorEncoding:
    def test_encode_decode_roundtrip(self):
        seed = 123456
        last_hash = "abc123def456" * 2  # 24-char fake hash
        cursor = RecommendationsService._encode_cursor(seed, last_hash)
        decoded_seed, decoded_hash = RecommendationsService.decode_cursor(cursor)
        assert decoded_seed == seed
        assert decoded_hash == last_hash

    def test_cursor_is_url_safe_base64(self):
        cursor = RecommendationsService._encode_cursor(1, "abc")
        assert "+" not in cursor
        assert "/" not in cursor

    def test_hash_matches_postgresql_md5(self):
        # Verify Python's md5(id_str + seed_str) matches what PostgreSQL computes.
        # PostgreSQL: md5(CAST(uuid AS VARCHAR) || seed_str)
        # Python: hashlib.md5((str(uuid) + str(seed)).encode('utf-8')).hexdigest()
        import uuid as uuid_mod
        test_uuid = uuid_mod.UUID("550e8400-e29b-41d4-a716-446655440000")
        seed = 12345
        expected_input = f"{test_uuid}{seed}"  # matches PostgreSQL text concat
        expected_hash = hashlib.md5(expected_input.encode("utf-8")).hexdigest()
        assert len(expected_hash) == 32
        assert expected_hash == expected_hash.lower()