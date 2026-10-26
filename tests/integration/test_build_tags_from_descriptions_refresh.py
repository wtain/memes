"""build_tags_from_descriptions --refresh-stale (screenshot recut R3c). Requires a live PostgreSQL instance."""
import types
import uuid

import pytest
from sqlalchemy import select

from batch.build_tags_from_descriptions import refresh_stale_description_tags
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback, ImageStatus, ImageTag


class FakeEngine:
    def tag(self, text, language="unknown"):
        return types.SimpleNamespace(tags=[("topic", "cats")] if "cat" in text else [])


async def _cut(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(image)
    await session.flush()
    return image


async def _tags(session, image_id, source="Ollama"):
    return (await session.execute(select(ImageTag.key, ImageTag.value, ImageTag.stale)
                                  .where(ImageTag.image_id == image_id, ImageTag.source == source))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_replaces_stale_ollama_tags_and_leaves_ocr_tags(db_session):
    cut = await _cut(db_session)
    db_session.add_all([
        ImageDescription(image_id=cut.id, prompt_key="p1", model_used="m", text="a cat"),
        ImageTag(image_id=cut.id, key="topic", value="dogs", source="Ollama", stale=True),
        ImageTag(image_id=cut.id, key="topic", value="ocr", source="OCR", stale=True),
    ])
    await db_session.flush()

    stats = await refresh_stale_description_tags(db_session, FakeEngine())

    assert await _tags(db_session, cut.id) == [("topic", "cats", False)]
    assert await _tags(db_session, cut.id, "OCR") == [("topic", "ocr", True)]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_an_image_whose_descriptions_are_still_stale(db_session):
    cut = await _cut(db_session)
    db_session.add_all([
        ImageDescription(image_id=cut.id, prompt_key="p1", model_used="m", text="a cat", stale=True),
        ImageTag(image_id=cut.id, key="topic", value="dogs", source="Ollama", stale=True),
    ])
    await db_session.flush()

    stats = await refresh_stale_description_tags(db_session, FakeEngine())

    assert stats["skipped_upstream_stale"] >= 1
    assert await _tags(db_session, cut.id) == [("topic", "dogs", True)]


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_descriptions_do_not_feed_the_tags(db_session):
    cut = await _cut(db_session)
    rejected = ImageDescription(image_id=cut.id, prompt_key="p2", model_used="m", text="a cat")
    kept = ImageDescription(image_id=cut.id, prompt_key="p1", model_used="m", text="nothing here")
    db_session.add_all([rejected, kept, ImageTag(image_id=cut.id, key="topic", value="dogs", source="Ollama", stale=True)])
    await db_session.flush()
    db_session.add(ImageDescriptionFeedback(image_description_id=rejected.id, approved=False))
    await db_session.flush()

    await refresh_stale_description_tags(db_session, FakeEngine())

    assert await _tags(db_session, cut.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_no_descriptions_at_all_leaves_no_stale_ollama_tag(db_session):
    cut = await _cut(db_session)
    db_session.add(ImageTag(image_id=cut.id, key="topic", value="dogs", source="Ollama", stale=True))
    await db_session.flush()

    await refresh_stale_description_tags(db_session, FakeEngine())

    assert await _tags(db_session, cut.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_an_engine_failure_keeps_every_stale_tag(db_session):
    cut = await _cut(db_session)
    db_session.add_all([ImageDescription(image_id=cut.id, prompt_key="p1", model_used="m", text="a cat"),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="Ollama", stale=True)])
    cut_id = cut.id
    await db_session.commit()

    class Broken:
        def tag(self, text, language="unknown"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await refresh_stale_description_tags(db_session, Broken())

    assert await _tags(db_session, cut_id) == [("topic", "dogs", True)]
