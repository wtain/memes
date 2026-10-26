"""
Rule-based tags from OCR text with ConceptTagger, source "OCR". Texts failing the OCR confidence or
language-plausibility filters are skipped. --incremental processes only images with no OCR tags yet; a full run
deletes every OCR tag first. Run-tracked (kind build_tags_from_ocr); main() defaults to incremental; admin-triggerable,
manual only; needs the tagging YAML of the environment profile (not available for it). --refresh-stale replaces the
stale OCR tags of recut cuts (cut_proposed) with tags of their refreshed OCR.
"""
import argparse
import asyncio
import uuid
from pathlib import Path

from collections import defaultdict

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.progress import ProgressTracker
from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from Storage.models import ImageStatus, ImageTag, OCRText
from Storage.stale import delete_stale_rows, images_with_stale_rows, stale_among
from repository.images import ImagesRepository
from repository.ocr_text import OCRTextRepository
from repository.tags import TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent


async def refresh_stale_ocr_tags(session, engine, ocr_confidence_min, ocr_lang_score_min) -> dict:
    """Replaces the stale OCR tags of cut_proposed images with tags of their (refreshed) OCR. The tags are computed
    before anything is deleted, so an engine failure leaves every stale tag in place."""
    selected = await images_with_stale_rows(session, ImageTag, ImageStatus.CUT_PROPOSED, source="OCR")
    blocked = await stale_among(session, OCRText, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    tags_by_image = defaultdict(set)
    for image_id, text, confidence, language, lang_score in await OCRTextRepository(session).get_rows_for_images(ready):
        if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
            continue
        tags_by_image[image_id].update(engine.tag(text, language=language or "unknown").tags)
    try:
        await delete_stale_rows(session, ImageTag, ready, source="OCR")
        for image_id in ready:
            for name, value in sorted(tags_by_image.get(image_id, ())):
                session.add(ImageTag(image_id=image_id, key=name, value=value, source="OCR"))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))


async def _process(incremental: bool, refresh_stale: bool = False) -> dict | None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    engine = ConceptTagger.load(data_dir, profile)

    if refresh_stale:
        async with AsyncSessionLocal() as session:
            stats = await refresh_stale_ocr_tags(session, engine, ocr_confidence_min, ocr_lang_score_min)
        print(f"Refresh stale: {stats}")
        return stats

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


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True,
               refresh_stale: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(incremental=incremental, refresh_stale=refresh_stale)
            if stats:
                await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_tags_from_ocr", trigger=trigger) as new_run_id:
            stats = await _process(incremental=incremental, refresh_stale=refresh_stale)
            if stats:
                await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no OCR tags yet (default: clear all and reprocess)")
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Replace the stale OCR tags of cut_proposed images (recut) with tags of their refreshed OCR")
    args = parser.parse_args()
    reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental, refresh_stale=args.refresh_stale))