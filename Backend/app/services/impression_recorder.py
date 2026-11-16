"""Best-effort recording of search impressions (epic meme-popularity, S1): one bulk write inside a savepoint, so a
failure rolls back only itself and never fails or poisons the search that triggered it."""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.abuse import impression_rows_cap, recording_allowed
from repository.meme_impressions import MemeImpressionRepository, impressions_enabled

logger = logging.getLogger(__name__)


async def record_impressions(session: AsyncSession, surface: str, image_ids: list[uuid.UUID | str],
                             search_history_id: uuid.UUID | None = None) -> None:
    """Callers pass the ids as they have them (UUIDs or UUID strings); a malformed id is a swallowed failure too."""
    if not image_ids or not impressions_enabled():
        return
    image_ids = image_ids[:impression_rows_cap()]          # popularity.impressions.max_rows_per_call
    if not recording_allowed(len(image_ids)):              # shared with history rows: abuse.recording_per_min
        return
    try:
        ids = [image_id if isinstance(image_id, uuid.UUID) else uuid.UUID(str(image_id)) for image_id in image_ids]
        async with session.begin_nested():
            await MemeImpressionRepository(session).record_many(surface, ids, search_history_id)
    except Exception as error:
        # The surface and the exception type only: nothing from the query or the message reaches the log.
        logger.warning("impression write failed: surface=%s error=%s", surface, type(error).__name__)
