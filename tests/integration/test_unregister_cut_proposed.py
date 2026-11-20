"""UnregisterNonExisting also reconciles flagged cut_proposed images (screenshot recut R5a): a rejected cut whose file
move_flagged has moved away loses its row, an undecided cut never does. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from batch.tasks.SourceTasks import UnregisterNonExisting
from repository.image_extras import ImageExtrasRepository
from Storage.models import CutState, Image, ImageCut, ImageStatus


async def _cut_pair(session, tmp_path, *, flagged, file_exists):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add_all([source, cut])
    await session.flush()
    (tmp_path / source.filename).write_bytes(b"x")
    if file_exists:
        (tmp_path / cut.filename).write_bytes(b"x")
    row = ImageCut(source_image_id=source.id, cut_image_id=cut.id, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                   detector="margin_trim", detector_version="1",
                   state=CutState.REJECTED if flagged else CutState.PROPOSED)
    session.add(row)
    if flagged:
        await ImageExtrasRepository(session).set_flagged(cut.id, True, "recut_rejected")
    await session.flush()
    return row, source, cut


async def _exists(session, image_id):
    return (await session.execute(select(Image.id).where(Image.id == image_id))).scalar_one_or_none() is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flagged_cut_whose_file_is_gone_is_removed_and_its_proposal_survives(db_session, tmp_path):
    row, source, cut = await _cut_pair(db_session, tmp_path, flagged=True, file_exists=False)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert not await _exists(db_session, cut.id)
    assert await _exists(db_session, source.id)
    await db_session.refresh(row)
    assert row.state == CutState.REJECTED and row.cut_image_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_undecided_cut_with_a_missing_file_is_kept(db_session, tmp_path):
    row, source, cut = await _cut_pair(db_session, tmp_path, flagged=False, file_exists=False)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert await _exists(db_session, cut.id)
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED and row.cut_image_id == cut.id


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flagged_cut_whose_file_is_still_there_is_kept(db_session, tmp_path):
    _, _, cut = await _cut_pair(db_session, tmp_path, flagged=True, file_exists=True)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert await _exists(db_session, cut.id)


# SYN-1: guards against mass deletion when BASE_PATH is unmounted/empty or most files look missing.
async def _active(session, tmp_path, n, *, with_file):
    out = []
    for _ in range(n):
        img = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
        session.add(img)
        await session.flush()
        if with_file:
            (tmp_path / img.filename).write_bytes(b"x")
        out.append(img)
    return out


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_base_path_aborts_without_deleting(db_session, tmp_path):
    imgs = await _active(db_session, tmp_path, 3, with_file=False)
    with pytest.raises(RuntimeError, match="BASE_PATH"):
        await UnregisterNonExisting(db_session, str(tmp_path / "not-mounted")).run()
    assert await _exists(db_session, imgs[0].id)


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_base_path_aborts_without_deleting(db_session, tmp_path):
    imgs = await _active(db_session, tmp_path, 3, with_file=False)
    with pytest.raises(RuntimeError, match="empty"):
        await UnregisterNonExisting(db_session, str(tmp_path)).run()
    assert await _exists(db_session, imgs[0].id)


@pytest.mark.asyncio(loop_scope="session")
async def test_mass_deletion_over_threshold_aborts_unless_overridden(db_session, tmp_path):
    kept = await _active(db_session, tmp_path, 2, with_file=True)
    gone = await _active(db_session, tmp_path, 30, with_file=False)
    with pytest.raises(RuntimeError, match="allow-mass-delete"):
        await UnregisterNonExisting(db_session, str(tmp_path), min_guarded_count=10, max_delete_fraction=0.5).run()
    assert await _exists(db_session, gone[0].id)

    await UnregisterNonExisting(db_session, str(tmp_path), allow_mass_delete=True,
                                min_guarded_count=10, max_delete_fraction=0.5).run()
    assert not await _exists(db_session, gone[0].id)
    assert await _exists(db_session, kept[0].id)


@pytest.mark.asyncio(loop_scope="session")
async def test_small_deletion_below_threshold_proceeds(db_session, tmp_path):
    await _active(db_session, tmp_path, 40, with_file=True)
    gone = await _active(db_session, tmp_path, 2, with_file=False)
    await UnregisterNonExisting(db_session, str(tmp_path), min_guarded_count=1, max_delete_fraction=0.2).run()
    assert not await _exists(db_session, gone[0].id)
