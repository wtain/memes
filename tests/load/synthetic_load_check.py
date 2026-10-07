"""Synthetic load check for the P4 retrieval stage (spec decision 11). TEST DATABASE ONLY.

    DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONPATH=. \
        python tests/load/synthetic_load_check.py --images 25000 --queries 50

Fills ocrdb_test with synthetic images, profiles, normalised random situation vectors and a few OCR lemmas per
image, measures the retrieval stage (candidate SQL, scoring, fusion, collapse, facets; no parsing, no embedding
call) and removes its rows. `--lexical-tokens N` (or `--lexical-text TEXT`) switches the lexical source on, and
the lexical source is then also timed on its own.
It never runs against anything but a database named exactly `ocrdb_test`, and it never drops the schema."""
import argparse
import asyncio
import os
import random
import statistics
import sys
import time
import uuid

import numpy as np
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.engine import make_url


def _database_is_test() -> bool:
    try:
        return make_url(os.environ["DATABASE_URL"]).database == "ocrdb_test"
    except Exception:
        return False


if not _database_is_test():
    sys.exit("Refusing to run: DATABASE_URL must point at the test database (ocrdb_test).")

from repository.meme_retrieval import MemeRetrievalRepository                      # noqa: E402
from repository.ocr_lemmas import lexical_or_scores                                  # noqa: E402
from retrieval.config import AXES, RetrievalConfig                                   # noqa: E402
from retrieval.facets import collapse_clusters, facet_counts                         # noqa: E402
from retrieval.fusion import fuse, rank_candidates                                   # noqa: E402
from retrieval.model import Filters, QueryView                                       # noqa: E402
from retrieval.similarity import load_similarity                                     # noqa: E402
from Storage.db import AsyncSessionLocal, engine                                     # noqa: E402
from Storage.models import (                                                         # noqa: E402
    PROFILE_EMBEDDING_DIM, Base, Image, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue, OCRLemma,
    TmpImageClusters,
)
from taxonomy import load                                                            # noqa: E402

PREFIX = "synthetic-load-"
MODEL = "bge-large"
BATCH = 1000            # must stay a multiple of CLUSTER_BLOCK
CLUSTER_BLOCK = 10      # every fifth block of ten images forms one duplicate cluster of exactly ten
SAMPLE_VECTORS = 200
WARMUP_QUERIES = 3
BUDGET_MS = 300
INT_MAX_SAFE = 2_147_000_000   # tmp_clusters.cluster_id is a 32-bit Integer
ANALYZED = (Image, MemeProfile, MemeProfileValue, MemeProfileSituationEmbedding, TmpImageClusters, OCRLemma)
VOCABULARY_SIZE = 3000
LEMMAS_PER_IMAGE = 5
_CONSONANTS, _VOWELS = "bdfgklmnprstvz", "aeiou"


def _vocabulary(rng: random.Random) -> list[str]:
    """3000 distinct pronounceable pseudo-words (three consonant-vowel syllables), language-agnostic."""
    syllables = [c + v for c in _CONSONANTS for v in _VOWELS]
    words: set[str] = set()
    while len(words) < VOCABULARY_SIZE:
        words.add("".join(rng.choice(syllables) for _ in range(3)))
    return sorted(words)


