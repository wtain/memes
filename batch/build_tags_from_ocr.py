import asyncio
import os
from pathlib import Path

from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent
DATA_DIR = os.getenv("TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
PROFILE = os.getenv("TAGGING_PROFILE", "general")
OCR_CONFIDENCE_MIN = float(os.getenv("OCR_CONFIDENCE_MIN", "0.4"))

engine = ConceptTagger.load(DATA_DIR, PROFILE)


async def main():
    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        await tags_repo.delete_tags("OCR")

        images_repo = ImagesRepository(session)
        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")

        print(f"Tagging with profile '{PROFILE}' from {DATA_DIR} ...")
        images_and_texts_results = await images_repo.get_images_and_ocr_texts()

        tagged = skipped = 0
        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence in images_and_texts_results:
                if confidence < OCR_CONFIDENCE_MIN:
                    skipped += 1
                    continue
                result = engine.tag(text)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, "OCR")
                tagged += 1

        print(f"Done: {tagged} images tagged, {skipped} skipped (low confidence)")


if __name__ == "__main__":
    asyncio.run(main())