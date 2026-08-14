import argparse
import asyncio
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent


async def _process(incremental: bool) -> None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    engine = ConceptTagger.load(data_dir, profile)

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags("OCR")

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")
        print(f"Tagging with profile '{profile}' from {data_dir} ...")
        print(f"Mode: {'incremental' if incremental else 'full'}")
        print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}, OCR_LANG_SCORE_MIN={ocr_lang_score_min}")

        if incremental:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags_with_language("OCR")
        else:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_with_language()

        metrics = SimpleMetricsListener()
        tracker = ProgressTracker(
            len(images_and_texts_results),
            report_every=100,
            report_interval_secs=10,
        )

        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence, language, lang_score in images_and_texts_results:
                if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
                    metrics.increment("images.skipped")
                    tracker.skip()
                    continue
                result = engine.tag(text, language=language or "unknown")
                tag_count = len(result.tags)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, "OCR")
                metrics.increment("images.processed")
                metrics.add("tags.total", tag_count)
                metrics.bucket("tags_per_image", tag_count)
                tracker.mark_done()

        tracker.summary()
    print("Tags:")
    metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_ocr", trigger=trigger):
            await _process(incremental=incremental)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no OCR tags yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental))