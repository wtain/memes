"""Search outcomes (epic meme-popularity, S6): the 'didn't find it' log and the popularity.search_outcomes.* setting."""
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from Storage.models import SearchNotFound


def outcomes_enabled() -> bool:
    """popularity.search_outcomes.enabled: whether saves link to their search and presses are stored (bool)."""
    raw = settings.get("popularity.search_outcomes.enabled")
    if not isinstance(raw, bool):
        raise ValueError(f"popularity.search_outcomes.enabled must be a bool, got {raw!r}")
    return raw


def validate_outcome_settings() -> None:
    """Load every outcome setting so a missing or invalid key is a startup error (called from the lifespan)."""
    outcomes_enabled()


class SearchNotFoundRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, search_history_id: uuid.UUID) -> None:
        """Append one press; a second press for the same search is a no-op. Flushes, never commits."""
        await self.session.execute(
            insert(SearchNotFound).values(search_history_id=search_history_id)
            .on_conflict_do_nothing(index_elements=["search_history_id"]))
        await self.session.flush()
