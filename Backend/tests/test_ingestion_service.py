"""
Unit tests for IngestionService.resolve(), mocking IngestionRepository directly.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from Backend.app.services.ingestion_service import IngestionService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo):
    return IngestionService(mock_repo)


class TestResolveRejectSkipsNonPending:
    async def test_reject_on_non_pending_image_does_not_move_file(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = None  # not pending, per the repository's own guard

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_image_store.move_to_rejected.assert_not_called()
        assert result["rejected"] == []


class TestResolveCommitOrdering:
    async def test_reject_commits_before_move(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = "file.jpg"
        call_order = []
        mock_repo.commit.side_effect = lambda: call_order.append("commit")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = lambda f: call_order.append("move")
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        assert call_order == ["commit", "move"]
        assert result == {"rejected": [str(image_id)], "kept": [], "failed": [], "move_failed": []}

    async def test_keep_commits_after_mark_reviewed(self, service, mock_repo):
        image_id = uuid.uuid4()

        result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "keep"}])

        mock_repo.mark_reviewed.assert_awaited_once_with(image_id, "tier_a")
        mock_repo.commit.assert_awaited_once()
        assert result == {"rejected": [], "kept": [str(image_id)], "failed": [], "move_failed": []}


class TestResolveMoveFailure:
    async def test_move_failure_after_commit_lands_in_move_failed_not_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = "file.jpg"

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = OSError("file locked")
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_repo.rollback.assert_not_called()  # commit already happened -- nothing to roll back
        assert result["rejected"] == [str(image_id)]
        assert result["move_failed"] == [{"image_id": str(image_id), "error": "file locked"}]
        assert result["failed"] == []


class TestResolveDbFailure:
    async def test_reject_db_failure_rolls_back_and_records_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.side_effect = RuntimeError("db down")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_repo.rollback.assert_awaited_once()
        mock_image_store.move_to_rejected.assert_not_called()
        assert result["failed"] == [
            {"image_id": str(image_id), "decision": "reject", "error": "db down"}
        ]
        assert result["rejected"] == []

    async def test_keep_db_failure_rolls_back_and_records_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.mark_reviewed.side_effect = RuntimeError("db down")

        result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "keep"}])

        mock_repo.rollback.assert_awaited_once()
        assert result["failed"] == [
            {"image_id": str(image_id), "decision": "keep", "error": "db down"}
        ]
        assert result["kept"] == []


class TestResolvePartialBatch:
    async def test_one_failure_does_not_abort_remaining_decisions(self, service, mock_repo):
        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.reject_image.side_effect = ["first.jpg", RuntimeError("boom"), "third.jpg"]
        decisions = [
            {"image_id": id1, "decision": "reject"},
            {"image_id": id2, "decision": "reject"},
            {"image_id": id3, "decision": "reject"},
        ]

        with patch("Backend.app.services.ingestion_service.image_store"):
            result = await service.resolve("tier_a", decisions)

        assert result["rejected"] == [str(id1), str(id3)]
        assert result["failed"] == [{"image_id": str(id2), "decision": "reject", "error": "boom"}]
        assert mock_repo.commit.await_count == 2
        assert mock_repo.rollback.await_count == 1

    async def test_unknown_decision_value_still_raises_and_aborts(self, service, mock_repo):
        # Unreachable through the real API (Pydantic validates decision values before the
        # service ever runs) -- this documents that the existing hard-abort behavior for this
        # branch is intentionally unchanged.
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await service.resolve("tier_a", [{"image_id": uuid.uuid4(), "decision": "maybe"}])

    async def test_one_move_failure_does_not_abort_remaining_decisions(self, service, mock_repo):
        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.reject_image.side_effect = ["first.jpg", "second.jpg", "third.jpg"]
        decisions = [
            {"image_id": id1, "decision": "reject"},
            {"image_id": id2, "decision": "reject"},
            {"image_id": id3, "decision": "reject"},
        ]

        def flaky_move(filename):
            if filename == "second.jpg":
                raise OSError("file locked")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = flaky_move
            result = await service.resolve("tier_a", decisions)

        # All three are "rejected" -- the DB commit succeeded for every one of them, including
        # id2, whose only problem was its file move, not its database write.
        assert result["rejected"] == [str(id1), str(id2), str(id3)]
        assert result["move_failed"] == [{"image_id": str(id2), "error": "file locked"}]
        assert result["failed"] == []
        assert mock_repo.commit.await_count == 3
        assert mock_repo.rollback.await_count == 0


class TestListClustersPagination:
    """list_clusters builds the full union-find, sorts clusters by tightest edge, and returns
    one cursor-delimited page. mock_repo returns raw tmp_duplicates rows."""

    def _row(self, id1, id2, distance, status1="pending", status2="pending"):
        # matches get_tier_candidate_rows' tuple shape:
        # (image_id1, filename1, status1, image_id2, filename2, status2, distance, match_source)
        return (id1, f"{id1}.jpg", status1, id2, f"{id2}.jpg", status2, distance, "clip")

    async def _setup(self, service, mock_repo, rows):
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.get_tier_candidate_rows.return_value = rows
        mock_repo.get_ocr_texts.return_value = {}

    async def test_orders_clusters_by_tightest_edge_and_paginates(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        b1, b2 = "00000000-0000-0000-0000-0000000000b1", "00000000-0000-0000-0000-0000000000b2"
        c1, c2 = "00000000-0000-0000-0000-0000000000c1", "00000000-0000-0000-0000-0000000000c2"
        rows = [
            self._row(b1, b2, 0.20),
            self._row(a1, a2, 0.05),   # tightest -> first
            self._row(c1, c2, 0.30),
        ]
        await self._setup(service, mock_repo, rows)

        page1 = await service.list_clusters("tier_b", limit=2)
        assert [e["distance"] for c in page1["items"] for e in c["edges"]] == [0.05, 0.20]
        assert page1["has_next"] is True
        assert page1["next_cursor"] is not None
        assert "_sort_key" not in page1["items"][0]

        page2 = await service.list_clusters("tier_b", cursor=page1["next_cursor"], limit=2)
        assert [e["distance"] for c in page2["items"] for e in c["edges"]] == [0.30]
        assert page2["has_next"] is False
        assert page2["next_cursor"] is None

    async def test_blank_and_malformed_cursor_start_from_beginning(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        await self._setup(service, mock_repo, [self._row(a1, a2, 0.05)])
        for bad in ("", "   ", "not-a-cursor", "abc|def", "0.1|"):
            page = await service.list_clusters("tier_b", cursor=bad, limit=10)
            assert len(page["items"]) == 1

    async def test_cursor_preserves_distances_beyond_six_decimals(self, service, mock_repo):
        # Two clusters whose min_distance differ only ~9th decimal. A fixed-precision cursor
        # would collapse them and the strict ">" filter would drop the second one forever.
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        b1, b2 = "00000000-0000-0000-0000-0000000000b1", "00000000-0000-0000-0000-0000000000b2"
        rows = [
            self._row(a1, a2, 0.12345678),
            self._row(b1, b2, 0.12345679),
        ]
        await self._setup(service, mock_repo, rows)

        page1 = await service.list_clusters("tier_b", limit=1)
        assert [e["distance"] for c in page1["items"] for e in c["edges"]] == [0.12345678]
        assert page1["has_next"] is True

        page2 = await service.list_clusters("tier_b", cursor=page1["next_cursor"], limit=1)
        assert [e["distance"] for c in page2["items"] for e in c["edges"]] == [0.12345679]
        assert page2["has_next"] is False
        assert page2["next_cursor"] is None

    async def test_exactly_limit_remaining_has_no_next(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        b1, b2 = "00000000-0000-0000-0000-0000000000b1", "00000000-0000-0000-0000-0000000000b2"
        c1, c2 = "00000000-0000-0000-0000-0000000000c1", "00000000-0000-0000-0000-0000000000c2"
        rows = [
            self._row(a1, a2, 0.05),
            self._row(b1, b2, 0.20),
            self._row(c1, c2, 0.30),
        ]
        await self._setup(service, mock_repo, rows)

        # First page consumes one cluster; exactly `limit` (2) remain after the cursor filter.
        page1 = await service.list_clusters("tier_b", limit=1)
        page2 = await service.list_clusters("tier_b", cursor=page1["next_cursor"], limit=2)
        assert [e["distance"] for c in page2["items"] for e in c["edges"]] == [0.20, 0.30]
        assert page2["has_next"] is False
        assert page2["next_cursor"] is None

    async def test_empty_queue(self, service, mock_repo):
        await self._setup(service, mock_repo, [])
        page = await service.list_clusters("tier_b", limit=10)
        assert page == {"items": [], "next_cursor": None, "has_next": False}

    async def test_passes_ocr_thresholds_from_settings(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        await self._setup(service, mock_repo, [self._row(a1, a2, 0.05)])
        await service.list_clusters("tier_b", limit=10)
        _, kwargs = mock_repo.get_ocr_texts.call_args
        args = mock_repo.get_ocr_texts.call_args.args
        # confidence_min, lang_score_min passed positionally after image_ids
        assert args[1:] == (0.4, 0.3) or (kwargs.get("confidence_min"), kwargs.get("lang_score_min")) == (0.4, 0.3)


class TestUndoRejectAfterMoveFailure:
    async def test_undo_reject_is_safe_after_a_move_failure(self, service, mock_repo):
        # A move_failed reject leaves the DB durably "rejected" but the file never actually
        # moved to rejected/ -- undo_reject()'s own precondition (status == "rejected") already
        # matches that state, and image_store.move_from_rejected() is a no-op when the file
        # isn't where it expects it, so undoing is safe without any change to undo_reject()
        # itself. This test lets the real image_store.move_from_rejected run (not mocked) to
        # prove that against Backend/tests/conftest.py's BASE_PATH=/tmp/test_images, where the
        # file in question was never actually created.
        image_id = uuid.uuid4()
        mock_repo.undo_reject.return_value = "file-that-was-never-moved.jpg"

        result = await service.undo_reject(image_id)

        assert result == {"image_id": str(image_id), "status": "pending"}
        mock_repo.undo_reject.assert_awaited_once_with(image_id)
