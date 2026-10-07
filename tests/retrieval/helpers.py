from retrieval.config import RetrievalConfig
from retrieval.model import Candidate
from retrieval.similarity import SimilarityIndex

WEIGHTS = {"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10, "lexical": 0.10}


def make_config(**overrides) -> RetrievalConfig:
    values = dict(
        weights=dict(WEIGHTS), situation_k=300, axis_k=300, lexical_k=100, total_cap=600, neutral_score=0.3,
        situation_floor=0.45, situation_ceil=0.85, use_neighbours=True, lexical_enabled=True, lexical_max_tokens=20,
        cache_ttl_seconds=120.0, embed_timeout_seconds=2.0, warm_interval_minutes=20.0, breaker_failures=3,
        breaker_open_seconds=30.0, default_limit=20, max_limit=50, embedding_model="bge-large",
        ollama_url="http://127.0.0.1:11434")
    values.update(overrides)
    return RetrievalConfig(**values)


def make_similarity() -> SimilarityIndex:
    return SimilarityIndex({
        "emotion": {"anger": {"annoyance": 0.8}, "annoyance": {"anger": 0.8}},
        "stance": {"celebrate": {"congratulate": 0.8}, "congratulate": {"celebrate": 0.8}},
    })


def make_candidate(image_id="a", *, cosine=0.85, values=None, lexical=0.0, axis_path=True, cluster_id=None,
                   situation_text="a situation", use_when="Use when it fits.") -> Candidate:
    return Candidate(image_id=image_id, situation_text=situation_text, use_when=use_when, cosine=cosine,
                     values=values or {}, lexical=lexical, axis_path=axis_path, cluster_id=cluster_id)
