"""Discovery report queries (epic meme-popularity, S4): memes that keep coming back and how visible they are.

Read only: nothing here writes or commits. Design:
docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s4-discovery-report-design.md.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.popularity import OPEN_EDGE_SQL
from Storage.models import PopularitySource

DEFAULT_SOURCES = tuple(source for source in PopularitySource.ALL if source != PopularitySource.ADJUSTMENT)
MEME_SORTS = ("per_100", "redrops", "redrops_window", "popularity", "saves", "impressions")

_TIE = "redrops_window DESC, redrops DESC"
_MEME_ORDER = {
    "per_100": f"per_100 DESC NULLS LAST, {_TIE}",
    "redrops": f"redrops DESC, redrops_window DESC",
    "redrops_window": _TIE,
    "popularity": f"popularity DESC, {_TIE}",
    "saves": f"saves DESC, {_TIE}",
    "impressions": f"impressions DESC NULLS LAST, {_TIE}",
}


@dataclass(frozen=True)
class MemeRow:
    image_id: uuid.UUID
    filename: str | None
    redrops: int
    redrops_window: int
    sources: dict[str, int]
    popularity: int
    saves: int
    impressions: int | None          # None: the meme_impressions table does not exist yet
    impressions_window: int | None
    last_back: datetime | None
    per_100: float | None            # re-drops in the window per 100 impressions in the window; inf = never shown


# Roots are the true roots of the forest (winners of an open edge that are not losers of one), so a tree whose
# counted edges sit below an excluded edge is still found. UNION (not UNION ALL) so a cycle cannot loop.
_MEMES_SQL = f"""
    WITH RECURSIVE
    roots AS (
        SELECT DISTINCT a.winner_image_id AS root_id FROM popularity_absorptions a
        WHERE {OPEN_EDGE_SQL}
          AND NOT EXISTS (SELECT 1 FROM popularity_absorptions l
                          WHERE l.kind = 'absorb' AND l.reversed_by_id IS NULL
                            AND l.loser_image_id = a.winner_image_id)
    ),
    tree(root_id, id) AS (
        SELECT root_id, root_id FROM roots
        UNION
        SELECT t.root_id, a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE {OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL
    ),
    edges AS (
        SELECT t.root_id, a.source, a.weight, a.created_at
        FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE {OPEN_EDGE_SQL} AND a.source = ANY(CAST(:sources AS text[])) AND a.weight <> 0
    ),
    by_source AS (SELECT root_id, source, sum(weight) AS n FROM edges GROUP BY root_id, source),
    agg AS (
        SELECT root_id, sum(weight) AS redrops,
               coalesce(sum(weight) FILTER (WHERE created_at >= now() - make_interval(days => CAST(:days AS int))), 0)
                   AS redrops_window,
               max(created_at) AS last_back
        FROM edges GROUP BY root_id
    ),
    breakdown AS (SELECT root_id, jsonb_object_agg(source, n) AS sources FROM by_source GROUP BY root_id),
    saves AS (SELECT t.root_id, count(*) AS n FROM meme_saves s JOIN tree t ON s.image_id = t.id GROUP BY t.root_id),
    imp AS ({{imp_select}})
    SELECT i.id AS image_id, i.filename, agg.redrops, agg.redrops_window, breakdown.sources, i.popularity,
           coalesce(saves.n, 0) AS saves, {{impressions}} AS impressions, {{impressions_window}} AS impressions_window,
           agg.last_back, {{per_100}} AS per_100
    FROM agg
    JOIN breakdown ON breakdown.root_id = agg.root_id
    JOIN images i ON i.id = agg.root_id AND i.status = 'active'
    LEFT JOIN image_extras x ON x.image_id = i.id
    LEFT JOIN saves ON saves.root_id = agg.root_id
    LEFT JOIN imp ON imp.root_id = agg.root_id
    WHERE coalesce(x.flagged, false) = false
      AND agg.redrops_window >= :min_redrops
      {{max_impressions}}
    ORDER BY {{order}}, i.id
    LIMIT :limit
"""

_IMP_AVAILABLE = """
        SELECT t.root_id, count(*) AS n,
               count(*) FILTER (WHERE s.shown_at >= now() - make_interval(days => CAST(:days AS int))) AS nw
        FROM meme_impressions s JOIN tree t ON s.image_id = t.id GROUP BY t.root_id
"""
_IMP_MISSING = "SELECT NULL::uuid AS root_id, NULL::bigint AS n, NULL::bigint AS nw WHERE false"
_PER_100 = """CASE WHEN agg.redrops_window = 0 THEN 0::float8
                 WHEN coalesce(imp.nw, 0) = 0 THEN 'Infinity'::float8
                 ELSE agg.redrops_window * 100.0 / imp.nw END"""


async def _impressions_available(session: AsyncSession) -> bool:
    return (await session.execute(text("SELECT to_regclass('public.meme_impressions') IS NOT NULL"))).scalar_one()


async def memes_report(session: AsyncSession, *, window_days: int, sources: tuple[str, ...] = DEFAULT_SOURCES,
                       min_redrops: int = 1, max_impressions: int | None = None, sort: str = "per_100",
                       limit: int = 50) -> tuple[list[MemeRow], bool]:
    """Memes with at least one counted open edge in their duplicate tree, with saves and impressions over the whole
    tree. Returns the rows and whether impression data exists. Active, unflagged roots only."""
    unknown = [source for source in sources if source not in PopularitySource.ALL]
    if unknown:
        raise ValueError(f"unknown popularity source(s): {', '.join(unknown)}")
    if sort not in _MEME_ORDER:
        raise ValueError(f"unknown sort {sort!r}; choose one of {', '.join(MEME_SORTS)}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    available = await _impressions_available(session)
    if max_impressions is not None and not available:
        raise ValueError("max_impressions needs the meme_impressions table, which does not exist yet")
    # Without impression data only the sorts that depend on it fall back; an explicit other sort is honoured.
    needs_impressions = sort in ("per_100", "impressions")
    order = _MEME_ORDER["redrops_window"] if needs_impressions and not available else _MEME_ORDER[sort]
    statement = _MEMES_SQL.format(
        imp_select=_IMP_AVAILABLE if available else _IMP_MISSING,
        impressions="coalesce(imp.n, 0)" if available else "NULL::bigint",
        impressions_window="coalesce(imp.nw, 0)" if available else "NULL::bigint",
        per_100=_PER_100 if available else "NULL::float8",
        max_impressions="AND coalesce(imp.nw, 0) <= :max_impressions" if max_impressions is not None else "",
        order=order)
    params = {"sources": list(sources), "days": window_days, "min_redrops": min_redrops, "limit": limit}
    if max_impressions is not None:
        params["max_impressions"] = max_impressions
    rows = (await session.execute(text(statement), params)).all()
    return [MemeRow(
        image_id=row.image_id, filename=row.filename, redrops=int(row.redrops), redrops_window=int(row.redrops_window),
        sources={name: int(count) for name, count in row.sources.items()}, popularity=row.popularity,
        saves=int(row.saves), impressions=None if row.impressions is None else int(row.impressions),
        impressions_window=None if row.impressions_window is None else int(row.impressions_window),
        last_back=row.last_back, per_100=row.per_100) for row in rows], available


QUERY_KINDS = ("search", "meme_text", "all")
QUERY_SORTS = ("times", "last", "min_results")
TAIL_PAGE_MINUTES = 30

_QUERY_ORDER = {
    "times": "times DESC, last_at DESC",
    "last": "last_at DESC, times DESC",
    "min_results": "min_results ASC, times DESC",
}


@dataclass(frozen=True)
class QueryRow:
    query: str          # normalised (lower-cased, whitespace collapsed); "" when the search had no text
    tags: str           # "category:value" pairs, sorted, joined by ","; "" when none
    kind: str
    times: int
    first_at: datetime
    last_at: datetime
    min_results: int
    avg_results: float
    web: int
    android: int
    degraded: int
    fallbacks: str      # distinct fallback reasons joined by ","


# Every page of a search writes its own row and the row does not say whether it came from a cursor, so a low count
# right after a higher one (same normalised query, same client, within TAIL_PAGE_MINUTES) is the last page of a search
# that worked, not a failure. recent_max includes the row itself, so `recent_max > result_count` means a higher
# count came before it. An empty page is always a failure.
_QUERIES_SQL = f"""
    WITH h AS (
        SELECT s.id, s.searched_at, s.kind, s.client, s.result_count, s.degraded, s.fallback,
               lower(regexp_replace(btrim(coalesce(s.query, '')), '\\s+', ' ', 'g')) AS qn,
               coalesce((SELECT string_agg(t.category || ':' || t.value, ',' ORDER BY t.category, t.value)
                         FROM search_history_tags t WHERE t.search_id = s.id), '') AS tag_text
        FROM search_history s
        WHERE (:kind = 'all' OR s.kind = :kind)
          AND (CAST(:since AS timestamptz) IS NULL
               OR s.searched_at >= CAST(:since AS timestamptz) - interval '{TAIL_PAGE_MINUTES} minutes')
    ),
    f AS (
        SELECT h.*, max(h.result_count) OVER (
                   PARTITION BY h.kind, h.qn, h.tag_text, h.client ORDER BY h.searched_at
                   RANGE BETWEEN INTERVAL '{TAIL_PAGE_MINUTES} minutes' PRECEDING AND CURRENT ROW) AS recent_max
        FROM h
    )
    SELECT f.qn AS query, f.tag_text AS tags, f.kind, count(*) AS times, min(f.searched_at) AS first_at,
           max(f.searched_at) AS last_at, min(f.result_count) AS min_results,
           avg(f.result_count)::float8 AS avg_results,
           count(*) FILTER (WHERE f.client = 'web') AS web,
           count(*) FILTER (WHERE f.client = 'android') AS android,
           count(*) FILTER (WHERE f.degraded IS TRUE) AS degraded,
           coalesce(string_agg(DISTINCT f.fallback, ','), '') AS fallbacks
    FROM f
    WHERE (CAST(:since AS timestamptz) IS NULL OR f.searched_at >= CAST(:since AS timestamptz))
      AND (f.result_count = 0
           OR (f.result_count <= :max_results AND NOT f.recent_max > f.result_count)
           OR (CAST(:include_degraded AS boolean) AND (f.degraded IS TRUE OR f.fallback IS NOT NULL)))
    GROUP BY f.qn, f.tag_text, f.kind
    HAVING count(*) >= :min_times
    ORDER BY {{order}}, f.qn, f.tag_text
    LIMIT :limit
"""


async def queries_report(session: AsyncSession, *, kind: str = "search", max_results: int = 3,
                         include_degraded: bool = False, since_days: int | None = None, min_times: int = 1,
                         sort: str = "times", limit: int = 50) -> list[QueryRow]:
    """Searches that found nothing or little, grouped by normalised query and facet pairs (see the tail-page rule
    above). Reads search_history only."""
    if kind not in QUERY_KINDS:
        raise ValueError(f"unknown kind {kind!r}; choose one of {', '.join(QUERY_KINDS)}")
    if sort not in _QUERY_ORDER:
        raise ValueError(f"unknown sort {sort!r}; choose one of {', '.join(QUERY_SORTS)}")
    if max_results < 0 or min_times < 1 or limit < 1:
        raise ValueError("max_results must be >= 0, min_times and limit >= 1")
    since = None if since_days is None else datetime.now(timezone.utc) - timedelta(days=since_days)
    statement = _QUERIES_SQL.format(order=_QUERY_ORDER[sort])
    rows = (await session.execute(text(statement), {
        "kind": kind, "since": since, "max_results": max_results, "include_degraded": include_degraded,
        "min_times": min_times, "limit": limit})).all()
    return [QueryRow(
        query=row.query, tags=row.tags, kind=row.kind, times=int(row.times), first_at=row.first_at,
        last_at=row.last_at, min_results=int(row.min_results), avg_results=float(row.avg_results),
        web=int(row.web), android=int(row.android), degraded=int(row.degraded), fallbacks=row.fallbacks)
        for row in rows]


# --- misses view (epic meme-popularity, S6) ---

MISS_SORTS = ("times", "explicit", "last")
_MISS_ORDER = {
    "times": "times DESC, last_at DESC",
    "explicit": "explicit DESC, times DESC, last_at DESC",
    "last": "last_at DESC, times DESC",
}

_SCHEMA_SQL = text("""
    SELECT to_regclass('public.search_not_found') IS NOT NULL
       AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'meme_saves' AND column_name = 'search_history_id')
