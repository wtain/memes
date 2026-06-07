import uuid
from datetime import date
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Response

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.trends_repository import TrendsRepository
from Backend.app.services.trends_service import TrendsService
from Backend.app.types.generated.trendentry import Schema as TrendEntry
from Backend.app.types.generated.trendhistoryentry import Schema as TrendHistoryEntry
from Backend.app.types.generated.trendsrun import Schema as TrendsRunDto

router = APIRouter(prefix="/trends", tags=["trends"])


async def get_trends_service(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[TrendsService, None]:
    service = TrendsService(TrendsRepository(db))
    try:
        yield service
    finally:
        pass


@router.get("/dates", response_model=list[str])
async def get_available_dates(
    response: Response,
    label: str | None = None,
    name: str | None = None,
    service: TrendsService = Depends(get_trends_service),
):
    return await service.get_available_dates(label, name)


@router.get("/dates/{run_date}/runs", response_model=list[TrendsRunDto])
async def get_runs_for_date(
    response: Response,
    run_date: date,
    service: TrendsService = Depends(get_trends_service),
):
    return await service.get_runs_for_date(run_date)


@router.get("/dates/{run_date}/runs/latest", response_model=TrendsRunDto)
async def get_latest_run_for_date(
    response: Response,
    run_date: date,
    service: TrendsService = Depends(get_trends_service),
):
    return await service.get_latest_run_for_date(run_date)


@router.get("/runs/{run_id}", response_model=list[TrendEntry])
async def get_entries_for_run(
    response: Response,
    run_id: uuid.UUID,
    label: str | None = None,
    name: str | None = None,
    min_value: int = 10,
    service: TrendsService = Depends(get_trends_service),
):
    return await service.get_entries_for_run(run_id, label, name, min_value)


@router.get("/history", response_model=list[TrendHistoryEntry])
async def get_history(
    response: Response,
    label: str | None = None,
    name: str | None = None,
    min_value: int = 10,
    service: TrendsService = Depends(get_trends_service),
):
    return await service.get_history(label, name, min_value)