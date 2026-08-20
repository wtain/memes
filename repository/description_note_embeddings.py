from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from Storage.models import DescriptionNote, DescriptionNoteEmbedding


class DescriptionNoteEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_notes_needing_embedding(self):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .where(or_(
                DescriptionNote.embedding_built_at.is_(None),
                DescriptionNote.embedding_built_at < DescriptionNote.updated_at,
            ))
        )
        return result.all()

    async def save(self, image_id, embedding: list[float]) -> None:
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
            .values(embedding_built_at=func.now())
        )
