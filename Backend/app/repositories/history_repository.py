import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from Storage.models import SearchHistory, SearchHistoryTag


class HistoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        query: Optional[str],
        client: str,
        result_count: int,
        tags: list[tuple[str, str]],
        *,
        kind: str = "search",
        language: Optional[str] = None,
        query_hash: Optional[str] = None,
        situation: Optional[str] = None,
        degraded: Optional[bool] = None,
        fallback: Optional[str] = None,
        parse_ms: Optional[int] = None,
        retrieval_ms: Optional[int] = None,
    ) -> None:
        record = SearchHistory(
            query=query,
            client=client,
            result_count=result_count,
            kind=kind,
            language=language,
            query_hash=query_hash,
            situation=situation,
            degraded=degraded,
            fallback=fallback,
            parse_ms=parse_ms,
            retrieval_ms=retrieval_ms,
            tags=[SearchHistoryTag(category=category, value=value) for category, value in tags],
        )
        self.session.add(record)

    async def list(
        self,
        limit: int,
        cursor_searched_at: Optional[datetime],
        cursor_id: Optional[uuid.UUID],
        client: Optional[str],
        kind: str = "search",
    ) -> list[SearchHistory]:
        stmt = (
            select(SearchHistory)
            .options(selectinload(SearchHistory.tags))
            .order_by(SearchHistory.searched_at.desc(), SearchHistory.id.desc())
        )
        if client:
            stmt = stmt.where(SearchHistory.client == client)
        if kind != "all":
            stmt = stmt.where(SearchHistory.kind == kind)
        if cursor_searched_at:
            stmt = stmt.where(
                or_(
                    SearchHistory.searched_at < cursor_searched_at,
                    and_(
                        SearchHistory.searched_at == cursor_searched_at,
                        SearchHistory.id < cursor_id,
                    ),
                )
            )
        stmt = stmt.limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars())

    async def delete_by_query_hash(self, query_hash: str) -> int:
        """Delete the meme-from-text log rows of one message (tags go with them through the FK cascade)."""
        result = await self.session.execute(
            delete(SearchHistory).where(SearchHistory.kind == "meme_text", SearchHistory.query_hash == query_hash))
        return result.rowcount
