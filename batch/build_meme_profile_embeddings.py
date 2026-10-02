"""Embeds each meme profile's situation_text with the Ollama-served embedding model chosen in P1
(settings taxonomy.embedding_model) into meme_profile_situation_embeddings. Incremental: a profile is
(re)embedded when it has no vector or its vector came from a different model or digest, so a model change
is a re-embed, never a re-extraction. See
docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md.
"""
import argparse
import asyncio
import uuid

from ai.ollama_embed import OllamaEmbedder
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.meme_profile_config import NOT_CONFIGURED_MESSAGE, load_config
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.meme_profiles import MemeProfileRepository
from Storage.db import AsyncSessionLocal

EMBED_CHUNK = 32


async def _process(reset: bool, limit: int | None) -> dict:
    if load_config(settings.get) is None:
        print(NOT_CONFIGURED_MESSAGE)
        return {"not_configured": 1, "images_selected": 0, "images_remaining": 0}

    model_name = settings.TAXONOMY.EMBEDDING_MODEL
    embedder = OllamaEmbedder(model_name)
    model_digest = embedder.model_digest()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        repo = MemeProfileRepository(session)
        if reset:
            deleted = await repo.delete_all_embeddings()
            await session.commit()
            print(f"Deleted {deleted} situation embedding(s)")

        pending = await repo.get_profiles_needing_embedding(model_name, model_digest)
        total_pending = len(pending)
        work = pending[:limit] if limit is not None else pending
        print(f"{len(work)} profile(s) to embed this run, {total_pending - len(work)} left waiting")

        for start in range(0, len(work), EMBED_CHUNK):
            chunk = work[start:start + EMBED_CHUNK]
            try:
                vectors = await asyncio.to_thread(embedder.embed, [text for _, text in chunk])
            except Exception as error:
                print(f"Embedding failed for a chunk of {len(chunk)}: {error}")
                metrics.add("error.embed", len(chunk))
                continue
            for (image_id, _), vector in zip(chunk, vectors):
                await repo.save_situation_embedding(image_id, vector.tolist(), model_name, model_digest)
                metrics.increment("embedded")
            await session.commit()

    metrics.print()
    return {
        **metrics.counters_dict(),
        "images_selected": len(work),
        "images_remaining": total_pending - len(work),
    }


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, reset: bool = False,
               limit: int | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(reset=reset, limit=limit)
            await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_meme_profile_embeddings", trigger=trigger) as new_run_id:
            stats = await _process(reset=reset, limit=limit)
            await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true", help="Delete every situation embedding before running")
    parser.add_argument("--limit", type=int, default=None, help="Embed at most this many profiles")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(reset=args.reset, limit=args.limit))
