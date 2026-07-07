"""
Tests for the images endpoints.
Endpoints tested:
- get_images (search with query, facets, pagination)
- get_similar_images
- get_meme (single meme details)
- mark_flagged / unmark_flagged / get_flagged
- get_untagged_images
- get_no_ocr_images
- get_duplicate_images
- get_image (file download)
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
                    originalFileName="test.jpg",
                    flagged=False
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
                    originalFileName="cat.jpg",
                    flagged=False
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
                    originalFileName=f"img{i}.jpg",
                    flagged=False
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
                    originalFileName="page2.jpg",
                    flagged=False
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
                    originalFileName="happy.jpg",
                    flagged=False
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


class TestMarkFlagged:
    """Tests for PUT /api/images/meme/{image_id}/mark_flagged endpoint."""

    def test_mark_flagged_success(self, client, mock_image_service):
        """Test successfully marking an image as flagged."""
        # Arrange
        mock_image_service.mark_flagged.return_value = None

        # Act
        response = client.put("/api/images/meme/123/mark_flagged")

        # Assert
        assert response.status_code == 200
        mock_image_service.mark_flagged.assert_called_once_with("123")

    def test_mark_flagged_multiple_times(self, client, mock_image_service):
        """Test marking the same image as flagged multiple times (should be idempotent)."""
        # Arrange
        mock_image_service.mark_flagged.return_value = None

        # Act
        response1 = client.put("/api/images/meme/456/mark_flagged")
        response2 = client.put("/api/images/meme/456/mark_flagged")

        # Assert
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert mock_image_service.mark_flagged.call_count == 2

    def test_mark_flagged_with_uuid_format(self, client, mock_image_service):
        """Test marking flagged with UUID-format image ID."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_image_service.mark_flagged.return_value = None

        # Act
        response = client.put(f"/api/images/meme/{uuid_id}/mark_flagged")

        # Assert
        assert response.status_code == 200
        mock_image_service.mark_flagged.assert_called_once_with(uuid_id)

    def test_mark_flagged_service_error(self, client, mock_image_service):
        """Test handling of service errors when marking flagged."""
        # Arrange
        mock_image_service.mark_flagged.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(Exception, match="Database error"):
            client.put("/api/images/meme/789/mark_flagged")


