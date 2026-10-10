"""Best-effort write of one history row (search and meme-from-text). A failed write never fails the request."""
from Backend.app.repositories.history_repository import HistoryRepository
from Storage.db import AsyncSessionLocal


async def record_history(**fields) -> None:
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await HistoryRepository(session).add(**fields)
    except Exception:
        pass
