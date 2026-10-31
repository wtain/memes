"""Meme save log (epic meme-popularity, S2): record a save, count saves across a meme's duplicate tree."""
import uuid
from datetime import datetime

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.popularity import OPEN_EDGE_SQL
from Storage.models import MemeSave

# UNION (not UNION ALL) so a cycle in the edges cannot loop.
_SUMMARY_SQL = text(f"""
    WITH RECURSIVE tree(id) AS (
        SELECT CAST(:root AS uuid)
        UNION
        SELECT a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE {OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL
    )
    SELECT count(*), max(s.created_at) FROM meme_saves s JOIN tree t ON s.image_id = t.id
""")


class MemeSaveRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, image_id: uuid.UUID) -> None:
        """Append one save row. Flushes, never commits."""
        await self.session.execute(insert(MemeSave).values(image_id=image_id))
        await self.session.flush()

    async def summary(self, image_id: uuid.UUID) -> tuple[int, datetime | None]:
        """Saves of the image and of every image in its tree through open absorb edges, with the newest save
        time; (0, None) when there are none."""
        row = (await self.session.execute(_SUMMARY_SQL, {"root": image_id})).one()
        return int(row[0]), row[1]
