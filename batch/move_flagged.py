import argparse
import asyncio
import os
import shutil
import uuid

from sqlalchemy import select

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def run(session, base_path):
    query = (
        select(
            Image.filename,
        )
        .join(ImageExtras, ImageExtras.image_id == Image.id)
        .where(ImageExtras.flagged == True)
    )
    images = await session.execute(query)

    flagged_path = os.path.join(base_path, "excluded")
    os.makedirs(flagged_path, exist_ok=True)

    for (filename, ) in images:
        path_from = os.path.join(base_path, filename)
        path_to = os.path.join(flagged_path, filename)
        print(f"Moving {filename} from {path_from} to {path_to}")
        shutil.move(path_from, path_to)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
