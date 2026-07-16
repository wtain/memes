import argparse
import asyncio

from ai.sbert import SbertModel
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from repository.image_description_embeddings import ImageDescriptionEmbeddingsRepository

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


async def main(reset: bool):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing description embeddings before running "
                             "(default: fill only descriptions missing an embedding)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.reset))
