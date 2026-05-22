import asyncio
import os
import uuid

import open_clip
import torch

# from Backend.app.repositories.image_repository import ImageRepository
from Storage.db import AsyncSessionLocal
from embeddingutils.image import load_image, embed_image
from repository.images import ImagesRepository


async def main(path: str):

    reference_dir = "c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\Metalmemes-temp"

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model, preprocess, _ = open_clip.create_model_and_transforms(
        "ViT-B-32",
        pretrained="openai"
    )

    model = model.to(device)
    model.eval()

    async with AsyncSessionLocal() as session:

        # repo = ImageRepository(session)
        repo2 = ImagesRepository(session)

        cnt_not_found = 0
        random_uuid = str(uuid.uuid4())

        for file in os.listdir(reference_dir):
            try:
                if await repo2.find_image_by_filename(file):
                    continue

                cnt_not_found += 1
                print(f"Not found({cnt_not_found}): {file}")

                path = os.path.join(reference_dir, file)

                image = load_image(path)
                vector = embed_image(image, device, model, preprocess)
                # emb = Embedding(
                #     image_id="",
                #     embedding=vector.tolist()
                # )

                rows = await repo2.get_similar(random_uuid, vector.tolist())
                for iid, d, fname in rows:
                    print(f"--> {fname} ({d}) ({iid})")

                # session.add(emb)
            except Exception as e:
                print(f"Error for {path}: {e}")

if __name__ == "__main__":
    source_path = os.getenv('BASE_PATH')
    print(f"Base path: {source_path}")
    asyncio.run(main(source_path))
