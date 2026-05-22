import asyncio
import os

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver
from rules.engine import RulesEngine

RULES_FILE = os.getenv("RULES_FILE")

rules_engine = RulesEngine(RULES_FILE)

async def main():


    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        await tags_repo.delete_tags("OCR")

        images_repo = ImagesRepository(session)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")

        print("Running...")
        images_and_texts_results = await images_repo.get_images_and_ocr_texts()

        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence in images_and_texts_results:
                if confidence < 0.4:
                    continue
                for tag_name, value in rules_engine.get_tags_for_ocr_text(text):
                    tags_saver.add_tag(image_id, tag_name, value, "OCR")



if __name__ == "__main__":
    asyncio.run(main())