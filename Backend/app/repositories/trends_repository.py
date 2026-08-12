import uuid
from datetime import date

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import BatchRun, RunStatus, TrendsRunResult


class TrendsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_available_dates(
        self,
        label: str | None = None,
        name: str | None = None,
    ) -> list[date]:
        date_expr = cast(BatchRun.created_at, Date)
        query = (
            select(date_expr)
            .select_from(BatchRun)
            .where(BatchRun.kind == "trends", BatchRun.status == str(RunStatus.completed))
            .distinct()
        )
        if label or name:
            query = query.join(TrendsRunResult, BatchRun.run_id == TrendsRunResult.run_id)
            if label:
                query = query.where(TrendsRunResult.label == label)
            if name:
                query = query.where(TrendsRunResult.name == name)
        query = query.order_by(date_expr.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_runs_for_date(self, run_date: date) -> list[BatchRun]:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == "trends", cast(BatchRun.created_at, Date) == run_date)
            .order_by(BatchRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_run_for_date(self, run_date: date) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(
                BatchRun.kind == "trends",
                BatchRun.status == str(RunStatus.completed),
                cast(BatchRun.created_at, Date) == run_date,
            )
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_entries_for_run(
        self,
        run_id: uuid.UUID,
        label: str | None = None,
        name: str | None = None,
        min_value: int = 10,
    ):
        value_sum = func.sum(TrendsRunResult.value)
        query = (
            select(TrendsRunResult.label, TrendsRunResult.name, value_sum.label("value"))
            .where(TrendsRunResult.run_id == run_id)
            .group_by(TrendsRunResult.label, TrendsRunResult.name)
            .having(value_sum >= min_value)
            .order_by(value_sum.desc())
        )
        if label:
            query = query.where(TrendsRunResult.label == label)
        if name:
            query = query.where(TrendsRunResult.name == name)
        result = await self._session.execute(query)
        return result.all()

    async def get_history(
        self,
        label: str | None = None,
        name: str | None = None,
        min_value: int = 10,
    ):
        date_expr = cast(BatchRun.created_at, Date)
        value_sum = func.sum(TrendsRunResult.value)
        query = (
            select(
                BatchRun.run_id,
                date_expr.label("date"),
                TrendsRunResult.label,
                TrendsRunResult.name,
                value_sum.label("value"),
            )
            .join(TrendsRunResult, BatchRun.run_id == TrendsRunResult.run_id)
            .where(BatchRun.kind == "trends")
            .group_by(BatchRun.run_id, date_expr, TrendsRunResult.label, TrendsRunResult.name)
            .having(value_sum >= min_value)
            .order_by(date_expr.desc(), value_sum.desc())
        )
        if label:
            query = query.where(TrendsRunResult.label == label)
        if name:
            query = query.where(TrendsRunResult.name == name)
        result = await self._session.execute(query)
        return result.all()