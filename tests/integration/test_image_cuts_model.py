"""R1: status/state constants, the image_cuts relation and the stale marker.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py."""
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from Storage.models import (
    CutState, Image, ImageCut, ImageDescription, ImageStatus, ImageTag, OCRLemma, OCRText, StaleMixin,
)


def test_image_status_constants():
    assert ImageStatus.ACTIVE == "active"
    assert ImageStatus.PENDING == "pending"
    assert ImageStatus.REJECTED == "rejected"
    assert ImageStatus.CUT_PROPOSED == "cut_proposed"
    assert set(ImageStatus.ALL) == {"active", "pending", "rejected", "cut_proposed"}


def test_cut_state_constants():
    assert set(CutState.ALL) == {"detected", "proposed", "approved", "rejected", "failed"}
    assert set(CutState.LIVE) == {"detected", "proposed", "approved"}


def _cut(source_id, cut_id=None, state=CutState.PROPOSED, **box):
    return ImageCut(
        source_image_id=source_id, cut_image_id=cut_id, source_content_hash="h",
        crop_x=box.get("crop_x", 10), crop_y=box.get("crop_y", 20),
        crop_w=box.get("crop_w", 300), crop_h=box.get("crop_h", 200),
        detector="test", detector_version="1", confidence=0.9, state=state,
    )


async def _images(session, count):
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(count)]
    session.add_all(images)
    await session.flush()
    return images


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_live_row_for_one_source_is_rejected(db_session):
    source, cut_a, cut_b = await _images(db_session, 3)
    db_session.add(_cut(source.id, cut_a.id, CutState.PROPOSED))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, cut_b.id, CutState.DETECTED))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejected_row_does_not_block_a_new_live_row(db_session):
    source, cut_a, cut_b = await _images(db_session, 3)
    db_session.add(_cut(source.id, cut_a.id, CutState.REJECTED))
    db_session.add(_cut(source.id, cut_b.id, CutState.PROPOSED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_two_failed_rows_for_one_source_are_allowed(db_session):
    source, = await _images(db_session, 1)
    db_session.add(_cut(source.id, None, CutState.FAILED))
    db_session.add(_cut(source.id, None, CutState.FAILED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cut_image_belongs_to_one_proposal(db_session):
    source_a, source_b, cut = await _images(db_session, 3)
    db_session.add(_cut(source_a.id, cut.id))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source_b.id, cut.id))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("box", [
    {"crop_w": 0}, {"crop_h": 0}, {"crop_w": -5}, {"crop_x": -1}, {"crop_y": -1},
])
async def test_the_box_check_rejects_empty_or_negative_boxes(db_session, box):
    source, = await _images(db_session, 1)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, None, **box))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_state_check_rejects_unknown_states(db_session):
    source, = await _images(db_session, 1)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, None, "approvd"))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_either_image_keeps_the_proposal(db_session):
    source, cut_image = await _images(db_session, 2)
    cut = _cut(source.id, cut_image.id, CutState.APPROVED)
    db_session.add(cut)
    await db_session.flush()

    await db_session.execute(delete(Image).where(Image.id == source.id))
    await db_session.refresh(cut)
    assert cut.source_image_id is None and cut.cut_image_id == cut_image.id

    await db_session.execute(delete(Image).where(Image.id == cut_image.id))
    await db_session.refresh(cut)
    assert cut.source_image_id is None and cut.cut_image_id is None
    assert cut.state == CutState.APPROVED
    assert cut.source_content_hash == "h"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_proposal_with_both_images_gone_does_not_block_a_new_live_row(db_session):
    """Both ids null: the partial unique index must not treat the history row as live for any source."""
    first, = await _images(db_session, 1)
    db_session.add(_cut(first.id, None, CutState.APPROVED))
    await db_session.flush()
    await db_session.execute(delete(Image).where(Image.id == first.id))
    second, = await _images(db_session, 1)
    db_session.add(_cut(second.id, None, CutState.PROPOSED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_defaults_are_filled_by_the_database(db_session):
    source, = await _images(db_session, 1)
    cut = _cut(source.id)
    db_session.add(cut)
    await db_session.flush()
    await db_session.refresh(cut)
    assert cut.id is not None and cut.created_at is not None and cut.decided_at is None
    assert cut.detect_run_id is None


def test_the_four_derived_models_carry_the_stale_mixin():
    for model in (OCRText, OCRLemma, ImageDescription, ImageTag):
        assert issubclass(model, StaleMixin), model.__name__


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_defaults_to_false_on_every_marked_table(db_session):
    image, = await _images(db_session, 1)
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t")
    rows = [
        OCRText(image_id=image.id, text="hello"),
        OCRLemma(image_id=image.id, lemma="hello"),
        description,
        ImageTag(image_id=image.id, key="k", value="v", source="OCR"),
    ]
    db_session.add_all(rows)
    await db_session.flush()
    for row in rows:
        await db_session.refresh(row)
        assert row.stale is False, type(row).__name__
    found = (await db_session.execute(select(OCRText.stale).where(OCRText.image_id == image.id))).scalars().all()
    assert found == [False]
