from typing import AsyncGenerator, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from Backend.app.stage_policy import make_router

from Backend.app.types.generated.batchmetricsresponse import Schema as BatchMetricsResponse
from Backend.app.types.generated.batchnamesresponse import Schema as BatchNamesResponse
from Backend.app.types.generated.runlistresponse import Schema as RunListResponse
from Backend.app.types.generated.runstatusresponse import Schema as RunStatusResponse
from Backend.app.types.generated.runtriggerresponse import Schema as RunTriggerResponse
from Storage.db import get_async_db
from repository.batch_runs import BatchRunRepository
from Backend.app.services.admin_batch_service import AdminBatchService
from Backend.app.services.business_metrics_service import BusinessMetricsService, UnknownProcessError

router = make_router(prefix="/admin/batches", tags=["admin"])


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
    status: Literal["running", "completed", "failed", "aborted"] | None = None,
    name: str | None = Query(None, max_length=100),
    service: AdminBatchService = Depends(get_admin_batch_service),
):
    return await service.list_runs(limit=limit, offset=offset, status=status, name=name)


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
