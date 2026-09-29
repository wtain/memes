"""Precomputes the statistics page's numbers into the statistics_snapshots table so
GET /api/diagnostics/statistics is a single-row read. Idempotent; safe to re-run anytime.

Runs hourly (scheduler.jobs), on demand from /admin/batches, and -- via refresh_snapshot()
-- best-effort after every other wrapped batch (batch/run_wrapper.py). See
docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.
"""
import argparse
import asyncio
import uuid

from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.services.statistics_snapshot_service import refresh_corpus_snapshot
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env
from repository.statistics_snapshots import StatisticsSnapshotsRepository
from Storage.db import AsyncSessionLocal


async def refresh_snapshot() -> None:
    """Compute the 'corpus' snapshot and commit it. If the query fails nothing is committed
    and the previous snapshot stays in place (stale beats missing)."""
    async with AsyncSessionLocal() as session:
        await refresh_corpus_snapshot(
            DiagnosticsRepository(session),
            StatisticsSnapshotsRepository(session),
        )
        await session.commit()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await refresh_snapshot()
    else:
        async with tracked_run(kind="statistics", trigger=trigger):
            await refresh_snapshot()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
