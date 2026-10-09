"""Ratings of meme-from-text results (P5b). Never commits (CLAUDE.md): get_async_db owns the transaction."""
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from Storage.models import Image, MemeTextRating

CONFLICT = "uq_meme_text_ratings_query_image"


@dataclass(frozen=True)
class RatingRow:
    query_hash: str
    query_text: str | None
    image_id: uuid.UUID
    grade: int
    rank: int
    reading: str
    parser_version: str
    taxonomy_version: int
    degraded: bool
    fallback: str
    language: str
    rated_at: str


class MemeTextRatingRepository:
    def __init__(self, session):
        self.session = session

    async def upsert(self, *, query_hash: str, query_text: str | None, image_id: uuid.UUID, grade: int, rank: int,
                     reading: str, parser_version: str, taxonomy_version: int, degraded: bool, fallback: str,
                     language: str) -> int:
        """Insert, or update the existing rating of this message and image (the latest metadata wins)."""
        values = dict(query_hash=query_hash, query_text=query_text, image_id=image_id, grade=grade, rank=rank,
                      reading=reading, parser_version=parser_version, taxonomy_version=taxonomy_version,
                      degraded=degraded, fallback=fallback, language=language)
        update_values = {key: value for key, value in values.items() if key not in ("query_hash", "image_id")}
        update_values["updated_at"] = func.now()
        statement = (
            insert(MemeTextRating).values(**values)
            .on_conflict_do_update(constraint=CONFLICT, set_=update_values)
            .returning(MemeTextRating.grade)
        )
        return (await self.session.execute(statement)).scalar_one()

    async def delete(self, query_hash: str, image_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(MemeTextRating).where(MemeTextRating.query_hash == query_hash, MemeTextRating.image_id == image_id))
        return result.rowcount > 0

    async def grades_for(self, query_hash: str) -> list[tuple[uuid.UUID, int]]:
        rows = await self.session.execute(
            select(MemeTextRating.image_id, MemeTextRating.grade).where(MemeTextRating.query_hash == query_hash))
        return sorted(((image_id, grade) for image_id, grade in rows.all()), key=lambda pair: str(pair[0]))

    async def forget_text(self, query_hash: str) -> int:
        """Clear the stored message text of every rating of this message; grades and ids stay."""
        result = await self.session.execute(
            update(MemeTextRating).where(MemeTextRating.query_hash == query_hash).values(query_text=None))
        return result.rowcount

    async def image_is_active(self, image_id: uuid.UUID) -> bool:
        found = await self.session.execute(select(Image.id).where(Image.id == image_id, Image.status == "active"))
        return found.first() is not None

    async def all_ratings(self) -> list[RatingRow]:
        """Every rating, oldest first (the export reads this with the read-only role)."""
        rows = await self.session.execute(
            select(MemeTextRating).order_by(MemeTextRating.rated_at, MemeTextRating.query_hash, MemeTextRating.id))
        return [
            RatingRow(row.query_hash, row.query_text, row.image_id, row.grade, row.rank, row.reading,
                      row.parser_version, row.taxonomy_version, bool(row.degraded), row.fallback, row.language,
                      row.rated_at.isoformat())
            for row in rows.scalars().all()
        ]