class TestUnmarkFlagged:
    """Tests for PUT /api/images/meme/{image_id}/unmark_flagged endpoint."""

    def test_unmark_flagged_success(self, client, mock_image_service):
        """Test successfully unmarking an image as flagged."""
        # Arrange
        mock_image_service.unmark_flagged.return_value = None

        # Act
        response = client.put("/api/images/meme/123/unmark_flagged")

        # Assert
        assert response.status_code == 200
        mock_image_service.unmark_flagged.assert_called_once_with("123")

    def test_unmark_flagged_multiple_times(self, client, mock_image_service):
        """Test unmarking the same image as flagged multiple times (should be idempotent)."""
        # Arrange
        mock_image_service.unmark_flagged.return_value = None

        # Act
        response1 = client.put("/api/images/meme/456/unmark_flagged")
        response2 = client.put("/api/images/meme/456/unmark_flagged")

        # Assert
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert mock_image_service.unmark_flagged.call_count == 2

    def test_unmark_flagged_with_uuid_format(self, client, mock_image_service):
        """Test unmarking flagged with UUID-format image ID."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_image_service.unmark_flagged.return_value = None

        # Act
        response = client.put(f"/api/images/meme/{uuid_id}/unmark_flagged")

        # Assert
        assert response.status_code == 200
        mock_image_service.unmark_flagged.assert_called_once_with(uuid_id)

    def test_unmark_flagged_service_error(self, client, mock_image_service):
        """Test handling of service errors when unmarking flagged."""
        # Arrange
        mock_image_service.unmark_flagged.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(Exception, match="Database error"):
            client.put("/api/images/meme/789/unmark_flagged")


class TestMarkUnmarkFlaggedWorkflow:
    """Integration tests for mark/unmark flagged workflow."""

    def test_mark_then_unmark_workflow(self, client, mock_image_service):
        """Test marking an image as flagged and then unmarking it."""
        # Arrange
        mock_image_service.mark_flagged.return_value = None
        mock_image_service.unmark_flagged.return_value = None
        image_id = "workflow-test-123"

        # Act
        mark_response = client.put(f"/api/images/meme/{image_id}/mark_flagged")
        unmark_response = client.put(f"/api/images/meme/{image_id}/unmark_flagged")

        # Assert
        assert mark_response.status_code == 200
        assert unmark_response.status_code == 200
        mock_image_service.mark_flagged.assert_called_once_with(image_id)
        mock_image_service.unmark_flagged.assert_called_once_with(image_id)


class TestGetFlagged:
    """Tests for GET /api/images/meme/{image_id}/get_flagged endpoint."""

    def test_get_flagged_when_excluded(self, client, mock_image_service):
        """Test getting flagged status when image is flagged."""
        # Arrange
        mock_image_service.get_is_flagged.return_value = True

        # Act
        response = client.get("/api/images/meme/123/get_flagged")

        # Assert
        assert response.status_code == 200
        assert response.json() == 1
        mock_image_service.get_is_flagged.assert_called_once_with("123")

    def test_get_flagged_when_not_excluded(self, client, mock_image_service):
        """Test getting flagged status when image is not flagged."""
        # Arrange
        mock_image_service.get_is_flagged.return_value = False

        # Act
        response = client.get("/api/images/meme/456/get_flagged")

        # Assert
        assert response.status_code == 200
        assert response.json() == 0
        mock_image_service.get_is_flagged.assert_called_once_with("456")

    def test_get_flagged_with_uuid(self, client, mock_image_service):
        """Test getting flagged status with UUID format."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_image_service.get_is_flagged.return_value = True

        # Act
        response = client.get(f"/api/images/meme/{uuid_id}/get_flagged")

        # Assert
        assert response.status_code == 200
        assert response.json() == 1
        mock_image_service.get_is_flagged.assert_called_once_with(uuid_id)


class TestGetSimilarImages:
    """Tests for GET /api/images/{image_id}/similar endpoint."""

    def test_get_similar_images_success(self, client, mock_image_service):
        """Test getting similar images for a given image."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="similar-1",
                    imageUrl="/api/images/similar-1",
                    text=["Similar meme 1"],
                    tags=[MemeTag(name="cat", category="subject")],
                    originalFileName="similar1.jpg",
                    flagged=False,
                    cosineDistance=0.031,
                ),
                Meme(
                    id="similar-2",
                    imageUrl="/api/images/similar-2",
                    text=["Similar meme 2"],
                    tags=[MemeTag(name="cat", category="subject")],
                    originalFileName="similar2.jpg",
                    flagged=False,
                    cosineDistance=0.089,
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_similar.return_value = mock_response

        # Act
        response = client.get("/api/images/123/similar")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "similar-1"
        assert data["items"][0]["cosineDistance"] == 0.031
        assert data["items"][1]["id"] == "similar-2"
        assert data["items"][1]["cosineDistance"] == 0.089
        mock_image_service.get_similar.assert_called_once_with("123", limit=10)

    def test_get_similar_images_no_results(self, client, mock_image_service):
        """Test getting similar images when no similar images found."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_similar.return_value = mock_response

        # Act
        response = client.get("/api/images/456/similar")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        mock_image_service.get_similar.assert_called_once_with("456", limit=10)

    def test_get_similar_images_with_uuid(self, client, mock_image_service):
        """Test getting similar images with UUID format."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_similar.return_value = mock_response

        # Act
        response = client.get(f"/api/images/{uuid_id}/similar")

        # Assert
        assert response.status_code == 200
        mock_image_service.get_similar.assert_called_once_with(uuid_id, limit=10)


class TestGetMeme:
    """Tests for GET /api/images/meme/{image_id} endpoint."""

    def test_get_meme_success(self, client, mock_image_service):
        """Test getting meme details successfully."""
        # Arrange
        mock_meme = Meme(
            id="123",
            imageUrl="/api/images/123",
            text=["Sample text line 1", "Sample text line 2"],
            tags=[
                MemeTag(name="funny", category="mood", score=0.95),
                MemeTag(name="cat", category="subject", score=0.88)
            ],
            originalFileName="cat_meme.jpg",
            flagged=False
        )
        mock_image_service.get_meme.return_value = mock_meme

        # Act
        response = client.get("/api/images/meme/123")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "123"
        assert data["imageUrl"] == "/api/images/123"
        assert len(data["text"]) == 2
        assert len(data["tags"]) == 2
        assert data["tags"][0]["name"] == "funny"
        assert data["tags"][0]["category"] == "mood"
        assert data["originalFileName"] == "cat_meme.jpg"
        mock_image_service.get_meme.assert_called_once_with("123")

    def test_get_meme_with_no_tags(self, client, mock_image_service):
        """Test getting meme with no tags."""
        # Arrange
        mock_meme = Meme(
            id="456",
            imageUrl="/api/images/456",
            text=[],
            tags=[],
            originalFileName="no_tags.jpg",
            flagged=False
        )
        mock_image_service.get_meme.return_value = mock_meme

        # Act
        response = client.get("/api/images/meme/456")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "456"
        assert len(data["tags"]) == 0
        assert len(data["text"]) == 0

    def test_get_meme_with_uuid(self, client, mock_image_service):
        """Test getting meme with UUID format."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_meme = Meme(
            id=uuid_id,
            imageUrl=f"/api/images/{uuid_id}",
            text=["UUID test"],
            tags=[],
            originalFileName="uuid_test.jpg",
            flagged=False
        )
        mock_image_service.get_meme.return_value = mock_meme

        # Act
        response = client.get(f"/api/images/meme/{uuid_id}")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == uuid_id
        mock_image_service.get_meme.assert_called_once_with(uuid_id)


