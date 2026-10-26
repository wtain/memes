"""Selection of the images build_image_embeddings works on (the model itself is not exercised)."""
import uuid

import pytest

from batch.build_image_embeddings import select_images_to_embed
from Storage.models import Embedding, Image


async def _image(session, status, vector=False):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    if vector:
        session.add(Embedding(image_id=image.id, embedding=[0.1] * 512))
        await session.flush()
    return image.id


async def _ids(session, incremental, status):
    return {row.id for row in await select_images_to_embed(session, incremental, status)}


@pytest.mark.asyncio(loop_scope="session")
async def test_cut_proposed_selects_only_cuts(db_session):
    cut, active, pending = [await _image(db_session, s) for s in ("cut_proposed", "active", "pending")]
    ids = await _ids(db_session, False, "cut_proposed")
    assert cut in ids and active not in ids and pending not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_skips_cuts_that_already_have_a_vector(db_session):
    fresh, done = await _image(db_session, "cut_proposed"), await _image(db_session, "cut_proposed", vector=True)
    ids = await _ids(db_session, True, "cut_proposed")
    assert fresh in ids and done not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_active_and_pending_runs_ignore_cuts(db_session):
    cut = await _image(db_session, "cut_proposed")
    assert cut not in await _ids(db_session, True, "active")
    assert cut not in await _ids(db_session, True, "pending")


@pytest.mark.asyncio(loop_scope="session")
async def test_all_selects_every_status(db_session):
    made = [await _image(db_session, s) for s in ("cut_proposed", "active", "pending")]
    assert set(made) <= await _ids(db_session, True, "all")
