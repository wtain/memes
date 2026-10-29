"""A recut source/cut pair is never a duplicate candidate (screenshot recut R5b). Requires a live PostgreSQL instance
with pgvector -- see tests/integration/conftest.py. The test database is shared, so assertions are about the seeded ids."""
import uuid

import pytest
from sqlalchemy import text

from batch.ingest_find_duplicates import find_batch_duplicates
from batch.rebuild_duplicates import find_duplicates, rebuild_active_library
from repository.batch_runs import BatchRunRepository
from Storage.models import CutState, Embedding, Image, ImageCut

_DIM = 512
_ONE_IMAGE_PROBE = """
    SELECT i.id, e.embedding FROM images i JOIN embeddings e ON e.image_id = i.id WHERE i.id = :probe_id
"""


def _vector(*values: float) -> list[float]:
    vec = [0.0] * _DIM
    for index, value in enumerate(values):
        vec[index] = value
    return vec


async def _image(session, vector, status="active", batch_id=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    session.add(Embedding(image_id=image.id, embedding=vector))
    await session.flush()
    return image.id


async def _cut_row(session, source_id, cut_id, state=CutState.APPROVED) -> None:
    session.add(ImageCut(source_image_id=source_id, cut_image_id=cut_id, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                         detector="margin_trim", detector_version="1", state=state))
    await session.flush()


async def _pairs(session, ids) -> set[frozenset]:
    rows = (await session.execute(text("SELECT image_id1, image_id2 FROM tmp_duplicates"))).all()
    return {frozenset((r.image_id1, r.image_id2)) for r in rows if r.image_id1 in ids or r.image_id2 in ids}


@pytest.mark.asyncio(loop_scope="session")
async def test_without_a_cut_row_identical_images_do_pair(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert await _pairs(db_session, {source, cut}) == {frozenset({source, cut})}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("state", [CutState.APPROVED, CutState.PROPOSED, CutState.REJECTED])
async def test_rebuild_never_pairs_a_source_with_its_cut(db_session, state):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut, state)
    control_a = await _image(db_session, _vector(0.0, 1.0))
    control_b = await _image(db_session, _vector(0.0, 1.0))

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert await _pairs(db_session, {source, cut, control_a, control_b}) == {frozenset({control_a, control_b})}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("probe", ["source", "cut"])
async def test_the_pair_is_excluded_whichever_side_is_the_probe(db_session, probe):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut)

    await find_duplicates(db_session, _ONE_IMAGE_PROBE, "i2.status = 'active'", k=20, threshold=0.3,
                          distance_source="clip", extra_params={"probe_id": source if probe == "source" else cut})

    assert await _pairs(db_session, {source, cut}) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_source_and_the_cut_still_pair_with_other_copies(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    copy = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert await _pairs(db_session, {source, cut, copy}) == {frozenset({source, copy}), frozenset({cut, copy})}


@pytest.mark.asyncio(loop_scope="session")
async def test_an_excluded_pair_does_not_use_up_a_k_slot(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))            # distance 0 from the source, the nearest neighbour
    other = await _image(db_session, _vector(1.0, 0.3))     # distance ~0.04, the next nearest
    await _cut_row(db_session, source, cut)

    await find_duplicates(db_session, _ONE_IMAGE_PROBE, "i2.status = 'active'", k=1, threshold=0.3,
                          distance_source="clip", extra_params={"probe_id": source})

    assert await _pairs(db_session, {source, cut, other}) == {frozenset({source, other})}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_ingestion_fragments_use_the_same_exclusion(db_session):
    # Synthetic: an ingested file is not a cut in practice. The clause is status-agnostic, and this pins that
    # find_batch_duplicates reaches it through find_duplicates().
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    pending = await _image(db_session, _vector(1.0), "pending", batch_id)
    active = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, active, pending, CutState.PROPOSED)
    control_pending = await _image(db_session, _vector(0.0, 1.0), "pending", batch_id)
    control_active = await _image(db_session, _vector(0.0, 1.0))

    await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert await _pairs(db_session, {pending, active, control_pending, control_active}) == {
        frozenset({control_pending, control_active})}
