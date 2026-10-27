"""
Per-image lemma index of OCR text, for smart search and for the corroboration check of OCR-text
duplicate matching. --incremental skips images already indexed; a full run deletes the whole ocr_lemmas table, so
--status pending requires --incremental. Run-tracked (kind build_ocr_lemmas); main() defaults to incremental, so
admin and scheduled runs never rebuild everything. Admin-triggerable, manual only. --refresh-stale replaces the stale
lemmas of recut cuts (cut_proposed) with lemmas of their refreshed OCR; it ignores --status and --incremental.
"""
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.ocr_lemmas import group_lemmas_by_image
from batch.utils.progress import ProgressTracker
from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.db import AsyncSessionLocal
from Storage.models import ImageStatus, OCRLemma, OCRText
from Storage.stale import delete_stale_rows, images_with_stale_rows, stale_among
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository, OCR_LEMMAS_PIPELINE
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver
from repository.ocr_text import OCRTextRepository


async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph,
              metrics, status: str = "active"):
    lemmas_repo = OCRLemmasRepository(session)
    images_repo = ImagesRepository(session)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)

    if not incremental:
        await lemmas_repo.delete_all()
        await status_repo.delete_all()
        await session.commit()

    print(f"Mode: {'incremental' if incremental else 'full'}")
    print(f"Status: {status}")
    print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}, OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}")

    if incremental:
        rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language(status=status)
    else:
        rows = await images_repo.get_images_and_ocr_texts_with_language(status=status)

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


async def refresh_stale_lemmas(session, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length) -> dict:
    """Replaces the stale lemma rows of cut_proposed images with lemmas of their (refreshed) OCR. One transaction:
    on any error nothing is committed and the stale rows stay."""
    selected = await images_with_stale_rows(session, OCRLemma, ImageStatus.CUT_PROPOSED)
    blocked = await stale_among(session, OCRText, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    rows = await OCRTextRepository(session).get_rows_for_images(ready)
    lemmas_by_image, _, _ = group_lemmas_by_image(
        rows, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)
    # Not used as `async with`: OCRLemmasSaver.__aexit__ commits even when the block raised.
    saver = OCRLemmasSaver(session)
    try:
        await delete_stale_rows(session, OCRLemma, ready)
        for image_id in ready:
            await saver.add_lemmas(image_id, lemmas_by_image.get(image_id, set()))
            await status_repo.mark_done_by_id(image_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))


async def _process(incremental: bool, status: str = "active", refresh_stale: bool = False) -> dict | None:
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    if refresh_stale:
        async with AsyncSessionLocal() as session:
            stats = await refresh_stale_lemmas(session, morph, ocr_confidence_min, ocr_lang_score_min,
                                               min_word_length)
        print(f"Refresh stale: {stats}")
        return stats

    async with AsyncSessionLocal() as session:
        await run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length,
                  morph, metrics, status=status)

    print("Lemmas:")
    metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True,
                status: str = "active", refresh_stale: bool = False) -> None:
    if status == "pending" and not incremental and not refresh_stale:
        raise ValueError(
            "build_ocr_lemmas: --status pending requires incremental mode. A full reprocess "
            "(incremental=False) unconditionally deletes ALL ocr_lemmas rows via "
            "OCRLemmasRepository.delete_all() -- there is no status-scoped delete -- which would "
            "wipe the active corpus's lemma index too, not just pending images."
        )
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(incremental=incremental, status=status, refresh_stale=refresh_stale)
            if refresh_stale and stats:
                await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_ocr_lemmas", trigger=trigger) as new_run_id:
            stats = await _process(incremental=incremental, status=status, refresh_stale=refresh_stale)
            if refresh_stale and stats:
                await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images not yet marked done for the ocr_lemmas "
                             "pipeline (default: clear all and reprocess)")
    parser.add_argument("--status", choices=["active", "pending"], default="active",
                        help="Which images to process (default: active). --status pending "
                             "requires --incremental (see main()'s own guard).")
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Replace the stale lemmas of cut_proposed images (recut) with lemmas of their "
                             "refreshed OCR; ignores --status")
    args = parser.parse_args()
    reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental, status=args.status, refresh_stale=args.refresh_stale))
