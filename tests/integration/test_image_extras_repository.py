"""
Integration tests for repository/image_extras.py's get_flags_bulk().

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.image_extras import ImageExtrasRepository
from Storage.models import Image


@pytest.mark.asyncio(loop_scope="session")
async def test_get_flags_bulk_returns_correct_status_for_each_id(db_session):
    flagged_image = Image(filename=f"flagged-{uuid.uuid4()}.jpg")
    unflagged_image = Image(filename=f"unflagged-{uuid.uuid4()}.jpg")
    untouched_image = Image(filename=f"untouched-{uuid.uuid4()}.jpg")
    db_session.add_all([flagged_image, unflagged_image, untouched_image])
    await db_session.flush()

    repo = ImageExtrasRepository(db_session)
    await repo.set_flagged(flagged_image.id, True)
    await repo.set_flagged(unflagged_image.id, False)
    # untouched_image gets no image_extras row at all

    flags = await repo.get_flags_bulk([flagged_image.id, unflagged_image.id, untouched_image.id])

    assert flags == {
        flagged_image.id: True,
        unflagged_image.id: False,
        untouched_image.id: False,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_get_flags_bulk_empty_list_returns_empty_dict(db_session):
    repo = ImageExtrasRepository(db_session)

    flags = await repo.get_flags_bulk([])

    assert flags == {}
