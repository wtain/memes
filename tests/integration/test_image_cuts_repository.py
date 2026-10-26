import uuid

import pytest
from sqlalchemy import select

from repository.image_cuts import ImageCutsRepository
from Storage.models import CutState, Image, ImageCut, ImageExtras, ImageProcessingStatus, OCRText

PIPE, VER = "recut_detect_v1", "1"


async def _image(s, **kw):
    kw.setdefault("filename", f"{uuid.uuid4()}.png")
    kw.setdefault("status", "active")
    kw.setdefault("width", 400)
    kw.setdefault("height", 300)
    img = Image(**kw)
    s.add(img)
    await s.flush()
    return img


async def _cut(s, source, state=CutState.DETECTED, version=VER, cut_image=None):
    s.add(ImageCut(source_image_id=source.id if source else None, cut_image_id=cut_image.id if cut_image else None,
                   crop_x=1, crop_y=1, crop_w=10, crop_h=10, detector="margin_trim", detector_version=version,
                   state=state))
    await s.flush()


async def _ids(s, exclude=frozenset()):
    rows = await ImageCutsRepository(s).select_candidates(PIPE, VER, set(exclude))
    return {r.id for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_plain_active_image_is_a_candidate(db_session):
    img = await _image(db_session)
    assert img.id in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_corpus_images_are_excluded(db_session):
    img = await _image(db_session)
    assert img.id not in await _ids(db_session, {img.id})


@pytest.mark.asyncio(loop_scope="session")
async def test_non_active_images_are_excluded(db_session):
    for status in ("pending", "rejected", "cut_proposed"):
        img = await _image(db_session, status=status)
        assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_images_without_dimensions_are_excluded(db_session):
    img = await _image(db_session, width=None, height=None)
    assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_images_are_excluded(db_session):
    img = await _image(db_session)
    db_session.add(ImageExtras(image_id=img.id, flagged=True))
    await db_session.flush()
    assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_unflagged_extras_do_not_exclude(db_session):
    img = await _image(db_session)
    db_session.add(ImageExtras(image_id=img.id, flagged=False))
    await db_session.flush()
    assert img.id in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_live_cut_excludes_the_source(db_session):
    for state in CutState.LIVE:
        img = await _image(db_session)
        await _cut(db_session, img, state=state)
        assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_image_that_is_itself_a_cut_is_excluded(db_session):
    source = await _image(db_session)
    cut = await _image(db_session)
    await _cut(db_session, source, state=CutState.APPROVED, cut_image=cut)
    assert cut.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_for_this_version_is_excluded_but_not_for_another(db_session):
    same = await _image(db_session)
    other = await _image(db_session)
    await _cut(db_session, same, state=CutState.REJECTED, version=VER)
    await _cut(db_session, other, state=CutState.REJECTED, version="0")
    ids = await _ids(db_session)
    assert same.id not in ids
    assert other.id in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_a_processing_row_for_the_pipeline_excludes_done_and_failed(db_session):
    done = await _image(db_session)
    failed = await _image(db_session)
    other_pipe = await _image(db_session)
    db_session.add_all([
        ImageProcessingStatus(image_id=done.id, pipeline=PIPE, status="done"),
        ImageProcessingStatus(image_id=failed.id, pipeline=PIPE, status="failed"),
        ImageProcessingStatus(image_id=other_pipe.id, pipeline="recut_detect_v0", status="done"),
    ])
    await db_session.flush()
    ids = await _ids(db_session)
    assert done.id not in ids and failed.id not in ids
    assert other_pipe.id in ids  # an older detector version does not block the new one


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_and_count(db_session):
    for _ in range(3):
        await _image(db_session)
    repo = ImageCutsRepository(db_session)
    total = await repo.count_candidates(PIPE, VER, set())
    assert total >= 3
    assert len(await repo.select_candidates(PIPE, VER, set(), limit=2)) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_insert_detected_writes_a_detected_row(db_session):
    img = await _image(db_session, content_hash="h1")
    await ImageCutsRepository(db_session).insert_detected(
        source_image_id=img.id, source_content_hash="h1", box=(10, 20, 110, 220),
        detector="margin_trim", detector_version=VER, confidence=0.9, detect_run_id=None)
    row = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalar_one()
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (10, 20, 100, 200)
    assert row.state == CutState.DETECTED and row.confidence == 0.9 and row.source_content_hash == "h1"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_regions_respects_the_confidence_floor(db_session):
    img = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=img.id, text="hi", confidence=0.9, bbox=[[0, 0], [5, 0], [5, 5], [0, 5]]),
        OCRText(image_id=img.id, text="lo", confidence=0.1, bbox=[[9, 9], [10, 9], [10, 10], [9, 10]]),
        OCRText(image_id=img.id, text="nb", confidence=0.9, bbox=None),
    ])
    await db_session.flush()
    regions = await ImageCutsRepository(db_session).ocr_regions(img.id, 0.4)
    assert regions == [[[0, 0], [5, 0], [5, 5], [0, 5]]]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cut_can_store_a_failure_reason(db_session):
    img = await _image(db_session)
    await _cut(db_session, img, state=CutState.FAILED)
    row = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalar_one()
    assert row.failure_reason is None
    row.failure_reason = "source_missing"
    await db_session.flush()
    assert row.failure_reason == "source_missing"


