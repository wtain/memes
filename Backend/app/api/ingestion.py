from typing import AsyncGenerator, Literal, Optional
from uuid import UUID

from fastapi import Depends, Query
from Backend.app.stage_policy import make_router
from pydantic import BaseModel

from Backend.app.types.generated import (
    ingestioncluster,
    ingestionclusterpage,
    ingestionclustermember,
    ingestiontierbcandidate,
    ingestiontierbreviewitem,
    ingestiontierbreviewpage,
)
from Backend.app.types.generated.ingestionpendingimage import Schema as PendingImage
from Backend.app.types.generated.ingestionresolveresponse import Schema as ResolveResponse
from Backend.app.types.generated.ingestionrunstatus import Schema as RunStatusResponse
from Backend.app.types.generated.ingestionundorejectresponse import Schema as UndoRejectResponse
from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService

router = make_router(prefix="/ingestion", tags=["ingestion"])

Tier = Literal["tier_a", "tier_b"]


# The generated member declares `text_heavy: bool | None = None`, but the wire contract is "false for both a
# not_text_heavy result and an unclassified image" (a boolean, never absent/null). These thin subclasses pin that
# default and re-nest the page models; every other field is inherited, so a schema field added to a generated
# class reaches the response automatically (see tests/test_response_wire_contract.py).
class ClusterMember(ingestionclustermember.Schema):
    text_heavy: bool = False


class Cluster(ingestioncluster.Schema):
    members: list[ClusterMember]


class ClusterPage(ingestionclusterpage.Schema):
    items: list[Cluster]


class TierBCandidate(ingestiontierbcandidate.Schema):
    member: ClusterMember


class TierBReviewItem(ingestiontierbreviewitem.Schema):
    image: ClusterMember
    candidates: list[TierBCandidate]


class TierBReviewPage(ingestiontierbreviewpage.Schema):
    items: list[TierBReviewItem]


class Decision(BaseModel):
    image_id: UUID
    decision: Literal["reject", "keep"]


class ResolveRequest(BaseModel):
    decisions: list[Decision]


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


@router.get("/clusters/{tier}", response_model=ClusterPage)
async def list_clusters(
    tier: Tier,
    cursor: Optional[str] = None,
    limit: int = Query(40, ge=1, le=200),
    service: IngestionService = Depends(get_ingestion_service),
):
    return await service.list_clusters(tier, cursor=cursor, limit=limit)


@router.get("/review/tier_b", response_model=TierBReviewPage)
async def tier_b_review(
    cursor: Optional[str] = None,
    limit: int = Query(40, ge=1, le=200),
    service: IngestionService = Depends(get_ingestion_service),
):
    return await service.list_tier_b_review(cursor=cursor, limit=limit)


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
