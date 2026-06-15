import base64
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.db import get_async_db
from Backend.app.repositories.history_repository import HistoryRepository

router = APIRouter(prefix="/history", tags=["history"])


class SearchHistoryTagResponse(BaseModel):
    category: str
    value: str


class SearchHistoryItemResponse(BaseModel):
    id: str
    searchedAt: str
    query: Optional[str]
    client: str
    resultCount: int
    tags: list[SearchHistoryTagResponse]


class SearchHistoryListResponse(BaseModel):
    items: list[SearchHistoryItemResponse]
    nextCursor: Optional[str]
    hasNext: bool


async def get_history_repo(db: AsyncSession = Depends(get_async_db)):
    try:
        yield HistoryRepository(db)
    finally:
        pass


def _decode_cursor(cursor: Optional[str]) -> tuple[Optional[datetime], Optional[uuid.UUID]]:
    if not cursor:
        return None, None
    obj = json.loads(base64.urlsafe_b64decode(cursor).decode())
    return datetime.fromisoformat(obj["searched_at"]), uuid.UUID(obj["id"])


def _encode_cursor(row) -> str:
    payload = json.dumps({"id": str(row.id), "searched_at": row.searched_at.isoformat()})
    return base64.urlsafe_b64encode(payload.encode()).decode()


@router.get("", response_model=SearchHistoryListResponse)
async def get_history(
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = None,
    client: Optional[str] = None,
    repo: HistoryRepository = Depends(get_history_repo),
):
    cursor_searched_at, cursor_id = _decode_cursor(cursor)
    rows = await repo.list(
        limit=limit,
        cursor_searched_at=cursor_searched_at,
        cursor_id=cursor_id,
        client=client,
    )
    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1]) if rows and has_next else None
    items = [
        SearchHistoryItemResponse(
            id=str(r.id),
            searchedAt=r.searched_at.isoformat(),
            query=r.query,
            client=r.client,
            resultCount=r.result_count,
            tags=[SearchHistoryTagResponse(category=t.category, value=t.value) for t in r.tags],
        )
        for r in rows
    ]
    return SearchHistoryListResponse(items=items, nextCursor=next_cursor, hasNext=has_next)
