"""
Integration tests for repository.image_descriptions.description_not_rejected -- the single
definition of "rejected description" (feedback row with approved = FALSE) shared by search,
similarity and, later, tagging (task 149).

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_descriptions import description_not_rejected
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback


async def _description(db_session, prompt_key, feedback):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
        await db_session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_predicate_keeps_unreviewed_and_approved_and_drops_rejected(db_session):
    unreviewed = await _description(db_session, "a", None)
    approved = await _description(db_session, "b", True)
    rejected = await _description(db_session, "c", False)

    ids = set((await db_session.execute(
        select(ImageDescription.id).where(
            ImageDescription.id.in_([unreviewed.id, approved.id, rejected.id]),
            description_not_rejected(ImageDescription.id),
        )
    )).scalars().all())

    assert ids == {unreviewed.id, approved.id}
