import asyncio
import os

from ai.ollama import OllamaImageDescriber
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.ollama_descriptions import OllamaDescriptionsRepository


async def main():
    BASE_PATH = os.getenv('BASE_PATH')
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    metrics = SimpleMetricsListener()
    describer = OllamaImageDescriber()

    async with AsyncSessionLocal() as session:
        descriptions_repo = OllamaDescriptionsRepository(session)
        images_repo = ImagesRepository(session)

        print("Deleting all descriptions...")
        await descriptions_repo.delete_all()
        await session.commit()
        print("Done")

        images = await images_repo.get_all_images()

        for (filename, image_id,) in images:
            path = os.path.join(base_path, filename)

            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                metrics.increment("skipped.webp")
                continue

            print(f"Running for {path}")

            # todo: batching - commit in batches and enable resume mode, not deleting all in the beginning

            try:
                description = describer.describe(path)
                descriptions_repo.save(image_id, description)
                metrics.increment("saved")
            except Exception as e:
                print(f"Model failed: {e}")
                metrics.increment("error.model")

        # batch commit?
        print("Committing...")
        await session.commit()
        print("Done")

    metrics.print()


if __name__ == "__main__":
    asyncio.run(main())