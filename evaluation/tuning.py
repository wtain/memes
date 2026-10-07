"""Weight tuning on the gold set (P4 spec, decision 10). Pure: stdlib, evaluation.metrics and retrieval.

Candidates are collected once per query; every objective evaluation re-fuses them in memory."""
import itertools
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from evaluation.metrics import mean, query_metrics
from retrieval.config import RetrievalConfig, WEIGHT_KEYS
from retrieval.facets import collapse_clusters
from retrieval.fusion import fuse, rank_candidates
from retrieval.model import Candidate, QueryView
from retrieval.similarity import SimilarityIndex

GRID = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6)


@dataclass(frozen=True)
class PreparedQuery:
    query_id: str
    language: str
    view: QueryView
    candidates: Sequence[Candidate]
    lexical_active: bool
    grades: Mapping[str, int]
    degraded: bool = False
    fallback: str = "none"


def split_degraded(prepared: Sequence[PreparedQuery]) -> tuple[list[PreparedQuery], list[PreparedQuery]]:
    """(clean, degraded): a degraded profile (parser failure, open breaker) must not feed the tuning."""
    clean = [item for item in prepared if not item.degraded]
    return clean, [item for item in prepared if item.degraded]


def valid_weights(config: RetrievalConfig, weights: Mapping[str, float]) -> bool:
    """A vector the production configuration would accept: non-negative, situation > 0 (the degraded paths), lexical
    > 0 while the lexical source is enabled."""
    if any(value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
        return False
    if weights.get("situation", 0.0) <= 0:
        return False
    return not (config.lexical_enabled and weights.get("lexical", 0.0) <= 0)


def evaluate_weights(prepared: Sequence[PreparedQuery], config: RetrievalConfig, similarity: SimilarityIndex,
                     weights: Mapping[str, float], k: int = 10) -> float | None:
    """Mean nDCG@k over the queries that have a judged positive; None when there is none."""
    tuned = config.with_weights(weights)
    scores = []
    for item in prepared:
        if not item.view.present_axes(lexical_active=item.lexical_active):
            ids = []        # production answers such a query with an empty ranking (MemeFromTextPipeline.rank)
        else:
            scored = [fuse(candidate, item.view, tuned, similarity, lexical_active=item.lexical_active)
                      for candidate in item.candidates]
            ids = [entry.scored.candidate.image_id for entry in collapse_clusters(rank_candidates(scored))][:k]
        metrics = query_metrics(ids, item.grades, k=k)
        if metrics.ndcg is not None:
            scores.append(metrics.ndcg)
    return mean(scores)


def coordinate_ascent(prepared, config, similarity, start: Mapping[str, float], *, grid=GRID, passes: int = 3,
                      k: int = 10) -> tuple[dict, float]:
    """One weight at a time over the grid, keeping the best (ties keep the current value); a few passes."""
    weights = dict(start)
    best = evaluate_weights(prepared, config, similarity, weights, k) or 0.0
    for _pass in range(passes):
        improved = False
        for name in WEIGHT_KEYS:
            for value in grid:
                if value == weights[name]:
                    continue
                trial = {**weights, name: value}
                if not valid_weights(config, trial):
                    continue
                score = evaluate_weights(prepared, config, similarity, trial, k) or 0.0
                if score > best + 1e-9:
                    weights, best, improved = trial, score, True
        if not improved:
            break
    return weights, best


def simplex_points(step: float = 0.2):
    """Every weight vector over WEIGHT_KEYS whose values are multiples of `step` and sum to 1 (252 at step 0.2)."""
    units = round(1 / step)
    for combo in itertools.product(range(units + 1), repeat=len(WEIGHT_KEYS) - 1):
        remainder = units - sum(combo)
        if remainder >= 0:
            yield {name: round(count * step, 10) for name, count in zip(WEIGHT_KEYS, (*combo, remainder))}


def coarse_start(prepared, config, similarity, *, step: float = 0.2, k: int = 10) -> dict:
    """Best valid simplex grid point (ties: lexicographically smallest weight tuple, for determinism)."""
    best, best_score, best_key = None, -1.0, None
    for point in simplex_points(step):
        if not valid_weights(config, point):
            continue
        score = evaluate_weights(prepared, config, similarity, point, k) or 0.0
        key = tuple(point[name] for name in WEIGHT_KEYS)
        if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9 and key < best_key):
            best, best_score, best_key = point, score, key
    return best if best is not None else dict(config.weights)


def fit_weights(prepared, config, similarity, *, k: int = 10) -> tuple[dict, float, str]:
    """Coordinate ascent from the configured defaults and from the coarse simplex best; keep the better one
    (ties prefer the defaults). Returns (weights, in-sample score, "default" | "coarse")."""
    default_fit = coordinate_ascent(prepared, config, similarity, config.weights, k=k)
    coarse_fit = coordinate_ascent(prepared, config, similarity, coarse_start(prepared, config, similarity, k=k), k=k)
    if coarse_fit[1] > default_fit[1] + 1e-9:
        return coarse_fit[0], coarse_fit[1], "coarse"
    return default_fit[0], default_fit[1], "default"


def stratified_folds(items: Sequence, folds: int, seed: int) -> list[list]:
    """Shuffle (seeded) within each language and deal round-robin so every fold has every language."""
    if folds < 2:
        raise ValueError("folds must be at least 2")
    rng = random.Random(seed)
    by_language: dict[str, list] = {}
    for item in items:
        by_language.setdefault(item.language, []).append(item)
    result: list[list] = [[] for _ in range(folds)]
    position = 0
    for language in sorted(by_language):
        group = sorted(by_language[language], key=lambda item: item.query_id)
        rng.shuffle(group)
        for item in group:
            result[position % folds].append(item)
            position += 1
    return result


def cross_validate(prepared, config, similarity, *, folds: int = 5, seed: int = 7, k: int = 10) -> dict:
    parts = stratified_folds(prepared, folds, seed)
    scores = []
    for index, held_out in enumerate(parts):
        training = [item for other, part in enumerate(parts) if other != index for item in part]
        weights, _score, _start = fit_weights(training, config, similarity, k=k)
        score = evaluate_weights(held_out, config, similarity, weights, k)
        if score is not None:       # a fold without a judged positive says nothing; it is skipped, not a zero
            scores.append(score)
    if not scores:
        return {"foldScores": [], "cvScore": 0.0, "foldSpread": 0.0}
    return {"foldScores": scores, "cvScore": sum(scores) / len(scores), "foldSpread": max(scores) - min(scores)}


def tune(prepared, config, similarity, *, folds: int = 5, seed: int = 7, k: int = 10) -> dict:
    default_score = evaluate_weights(prepared, config, similarity, config.weights, k) or 0.0
    weights, in_sample, started_from = fit_weights(prepared, config, similarity, k=k)
    cv = cross_validate(prepared, config, similarity, folds=folds, seed=seed, k=k)
    return {"defaultWeights": dict(config.weights), "defaultScore": default_score, "recommendedWeights": weights,
            "inSampleScore": in_sample, "startedFrom": started_from, **cv}
