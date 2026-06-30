from Storage.models import Image
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
        metrics: dict,
    ) -> None:
        await OCRTextRepository(self._session).overwrite_texts(image, ocr_result, language)
        await ImageMetricsRepository(self._session).overwrite_metrics(image, metrics)

    async def on_image_done(self, image: Image) -> None:
        await ImageProcessingStatusRepository(self._session, self._pipeline).mark_done(image)
        self._pending += 1
        if self._pending >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        await self._session.commit()
        self._pending = 0

    async def close(self) -> None:
        if self._pending > 0:
            await self.flush()
        await self._session.close()
