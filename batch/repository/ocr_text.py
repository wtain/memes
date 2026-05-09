import numpy
from sqlalchemy import delete

from Storage.models import ImageMetrics, OCRText


class OCRTextRepository:

    def __init__(self, session):
        self.session = session

    async def overwrite_texts(self, image, ocr_result, language):
        await self.session.execute(
            delete(ImageMetrics).where(
                OCRText.image_id == image.id
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
