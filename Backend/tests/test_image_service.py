"""
Unit tests for ImageService.get_similar, mocking ImageRepository directly.

Regression coverage for the description-mode dispatch branch: prior to this
file, no test exercised ImageService.get_similar's actual branching logic
(choosing get_similar_by_description vs get_similar, and the two distinct
404s) - test_images_endpoints.py mocks ImageService wholesale, and the
integration tests call ImageRepository directly, never through the service.
"""
import uuid
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


class TestGetDuplicatesClusteredPagination:
    """
    Regression coverage for the compound-cursor fix: pagination for
    /api/images/duplicates used to cursor on cluster_id alone. When a
    cluster's members straddled the page's `limit` row cutoff, the
    remaining members were permanently skipped - the next page's filter
    (cluster_id > last_seen_cluster_id) excludes same-cluster rows outright.

    Here cluster 1 has 3 members (A, B, C) and cluster 2 has 1 member (D),
    with limit=2 so cluster 1 alone straddles the boundary. The fake repo
    below mirrors the real ImageRepository.get_duplicates_clustered's
    keyset filter (`tuple_(cluster_id, image_id) > cursor`) so the test
    exercises the same cursor semantics the service now relies on.
    """

    IMAGE_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
    IMAGE_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
    IMAGE_C = uuid.UUID("00000000-0000-0000-0000-000000000003")
    IMAGE_D = uuid.UUID("00000000-0000-0000-0000-000000000004")

    @staticmethod
    def _make_fake_rows():
        now = datetime(2026, 8, 10, 12, 0, 0)
        return [
            (TestGetDuplicatesClusteredPagination.IMAGE_A, "a.jpg", now, 1, False),
            (TestGetDuplicatesClusteredPagination.IMAGE_B, "b.jpg", now, 1, False),
            (TestGetDuplicatesClusteredPagination.IMAGE_C, "c.jpg", now, 1, False),
            (TestGetDuplicatesClusteredPagination.IMAGE_D, "d.jpg", now, 2, False),
        ]

    def _wire_fake_repo(self, mock_repo):
        full_rows = self._make_fake_rows()

        async def fake_get_duplicates_clustered(cursor_cluster_id, cursor_image_id, limit):
            if cursor_cluster_id is not None and cursor_image_id is not None:
                cursor = (cursor_cluster_id, cursor_image_id)
                rows = [r for r in full_rows if (r[3], r[0]) > cursor]
            else:
                rows = list(full_rows)
            return rows[: limit + 1]

        mock_repo.get_duplicates_clustered.side_effect = fake_get_duplicates_clustered
        mock_repo.get_texts.return_value = []
        mock_repo.get_tags.return_value = []

    async def test_split_cluster_members_all_appear_exactly_once_across_pages(self, service, mock_repo):
        self._wire_fake_repo(mock_repo)

        page1 = await service.get_duplicates_clustered(cursor=None, limit=2, threshold=0.2)
        assert [item.id for item in page1.items] == [str(self.IMAGE_A), str(self.IMAGE_B)]
        assert page1.hasNext is True
        assert page1.nextCursor is not None

        page2 = await service.get_duplicates_clustered(cursor=page1.nextCursor, limit=2, threshold=0.2)

        all_ids = [item.id for item in page1.items] + [item.id for item in page2.items]

        # Nothing dropped, nothing duplicated: cluster 1's 3 members (A, B, C)
        # plus cluster 2's 1 member (D), each exactly once.
        assert sorted(all_ids) == sorted(
            [str(self.IMAGE_A), str(self.IMAGE_B), str(self.IMAGE_C), str(self.IMAGE_D)]
        )
        assert len(all_ids) == len(set(all_ids))

        # Specifically: C (the tail of the split cluster) must not have been
        # skipped - this is the exact row the old cluster_id-only cursor lost.
        assert str(self.IMAGE_C) in all_ids
        assert page2.hasNext is False

    async def test_no_cursor_and_malformed_cursor_both_start_from_beginning(self, service, mock_repo):
        self._wire_fake_repo(mock_repo)

        result_no_cursor = await service.get_duplicates_clustered(cursor=None, limit=2, threshold=0.2)
        result_bad_cursor = await service.get_duplicates_clustered(cursor="not-a-valid-cursor", limit=2, threshold=0.2)

        assert [item.id for item in result_no_cursor.items] == [item.id for item in result_bad_cursor.items]
