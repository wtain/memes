import asyncio
import json
import os

import ollama
import open_clip
import torch
from ollama import ResponseError
from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from batch.ai.yolo import YoloAnimalDetector
from batch.embeddingutils.image import embed_image, load_image
from batch.models.external import AsyncSessionLocal
from batch.models.external import OllamaDescription

from batch.models.external import Image as Img



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
            if path.lower().endswith("webp"):
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
    asyncio.run(main())

