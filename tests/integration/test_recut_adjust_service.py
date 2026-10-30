"""RecutAdjustService: adjust the box of a recut proposal and rebuild its cut (screenshot recut R6).
Requires a live PostgreSQL instance. Files are real small images in a temporary BASE_PATH."""
import os
import uuid

import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from Backend.app.services.recut_adjust_service import (
    InvalidBoxError, ProposalWriteError, RecutAdjustService)
from Backend.app.services.recut_decision_service import ProposalConflictError, ProposalNotFoundError
from batch.utils import recut_cut
from batch.utils.file_hash import sha256_file
from repository import process_events
from repository.cut_copier import CutCopier
from repository.image_extras import ImageExtrasRepository
from Storage.models import CutState, Image, ImageCut, ImageStatus, OCRText, ProcessEvent

SIZE = (400, 300)
BOX = (10, 20, 300, 200)


def _write_image(path, fmt, color="white", size=SIZE):
    PILImage.new("RGB", size, color).save(path, format=fmt)


async def _proposal(session, tmp_path, fmt="JPEG"):
    suffix = ".png" if fmt == "PNG" else ".jpg"
    source_name, cut_name = f"{uuid.uuid4()}{suffix}", f"{uuid.uuid4()}{suffix}"
    _write_image(tmp_path / source_name, fmt)
    _write_image(tmp_path / cut_name, fmt, size=(BOX[2], BOX[3]))
    source = Image(filename=source_name, status=ImageStatus.ACTIVE, width=SIZE[0], height=SIZE[1])
    cut = Image(filename=cut_name, status=ImageStatus.CUT_PROPOSED, width=BOX[2], height=BOX[3])
    session.add_all([source, cut])
    await session.flush()
    row = ImageCut(
        source_image_id=source.id, cut_image_id=cut.id, source_content_hash=sha256_file(str(tmp_path / source_name)),
        crop_x=BOX[0], crop_y=BOX[1], crop_w=BOX[2], crop_h=BOX[3], detector="margin_trim", detector_version="1",
        confidence=0.9, state=CutState.PROPOSED)
    session.add(row)
    session.add(OCRText(image_id=source.id, text="hello", bbox=[60, 50, 40, 20]))
    await session.flush()
    return row, source, cut


def _service(session, tmp_path):
    return RecutAdjustService(session, base_path=str(tmp_path), jpeg_quality=90)


def _files(tmp_path):
    return sorted(os.listdir(tmp_path))


async def _image(session, image_id):
    return (await session.execute(select(Image).where(Image.id == image_id))).scalar_one_or_none()


@pytest.mark.asyncio(loop_scope="session")
async def test_adjust_replaces_the_cut_image_and_keeps_the_proposal(db_session, tmp_path):
    row, source, old_cut = await _proposal(db_session, tmp_path)
    old_cut_id, old_cut_name = old_cut.id, old_cut.filename
    await db_session.refresh(source)

    await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)

    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED and row.confidence == 0.9
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (50, 40, 200, 120)
    assert (row.detected_crop_x, row.detected_crop_y, row.detected_crop_w, row.detected_crop_h) == BOX
    assert row.cut_image_id != old_cut_id
    assert await _image(db_session, old_cut_id) is None
    new = await _image(db_session, row.cut_image_id)
    assert new.status == ImageStatus.CUT_PROPOSED and (new.width, new.height) == (200, 120)
    assert new.created_at == source.created_at
    with PILImage.open(tmp_path / new.filename) as im:
        assert im.size == (200, 120) and im.format == "JPEG"
    assert new.content_hash == sha256_file(str(tmp_path / new.filename))
    assert (tmp_path / old_cut_name).exists()                      # removed only after the commit
    ocr = (await db_session.execute(select(OCRText).where(OCRText.image_id == new.id))).scalar_one()
    assert ocr.stale is True and ocr.bbox == [10, 10, 40, 20]       # shifted by (-50, -40)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_png_source_gives_a_png_cut(db_session, tmp_path):
    row, _, _ = await _proposal(db_session, tmp_path, fmt="PNG")

    await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)

    new = await _image(db_session, row.cut_image_id)
    assert new.filename.endswith(".png")
    with PILImage.open(tmp_path / new.filename) as im:
        assert im.size == (200, 120) and im.format == "PNG"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_adjustment_keeps_the_first_detector_box(db_session, tmp_path):
    row, _, _ = await _proposal(db_session, tmp_path)
    service = _service(db_session, tmp_path)
    await service.adjust(row.id, 50, 40, 200, 120)
    first_cut_id = row.cut_image_id

    await service.adjust(row.id, 60, 50, 100, 100)

    assert (row.detected_crop_x, row.detected_crop_y, row.detected_crop_w, row.detected_crop_h) == BOX
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (60, 50, 100, 100)
    assert await _image(db_session, first_cut_id) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_adjusting_back_to_the_detected_box_is_allowed(db_session, tmp_path):
    row, _, _ = await _proposal(db_session, tmp_path)
    service = _service(db_session, tmp_path)
    await service.adjust(row.id, 50, 40, 200, 120)

    await service.adjust(row.id, *BOX)

    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == BOX
    new = await _image(db_session, row.cut_image_id)
    assert (new.width, new.height) == (BOX[2], BOX[3])


