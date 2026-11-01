"""Discovery report, queries view (epic meme-popularity, S4): searches that found nothing or little."""
from datetime import datetime, timedelta, timezone

import pytest

from repository.discovery_report import QUERY_KINDS, QUERY_SORTS, queries_report
from Storage.models import SearchHistory, SearchHistoryTag

pytestmark = pytest.mark.asyncio(loop_scope="session")

T = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def ago(**delta):
    return datetime.now(timezone.utc) - timedelta(**delta)


async def hist(session, query, count, *, at=T, client="web", kind="search", tags=(), degraded=None, fallback=None):
    session.add(SearchHistory(
        searched_at=at, query=query, client=client, result_count=count, kind=kind, degraded=degraded,
        fallback=fallback, tags=[SearchHistoryTag(category=c, value=v) for c, v in tags]))
    await session.flush()


async def report(session, **kwargs):
    return {(row.query, row.tags): row for row in await queries_report(session, **kwargs)}


async def test_zero_results_is_a_failure_and_a_good_search_is_not(db_session):
    await hist(db_session, "nothing", 0)
    await hist(db_session, "plenty", 10)
    rows = await report(db_session)
    assert set(rows) == {("nothing", "")}


async def test_low_results_are_included_up_to_max_results(db_session):
    for name, count in (("q0", 0), ("q1", 1), ("q3", 3), ("q4", 4)):
        await hist(db_session, name, count)
    assert {q for q, _ in await report(db_session)} == {"q0", "q1", "q3"}
    assert {q for q, _ in await report(db_session, max_results=0)} == {"q0"}


async def test_queries_are_grouped_after_normalising(db_session):
    for i, text in enumerate(("Funny  Cat", " funny cat ", "FUNNY CAT")):
        await hist(db_session, text, 0, at=T + timedelta(hours=i))
    rows = await report(db_session)
    assert list(rows) == [("funny cat", "")]
    assert rows[("funny cat", "")].times == 3


async def test_facets_split_groups_and_a_facet_only_search_has_no_text(db_session):
    await hist(db_session, "cat", 0)
    await hist(db_session, "cat", 0, tags=[("animal", "cat"), ("mood", "sad")])
    await hist(db_session, None, 0, tags=[("animal", "cat")])
    rows = await report(db_session)
    assert set(rows) == {("cat", ""), ("cat", "animal:cat,mood:sad"), ("", "animal:cat")}


async def test_tail_page_rule(db_session):
    # a: a finished search: 23 results, then its last page of 3, 2 minutes later, same client
    await hist(db_session, "a", 23)
    await hist(db_session, "a", 3, at=T + timedelta(minutes=2))
    # b: the low row comes 31 minutes later: a new search, a failure
    await hist(db_session, "b", 23)
    await hist(db_session, "b", 3, at=T + timedelta(minutes=31))
    # c: another client: a failure
    await hist(db_session, "c", 23, client="web")
    await hist(db_session, "c", 3, client="android", at=T + timedelta(minutes=2))
    # d: the same low count twice is two failures
    await hist(db_session, "d", 2)
    await hist(db_session, "d", 2, at=T + timedelta(minutes=5))
    # e: a higher low count after a lower one counts too
    await hist(db_session, "e", 2)
    await hist(db_session, "e", 3, at=T + timedelta(minutes=1))
    # f: an empty page is always a failure
    await hist(db_session, "f", 23)
    await hist(db_session, "f", 0, at=T + timedelta(minutes=2))
    rows = await report(db_session)
    assert "a" not in {q for q, _ in rows}
    assert rows[("b", "")].times == 1 and rows[("b", "")].min_results == 3
    assert rows[("c", "")].times == 1 and rows[("c", "")].android == 1
    assert rows[("d", "")].times == 2
    assert rows[("e", "")].times == 2
    assert rows[("f", "")].times == 1 and rows[("f", "")].min_results == 0


async def test_kind_filter(db_session):
    await hist(db_session, "typed", 0)
    await hist(db_session, "pasted message", 0, kind="meme_text")
    assert {q for q, _ in await report(db_session)} == {"typed"}
    assert {q for q, _ in await report(db_session, kind="meme_text")} == {"pasted message"}
    assert {q for q, _ in await report(db_session, kind="all")} == {"typed", "pasted message"}


async def test_include_degraded(db_session):
    await hist(db_session, "broken parse", 10, kind="meme_text", degraded=True, fallback="timeout")
    await hist(db_session, "fine parse", 10, kind="meme_text", degraded=False)
    assert await report(db_session, kind="meme_text") == {}
    rows = await report(db_session, kind="meme_text", include_degraded=True)
    assert set(rows) == {("broken parse", "")}
    row = rows[("broken parse", "")]
    assert row.degraded == 1 and row.fallbacks == "timeout"


async def test_clients_are_counted(db_session):
    await hist(db_session, "q", 0, client="web", at=T)
    await hist(db_session, "q", 0, client="web", at=T + timedelta(hours=1))
    await hist(db_session, "q", 0, client="android", at=T + timedelta(hours=2))
    row = (await report(db_session))[("q", "")]
    assert (row.web, row.android, row.times) == (2, 1, 3)
    assert row.first_at == T and row.last_at == T + timedelta(hours=2)
    assert row.avg_results == 0


async def test_since_days_min_times_limit_and_sorts(db_session):
    for days in (5, 6, 7):
        await hist(db_session, "x", 0, at=ago(days=days))
    await hist(db_session, "y", 0, at=ago(days=1))
    for hours in (48, 49, 50):
        await hist(db_session, "z", 1, at=ago(hours=hours))

    def order(rows):
        return [row.query for row in rows]

    assert order(await queries_report(db_session, sort="times")) == ["z", "x", "y"]
    assert order(await queries_report(db_session, sort="last")) == ["y", "z", "x"]
    assert order(await queries_report(db_session, sort="min_results")) == ["x", "y", "z"]
    assert order(await queries_report(db_session)) == ["z", "x", "y"]
    assert set(order(await queries_report(db_session, since_days=3))) == {"y", "z"}
    assert set(order(await queries_report(db_session, min_times=2))) == {"x", "z"}
    assert order(await queries_report(db_session, limit=1)) == ["z"]
    assert set(QUERY_SORTS) == {"times", "last", "min_results"} and "all" in QUERY_KINDS


async def test_invalid_arguments_raise_before_any_sql(db_session):
    for kwargs in ({"kind": "nope"}, {"sort": "nope"}, {"max_results": -1}, {"min_times": 0}, {"limit": 0}):
        with pytest.raises(ValueError):
            await queries_report(db_session, **kwargs)
