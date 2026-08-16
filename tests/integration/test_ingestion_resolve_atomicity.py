"""
Integration test for IngestionService.resolve()'s per-decision commit behavior -- proves a
later decision's DB failure cannot roll back an earlier decision that already committed. Unit
tests (Backend/tests/test_ingestion_service.py) prove the code *calls* commit/rollback in the
right order; this proves those calls actually protect data against PostgreSQL's own rollback
semantics, which a mocked repository can't verify.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py. Safe to
call session.commit()/rollback() here: db_session is bound with
join_transaction_mode="create_savepoint", so these become nested SAVEPOINTs inside the test's
own outer transaction, which is always rolled back at the end regardless (see conftest.py).
"""
import uuid
from unittest.mock import patch

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _make_image(session, status: str, batch_id) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_earlier_decision_stays_committed_after_a_later_db_failure(db_session):
    batch_id = await _make_run(db_session)
    first_id = await _make_image(db_session, "pending", batch_id)
    second_id = await _make_image(db_session, "pending", batch_id)

    service = IngestionService(IngestionRepository(db_session))

    async def flaky_mark_reviewed(image_id, tier):
        raise RuntimeError("simulated DB failure")

    service.repo.mark_reviewed = flaky_mark_reviewed
    decisions = [
        {"image_id": first_id, "decision": "reject"},
        {"image_id": second_id, "decision": "keep"},
    ]

    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        result = await service.resolve("tier_a", decisions)

    assert result["rejected"] == [str(first_id)]
    assert result["failed"] == [
        {"image_id": str(second_id), "decision": "keep", "error": "simulated DB failure"}
    ]

    first_image = await db_session.get(Image, first_id)
    await db_session.refresh(first_image)  # force a real re-read -- see module docstring
    assert first_image.status == "rejected"
