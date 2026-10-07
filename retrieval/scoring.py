"""Per-axis scores in 0..1 (P4 spec, decision 4). Pure."""
from typing import Sequence

from retrieval.model import AxisValues, Match
from retrieval.similarity import SimilarityIndex


def situation_score(cosine: float | None, floor: float, ceil: float, neutral: float) -> tuple[float, bool]:
    """Cosine similarity rescaled between a floor and a ceiling; neutral (flagged) without an embedding."""
    if cosine is None:
        return neutral, True
    return min(max((cosine - floor) / (ceil - floor), 0.0), 1.0), False


def axis_score(axis: str, query_values: AxisValues, meme_values: AxisValues, similarity: SimilarityIndex,
               neutral: float, use_neighbours: bool) -> tuple[float, bool, tuple[Match, ...]]:
    """Confidence-weighted match of the query values against the meme values on one controlled axis.

    match(k) = max over meme values m of sim(k, m) x meme confidence (1 for the same key, the curated neighbour
    score for neighbours); score = sum(query confidence x match) / sum(query confidence). A meme with no value
    on the axis scores `neutral` (flagged), not zero (FR-15)."""
    if not meme_values:
        return neutral, True, ()
    total = sum(confidence for _key, confidence in query_values)
    equal_weights = total <= 0
    if equal_weights:
        total = float(len(query_values))
    weighted = 0.0
    matched: list[Match] = []
    for query_key, query_confidence in query_values:
        best, best_match = 0.0, None
        for meme_key, meme_confidence in meme_values:
            sim = 1.0 if meme_key == query_key else (
                similarity.similarity(axis, query_key, meme_key) if use_neighbours else 0.0)
            value = sim * meme_confidence
            if value > best:
                best, best_match = value, Match(query_key, meme_key, round(sim, 6), meme_confidence)
        weighted += (1.0 if equal_weights else query_confidence) * best
        if best_match is not None:
            matched.append(best_match)
    return weighted / total, False, tuple(matched)
