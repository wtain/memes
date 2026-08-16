from datetime import datetime
from typing import AsyncGenerator, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

Tier = Literal["tier_a", "tier_b"]


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    stage: Optional[str]
    stats: Optional[dict]
    created_at: datetime
    completed_at: Optional[datetime]


class PendingImage(BaseModel):
    image_id: str
    filename: str
    created_at: datetime


class ClusterMember(BaseModel):
    image_id: str
    filename: str
    status: str
    ocr_text: Optional[str]


class ClusterEdge(BaseModel):
    image_id1: str
    image_id2: str
    distance: float
    match_source: Optional[str]


class Cluster(BaseModel):
    members: list[ClusterMember]
    edges: list[ClusterEdge]


class Decision(BaseModel):
    image_id: UUID
    decision: Literal["reject", "keep"]


class ResolveRequest(BaseModel):
    decisions: list[Decision]


class FailedDecision(BaseModel):
    image_id: str
    decision: str
    error: str


class MoveFailure(BaseModel):
    image_id: str
    error: str


class ResolveResponse(BaseModel):
    rejected: list[str]
    kept: list[str]
    failed: list[FailedDecision]
    move_failed: list[MoveFailure]


class UndoRejectResponse(BaseModel):
    image_id: str
    status: str


async def get_ingestion_service(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[IngestionService, None]:
    yield IngestionService(IngestionRepository(db))


@router.get("/run", response_model=RunStatusResponse)
async def get_run_status(service: IngestionService = Depends(get_ingestion_service)):
    return await service.get_run_status()


@router.get("/pending", response_model=list[PendingImage])
async def list_pending(service: IngestionService = Depends(get_ingestion_service)):
    return await service.list_pending()


@router.get("/clusters/{tier}", response_model=list[Cluster])
async def list_clusters(tier: Tier, service: IngestionService = Depends(get_ingestion_service)):
    return await service.list_clusters(tier)


@router.post("/clusters/{tier}/resolve", response_model=ResolveResponse)
async def resolve_cluster(
    tier: Tier,
    body: ResolveRequest,
    service: IngestionService = Depends(get_ingestion_service),
):
    decisions = [{"image_id": d.image_id, "decision": d.decision} for d in body.decisions]
    return await service.resolve(tier, decisions)


@router.post("/images/{image_id}/undo-reject", response_model=UndoRejectResponse)
async def undo_reject(image_id: UUID, service: IngestionService = Depends(get_ingestion_service)):
    return await service.undo_reject(image_id)
