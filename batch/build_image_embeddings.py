import asyncio
import os

import open_clip
import torch
from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from batch.embeddingutils.image import embed_image, load_image
from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, OCRText, Embedding

from batch.models.external import Image as Img



async def main():

    async with AsyncSessionLocal() as session:
        print("Deleting all embeddings...")
        await session.execute(
            delete(Embedding)
        )
        await session.commit()
        print("Done")

        total_images = (await session.execute(
            select(count(Img.id))
        )).scalar_one()

        stmt = (
            select(Img.filename, Img.id)
        )
        result = await session.execute(stmt)

        device = "cuda" if torch.cuda.is_available() else "cpu"

        model, preprocess, _ = open_clip.create_model_and_transforms(
            "ViT-B-32",
            pretrained="openai"
        )

        model = model.to(device)
        model.eval()

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        print(f"Processing on {device}")
        # todo: batch encode
        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            image = load_image(path)
            vector = embed_image(image, device, model, preprocess)
            emb = Embedding(
                image_id=image_id,
                embedding=vector.tolist()
            )

            session.add(emb)

        # batch commit?
        print("Committing...")
        await session.commit()
        print("Done")



if __name__ == "__main__":
    asyncio.run(main())

