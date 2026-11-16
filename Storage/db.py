from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import logging

from config.settings import settings
from config.stage import is_prod
from Storage.config import DATABASE_URL
from Storage.models import Base

logger = logging.getLogger(__name__)

# Pool bounds come from tracked settings (database.pool_size / database.max_overflow; defaults 10 and 20, the values this module always
# had). Storage.config already imports `settings`, so reading it here adds no new import-order constraint: the object is built once at
# import with whatever APP_ENV the process has (batch scripts that import Storage.db before load_env() get the common-file values, which
# are the same for every developer environment). The prod overlay lowers both.
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 20


def _pool_setting(key: str, default: int) -> int:
    raw = settings.get(f"database.{key}", default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or (key == "pool_size" and raw < 1):
        raise RuntimeError(f"database.{key} must be a positive integer, got {raw!r}" if key == "pool_size"
                           else f"database.{key} must be an integer >= 0, got {raw!r}")
    return raw


POOL_SIZE = _pool_setting("pool_size", DEFAULT_POOL_SIZE)
MAX_OVERFLOW = _pool_setting("max_overflow", DEFAULT_MAX_OVERFLOW)


def max_connections() -> int:
    """Most connections this process can hold open: pool_size + max_overflow."""
    return POOL_SIZE + MAX_OVERFLOW


def log_pool_bounds() -> None:
    """One startup line (no secrets) with the computed connection ceiling of this process; the backend lifespan calls it on prod."""
    logger.info("database pool: pool_size=%d max_overflow=%d max_connections_per_process=%d stage=%s",
                POOL_SIZE, MAX_OVERFLOW, max_connections(), "prod" if is_prod() else "dev")

async def get_async_db():
    async with AsyncSessionLocal() as db:
        try:
            yield db
            # If we get here without exception, commit any changes
            await db.commit()
        except Exception:
            # If there's an exception, rollback
            await db.rollback()
            raise
        # Session automatically closes when exiting the async with block


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

SessionLocal = sessionmaker(engine)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
