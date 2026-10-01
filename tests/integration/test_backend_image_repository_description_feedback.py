"""
Integration tests for the feedback-aware paths of Backend ImageRepository: rejected
descriptions are excluded from get_similar_by_description (task 149), and
delete_description_tags drops only the image's Ollama tags.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import (
    Image, ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback, ImageTag,
    TEXT_EMBEDDING_DIM,
)


def _vec(*head):
    v = [0.0] * TEXT_EMBEDDING_DIM
    for i, x in enumerate(head):
        v[i] = x
    return v


async def _image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    return image


async def _description(db_session, image, vector, prompt_key="p", feedback=None):
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(ImageDescriptionEmbedding(image_description_id=description.id, embedding=vector))
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
    await db_session.flush()
    return description


async def _similar_ids(db_session, source):
    rows = await ImageRepository(db_session).get_similar_by_description(str(source.id), limit=50)
    return {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_candidate_description_does_not_match(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _description(db_session, source, _vec(1.0))
    await _description(db_session, candidate, _vec(1.0), feedback=False)

    assert candidate.id not in await _similar_ids(db_session, source)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_source_description_does_not_match(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _description(db_session, source, _vec(1.0), feedback=False)
    await _description(db_session, candidate, _vec(1.0))

    assert candidate.id not in await _similar_ids(db_session, source)


@pytest.mark.asyncio(loop_scope="session")
async def test_unreviewed_and_approved_descriptions_still_match(db_session):
    source = await _image(db_session)
    unreviewed = await _image(db_session)
    approved = await _image(db_session)
    await _description(db_session, source, _vec(1.0))
    await _description(db_session, unreviewed, _vec(1.0))
    await _description(db_session, approved, _vec(1.0), feedback=True)

    assert {unreviewed.id, approved.id} <= await _similar_ids(db_session, source)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejected_description_does_not_hide_the_images_other_descriptions(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _description(db_session, source, _vec(1.0), prompt_key="a")
    await _description(db_session, candidate, _vec(1.0), prompt_key="a", feedback=False)
    await _description(db_session, candidate, _vec(1.0), prompt_key="b")  # different prompt: no pair with source
    await _description(db_session, candidate, _vec(0.0, 1.0), prompt_key="a2")

    # The only same-prompt pair for prompt "a" is rejected, so the candidate must not appear.
    assert candidate.id not in await _similar_ids(db_session, source)


@pytest.mark.asyncio(loop_scope="session")
async def test_has_description_embedding_ignores_rejected_descriptions(db_session):
    only_rejected = await _image(db_session)
    mixed = await _image(db_session)
    await _description(db_session, only_rejected, _vec(1.0), feedback=False)
    await _description(db_session, mixed, _vec(1.0), prompt_key="a", feedback=False)
    await _description(db_session, mixed, _vec(1.0), prompt_key="b")

    repo = ImageRepository(db_session)

    assert await repo.has_description_embedding(str(only_rejected.id)) is False
    assert await repo.has_description_embedding(str(mixed.id)) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_description_tags_only_removes_this_images_ollama_tags(db_session):
    target = await _image(db_session)
    bystander = await _image(db_session)
    db_session.add_all([
        ImageTag(image_id=target.id, key="k", value="v", source="Ollama"),
        ImageTag(image_id=target.id, key="k", value="v", source="OCR"),
        ImageTag(image_id=bystander.id, key="k", value="v", source="Ollama"),
    ])
    await db_session.flush()

    await ImageRepository(db_session).delete_description_tags(str(target.id))

    remaining = (await db_session.execute(
        select(ImageTag.image_id, ImageTag.source).where(ImageTag.image_id.in_([target.id, bystander.id]))
    )).all()
    assert sorted(remaining, key=lambda r: (str(r[0]), r[1])) == sorted(
        [(target.id, "OCR"), (bystander.id, "Ollama")], key=lambda r: (str(r[0]), r[1])
    )
