"""Relevance scoring primitives for smart search: weights from config and the pure scorer.

See docs/superpowers/specs/2026-10-01-search-result-ranking-design.md and
docs/adr/adr-2026-10-01-search-ranking-in-python.md.
"""
import uuid
from dataclasses import dataclass
from functools import lru_cache

from config.settings import settings

SOURCES = ("note", "ocr", "tag", "description")
TIERS = ("exact", "stem", "fuzzy", "phonetic")

_SCORE_DECIMALS = 6


@dataclass(frozen=True)
class RankingWeights:
    source: dict
    tier: dict
    warn_match_count: int

    def hit(self, source: str, tier: str) -> float:
        """Weight of one hit: the source's weight times the match tier's weight."""
        return round(self.source[source] * self.tier[tier], _SCORE_DECIMALS)


@lru_cache(maxsize=1)
def get_weights() -> RankingWeights:
    ranking = settings.SEARCH.RANKING
    source_cfg = ranking.SOURCE_WEIGHTS
    tier_cfg = ranking.TIER_WEIGHTS
    missing = [f"source_weights.{k}" for k in SOURCES if k not in source_cfg]
    missing += [f"tier_weights.{k}" for k in TIERS if k not in tier_cfg]
    if missing:
        raise ValueError(f"search.ranking is missing keys: {', '.join(missing)}")
    return RankingWeights(
        source={k: float(source_cfg[k]) for k in SOURCES},
        tier={k: float(tier_cfg[k]) for k in TIERS},
        warn_match_count=int(ranking.WARN_MATCH_COUNT),
    )


def score_images(token_hits: list[dict[uuid.UUID, float]]) -> dict[uuid.UUID, float]:
    """token_hits[i] maps image id -> that image's best hit weight for query token i. Returns the images
    present for EVERY token with score = sum of their per-token weights (AND semantics)."""
    if not token_hits:
        return {}
    candidates = set(token_hits[0])
    for hits in token_hits[1:]:
        candidates &= set(hits)
    return {
        image_id: round(sum(hits[image_id] for hits in token_hits), _SCORE_DECIMALS)
        for image_id in candidates
    }
