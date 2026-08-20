from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import DescriptionNote, DescriptionNoteEmbedding


class DescriptionNoteEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_notes_needing_embedding(self):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text, DescriptionNote.updated_at)
            .where(or_(
                DescriptionNote.embedding_built_at.is_(None),
                DescriptionNote.embedding_built_at < DescriptionNote.updated_at,
            ))
        )
        return result.all()

    async def save(self, image_id, embedding: list[float], observed_updated_at) -> None:
        """observed_updated_at is the note's updated_at value AS READ, captured by the
        caller from get_notes_needing_embedding() -- not func.now(). Stamping with the
        observed value (rather than the commit-time now()) keeps the staleness predicate
        honest under batched/chunked commits: if the note is edited again between when
        this row was read and when this save() actually commits, the note's real
        updated_at will be newer than what we stamp here, so it correctly stays flagged
        stale for the next run instead of silently dropping the concurrent edit."""
        stmt = (
            insert(DescriptionNoteEmbedding)
            .values(description_note_id=image_id, embedding=embedding)
            .on_conflict_do_update(
                index_elements=["description_note_id"],
                set_={"embedding": embedding},
            )
        )
        await self.session.execute(stmt)
        await self.session.execute(
            update(DescriptionNote)
            .where(DescriptionNote.image_id == image_id)
            .values(embedding_built_at=observed_updated_at)
        )
