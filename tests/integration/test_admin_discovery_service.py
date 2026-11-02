import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

from Backend.app.services.discovery_report_service import DiscoveryReportService
from Backend.app.types.generated.discoverymemes import Schema as DiscoveryMemes
from Backend.app.types.generated.discoverymisses import Schema as DiscoveryMisses
from Backend.app.types.generated.discoveryqueries import Schema as DiscoveryQueries
from repository.discovery_report import DEFAULT_SOURCES
from Storage.models import (MemeImpression, PopularityAbsorption, SearchHistory, SearchHistoryTag,
                            SearchNotFound)
from tests.integration.test_popularity_repository import make_image

pytestmark = pytest.mark.asyncio(loop_scope="session")

MEME_ARGS = dict(window_days=30, sources=DEFAULT_SOURCES, min_redrops=1, max_impressions=None, sort="per_100", limit=50)


async def edge(session, winner, source="hash_cross_corpus", created_at=None):
    values = dict(kind="absorb", source=source, winner_image_id=winner.id, loser_image_id=None, weight=1)
    if created_at is not None:
        values["created_at"] = created_at
    await session.execute(insert(PopularityAbsorption).values(**values))


async def test_a_re_drop_without_impressions_is_infinite_and_validates_as_json(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    result = await DiscoveryReportService(db_session).memes(**MEME_ARGS)
    item = result["items"][0]
    assert item["id"] == str(img.id) and item["imageUrl"] == f"/api/images/{img.id}"
    assert item["per100"] is None and item["per100Infinite"] is True
    assert result["impressionsAvailable"] is True and result["windowDays"] == 30
    assert item["sources"] == [{"source": "hash_cross_corpus", "count": 1}]
    DiscoveryMemes.model_validate(result).model_dump_json()  # no Infinity anywhere


async def test_a_finite_ratio_is_a_number(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    db_session.add_all([MemeImpression(image_id=img.id, rank=1, surface="search") for _ in range(4)])
    await db_session.flush()
    item = (await DiscoveryReportService(db_session).memes(**MEME_ARGS))["items"][0]
    assert item["per100"] == 25.0 and item["per100Infinite"] is False and item["impressions"] == 4


async def test_without_the_impressions_table_the_fields_are_null_not_an_error(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    await db_session.execute(text("DROP TABLE meme_impressions"))
    result = await DiscoveryReportService(db_session).memes(**{**MEME_ARGS, "sort": "per_100"})
    item = result["items"][0]
    assert result["impressionsAvailable"] is False
    assert item["impressions"] is None and item["impressionsWindow"] is None
    assert item["per100"] is None and item["per100Infinite"] is False


async def test_queries_map_tags_and_fallbacks_to_lists(db_session):
    db_session.add(SearchHistory(searched_at=datetime.now(timezone.utc), query="Funny  Cat", client="web", result_count=0,
                                 kind="search", tags=[SearchHistoryTag(category="topic", value="work"),
                                                      SearchHistoryTag(category="emotion", value="joy")]))
    await db_session.flush()
    result = await DiscoveryReportService(db_session).queries(
        kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=50)
    item = result["items"][0]
    assert item["query"] == "funny cat" and item["tags"] == ["emotion:joy", "topic:work"] and item["fallbacks"] == []
    assert item["times"] == 1 and item["web"] == 1 and item["avgResults"] == 0.0
    DiscoveryQueries.model_validate(result)


async def test_misses_carry_status_and_cutoff(db_session):
    db_session.add(SearchNotFound(search_history_id=uuid.uuid4(),
                                  created_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    db_session.add(SearchHistory(searched_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), query="lost",
                                 client="android", result_count=7, kind="search"))
    await db_session.flush()
    result = await DiscoveryReportService(db_session).misses(
        gap_minutes=5, kind="search", since_days=None, min_times=1, explicit_only=False, sort="times", limit=50)
    assert result["status"] == "ok" and result["cutoff"].startswith("2026-09-01") and result["gapMinutes"] == 5
    assert [i["query"] for i in result["items"]] == ["lost"] and result["items"][0]["searches"] == 1.0
    DiscoveryMisses.model_validate(result)


async def test_the_status_is_reported_when_there_is_no_outcome_data(db_session):
    result = await DiscoveryReportService(db_session).misses(
        gap_minutes=5, kind="search", since_days=None, min_times=1, explicit_only=False, sort="times", limit=50)
    assert result["status"] == "no_outcomes" and result["cutoff"] is None and result["items"] == []


async def test_the_transaction_is_read_only_after_the_service_ran(db_session):
    await make_image(db_session, "a.jpg")   # statements already ran in this transaction
    await DiscoveryReportService(db_session).queries(
        kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=1)
    with pytest.raises(DBAPIError) as raised:
        await db_session.execute(text("INSERT INTO search_not_found (search_history_id) VALUES (gen_random_uuid())"))
    assert "read-only" in str(raised.value).lower()


async def test_nothing_is_logged_with_search_text(db_session, caplog):
    db_session.add(SearchHistory(searched_at=datetime.now(timezone.utc), query="secret quokka", client="web",
                                 result_count=0, kind="search"))
    await db_session.flush()
    with caplog.at_level(logging.DEBUG):
        await DiscoveryReportService(db_session).queries(
            kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=50)
    assert "secret quokka" not in caplog.text
