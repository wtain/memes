"""Storage/stale.py: select and delete stale rows without committing.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py."""
import uuid

import pytest
from sqlalchemy import select

import Storage.stale as stale
from Storage.models import (
    DescriptionLemma, Embedding, Image, ImageDescription, ImageDescriptionFeedback, ImageStatus, ImageTag, OCRLemma,
    OCRText, StaleMixin,
)
from Storage.stale import STALE_MODELS, delete_stale_rows, images_with_stale_rows, stale_counts


async def _image(session, status=ImageStatus.CUT_PROPOSED):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def test_stale_models_are_exactly_the_mixin_users():
    assert set(STALE_MODELS) == set(StaleMixin.__subclasses__())


@pytest.mark.asyncio(loop_scope="session")
async def test_selects_only_stale_rows_of_images_in_the_requested_status(db_session):
    cut = await _image(db_session, ImageStatus.CUT_PROPOSED)
    active = await _image(db_session, ImageStatus.ACTIVE)
    fresh = await _image(db_session, ImageStatus.CUT_PROPOSED)
    db_session.add_all([
        OCRText(image_id=cut.id, text="a", stale=True),
        OCRText(image_id=cut.id, text="b", stale=True),   # two stale rows, one image: reported once
        OCRText(image_id=active.id, text="c", stale=True),
        OCRText(image_id=fresh.id, text="d", stale=False),
    ])
    await db_session.flush()

    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.CUT_PROPOSED) == [cut.id]
    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.ACTIVE) == [active.id]
    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.PENDING) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_deletes_only_stale_rows_of_the_listed_images(db_session):
    target = await _image(db_session)
    other = await _image(db_session)
    db_session.add_all([
        ImageTag(image_id=target.id, key="k", value="stale", source="OCR", stale=True),
        ImageTag(image_id=target.id, key="k", value="kept-note", source="Note", stale=False),
        ImageTag(image_id=other.id, key="k", value="other-stale", source="OCR", stale=True),
    ])
    await db_session.flush()

    assert await delete_stale_rows(db_session, ImageTag, [target.id]) == 1
    left = (await db_session.execute(select(ImageTag.value).order_by(ImageTag.value))).scalars().all()
    assert left == ["kept-note", "other-stale"]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_empty_id_list_deletes_nothing_and_issues_no_statement(db_session, monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("no statement expected for an empty id list")

    monkeypatch.setattr(db_session, "execute", boom)
    assert await delete_stale_rows(db_session, OCRText, []) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_deletion_is_chunked_for_long_id_lists(db_session, monkeypatch):
    monkeypatch.setattr(stale, "_CHUNK", 2)
    images = [await _image(db_session) for _ in range(5)]
    db_session.add_all([OCRText(image_id=i.id, text="t", stale=True) for i in images])
    await db_session.flush()

    assert await delete_stale_rows(db_session, OCRText, [i.id for i in images]) == 5
    assert (await db_session.execute(select(OCRText))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_a_stale_description_takes_its_children_with_it(db_session):
    image = await _image(db_session)
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t", stale=True)
    db_session.add(description)
    await db_session.flush()
    db_session.add_all([
        ImageDescriptionFeedback(image_description_id=description.id, approved=True),
        DescriptionLemma(image_description_id=description.id, lemma="x"),
    ])
    await db_session.flush()

    assert await delete_stale_rows(db_session, ImageDescription, [image.id]) == 1
    assert (await db_session.execute(select(ImageDescriptionFeedback))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionLemma))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_counts_reports_every_marked_table(db_session):
    image = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=image.id, text="a", stale=True),
        OCRLemma(image_id=image.id, lemma="a", stale=True),
        OCRLemma(image_id=image.id, lemma="b", stale=False),
    ])
    await db_session.flush()
    counts = await stale_counts(db_session)
    assert set(counts) == {"ocr_texts", "ocr_lemmas", "image_descriptions", "tags"}
    assert counts["ocr_texts"] >= 1 and counts["ocr_lemmas"] >= 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("call", ["select", "delete"])
async def test_a_model_without_the_mixin_is_refused(db_session, call):
    with pytest.raises(ValueError):
        if call == "select":
            await images_with_stale_rows(db_session, Embedding, ImageStatus.ACTIVE)
        else:
            await delete_stale_rows(db_session, Embedding, [uuid.uuid4()])


@pytest.mark.asyncio(loop_scope="session")
async def test_the_helpers_never_commit(db_session, monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("helpers must not commit")

    image = await _image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a", stale=True))
    await db_session.flush()
    monkeypatch.setattr(db_session, "commit", boom)
    await images_with_stale_rows(db_session, OCRText, ImageStatus.CUT_PROPOSED)
    await delete_stale_rows(db_session, OCRText, [image.id])
    await stale_counts(db_session)
