"""RecutRepository.band_stats: the live approve rate by confidence band (screenshot recut R7). Requires a live PostgreSQL
instance. The test database is shared, so assertions compare the change a seeded set makes against a baseline taken first."""
import uuid

import pytest

from Backend.app.repositories.recut_repository import RecutRepository
from Storage.models import CutState, ImageCut

LABELS = ["0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0", "unscored"]


async def _row(session, confidence, state):
    session.add(ImageCut(source_image_id=None, cut_image_id=None, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                         detector="margin_trim", detector_version=str(uuid.uuid4())[:8], confidence=confidence,
                         state=state))
    await session.flush()


def _by_label(stats):
    return {band["label"]: band for band in stats["bands"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_bands_come_in_a_fixed_order_with_their_edges(db_session):
    stats = await RecutRepository(db_session).band_stats()

    assert [b["label"] for b in stats["bands"]] == LABELS
    assert [(b["lower"], b["upper"]) for b in stats["bands"]] == [
        (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 1.0), (None, None)]
    assert stats["total"]["label"] == "total"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("confidence, label", [
    (0.6, "0.6-0.7"), (0.699, "0.6-0.7"), (0.7, "0.7-0.8"), (0.8, "0.8-0.9"), (0.9, "0.9-1.0"), (1.0, "1.0"),
    (None, "unscored")])
async def test_a_confidence_lands_in_the_band_whose_lower_edge_it_reaches(db_session, confidence, label):
    repo = RecutRepository(db_session)
    before = _by_label(await repo.band_stats())[label]["waiting"]

    await _row(db_session, confidence, CutState.PROPOSED)

    assert _by_label(await repo.band_stats())[label]["waiting"] == before + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_counts_per_state_and_the_approve_rate(db_session):
    repo = RecutRepository(db_session)
    before = _by_label(await repo.band_stats())["0.8-0.9"]
    for state in (CutState.APPROVED, CutState.APPROVED, CutState.APPROVED, CutState.REJECTED, CutState.PROPOSED):
        await _row(db_session, 0.85, state)

    band = _by_label(await repo.band_stats())["0.8-0.9"]

    assert band["approved"] == before["approved"] + 3
    assert band["rejected"] == before["rejected"] + 1
    assert band["waiting"] == before["waiting"] + 1
    decided = band["approved"] + band["rejected"]
    assert band["approveRate"] == pytest.approx(band["approved"] / decided)


@pytest.mark.asyncio(loop_scope="session")
async def test_detected_and_failed_rows_are_not_counted(db_session):
    repo = RecutRepository(db_session)
    before = (await repo.band_stats())["total"]
    await _row(db_session, 0.95, CutState.DETECTED)
    await _row(db_session, 0.95, CutState.FAILED)

    after = (await repo.band_stats())["total"]

    assert (after["waiting"], after["approved"], after["rejected"]) == (
        before["waiting"], before["approved"], before["rejected"])


@pytest.mark.asyncio(loop_scope="session")
async def test_the_total_is_the_sum_of_the_bands_and_a_rate_is_null_without_decisions(db_session):
    repo = RecutRepository(db_session)
    await _row(db_session, 0.75, CutState.PROPOSED)

    stats = await repo.band_stats()

    for field in ("waiting", "approved", "rejected"):
        assert stats["total"][field] == sum(b[field] for b in stats["bands"])
    for band in [*stats["bands"], stats["total"]]:
        decided = band["approved"] + band["rejected"]
        assert band["approveRate"] == (None if decided == 0 else band["approved"] / decided)


@pytest.mark.asyncio(loop_scope="session")
async def test_adjusted_counts_the_rows_with_a_stored_detector_box_in_any_decidable_state(db_session):
    repo = RecutRepository(db_session)
    before = await repo.band_stats()
    for state in (CutState.PROPOSED, CutState.APPROVED):
        await _row(db_session, 0.95, state)
        db_session.add(ImageCut(
            source_image_id=None, cut_image_id=None, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
            detected_crop_x=0, detected_crop_y=0, detected_crop_w=6, detected_crop_h=6,
            detector="margin_trim", detector_version=str(uuid.uuid4())[:8], confidence=0.95, state=state))
    await db_session.flush()

    after = await repo.band_stats()

    assert _by_label(after)["0.9-1.0"]["adjusted"] - _by_label(before)["0.9-1.0"]["adjusted"] == 2
    assert after["total"]["adjusted"] - before["total"]["adjusted"] == 2
    assert _by_label(after)["0.6-0.7"]["adjusted"] == _by_label(before)["0.6-0.7"]["adjusted"]
