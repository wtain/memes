"""Discovery report, misses view (epic meme-popularity, S6): search sessions that ended without a save."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from repository.discovery_report import MISS_SORTS, misses_report
from Storage.models import MemeSave, SearchHistory, SearchHistoryTag, SearchNotFound

pytestmark = pytest.mark.asyncio(loop_scope="session")

ANCHOR = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)   # earliest linked outcome: the cut-off
T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)      # all session rows are well after it and long ended


async def anchor(session):
    session.add(SearchNotFound(search_history_id=uuid.uuid4(), created_at=ANCHOR))
    await session.flush()


async def hist(session, query, at, *, client="web", kind="search", tags=(), count=5):
    row = SearchHistory(searched_at=at, query=query, client=client, result_count=count, kind=kind,
                        tags=[SearchHistoryTag(category=c, value=v) for c, v in tags])
    session.add(row)
    await session.flush()
    return row.id


async def save_for(session, search_id):
    session.add(MemeSave(image_id=uuid.uuid4(), search_history_id=search_id))
    await session.flush()


async def press_for(session, search_id):
    session.add(SearchNotFound(search_history_id=search_id))
    await session.flush()


async def report(session, **kwargs):
    result = await misses_report(session, **kwargs)
    return {(row.query, row.tags): row for row in result.rows}


async def test_an_unsaved_session_is_a_miss_and_a_saved_one_is_not(db_session):
    await anchor(db_session)
    await hist(db_session, "lost", T)
    saved = await hist(db_session, "found", T + timedelta(hours=1))
    await save_for(db_session, saved)
    assert set(await report(db_session)) == {("lost", "")}


async def test_a_save_in_any_row_resolves_the_session(db_session):
    await anchor(db_session)
    first = await hist(db_session, "cat", T)
    await hist(db_session, "cat meme", T + timedelta(minutes=2))
    await save_for(db_session, first)
    assert await report(db_session) == {}


async def test_rows_within_the_gap_form_one_session_and_the_last_query_is_reported(db_session):
    await anchor(db_session)
    await hist(db_session, "ca", T)
    await hist(db_session, "cat", T + timedelta(minutes=4, seconds=59))
    rows = await report(db_session)
    assert set(rows) == {("cat", "")} and rows[("cat", "")].times == 1 and rows[("cat", "")].searches == 2


async def test_a_pause_longer_than_the_gap_starts_a_new_session(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T)
    await hist(db_session, "cat", T + timedelta(minutes=5, seconds=1))
    assert (await report(db_session))[("cat", "")].times == 2


async def test_gap_minutes_is_an_option(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T)
    await hist(db_session, "cat", T + timedelta(minutes=8))
    assert (await report(db_session, gap_minutes=10))[("cat", "")].times == 1


async def test_clients_never_share_a_session(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T, client="web")
    await hist(db_session, "cat", T + timedelta(minutes=1), client="android")
    row = (await report(db_session))[("cat", "")]
    assert row.times == 2 and row.web == 1 and row.android == 1


async def test_browse_rows_are_not_searches_and_do_not_join_sessions(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T)
    await hist(db_session, None, T + timedelta(minutes=1))     # the browse feed right after: not the last query
    await hist(db_session, "  ", T + timedelta(minutes=2))
    await hist(db_session, None, T + timedelta(hours=1))        # a lone browse row is no session at all
    rows = await report(db_session)
    assert set(rows) == {("cat", "")} and rows[("cat", "")].searches == 1 and rows[("cat", "")].times == 1


async def test_a_facet_only_search_is_reported_with_its_tags(db_session):
    await anchor(db_session)
    await hist(db_session, None, T, tags=[("topic", "work"), ("emotion", "joy")])
    assert set(await report(db_session)) == {("", "emotion:joy,topic:work")}


async def test_queries_are_normalised_for_grouping(db_session):
    await anchor(db_session)
    await hist(db_session, "Grumpy  Cat", T)
    await hist(db_session, "grumpy cat", T + timedelta(hours=1))
    assert (await report(db_session))[("grumpy cat", "")].times == 2


async def test_a_press_marks_a_miss_explicit_and_a_save_wins_over_a_press(db_session):
    await anchor(db_session)
    pressed = await hist(db_session, "pressed", T)
    await press_for(db_session, pressed)
    both = await hist(db_session, "both", T + timedelta(hours=1))
    await press_for(db_session, both)
    await save_for(db_session, both)
    rows = await report(db_session)
    assert set(rows) == {("pressed", "")} and rows[("pressed", "")].explicit == 1
    assert set(await report(db_session, explicit_only=True)) == {("pressed", "")}


async def test_a_session_still_inside_the_gap_is_left_out(db_session):
    await anchor(db_session)
    now = datetime.now(timezone.utc)
    await hist(db_session, "open", now - timedelta(minutes=2))
    await hist(db_session, "ended", now - timedelta(minutes=10))
    assert set(await report(db_session)) == {("ended", "")}


async def test_sessions_before_the_first_outcome_are_ignored_and_the_cutoff_is_returned(db_session):
    await anchor(db_session)
    await hist(db_session, "old", ANCHOR - timedelta(days=1))
    await hist(db_session, "new", T)
    result = await misses_report(db_session)
    assert {r.query for r in result.rows} == {"new"} and result.cutoff == ANCHOR


async def test_without_any_outcome_the_status_says_so(db_session):
    await hist(db_session, "cat", T)
    result = await misses_report(db_session)
    assert result.rows == [] and result.status == "no_outcomes" and result.cutoff is None


async def test_missing_schema_is_reported_not_raised(db_session):
    await db_session.execute(text("DROP TABLE search_not_found"))
    result = await misses_report(db_session)
    assert result.rows == [] and result.status == "no_schema"


async def test_kind_filter(db_session):
    await anchor(db_session)
    await hist(db_session, "plain", T)
    await hist(db_session, "message", T, kind="meme_text")
    assert set(await report(db_session)) == {("plain", "")}
    assert set(await report(db_session, kind="meme_text")) == {("message", "")}
    assert set(await report(db_session, kind="all")) == {("plain", ""), ("message", "")}


async def test_since_days_min_times_and_limit(db_session):
    await anchor(db_session)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await hist(db_session, "recent", recent)
    await hist(db_session, "twice", T)
    await hist(db_session, "twice", T + timedelta(hours=1))
    assert set(await report(db_session, since_days=2)) == {("recent", "")}
    assert set(await report(db_session, min_times=2)) == {("twice", "")}
    assert len((await misses_report(db_session, limit=1)).rows) == 1


async def test_sorts(db_session):
    await anchor(db_session)
    await hist(db_session, "a-many", T)
    await hist(db_session, "a-many", T + timedelta(hours=1))
    b = await hist(db_session, "b-pressed", T + timedelta(hours=2))
    await press_for(db_session, b)
    await hist(db_session, "c-latest", T + timedelta(hours=3))
    order = lambda result: [r.query for r in result.rows]
    assert order(await misses_report(db_session, sort="times"))[0] == "a-many"
    assert order(await misses_report(db_session, sort="explicit"))[0] == "b-pressed"
    assert order(await misses_report(db_session, sort="last"))[0] == "c-latest"
    with pytest.raises(ValueError):
        await misses_report(db_session, sort="nope")
    assert MISS_SORTS == ("times", "explicit", "last")


async def test_option_validation(db_session):
    for bad in ({"gap_minutes": 0}, {"min_times": 0}, {"limit": 0}, {"kind": "x"}):
        with pytest.raises(ValueError):
            await misses_report(db_session, **bad)