class TestGetUntaggedImages:
    """Tests for GET /api/images/untagged endpoint."""

    def test_get_untagged_images_success(self, client, mock_image_service):
        """Test getting untagged images successfully."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="untagged-1",
                    imageUrl="/api/images/untagged-1",
                    text=["Text without tags"],
                    tags=[],
                    originalFileName="untagged1.jpg",
                    flagged=False
                ),
                Meme(
                    id="untagged-2",
                    imageUrl="/api/images/untagged-2",
                    text=[],
                    tags=[],
                    originalFileName="untagged2.jpg",
                    flagged=False
                )
            ],
            nextCursor="next-untagged",
            hasNext=True,
            facets=[]
        )
        mock_image_service.get_untagged.return_value = mock_response

        # Act
        response = client.get("/api/images/untagged")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "untagged-1"
        assert len(data["items"][0]["tags"]) == 0
        assert data["hasNext"] is True
        mock_image_service.get_untagged.assert_called_once()
        call_kwargs = mock_image_service.get_untagged.call_args.kwargs
        assert call_kwargs["limit"] == 20
        assert call_kwargs["cursor"] is None

    def test_get_untagged_images_with_limit(self, client, mock_image_service):
        """Test getting untagged images with custom limit."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_untagged.return_value = mock_response

        # Act
        response = client.get("/api/images/untagged", params={"limit": 50})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_untagged.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_get_untagged_images_with_cursor(self, client, mock_image_service):
        """Test pagination of untagged images with cursor."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_untagged.return_value = mock_response

        # Act
        response = client.get("/api/images/untagged", params={"cursor": "cursor123"})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_untagged.call_args.kwargs
        assert call_kwargs["cursor"] == "cursor123"

    def test_get_untagged_images_empty_results(self, client, mock_image_service):
        """Test getting untagged images when none found."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_untagged.return_value = mock_response

        # Act
        response = client.get("/api/images/untagged")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False

    def test_get_untagged_images_limit_validation(self, client, mock_image_service):
        """Test limit validation for untagged images endpoint."""
        # Test limit too high
        response = client.get("/api/images/untagged", params={"limit": 101})
        assert response.status_code == 422

        # Test limit too low
        response = client.get("/api/images/untagged", params={"limit": 0})
        assert response.status_code == 422