async def _detected(s, source, confidence=0.9):
    cut = ImageCut(source_image_id=source.id, crop_x=1, crop_y=1, crop_w=10, crop_h=10, detector="margin_trim",
                   detector_version=VER, confidence=confidence, state=CutState.DETECTED)
    s.add(cut)
    await s.flush()
    return cut


async def _cuttable_ids(s, corpus=frozenset(), limit=None):
    return [r.cut_id for r in await ImageCutsRepository(s).select_cuttable(set(corpus), limit)]


@pytest.mark.asyncio(loop_scope="session")
async def test_cuttable_orders_by_confidence_then_limits(db_session):
    low = await _detected(db_session, await _image(db_session), 0.2)
    high = await _detected(db_session, await _image(db_session), 0.95)
    mid = await _detected(db_session, await _image(db_session), 0.5)
    ids = [i for i in await _cuttable_ids(db_session) if i in {low.id, high.id, mid.id}]
    assert ids == [high.id, mid.id, low.id]
    assert len(await _cuttable_ids(db_session, limit=1)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cuttable_row_carries_what_the_job_needs(db_session):
    src = await _image(db_session, content_hash="h")
    cut = await _detected(db_session, src)
    (row,) = [r for r in await ImageCutsRepository(db_session).select_cuttable(set()) if r.cut_id == cut.id]
    assert row.source_image_id == src.id and row.filename == src.filename and row.source_content_hash is None
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (1, 1, 10, 10)
    assert row.created_at == src.created_at and row.detect_run_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_ineligible_sources_are_not_cuttable_but_are_counted(db_session):
    repo = ImageCutsRepository(db_session)
    before = await repo.count_ineligible_detected(set())
    inactive = await _detected(db_session, await _image(db_session, status="rejected"))
    flagged_img = await _image(db_session)
    db_session.add(ImageExtras(image_id=flagged_img.id, flagged=True))
    flagged = await _detected(db_session, flagged_img)
    in_corpus_img = await _image(db_session)
    in_corpus = await _detected(db_session, in_corpus_img)
    ok = await _detected(db_session, await _image(db_session))
    await db_session.flush()
    ids = await _cuttable_ids(db_session, corpus={in_corpus_img.id})
    assert ok.id in ids and not ({inactive.id, flagged.id, in_corpus.id} & set(ids))
    assert await repo.count_ineligible_detected({in_corpus_img.id}) == before + 3


@pytest.mark.asyncio(loop_scope="session")
async def test_only_detected_rows_are_cuttable(db_session):
    img = await _image(db_session)
    await _cut(db_session, img, state=CutState.PROPOSED)
    rows = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalars().all()
    assert rows[0].id not in await _cuttable_ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_proposed_and_mark_failed(db_session):
    repo = ImageCutsRepository(db_session)
    a, b, cut_img = await _image(db_session), await _image(db_session), await _image(db_session)
    ok, bad = await _detected(db_session, a), await _detected(db_session, b)
    await repo.mark_proposed(ok.id, cut_img.id)
    await repo.mark_failed(bad.id, "source_missing")
    await db_session.refresh(ok)
    await db_session.refresh(bad)
    assert ok.state == CutState.PROPOSED and ok.cut_image_id == cut_img.id
    assert bad.state == CutState.FAILED and bad.failure_reason == "source_missing" and bad.cut_image_id is None