""")
_CUTOFF_SQL = text("""
    SELECT least((SELECT min(created_at) FROM meme_saves WHERE search_history_id IS NOT NULL),
                 (SELECT min(created_at) FROM search_not_found))
""")

# Sessions: rows of one client and kind, a new session after a pause longer than :gap minutes. Rows with no query
# text and no tags are the browse feed, not searches (S6 A1). A session is open (and not reported) while its last
# row is newer than the gap (A2). The web has no save action, so a web session is a miss only when the button was
# pressed; an unpressed web session says nothing (A6).
_MISSES_SQL = """
    WITH h AS (
        SELECT s.id, s.searched_at, s.kind, s.client,
               lower(regexp_replace(btrim(coalesce(s.query, '')), '\\s+', ' ', 'g')) AS qn,
               coalesce((SELECT string_agg(t.category || ':' || t.value, ',' ORDER BY t.category, t.value)
                         FROM search_history_tags t WHERE t.search_id = s.id), '') AS tag_text
        FROM search_history s
        WHERE (:kind = 'all' OR s.kind = :kind) AND s.searched_at >= :from_at
    ),
    r AS (SELECT * FROM h WHERE qn <> '' OR tag_text <> ''),
    g AS (
        SELECT r.*, CASE WHEN lag(r.searched_at) OVER w IS NULL
                           OR r.searched_at - lag(r.searched_at) OVER w > make_interval(mins => :gap)
                         THEN 1 ELSE 0 END AS starts
        FROM r WINDOW w AS (PARTITION BY r.client, r.kind ORDER BY r.searched_at, r.id)
    ),
    sess AS (
        SELECT g.*, sum(g.starts) OVER (PARTITION BY g.client, g.kind ORDER BY g.searched_at, g.id) AS sid,
               EXISTS (SELECT 1 FROM meme_saves m WHERE m.search_history_id = g.id) AS has_save,
               EXISTS (SELECT 1 FROM search_not_found n WHERE n.search_history_id = g.id) AS has_press
        FROM g
    ),
    agg AS (
        SELECT client, kind, sid, count(*) AS searches, min(searched_at) AS first_at, max(searched_at) AS last_at,
               (array_agg(qn ORDER BY searched_at DESC, id DESC))[1] AS qn,
               (array_agg(tag_text ORDER BY searched_at DESC, id DESC))[1] AS tag_text,
               bool_or(has_save) AS saved, bool_or(has_press) AS pressed
        FROM sess GROUP BY client, kind, sid
    )
    SELECT qn AS query, tag_text AS tags, kind, count(*) AS times, count(*) FILTER (WHERE pressed) AS explicit,
           avg(searches)::float8 AS searches, min(first_at) AS first_at, max(last_at) AS last_at,
           count(*) FILTER (WHERE client = 'web') AS web, count(*) FILTER (WHERE client = 'android') AS android
    FROM agg
    WHERE NOT saved AND last_at <= now() - make_interval(mins => :gap)
      AND (client <> 'web' OR pressed)
      AND (NOT CAST(:explicit_only AS boolean) OR pressed)
    GROUP BY qn, tag_text, kind
    HAVING count(*) >= :min_times
    ORDER BY {order}, qn, tag_text
    LIMIT :limit