def _config() -> RetrievalConfig:
    """Production defaults for the candidate caps and weights, built directly (no import of the test helpers)."""
    return RetrievalConfig(
        weights={"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10, "lexical": 0.10},
        situation_k=300, axis_k=300, lexical_k=100, total_cap=600, neutral_score=0.3, situation_floor=0.45,
        situation_ceil=0.85, use_neighbours=True, lexical_enabled=True, lexical_max_tokens=20, lexical_only_when_degraded=False, cache_ttl_seconds=120.0,
        embed_timeout_seconds=2.0, warm_interval_minutes=20.0, breaker_failures=3, breaker_open_seconds=30.0,
        default_limit=20, max_limit=50, embedding_model=MODEL, ollama_url="http://127.0.0.1:11434")


async def _populate(session, count: int, rng: random.Random, np_rng, tax, cluster_base: int,
                    vocabulary: list[str]) -> list[np.ndarray]:
    """Inserts the rows and returns a small sample of the inserted situation vectors (for realistic queries)."""
    keys = {axis: list(tax.values[axis]) for axis in AXES}
    sample: list[np.ndarray] = []
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
        await session.execute(insert(OCRLemma), [
            {"image_id": image_id, "lemma": lemma}
            for image_id in ids for lemma in rng.sample(vocabulary, LEMMAS_PER_IMAGE)])
        vectors = np_rng.standard_normal((size, PROFILE_EMBEDDING_DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        await session.execute(insert(MemeProfileSituationEmbedding), [
            {"image_id": image_id, "embedding": vector.tolist(), "model_name": MODEL, "model_digest": "synthetic"}
            for image_id, vector in zip(ids, vectors)])
        clustered = []
        for index, image_id in enumerate(ids):
            block = (start + index) // CLUSTER_BLOCK
            if block % 5 == 0:
                clustered.append({"cluster_id": cluster_base + block, "image_id": image_id})
        await session.execute(insert(TmpImageClusters), clustered)
        await session.commit()
        wanted = SAMPLE_VECTORS - len(sample)
        if wanted > 0:
            sample.extend(vectors[:min(wanted, size, 40)])
        print(f"  inserted {start + size} / {count}", flush=True)
    return sample


async def _ensure_tables() -> None:
    """The test database only has tables while a pytest run is active. create_all checks first, so it only adds
    what is missing and never touches existing tables. The script never drops anything."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _count() -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(func.count()).select_from(Image).where(Image.filename.like(f"{PREFIX}%")))).scalar_one()


async def _cluster_base() -> int:
    async with AsyncSessionLocal() as session:
        highest = (await session.execute(select(func.max(TmpImageClusters.cluster_id)))).scalar_one()
    return max(highest or 0, 0) + 1_000_000


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        ids = select(Image.id).where(Image.filename.like(f"{PREFIX}%"))
        await session.execute(delete(TmpImageClusters).where(TmpImageClusters.image_id.in_(ids)))
        await session.execute(delete(Image).where(Image.filename.like(f"{PREFIX}%")))
        await session.commit()


async def _safe_cleanup() -> None:
    """Cleanup that cannot mask the original error: a failure here is printed, not raised."""
    try:
        await _cleanup()
        print(f"synthetic rows remaining: {await _count()}")
    except Exception as error:  # noqa: BLE001
        print(f"WARNING: cleanup failed ({type(error).__name__}: {error}); synthetic rows may remain")


def _query_vector(np_rng, sample: list[np.ndarray]) -> list[float]:
    """0.6 x a random inserted vector + 0.8 x noise, normalised, so some memes clear the situation floor."""
    noise = np_rng.standard_normal(PROFILE_EMBEDDING_DIM).astype(np.float32)
    noise /= np.linalg.norm(noise)
    base = sample[int(np_rng.integers(len(sample)))]
    mixed = 0.6 * base + 0.8 * noise
    return (mixed / np.linalg.norm(mixed)).tolist()


async def main(images: int, queries: int, keep: bool, lexical_text: str | None = None,
               lexical_tokens: int | None = None) -> int:
    tax, similarity, config = load(), load_similarity(), _config()
    rng, np_rng = random.Random(7), np.random.default_rng(7)
    vocabulary = _vocabulary(random.Random(11))
    lexical_on = bool(lexical_text) or bool(lexical_tokens)
    lexical_texts: list[str] = []
    keys = {axis: list(tax.values[axis]) for axis in AXES}
    try:
        await _ensure_tables()
        existing = await _count()
        if existing:
            print(f"removing {existing} leftover synthetic rows")
            await _cleanup()
        cluster_base = await _cluster_base()
        if cluster_base + images // CLUSTER_BLOCK >= INT_MAX_SAFE:
            raise RuntimeError("cluster ids would overflow tmp_clusters.cluster_id")
        async with AsyncSessionLocal() as session:
            print(f"populating {images} synthetic images")
            sample = await _populate(session, images, rng, np_rng, tax, cluster_base, vocabulary)
        async with AsyncSessionLocal() as session:
            for model in ANALYZED:
                await session.execute(text(f'ANALYZE "{model.__tablename__}"'))
            await session.commit()

        candidate_ms: list[float] = []      # candidate generation (SQL, including the lexical source and loading)
        scoring_ms: list[float] = []        # scoring, fusion, collapse, facets

        async def one_query(repository: MemeRetrievalRepository) -> tuple[float, int]:
            vector = _query_vector(np_rng, sample)
            axes = {axis: tuple((key, rng.choice([0.3, 0.6, 0.9])) for key in rng.sample(keys[axis], 2))
                    for axis in AXES}
            view = QueryView("synthetic query", axes, "en", False)
            text_for_query = None
            if lexical_on:
                text_for_query = lexical_text or " ".join(rng.sample(vocabulary, lexical_tokens))
                lexical_texts.append(text_for_query)
            started = time.perf_counter()
            candidates = await repository.generate_candidates(
                query_vector=vector, query_axes=axes, neighbours=similarity.neighbours_for(axes),
                lexical_text=text_for_query, filters=Filters(), restrict_ids=None, model_name=MODEL,
                situation_k=config.situation_k, axis_k=config.axis_k, lexical_k=config.lexical_k,
                total_cap=config.total_cap,
                lexical_max_tokens=config.lexical_max_tokens)
            generated = time.perf_counter()
            candidate_ms.append((generated - started) * 1000)
            lexical_active = text_for_query is not None and any(c.lexical > 0 for c in candidates)
            scored = [fuse(c, view, config, similarity, lexical_active=lexical_active)
                      for c in candidates]
            items = collapse_clusters(rank_candidates(scored))
            facet_counts(items)
            scoring_ms.append((time.perf_counter() - generated) * 1000)
            return (time.perf_counter() - started) * 1000, len(candidates)

        timings, counts = [], []
        async with AsyncSessionLocal() as session:
            repository = MemeRetrievalRepository(session)
            for _ in range(WARMUP_QUERIES):
                await one_query(repository)
            candidate_ms.clear()
            scoring_ms.clear()
            for _ in range(queries):
                elapsed, count = await one_query(repository)
                timings.append(elapsed)
                counts.append(count)
        lexical_timings: list[float] = []
        if lexical_on:
            async with AsyncSessionLocal() as session:        # the lexical source alone, same texts
                for text_value in lexical_texts[WARMUP_QUERIES:]:
                    started = time.perf_counter()
                    await lexical_or_scores(session, text_value, max_tokens=config.lexical_max_tokens)
                    lexical_timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        p50 = timings[len(timings) // 2]
        p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
        verdict = "PASS" if p95 < BUDGET_MS else "FAIL"
        print(f"{images} images, {queries} queries: retrieval p50 {p50:.0f} ms, p95 {p95:.0f} ms "
              f"(candidates median {statistics.median(counts):.0f}) -> {verdict} (budget: p95 under {BUDGET_MS} ms)")
        print(f"stage split p50: candidate generation {statistics.median(candidate_ms):.0f} ms, "
              f"scoring/fusion/collapse/facets {statistics.median(scoring_ms):.0f} ms")
        if lexical_timings:
            lexical_timings.sort()
            tokens = len(set(lexical_texts[-1].split()))
            print(f"lexical source alone ({tokens} tokens in the last message): "
                  f"p50 {lexical_timings[len(lexical_timings) // 2]:.0f} ms, "
                  f"p95 {lexical_timings[min(len(lexical_timings) - 1, int(len(lexical_timings) * 0.95))]:.0f} ms")
        return 0 if verdict == "PASS" else 1
    finally:
        if not keep:
            print("removing synthetic rows")
            await _safe_cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=25000)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--keep", action="store_true", help="leave the synthetic rows in place")
    parser.add_argument("--lexical-text", default=None, help="message for the lexical source (default: off)")
    parser.add_argument("--lexical-tokens", type=int, default=None,
                        help="without --lexical-text: a synthetic message of this many distinct tokens per query, "
                             "sampled from the lemmas of the synthetic images")
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.images, arguments.queries, arguments.keep, arguments.lexical_text,
                              arguments.lexical_tokens)))
