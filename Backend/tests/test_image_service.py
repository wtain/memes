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
from unittest.mock import AsyncMock
from fastapi import HTTPException

from Backend.app.services.image_service import ImageService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def mock_decision_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo, mock_decision_repo):
    return ImageService(mock_repo, mock_decision_repo)


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
        # A real list[float], not a MagicMock -- pgvector's SQLAlchemy VECTOR type
        # (result_processor in pgvector/sqlalchemy/vector.py) returns embeddings as
        # plain list[float] as of pgvector 0.5.0, not a numpy array. A MagicMock with
        # a stubbed .tolist() previously masked a real AttributeError here (repo
        # get_embedding() -- no .tolist() attribute on a plain list).
        embedding = [0.1, 0.2, 0.3]
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

    def _wire_fake_repo_backward(self, mock_repo):
        full_rows = self._make_fake_rows()  # A,B,C in cluster 1; D in cluster 2

        async def fake_get_duplicates_clustered_before(cursor_cluster_id, cursor_image_id, limit):
            if cursor_cluster_id is not None and cursor_image_id is not None:
                cursor = (cursor_cluster_id, cursor_image_id)
                rows = [r for r in full_rows if (r[3], r[0]) < cursor]
            else:
                rows = list(full_rows)
            rows_desc = sorted(rows, key=lambda r: (r[3], r[0]), reverse=True)
            return rows_desc[: limit + 1]

        mock_repo.get_duplicates_clustered_before.side_effect = fake_get_duplicates_clustered_before
        mock_repo.get_texts.return_value = []
        mock_repo.get_tags.return_value = []

    async def test_backward_fetch_returns_items_before_cursor_in_ascending_order(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(2, self.IMAGE_D)  # anchor: right after D

        page = await service.get_duplicates_clustered(cursor=cursor, limit=2, threshold=0.2, direction="backward")

        # limit=2 takes the 2 rows immediately before D in descending order (C, B),
        # reversed back to ascending for the response.
        assert [item.id for item in page.items] == [str(self.IMAGE_B), str(self.IMAGE_C)]
        # Forward continuation from this backward page must be the cursor we anchored on.
        assert page.nextCursor == cursor
        assert page.hasNext is True
        # One more row (A) exists before this page, so previousCursor must be set.
        assert page.previousCursor is not None

    async def test_backward_fetch_at_true_beginning_has_no_previous_cursor(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(1, self.IMAGE_B)  # anchor: right after B

        page = await service.get_duplicates_clustered(cursor=cursor, limit=5, threshold=0.2, direction="backward")

        assert [item.id for item in page.items] == [str(self.IMAGE_A)]
        assert page.previousCursor is None  # nothing before A

    async def test_backward_fetch_with_no_earlier_items_returns_empty(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(1, self.IMAGE_A)  # anchor: right before A, nothing earlier

        page = await service.get_duplicates_clustered(cursor=cursor, limit=5, threshold=0.2, direction="backward")

        assert page.items == []
        assert page.previousCursor is None

    async def test_backward_fetch_with_no_cursor_has_no_next_cursor(self, service, mock_repo):
        # direction="backward" with cursor=None has no real anchor to resume
        # forward from - hasNext=True with nextCursor=None would let a caller
        # that trusts hasNext loop on the same page forever.
        self._wire_fake_repo_backward(mock_repo)

        page = await service.get_duplicates_clustered(cursor=None, limit=5, threshold=0.2, direction="backward")

        assert page.hasNext is False
        assert page.nextCursor is None
        # With no real anchor, there's nothing that exists "before the start" -- the response
        # must be empty, matching backend_api.md's documented contract. The fake repo above
        # would happily return the tail of the corpus if the service still queried it with no
        # cursor filter, so this also guards against that regression.
        assert page.items == []
        mock_repo.get_duplicates_clustered_before.assert_not_called()


class TestDismissCluster:
    async def test_dismiss_generates_all_pairs_for_current_members(self, service, mock_repo, mock_decision_repo):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b, c]

        pairs = await service.dismiss_cluster(141)

        mock_repo.get_cluster_member_ids.assert_awaited_once_with(141)
        assert sorted(pairs) == sorted([(a, b), (a, c), (b, c)])
        mock_decision_repo.record_decisions_bulk.assert_awaited_once()
        recorded = mock_decision_repo.record_decisions_bulk.call_args.args[0]
        assert sorted(recorded) == sorted(pairs)

    async def test_dismiss_raises_404_for_unknown_cluster(self, service, mock_repo):
        mock_repo.get_cluster_member_ids.return_value = []

        with pytest.raises(HTTPException) as exc_info:
            await service.dismiss_cluster(999)

        assert exc_info.value.status_code == 404


class TestUndoDismiss:
    async def test_undo_delegates_to_repository(self, service, mock_decision_repo):
        pairs = [(uuid.uuid4(), uuid.uuid4())]

        await service.undo_dismiss(pairs)

        mock_decision_repo.delete_decisions.assert_awaited_once_with(pairs)


class TestListDuplicateDecisions:
    async def test_list_delegates_to_repository(self, service, mock_decision_repo):
        mock_decision_repo.list_recent.return_value = ([], 0)

        result = await service.list_duplicate_decisions(limit=10, offset=5)

        mock_decision_repo.list_recent.assert_awaited_once_with(10, 5)
        assert result == ([], 0)
