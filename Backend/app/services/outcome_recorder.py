"""Best-effort recording of a 'didn't find it' press (epic meme-popularity, S6): inside a savepoint, so a failure
never fails the request and never poisons its transaction."""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from repository.search_outcomes import SearchNotFoundRepository, outcomes_enabled

logger = logging.getLogger(__name__)


async def record_not_found(session: AsyncSession, search_id: str) -> None:
    if not outcomes_enabled():
        return
    try:
        search_uuid = uuid.UUID(str(search_id))
    except ValueError:
        return
    try:
        async with session.begin_nested():
            await SearchNotFoundRepository(session).record(search_uuid)
    except Exception as error:
        # The exception type only: nothing user-derived reaches the log.
        logger.warning("not-found write failed: error=%s", type(error).__name__)
