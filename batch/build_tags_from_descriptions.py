import argparse
import asyncio
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import DESCRIPTION_TAG_SOURCE, TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent
_SOURCE = DESCRIPTION_TAG_SOURCE
# Descriptions are LLM output in English regardless of the meme's own language.
_DESCRIPTION_LANGUAGE = "en"


async def _process(incremental: bool) -> None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    engine = ConceptTagger.load(data_dir, profile)

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags(_SOURCE)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")
        print(f"Tagging with profile '{profile}' from {data_dir} ...")
        print(f"Mode: {'incremental' if incremental else 'full'}")

        if incremental:
            images_and_texts_results = await images_repo.get_images_and_descriptions_needing_tags(_SOURCE)
            # Tags are rewritten from the image's whole description set, so drop the old ones
            # first; same session as the saver below, so both commit together.
            await tags_repo.delete_tags_for_images(
                _SOURCE, {image_id for _filename, image_id, _text in images_and_texts_results}
            )
        else:
            images_and_texts_results = await images_repo.get_images_and_descriptions()

        metrics = SimpleMetricsListener()
        async with TagsSaver(session) as tags_saver:
            # One image has one description per prompt. Each is tagged on its own (so a tag must
            # reach its threshold within a single description) and the results are unioned per
            # image; TagsSaver dedups identical (key, value) pairs.
            for _filename, image_id, text in images_and_texts_results:
                result = engine.tag(text, language=_DESCRIPTION_LANGUAGE)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, _SOURCE)
                metrics.increment("descriptions.processed")
                metrics.add("tags.total", len(result.tags))
        print("Tags:")
        metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_descriptions", trigger=trigger):
            await _process(incremental=incremental)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no Ollama tags yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental))
