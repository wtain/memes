"""Extracts a meme profile (situation, emotion, stance, topic, tone, "use when") for every active image
that has none or a stale one, with one schema-constrained Ollama call per image. Text-first: the text
model reads the note, non-rejected descriptions and filtered OCR; the vision model runs only when there is
no usable text. Resumable and incremental: selection is derived from the data (input fingerprint plus
taxonomy, prompt and model versions), never from a marker. See
docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md.
"""
import argparse
import asyncio
import os
import uuid

from ai.meme_profile_extractor import OllamaProfileExtractor, ProfileExtractionError
from ai.meme_profile_prompt import PROMPT_VERSION
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.image_format_filter import has_unsupported_image_extension
from batch.utils.meme_profile_bundle import PATH_VISION, build_bundle, fingerprint
from batch.utils.meme_profile_config import NOT_CONFIGURED_MESSAGE, PIPELINE, load_config
from batch.utils.meme_profile_selection import Candidate, select_work
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.meme_profile_inputs import MemeProfileInputsRepository
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.db import AsyncSessionLocal
from taxonomy import load as load_taxonomy

CHUNK_SIZE = 1000


async def _collect_candidates(session, config) -> list[Candidate]:
    """Bundles and fingerprints for ALL active images, in chunks (text only: seconds against hours of LLM time)."""
    inputs_repo = MemeProfileInputsRepository(session)
    images = await inputs_repo.list_active_images()
    candidates: list[Candidate] = []
    for start in range(0, len(images), CHUNK_SIZE):
        chunk = images[start:start + CHUNK_SIZE]
        raw = await inputs_repo.load_inputs([image_id for image_id, _, _ in chunk])
        for image_id, filename, content_hash in chunk:
            item = raw[image_id]
            bundle = build_bundle(
                item.note, item.descriptions, item.ocr_rows,
                ocr_confidence_min=config.ocr_confidence_min, ocr_lang_score_min=config.ocr_lang_score_min,
                limits=config.limits,
            )
            candidates.append(Candidate(image_id, filename, bundle, fingerprint(bundle, content_hash)))
    return candidates


async def _process(reset: bool, limit: int | None, retry_failed: bool) -> dict:
    """Returns run stats: selection counts, the metrics counters and a post-run coverage snapshot."""
    config = load_config(settings.get)
    if config is None:
        print(NOT_CONFIGURED_MESSAGE)
        return {"not_configured": 1, "images_selected": 0, "images_remaining": 0}

    tax = load_taxonomy()
    base_path = os.path.abspath(settings.BASE_PATH)
    metrics = SimpleMetricsListener()
    extractor = OllamaProfileExtractor(tax, seed=config.seed, num_ctx=config.num_ctx)

    async with AsyncSessionLocal() as session:
        profiles = MemeProfileRepository(session)
        status_repo = ImageProcessingStatusRepository(session, PIPELINE)

        if reset:
            deleted = await profiles.delete_model_generated()
            await status_repo.delete_all()
            await session.commit()
            print(f"Deleted {deleted} model-generated profile(s) (human-verified profiles are kept)")

        candidates = await _collect_candidates(session, config)
        metas = await profiles.get_meta([candidate.image_id for candidate in candidates])
        failed_ids = await status_repo.get_image_ids_with_status("failed")
        selection = select_work(
            candidates, metas, failed_ids,
            taxonomy_version=tax.version, prompt_version=PROMPT_VERSION,
            text_model=config.text_model, vision_model=config.vision_model,
            min_text_chars=config.min_text_chars, retry_failed=retry_failed,
        )
        metrics.add("skipped.failed", selection.skipped_failed)

        total_pending = len(selection.work)
        work = selection.work[:limit] if limit is not None else selection.work
        print(f"{len(work)} image(s) to profile this run, {total_pending - len(work)} left waiting "
              f"({len(selection.needs_review_ids)} human-verified profile(s) need review)")

        tracker = ProgressTracker(total=len(work), report_every=settings.GENERAL.PROGRESS_EVERY)
        semaphore = asyncio.Semaphore(config.concurrent_requests)
        # AsyncSession is not safe for concurrent use across tasks: this lock serializes every
        # session-touching call, so only the Ollama request itself (asyncio.to_thread) overlaps.
        db_lock = asyncio.Lock()
        saved_ids: set = set()
        pending = 0

        async def commit_if_due(force: bool = False) -> None:
            nonlocal pending
            pending += 0 if force else 1
            if pending >= config.batch_size or (force and pending):
                await session.commit()
                pending = 0

        async def process_image(item) -> None:
            image_path = os.path.join(base_path, item.filename) if item.path == PATH_VISION else None
            if image_path is not None and has_unsupported_image_extension(image_path):
                print(f"Skipping {image_path}")
                metrics.increment("skipped.unsupported_ext")
                tracker.skip()
                return

            async with semaphore:
                try:
                    result = await asyncio.to_thread(
                        extractor.extract, model=item.model, note=item.bundle.note,
                        descriptions=list(item.bundle.descriptions), ocr=item.bundle.ocr, image_path=image_path,
                    )
                except ProfileExtractionError as error:
                    print(f"Unusable answer for {item.filename}: {error}")
                    metrics.increment("error.invalid_output")
                    async with db_lock:
                        await status_repo.record_failure(item.image_id, str(error))
                        await commit_if_due()
                    tracker.mark_done()
                    return
                except Exception as error:
                    print(f"Model failed for {item.filename}: {error}")
                    metrics.increment("error.model")
                    async with db_lock:
                        await status_repo.record_failure(item.image_id, str(error))
                        await commit_if_due()
                    tracker.mark_done()
                    return

            data = ProfileData.from_validated(result.profile, result.use_when)
            meta = ProfileMeta(tax.version, item.model, PROMPT_VERSION, item.fingerprint, False)
            async with db_lock:
                if await profiles.save_profile(item.image_id, data, meta):
                    await status_repo.mark_done_by_id(item.image_id)
                    saved_ids.add(item.image_id)
                    metrics.increment("saved")
                    metrics.increment(f"path.{item.path}")
                await commit_if_due()
            tracker.mark_done()

        await asyncio.gather(*(process_image(item) for item in work))
        async with db_lock:
            await commit_if_due(force=True)

        failed_after = await status_repo.get_image_ids_with_status("failed")
        coverage = await profiles.coverage(
            taxonomy_version=tax.version, prompt_version=PROMPT_VERSION,
            stale_image_ids=selection.stale_ids - saved_ids, failed_image_ids=failed_after,
        )

    tracker.summary()
    metrics.print()
    return {
        **metrics.counters_dict(),
        "images_selected": len(work),
        "images_remaining": total_pending - len(work),
        "coverage": coverage,
    }


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, reset: bool = False,
               limit: int | None = None, retry_failed: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(reset=reset, limit=limit, retry_failed=retry_failed)
            await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_meme_profiles", trigger=trigger) as new_run_id:
            stats = await _process(reset=reset, limit=limit, retry_failed=retry_failed)
            await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all model-generated profiles (and failure records) before running; "
                             "human-verified profiles are kept")
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many images")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-attempt images that failed on a prior run (default: skip them)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(reset=args.reset, limit=args.limit, retry_failed=args.retry_failed))
