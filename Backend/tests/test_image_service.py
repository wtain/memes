"""
Unit tests for ImageService.get_similar, mocking ImageRepository directly.

Regression coverage for the description-mode dispatch branch: prior to this
file, no test exercised ImageService.get_similar's actual branching logic
(choosing get_similar_by_description vs get_similar, and the two distinct
404s) - test_images_endpoints.py mocks ImageService wholesale, and the
integration tests call ImageRepository directly, never through the service.
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from Backend.app.services.image_service import ImageService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo):
    return ImageService(mock_repo)


class TestGetSimilarImageMode:
    async def test_raises_404_when_no_embedding(self, service, mock_repo):
        mock_repo.get_embedding.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.get_similar("image-1", limit=10, source="image")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No embedding found for this image"
        mock_repo.get_similar.assert_not_called()
        mock_repo.get_similar_by_description.assert_not_called()

    async def test_happy_path_calls_repo_get_similar(self, service, mock_repo):
        embedding = MagicMock()
        embedding.tolist.return_value = [0.1, 0.2, 0.3]
        mock_repo.get_embedding.return_value = embedding
        mock_repo.get_similar.return_value = [
            ("image-2", 0.05, "second.png", False),
            ("image-3", 0.12, "third.png", True),
        ]

        result = await service.get_similar("image-1", limit=5, source="image")

        mock_repo.get_similar.assert_awaited_once_with("image-1", [0.1, 0.2, 0.3], limit=5)
        mock_repo.get_similar_by_description.assert_not_called()
        mock_repo.has_description_embedding.assert_not_called()

        assert [item.id for item in result.items] == ["image-2", "image-3"]
        assert [item.cosineDistance for item in result.items] == [0.05, 0.12]


class TestGetSimilarDescriptionMode:
    async def test_raises_404_when_no_description_embedding(self, service, mock_repo):
        mock_repo.has_description_embedding.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await service.get_similar("image-1", limit=10, source="description")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No description embedding found for this image"
        mock_repo.get_similar_by_description.assert_not_called()

    async def test_404_detail_differs_from_image_mode(self, service, mock_repo):
        mock_repo.has_description_embedding.return_value = False
        mock_repo.get_embedding.return_value = None

        with pytest.raises(HTTPException) as description_exc:
            await service.get_similar("image-1", limit=10, source="description")

        with pytest.raises(HTTPException) as image_exc:
            await service.get_similar("image-1", limit=10, source="image")

        assert description_exc.value.detail != image_exc.value.detail

    async def test_happy_path_calls_repo_get_similar_by_description(self, service, mock_repo):
        mock_repo.has_description_embedding.return_value = True
        mock_repo.get_similar_by_description.return_value = [
            ("image-4", 0.02, "fourth.png", False),
        ]

        result = await service.get_similar("image-1", limit=7, source="description")

        mock_repo.get_similar_by_description.assert_awaited_once_with("image-1", limit=7)
        mock_repo.get_similar.assert_not_called()
        mock_repo.get_embedding.assert_not_called()

        assert [item.id for item in result.items] == ["image-4"]
        assert [item.cosineDistance for item in result.items] == [0.02]


class TestGetDescriptionsFeedback:
    async def test_feedback_is_none_when_no_row(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), None),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback is None

    async def test_feedback_approved_maps_to_string(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), True),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback == "approved"

    async def test_feedback_rejected_maps_to_string(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), False),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback == "rejected"


class TestApproveDescriptionFeedback:
    async def test_raises_404_when_description_missing(self, service, mock_repo):
        mock_repo.get_description_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.approve_description_feedback("image-1", "unknown_prompt")

        assert exc_info.value.status_code == 404
        mock_repo.set_description_feedback.assert_not_called()

    async def test_approve_when_no_prior_feedback_sets_approved(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = None

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", True)
        mock_repo.clear_description_feedback.assert_not_called()
        assert result == "approved"

    async def test_approve_when_already_approved_clears(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = True

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.clear_description_feedback.assert_awaited_once_with("desc-uuid-1")
        mock_repo.set_description_feedback.assert_not_called()
        assert result is None

    async def test_approve_when_currently_rejected_switches(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = False

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", True)
        assert result == "approved"


class TestRejectDescriptionFeedback:
    async def test_raises_404_when_description_missing(self, service, mock_repo):
        mock_repo.get_description_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.reject_description_feedback("image-1", "unknown_prompt")

        assert exc_info.value.status_code == 404

    async def test_reject_when_no_prior_feedback_sets_rejected(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = None

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", False)
        assert result == "rejected"

    async def test_reject_when_already_rejected_clears(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = False

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.clear_description_feedback.assert_awaited_once_with("desc-uuid-1")
        mock_repo.set_description_feedback.assert_not_called()
        assert result is None

    async def test_reject_when_currently_approved_switches(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = True

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", False)
        assert result == "rejected"
