from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from repository.ocr_text import concatenate_ocr_rows
from rules.text_heavy_result import TEXT_HEAVY
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding


class OCRTextEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_text_heavy_images_needing_embedding(
        self, classifier: str, confidence_min: float, lang_score_min: float, status: str = "active",
    ) -> dict:
        """Concatenated OCR text (see concatenate_ocr_rows) per text_heavy image not yet in
        ocr_text_embeddings. Images whose filtered text comes out empty (possible: the
        classifier's own bbox-coverage filter only checks confidence, not lang_score, so a
        text_heavy image's OCR could in principle be entirely low-lang-score noise) are simply
        absent from the result -- unlike classify_text_heavy.py's "unreadable" outcome, this
        has no separate counter; the caller's to-embed count is just len(this dict)."""
        already_embedded = select(OCRTextEmbedding.image_id).scalar_subquery()
        text_heavy_ids = (
            select(ImageClassification.image_id)
            .where(ImageClassification.classifier == classifier, ImageClassification.result == TEXT_HEAVY)
            .scalar_subquery()
        )
        candidate_ids = (
            select(Image.id)
            .where(
                Image.status == status,
                Image.id.in_(text_heavy_ids),
                Image.id.not_in(already_embedded),
            )
        )
        candidates = (await self.session.execute(candidate_ids)).scalars().all()
        if not candidates:
            return {}

        rows = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(candidates))
        )
        texts = concatenate_ocr_rows(rows.all(), confidence_min, lang_score_min)
        return {image_id: texts[image_id] for image_id in candidates if image_id in texts}

    async def save(self, image_id, embedding: list[float]) -> None:
        stmt = (
            insert(OCRTextEmbedding)
            .values(image_id=image_id, embedding=embedding)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"embedding": embedding, "computed_at": func.now()},
            )
        )
        await self.session.execute(stmt)
