"""
Integration tests for ImageRepository.get_similar_by_description_all / has_text_embedding:
min cosine distance over all non-rejected description vectors plus the note vector.
Requires a live PostgreSQL instance.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import (
    DescriptionNote, DescriptionNoteEmbedding, Image, ImageDescription,
    ImageDescriptionEmbedding, ImageDescriptionFeedback, TEXT_EMBEDDING_DIM,
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


async def _description_vector(db_session, image, vector, prompt_key="p", feedback=None):
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(ImageDescriptionEmbedding(image_description_id=description.id, embedding=vector))
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
    await db_session.flush()


async def _note_vector(db_session, image, vector):
    db_session.add(DescriptionNote(image_id=image.id, text="note"))
    await db_session.flush()
    db_session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=vector))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_note_of_source_matches_ollama_description_of_candidate(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _note_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, candidate, _vec(1.0, 0.0))

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    by_id = {row[0]: row[1] for row in rows}
    assert by_id[candidate.id] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_distance_is_minimum_over_all_pairs(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _description_vector(db_session, source, _vec(1.0, 0.0), prompt_key="a")
    await _note_vector(db_session, candidate, _vec(0.0, 1.0))           # orthogonal: distance 1
    await _description_vector(db_session, candidate, _vec(1.0, 0.0), prompt_key="b")  # identical: 0

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    by_id = {row[0]: row[1] for row in rows}
    assert by_id[candidate.id] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_vectors_are_ignored_on_both_sides(db_session):
    source = await _image(db_session)
    near_but_rejected = await _image(db_session)
    await _description_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, near_but_rejected, _vec(1.0, 0.0), feedback=False)

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    assert near_but_rejected.id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_source_itself_and_non_active_candidates_and_orders_by_distance(db_session):
    source = await _image(db_session)
    close = await _image(db_session)
    far = await _image(db_session)
    pending = await _image(db_session, status="pending")
    await _description_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, close, _vec(1.0, 0.1))
    await _description_vector(db_session, far, _vec(0.0, 1.0))
    await _description_vector(db_session, pending, _vec(1.0, 0.0))

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)
    ids = [row[0] for row in rows]

    assert source.id not in ids
    assert pending.id not in ids
    assert ids.index(close.id) < ids.index(far.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_has_text_embedding_reflects_available_vectors(db_session):
    with_note = await _image(db_session)
    with_description = await _image(db_session)
    only_rejected = await _image(db_session)
    nothing = await _image(db_session)
    await _note_vector(db_session, with_note, _vec(1.0))
    await _description_vector(db_session, with_description, _vec(1.0))
    await _description_vector(db_session, only_rejected, _vec(1.0), feedback=False)

    repo = ImageRepository(db_session)
    assert await repo.has_text_embedding(str(with_note.id)) is True
    assert await repo.has_text_embedding(str(with_description.id)) is True
    assert await repo.has_text_embedding(str(only_rejected.id)) is False
    assert await repo.has_text_embedding(str(nothing.id)) is False
