import argparse
import asyncio
import uuid

from ai.sbert import SbertModel
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from repository.image_description_embeddings import ImageDescriptionEmbeddingsRepository

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


async def _process(reset: bool) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        embeddings_repo = ImageDescriptionEmbeddingsRepository(session)

        if reset:
            print("Deleting all description embeddings...")
            await embeddings_repo.delete_all()
            await session.commit()
            print("Done")

        rows = await embeddings_repo.get_descriptions_without_embedding()
        print(f"Found {len(rows)} description(s) needing embeddings")

        embedder = SbertModel(model_name=EMBEDDING_MODEL)
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        for i, (description_id, text) in enumerate(rows):
            vector = embedder.embed_text(text)
            embeddings_repo.save(description_id, vector.tolist())
            tracker.mark_done()
            if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
                await session.commit()

        await session.commit()

    tracker.summary()
    return {"descriptions_embedded": len(rows)}


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, reset: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(reset=reset)
            await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_image_description_embeddings", trigger=trigger) as new_run_id:
            stats = await _process(reset=reset)
            await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing description embeddings before running "
                             "(default: fill only descriptions missing an embedding)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(reset=args.reset))  # trigger defaults to "manual" -- unchanged direct-CLI behavior
