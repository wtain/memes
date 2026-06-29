import argparse
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from ai.clip import ClipModel
from embeddingutils.image import load_image
from Storage.db import AsyncSessionLocal
from Storage.models import Embedding

from Storage.models import Image as Img


load_dotenv()


async def main(incremental: bool):

    async with AsyncSessionLocal() as session:
        if not incremental:
            print("Deleting all embeddings...")
            await session.execute(
                delete(Embedding)
            )
            await session.commit()
            print("Done")

        total_images = (await session.execute(
            select(count(Img.id))
        )).scalar_one()

        if incremental:
            has_embedding = select(Embedding.image_id).distinct().scalar_subquery()
            stmt = select(Img.filename, Img.id).where(Img.id.not_in(has_embedding))
        else:
            stmt = select(Img.filename, Img.id)

        result = await session.execute(stmt)

        clip_model = ClipModel()

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        print(f"Processing on {clip_model.device}")
        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            if os.path.isdir(path):
                continue
            if not os.path.exists(path):
                continue
            try:
                image = load_image(path)
                vector = clip_model.embed_image(image)
                emb = Embedding(
                    image_id=image_id,
                    embedding=vector.tolist()
                )

                session.add(emb)
            except Exception as e:
                print(f"Can't read {path}: {e}")

        print("Committing...")
        await session.commit()
        print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--incremental", action="store_true",
                        help="Only embed images that have no embedding yet (default: clear all and reprocess)")
    args = parser.parse_args()
    asyncio.run(main(args.incremental))