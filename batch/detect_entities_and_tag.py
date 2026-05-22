import asyncio
import os

from ai.yolo import YoloAnimalDetector
from Storage.db import AsyncSessionLocal

from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver


async def main():

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
                if path.lower().endswith("webp"):
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



if __name__ == "__main__":
    asyncio.run(main())

