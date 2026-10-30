"""Relevance scoring primitives for smart search: weights from config and the pure scorer.

See docs/superpowers/specs/2026-10-01-search-result-ranking-design.md and
docs/adr/adr-2026-10-01-search-ranking-in-python.md.
"""
import math
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
    try:
        ranking = settings.SEARCH.RANKING
        source_cfg = ranking.SOURCE_WEIGHTS
        tier_cfg = ranking.TIER_WEIGHTS
        warn_raw = ranking.WARN_MATCH_COUNT
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"search.ranking missing: {exc}") from exc
    missing = [f"source_weights.{k}" for k in SOURCES if k not in source_cfg]
    missing += [f"tier_weights.{k}" for k in TIERS if k not in tier_cfg]
    if missing:
        raise ValueError(f"search.ranking is missing keys: {', '.join(missing)}")

    # A weight <= 0 would make the max-merge treat a real hit as "no hit" and so change which
    # images match, so every weight must be a finite number strictly above zero.
    problems = []
    source, tier = {}, {}
    for target, cfg, prefix, keys in (
        (source, source_cfg, "source_weights", SOURCES),
        (tier, tier_cfg, "tier_weights", TIERS),
    ):
        for k in keys:
            raw = cfg[k]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            if isinstance(raw, bool) or value is None or not math.isfinite(value) or value <= 0:
                problems.append(f"{prefix}.{k}={raw!r}")
            else:
                target[k] = value
    warn = None
    if isinstance(warn_raw, int) and not isinstance(warn_raw, bool) and warn_raw >= 1:
        warn = warn_raw
    else:
        problems.append(f"warn_match_count={warn_raw!r}")
    if problems:
        raise ValueError(
            "search.ranking has invalid values (weights must be finite numbers > 0, "
            f"warn_match_count an int >= 1): {', '.join(problems)}"
        )
    return RankingWeights(source=source, tier=tier, warn_match_count=warn)


@dataclass(frozen=True)
class PopularityBoost:
    """Capped popularity term added to a text-search score (P5 design: L1). weight 0 switches it off."""
    weight: float
    max_log2: float

    def bonus(self, popularity: int) -> float:
        """weight * min(log2(max(1, popularity)), max_log2), unrounded."""
        return self.weight * min(math.log2(max(1, popularity)), self.max_log2)


def _finite_number(raw):
    """float(raw) for a real int/float that is finite, else None (bool and str are rejected)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None
    return float(raw)


@lru_cache(maxsize=1)
def get_popularity_boost() -> PopularityBoost:
    """search.ranking.popularity.{weight,max_log2}; see
    docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p5-ranking-and-trending-design.md."""
    try:
        block = settings.SEARCH.RANKING.POPULARITY
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"search.ranking.popularity missing: {exc}") from exc
    raw = {}
    missing = []
    for key in ("weight", "max_log2"):
        try:
            raw[key] = getattr(block, key.upper())
        except (AttributeError, KeyError):
            missing.append(key)
    if missing:
        raise ValueError(f"search.ranking.popularity is missing keys: {', '.join(missing)}")
    weight = _finite_number(raw["weight"])
    max_log2 = _finite_number(raw["max_log2"])
    problems = []
    if weight is None or weight < 0:
        problems.append(f"weight={raw['weight']!r}")
    if max_log2 is None or max_log2 <= 0:
        problems.append(f"max_log2={raw['max_log2']!r}")
    if problems:
        raise ValueError(
            "search.ranking.popularity has invalid values (weight a finite number >= 0, "
            f"max_log2 a finite number > 0): {', '.join(problems)}"
        )
    return PopularityBoost(weight=weight, max_log2=max_log2)


@lru_cache(maxsize=1)
def get_trending_min_popularity() -> int:
    """search.trending.min_popularity: the fewest copies a meme needs to be listed as trending (int >= 1)."""
    try:
        raw = settings.SEARCH.TRENDING.MIN_POPULARITY
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"search.trending.min_popularity missing: {exc}") from exc
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"search.trending.min_popularity must be an int >= 1, got {raw!r}")
    return raw


def validate_search_settings() -> None:
    """Load every search setting this module owns so a missing or invalid key is a startup error (called from the
    backend lifespan) instead of a 500 on the first request that needs it."""
    get_weights()
    get_popularity_boost()
    get_trending_min_popularity()


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
