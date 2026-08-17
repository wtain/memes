"""
Admin/scheduler-triggerable wrapper around ingest_find_duplicates.py's Tier B pass.

ingest_find_duplicates.py itself stays tier-parameterized and untouched -- it's still used
directly by ingest_auto_prep.py for Tier A, and by CLI for either tier. This thin driver
exists only because the admin-trigger contract (batch/run_wrapper.py) always calls
`module.main(trigger=..., run_id=...)` with no way to pass extra params like `tier` through,
so a tier-specific entry needs its own module -- the same reason ingest_auto_prep.py exists
as a driver rather than adding chain-awareness to each wrapped step.

Tier A has no equivalent standalone entry here -- it's already reachable via
ingest_auto_prep, which runs it as the last step of its prep chain.
"""
import argparse
import asyncio
import uuid

from batch import ingest_find_duplicates
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await ingest_find_duplicates.main(env=None, tier="tier_b", k=None)
    else:
        async with tracked_run(kind="ingest_find_duplicates_tier_b", trigger=trigger):
            await ingest_find_duplicates.main(env=None, tier="tier_b", k=None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
