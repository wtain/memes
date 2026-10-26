"""
Multi-prompt Ollama image descriptions: one row per (image, prompt) for active images that lack
one. Prompts and model come from the image_descriptions settings. A pair that fails is recorded as failed and skipped
on later runs unless --retry-failed is given; --reset clears descriptions; --limit caps the run. Run-tracked (kind
build_image_descriptions); admin-triggerable (no limit), manual only. --refresh-stale re-describes recut cuts
(cut_proposed) that still carry the source's stale descriptions. See
docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md.
"""
import argparse
import asyncio
import os
import uuid

from ai.image_description_prompts import load_prompts, resolve_model
from ai.ollama import OllamaImageDescriber
from sqlalchemy import select

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.description_batch_commit import DescriptionBatchCommitter
from batch.utils.image_format_filter import has_unsupported_image_extension
from batch.utils.progress import ProgressTracker
from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageDescription, ImageStatus
from Storage.stale import delete_stale_rows, images_with_stale_rows
from repository.image_descriptions import ImageDescriptionsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository

MAX_CONCURRENT_DESCRIBE_REQUESTS = 2
# Ollama's OLLAMA_NUM_PARALLEL is set to 4, but that's a server-side ceiling, not
# a safe client-side target. Empirically (2026-08-01, RTX 3060 12GB, this
# pipeline's num_ctx=8192): loading the model with 4 parallel KV-cache slots
# already reserves ~10.5GB of the 12GB VRAM at idle, leaving only ~1.6GB free.
# A 3rd/4th concurrent request would risk Ollama falling back to partial CPU
# offload (slower than running serially) or an outright OOM. Capping the client
# at 2 stays well inside that headroom while still overlapping GPU decode of one
# image with the next image's request/DB-write overhead -- measured ~25% wall
# time reduction for 2 concurrent requests over serial in that same test.


def _status_repos(session, prompts):
    # Unlike OCR's use of this table (its authoritative done/processing/failed
    # state machine), here it only ever holds "failed" rows — success is
    # signaled solely by the image_descriptions unique constraint.
    return {p.key: ImageProcessingStatusRepository(session, f"image_description:{p.key}") for p in prompts}


async def _load_existing_pairs(descriptions_repo, prompts):
    existing = {}
    for prompt in prompts:
        existing[prompt.key] = await descriptions_repo.get_image_ids_with_prompt(prompt.key)
    return existing


async def _load_failed_pairs(status_repos, prompts):
    failed = {}
    for prompt in prompts:
        failed[prompt.key] = await status_repos[prompt.key].get_image_ids_with_status("failed")
    return failed


async def _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts,
                                   retry_failed: bool = False, metrics=None):
    succeeded = await _load_existing_pairs(descriptions_repo, prompts)
    failed = {} if retry_failed else await _load_failed_pairs(status_repos, prompts)

    result = await images_repo.get_all_images()
    work = []
    for filename, image_id in result:
        missing = []
        for p in prompts:
            if image_id in succeeded[p.key]:
                continue
            if not retry_failed and image_id in failed[p.key]:
                if metrics is not None:
                    metrics.increment("skipped.failed")
                continue
            missing.append(p)
        if missing:
            work.append((filename, image_id, missing))
    return work


async def refresh_stale_descriptions(session, prompts, describer, base_path, num_ctx, model_for, limit=None) -> dict:
    """Re-describes the cut_proposed images that still carry the stale descriptions copied from their source.
    A prompt that succeeds replaces its stale row (same transaction, committed per image); a prompt that fails keeps
    its stale row, nothing is recorded as failed and the image is retried on the next run. Sequential: the volume is
    the number of cuts, not the corpus."""
    ids = await images_with_stale_rows(session, ImageDescription, ImageStatus.CUT_PROPOSED)
    selected = ids[:limit] if limit is not None else ids
    filenames = {}
    if selected:
        rows = await session.execute(select(Image.id, Image.filename).where(Image.id.in_(selected)))
        filenames = {image_id: filename for image_id, filename in rows.all()}
    repo = ImageDescriptionsRepository(session)
    refreshed = failed = 0
    for image_id in selected:
        filename = filenames.get(image_id)
        if filename is None:
            continue
        path = os.path.join(base_path, filename)
        if has_unsupported_image_extension(path):
            print(f"Skipping {path}")
            continue
        all_ok = True
        for prompt in prompts:
            model = model_for(prompt)
            try:
                text = await asyncio.to_thread(describer.describe, path, prompt.prompt, model, num_ctx)
            except Exception as error:
                print(f"Model failed for {path} [{prompt.key}]: {error}")
                all_ok = False
                continue
            await repo.delete_stale_for_prompt(image_id, prompt.key)
            repo.save(image_id, prompt.key, model, text)
        if all_ok:
            await session.flush()
            await delete_stale_rows(session, ImageDescription, [image_id])  # prompts that are no longer configured
            refreshed += 1
        else:
            failed += 1
        await session.commit()
    return refresh_stats(selected=len(ids), refreshed=refreshed, failed=failed)


