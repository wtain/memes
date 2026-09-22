"""Computes sentence embeddings for the OCR text of every image classified text_heavy (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md), storing them in
ocr_text_embeddings for a future duplicate-matching signal (see
docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md). Safe to re-run: only images not yet
in ocr_text_embeddings are processed."""
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.ocr_text_embeddings import OCRTextEmbeddingsRepository
from rules.text_heavy_result import CLASSIFIER_NAME
from Storage.db import AsyncSessionLocal

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # ai/sbert.py's own default -- named
                                                             # explicitly here so the model in
                                                             # use is visible at a glance,
                                                             # defensive against that default
                                                             # ever changing later.


def _get_embedder():
    # Imported lazily so this module stays importable without sentence-transformers/torch
    # (integration CI and Dockerfile.backend install neither; only actually embedding needs them).
    from ai.sbert import SbertModel
    return SbertModel(model_name=EMBEDDING_MODEL)


async def run(session, confidence_min: float, lang_score_min: float, status: str) -> SimpleMetricsListener:
    repo = OCRTextEmbeddingsRepository(session)
    texts = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, confidence_min, lang_score_min, status=status)
    print(f"Images to embed: {len(texts)}")

    metrics = SimpleMetricsListener()
    if not texts:
        return metrics

    embedder = _get_embedder()
    tracker = ProgressTracker(total=len(texts), report_every=settings.GENERAL.PROGRESS_EVERY)

    for i, (image_id, text) in enumerate(texts.items()):
        vector = embedder.embed_text(text)
        await repo.save(image_id, vector.tolist())
        metrics.increment("embedded")
        tracker.mark_done()
        if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
            await session.commit()

    await session.commit()
    tracker.summary()
    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    confidence_min = settings.OCR.CONFIDENCE_MIN
    lang_score_min = settings.OCR.LANG_SCORE_MIN

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)
    else:
        async with tracked_run(kind="build_ocr_text_embeddings", trigger=trigger):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)

    print("Embedding results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
