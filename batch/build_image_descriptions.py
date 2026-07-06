import argparse
import asyncio
import os

from ai.ollama import OllamaImageDescriber
from config.settings import load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.ollama_descriptions import OllamaDescriptionsRepository


async def main(incremental: bool):
    BASE_PATH = os.getenv('BASE_PATH')
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    metrics = SimpleMetricsListener()
    describer = OllamaImageDescriber()

    async with AsyncSessionLocal() as session:
        descriptions_repo = OllamaDescriptionsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            print("Deleting all descriptions...")
            await descriptions_repo.delete_all()
            await session.commit()
            print("Done")

        print(f"Mode: {'incremental' if incremental else 'full'}")

        if incremental:
            images = await images_repo.get_all_images_without_description()
        else:
            images = await images_repo.get_all_images()

        for (filename, image_id,) in images:
            path = os.path.join(base_path, filename)

            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                metrics.increment("skipped.webp")
                continue

            print(f"Running for {path}")

            try:
                description = describer.describe(path)
                descriptions_repo.save(image_id, description)
                metrics.increment("saved")
            except Exception as e:
                print(f"Model failed: {e}")
                metrics.increment("error.model")

        print("Committing...")
        await session.commit()
        print("Done")

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--incremental", action="store_true",
                        help="Only describe images that have no description yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))