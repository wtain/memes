import argparse
import asyncio

from batch.utils.ocr_lemmas import group_lemmas_by_image
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.db import AsyncSessionLocal
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository, OCR_LEMMAS_PIPELINE
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver


async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics):
    lemmas_repo = OCRLemmasRepository(session)
    images_repo = ImagesRepository(session)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)

    if not incremental:
        await lemmas_repo.delete_all()
        await status_repo.delete_all()
        await session.commit()

    print(f"Mode: {'incremental' if incremental else 'full'}")
    print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}, OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}")

    if incremental:
        rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    else:
        rows = await images_repo.get_images_and_ocr_texts_with_language()

    simplified_rows = [
        (image_id, text, confidence, language, lang_score)
        for _filename, image_id, text, confidence, language, lang_score in rows
    ]

    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(
        simplified_rows, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length
    )
    metrics.add("ocr_rows.total", stats["rows_total"])
    metrics.add("ocr_rows.skipped", stats["rows_skipped"])
    metrics.add("ocr_rows.processed", stats["rows_processed"])

    print(f"Total images: {len(all_image_ids)}")
    tracker = ProgressTracker(len(all_image_ids), report_every=100, report_interval_secs=10)

    async with OCRLemmasSaver(session) as saver:
        for image_id in all_image_ids:
            lemma_set = lemmas_by_image.get(image_id, set())
            await saver.add_lemmas(image_id, lemma_set)
            await status_repo.mark_done_by_id(image_id)
            metrics.add("lemmas.total", len(lemma_set))
            metrics.bucket("lemmas_per_image", len(lemma_set))
            tracker.mark_done()

    tracker.summary()


async def main(incremental: bool):
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        await run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics)

    print("Lemmas:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images not yet marked done for the ocr_lemmas "
                             "pipeline (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))
