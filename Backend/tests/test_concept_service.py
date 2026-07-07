"""
Unit tests for ConceptService, mocking ConceptRepository directly.

Regression coverage for the /concepts/for-image 500: an image with no row in
the embeddings table must produce a 404 (matching ImageService.get_similar's
handling of the same condition), not an unhandled NoResultFound.
"""
import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

from Backend.app.services.concept_service import ConceptService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo):
    return ConceptService(mock_repo)


class TestGetForImage:
    async def test_raises_404_when_image_has_no_embedding(self, service, mock_repo):
        mock_repo.top_concepts_for_image.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.get_for_image("no-embedding-image")

        assert exc_info.value.status_code == 404

    async def test_returns_empty_list_when_no_concepts_match(self, service, mock_repo):
        mock_repo.top_concepts_for_image.return_value = []

        result = await service.get_for_image("some-image")

        assert result == []

    async def test_returns_concepts_when_found(self, service, mock_repo):
        mock_repo.top_concepts_for_image.return_value = [
            ("Metal Music", 1, 0.1),
            ("Rock Music", 2, 0.15),
        ]

        result = await service.get_for_image("some-image")

        assert [(c.id, c.name) for c in result] == [(1, "Metal Music"), (2, "Rock Music")]
