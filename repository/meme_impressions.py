"""Search impression log (epic meme-popularity, S1): record the images a search returned, count them across a
meme's duplicate tree, and read the popularity.impressions.* settings."""
import uuid
from datetime import datetime

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from repository.popularity import OPEN_EDGE_SQL
from Storage.models import MemeImpression

SURFACES = ("search", "similar", "reply")

# UNION (not UNION ALL) so a cycle in the edges cannot loop.
_SUMMARY_SQL = text(f"""
    WITH RECURSIVE tree(id) AS (
        SELECT CAST(:root AS uuid)
        UNION
        SELECT a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE {OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL
    )
    SELECT count(*),
           count(*) FILTER (WHERE s.shown_at >= now() - make_interval(days => :days)),
           max(s.shown_at)
    FROM meme_impressions s JOIN tree t ON s.image_id = t.id
""")


def impressions_enabled() -> bool:
    """popularity.impressions.enabled: whether searches record the images they return (bool)."""
    raw = settings.get("popularity.impressions.enabled")
    if not isinstance(raw, bool):
        raise ValueError(f"popularity.impressions.enabled must be a bool, got {raw!r}")
    return raw


def window_days() -> int:
    """popularity.impressions.window_days: the span of the windowed impression count (int >= 1)."""
    raw = settings.get("popularity.impressions.window_days")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"popularity.impressions.window_days must be an int >= 1, got {raw!r}")
    return raw


def validate_impression_settings() -> None:
    """Load every impression setting so a missing or invalid key is a startup error (called from the lifespan)."""
    impressions_enabled()
    window_days()


class MemeImpressionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_many(self, surface: str, image_ids: list[uuid.UUID],
                          search_history_id: uuid.UUID | None = None) -> None:
        """Append one row per image, rank = 1-based list position, in one INSERT. Flushes, never commits."""
        if surface not in SURFACES:
            raise ValueError(f"unknown impression surface {surface!r}")
        if not image_ids:
            return
        rows = [{"image_id": image_id, "surface": surface, "rank": rank, "search_history_id": search_history_id}
                for rank, image_id in enumerate(image_ids, 1)]
        await self.session.execute(insert(MemeImpression), rows)
        await self.session.flush()

    async def summary(self, image_id: uuid.UUID, days: int) -> tuple[int, int, datetime | None]:
        """Impressions of the image and of every image in its tree through open absorb edges: all-time count,
        count within the last `days` days, and the newest time; (0, 0, None) when there are none."""
        row = (await self.session.execute(_SUMMARY_SQL, {"root": image_id, "days": days})).one()
        return int(row[0]), int(row[1]), row[2]
