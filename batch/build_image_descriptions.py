import argparse
import asyncio
import os

from ai.image_description_prompts import load_prompts, resolve_model
from ai.ollama import OllamaImageDescriber
from batch.utils.description_batch_commit import DescriptionBatchCommitter
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.image_descriptions import ImageDescriptionsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository


def _status_repos(session, prompts):
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


async def main(reset: bool, limit: int | None = None, retry_failed: bool = False):
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
        if limit is not None:
            work = work[:limit]

        committer = DescriptionBatchCommitter(session, batch_size=batch_size)
        tracker = ProgressTracker(total=len(work), report_every=settings.GENERAL.PROGRESS_EVERY)

        for filename, image_id, missing in work:
            path = os.path.join(base_path, filename)

            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                metrics.increment("skipped.webp")
                tracker.skip()
                continue

            for prompt in missing:
                model = resolve_model(prompt, settings)
                try:
                    text = describer.describe(path, prompt.prompt, model, num_ctx)
                    committer.save_description(image_id, prompt.key, model, text)
                    metrics.increment("saved")
                except Exception as e:
                    print(f"Model failed for {path} [{prompt.key}]: {e}")
                    metrics.increment("error.model")
                    await status_repos[prompt.key].record_failure(image_id, str(e))

            await committer.on_image_done()
            tracker.mark_done()

        await committer.close()

    tracker.summary()
    metrics.print()


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
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.reset, args.limit, args.retry_failed))
