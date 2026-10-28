"""images.popularity and the popularity_absorptions log: defaults and database-level guards."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from Storage.models import Image, PopularityAbsorption, PopularityKind, PopularitySource

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _absorb(loser=None, source=PopularitySource.CLUSTER_REVIEW, **extra):
    return PopularityAbsorption(
        kind=PopularityKind.ABSORB, source=source, loser_image_id=loser,
        winner_image_id=uuid.uuid4(), weight=1, **extra)


async def test_a_new_image_defaults_to_popularity_one(db_session):
    image = Image(filename="a.jpg")
    db_session.add(image)
    await db_session.flush()
    await db_session.refresh(image)
    assert image.popularity == 1


async def test_negative_popularity_is_rejected(db_session):
    db_session.add(Image(filename="b.jpg", popularity=-1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_the_open_loser_index_allows_one_open_edge_per_loser(db_session):
    loser = uuid.uuid4()
    first = _absorb(loser)
    db_session.add(first)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_absorb(loser))
            await db_session.flush()

    reverse = PopularityAbsorption(
        kind=PopularityKind.REVERSE, source=first.source, loser_image_id=loser,
        winner_image_id=first.winner_image_id, weight=1, reverses_id=first.id, reason="correction")
    db_session.add(reverse)
    await db_session.flush()
    first.reversed_by_id = reverse.id
    await db_session.flush()

    db_session.add(_absorb(loser))
    await db_session.flush()


async def test_the_shape_check_rejects_a_reverse_row_without_reverses_id(db_session):
    db_session.add(PopularityAbsorption(
        kind=PopularityKind.REVERSE, source=PopularitySource.RECUT,
        winner_image_id=uuid.uuid4(), weight=0, reason="abort"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_a_source_outside_the_eight_is_rejected(db_session):
    db_session.add(_absorb(uuid.uuid4(), source="telepathy"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_dedupe_key_is_unique_but_null_is_repeatable(db_session):
    db_session.add_all([_absorb(source=PopularitySource.FILE_DUPLICATES) for _ in range(2)])
    await db_session.flush()
    db_session.add(_absorb(source=PopularitySource.FILE_DUPLICATES, dedupe_key="run:hash:path"))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_absorb(source=PopularitySource.FILE_DUPLICATES, dedupe_key="run:hash:path"))
            await db_session.flush()


async def test_there_is_no_foreign_key_from_the_log_to_images():
    assert {fk.column.table.name for fk in PopularityAbsorption.__table__.foreign_keys} == {
        "batch_runs", "popularity_absorptions"}
