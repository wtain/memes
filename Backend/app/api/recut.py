import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.recut_decision_service import (
    ProposalConflictError, ProposalNotFoundError, RecutDecisionService)
from Backend.app.types.generated.recutdecision import Schema as RecutDecision
from Backend.app.types.generated.recutproposaldetail import Schema as RecutProposalDetail
from Backend.app.types.generated.recutstats import Schema as RecutStats
from Backend.app.types.generated.recutproposalpage import Schema as RecutProposalPage
from Storage.db import get_async_db

router = APIRouter(prefix="/recut", tags=["recut"])


async def get_recut_repository(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutRepository, None]:
    yield RecutRepository(db)


async def get_recut_decision_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutDecisionService, None]:
    yield RecutDecisionService(db)


@router.get("/proposals", response_model=RecutProposalPage)
async def list_proposals(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                         repo: RecutRepository = Depends(get_recut_repository)):
    items, total = await repo.list_proposed(offset, limit)
    return {"items": items, "total": total}


@router.get("/stats", response_model=RecutStats)
async def get_stats(repo: RecutRepository = Depends(get_recut_repository)):
    return await repo.band_stats()


@router.get("/proposals/{proposal_id}", response_model=RecutProposalDetail)
async def get_proposal(proposal_id: uuid.UUID, repo: RecutRepository = Depends(get_recut_repository)):
    detail = await repo.get(proposal_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return detail


async def _decide(call, proposal_id: uuid.UUID) -> dict:
    # Raising after the service wrote anything is safe: get_async_db rolls the whole request back.
    try:
        return await call(proposal_id)
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ProposalConflictError as error:
        raise HTTPException(status_code=409, detail=error.code) from None


@router.post("/proposals/{proposal_id}/approve", response_model=RecutDecision)
async def approve_proposal(proposal_id: uuid.UUID, service: RecutDecisionService = Depends(get_recut_decision_service)):
    return await _decide(service.approve, proposal_id)


@router.post("/proposals/{proposal_id}/reject", response_model=RecutDecision)
async def reject_proposal(proposal_id: uuid.UUID, service: RecutDecisionService = Depends(get_recut_decision_service)):
    return await _decide(service.reject, proposal_id)
