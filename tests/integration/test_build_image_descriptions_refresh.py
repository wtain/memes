"""build_image_descriptions --refresh-stale (screenshot recut R3c). Requires a live PostgreSQL instance -- see conftest."""
import types
import uuid

import pytest
from sqlalchemy import select

from batch.build_image_descriptions import refresh_stale_descriptions
from Storage.models import DescriptionLemma, Image, ImageDescription, ImageStatus
from Storage.stale import images_with_stale_rows

PROMPTS = [types.SimpleNamespace(key="p1", prompt="describe"), types.SimpleNamespace(key="p2", prompt="describe more")]


class FakeDescriber:
    def __init__(self, fail_prompts=()):
        self.fail_prompts = fail_prompts

    def describe(self, path, prompt_text, model, num_ctx):
        if prompt_text in self.fail_prompts:
            raise RuntimeError("model down")
        return f"new text for {prompt_text}"


async def _cut_with_stale_descriptions(session, keys=("p1", "p2")):
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(cut)
    await session.flush()
    for key in keys:
        session.add(ImageDescription(image_id=cut.id, prompt_key=key, model_used="old", text=f"old {key}", stale=True))
    await session.flush()
    return cut


async def _rows(session, image_id):
    return (await session.execute(
        select(ImageDescription.prompt_key, ImageDescription.text, ImageDescription.stale)
        .where(ImageDescription.image_id == image_id).order_by(ImageDescription.prompt_key))).all()


async def _refresh(session, describer, limit=None):
    return await refresh_stale_descriptions(session, PROMPTS, describer, "/tmp/images", 2048, lambda p: "model", limit)


@pytest.mark.asyncio(loop_scope="session")
async def test_every_prompt_is_redescribed_and_the_stale_copies_go(db_session):
    cut = await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber())

    assert await _rows(db_session, cut.id) == [
        ("p1", "new text for describe", False), ("p2", "new text for describe more", False)]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_prompt_keeps_its_stale_copy_and_the_image_stays_selectable(db_session):
    cut = await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber(fail_prompts=("describe more",)))

    assert await _rows(db_session, cut.id) == [("p1", "new text for describe", False), ("p2", "old p2", True)]
    assert stats["failed"] >= 1
    assert cut.id in await images_with_stale_rows(db_session, ImageDescription, ImageStatus.CUT_PROPOSED)


@pytest.mark.asyncio(loop_scope="session")
async def test_replacing_a_description_drops_its_copied_lemmas(db_session):
    cut = await _cut_with_stale_descriptions(db_session, keys=("p1",))
    description = (await db_session.execute(
        select(ImageDescription).where(ImageDescription.image_id == cut.id))).scalar_one()
    description_id = description.id
    db_session.add(DescriptionLemma(image_description_id=description_id, lemma="old"))
    await db_session.flush()

    await _refresh(db_session, FakeDescriber())

    assert (await db_session.execute(
        select(DescriptionLemma).where(DescriptionLemma.image_description_id == description_id))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_description_of_a_prompt_that_is_no_longer_configured_is_removed(db_session):
    cut = await _cut_with_stale_descriptions(db_session, keys=("p1", "retired"))

    await _refresh(db_session, FakeDescriber())

    assert [key for key, _, _ in await _rows(db_session, cut.id)] == ["p1", "p2"]


@pytest.mark.asyncio(loop_scope="session")
async def test_active_images_are_never_redescribed(db_session):
    active = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    db_session.add(active)
    await db_session.flush()
    db_session.add(ImageDescription(image_id=active.id, prompt_key="p1", model_used="old", text="old", stale=True))
    await db_session.flush()

    await _refresh(db_session, FakeDescriber())

    assert await _rows(db_session, active.id) == [("p1", "old", True)]


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_caps_the_images_and_reports_the_rest_as_remaining(db_session):
    await _cut_with_stale_descriptions(db_session)
    await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber(), limit=1)

    assert stats["refreshed"] == 1
    assert stats["images_remaining"] >= 1
