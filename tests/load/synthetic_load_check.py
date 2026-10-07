"""Synthetic load check for the P4 retrieval stage (spec decision 11). TEST DATABASE ONLY.

    DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONPATH=. \
        python tests/load/synthetic_load_check.py --images 25000 --queries 50

Fills ocrdb_test with synthetic images, profiles and normalised random situation vectors, measures the retrieval
stage (candidate SQL, scoring, fusion, collapse, facets; no parsing, no embedding call) and removes its rows.
It never runs against anything but a database whose URL contains `ocrdb_test`."""
import argparse
import asyncio
import os
import random
import sys
import time
import uuid

import numpy as np
from sqlalchemy import delete, func, insert, inspect, select

if "ocrdb_test" not in os.environ.get("DATABASE_URL", ""):
    sys.exit("Refusing to run: DATABASE_URL must point at the test database (ocrdb_test).")

from repository.meme_retrieval import MemeRetrievalRepository                      # noqa: E402
from retrieval.config import AXES, RetrievalConfig                                   # noqa: E402
from retrieval.facets import collapse_clusters, facet_counts                         # noqa: E402
from retrieval.fusion import fuse, rank_candidates                                   # noqa: E402
from retrieval.model import Filters, QueryView                                       # noqa: E402
from retrieval.similarity import load_similarity                                     # noqa: E402
from Storage.db import AsyncSessionLocal, engine                                             # noqa: E402
from Storage.models import (                                                         # noqa: E402
    PROFILE_EMBEDDING_DIM, Base, Image, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue, TmpImageClusters,
)
from taxonomy import load                                                            # noqa: E402

PREFIX = "synthetic-load-"
MODEL = "bge-large"
BATCH = 1000
BUDGET_MS = 300


def _config() -> RetrievalConfig:
    """Production defaults for the candidate caps and weights, built directly (no import of the test helpers)."""
    return RetrievalConfig(
        weights={"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10, "lexical": 0.10},
        situation_k=300, axis_k=300, lexical_k=100, total_cap=600, neutral_score=0.3, situation_floor=0.45,
        situation_ceil=0.85, use_neighbours=True, lexical_enabled=True, cache_ttl_seconds=120.0,
        embed_timeout_seconds=2.0, warm_interval_minutes=20.0, breaker_failures=3, breaker_open_seconds=30.0,
        default_limit=20, max_limit=50, embedding_model=MODEL, ollama_url="http://127.0.0.1:11434")


async def _populate(session, count: int, rng: random.Random, np_rng, tax) -> None:
    keys = {axis: list(tax.values[axis]) for axis in AXES}
    for start in range(0, count, BATCH):
        size = min(BATCH, count - start)
        ids = [uuid.uuid4() for _ in range(size)]
        await session.execute(insert(Image), [
            {"id": image_id, "filename": f"{PREFIX}{image_id}.jpg", "status": "active"} for image_id in ids])
        await session.execute(insert(MemeProfile), [
            {"image_id": image_id, "taxonomy_version": tax.version, "extractor_model": "synthetic",
             "extractor_prompt_version": "synthetic", "inputs_fingerprint": "0" * 64,
             "situation_text": "synthetic situation", "use_when": "Use when synthetic."} for image_id in ids])
        values = []
        for image_id in ids:
            for axis in AXES:
                for key in rng.sample(keys[axis], rng.randint(1, 2)):
                    values.append({"image_id": image_id, "axis": axis, "value_key": key,
                                   "confidence": rng.choice([0.3, 0.6, 0.9]), "source": "model"})
        await session.execute(insert(MemeProfileValue), values)
        vectors = np_rng.standard_normal((size, PROFILE_EMBEDDING_DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        await session.execute(insert(MemeProfileSituationEmbedding), [
            {"image_id": image_id, "embedding": vector.tolist(), "model_name": MODEL, "model_digest": "synthetic"}
            for image_id, vector in zip(ids, vectors)])
        # every tenth image and its successor share a cluster: a duplicate pair per ten images
        await session.execute(insert(TmpImageClusters), [
            {"cluster_id": 990000 + (start + index) // 10, "image_id": image_id}
            for index, image_id in enumerate(ids) if index % 10 in (0, 1)])
        await session.commit()
        print(f"  inserted {start + size} / {count}", flush=True)


async def _ensure_schema() -> bool:
    """The test database only has tables while a pytest run is active (tests/integration/conftest.py creates and
    drops them), so create them when absent. Returns True when this call created them (to drop them again)."""
    async with engine.begin() as connection:
        present = await connection.run_sync(lambda sync: inspect(sync).has_table("images"))
        if not present:
            await connection.run_sync(Base.metadata.create_all)
        return not present


async def _count() -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(func.count()).select_from(Image).where(Image.filename.like(f"{PREFIX}%")))).scalar_one()


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        ids = select(Image.id).where(Image.filename.like(f"{PREFIX}%"))
        await session.execute(delete(TmpImageClusters).where(TmpImageClusters.image_id.in_(ids)))
        await session.execute(delete(Image).where(Image.filename.like(f"{PREFIX}%")))
        await session.commit()


async def main(images: int, queries: int, keep: bool) -> int:
    tax, similarity, config = load(), load_similarity(), _config()
    rng, np_rng = random.Random(7), np.random.default_rng(7)
    created_schema = await _ensure_schema()
    if created_schema:
        print("created the schema in the empty test database")
    existing = await _count()
    if existing:
        print(f"removing {existing} leftover synthetic rows")
        await _cleanup()
    try:
        async with AsyncSessionLocal() as session:
            print(f"populating {images} synthetic images")
            await _populate(session, images, rng, np_rng, tax)
        timings = []
        keys = {axis: list(tax.values[axis]) for axis in AXES}
        candidates = []
        async with AsyncSessionLocal() as session:
            repository = MemeRetrievalRepository(session)
            for _ in range(queries):
                vector = np_rng.standard_normal(PROFILE_EMBEDDING_DIM).astype(np.float32)
                vector = (vector / np.linalg.norm(vector)).tolist()
                axes = {axis: tuple((key, rng.choice([0.3, 0.6, 0.9])) for key in rng.sample(keys[axis], 2))
                        for axis in AXES}
                view = QueryView("synthetic query", axes, "en", False)
                started = time.perf_counter()
                candidates = await repository.generate_candidates(
                    query_vector=vector, query_axes=axes, neighbours=similarity.neighbours_for(axes),
                    lexical_text=None, filters=Filters(), restrict_ids=None, model_name=MODEL,
                    situation_k=config.situation_k, axis_k=config.axis_k, lexical_k=config.lexical_k,
                    total_cap=config.total_cap)
                scored = [fuse(c, view, config, similarity, lexical_active=False) for c in candidates]
                items = collapse_clusters(rank_candidates(scored))
                facet_counts(items)
                timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        p50 = timings[len(timings) // 2]
        p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
        verdict = "PASS" if p95 < BUDGET_MS else "FAIL"
        print(f"{images} images, {queries} queries: retrieval p50 {p50:.0f} ms, p95 {p95:.0f} ms "
              f"(candidates {len(candidates)}) -> {verdict} (budget: p95 under {BUDGET_MS} ms)")
        return 0 if verdict == "PASS" else 1
    finally:
        if not keep:
            print("removing synthetic rows")
            await _cleanup()
            print(f"synthetic rows remaining: {await _count()}")
        if created_schema and not keep:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
            print("dropped the schema this run created")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=25000)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--keep", action="store_true", help="leave the synthetic rows in place")
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.images, arguments.queries, arguments.keep)))
