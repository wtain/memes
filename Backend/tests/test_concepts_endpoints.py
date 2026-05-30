"""
Tests for the concepts endpoints.
Endpoints tested:
- get_concepts (list all concepts)
- get_top_images_for_concept (top images for a concept)
- get_concepts_for_image (concepts for a specific image)
- get_concept (single concept details)
"""
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from Backend.app.api.concepts import router as concepts_router
from Backend.app.types.generated.concept import Schema as Concept
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memetag import Schema as MemeTag
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse


# Create test app
app = FastAPI()
app.include_router(concepts_router, prefix="/api")


@pytest.fixture
def mock_concept_service():
    """Mock ConceptService for testing."""
    service = AsyncMock()
    return service


@pytest.fixture
def client(mock_concept_service):
    """Create test client with mocked dependencies."""

    async def override_get_concept_service():
        yield mock_concept_service

    # Import the dependency function to override it
    from Backend.app.api.concepts import get_concept_service
    app.dependency_overrides[get_concept_service] = override_get_concept_service

    with TestClient(app) as test_client:
        yield test_client

    # Clean up
    app.dependency_overrides.clear()


class TestGetConcepts:
    """Tests for GET /api/concepts endpoint."""

    def test_get_all_concepts_success(self, client, mock_concept_service):
        """Test getting all concepts successfully."""
        # Arrange
        mock_concepts = [
            Concept(id=1, name="Metal Music"),
            Concept(id=2, name="Glam Metal"),
            Concept(id=3, name="Heavy Metal"),
            Concept(id=4, name="Office Work"),
            Concept(id=5, name="Programming"),
        ]
        mock_concept_service.get_all.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Metal Music"
        assert data[4]["id"] == 5
        assert data[4]["name"] == "Programming"
        mock_concept_service.get_all.assert_called_once()

    def test_get_all_concepts_empty(self, client, mock_concept_service):
        """Test getting all concepts when none exist."""
        # Arrange
        mock_concept_service.get_all.return_value = []

        # Act
        response = client.get("/api/concepts")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0
        assert data == []
        mock_concept_service.get_all.assert_called_once()

    def test_get_all_concepts_single(self, client, mock_concept_service):
        """Test getting all concepts with single result."""
        # Arrange
        mock_concepts = [
            Concept(id=10, name="Single Concept")
        ]
        mock_concept_service.get_all.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == 10
        assert data[0]["name"] == "Single Concept"

    def test_get_all_concepts_special_characters(self, client, mock_concept_service):
        """Test getting concepts with special characters in names."""
        # Arrange
        mock_concepts = [
            Concept(id=1, name="Rock & Roll"),
            Concept(id=2, name="Nu-Metal"),
            Concept(id=3, name="Prog/Art Rock"),
            Concept(id=4, name="C++ Programming"),
        ]
        mock_concept_service.get_all.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 4
        assert data[0]["name"] == "Rock & Roll"
        assert data[1]["name"] == "Nu-Metal"
        assert data[2]["name"] == "Prog/Art Rock"
        assert data[3]["name"] == "C++ Programming"

    def test_get_all_concepts_unicode_names(self, client, mock_concept_service):
        """Test getting concepts with Unicode names."""
        # Arrange
        mock_concepts = [
            Concept(id=1, name="Метал музыка"),  # Cyrillic
            Concept(id=2, name="音楽"),  # Japanese
            Concept(id=3, name="Música"),  # Spanish
        ]
        mock_concept_service.get_all.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert data[0]["name"] == "Метал музыка"
        assert data[1]["name"] == "音楽"
        assert data[2]["name"] == "Música"

    def test_get_all_concepts_service_error(self, client, mock_concept_service):
        """Test handling of service errors when getting all concepts."""
        # Arrange
        mock_concept_service.get_all.side_effect = Exception("Database connection error")

        # Act & Assert
        with pytest.raises(Exception, match="Database connection error"):
            client.get("/api/concepts")


