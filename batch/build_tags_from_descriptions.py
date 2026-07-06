import argparse
import asyncio

from config.settings import settings, load_env
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver
from rules.engine import RulesEngine


async def main(incremental: bool):
    rules_engine = RulesEngine(settings.get("RULES_FILE"))

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags("Ollama")

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")
        print(f"Mode: {'incremental' if incremental else 'full'}")
        print("Running...")

        if incremental:
            images_and_texts_results = await images_repo.get_images_and_ollama_descriptions_without_tags("Ollama")
        else:
            images_and_texts_results = await images_repo.get_images_and_ollama_descriptions()

        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text in images_and_texts_results:
                for tag_name, value in rules_engine.get_tags_for_text(text):
                    tags_saver.add_tag(image_id, tag_name, value, "Ollama")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no Ollama tags yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))
