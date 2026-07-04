import numpy
from sqlalchemy import delete, select, text, update
from sqlalchemy.sql.functions import count

from Storage.models import OCRText
from rules.lang_plausibility import score as compute_lang_score


class OCRTextRepository:

    def __init__(self, session):
        self.session = session

    async def count_texts(self) -> int:
        result = await self.session.execute(select(count(OCRText.id)))
        return result.scalar_one()

    async def delete_duplicate_texts(self) -> int:
        """Delete duplicate OCR rows per (image_id, normalized_text, language).

        Keeps the row with the highest confidence; on tie, keeps the earliest.
        Returns the number of deleted rows.
        """
        stmt = text("""
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY image_id, lower(trim(text)), language
                           ORDER BY confidence DESC NULLS LAST, created_at ASC
                       ) AS rn
                FROM ocr_texts
            )
            DELETE FROM ocr_texts
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """)
        result = await self.session.execute(stmt)
        return result.rowcount

    async def get_all_texts_with_language(self):
        result = await self.session.execute(
            select(OCRText.text, OCRText.confidence, OCRText.language, OCRText.lang_score)
        )
        return result.all()

    async def overwrite_texts(self, image, ocr_result, language):
        await self.session.execute(
            delete(OCRText).where(
                OCRText.image_id == image.id,
                OCRText.language == language,
            )
        )

        for bbox, text, confidence in ocr_result:
            # todo: threshold confidence
            # todo: create session once
            self.session.add(
                OCRText(
                    image_id=image.id,
                    text=text,
                    confidence=float(confidence),
                    bbox=[[v.item() if isinstance(v, numpy.int32) else v for v in p] for p in bbox],
                    language=language,
                    lang_score=compute_lang_score(text, language),
                )
            )

    async def get_rows_for_scoring(self, rescore_all: bool = False):
        """Rows to (re)score. By default, only rows with lang_score IS NULL."""
        query = select(OCRText.id, OCRText.text, OCRText.language)
        if not rescore_all:
            query = query.where(OCRText.lang_score.is_(None))
        result = await self.session.execute(query)
        return result.all()

    async def update_lang_score(self, text_id, lang_score: float | None) -> None:
        await self.session.execute(
            update(OCRText).where(OCRText.id == text_id).values(lang_score=lang_score)
        )
