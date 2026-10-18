from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from Storage.db import get_async_db
from repository.batch_runs import BatchRunRepository
from Backend.app.services.admin_batch_service import AdminBatchService
from Backend.app.services.business_metrics_service import BusinessMetricsService, UnknownProcessError

router = APIRouter(prefix="/admin/batches", tags=["admin"])


class RunTriggerResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    batch_name: str
    trigger: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]


class RunListResponse(BaseModel):
    items: list[RunStatusResponse]
    total: int


class BatchNamesResponse(BaseModel):
    names: list[str]


class BatchMetricsBucket(BaseModel):
    counts: dict[str, int]
    metrics: dict[str, Optional[float]]


class BatchMetricsItem(BatchMetricsBucket):
    run_id: str
    run_created_at: datetime


class BatchMetricsResponse(BaseModel):
    process: str
    items: list[BatchMetricsItem]
    unattributed: BatchMetricsBucket


async def get_admin_batch_service(
    db=Depends(get_async_db),
) -> AsyncGenerator[AdminBatchService, None]:
    yield AdminBatchService(BatchRunRepository(db), db)


async def get_business_metrics_service(
    db=Depends(get_async_db),
) -> AsyncGenerator[BusinessMetricsService, None]:
    yield BusinessMetricsService(db)


@router.get("/names", response_model=BatchNamesResponse)
async def list_batch_names(service: AdminBatchService = Depends(get_admin_batch_service)):
    return await service.list_batch_names()


@router.post("/{batch_name}/run", response_model=RunTriggerResponse)
async def trigger_run(batch_name: str, service: AdminBatchService = Depends(get_admin_batch_service)):
    return await service.trigger_run(batch_name)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: UUID, service: AdminBatchService = Depends(get_admin_batch_service)):
    return await service.get_run(run_id)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = 50, offset: int = 0,
    service: AdminBatchService = Depends(get_admin_batch_service),
):
    return await service.list_runs(limit=limit, offset=offset)


@router.get("/metrics", response_model=BatchMetricsResponse)
async def batch_metrics(
    process: str,
    limit: int = Query(50, ge=1, le=200),
    service: BusinessMetricsService = Depends(get_business_metrics_service),
):
    try:
        return await service.batch_metrics(process, limit=limit)
    except UnknownProcessError:
        raise HTTPException(status_code=404, detail=f"Unknown process: {process}")
