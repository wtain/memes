"""Discovery report for the admin page (epic meme-popularity, S7): maps the report dataclasses to the wire shape.

Read only: the transaction is switched to READ ONLY first, so nothing here can write. Search text is never logged."""
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.discovery_report import memes_report, misses_report, queries_report


def _split(value: str) -> list[str]:
    return value.split(",") if value else []


def _stamp(value) -> str | None:
    return None if value is None else value.isoformat()


def _ratio(per_100: float | None) -> tuple[float | None, bool]:
    if per_100 is None:
        return None, False
    if math.isinf(per_100):
        return None, True
    return float(per_100), False


class DiscoveryReportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _read_only(self) -> None:
        await self.session.execute(text("SET TRANSACTION READ ONLY"))

    async def memes(self, *, window_days: int, sources: tuple[str, ...], min_redrops: int,
                    max_impressions: int | None, sort: str, limit: int) -> dict:
        await self._read_only()
        rows, available = await memes_report(
            self.session, window_days=window_days, sources=sources, min_redrops=min_redrops,
            max_impressions=max_impressions, sort=sort, limit=limit)
        items = []
        for row in rows:
            per_100, infinite = _ratio(row.per_100)
            items.append({
                "id": str(row.image_id), "imageUrl": f"/api/images/{row.image_id}", "filename": row.filename,
                "redrops": row.redrops, "redropsWindow": row.redrops_window,
                "sources": [{"source": name, "count": count} for name, count in sorted(row.sources.items())],
                "popularity": row.popularity, "saves": row.saves, "impressions": row.impressions,
                "impressionsWindow": row.impressions_window, "lastBack": _stamp(row.last_back),
                "per100": per_100, "per100Infinite": infinite})
        return {"windowDays": window_days, "sources": list(sources), "impressionsAvailable": available, "items": items}

    async def queries(self, *, kind: str, max_results: int, include_degraded: bool, since_days: int | None,
                      min_times: int, sort: str, limit: int) -> dict:
        await self._read_only()
        rows = await queries_report(
            self.session, kind=kind, max_results=max_results, include_degraded=include_degraded,
            since_days=since_days, min_times=min_times, sort=sort, limit=limit)
        return {"kind": kind, "items": [{
            "query": row.query, "tags": _split(row.tags), "kind": row.kind, "times": row.times,
            "firstAt": _stamp(row.first_at), "lastAt": _stamp(row.last_at), "minResults": row.min_results,
            "avgResults": row.avg_results, "web": row.web, "android": row.android, "degraded": row.degraded,
            "fallbacks": _split(row.fallbacks)} for row in rows]}

    async def misses(self, *, gap_minutes: int, kind: str, since_days: int | None, min_times: int,
                     explicit_only: bool, sort: str, limit: int) -> dict:
        await self._read_only()
        report = await misses_report(
            self.session, gap_minutes=gap_minutes, kind=kind, since_days=since_days, min_times=min_times,
            explicit_only=explicit_only, sort=sort, limit=limit)
        return {"status": report.status, "cutoff": _stamp(report.cutoff), "gapMinutes": gap_minutes, "kind": kind,
                "items": [{
                    "query": row.query, "tags": _split(row.tags), "kind": row.kind, "times": row.times,
                    "explicit": row.explicit, "searches": row.searches, "firstAt": _stamp(row.first_at),
                    "lastAt": _stamp(row.last_at), "web": row.web, "android": row.android} for row in report.rows]}
