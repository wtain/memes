"""
Integration tests for repository/meme_text_ratings.py. Requires a live PostgreSQL instance with pgvector --
see tests/integration/conftest.py.
"""
import inspect
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from repository import meme_text_ratings
from repository.meme_text_ratings import MemeTextRatingRepository
from Storage.models import Image, MemeTextRating

HASH = "a" * 64


async def _image(session, status="active") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def _fields(image, **overrides):
    values = dict(query_hash=HASH, query_text="my manager scheduled a meeting", image_id=image.id, grade=2, rank=3,
                  reading="parsed", parser_version="p3-x", taxonomy_version=2, degraded=False, fallback="none",
                  language="en")
    values.update(overrides)
    return values


async def _count(session, query_hash=HASH) -> int:
    return (await session.execute(
        select(func.count()).select_from(MemeTextRating).where(MemeTextRating.query_hash == query_hash))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_inserts_then_updates_the_same_row(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)

    assert await repo.upsert(**_fields(image, grade=2)) == 2
    assert await repo.upsert(**_fields(image, grade=0, rank=5, reading="edited", degraded=True, fallback="timeout")) == 0

    assert await _count(db_session) == 1
    row = (await db_session.execute(select(MemeTextRating).where(MemeTextRating.image_id == image.id))).scalar_one()
    assert (row.grade, row.rank, row.reading, row.degraded, row.fallback) == (0, 5, "edited", True, "timeout")
    assert row.updated_at >= row.rated_at


@pytest.mark.asyncio(loop_scope="session")
async def test_the_same_image_can_be_rated_for_different_messages(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image, query_hash="a" * 64))
    await repo.upsert(**_fields(image, query_hash="b" * 64, grade=1))

    assert await _count(db_session, "a" * 64) == 1 and await _count(db_session, "b" * 64) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_grades_for_lists_one_message_ordered_by_image(db_session):
    first, second, other = await _image(db_session), await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first, grade=2))
    await repo.upsert(**_fields(second, grade=0))
    await repo.upsert(**_fields(other, query_hash="c" * 64, grade=1))

    grades = await repo.grades_for(HASH)

    assert sorted(grades) == sorted([(first.id, 2), (second.id, 0)])
    assert grades == sorted(grades, key=lambda pair: str(pair[0]))
    assert await repo.grades_for("d" * 64) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_removes_only_the_named_rating(db_session):
    first, second = await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first))
    await repo.upsert(**_fields(second))

    assert await repo.delete(HASH, first.id) is True
    assert await repo.delete(HASH, first.id) is False
    assert [image_id for image_id, _ in await repo.grades_for(HASH)] == [second.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_forget_text_clears_the_text_and_keeps_grades_and_ids(db_session):
    first, second = await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first, grade=2))
    await repo.upsert(**_fields(second, grade=1))
    await repo.upsert(**_fields(first, query_hash="e" * 64, query_text="another message"))

    assert await repo.forget_text(HASH) == 2

    rows = (await db_session.execute(
        select(MemeTextRating).where(MemeTextRating.query_hash == HASH))).scalars().all()
    assert {(row.image_id, row.grade) for row in rows} == {(first.id, 2), (second.id, 1)}
    assert all(row.query_text is None for row in rows)
    other = (await db_session.execute(
        select(MemeTextRating).where(MemeTextRating.query_hash == "e" * 64))).scalar_one()
    assert other.query_text == "another message"


@pytest.mark.asyncio(loop_scope="session")
async def test_rating_again_after_forgetting_stores_the_text_again(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image))
    await repo.forget_text(HASH)
    await repo.upsert(**_fields(image, grade=1))

    row = (await db_session.execute(select(MemeTextRating).where(MemeTextRating.image_id == image.id))).scalar_one()
    assert row.query_text == "my manager scheduled a meeting"


@pytest.mark.asyncio(loop_scope="session")
async def test_image_is_active_only_for_active_images(db_session):
    active, pending = await _image(db_session), await _image(db_session, status="pending")
    repo = MemeTextRatingRepository(db_session)

    assert await repo.image_is_active(active.id) is True
    assert await repo.image_is_active(pending.id) is False
    assert await repo.image_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_all_ratings_returns_plain_rows_oldest_first(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image, query_hash="1" * 64))
    await repo.upsert(**_fields(image, query_hash="2" * 64, grade=0))

    rows = [row for row in await repo.all_ratings() if row.image_id == image.id]

    assert [row.query_hash for row in rows] == ["1" * 64, "2" * 64]
    assert rows[0].grade == 2 and rows[1].grade == 0 and rows[0].language == "en"
    assert isinstance(rows[0].rated_at, str)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("overrides", [{"grade": 3}, {"grade": -1}, {"rank": 0}, {"reading": "guessed"}])
async def test_the_database_rejects_invalid_values(db_session, overrides):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.upsert(**_fields(image, **overrides))


@pytest.mark.asyncio(loop_scope="session")
async def test_ratings_cascade_when_the_image_is_deleted(db_session):
    image = await _image(db_session)
    await MemeTextRatingRepository(db_session).upsert(**_fields(image))

    await db_session.delete(image)
    await db_session.flush()

    assert await _count(db_session) == 0


def test_the_repository_never_commits():
    assert "commit(" not in inspect.getsource(meme_text_ratings)
