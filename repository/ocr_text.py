import numpy
from sqlalchemy import delete, select, text
from sqlalchemy.sql.functions import count

from Storage.models import OCRText


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
            select(OCRText.text, OCRText.confidence, OCRText.language)
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
                )
            )
