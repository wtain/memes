"""Read-only discovery report (epic meme-popularity, S4): which memes keep coming back through ingestion and how
visible they are, and which searches found nothing or little.

    python -m tools.discovery_report memes   --env general
    python -m tools.discovery_report queries --env general --kind all --include-degraded
    python -m tools.discovery_report misses  --env general --gap-minutes 5

Reads DATABASE_URL_READONLY from the shell (never the main DATABASE_URL), opens its own short-lived read-only
connection, refuses a role that can write, never imports Storage.db and never prints the database URL. Query text is
printed to stdout only; nothing is logged and no file is written unless --csv is given. How to read the columns:
docs/discovery-report.md. Design: docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s4-discovery-report-design.md.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from repository.discovery_report import (DEFAULT_SOURCES, MEME_SORTS, MISS_SORTS, QUERY_KINDS, QUERY_SORTS, MemeRow,
                                         MissRow, QueryRow, memes_report, misses_report, queries_report)
from repository.meme_impressions import window_days
from Storage.models import PopularitySource
from tools import rollout_preflight as rp

MEME_HEADERS = ["id", "filename", "redrops", "redrops_window", "sources", "popularity", "saves", "impressions",
                "impressions_window", "last_back", "per_100"]
QUERY_HEADERS = ["query", "tags", "kind", "times", "first_at", "last_at", "min_results", "avg_results", "web",
                 "android", "degraded", "fallbacks"]
MISS_HEADERS = ["query", "tags", "kind", "times", "explicit", "searches", "first_at", "last_at", "web", "android"]


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _not_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def _sources(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    unknown = [name for name in names if name not in PopularitySource.ALL]
    if not names or unknown:
        raise argparse.ArgumentTypeError(
            f"unknown source(s) {', '.join(unknown) or value!r}; choose from {', '.join(PopularitySource.ALL)}")
    return names


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", required=True, choices=["metal", "general", "it"])
    common.add_argument("--limit", type=_positive, default=50)
    common.add_argument("--csv", metavar="PATH", help="also write the rows as CSV (UTF-8)")
    parser = argparse.ArgumentParser(description="Read-only discovery report: memes that keep coming back, "
                                                 "and searches that found little")
    commands = parser.add_subparsers(dest="command", required=True)
    memes = commands.add_parser("memes", parents=[common], help="memes that came back and how visible they are")
    memes.add_argument("--window-days", type=_positive, default=window_days(),
                       help="default: popularity.impressions.window_days")
    memes.add_argument("--sources", type=_sources, default=None,
                       help=f"comma list; default: every source except adjustment ({', '.join(DEFAULT_SOURCES)})")
    memes.add_argument("--min-redrops", type=_not_negative, default=1, help="re-drops in the window (default 1)")
    memes.add_argument("--max-impressions", type=_not_negative, default=None)
    memes.add_argument("--sort", choices=MEME_SORTS, default="per_100")
    queries = commands.add_parser("queries", parents=[common], help="searches that found nothing or little")
    queries.add_argument("--kind", choices=QUERY_KINDS, default="search")
    queries.add_argument("--max-results", type=_not_negative, default=3)
    queries.add_argument("--include-degraded", action="store_true")
    queries.add_argument("--since-days", type=_positive, default=None)
    queries.add_argument("--min-times", type=_positive, default=1)
    queries.add_argument("--sort", choices=QUERY_SORTS, default="times")
    misses = commands.add_parser("misses", parents=[common], help="search sessions that ended without a save")
    misses.add_argument("--gap-minutes", type=_positive, default=5, help="pause that ends a session (default 5)")
    misses.add_argument("--kind", choices=QUERY_KINDS, default="search")
    misses.add_argument("--since-days", type=_positive, default=None)
    misses.add_argument("--min-times", type=_positive, default=1)
    misses.add_argument("--explicit-only", action="store_true", help="only sessions with a web 'didn't find it' press")
    misses.add_argument("--sort", choices=MISS_SORTS, default="times")
    return parser


def _stamp(value) -> str:
    return "" if value is None else value.strftime("%Y-%m-%d %H:%M")


def _ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "inf" if value == float("inf") else f"{value:.1f}"


def meme_table(rows: list[MemeRow], available: bool) -> tuple[list[str], list[list[str]]]:
    def count(value) -> str:
        return "n/a" if value is None else str(value)

    return MEME_HEADERS, [[
        str(row.image_id), row.filename or "", str(row.redrops), str(row.redrops_window),
        ",".join(f"{name}:{n}" for name, n in sorted(row.sources.items())), str(row.popularity), str(row.saves),
        count(row.impressions), count(row.impressions_window), _stamp(row.last_back), _ratio(row.per_100)]
        for row in rows]


def query_table(rows: list[QueryRow]) -> tuple[list[str], list[list[str]]]:
    return QUERY_HEADERS, [[
        row.query or "(no text)", row.tags, row.kind, str(row.times), _stamp(row.first_at), _stamp(row.last_at),
        str(row.min_results), f"{row.avg_results:.1f}", str(row.web), str(row.android), str(row.degraded),
        row.fallbacks] for row in rows]


def miss_table(rows: list[MissRow]) -> tuple[list[str], list[list[str]]]:
    return MISS_HEADERS, [[
        row.query or "(no text)", row.tags, row.kind, str(row.times), str(row.explicit), f"{row.searches:.1f}",
        _stamp(row.first_at), _stamp(row.last_at), str(row.web), str(row.android)] for row in rows]


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max([len(header)] + [len(row[i]) for row in rows]) for i, header in enumerate(headers)]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()

    return "\n".join([line(headers), "  ".join("-" * width for width in widths)] + [line(row) for row in rows])


def write_csv(path, headers: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


async def _run_session(session, args, url: str | None = None) -> int:
    """Everything after the connection is open; split from _run so tests can drive it on a prepared session."""
    if url is not None:
        status, detail = rp.target_check(args.env, url)
        if status != rp.OK:
            print(f"warning: {detail}", file=sys.stderr)
    status, detail = await rp.role_is_read_only(session)
    if status == rp.FAIL:
        print(detail, file=sys.stderr)
        return 2
    try:
        if args.command == "memes":
            sources = args.sources or DEFAULT_SOURCES
            rows, available = await memes_report(
                session, window_days=args.window_days, sources=sources, min_redrops=args.min_redrops,
                max_impressions=args.max_impressions, sort=args.sort, limit=args.limit)
            headers, cells = meme_table(rows, available)
            title = (f"Discovery report: memes ({args.env}, window {args.window_days} days, "
                     f"sources: {', '.join(sources)}, {len(rows)} rows)")
            note = None if available else "impressions are n/a: the meme_impressions table does not exist here yet"
        elif args.command == "misses":
            report = await misses_report(
                session, gap_minutes=args.gap_minutes, kind=args.kind, since_days=args.since_days,
                min_times=args.min_times, explicit_only=args.explicit_only, sort=args.sort, limit=args.limit)
            rows = report.rows
            headers, cells = miss_table(rows)
            cutoff = "n/a" if report.cutoff is None else report.cutoff.strftime("%Y-%m-%d")
            title = (f"Discovery report: misses ({args.env}, kind {args.kind}, gap {args.gap_minutes} min, "
                     f"outcome data since {cutoff}, {len(rows)} rows)")
            note = {"no_schema": "the S6 migration has not run here yet: no outcome data to report",
                    "no_outcomes": "no linked save or 'didn't find it' press has been recorded yet",
                    "ok": "sessions are a time-gap approximation; sessions before the outcome data began are invisible",
                    }[report.status]
        else:
            rows = await queries_report(
                session, kind=args.kind, max_results=args.max_results, include_degraded=args.include_degraded,
                since_days=args.since_days, min_times=args.min_times, sort=args.sort, limit=args.limit)
            headers, cells = query_table(rows)
            since = "all time" if args.since_days is None else f"last {args.since_days} days"
            title = (f"Discovery report: queries ({args.env}, kind {args.kind}, up to {args.max_results} results, "
                     f"{since}, {len(rows)} rows)")
            note = "a low-result row right after a higher one from the same client is skipped (tail-page rule, approximate)"
    except ValueError as exc:  # our own validation messages: they never contain query text
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(title)
    print(format_table(headers, cells))
    if note:
        print(f"note: {note}")
    if args.csv:
        write_csv(args.csv, headers, cells)
        if args.command in ("queries", "misses"):
            print(f"note: {args.csv} contains search text; keep it out of the repository", file=sys.stderr)
        else:
            print(f"wrote {args.csv}", file=sys.stderr)
    return 0


async def _run(url: str, args) -> int:
    engine = create_async_engine(url, connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    try:
        async with AsyncSession(engine) as session:
            return await _run_session(session, args, url)
    finally:
        await engine.dispose()


def configure_output(stream) -> None:
    """Query text can be any language: a redirected Windows stdout defaults to cp1252 and would turn it into '?'."""
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = os.environ.get("DATABASE_URL_READONLY")
    if not url:
        print("DATABASE_URL_READONLY is not set in the shell (the read-only role's URL; never the main DATABASE_URL)",
              file=sys.stderr)
        return 2
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    configure_output(sys.stdout)
    try:
        return asyncio.run(_run(url, args))
    except ValueError as exc:  # our own validation messages: they never contain query text
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # The exception type only: a database error echoes statement parameters, which can hold query text.
        print(rp.scrub(f"could not run the report: {type(exc).__name__}", url), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
