"""
Tests for the images endpoints.
Endpoints tested: get_images, mark_excluded, unmark_excluded
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from Backend.app.api.images import router as images_router
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memetag import Schema as MemeTag
from Backend.app.types.generated.facet import Schema as Facet
from Backend.app.types.generated.facetbucket import Schema as FacetBucket
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse


# Create test app
app = FastAPI()
app.include_router(images_router, prefix="/api")


@pytest.fixture
def mock_image_service():
    """Mock ImageService for testing."""
    service = AsyncMock()
    return service


@pytest.fixture
def client(mock_image_service):
    """Create test client with mocked dependencies."""

    async def override_get_image_service():
        yield mock_image_service

    # Import the dependency function to override it
    from Backend.app.api.images import get_image_service
    app.dependency_overrides[get_image_service] = override_get_image_service

    with TestClient(app) as test_client:
        yield test_client

    # Clean up
    app.dependency_overrides.clear()


class TestGetImages:
    """Tests for GET /api/images endpoint."""

    def test_get_images_without_query(self, client, mock_image_service):
        """Test getting images without search query."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="123",
                    imageUrl="/api/images/123",
                    text=["Sample text (0.95)"],
                    tags=[MemeTag(name="funny", category="mood")],
                    originalFileName="test.jpg"
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "123"
        assert data["items"][0]["imageUrl"] == "/api/images/123"
        assert data["hasNext"] is False

        # Verify service was called with correct params
        mock_image_service.search.assert_called_once()
        call_kwargs = mock_image_service.search.call_args.kwargs
        assert call_kwargs["q"] is None
        assert call_kwargs["limit"] == 20
        assert call_kwargs["cursor"] is None

    def test_get_images_with_search_query(self, client, mock_image_service):
        """Test getting images with search query."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="456",
                    imageUrl="/api/images/456",
                    text=["Cat meme (0.98)"],
                    tags=[MemeTag(name="cats", category="subject")],
                    originalFileName="cat.jpg"
                )
            ],
            nextCursor="next123",
            hasNext=True,
            facets=[
                Facet(
                    name="subject",
                    buckets=[FacetBucket(value="cats", count=5.0)]
                )
            ]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images", params={"q": "cat"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "456"
        assert data["nextCursor"] == "next123"
        assert data["hasNext"] is True
        assert len(data["facets"]) == 1
        assert data["facets"][0]["name"] == "subject"

        # Verify service was called with search query
        call_kwargs = mock_image_service.search.call_args.kwargs
        assert call_kwargs["q"] == "cat"

    def test_get_images_with_limit(self, client, mock_image_service):
        """Test getting images with custom limit."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id=str(i),
                    imageUrl=f"/api/images/{i}",
                    text=[],
                    tags=[],
                    originalFileName=f"img{i}.jpg"
                )
                for i in range(50)
            ],
            nextCursor="next456",
            hasNext=True,
            facets=[]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images", params={"limit": 50})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 50

        # Verify service was called with custom limit
        call_kwargs = mock_image_service.search.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_get_images_with_cursor(self, client, mock_image_service):
        """Test pagination with cursor."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="789",
                    imageUrl="/api/images/789",
                    text=[],
                    tags=[],
                    originalFileName="page2.jpg"
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images", params={"cursor": "cursor123"})

        # Assert
        assert response.status_code == 200

        # Verify service was called with cursor
        call_kwargs = mock_image_service.search.call_args.kwargs
        assert call_kwargs["cursor"] == "cursor123"

    def test_get_images_with_facets(self, client, mock_image_service):
        """Test filtering with facets."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="111",
                    imageUrl="/api/images/111",
                    text=[],
                    tags=[MemeTag(name="happy", category="mood")],
                    originalFileName="happy.jpg"
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images", params={"facets": "mood:happy,subject:cat"})

        # Assert
        assert response.status_code == 200

        # Verify service was called with facets
        call_kwargs = mock_image_service.search.call_args.kwargs
        assert call_kwargs["raw_facets"] == "mood:happy,subject:cat"

    def test_get_images_limit_validation(self, client, mock_image_service):
        """Test that limit validation works (should be between 1 and 100)."""
        # Test limit too high
        response = client.get("/api/images", params={"limit": 101})
        assert response.status_code == 422  # Validation error

        # Test limit too low
        response = client.get("/api/images", params={"limit": 0})
        assert response.status_code == 422  # Validation error

    def test_get_images_empty_results(self, client, mock_image_service):
        """Test getting images when no results found."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.search.return_value = mock_response

        # Act
        response = client.get("/api/images", params={"q": "nonexistent"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False


class TestMarkExcluded:
    """Tests for PUT /api/images/meme/{image_id}/mark_excluded endpoint."""

    def test_mark_excluded_success(self, client, mock_image_service):
        """Test successfully marking an image as excluded."""
        # Arrange
        mock_image_service.mark_excluded.return_value = None

        # Act
        response = client.put("/api/images/meme/123/mark_excluded")

        # Assert
        assert response.status_code == 200
        mock_image_service.mark_excluded.assert_called_once_with("123")

    def test_mark_excluded_multiple_times(self, client, mock_image_service):
        """Test marking the same image as excluded multiple times (should be idempotent)."""
        # Arrange
        mock_image_service.mark_excluded.return_value = None

        # Act
        response1 = client.put("/api/images/meme/456/mark_excluded")
        response2 = client.put("/api/images/meme/456/mark_excluded")

        # Assert
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert mock_image_service.mark_excluded.call_count == 2

    def test_mark_excluded_with_uuid_format(self, client, mock_image_service):
        """Test marking excluded with UUID-format image ID."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_image_service.mark_excluded.return_value = None

        # Act
        response = client.put(f"/api/images/meme/{uuid_id}/mark_excluded")

        # Assert
        assert response.status_code == 200
        mock_image_service.mark_excluded.assert_called_once_with(uuid_id)

    def test_mark_excluded_service_error(self, client, mock_image_service):
        """Test handling of service errors when marking excluded."""
        # Arrange
        mock_image_service.mark_excluded.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(Exception, match="Database error"):
            client.put("/api/images/meme/789/mark_excluded")