async def _process(reset: bool, limit: int | None = None, retry_failed: bool = False,
                   refresh_stale: bool = False) -> dict[str, int]:
    """Returns run stats: images selected for this run, images still waiting beyond `limit`,
    plus the metrics counters (saved, error.model, skipped.*)."""
    BASE_PATH = settings.BASE_PATH
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    prompts_file = settings.get("image_descriptions.prompts_file")
    if not prompts_file:
        raise RuntimeError(
            "image_descriptions.prompts_file is not configured for this environment"
        )
    prompts_path = os.path.join(os.path.dirname(__file__), prompts_file)
    prompts = load_prompts(prompts_path)
    print(f"Loaded {len(prompts)} prompt(s): {[p.key for p in prompts]}")

    num_ctx = settings.get("image_descriptions.num_ctx")
    batch_size = settings.get("image_descriptions.batch_size")

    metrics = SimpleMetricsListener()
    describer = OllamaImageDescriber()

    if refresh_stale:
        async with AsyncSessionLocal() as session:
            stats = await refresh_stale_descriptions(
                session, prompts, describer, base_path, num_ctx, lambda p: resolve_model(p, settings), limit)
        print(f"Refresh stale: {stats}")
        return stats

    async with AsyncSessionLocal() as session:
        descriptions_repo = ImageDescriptionsRepository(session)
        images_repo = ImagesRepository(session)
        status_repos = _status_repos(session, prompts)

        if reset:
            print("Deleting all descriptions...")
            await descriptions_repo.delete_all()
            for status_repo in status_repos.values():
                await status_repo.delete_all()
            await session.commit()
            print("Done")

        work = await _images_missing_prompts(
            images_repo, descriptions_repo, status_repos, prompts, retry_failed, metrics
        )
        total_pending = len(work)
        if limit is not None:
            work = work[:limit]
        print(f"{len(work)} image(s) to describe this run, {total_pending - len(work)} left waiting")

        committer = DescriptionBatchCommitter(session, batch_size=batch_size)
        tracker = ProgressTracker(total=len(work), report_every=settings.GENERAL.PROGRESS_EVERY)

        describe_sem = asyncio.Semaphore(MAX_CONCURRENT_DESCRIBE_REQUESTS)
        # AsyncSession isn't safe for concurrent use across tasks -- this lock
        # serializes every session-touching call (save/record_failure/commit) so
        # only the Ollama request itself (asyncio.to_thread, below) actually
        # overlaps between images.
        db_lock = asyncio.Lock()

        async def process_image(filename, image_id, missing):
            path = os.path.join(base_path, filename)

            if has_unsupported_image_extension(path):
                print(f"Skipping {path}")
                metrics.increment("skipped.unsupported_ext")
                tracker.skip()
                return

            async with describe_sem:
                for prompt in missing:
                    model = resolve_model(prompt, settings)
                    try:
                        text = await asyncio.to_thread(
                            describer.describe, path, prompt.prompt, model, num_ctx
                        )
                    except Exception as e:
                        print(f"Model failed for {path} [{prompt.key}]: {e}")
                        metrics.increment("error.model")
                        async with db_lock:
                            await status_repos[prompt.key].record_failure(image_id, str(e))
                        continue

                    async with db_lock:
                        committer.save_description(image_id, prompt.key, model, text)
                        metrics.increment("saved")

            async with db_lock:
                await committer.on_image_done()
            tracker.mark_done()

        await asyncio.gather(*(
            process_image(filename, image_id, missing) for filename, image_id, missing in work
        ))

        await committer.close()

    tracker.summary()
    metrics.print()
    return {
        **metrics.counters_dict(),
        "images_selected": len(work),
        "images_remaining": total_pending - len(work),
    }


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, reset: bool = False,
               limit: int | None = None, retry_failed: bool = False, refresh_stale: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(reset=reset, limit=limit, retry_failed=retry_failed, refresh_stale=refresh_stale)
            await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_image_descriptions", trigger=trigger) as new_run_id:
            stats = await _process(reset=reset, limit=limit, retry_failed=retry_failed, refresh_stale=refresh_stale)
            await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing descriptions before running "
                             "(default: fill only missing image/prompt pairs)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most this many images (default: no limit)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-attempt previously-failed image/prompt pairs "
                             "(default: skip pairs that failed on a prior run)")
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Re-describe the cut_proposed images (recut) that still carry the stale descriptions "
                             "copied from their source; --limit still applies")
    args = parser.parse_args()
    reject_conflicts(parser, args.refresh_stale, reset=args.reset, retry_failed=args.retry_failed)
    load_env(args.env)
    asyncio.run(main(reset=args.reset, limit=args.limit, retry_failed=args.retry_failed,
                     refresh_stale=args.refresh_stale))  # trigger defaults to "manual"
