from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionNote, DescriptionNoteLemma


class DescriptionNoteLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_notes_needing_lemmas(self):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text, DescriptionNote.updated_at)
            .where(or_(
                DescriptionNote.lemmas_built_at.is_(None),
                DescriptionNote.lemmas_built_at < DescriptionNote.updated_at,
            ))
        )
        return result.all()

    async def mark_lemmas_built(self, image_id, observed_updated_at) -> None:
        """observed_updated_at is the note's updated_at value AS READ, captured by the
        caller from get_notes_needing_lemmas() -- not func.now(). Stamping with the
        observed value (rather than the commit-time now()) keeps the staleness predicate
        honest if this job's caller ever moves to chunked/batched commits: if the note is
        edited again between when this row was read and when this stamp actually commits,
        the note's real updated_at will be newer than what we stamp here, so it correctly
        stays flagged stale for the next run instead of silently dropping the concurrent
        edit. See repository/description_note_embeddings.py's save() for the analogous fix
        (that one hit the race for real, since it commits in chunks)."""
        await self.session.execute(
            update(DescriptionNote)
            .where(DescriptionNote.image_id == image_id)
            .values(lemmas_built_at=observed_updated_at)
        )


class DescriptionNoteLemmasSaver:
    """Unlike OCRLemmasSaver.add_lemmas (append-only -- OCR text is never
    edited after extraction), a description note can be edited to remove
    words, so replace_lemmas clears any existing rows for the image before
    inserting the new set, rather than merging via on_conflict_do_nothing."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    async def replace_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        await self.session.execute(
            delete(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image_id)
        )
        if not lemmas:
            return
        stmt = insert(DescriptionNoteLemma).values([
            {
                "image_id": image_id,
                "lemma": lemma,
                "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
            }
            for lemma in lemmas
        ])
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total notes indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