class TestGetTopImagesForConcept:
    """Tests for GET /api/concepts/top-images endpoint."""

    def test_get_top_images_success(self, client, mock_concept_service):
        """Test getting top images for a concept successfully."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="img-1",
                    imageUrl="/api/images/img-1",
                    text=["Metal meme 1"],
                    tags=[MemeTag(name="metal", category="genre")],
                    originalFileName="metal1.jpg"
                ),
                Meme(
                    id="img-2",
                    imageUrl="/api/images/img-2",
                    text=["Metal meme 2"],
                    tags=[MemeTag(name="metal", category="genre")],
                    originalFileName="metal2.jpg"
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_concept_service.get_top_images.return_value = mock_response

        # Act
        response = client.get("/api/concepts/top-images", params={"concept_id": 1})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "img-1"
        assert data["items"][1]["id"] == "img-2"
        assert data["hasNext"] is False
        mock_concept_service.get_top_images.assert_called_once_with(1)

    def test_get_top_images_empty_results(self, client, mock_concept_service):
        """Test getting top images when concept has no images."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_concept_service.get_top_images.return_value = mock_response

        # Act
        response = client.get("/api/concepts/top-images", params={"concept_id": 999})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False
        mock_concept_service.get_top_images.assert_called_once_with(999)

    def test_get_top_images_with_large_concept_id(self, client, mock_concept_service):
        """Test getting top images with large concept ID."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )
        mock_concept_service.get_top_images.return_value = mock_response

        # Act
        response = client.get("/api/concepts/top-images", params={"concept_id": 999999})

        # Assert
        assert response.status_code == 200
        mock_concept_service.get_top_images.assert_called_once_with(999999)

    def test_get_top_images_missing_concept_id(self, client, mock_concept_service):
        """Test getting top images without providing concept_id."""
        # Act
        response = client.get("/api/concepts/top-images")

        # Assert
        assert response.status_code == 422  # Validation error

    def test_get_top_images_invalid_concept_id_type(self, client, mock_concept_service):
        """Test getting top images with invalid concept_id type."""
        # Act
        response = client.get("/api/concepts/top-images", params={"concept_id": "invalid"})

        # Assert
        assert response.status_code == 422  # Validation error

    def test_get_top_images_with_pagination(self, client, mock_concept_service):
        """Test getting top images with pagination."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id=f"img-{i}",
                    imageUrl=f"/api/images/img-{i}",
                    text=[f"Meme {i}"],
                    tags=[],
                    originalFileName=f"img{i}.jpg"
                )
                for i in range(20)
            ],
            nextCursor="next-cursor-123",
            hasNext=True,
            facets=[]
        )
        mock_concept_service.get_top_images.return_value = mock_response

        # Act
        response = client.get("/api/concepts/top-images", params={"concept_id": 5})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 20
        assert data["hasNext"] is True
        assert data["nextCursor"] == "next-cursor-123"

    def test_get_top_images_service_error(self, client, mock_concept_service):
        """Test handling of service errors when getting top images."""
        # Arrange
        mock_concept_service.get_top_images.side_effect = Exception("Concept not found")

        # Act & Assert
        with pytest.raises(Exception, match="Concept not found"):
            client.get("/api/concepts/top-images", params={"concept_id": 1})


