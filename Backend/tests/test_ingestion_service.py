"""
Unit tests for IngestionService.resolve(), mocking IngestionRepository directly.
"""
import uuid
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
