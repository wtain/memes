"""Data access for the screenshot recut epic's detection step (R2). Never commits: the caller (the batch job)
controls commit timing."""
import uuid

from sqlalchemy import exists, func, select

from Storage.models import CutState, Image, ImageCut, ImageExtras, ImageProcessingStatus, OCRText


class ImageCutsRepository:

    def __init__(self, session):
        self.session = session

    def _candidates(self, pipeline: str, detector_version: str, exclude_ids):
        """Active images with real dimensions that are not flagged, not in `exclude_ids` (the frozen evaluation
        corpus), have no live cut as source, are not themselves a cut, were not rejected for this detector
        version, and have no processing row for `pipeline` (done or failed)."""
        live_source = exists().where(ImageCut.source_image_id == Image.id, ImageCut.state.in_(CutState.LIVE))
        is_cut = exists().where(ImageCut.cut_image_id == Image.id)
        rejected_here = exists().where(
            ImageCut.source_image_id == Image.id,
            ImageCut.state == CutState.REJECTED,
            ImageCut.detector_version == detector_version,
        )
        checked = exists().where(ImageProcessingStatus.image_id == Image.id, ImageProcessingStatus.pipeline == pipeline)
        flagged = exists().where(ImageExtras.image_id == Image.id, ImageExtras.flagged.is_(True))
        query = (
            select(Image.id, Image.filename, Image.content_hash)
            .where(Image.status == "active", Image.width.isnot(None), Image.height.isnot(None))
            .where(~live_source, ~is_cut, ~rejected_here, ~checked, ~flagged)
        )
        if exclude_ids:
            query = query.where(Image.id.not_in(list(exclude_ids)))
        return query

    async def select_candidates(self, pipeline: str, detector_version: str, exclude_ids, limit: int | None = None):
        query = self._candidates(pipeline, detector_version, exclude_ids).order_by(Image.id)
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).fetchall()

    async def count_candidates(self, pipeline: str, detector_version: str, exclude_ids) -> int:
        sub = self._candidates(pipeline, detector_version, exclude_ids).subquery()
        return (await self.session.execute(select(func.count()).select_from(sub))).scalar_one()

    async def insert_detected(self, *, source_image_id: uuid.UUID, source_content_hash: str | None, box,
                              detector: str, detector_version: str, confidence: float,
                              detect_run_id: uuid.UUID | None) -> None:
        x0, y0, x1, y1 = box
        self.session.add(ImageCut(
            source_image_id=source_image_id, source_content_hash=source_content_hash,
            crop_x=x0, crop_y=y0, crop_w=x1 - x0, crop_h=y1 - y0,
            detector=detector, detector_version=detector_version, confidence=confidence,
            detect_run_id=detect_run_id, state=CutState.DETECTED,
        ))
        await self.session.flush()

    async def ocr_regions(self, image_id: uuid.UUID, confidence_min: float) -> list:
        """Raw `bbox` values of the image's OCR regions at or above `confidence_min`."""
        result = await self.session.execute(
            select(OCRText.bbox).where(
                OCRText.image_id == image_id, OCRText.confidence >= confidence_min, OCRText.bbox.isnot(None)
            )
        )
        # the bbox column is JSON: a Python None can be stored as JSON null, which isnot(None) does not exclude
        return [bbox for bbox in result.scalars().all() if bbox is not None]
