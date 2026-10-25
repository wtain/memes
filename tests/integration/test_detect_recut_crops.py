import uuid

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.detect_recut_crops import PIPELINE, load_corpus_ids, run
from batch.utils.crop_detection import DETECTOR, DETECTOR_VERSION, DetectParams
from repository.image_cuts import ImageCutsRepository
from Storage.models import BatchRun, CutState, Image, ImageCut, ImageProcessingStatus, OCRText

PARAMS = DetectParams(min_removed_share=0.07, max_removed_share=0.75, flat_tolerance=7.0, ocr_overlap_max=0.15)


def _write_bordered(path, w=400, h=300, border=30):
    arr = np.full((h, w, 3), 255, dtype=np.uint8)
    arr[border:h - border, border:w - border] = np.random.default_rng(1).integers(
        0, 256, (h - 2 * border, w - 2 * border, 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(path, "PNG")


def _write_plain(path):
    PILImage.new("RGB", (400, 300), (255, 255, 255)).save(path)


async def _image(s, tmp_path, name, writer, **kw):
    kw.setdefault("width", 400)
    kw.setdefault("height", 300)
    img = Image(filename=name, status="active", **kw)
    s.add(img)
    await s.flush()
    if writer:
        writer(tmp_path / name)
    return img


async def _go(s, tmp_path, run_id=None, corpus=frozenset(), limit=None):
    m = await run(s, str(tmp_path), PARAMS, 0.4, run_id, set(corpus), 200, limit=limit)
    await s.commit()
    return m.counters_dict()


async def _cuts(s, image_id):
    return (await s.execute(select(ImageCut).where(ImageCut.source_image_id == image_id))).scalars().all()


async def _only(s, ids):
    """Corpus ids that make a run see only `ids`: every other current candidate is excluded."""
    rows = await ImageCutsRepository(s).select_candidates(PIPELINE, DETECTOR_VERSION, set())
    return {r.id for r in rows} - set(ids)


@pytest.mark.asyncio(loop_scope="session")
async def test_bordered_image_gets_a_detected_proposal(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered, content_hash="h")
    stats = await _go(db_session, tmp_path, corpus=await _only(db_session, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert (cut.crop_x, cut.crop_y, cut.crop_w, cut.crop_h) == (30, 30, 340, 240)
    assert cut.state == CutState.DETECTED and cut.detector == DETECTOR and cut.detector_version == DETECTOR_VERSION
    assert cut.source_content_hash == "h" and 0.6 <= cut.confidence <= 1.0
    status = await db_session.get(ImageProcessingStatus, {"image_id": img.id, "pipeline": PIPELINE})
    assert status.status == "done"
    assert stats["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_add_up_across_every_outcome(tmp_path, db_session):
    good = await _image(db_session, tmp_path, "good.png", _write_bordered)
    plain = await _image(db_session, tmp_path, "plain.png", _write_plain)
    clipped = await _image(db_session, tmp_path, "clipped.png", _write_bordered)
    missing = await _image(db_session, tmp_path, "missing.png", None)
    # a text region spilling out of the content box (content is x 30..370): 30% of it lies outside
    db_session.add(OCRText(image_id=clipped.id, text="t", confidence=0.9,
                           bbox=[[0, 100], [100, 100], [100, 130], [0, 130]]))
    await db_session.flush()
    corpus = await _only(db_session, {good.id, plain.id, clipped.id, missing.id})

    stats = await _go(db_session, tmp_path, corpus=corpus)

    assert stats["images_selected"] == 4
    assert stats["proposed"] == 1 and stats["no_cut"] == 1 and stats["clipped_text"] == 1 and stats["unreadable"] == 1
    assert stats["images_selected"] == stats["proposed"] + stats["no_cut"] + stats["clipped_text"] + stats["unreadable"]
    assert stats["images_remaining"] == 0
    assert (await db_session.get(ImageProcessingStatus, {"image_id": clipped.id, "pipeline": PIPELINE})).status == "done"
    assert await _cuts(db_session, clipped.id) == []
    bad = await db_session.get(ImageProcessingStatus, {"image_id": missing.id, "pipeline": PIPELINE})
    assert bad.status == "failed" and bad.error_message


@pytest.mark.asyncio(loop_scope="session")
async def test_second_run_adds_nothing(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered)
    corpus = await _only(db_session, {img.id})
    await _go(db_session, tmp_path, corpus=corpus)
    again = await _go(db_session, tmp_path, corpus=corpus)
    assert again["images_selected"] == 0 and again.get("proposed", 0) == 0
    assert len(await _cuts(db_session, img.id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_reports_images_remaining(tmp_path, db_session):
    imgs = [await _image(db_session, tmp_path, f"l{i}.png", _write_bordered) for i in range(3)]
    corpus = await _only(db_session, {i.id for i in imgs})
    stats = await _go(db_session, tmp_path, corpus=corpus, limit=2)
    assert stats["images_selected"] == 2
    assert stats["images_remaining"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stale_database_size_still_gives_a_box_inside_the_file(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered, width=10, height=10)
    await _go(db_session, tmp_path, corpus=await _only(db_session, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert cut.crop_x + cut.crop_w <= 400 and cut.crop_y + cut.crop_h <= 300


@pytest.mark.asyncio(loop_scope="session")
async def test_run_id_is_stored_on_the_proposal(tmp_path, db_session):
    run_row = BatchRun(kind="detect_recut_crops", trigger="manual", status="completed")
    db_session.add(run_row)
    await db_session.flush()
    img = await _image(db_session, tmp_path, "a.png", _write_bordered)
    await _go(db_session, tmp_path, run_id=run_row.run_id, corpus=await _only(db_session, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert cut.detect_run_id == run_row.run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_a_new_detector_version_checks_images_again(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "p.png", _write_plain)
    await _go(db_session, tmp_path, corpus=await _only(db_session, {img.id}))
    repo = ImageCutsRepository(db_session)
    assert img.id not in {r.id for r in await repo.select_candidates(PIPELINE, DETECTOR_VERSION, set())}
    assert img.id in {r.id for r in await repo.select_candidates("recut_detect_v2", "2", set())}


def test_load_corpus_ids_reads_the_committed_corpus():
    ids = load_corpus_ids()
    assert len(ids) == 1500 and all(isinstance(i, uuid.UUID) for i in ids)