class TestGetConceptsForImage:
    """Tests for GET /api/concepts/for-image endpoint."""

    def test_get_concepts_for_image_success(self, client, mock_concept_service):
        """Test getting concepts for an image successfully."""
        # Arrange
        mock_concepts = [
            Concept(id=1, name="Metal Music"),
            Concept(id=2, name="Rock Music"),
            Concept(id=3, name="Music"),
        ]
        mock_concept_service.get_for_image.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts/for-image", params={"image_id": "img-123"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Metal Music"
        assert data[1]["id"] == 2
        assert data[1]["name"] == "Rock Music"
        assert data[2]["id"] == 3
        assert data[2]["name"] == "Music"
        mock_concept_service.get_for_image.assert_called_once_with("img-123")

    def test_get_concepts_for_image_empty_results(self, client, mock_concept_service):
        """Test getting concepts when image has no associated concepts."""
        # Arrange
        mock_concept_service.get_for_image.return_value = []

        # Act
        response = client.get("/api/concepts/for-image", params={"image_id": "img-999"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0
        assert data == []
        mock_concept_service.get_for_image.assert_called_once_with("img-999")

    def test_get_concepts_for_image_with_uuid(self, client, mock_concept_service):
        """Test getting concepts for image with UUID format."""
        # Arrange
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_concepts = [
            Concept(id=10, name="Test Concept")
        ]
        mock_concept_service.get_for_image.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts/for-image", params={"image_id": uuid_id})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == 10
        mock_concept_service.get_for_image.assert_called_once_with(uuid_id)

    def test_get_concepts_for_image_missing_image_id(self, client, mock_concept_service):
        """Test getting concepts without providing image_id."""
        # Act
        response = client.get("/api/concepts/for-image")

        # Assert
        assert response.status_code == 422  # Validation error

    def test_get_concepts_for_image_single_concept(self, client, mock_concept_service):
        """Test getting single concept for an image."""
        # Arrange
        mock_concepts = [
            Concept(id=7, name="Programming")
        ]
        mock_concept_service.get_for_image.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts/for-image", params={"image_id": "code-img"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == 7
        assert data[0]["name"] == "Programming"

    def test_get_concepts_for_image_many_concepts(self, client, mock_concept_service):
        """Test getting many concepts for an image."""
        # Arrange
        mock_concepts = [
            Concept(id=i, name=f"Concept {i}")
            for i in range(1, 11)
        ]
        mock_concept_service.get_for_image.return_value = mock_concepts

        # Act
        response = client.get("/api/concepts/for-image", params={"image_id": "multi-concept-img"})

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 10
        assert data[0]["id"] == 1
        assert data[9]["id"] == 10

    def test_get_concepts_for_image_service_error(self, client, mock_concept_service):
        """Test handling of service errors when getting concepts for image."""
        # Arrange
        mock_concept_service.get_for_image.side_effect = Exception("Image not found")

        # Act & Assert
        with pytest.raises(Exception, match="Image not found"):
            client.get("/api/concepts/for-image", params={"image_id": "nonexistent"})


class TestGetConcept:
    """Tests for GET /api/concepts/{concept_id} endpoint."""

    def test_get_concept_by_id_success(self, client, mock_concept_service):
        """Test getting concept by ID successfully."""
        # Arrange
        mock_concept = Concept(id=5, name="Heavy Metal")
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/5")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 5
        assert data["name"] == "Heavy Metal"
        mock_concept_service.get_by_id.assert_called_once_with(5)

    def test_get_concept_by_id_small_id(self, client, mock_concept_service):
        """Test getting concept with small ID."""
        # Arrange
        mock_concept = Concept(id=1, name="First Concept")
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/1")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["name"] == "First Concept"

    def test_get_concept_by_id_large_id(self, client, mock_concept_service):
        """Test getting concept with large ID."""
        # Arrange
        mock_concept = Concept(id=999999, name="Large ID Concept")
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/999999")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 999999
        assert data["name"] == "Large ID Concept"

    def test_get_concept_by_id_with_special_characters(self, client, mock_concept_service):
        """Test getting concept with special characters in name."""
        # Arrange
        mock_concept = Concept(id=10, name="Rock & Roll / Hard Rock")
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/10")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Rock & Roll / Hard Rock"

    def test_get_concept_by_id_with_unicode(self, client, mock_concept_service):
        """Test getting concept with Unicode name."""
        # Arrange
        mock_concept = Concept(id=20, name="Тяжелый метал")  # Cyrillic
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/20")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Тяжелый метал"

    def test_get_concept_by_id_invalid_id_type(self, client, mock_concept_service):
        """Test getting concept with invalid ID type."""
        # Act
        response = client.get("/api/concepts/invalid")

        # Assert
        assert response.status_code == 422  # Validation error

    def test_get_concept_by_id_negative_id(self, client, mock_concept_service):
        """Test getting concept with negative ID."""
        # Arrange
        mock_concept = Concept(id=-1, name="Negative ID")
        mock_concept_service.get_by_id.return_value = mock_concept

        # Act
        response = client.get("/api/concepts/-1")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == -1

    def test_get_concept_by_id_service_error(self, client, mock_concept_service):
        """Test handling of service errors when getting concept by ID."""
        # Arrange
        mock_concept_service.get_by_id.side_effect = Exception("Concept not found")

        # Act & Assert
        with pytest.raises(Exception, match="Concept not found"):
            client.get("/api/concepts/999")

    def test_get_concept_by_id_not_found_response_validation(self, client, mock_concept_service):
        """Test response validation when service returns None (concept not found).

        This tests that FastAPI validates the response against the ConceptDto model.
        When the service returns None, FastAPI raises ResponseValidationError since
        None doesn't match the expected ConceptDto schema.
        """
        from fastapi.exceptions import ResponseValidationError

        # Arrange
        mock_concept_service.get_by_id.return_value = None

        # Act & Assert
        # FastAPI validates response model and raises ResponseValidationError
        # when None is returned for a required ConceptDto response
        with pytest.raises(ResponseValidationError):
            client.get("/api/concepts/999")


class TestConceptsIntegration:
    """Integration tests for concepts endpoints workflow."""

    def test_get_all_concepts_then_get_single(self, client, mock_concept_service):
        """Test getting all concepts then fetching a single one."""
        # Arrange
        all_concepts = [
            Concept(id=1, name="Metal"),
            Concept(id=2, name="Rock"),
            Concept(id=3, name="Jazz"),
        ]
        single_concept = Concept(id=2, name="Rock")

        mock_concept_service.get_all.return_value = all_concepts
        mock_concept_service.get_by_id.return_value = single_concept

        # Act
        all_response = client.get("/api/concepts")
        single_response = client.get("/api/concepts/2")

        # Assert
        assert all_response.status_code == 200
        assert single_response.status_code == 200
        all_data = all_response.json()
        single_data = single_response.json()
        assert len(all_data) == 3
        assert single_data["id"] == 2
        assert single_data["name"] == "Rock"

    def test_get_concept_then_get_top_images(self, client, mock_concept_service):
        """Test getting concept details then fetching its top images."""
        # Arrange
        concept = Concept(id=5, name="Programming")
        top_images = MemeSearchResponse(
            items=[
                Meme(
                    id="code-1",
                    imageUrl="/api/images/code-1",
                    text=["Code meme"],
                    tags=[],
                    originalFileName="code1.jpg"
                )
            ],
            nextCursor=None,
            hasNext=False,
            facets=[]
        )

        mock_concept_service.get_by_id.return_value = concept
        mock_concept_service.get_top_images.return_value = top_images

        # Act
        concept_response = client.get("/api/concepts/5")
        images_response = client.get("/api/concepts/top-images", params={"concept_id": 5})

        # Assert
        assert concept_response.status_code == 200
        assert images_response.status_code == 200
        concept_data = concept_response.json()
        images_data = images_response.json()
        assert concept_data["name"] == "Programming"
        assert len(images_data["items"]) == 1

    def test_get_concepts_for_image_then_get_each_concept(self, client, mock_concept_service):
        """Test getting concepts for an image then fetching each concept detail."""
        # Arrange
        image_concepts = [
            Concept(id=1, name="Metal"),
            Concept(id=2, name="Music"),
        ]
        concept1 = Concept(id=1, name="Metal")
        concept2 = Concept(id=2, name="Music")

        mock_concept_service.get_for_image.return_value = image_concepts
        mock_concept_service.get_by_id.side_effect = [concept1, concept2]

        # Act
        image_concepts_response = client.get("/api/concepts/for-image", params={"image_id": "img-1"})
        concept1_response = client.get("/api/concepts/1")
        concept2_response = client.get("/api/concepts/2")

        # Assert
        assert image_concepts_response.status_code == 200
        assert concept1_response.status_code == 200
        assert concept2_response.status_code == 200

        image_concepts_data = image_concepts_response.json()
        assert len(image_concepts_data) == 2
        assert concept1_response.json()["name"] == "Metal"
        assert concept2_response.json()["name"] == "Music"