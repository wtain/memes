import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.types.generated.recutproposaldetail import Schema as RecutProposalDetail
from Backend.app.types.generated.recutproposalpage import Schema as RecutProposalPage
from Storage.db import get_async_db

router = APIRouter(prefix="/recut", tags=["recut"])


async def get_recut_repository(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutRepository, None]:
    yield RecutRepository(db)


@router.get("/proposals", response_model=RecutProposalPage)
async def list_proposals(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                         repo: RecutRepository = Depends(get_recut_repository)):
    items, total = await repo.list_proposed(offset, limit)
    return {"items": items, "total": total}


@router.get("/proposals/{proposal_id}", response_model=RecutProposalDetail)
async def get_proposal(proposal_id: uuid.UUID, repo: RecutRepository = Depends(get_recut_repository)):
    detail = await repo.get(proposal_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return detail
