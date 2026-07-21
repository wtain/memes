from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from Storage.models import ImageTag, OCRLemma


@lru_cache(maxsize=1)
def _get_morph():
    return make_morph()


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.
    """
    if not q:
        return None

    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
    if not lemmas:
        return None

    matching_ids: Optional[set] = None
    for lemma in lemmas:
        ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
        tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())

        result = await session.execute(union(ocr_subq, tag_subq))
        lemma_ids = {row[0] for row in result.all()}

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids


class OCRLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def delete_all(self) -> None:
        print("Deleting all ocr_lemmas rows...")
        await self.session.execute(delete(OCRLemma))
        await self.session.commit()
        print("Done")


class OCRLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        for lemma in lemmas:
            self.session.add(OCRLemma(image_id=image_id, lemma=lemma))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total images indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
