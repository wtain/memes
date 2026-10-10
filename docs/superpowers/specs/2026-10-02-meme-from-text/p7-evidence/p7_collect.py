"""P7 memo evidence, step 1: run the production fused pipeline DEEP (top N) on the gold queries and store the raw rankings.
Read-only: DATABASE_URL must be the read-only role (the evaluation CLI's own rule). Writes only to the scratchpad."""
import asyncio
import json
import sys
from pathlib import Path

from evaluation.__main__ import require_readonly
from evaluation.gold import load_corpus, load_queries
from evaluation.retrievers.fused import FusedRetriever
from Storage.db import AsyncSessionLocal

OUT = Path(sys.argv[1])
MODEL = sys.argv[2]
DEPTH = int(sys.argv[3])
GOLD = Path("evaluation/gold")


def build_pipeline(parse_model):
    """Same as evaluation.__main__._build_pipeline, but patient: a long parse/embed timeout and no breaker, so a busy GPU
    cannot degrade the run (this changes latency only, never the ranking quality being measured)."""
    from config.settings import settings
    from parse.config import load_config as load_parse_config
    from parse.parser import QueryParser
    from retrieval.config import load_config
    from retrieval.embed import OllamaEmbedClient
    from retrieval.pipeline import MemeFromTextPipeline
    from retrieval.similarity import load_similarity
    from Storage.models import PROFILE_EMBEDDING_DIM

    overrides = {"parse.timeout_seconds": 120, "parse.retry_min_seconds": 5, "parser_breaker.failures": 100000,
                 "search.meme_from_text.parser_breaker.failures": 100000}

    def get(key, default=None):
        if key == "parse.model":
            return parse_model
        if key in overrides:
            return overrides[key]
        return settings.get(key, default)

    parser = QueryParser(load_parse_config(get))
    config = load_config(get)
    from dataclasses import replace
    config = replace(config, breaker_failures=100000, embed_timeout_seconds=120.0)
    return MemeFromTextPipeline(config, parser, OllamaEmbedClient(config.ollama_url, PROFILE_EMBEDDING_DIM),
                                load_similarity(), max_input_chars=int(settings.get("parse.max_input_chars")))


async def main():
    require_readonly()
    corpus = load_corpus(GOLD / "corpus.json")
    queries = [q for q in load_queries(GOLD / "queries.json") if q.reviewed]
    async with AsyncSessionLocal() as session:
        retriever = FusedRetriever(session, build_pipeline(MODEL))
        await retriever.prepare(queries)
        raw = {}
        for query in queries:
            ranked = await retriever.retrieve(query, list(corpus.image_ids()), DEPTH)
            raw[query.id] = [[str(image_id), float(score)] for image_id, score in ranked]
        meta = {m["queryId"]: m for m in retriever.query_meta}
    OUT.write_text(json.dumps({"depth": DEPTH, "model": MODEL, "rankings": raw, "meta": meta}), encoding="utf-8")
    sizes = sorted(len(v) for v in raw.values())
    print(f"{len(raw)} queries; list sizes min {sizes[0]} median {sizes[len(sizes) // 2]} max {sizes[-1]}; "
          f"degraded {[q for q, m in meta.items() if m.get('fallback') not in (None, 'none')]}")


asyncio.run(main())