class TestGetNoOcrImages:
    """Tests for GET /api/images/no-ocr endpoint."""

    def test_get_no_ocr_images_success(self, client, mock_image_service):
        """Test getting no-OCR images successfully."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="no-ocr-1",
                    imageUrl="/api/images/no-ocr-1",
                    text=[],
                    tags=[],
                    originalFileName="no-ocr1.jpg",
                    flagged=False
                ),
                Meme(
                    id="no-ocr-2",
                    imageUrl="/api/images/no-ocr-2",
                    text=[],
                    tags=[],
                    originalFileName="no-ocr2.jpg",
                    flagged=False
                )
            ],
            nextCursor="next-no-ocr",
            hasNext=True,
            facets=[]
        )
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "no-ocr-1"
        assert len(data["items"][0]["text"]) == 0
        assert data["hasNext"] is True
        mock_image_service.get_no_ocr.assert_called_once()
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["limit"] == 20
        assert call_kwargs["cursor"] is None

    def test_get_no_ocr_images_with_limit(self, client, mock_image_service):
        """Test getting no-OCR images with custom limit."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr", params={"limit": 50})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_get_no_ocr_images_with_cursor(self, client, mock_image_service):
        """Test pagination of no-OCR images with cursor."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr", params={"cursor": "cursor123"})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["cursor"] == "cursor123"

    def test_get_no_ocr_images_empty_results(self, client, mock_image_service):
        """Test getting no-OCR images when none found."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False

    def test_get_no_ocr_images_limit_validation(self, client, mock_image_service):
        """Test limit validation for no-OCR images endpoint."""
        # Test limit too high
        response = client.get("/api/images/no-ocr", params={"limit": 101})
        assert response.status_code == 422

        # Test limit too low
        response = client.get("/api/images/no-ocr", params={"limit": 0})
        assert response.status_code == 422


