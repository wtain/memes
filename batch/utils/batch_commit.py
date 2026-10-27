from sqlalchemy import delete

from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding
from Storage.stale import delete_stale_rows
from repository.image_metrics import ImageMetricsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.ocr_text import OCRTextRepository


class BatchCommitter:
    def __init__(self, session, batch_size: int = 100, pipeline: str = ""):
        self._session = session
        self._batch_size = batch_size
        self._pipeline = pipeline
        self._pending = 0

    async def add_language_result(
        self,
        image: Image,
        language: str,
        ocr_result: list,
    ) -> None:
        await OCRTextRepository(self._session).overwrite_texts(image, ocr_result, language)

    async def on_image_done(self, image: Image, metrics: dict) -> None:
        await ImageMetricsRepository(self._session).overwrite_metrics(image, metrics)
        await ImageProcessingStatusRepository(self._session, self._pipeline).mark_done(image)
        self._pending += 1
        if self._pending >= self._batch_size:
            await self.flush()

    async def on_refresh_done(self, image: Image, metrics: dict) -> None:
        """A cut's OCR was rewritten: sweep what is still the source's copy and what was computed from the old text
        (the text-heavy classification and the OCR-text embedding), so classify_text_heavy and
        build_ocr_text_embeddings recompute them from the new OCR."""
        await delete_stale_rows(self._session, OCRText, [image.id])
        await self._session.execute(delete(ImageClassification).where(ImageClassification.image_id == image.id))
        await self._session.execute(delete(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id))
        await self.on_image_done(image, metrics)

    async def flush(self) -> None:
        await self._session.commit()
        self._pending = 0

    async def close(self) -> None:
        if self._pending > 0:
            await self.flush()
        await self._session.close()
