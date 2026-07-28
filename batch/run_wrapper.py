import argparse
import asyncio
import importlib
import sys
import uuid

from batch.registry import BatchRegistry
from config.settings import load_env


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


if __name__ == "__main__":
    asyncio.run(main())