@pytest.mark.asyncio(loop_scope="session")
async def test_the_adjusted_event_is_recorded_once(db_session, tmp_path):
    row, _, _ = await _proposal(db_session, tmp_path)
    service = _service(db_session, tmp_path)

    await service.adjust(row.id, 50, 40, 200, 120)
    await service.adjust(row.id, 60, 50, 100, 100)

    assert await process_events.subject_has_any_event(db_session, "recut", row.id, ("adjusted",))
    count = await db_session.scalar(select(func.count()).select_from(ProcessEvent).where(
        ProcessEvent.process == "recut", ProcessEvent.event == "adjusted", ProcessEvent.subject_id == row.id))
    assert count == 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("box", [
    (50, 40, 15, 120), (50, 40, 200, 15), BOX, (250, 40, 200, 120), (50, 200, 200, 120), (-1, 0, 100, 100)],
    ids=["too_narrow", "too_short", "same_as_current", "overflows_width", "overflows_height", "negative_x"])
async def test_an_invalid_box_is_refused_and_leaves_nothing_behind(db_session, tmp_path, box):
    row, _, old_cut = await _proposal(db_session, tmp_path)
    before = _files(tmp_path)

    with pytest.raises(InvalidBoxError):
        await _service(db_session, tmp_path).adjust(row.id, *box)

    assert _files(tmp_path) == before
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == BOX and row.cut_image_id == old_cut.id
    assert row.detected_crop_x is None


async def _break(kind, session, tmp_path, row, source):
    if kind == "already_decided":
        row.state = CutState.APPROVED
    elif kind == "cut_missing":
        row.cut_image_id = None
    elif kind == "source_not_active":
        source.status = ImageStatus.PENDING
    elif kind == "source_flagged":
        await ImageExtrasRepository(session).set_flagged(source.id, True, "something")
    elif kind == "source_missing":
        row.source_image_id = None
    elif kind == "source_file_missing":
        os.remove(tmp_path / source.filename)
    elif kind == "source_changed":
        _write_image(tmp_path / source.filename, "JPEG", color="black")
    elif kind == "source_unreadable":
        row.source_content_hash = None
        (tmp_path / source.filename).write_bytes(b"not an image at all")
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("kind, code", [
    ("already_decided", "already_decided"), ("cut_missing", "cut_missing"), ("source_not_active", "source_not_active"),
    ("source_flagged", "source_flagged"), ("source_missing", "source_missing"),
    ("source_file_missing", "source_missing"), ("source_changed", "source_changed"),
    ("source_unreadable", "source_unreadable")])
async def test_a_proposal_that_cannot_be_adjusted_is_a_conflict_and_leaves_no_new_file(db_session, tmp_path, kind, code):
    row, source, _ = await _proposal(db_session, tmp_path)
    await _break(kind, db_session, tmp_path, row, source)
    before = _files(tmp_path)

    with pytest.raises(ProposalConflictError) as caught:
        await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)

    assert caught.value.code == code
    assert _files(tmp_path) == before
    assert row.detected_crop_x is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unknown_proposal_is_not_found(db_session, tmp_path):
    with pytest.raises(ProposalNotFoundError):
        await _service(db_session, tmp_path).adjust(uuid.uuid4(), 50, 40, 200, 120)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_old_file_is_removed_only_after_the_commit(db_session, tmp_path):
    row, _, old_cut = await _proposal(db_session, tmp_path)
    old_path = tmp_path / old_cut.filename

    await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)
    assert old_path.exists()

    await db_session.commit()

    assert not old_path.exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rollback_removes_the_new_file_and_keeps_the_old_one(db_session, tmp_path):
    row, _, old_cut = await _proposal(db_session, tmp_path)
    before = _files(tmp_path)

    await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)
    assert len(_files(tmp_path)) == len(before) + 1

    await db_session.rollback()

    assert _files(tmp_path) == before
    assert (tmp_path / old_cut.filename).exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failure_after_the_file_is_written_leaves_no_orphan(db_session, tmp_path, monkeypatch):
    row, _, _ = await _proposal(db_session, tmp_path)
    before = _files(tmp_path)

    async def broken(self, *args, **kwargs):
        raise RuntimeError("database trouble")
    monkeypatch.setattr(CutCopier, "copy", broken)

    with pytest.raises(RuntimeError):
        await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)
    await db_session.rollback()     # what get_async_db does when the request raises

    assert _files(tmp_path) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_a_write_error_is_reported_as_such(db_session, tmp_path, monkeypatch):
    row, _, _ = await _proposal(db_session, tmp_path)
    before = _files(tmp_path)

    def broken(*args, **kwargs):
        raise recut_cut.CutError(recut_cut.WRITE_ERROR, "disk full")
    monkeypatch.setattr("Backend.app.services.recut_adjust_service.crop_to_file", broken)

    with pytest.raises(ProposalWriteError):
        await _service(db_session, tmp_path).adjust(row.id, 50, 40, 200, 120)

    assert _files(tmp_path) == before
