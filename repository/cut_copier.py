"""Copies the inherited derived data of a source image to its recut cut (screenshot recut R3a; the rules are
docs/image-derived-data.md and docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md,
section 3). Never commits: the job runs it inside the cut's savepoint.

Rows are cloned with INSERT ... SELECT built from the model's own columns, so a column added later is copied without
touching this file. Only ocr_texts goes through Python (its boxes are moved into the cut's frame)."""
import uuid

import sqlalchemy as sa
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from batch.utils.recut_cut import shift_bbox
from Storage.models import (DescriptionLemma, DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma,
                            ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback, ImageExtras,
                            ImageTag, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue, OCRLemma, OCRText)

# Every table in docs/image-derived-data.md is in exactly one of these; tests/docs/test_cut_copier_inventory.py enforces it.
COPIED_TABLES = frozenset({
    "description_notes", "description_note_lemmas", "description_note_embeddings", "image_extras", "tags",
    "meme_profiles", "meme_profile_values", "meme_profile_situation_embeddings",
    "ocr_texts", "ocr_lemmas", "image_descriptions", "image_description_embeddings", "image_description_feedback",
    "description_lemmas",
})
NOT_COPIED_TABLES = frozenset({
    "image_cuts", "meme_text_ratings", "embeddings", "image_classifications", "ocr_text_embeddings",
    "image_processing_status", "tmp_duplicates", "tmp_clusters", "duplicate_decisions", "processing_errors",
    "image_metrics", "popularity_absorptions", "meme_saves",
})


def _uuid(value: uuid.UUID):
    return sa.literal(value, type_=PG_UUID(as_uuid=True))


def clone_rows(model, where, replace: dict):
    """INSERT ... SELECT copying every column of `model` for rows matching `where`; columns named in `replace` take the
    given SQL expression instead of the source value."""
    table = model.__table__
    names = [c.name for c in table.columns]
    values = [replace.get(name, table.c[name]) for name in names]
    return insert(table).from_select(names, select(*values).where(where))


class CutCopier:

    def __init__(self, session):
        self.session = session

    async def _run(self, statement) -> int:
        return (await self.session.execute(statement)).rowcount or 0

    async def copy(self, source_id: uuid.UUID, cut_id: uuid.UUID, crop: tuple[int, int, int, int]) -> dict[str, int]:
        new = _uuid(cut_id)
        new_pk = func.gen_random_uuid()
        counts: dict[str, int] = {}

        # Group 1: copied as is.
        counts["description_notes"] = await self._run(clone_rows(
            DescriptionNote, DescriptionNote.image_id == source_id, {"image_id": new}))
        counts["description_note_embeddings"] = await self._run(clone_rows(
            DescriptionNoteEmbedding, DescriptionNoteEmbedding.description_note_id == source_id,
            {"description_note_id": new}))
        counts["description_note_lemmas"] = await self._run(clone_rows(
            DescriptionNoteLemma, DescriptionNoteLemma.image_id == source_id, {"image_id": new}))
        counts["image_extras"] = await self._run(clone_rows(
            ImageExtras, sa.and_(ImageExtras.image_id == source_id, ImageExtras.remarks.isnot(None)),
            {"image_id": new, "flagged": sa.null(), "flagged_reason": sa.null()}))
        counts["meme_profiles"] = await self._run(clone_rows(
            MemeProfile, MemeProfile.image_id == source_id, {"image_id": new}))
        counts["meme_profile_values"] = await self._run(clone_rows(
            MemeProfileValue, MemeProfileValue.image_id == source_id, {"image_id": new}))
        counts["meme_profile_situation_embeddings"] = await self._run(clone_rows(
            MemeProfileSituationEmbedding, MemeProfileSituationEmbedding.image_id == source_id, {"image_id": new}))
        note_tags = await self._run(clone_rows(
            ImageTag, sa.and_(ImageTag.image_id == source_id, ImageTag.source == "Note"),
            {"id": new_pk, "image_id": new, "stale": sa.false()}))

        # Group 3: copied stale.
        counts["ocr_texts"] = await self._copy_ocr_texts(source_id, cut_id, crop)
        counts["ocr_lemmas"] = await self._run(clone_rows(
            OCRLemma, OCRLemma.image_id == source_id, {"image_id": new, "stale": sa.true()}))
        counts.update(await self._copy_descriptions(source_id, cut_id))
        stale_tags = await self._run(clone_rows(
            ImageTag, sa.and_(ImageTag.image_id == source_id, ImageTag.source.in_(("OCR", "Ollama"))),
            {"id": new_pk, "image_id": new, "stale": sa.true()}))
        counts["tags"] = note_tags + stale_tags
        return counts

    async def _copy_ocr_texts(self, source_id, cut_id, crop) -> int:
        rows = (await self.session.execute(
            select(OCRText.text, OCRText.confidence, OCRText.bbox, OCRText.language, OCRText.lang_score,
                   OCRText.created_at).where(OCRText.image_id == source_id))).all()
        out = []
        for row in rows:
            keep, bbox = shift_bbox(row.bbox, crop)
            if keep:
                out.append({"id": uuid.uuid4(), "image_id": cut_id, "text": row.text, "confidence": row.confidence,
                            "bbox": bbox, "language": row.language, "lang_score": row.lang_score,
                            "created_at": row.created_at, "stale": True})
        if out:
            await self.session.execute(insert(OCRText.__table__), out)
        return len(out)

    async def _copy_descriptions(self, source_id, cut_id) -> dict[str, int]:
        counts = {"image_descriptions": 0, "image_description_embeddings": 0, "image_description_feedback": 0,
                  "description_lemmas": 0}
        old_ids = (await self.session.execute(
            select(ImageDescription.id).where(ImageDescription.image_id == source_id))).scalars().all()
        for old in old_ids:
            new = _uuid(uuid.uuid4())  # one id for the description and all of its children
            counts["image_descriptions"] += await self._run(clone_rows(
                ImageDescription, ImageDescription.id == old,
                {"id": new, "image_id": _uuid(cut_id), "stale": sa.true()}))
            for key, model in (("image_description_embeddings", ImageDescriptionEmbedding),
                               ("image_description_feedback", ImageDescriptionFeedback),
                               ("description_lemmas", DescriptionLemma)):
                counts[key] += await self._run(clone_rows(
                    model, model.image_description_id == old, {"image_description_id": new}))
        return counts
