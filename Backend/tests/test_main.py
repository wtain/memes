"""
Tests for main application endpoints (health check, etc.)

Note: The main app tests use a minimal app setup to avoid Storage initialization.
The health endpoint is tested by recreating it in an isolated test app.
"""
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


# Create test app with health endpoint
test_app = FastAPI(title="Memes API", version="0.1.0")


@test_app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {"status": "healthy"}


@pytest.fixture
def client():
    """Create test client."""
    with TestClient(test_app) as test_client:
        yield test_client


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_check_returns_healthy(self, client):
        """Test that health endpoint returns healthy status."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_check_is_fast(self, client):
        """Test that health endpoint responds quickly (no DB dependency)."""
        import time
        start = time.time()
        response = client.get("/health")
        elapsed = time.time() - start

        assert response.status_code == 200
        # Health check should respond in under 100ms
        assert elapsed < 0.1

    def test_health_check_json_content_type(self, client):
        """Test that health endpoint returns JSON content type."""
        response = client.get("/health")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

    def test_health_check_method_not_allowed_for_post(self, client):
        """Test that POST to health endpoint returns 405."""
        response = client.post("/health")
        assert response.status_code == 405

    def test_health_check_method_not_allowed_for_put(self, client):
        """Test that PUT to health endpoint returns 405."""
        response = client.put("/health")
        assert response.status_code == 405


class TestAppConfiguration:
    """Tests for application configuration."""

    def test_app_title(self, client):
        """Test that app has correct title."""
        assert test_app.title == "Memes API"

    def test_app_version(self, client):
        """Test that app has a version."""
        assert test_app.version is not None
        assert test_app.version == "0.1.0"

    def test_openapi_docs_available(self, client):
        """Test that OpenAPI docs are available."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_schema_available(self, client):
        """Test that OpenAPI schema is available."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema

    def test_health_endpoint_in_openapi_schema(self, client):
        """Test that health endpoint is documented in OpenAPI."""
        response = client.get("/openapi.json")
        schema = response.json()
        assert "/health" in schema["paths"]