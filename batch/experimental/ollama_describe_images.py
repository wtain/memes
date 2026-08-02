import argparse
import asyncio
import os

from sqlalchemy import select

from ai.yolo import YoloAnimalDetector
from batch.utils.image_format_filter import has_unsupported_image_extension
from config.settings import load_env
from Storage.db import AsyncSessionLocal

from Storage.models import Image as Img



async def main():

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Img.filename, Img.id)
        )
        result = await session.execute(stmt)

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        model = YoloAnimalDetector()

        animals, objects = set(), set()

        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            if has_unsupported_image_extension(path):
                print(f"Skipping {path}")
                continue
            print(f"Running for {path}")

            # todo: batching - commit in batches and enable resume mode, not deleting all in the beginning

            try:
                response = model.detect_animals(path)

                print(f"{filename}: {response}")
                for animal in response["animals"]:
                    animals.add(animal)
                for object in response["objects"]:
                    objects.add(object)

            except Exception as e:
                print(f"Model failed: {e}")
        print(f"Animals: {animals}")
        print(f"Objects: {objects}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())

