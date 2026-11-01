"""Discovery report queries (epic meme-popularity, S4): memes that keep coming back and how visible they are.

Read only: nothing here writes or commits. Design:
docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s4-discovery-report-design.md.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

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
        WHERE {OPEN_EDGE_SQL} AND a.source = ANY(CAST(:sources AS text[])) AND a.weight > 0
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
    order = _MEME_ORDER[sort] if available else _MEME_ORDER["redrops_window"]
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
