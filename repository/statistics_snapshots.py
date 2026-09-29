from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import StatisticsSnapshot


class StatisticsSnapshotsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, name: str) -> StatisticsSnapshot | None:
        # populate_existing: upsert() writes with a Core statement, which bypasses the ORM
        # identity map -- without this a row loaded earlier in the same session would be
        # returned stale after an overwrite.
        result = await self._session.execute(
            select(StatisticsSnapshot)
            .where(StatisticsSnapshot.name == name)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def upsert(self, name: str, payload: dict, computed_at: datetime, duration_ms: int) -> None:
        stmt = insert(StatisticsSnapshot).values(
            name=name, payload=payload, computed_at=computed_at, duration_ms=duration_ms,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "payload": stmt.excluded.payload,
                "computed_at": stmt.excluded.computed_at,
                "duration_ms": stmt.excluded.duration_ms,
            },
        )
        await self._session.execute(stmt)
