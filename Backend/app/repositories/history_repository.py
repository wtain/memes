import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, or_, and_
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
    ) -> None:
        record = SearchHistory(
            query=query,
            client=client,
            result_count=result_count,
            tags=[SearchHistoryTag(category=category, value=value) for category, value in tags],
        )
        self.session.add(record)

    async def list(
        self,
        limit: int,
        cursor_searched_at: Optional[datetime],
        cursor_id: Optional[uuid.UUID],
        client: Optional[str],
    ) -> list[SearchHistory]:
        stmt = (
            select(SearchHistory)
            .options(selectinload(SearchHistory.tags))
            .order_by(SearchHistory.searched_at.desc(), SearchHistory.id.desc())
        )
        if client:
            stmt = stmt.where(SearchHistory.client == client)
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
