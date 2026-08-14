import argparse
import asyncio
import os
import uuid

from ai.yolo import YoloAnimalDetector
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.image_format_filter import has_unsupported_image_extension
from config.settings import load_env
from Storage.db import AsyncSessionLocal

from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver


async def _process():

    async with AsyncSessionLocal() as session:

        TAG_KIND = "YOLO"

        tags_repo = TagsRepository(session)
        await tags_repo.delete_tags(TAG_KIND)

        img_repo = ImagesRepository(session)

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        model = YoloAnimalDetector()

        async with TagsSaver(session) as tags_saver:
            async for (filename, image_id,) in img_repo.iterate_images():
                path = os.path.join(base_path, filename)
                if has_unsupported_image_extension(path):
                    print(f"Skipping {path}")
                    continue
                print(f"Running for {path}")

                # todo: batching - commit in batches and enable resume mode, not deleting all in the beginning

                try:
                    response = model.detect_animals(path)

                    for animal in response["animals"]:
                        tags_saver.add_tag(image_id, "animal", animal, TAG_KIND)
                    for object in response["objects"]:
                        tags_saver.add_tag(image_id, "object", object, TAG_KIND)

                except Exception as e:
                    print(f"Model failed: {e}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="detect_entities_and_tag", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())

