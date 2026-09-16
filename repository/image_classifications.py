from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from Storage.models import Image, ImageClassification, OCRText


class ImageClassificationsRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.session = session

    async def set_result(self, image_id, classifier: str, result: str, details: dict) -> None:
        stmt = (
            insert(ImageClassification)
            .values(image_id=image_id, classifier=classifier, result=result, details=details)
            .on_conflict_do_update(
                index_elements=["image_id", "classifier"],
                set_={"result": result, "details": details, "computed_at": func.now()},
            )
        )
        await self.session.execute(stmt)

    async def get_candidate_regions(self, classifier: str, confidence_min: float, status: str = "active"):
        """Returns (image_id, filename, width, height, bbox, confidence) rows, ordered by
        image_id, for images with real dimensions and at least one OCR region at or above
        confidence_min, excluding images that already have a row for `classifier`. Callers group
        consecutive rows by image_id (already ordered -- no need for a full dict-of-lists)."""
        already_classified = (
            select(ImageClassification.image_id)
            .where(ImageClassification.classifier == classifier)
            .scalar_subquery()
        )
        query = (
            select(self.img.id, self.img.filename, self.img.width, self.img.height,
                   self.ocr.bbox, self.ocr.confidence)
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(
                self.img.status == status,
                self.img.width.isnot(None), self.img.height.isnot(None),
                self.ocr.confidence >= confidence_min, self.ocr.bbox.isnot(None),
                self.img.id.not_in(already_classified),
            )
            .order_by(self.img.id)
        )
        result = await self.session.execute(query)
        return result.fetchall()
