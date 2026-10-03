"""
Integration tests for repository/meme_profile_inputs.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.meme_profile_inputs import MemeProfileInputsRepository
from Storage.models import (
    DescriptionNote, Image, ImageDescription, ImageDescriptionFeedback, OCRText,
)


async def _insert_image(session, status: str | None = None, content_hash: str | None = None) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", content_hash=content_hash)
    if status is not None:
        image.status = status
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_list_active_images_returns_only_active_ones_with_filename_and_hash(db_session):
    active = await _insert_image(db_session, content_hash="hash-1")
    pending = await _insert_image(db_session, status="pending")
    repo = MemeProfileInputsRepository(db_session)

    images = await repo.list_active_images()

    assert (active.id, active.filename, "hash-1") in images
    assert pending.id not in [row[0] for row in images]


@pytest.mark.asyncio(loop_scope="session")
async def test_load_inputs_returns_an_entry_for_every_requested_image(db_session):
    empty = await _insert_image(db_session)
    repo = MemeProfileInputsRepository(db_session)

    inputs = await repo.load_inputs([empty.id])

    assert set(inputs) == {empty.id}
    assert inputs[empty.id].note is None
    assert inputs[empty.id].descriptions == []
    assert inputs[empty.id].ocr_rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_load_inputs_gathers_note_descriptions_and_ocr(db_session):
    image = await _insert_image(db_session)
    db_session.add(DescriptionNote(image_id=image.id, text="owner note"))
    db_session.add(ImageDescription(image_id=image.id, prompt_key="general_description", model_used="m", text="a cat"))
    db_session.add(OCRText(image_id=image.id, text="HELLO", confidence=0.9, lang_score=0.8, language="en",
                           bbox=[[0, 0], [1, 0], [1, 1], [0, 1]]))
    await db_session.flush()

    inputs = (await MemeProfileInputsRepository(db_session).load_inputs([image.id]))[image.id]

    assert inputs.note == "owner note"
    assert inputs.descriptions == [("general_description", "a cat")]
    assert len(inputs.ocr_rows) == 1
    row = inputs.ocr_rows[0]
    assert (row.text, row.confidence, row.lang_score) == ("HELLO", 0.9, 0.8)
    assert row.bbox == [[0, 0], [1, 0], [1, 1], [0, 1]]


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_descriptions_are_excluded_and_unreviewed_or_approved_are_kept(db_session):
    image = await _insert_image(db_session)
    kept = ImageDescription(image_id=image.id, prompt_key="a_unreviewed", model_used="m", text="kept")
    approved = ImageDescription(image_id=image.id, prompt_key="b_approved", model_used="m", text="approved")
    rejected = ImageDescription(image_id=image.id, prompt_key="c_rejected", model_used="m", text="rejected")
    db_session.add_all([kept, approved, rejected])
    await db_session.flush()
    db_session.add(ImageDescriptionFeedback(image_description_id=approved.id, approved=True))
    db_session.add(ImageDescriptionFeedback(image_description_id=rejected.id, approved=False))
    await db_session.flush()

    inputs = (await MemeProfileInputsRepository(db_session).load_inputs([image.id]))[image.id]

    assert sorted(inputs.descriptions) == [("a_unreviewed", "kept"), ("b_approved", "approved")]


@pytest.mark.asyncio(loop_scope="session")
async def test_load_inputs_keeps_each_images_inputs_separate(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    db_session.add(ImageDescription(image_id=first.id, prompt_key="k", model_used="m", text="first text"))
    db_session.add(ImageDescription(image_id=second.id, prompt_key="k", model_used="m", text="second text"))
    await db_session.flush()

    inputs = await MemeProfileInputsRepository(db_session).load_inputs([first.id, second.id])

    assert inputs[first.id].descriptions == [("k", "first text")]
    assert inputs[second.id].descriptions == [("k", "second text")]
