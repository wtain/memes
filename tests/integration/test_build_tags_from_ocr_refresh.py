"""build_tags_from_ocr --refresh-stale (screenshot recut R3c). Requires a live PostgreSQL instance -- see conftest."""
import types
import uuid

import pytest
from sqlalchemy import select

from batch.build_tags_from_ocr import refresh_stale_ocr_tags
from Storage.models import Image, ImageStatus, ImageTag, OCRText


class FakeEngine:
    def tag(self, text, language="unknown"):
        return types.SimpleNamespace(tags=[("topic", "cats")] if "cat" in text else [])


async def _cut(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(image)
    await session.flush()
    return image


async def _tags(session, image_id, source="OCR"):
    return (await session.execute(select(ImageTag.key, ImageTag.value, ImageTag.stale)
                                  .where(ImageTag.image_id == image_id, ImageTag.source == source))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_replaces_stale_ocr_tags_and_leaves_other_sources(db_session):
    cut = await _cut(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="a cat", confidence=0.9, language="en", lang_score=0.9),
        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True),
        ImageTag(image_id=cut.id, key="topic", value="ollama", source="Ollama", stale=True),
    ])
    await db_session.flush()

    stats = await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert await _tags(db_session, cut.id) == [("topic", "cats", False)]
    assert await _tags(db_session, cut.id, "Ollama") == [("topic", "ollama", True)]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_new_ocr_without_tags_leaves_no_stale_ocr_tag(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="nothing", confidence=0.9, language="en", lang_score=0.9),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    await db_session.flush()

    await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert await _tags(db_session, cut.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_no_ocr_rows_at_all_leaves_no_stale_ocr_tag(db_session):
    cut = await _cut(db_session)
    db_session.add(ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True))
    await db_session.flush()

    await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert await _tags(db_session, cut.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_an_image_whose_ocr_is_still_stale(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="a cat", language="en", stale=True),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    await db_session.flush()

    stats = await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert stats["skipped_upstream_stale"] >= 1
    assert await _tags(db_session, cut.id) == [("topic", "dogs", True)]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_engine_failure_keeps_every_stale_tag(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="a cat", confidence=0.9, language="en", lang_score=0.9),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    cut_id = cut.id
    await db_session.commit()

    class Broken:
        def tag(self, text, language="unknown"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await refresh_stale_ocr_tags(db_session, Broken(), 0.0, 0.0)

    assert await _tags(db_session, cut_id) == [("topic", "dogs", True)]
