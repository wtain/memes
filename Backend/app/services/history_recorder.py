"""Best-effort write of one history row (search and meme-from-text). A failed write never fails the request."""
import logging
import uuid

from Backend.app.repositories.history_repository import HistoryRepository
from Backend.app.services.abuse import recording_allowed
from Storage.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def record_history(**fields) -> uuid.UUID | None:
    """Returns the id of the stored row, or None when the write failed or the client's recording budget is spent."""
    if not recording_allowed(1):
        return None   # over the per-client budget (abuse.recording_per_min): skipped, the request is still served
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                history_id = await HistoryRepository(session).add(**fields)
        return history_id
    except Exception as error:
        # The type name only: the fields carry the user's message.
        logger.warning("history write failed: %s", type(error).__name__)
        return None
