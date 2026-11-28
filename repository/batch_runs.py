import uuid
from datetime import datetime, timezone

from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

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

    async def set_progress(self, run_id: uuid.UUID, value: dict) -> bool:
        """Replace the live progress of a started run in one UPDATE (no read-modify-write).

        The server adds ``updated_at``. Returns True when a started run was updated, False
        otherwise (unknown or finished run). Does not commit.
        """
        stmt = (
            update(BatchRun)
            .where(BatchRun.run_id == run_id, BatchRun.status == str(RunStatus.started))
            .values(
                progress=cast(value, JSONB).op("||")(
                    func.jsonb_build_object("updated_at", func.now())
                )
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

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

    async def fail_started_of_kinds(self, kinds: list[str], since: datetime, error: str) -> int:
        """Fails every still-started run of these kinds created at or after `since`; returns how many.
        For the steps of a driver whose process died: they ran inside it, so they are gone too. Does not commit."""
        stmt = (
            update(BatchRun)
            .where(BatchRun.kind.in_(kinds), BatchRun.status == str(RunStatus.started), BatchRun.created_at >= since)
            .values(status=str(RunStatus.failed), completed_at=datetime.now(timezone.utc), error=error)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def abort(self, run_id: uuid.UUID, note: str | None = None) -> None:
        run = await self._get(run_id)
        run.status = str(RunStatus.aborted)
        run.completed_at = datetime.now(timezone.utc)
        if note is not None:
            run.error = note
        await self._session.flush()

    async def get_run(self, run_id: uuid.UUID, *, with_progress: bool = True) -> BatchRun | None:
        """``with_progress`` loads the deferred ``progress`` column (the Admin API needs it);
        pass False on a tracking path that must work before the column is migrated."""
        stmt = select(BatchRun).where(BatchRun.run_id == run_id)
        if with_progress:
            stmt = stmt.options(undefer(BatchRun.progress))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

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

    async def get_latest_completed_run(self, kind: str) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == kind, BatchRun.status == str(RunStatus.completed))
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_runs(self, kinds: list[str], limit: int, offset: int,
                         status: str | None = None) -> tuple[list[BatchRun], int]:
        """`status` is the stored value (`started`, `completed`, `failed`, `aborted`); None means every status."""
        base_where = BatchRun.kind.in_(kinds)
        if status is not None:
            base_where = base_where & (BatchRun.status == status)
        items_result = await self._session.execute(
            select(BatchRun).where(base_where)
            .options(undefer(BatchRun.progress))
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
