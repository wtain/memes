"""RecutRepository: the review queue of proposed cuts (screenshot recut R4). Requires a live PostgreSQL instance -- see conftest.
The test database is shared, so assertions are about the seeded ids and their relative order, not global counters."""
import uuid

import pytest

from Backend.app.repositories.recut_repository import RecutRepository
from Storage.models import CutState, Image, ImageCut, ImageStatus


async def _cut(session, confidence, state=CutState.PROPOSED, with_images=True):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add_all([source, cut])
    await session.flush()
    row = ImageCut(
        source_image_id=source.id if with_images else None,
        cut_image_id=cut.id if with_images else None,
        crop_x=10, crop_y=20, crop_w=300, crop_h=200,
        detector="margin_trim", detector_version="1", confidence=confidence, state=state)
    session.add(row)
    await session.flush()
    return row


async def _queue_ids(repo):
    items, total = await repo.list_proposed(0, 200)
    assert total <= 200, "the shared test database holds more proposals than one page; the order checks below need them all"
    return [item["id"] for item in items]


@pytest.mark.asyncio(loop_scope="session")
async def test_queue_is_ordered_by_confidence_with_null_last_and_ties_by_id(db_session):
    low = await _cut(db_session, 0.91)
    high = await _cut(db_session, 0.97)
    tie_a = await _cut(db_session, 0.94)
    tie_b = await _cut(db_session, 0.94)
    unscored = await _cut(db_session, None)
    repo = RecutRepository(db_session)

    ids = await _queue_ids(repo)

    mine = {str(r.id) for r in (high, tie_a, tie_b, low, unscored)}
    first_tie, second_tie = sorted([str(tie_a.id), str(tie_b.id)])
    assert [i for i in ids if i in mine] == [str(high.id), first_tie, second_tie, str(low.id), str(unscored.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_only_proposed_rows_with_both_images_are_listed(db_session):
    listed = await _cut(db_session, 0.9)
    detected = await _cut(db_session, 0.9, state=CutState.DETECTED)
    approved = await _cut(db_session, 0.9, state=CutState.APPROVED)
    rejected = await _cut(db_session, 0.9, state=CutState.REJECTED)
    orphan = await _cut(db_session, 0.9, with_images=False)
    repo = RecutRepository(db_session)

    ids = await _queue_ids(repo)

    assert str(listed.id) in ids
    for other in (detected, approved, rejected, orphan):
        assert str(other.id) not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_item_shape_and_box(db_session):
    row = await _cut(db_session, 0.8)
    repo = RecutRepository(db_session)

    items, total = await repo.list_proposed(0, 200)
    item = next(i for i in items if i["id"] == str(row.id))

    assert item == {"id": str(row.id), "sourceImageId": str(row.source_image_id), "cutImageId": str(row.cut_image_id),
                    "confidence": 0.8, "box": {"x": 10, "y": 20, "w": 300, "h": 200}}
    assert total == len(items)


@pytest.mark.asyncio(loop_scope="session")
async def test_paging_follows_the_full_order_and_total_is_the_whole_queue(db_session):
    for confidence in (0.71, 0.72, 0.73):
        await _cut(db_session, confidence)
    repo = RecutRepository(db_session)
    everything, total = await repo.list_proposed(0, 200)

    second, total_again = await repo.list_proposed(1, 1)

    assert second == [everything[1]]
    assert total_again == total == len(everything)


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_carries_provenance_and_neighbours_in_queue_order(db_session):
    for confidence in (0.51, 0.52, 0.53):
        await _cut(db_session, confidence)
    repo = RecutRepository(db_session)
    ids = await _queue_ids(repo)
    middle = ids[1]

    detail = await repo.get(uuid.UUID(middle))

    assert detail["id"] == middle and detail["state"] == "proposed"
    assert detail["detector"] == "margin_trim" and detail["detectorVersion"] == "1"
    assert detail["detectRunId"] is None and isinstance(detail["createdAt"], str)
    assert detail["previousId"] == ids[0] and detail["nextId"] == ids[2]
    first = await repo.get(uuid.UUID(ids[0]))
    last = await repo.get(uuid.UUID(ids[-1]))
    assert first["previousId"] is None and first["nextId"] == ids[1]
    assert last["nextId"] is None and last["previousId"] == ids[-2]


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_of_a_decided_row_has_no_neighbours(db_session):
    approved = await _cut(db_session, 0.9, state=CutState.APPROVED)
    repo = RecutRepository(db_session)

    detail = await repo.get(approved.id)

    assert detail["state"] == "approved"
    assert detail["previousId"] is None and detail["nextId"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_is_none_for_an_unknown_id_and_for_a_row_without_images(db_session):
    orphan = await _cut(db_session, 0.9, with_images=False)
    repo = RecutRepository(db_session)

    assert await repo.get(uuid.uuid4()) is None
    assert await repo.get(orphan.id) is None
