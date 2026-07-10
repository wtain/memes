"""
Integration tests for Backend/app/repositories/diagnostics_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Storage.models import Embedding, Image, ImageExtras, ImageTag, OCRText


@pytest.mark.asyncio(loop_scope="session")
async def test_check_database_returns_true_when_reachable(db_session):
    repo = DiagnosticsRepository(db_session)
    assert await repo.check_database() is True


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_seeded_rows(db_session):
    tagged = Image(filename=f"{uuid.uuid4()}.jpg")
    untagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([tagged, untagged])
    await db_session.flush()

    before = await DiagnosticsRepository(db_session).get_statistics()

    db_session.add_all([
        Embedding(image_id=tagged.id, embedding=[0.0] * 512),
        OCRText(image_id=tagged.id, text="hello", confidence=0.9),
        ImageTag(image_id=tagged.id, key="animal", value="cat", source="rules"),
        ImageExtras(image_id=tagged.id, flagged=True),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()

    assert after.total_memes == before.total_memes
    assert after.with_embeddings == before.with_embeddings + 1
    assert after.with_ocr == before.with_ocr + 1
    assert after.with_tags == before.with_tags + 1
    assert after.without_tags == before.without_tags - 1  # tagged image no longer counted
    assert after.flagged == before.flagged + 1
    assert after.ocr_texts == before.ocr_texts + 1
    assert after.tags == before.tags + 1