class TestGetDuplicateImages:
    """Tests for GET /api/images/duplicates endpoint."""

    def test_get_duplicates_success(self, client, mock_image_service):
        """Test getting duplicate images successfully."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="dup-1a",
                    imageUrl="/api/images/dup-1a",
                    text=["Duplicate 1"],
                    tags=[],
                    originalFileName="dup1a.jpg",
                    flagged=False
                ),
                Meme(
                    id="dup-1b",
                    imageUrl="/api/images/dup-1b",
                    text=["Duplicate 1"],
                    tags=[],
                    originalFileName="dup1b.jpg",
                    flagged=False
                )
            ],
            nextCursor="dup-next",
            hasNext=True,
            facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        # Act
        response = client.get("/api/images/duplicates")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["hasNext"] is True
        mock_image_service.get_duplicates_clustered.assert_called_once()
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["limit"] == 20
        assert call_kwargs["threshold"] == 0.05
        assert call_kwargs["cursor"] is None

    def test_get_duplicates_with_custom_threshold(self, client, mock_image_service):
        """Test getting duplicates with custom threshold."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        # Act
        response = client.get("/api/images/duplicates", params={"threshold": 0.1})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["threshold"] == 0.1

    def test_get_duplicates_with_limit(self, client, mock_image_service):
        """Test getting duplicates with custom limit."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        # Act
        response = client.get("/api/images/duplicates", params={"limit": 50})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_get_duplicates_with_cursor(self, client, mock_image_service):
        """Test pagination of duplicates with cursor."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        # Act
        response = client.get("/api/images/duplicates", params={"cursor": "dup-cursor"})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["cursor"] == "dup-cursor"

    def test_get_duplicates_threshold_validation(self, client, mock_image_service):
        """Test threshold validation (must be 0.0 to 1.0)."""
        # Test threshold too high
        response = client.get("/api/images/duplicates", params={"threshold": 1.1})
        assert response.status_code == 422

        # Test threshold too low
        response = client.get("/api/images/duplicates", params={"threshold": -0.1})
        assert response.status_code == 422

    def test_get_duplicates_limit_validation(self, client, mock_image_service):
        """Test limit validation for duplicates endpoint."""
        # Test limit too high
        response = client.get("/api/images/duplicates", params={"limit": 101})
        assert response.status_code == 422

        # Test limit too low
        response = client.get("/api/images/duplicates", params={"limit": 0})
        assert response.status_code == 422

    def test_get_duplicates_empty_results(self, client, mock_image_service):
        """Test getting duplicates when none found."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        # Act
        response = client.get("/api/images/duplicates")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False


class TestGetImage:
    """Tests for GET /api/images/{image_id} endpoint (file download)."""

    @pytest.fixture
    def mock_image_repository(self):
        """Mock ImageRepository for file download tests."""
        return AsyncMock()

    @pytest.fixture
    def client_with_db_mock(self, mock_image_repository):
        """Create test client with database mock for file operations."""

        async def override_get_async_db():
            yield mock_image_repository

        from Backend.app.api.images import get_async_db
        app.dependency_overrides[get_async_db] = override_get_async_db

        with TestClient(app) as test_client:
            yield test_client, mock_image_repository

        app.dependency_overrides.clear()

    def test_get_image_file_success(self, client_with_db_mock):
        """Test successfully downloading an image file."""
        client, mock_repo = client_with_db_mock

        # Arrange
        image_id = "test-image-123"
        filename = "test_meme.jpg"

        # Create a mock ImageRepository instance
        mock_repo_instance = AsyncMock()
        mock_repo_instance.get_filename.return_value = filename

        # Mock the ImageRepository constructor to return our instance
        with patch('Backend.app.api.images.ImageRepository', return_value=mock_repo_instance):
            mock_path = MagicMock()
            with patch('Backend.app.api.images.image_exists', return_value=True):
                with patch('Backend.app.api.images.get_image_path', return_value=mock_path):
                    with patch('Backend.app.api.images.FileResponse') as mock_file_response:
                        mock_file_response.return_value = MagicMock(
                            status_code=200,
                            headers={"Content-Type": "image/jpeg"}
                        )

                        response = client.get(f"/api/images/{image_id}")

                        assert response.status_code == 200
                        mock_repo_instance.get_filename.assert_called_once_with(image_id)

    def test_get_image_file_not_found(self, client_with_db_mock):
        """Test downloading image when file not found in database."""
        client, mock_repo = client_with_db_mock

        # Arrange
        image_id = "nonexistent-123"

        # Create a mock ImageRepository instance that returns None
        mock_repo_instance = AsyncMock()
        mock_repo_instance.get_filename.return_value = None

        with patch('Backend.app.api.images.ImageRepository', return_value=mock_repo_instance):
            # Act
            response = client.get(f"/api/images/{image_id}")

            # Assert
            assert response.status_code == 404
            data = response.json()
            assert data["detail"] == "Image not found"
            mock_repo_instance.get_filename.assert_called_once_with(image_id)

    def test_get_image_content_disposition_ascii(self, client_with_db_mock):
        """Test Content-Disposition header with ASCII filename."""
        from Backend.app.api.images import content_disposition

        # Test ASCII filename
        result = content_disposition("test_meme.jpg")
        assert "filename=\"test_meme.jpg\"" in result
        assert "filename*=UTF-8''test_meme.jpg" in result
        assert "inline" in result

    def test_get_image_content_disposition_unicode(self, client_with_db_mock):
        """Test Content-Disposition header with Unicode filename."""
        from Backend.app.api.images import content_disposition

        # Test Unicode filename
        result = content_disposition("мем_тест.jpg")
        assert "inline" in result
        assert "filename*=UTF-8''" in result
        # Verify URL encoding of Cyrillic characters
        assert "%D0%BC%D0%B5%D0%BC" in result  # encoded мем

    def test_get_image_mime_type_detection(self, client_with_db_mock):
        """Test MIME type detection for different file extensions."""
        client, mock_repo = client_with_db_mock

        test_cases = [
            ("image.jpg", "image/jpeg"),
            ("image.png", "image/png"),
            ("image.gif", "image/gif"),
            ("image.webp", "image/webp"),
        ]

        for filename, expected_mime in test_cases:
            # Arrange
            mock_repo_instance = AsyncMock()
            mock_repo_instance.get_filename.return_value = filename

            with patch('Backend.app.api.images.ImageRepository', return_value=mock_repo_instance):
                mock_path = MagicMock()
                with patch('Backend.app.api.images.image_exists', return_value=True):
                    with patch('Backend.app.api.images.get_image_path', return_value=mock_path):
                        with patch('Backend.app.api.images.FileResponse') as mock_file_response:
                            mock_file_response.return_value = MagicMock(status_code=200)

                            response = client.get(f"/api/images/test-{filename}")

                            assert response.status_code == 200


class TestFlaggedHydration:
    """Tests that flagged flag is correctly populated in list responses."""

    def test_search_returns_excluded_flag(self, client, mock_image_service):
        """Test that search results include flagged flag."""
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="1",
                    imageUrl="/api/images/1",
                    originalFileName="excluded.jpg",
                    flagged=True
                ),
                Meme(
                    id="2",
                    imageUrl="/api/images/2",
                    originalFileName="normal.jpg",
                    flagged=False
                ),
            ],
            facets=[],
            nextCursor=None,
            hasNext=False
        )
        mock_image_service.search.return_value = mock_response

        response = client.get("/api/images")
        data = response.json()

        assert response.status_code == 200
        assert len(data["items"]) == 2
        assert data["items"][0]["flagged"] is True
        assert data["items"][1]["flagged"] is False

    def test_similar_returns_excluded_flag(self, client, mock_image_service):
        """Test that similar images include flagged flag."""
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="similar-1",
                    imageUrl="/api/images/similar-1",
                    originalFileName="similar1.jpg",
                    flagged=True
                ),
            ],
            facets=[],
            nextCursor=None,
            hasNext=False
        )
        mock_image_service.get_similar.return_value = mock_response

        response = client.get("/api/images/test-id/similar")
        data = response.json()

        assert response.status_code == 200
        assert len(data["items"]) == 1
        assert data["items"][0]["flagged"] is True

    def test_untagged_returns_excluded_flag(self, client, mock_image_service):
        """Test that untagged images include flagged flag."""
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="untagged-1",
                    imageUrl="/api/images/untagged-1",
                    originalFileName="untagged1.jpg",
                    flagged=False
                ),
            ],
            facets=[],
            nextCursor=None,
            hasNext=False
        )
        mock_image_service.get_untagged.return_value = mock_response

        response = client.get("/api/images/untagged")
        data = response.json()

        assert response.status_code == 200
        assert len(data["items"]) == 1
        assert data["items"][0]["flagged"] is False

    def test_duplicates_returns_excluded_flag(self, client, mock_image_service):
        """Test that duplicate images include flagged flag for both images."""
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="dup-1a",
                    imageUrl="/api/images/dup-1a",
                    originalFileName="dup1a.jpg",
                    flagged=True
                ),
                Meme(
                    id="dup-1b",
                    imageUrl="/api/images/dup-1b",
                    originalFileName="dup1b.jpg",
                    flagged=False
                ),
            ],
            facets=[],
            nextCursor=None,
            hasNext=False
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        response = client.get("/api/images/duplicates")
        data = response.json()

        assert response.status_code == 200
        assert len(data["items"]) == 2
        assert data["items"][0]["flagged"] is True
        assert data["items"][1]["flagged"] is False

    def test_get_meme_returns_excluded_flag(self, client, mock_image_service):
        """Test that single meme details include flagged flag."""
        mock_meme = Meme(
            id="123",
            imageUrl="/api/images/123",
            originalFileName="test.jpg",
            flagged=True
        )
        mock_image_service.get_meme.return_value = mock_meme

        response = client.get("/api/images/meme/123")
        data = response.json()

        assert response.status_code == 200
        assert data["flagged"] is True
