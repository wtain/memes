import uuid
from datetime import date

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import TrendsRun, TrendsRunResult


class TrendsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_available_dates(
        self,
        label: str | None = None,
        name: str | None = None,
    ) -> list[date]:
        date_expr = cast(TrendsRun.created_at, Date)
        query = select(date_expr).distinct()
        if label or name:
            query = query.join(TrendsRunResult, TrendsRun.run_id == TrendsRunResult.run_id)
            if label:
                query = query.where(TrendsRunResult.label == label)
            if name:
                query = query.where(TrendsRunResult.name == name)
        query = query.order_by(date_expr.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_runs_for_date(self, run_date: date) -> list[TrendsRun]:
        result = await self._session.execute(
            select(TrendsRun)
            .where(cast(TrendsRun.created_at, Date) == run_date)
            .order_by(TrendsRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_run_for_date(self, run_date: date) -> TrendsRun | None:
        result = await self._session.execute(
            select(TrendsRun)
            .where(cast(TrendsRun.created_at, Date) == run_date)
            .order_by(TrendsRun.created_at.desc())
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
        date_expr = cast(TrendsRun.created_at, Date)
        value_sum = func.sum(TrendsRunResult.value)
        query = (
            select(
                TrendsRun.run_id,
                date_expr.label("date"),
                TrendsRunResult.label,
                TrendsRunResult.name,
                value_sum.label("value"),
            )
            .join(TrendsRunResult, TrendsRun.run_id == TrendsRunResult.run_id)
            .group_by(TrendsRun.run_id, date_expr, TrendsRunResult.label, TrendsRunResult.name)
            .having(value_sum >= min_value)
            .order_by(date_expr.desc(), value_sum.desc())
        )
        if label:
            query = query.where(TrendsRunResult.label == label)
        if name:
            query = query.where(TrendsRunResult.name == name)
        result = await self._session.execute(query)
        return result.all()