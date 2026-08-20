"""
Integration test for batch/build_description_note_lemmas.py's run() -- the full
staleness-selection -> lemma-building -> saving -> mark-built pipeline, exercised
end-to-end against a real database. Mirrors tests/integration/test_build_ocr_lemmas.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_description_note_lemmas import run
from rules.normalize import make_morph
from Storage.models import DescriptionNote, DescriptionNoteLemma, Image

_MORPH = make_morph()


@pytest.mark.asyncio(loop_scope="session")
async def test_run_indexes_notes_needing_lemmas_and_marks_built(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNote(image_id=image.id, text="a funny cat meme"))
    await db_session.flush()

    await run(db_session, morph=_MORPH, min_word_length=3)

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all()
    assert "cat" in rows
    assert "meme" in rows

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert note.lemmas_built_at is not None
