import argparse
import asyncio
import os

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.move_flagged import run as move_flagged
from batch.unregister_deleted_images import run as unregister_deleted_images


async def main():
    async with AsyncSessionLocal() as session:
        BASE_PATH = settings.BASE_PATH
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        print("=== Moving flagged images ===")
        await move_flagged(session, base_path)

        print("=== Unregistering deleted images ===")
        await unregister_deleted_images(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
