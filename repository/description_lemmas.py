from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionLemma, ImageDescription


class DescriptionLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_descriptions_needing_lemmas(self):
        """Descriptions with no lemma rows yet. Descriptions are insert-only (re-describing
        deletes and re-inserts), so "no rows" is the whole staleness test. A description that
        normalizes to zero lemmas is re-selected on every run; harmless and cheap."""
        result = await self.session.execute(
            select(ImageDescription.id, ImageDescription.text)
            .where(~exists().where(DescriptionLemma.image_description_id == ImageDescription.id))
        )
        return result.all()


class DescriptionLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.description_count = 0

    async def add_lemmas(self, description_id, lemmas: set) -> None:
        self.description_count += 1
        if not lemmas:
            return
        stmt = (
            insert(DescriptionLemma)
            .values([
                {
                    "image_description_id": description_id,
                    "lemma": lemma,
                    "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
                }
                for lemma in lemmas
            ])
            .on_conflict_do_nothing(index_elements=["image_description_id", "lemma"])
        )
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total descriptions indexed: {self.description_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
