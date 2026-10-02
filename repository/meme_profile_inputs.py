import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from repository.image_descriptions import description_not_rejected
from Storage.models import DescriptionNote, Image, ImageDescription, OCRText

ID_CHUNK = 1000


@dataclass(frozen=True)
class OcrRow:
    text: str
    confidence: float | None
    lang_score: float | None
    bbox: object  # raw OCRText.bbox JSON (a 4-point polygon in this pipeline) or None
    row_id: object


@dataclass
class RawInputs:
    note: str | None = None
    descriptions: list[tuple[str, str]] = field(default_factory=list)   # (prompt_key, text), rejected excluded
    ocr_rows: list[OcrRow] = field(default_factory=list)


class MemeProfileInputsRepository:
    """Read-only inputs for meme profile extraction: which active images exist and what text each has."""

    def __init__(self, session):
        self.session = session

    async def list_active_images(self) -> list[tuple[uuid.UUID, str, str | None]]:
        result = await self.session.execute(
            select(Image.id, Image.filename, Image.content_hash)
            .where(Image.status == "active")
            .order_by(Image.created_at, Image.id)
        )
        return [(image_id, filename, content_hash) for image_id, filename, content_hash in result.all()]

    async def load_inputs(self, image_ids: list[uuid.UUID]) -> dict[uuid.UUID, RawInputs]:
        inputs = {image_id: RawInputs() for image_id in image_ids}
        for start in range(0, len(image_ids), ID_CHUNK):
            chunk = image_ids[start:start + ID_CHUNK]

            notes = await self.session.execute(
                select(DescriptionNote.image_id, DescriptionNote.text).where(DescriptionNote.image_id.in_(chunk)))
            for image_id, text in notes.all():
                inputs[image_id].note = text

            descriptions = await self.session.execute(
                select(ImageDescription.image_id, ImageDescription.prompt_key, ImageDescription.text)
                .where(ImageDescription.image_id.in_(chunk), description_not_rejected(ImageDescription.id))
            )
            for image_id, prompt_key, text in descriptions.all():
                inputs[image_id].descriptions.append((prompt_key, text))

            ocr = await self.session.execute(
                select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score, OCRText.bbox, OCRText.id)
                .where(OCRText.image_id.in_(chunk))
            )
            for image_id, text, confidence, lang_score, bbox, row_id in ocr.all():
                inputs[image_id].ocr_rows.append(OcrRow(text, confidence, lang_score, bbox, row_id))
        return inputs
