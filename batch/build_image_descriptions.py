import asyncio
import os

import ollama
from sqlalchemy import delete, select

from Storage.db import AsyncSessionLocal
from Storage.models import OllamaDescription

from Storage.models import Image as Img



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

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)  # "c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\"

        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                continue
            print(f"Running for {path}")

            # todo: batching - commit in batches and enable resume mode, not deleting all in the beginning

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

