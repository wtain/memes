from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from models import Base

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

DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

SessionLocal = sessionmaker(engine)

async def init_db():
    async with engine.begin() as conn:
        #  await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
