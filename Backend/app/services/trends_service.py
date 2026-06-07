import uuid
from datetime import date

from fastapi import HTTPException

from Backend.app.repositories.trends_repository import TrendsRepository
from Backend.app.types.generated.trendentry import Schema as TrendEntry
from Backend.app.types.generated.trendhistoryentry import Schema as TrendHistoryEntry
from Backend.app.types.generated.trendsrun import Schema as TrendsRunDto


class TrendsService:
    def __init__(self, repo: TrendsRepository) -> None:
        self._repo = repo

    async def get_available_dates(
        self,
        label: str | None = None,
        name: str | None = None,
    ) -> list[str]:
        dates = await self._repo.get_available_dates(label, name)
        return [d.isoformat() for d in dates]

    async def get_runs_for_date(self, run_date: date) -> list[TrendsRunDto]:
        runs = await self._repo.get_runs_for_date(run_date)
        return [_to_run_dto(r) for r in runs]

    async def get_latest_run_for_date(self, run_date: date) -> TrendsRunDto:
        run = await self._repo.get_latest_run_for_date(run_date)
        if run is None:
            raise HTTPException(status_code=404, detail="No run found for the given date")
        return _to_run_dto(run)

    async def get_entries_for_run(
        self,
        run_id: uuid.UUID,
        label: str | None = None,
        name: str | None = None,
        min_value: int = 10,
    ) -> list[TrendEntry]:
        rows = await self._repo.get_entries_for_run(run_id, label, name, min_value)
        return [TrendEntry(label=row.label, name=row.name, value=row.value) for row in rows]

    async def get_history(
        self,
        label: str | None = None,
        name: str | None = None,
        min_value: int = 10,
    ) -> list[TrendHistoryEntry]:
        if label is None and name is None:
            raise HTTPException(
                status_code=422,
                detail="At least one of 'label' or 'name' must be specified",
            )
        rows = await self._repo.get_history(label, name, min_value)
        return [
            TrendHistoryEntry(
                runId=str(row.run_id),
                date=row.date.isoformat(),
                label=row.label,
                name=row.name,
                value=row.value,
            )
            for row in rows
        ]


def _to_run_dto(run) -> TrendsRunDto:
    return TrendsRunDto(
        runId=str(run.run_id),
        createdAt=run.created_at.isoformat(),
        status=run.status,
    )