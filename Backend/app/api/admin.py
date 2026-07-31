from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from Storage.db import get_async_db
from repository.batch_runs import BatchRunRepository
from Backend.app.services.admin_batch_service import AdminBatchService

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


async def get_admin_batch_service(
    db=Depends(get_async_db),
) -> AsyncGenerator[AdminBatchService, None]:
    yield AdminBatchService(BatchRunRepository(db), db)


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
