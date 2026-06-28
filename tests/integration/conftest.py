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
from sqlalchemy.orm import sessionmaker

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
    """Yields a session that is rolled back after each test."""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()
