"""
Integration test fixtures — require a real PostgreSQL instance with pgvector.

Set DATABASE_URL to a test database before running:
    pytest tests/integration/ --co  # confirm collection
    pytest tests/integration/       # run (needs live DB)

The CI workflow (.github/workflows/integration-tests.yml) spins up
pgvector/pgvector:pg16 as a service and sets DATABASE_URL automatically.
"""
import os
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Ensure repo root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

os.environ.setdefault("BASE_PATH", "/tmp/test_images")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test",
)

from Storage.db import AsyncSessionLocal  # noqa: E402 — env must be set first
from Storage.models import Base  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine():
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine):
    """Yields a session wrapped in an outer transaction that is always rolled
    back after each test, isolating tests from each other -- same guarantee
    as before. Bound via join_transaction_mode="create_savepoint" (SQLAlchemy
    2.0+) so that code under test which calls session.commit() (e.g.
    repository classes that manage their own commit timing, like
    OCRLemmasSaver/OCRLemmasRepository.delete_all()) only commits an inner
    SAVEPOINT -- invisible to the test, which keeps using this same session
    normally before and after -- rather than ending the outer transaction the
    way the previous plain session.begin() wrapping did."""
    async with db_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()
