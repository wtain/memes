"""Best-effort write of one history row (search and meme-from-text). A failed write never fails the request."""
import logging
import uuid

from Backend.app.repositories.history_repository import HistoryRepository
from Storage.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def record_history(**fields) -> uuid.UUID | None:
    """Returns the id of the stored row, or None when the write failed."""
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                history_id = await HistoryRepository(session).add(**fields)
        return history_id
    except Exception as error:
        # The type name only: the fields carry the user's message.
        logger.warning("history write failed: %s", type(error).__name__)
        return None
