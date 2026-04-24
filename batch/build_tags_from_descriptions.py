import asyncio

from batch.models.external import AsyncSessionLocal
from batch.repository.images import ImagesRepository
from batch.repository.tags import TagsRepository, TagsSaver
from batch.rules.engine import RulesEngine

rules_engine = RulesEngine()

async def main():

    async with AsyncSessionLocal() as session:

        tags_repo = TagsRepository(session)
        await tags_repo.delete_tags("Ollama")

        images_repo = ImagesRepository(session)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")

        print("Running...")
        images_and_texts_results = await images_repo.get_images_and_ollama_descriptions()
        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text in images_and_texts_results:
                for tag_name, value in rules_engine.get_tags_for_text(text):
                    tags_saver.add_tag(image_id, tag_name, value, "Ollama")



if __name__ == "__main__":
    asyncio.run(main())