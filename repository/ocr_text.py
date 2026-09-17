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
            select(OCRText.text, OCRText.confidence, OCRText.language, OCRText.lang_score)
        )
        return result.all()

    async def overwrite_texts(self, image, ocr_result, language):
        import numpy
        from rules.lang_plausibility import score as compute_lang_score

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

    async def update_lang_scores(self, updates: list[dict]) -> None:
        """Bulk-update lang_score for many rows in a single round trip
        (executemany), instead of one individually-awaited UPDATE per row --
        the latter was the bottleneck in batch/score_ocr_language.py at
        real corpus scale (~29 rows/sec measured against a live environment,
        which would have taken hours per environment).

        Each dict must have keys "b_id" (OCRText.id) and "lang_score".
        """
        if not updates:
            return
        await self.session.execute(
            text("UPDATE ocr_texts SET lang_score = :lang_score WHERE id = :b_id"),
            updates,
        )


def concatenate_ocr_rows(rows, confidence_min: float, lang_score_min: float) -> dict:
    """rows: iterable of (image_id, text, confidence, lang_score) tuples -- e.g. straight from
    a SELECT OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score. Drops
    blocks below either threshold, orders survivors most-language-plausible first (Russian
    leads instead of the EN/ES EasyOCR readers' Latin transliteration noise), dedupes identical
    block text per image, and concatenates into one string per image_id. Images with no
    surviving block are simply absent from the result. Thresholds are required, passed in by
    the caller from settings.OCR.* -- this function stays config-agnostic and carries no
    literal defaults that could drift from environments/settings.yaml."""
    def _order_key(row):
        _, _, confidence, lang_score = row
        return (
            lang_score is None,
            -(lang_score if lang_score is not None else 0.0),
            confidence is None,
            -(confidence if confidence is not None else 0.0),
        )

    rows = sorted(rows, key=_order_key)
    by_image: dict = {}
    seen: dict = {}
    for image_id, text, confidence, lang_score in rows:
        if confidence is not None and confidence < confidence_min:
            continue
        if lang_score is not None and lang_score < lang_score_min:
            continue
        block = (text or "").strip()
        if not block:
            continue
        seen_for_image = seen.setdefault(image_id, set())
        if block in seen_for_image:
            continue
        seen_for_image.add(block)
        by_image.setdefault(image_id, []).append(block)
    return {image_id: " ".join(parts) for image_id, parts in by_image.items()}