"""


@dataclass(frozen=True)
class MissRow:
    query: str
    tags: str
    kind: str
    times: int
    explicit: int
    searches: float
    first_at: datetime
    last_at: datetime
    web: int
    android: int


@dataclass(frozen=True)
class MissesReport:
    rows: list[MissRow]
    status: str            # "ok", "no_schema" (migration not run) or "no_outcomes" (no linked save or press yet)
    cutoff: datetime | None


async def misses_report(session: AsyncSession, *, gap_minutes: int = 5, kind: str = "search",
                        since_days: int | None = None, min_times: int = 1, explicit_only: bool = False,
                        sort: str = "times", limit: int = 50) -> MissesReport:
    """Search sessions that ended without a linked save, grouped by their last query and facet pairs. Reads
    search_history, meme_saves and search_not_found only."""
    if kind not in QUERY_KINDS:
        raise ValueError(f"unknown kind {kind!r}; choose one of {', '.join(QUERY_KINDS)}")
    if sort not in _MISS_ORDER:
        raise ValueError(f"unknown sort {sort!r}; choose one of {', '.join(MISS_SORTS)}")
    if gap_minutes < 1 or min_times < 1 or limit < 1:
        raise ValueError("gap_minutes, min_times and limit must be at least 1")
    if not (await session.execute(_SCHEMA_SQL)).scalar_one():
        return MissesReport([], "no_schema", None)
    cutoff = (await session.execute(_CUTOFF_SQL)).scalar_one()
    if cutoff is None:
        return MissesReport([], "no_outcomes", None)
    from_at = cutoff
    if since_days is not None:
        from_at = max(cutoff, datetime.now(timezone.utc) - timedelta(days=since_days))
    rows = (await session.execute(text(_MISSES_SQL.format(order=_MISS_ORDER[sort])), {
        "kind": kind, "from_at": from_at, "gap": gap_minutes, "explicit_only": explicit_only,
        "min_times": min_times, "limit": limit})).all()
    return MissesReport([MissRow(
        query=row.query, tags=row.tags, kind=row.kind, times=int(row.times), explicit=int(row.explicit),
        searches=float(row.searches), first_at=row.first_at, last_at=row.last_at, web=int(row.web),
        android=int(row.android)) for row in rows], "ok", cutoff)
