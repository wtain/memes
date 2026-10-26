import os
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import batch.create_recut_cuts as job
from batch.utils.file_hash import sha256_file
from Storage.models import CutState, DescriptionNote, Image, ImageCut, ImageExtras, ImageTag, OCRText, ProcessEvent

BOX = dict(crop_x=10, crop_y=20, crop_w=50, crop_h=40)


def _write(path, fmt="PNG"):
    arr = np.random.default_rng(3).integers(0, 256, (100, 120, 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(path, fmt)


async def _proposal(s, tmp_path, name="a.png", confidence=0.9, write=True, **image_kw):
    img = Image(filename=name, status="active", width=120, height=100, **image_kw)
    s.add(img)
    await s.flush()
    digest = None
    if write:
        _write(tmp_path / name, "JPEG" if name.endswith(".jpg") else "PNG")
        digest = sha256_file(str(tmp_path / name))
    cut = ImageCut(source_image_id=img.id, source_content_hash=digest, detector="margin_trim", detector_version="1",
                   confidence=confidence, state=CutState.DETECTED, **BOX)
    s.add(cut)
    await s.flush()
    return img, cut


async def _only(s, keep):
    """Corpus ids that make a run see only the proposals of `keep` images."""
    rows = await job.ImageCutsRepository(s).select_cuttable(set())
    return {r.source_image_id for r in rows} - {i.id for i in keep}


async def _go(s, tmp_path, corpus, limit=None):
    await s.commit()
    m = await job.run(s, str(tmp_path), corpus, 95, limit=limit)
    await s.commit()
    return m.counters_dict()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_proposal_becomes_a_cut_with_file_row_link_copies_and_event(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    db_session.add_all([DescriptionNote(image_id=src.id, text="n"),
                        OCRText(image_id=src.id, text="hi", confidence=0.9,
                                bbox=[[20, 30], [40, 30], [40, 40], [20, 40]]),
                        ImageTag(image_id=src.id, key="k", value="v", source="OCR")])
    stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.PROPOSED and cut.cut_image_id is not None and cut.failure_reason is None
    new = await db_session.get(Image, cut.cut_image_id)
    assert new.status == "cut_proposed" and (new.width, new.height) == (50, 40)
    assert new.created_at == src.created_at and new.ingestion_batch_id is None and new.id != src.id
    assert os.path.splitext(new.filename)[1] == ".png" and (tmp_path / new.filename).is_file()
    assert PILImage.open(tmp_path / new.filename).size == (50, 40)
    assert (await db_session.get(DescriptionNote, new.id)).text == "n"
    (ocr,) = (await db_session.execute(select(OCRText).where(OCRText.image_id == new.id))).scalars().all()
    assert ocr.bbox == [[10, 10], [30, 10], [30, 20], [10, 20]] and ocr.stale is True
    (tag,) = (await db_session.execute(select(ImageTag).where(ImageTag.image_id == new.id))).scalars().all()
    assert tag.stale is True
    events = (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).scalars().all()
    assert [(e.process, e.event) for e in events] == [("recut", "proposed")]
    assert src.status == "active"
    assert stats["cut"] == 1 and stats["proposals_selected"] == 1 and stats.get("failed", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_jpeg_source_gives_a_jpeg_cut(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path, name="a.jpg")
    await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    new = await db_session.get(Image, cut.cut_image_id)
    assert new.filename.endswith(".jpg") and PILImage.open(tmp_path / new.filename).format == "JPEG"


@pytest.mark.asyncio(loop_scope="session")
async def test_changed_source_ends_failed_and_makes_nothing(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    PILImage.new("RGB", (120, 100), (1, 2, 3)).save(tmp_path / "a.png")
    stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "source_changed" and cut.cut_image_id is None
    assert stats["failed"] == 1 and stats.get("cut", 0) == 0
    assert sorted(os.listdir(tmp_path)) == ["a.png"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_source_file_ends_failed(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path, write=False)
    await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "source_missing"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_database_failure_rolls_back_the_cut_and_removes_the_file(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)

    async def boom(self, *a, **k):
        raise IntegrityError("stmt", {}, Exception("boom"))

    with patch.object(job.CutCopier, "copy", boom):
        stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "write_error" and cut.cut_image_id is None
    assert sorted(os.listdir(tmp_path)) == ["a.png"]
    assert (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).first() is None
    assert (await db_session.execute(select(Image).where(Image.status == "cut_proposed"))).first() is None
    assert stats["failed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_highest_confidence_first_and_limit(db_session, tmp_path):
    lo_src, lo = await _proposal(db_session, tmp_path, name="lo.png", confidence=0.3)
    hi_src, hi = await _proposal(db_session, tmp_path, name="hi.png", confidence=0.95)
    stats = await _go(db_session, tmp_path, await _only(db_session, [lo_src, hi_src]), limit=1)
    await db_session.refresh(hi)
    await db_session.refresh(lo)
    assert hi.state == CutState.PROPOSED and lo.state == CutState.DETECTED
    assert stats["proposals_selected"] == 1 and stats["proposals_remaining"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_ineligible_sources_are_skipped_and_stay_detected(db_session, tmp_path):
    ok_src, _ = await _proposal(db_session, tmp_path, name="ok.png")
    rej_src, rej = await _proposal(db_session, tmp_path, name="rej.png")
    rej_src.status = "rejected"
    fl_src, fl = await _proposal(db_session, tmp_path, name="fl.png")
    db_session.add(ImageExtras(image_id=fl_src.id, flagged=True))
    await db_session.flush()
    corpus = await _only(db_session, [ok_src])
    stats = await _go(db_session, tmp_path, corpus)
    await db_session.refresh(rej)
    await db_session.refresh(fl)
    assert rej.state == CutState.DETECTED and fl.state == CutState.DETECTED
    assert stats["cut"] == 1 and stats["skipped"] >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rerun_cuts_nothing_and_does_not_double_count(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    corpus = await _only(db_session, [src])
    await _go(db_session, tmp_path, corpus)
    stats = await _go(db_session, tmp_path, corpus)
    assert stats.get("cut", 0) == 0 and stats["proposals_selected"] == 0
    events = (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_consecutive_failures_stop_the_run(db_session, tmp_path):
    srcs = [(await _proposal(db_session, tmp_path, name=f"m{i}.png", write=False))[0] for i in range(3)]
    corpus = await _only(db_session, srcs)
    await db_session.commit()
    with patch.object(job, "MAX_CONSECUTIVE_FAILURES", 2), pytest.raises(RuntimeError):
        await job.run(db_session, str(tmp_path), corpus, 95)


@pytest.mark.asyncio(loop_scope="session")
async def test_retry_failed_requeues_failed_proposals_and_cuts_them(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path, write=False)
    corpus = await _only(db_session, [src])
    await _go(db_session, tmp_path, corpus)
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED
    _write(tmp_path / "a.png")  # the volume is back
    await db_session.execute(
        ImageCut.__table__.update().where(ImageCut.id == cut.id).values(source_content_hash=None))
    await db_session.commit()
    m = await job.run(db_session, str(tmp_path), corpus, 95, retry_failed=True)
    await db_session.commit()
    await db_session.refresh(cut)
    assert cut.state == CutState.PROPOSED and cut.failure_reason is None and m.counters_dict()["cut"] == 1
