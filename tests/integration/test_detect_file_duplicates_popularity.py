"""detect_file_duplicates records its keeper as the winner (epic meme-popularity, P2)."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from batch.detect_file_duplicates import flag_cluster_duplicates
from metrics.listener import SimpleMetricsListener
from repository.image_extras import ImageExtrasRepository
from repository.popularity import PopularityRepository
from Storage.models import ImageExtras, PopularityAbsorption
from tests.integration.test_popularity_repository import make_image, pop


async def _three(db_session):
    return [await make_image(db_session, n) for n in ("keep.jpg", "d1.jpg", "d2.jpg")]


async def _flag_row(db_session, image):
    return (await db_session.execute(select(ImageExtras).where(ImageExtras.image_id == image.id))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_keeper_rises_by_the_number_flagged(db_session):
    keeper, d1, d2 = await _three(db_session)
    metrics = SimpleMetricsListener()
    await flag_cluster_duplicates(db_session, ImageExtrasRepository(db_session), keeper.id, [d1.id, d2.id], metrics)
    assert [await pop(db_session, i) for i in (keeper, d1, d2)] == [3, 0, 0]
    edges = (await db_session.execute(select(PopularityAbsorption))).scalars().all()
    assert len(edges) == 2 and {e.source for e in edges} == {"file_duplicates"}
    assert metrics.counters_dict()["flagged"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_error_still_flags(db_session):
    keeper, d1, d2 = await _three(db_session)
    metrics = SimpleMetricsListener()
    with patch.object(PopularityRepository, "absorb", side_effect=RuntimeError("boom")):
        await flag_cluster_duplicates(
            db_session, ImageExtrasRepository(db_session), keeper.id, [d1.id, d2.id], metrics)
    assert (await _flag_row(db_session, d1)).flagged is True
    assert (await _flag_row(db_session, d2)).flagged is True
    counters = metrics.counters_dict()
    assert counters["popularity_skipped"] == 2 and counters["flagged"] == 2
    assert [await pop(db_session, i) for i in (keeper, d1, d2)] == [1, 1, 1]


@pytest.mark.asyncio(loop_scope="session")
async def test_flags_have_no_reason(db_session):
    keeper, d1, _ = await _three(db_session)
    await flag_cluster_duplicates(
        db_session, ImageExtrasRepository(db_session), keeper.id, [d1.id], SimpleMetricsListener())
    row = await _flag_row(db_session, d1)
    assert row.flagged is True
    assert row.flagged_reason is None
