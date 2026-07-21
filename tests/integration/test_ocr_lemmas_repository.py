"""
Integration tests for repository/ocr_lemmas.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

Note on the two write-path tests below (test_saver_writes_one_row_per_lemma,
test_delete_all_clears_table): OCRLemmasSaver.__aexit__ and
OCRLemmasRepository.delete_all() call session.commit() per their documented
contract ("commits on exit" / "deletes all rows, commits"). That real commit
ends the transaction the db_session fixture opened via
`async with session.begin():`, so db_session cannot be used for any further
statement afterwards — SQLAlchemy raises "Can't operate on closed transaction
inside context manager". These two tests therefore verify through a second,
independent session opened directly from db_engine, and explicitly clean up
what they committed, since the fixture's end-of-test rollback no longer
covers data that was already committed for real.
"""
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver, matching_image_ids
from Storage.models import Image, ImageTag, OCRLemma


def _fresh_session(db_engine):
    return sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)()


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_writes_one_row_per_lemma(db_session, db_engine):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    async with OCRLemmasSaver(db_session) as saver:
        saver.add_lemmas(image.id, {"кот", "собака"})

    async with _fresh_session(db_engine) as session:
        rows = (await session.execute(
            select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
        )).scalars().all()
        assert set(rows) == {"кот", "собака"}

        # Clean up the rows OCRLemmasSaver committed for real.
        await session.execute(delete(OCRLemma).where(OCRLemma.image_id == image.id))
        await session.execute(delete(Image).where(Image.id == image.id))
        await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_clears_table(db_session, db_engine):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="кот"))
    await db_session.flush()

    await OCRLemmasRepository(db_session).delete_all()

    async with _fresh_session(db_engine) as session:
        remaining = (await session.execute(select(OCRLemma))).scalars().all()
        assert remaining == []

        # Clean up the image row committed for real (delete_all() only
        # touches the ocr_lemmas table, by design — it's a table-wide clear).
        await session.execute(delete(Image).where(Image.id == image.id))
        await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_query_returns_none(db_session):
    assert await matching_image_ids(db_session, None) is None
    assert await matching_image_ids(db_session, "") is None
    assert await matching_image_ids(db_session, "   ") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_single_lemma_matches_indexed_image(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="полиция"),
        OCRLemma(image_id=other.id, lemma="магазин"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "полицию")

    assert ids == {matching.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_word_query_requires_all_lemmas(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="звонить"),
        OCRLemma(image_id=both.id, lemma="полиция"),
        OCRLemma(image_id=only_one.id, lemma="звонить"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "звоню в полицию")

    assert ids == {both.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_value_matches_query_lemma(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="животное", value="кот", source="rules"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "коты")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_match_returns_empty_set(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ids = await matching_image_ids(db_session, "nonexistentword")

    assert ids == set()
