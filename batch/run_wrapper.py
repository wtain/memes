import argparse
import asyncio
import importlib
import logging
import sys
import uuid

from batch.registry import BatchRegistry
from config.settings import load_env

logger = logging.getLogger(__name__)


async def _refresh_statistics_best_effort() -> None:
    """Refreshes the precomputed statistics snapshot after a successful batch. Best-effort by
    design: any failure (including an import error) is logged and swallowed so it can never
    change the parent batch's outcome or exit code. Scripts invoked directly from a shell
    bypass this wrapper; the hourly build_statistics job covers those. See
    docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md."""
    try:
        from batch.build_statistics import refresh_snapshot
        await refresh_snapshot()
    except Exception:
        logger.warning("post-batch statistics refresh failed", exc_info=True)


async def main() -> None:
    registry = BatchRegistry()
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", choices=registry.all_names(), required=True)
    parser.add_argument("--env", choices=["metal", "general", "it"], required=True)
    parser.add_argument("--trigger", choices=["manual", "scheduled"], required=True)
    parser.add_argument("--run-id", default=None,
                         help="Pre-created run id (admin controller); omitted for the scheduler, "
                              "which lets this wrapper create its own run.")
    args = parser.parse_args()
    load_env(args.env)

    entry = registry.get(args.script)  # re-read here too, not reused from the choices lookup above
    module = importlib.import_module(entry["module"])
    run_id = uuid.UUID(args.run_id) if args.run_id else None
    await module.main(trigger=args.trigger, run_id=run_id)

    if args.script != "build_statistics":
        await _refresh_statistics_best_effort()


if __name__ == "__main__":
    asyncio.run(main())
