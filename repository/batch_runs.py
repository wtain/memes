import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import BatchRun, RunStatus


class BatchAlreadyRunningError(Exception):
    """Raised by create_run() when the one-active-per-kind partial unique index rejects a
    concurrent duplicate -- there is already a 'started' BatchRun row for this kind."""


class BatchRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, kind: str, trigger: str, stage: str | None = None) -> uuid.UUID:
        run = BatchRun(kind=kind, trigger=trigger, status=str(RunStatus.started), stage=stage)
        self._session.add(run)
        try:
            await self._session.flush()  # populates run_id without closing the transaction
        except IntegrityError as e:
            raise BatchAlreadyRunningError(kind) from e
        return run.run_id

    async def set_stage(self, run_id: uuid.UUID, stage: str) -> None:
        run = await self._get(run_id)
        run.stage = stage
        await self._session.flush()

    async def update_stats(self, run_id: uuid.UUID, **kwargs) -> None:
        run = await self._get(run_id)
        run.stats = {**(run.stats or {}), **kwargs}
        await self._session.flush()

    async def commit(self, run_id: uuid.UUID, stats: dict | None = None) -> None:
        run = await self._get(run_id)
        run.status = str(RunStatus.completed)
        run.completed_at = datetime.now(timezone.utc)
        if stats is not None:
            run.stats = {**(run.stats or {}), **stats}
        await self._session.flush()

    async def fail(self, run_id: uuid.UUID, error: str | None = None) -> None:
        run = await self._get(run_id)
        run.status = str(RunStatus.failed)
        run.completed_at = datetime.now(timezone.utc)
        if error is not None:
            run.error = error
        await self._session.flush()

    async def abort(self, run_id: uuid.UUID, note: str | None = None) -> None:
        run = await self._get(run_id)
        run.status = str(RunStatus.aborted)
        run.completed_at = datetime.now(timezone.utc)
        if note is not None:
            run.error = note
        await self._session.flush()

    async def get_run(self, run_id: uuid.UUID) -> BatchRun | None:
        return await self._get(run_id)

    async def get_active_run(self, kind: str) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == kind, BatchRun.status == str(RunStatus.started))
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_most_recent_run(self, kind: str) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == kind)
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_runs(self, kinds: list[str], limit: int, offset: int) -> tuple[list[BatchRun], int]:
        base_where = BatchRun.kind.in_(kinds)
        items_result = await self._session.execute(
            select(BatchRun).where(base_where)
            .order_by(BatchRun.created_at.desc())
            .limit(limit).offset(offset)
        )
        total_result = await self._session.execute(
            select(func.count()).select_from(BatchRun).where(base_where)
        )
        return list(items_result.scalars()), total_result.scalar_one()

    async def _get(self, run_id: uuid.UUID) -> BatchRun:
        result = await self._session.execute(
            select(BatchRun).where(BatchRun.run_id == run_id)
        )
        return result.scalar_one()