class TestUnmarkExcluded:
    """Tests for PUT /api/images/meme/{image_id}/unmark_excluded endpoint."""

    def test_unmark_excluded_success(self, client, mock_image_service):
        """Test successfully unmarking an image as excluded."""
        # Arrange
        mock_image_service.unmark_excluded.return_value = None

        # Act
        response = client.put("/api/images/meme/123/unmark_excluded")

        # Assert
        assert response.status_code == 200
        mock_image_service.unmark_excluded.assert_called_once_with("123")

    def test_unmark_excluded_multiple_times(self, client, mock_image_service):
        """Test unmarking the same image as excluded multiple times (should be idempotent)."""
        # Arrange
        mock_image_service.unmark_excluded.return_value = None

        # Act
        response1 = client.put("/api/images/meme/456/unmark_excluded")
        response2 = client.put("/api/images/meme/456/unmark_excluded")

        # Assert
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert mock_image_service.unmark_excluded.call_count == 2

    def test_unmark_excluded_with_uuid_format(self, client, mock_image_service):
        """Test unmarking excluded with UUID-format image ID."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_image_service.unmark_excluded.return_value = None

        # Act
        response = client.put(f"/api/images/meme/{uuid_id}/unmark_excluded")

        # Assert
        assert response.status_code == 200
        mock_image_service.unmark_excluded.assert_called_once_with(uuid_id)

    def test_unmark_excluded_service_error(self, client, mock_image_service):
        """Test handling of service errors when unmarking excluded."""
        # Arrange
        mock_image_service.unmark_excluded.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(Exception, match="Database error"):
            client.put("/api/images/meme/789/unmark_excluded")


class TestMarkUnmarkExcludedWorkflow:
    """Integration tests for mark/unmark excluded workflow."""

    def test_mark_then_unmark_workflow(self, client, mock_image_service):
        """Test marking an image as excluded and then unmarking it."""
        # Arrange
        mock_image_service.mark_excluded.return_value = None
        mock_image_service.unmark_excluded.return_value = None
        image_id = "workflow-test-123"

        # Act
        mark_response = client.put(f"/api/images/meme/{image_id}/mark_excluded")
        unmark_response = client.put(f"/api/images/meme/{image_id}/unmark_excluded")

        # Assert
        assert mark_response.status_code == 200
        assert unmark_response.status_code == 200
        mock_image_service.mark_excluded.assert_called_once_with(image_id)
        mock_image_service.unmark_excluded.assert_called_once_with(image_id)
