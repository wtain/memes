import argparse
import asyncio
from pathlib import Path

from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent


async def main(incremental: bool):
    data_dir = settings.get("TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.TAGGING_PROFILE
    ocr_confidence_min = settings.OCR_CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR_LANG_SCORE_MIN
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
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags("OCR")
        else:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts()

        metrics = SimpleMetricsListener()
        tracker = ProgressTracker(
            len(images_and_texts_results),
            report_every=100,
            report_interval_secs=10,
        )

        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence, lang_score in images_and_texts_results:
                if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
                    metrics.increment("images.skipped")
                    tracker.skip()
                    continue
                result = engine.tag(text)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no OCR tags yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))