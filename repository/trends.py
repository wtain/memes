import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import FeedSource, RunStatus, TrendsRun, TrendsRunResult


class FeedSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[FeedSource]:
        result = await self._session.execute(select(FeedSource))
        return list(result.scalars().all())


class TrendsRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self) -> uuid.UUID:
        run = TrendsRun(status=str(RunStatus.started))
        self._session.add(run)
        await self._session.flush()  # populates run_id without closing the transaction
        return run.run_id

    async def commit(self, run_id: uuid.UUID) -> None:
        await self._set_status(run_id, RunStatus.completed)

    async def fail(self, run_id: uuid.UUID) -> None:
        await self._set_status(run_id, RunStatus.failed)

    async def _set_status(self, run_id: uuid.UUID, status: RunStatus) -> None:
        result = await self._session.execute(
            select(TrendsRun).where(TrendsRun.run_id == run_id)
        )
        run = result.scalar_one()
        run.status = str(status)
        await self._session.flush()


class TrendsRunResultRepository:
    def __init__(self, session: AsyncSession, run_id: uuid.UUID) -> None:
        self._session = session
        self._run_id = run_id

    async def add_result(self, source_id: int, label: str, name: str, value: int) -> TrendsRunResult:
        result = TrendsRunResult(
            run_id=self._run_id,
            source_id=source_id,
            label=label,
            name=name,
            value=value,
        )
        self._session.add(result)
        await self._session.flush()
        return result