import asyncio
import os

import ollama
import open_clip
import torch
from ollama import ResponseError
from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from batch.embeddingutils.image import embed_image, load_image
from batch.models.external import AsyncSessionLocal
from batch.models.external import OllamaDescription

from batch.models.external import Image as Img



async def main():

    async with AsyncSessionLocal() as session:
        print("Deleting all descriptions...")
        await session.execute(
            delete(OllamaDescription)
        )
        await session.commit()
        print("Done")

        stmt = (
            select(Img.filename, Img.id)
        )
        result = await session.execute(stmt)

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            print(f"Running for {path}")

            try:
                response = ollama.chat(
                    model='llava',
                    messages=[{
                        'role': 'user',
                        'content': 'What is shown in this image?',
                        'images': [path]
                    }]
                )

                description = OllamaDescription(
                    image_id=image_id,
                    text=response['message']['content']
                )

                session.add(description)
            except Exception as e:
                print(f"Model failed: {e}")


        # batch commit?
        print("Committing...")
        await session.commit()
        print("Done")



if __name__ == "__main__":
    asyncio.run(main())

