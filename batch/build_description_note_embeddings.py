import argparse
import asyncio
import uuid

from ai.sbert import SbertModel
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_note_embeddings import DescriptionNoteEmbeddingsRepository
from Storage.db import AsyncSessionLocal

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


async def _process() -> None:
    async with AsyncSessionLocal() as session:
        repo = DescriptionNoteEmbeddingsRepository(session)
        rows = await repo.get_notes_needing_embedding()
        print(f"Found {len(rows)} description note(s) needing embeddings")

        embedder = SbertModel(model_name=EMBEDDING_MODEL)
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        for i, (image_id, text) in enumerate(rows):
            vector = embedder.embed_text(text)
            await repo.save(image_id, vector.tolist())
            tracker.mark_done()
            if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
                await session.commit()

        await session.commit()
    tracker.summary()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_description_note_embeddings", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
