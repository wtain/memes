from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from Storage.models import ImageTag, OCRLemma


@lru_cache(maxsize=1)
def _get_morph():
    return make_morph()


async def _exact_lemma_ids(session: AsyncSession, lemma: str) -> set:
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}


async def _fuzzy_lemma_ids(session: AsyncSession, lemma: str) -> set:
    threshold = settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD
    ocr_subq = select(OCRLemma.image_id).where(func.similarity(OCRLemma.lemma, lemma) >= threshold)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.similarity(ImageTag.value, lemma) >= threshold)
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.

    Each query lemma is matched exactly first; only if that finds nothing,
    and the lemma is at least settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH
    characters (avoiding short-word false positives — see the design doc's
    empirical similarity-score table), a trigram-similarity fallback
    (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD) is tried instead. See
    docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md.
    """
    if not q:
        return None

    # language=None enables pymorphy3's script-based fallback (real Cyrillic
    # lemmatization) for a query string, which has no per-word language tag.
    # This is intentionally more thorough than the index side
    # (batch/utils/ocr_lemmas.py), which trusts each OCR row's own detected
    # language and skips lemmatization for confidently-non-Russian rows — see
    # that file's comment for the resulting (accepted) asymmetry.
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
        lemma_ids = await _exact_lemma_ids(session, lemma)
        if not lemma_ids and len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
            lemma_ids = await _fuzzy_lemma_ids(session, lemma)

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids


class OCRLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def delete_all(self) -> None:
        """No commit — caller controls commit timing, same convention as
        ImageProcessingStatusRepository.delete_all()/record_failure."""
        print("Deleting all ocr_lemmas rows...")
        await self.session.execute(delete(OCRLemma))
        print("Done")


class OCRLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    async def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        if not lemmas:
            return
        stmt = (
            insert(OCRLemma)
            .values([{"image_id": image_id, "lemma": lemma} for lemma in lemmas])
            .on_conflict_do_nothing(index_elements=["image_id", "lemma"])
        )
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total images indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
