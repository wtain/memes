"""Weight tuning on the gold set (P4 spec, decision 10). Pure: stdlib, evaluation.metrics and retrieval.

Candidates are collected once per query; every objective evaluation re-fuses them in memory."""
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


def evaluate_weights(prepared: Sequence[PreparedQuery], config: RetrievalConfig, similarity: SimilarityIndex,
                     weights: Mapping[str, float], k: int = 10) -> float | None:
    """Mean nDCG@k over the queries that have a judged positive; None when there is none."""
    tuned = config.with_weights(weights)
    scores = []
    for item in prepared:
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
                if sum(trial.values()) <= 0:
                    continue
                score = evaluate_weights(prepared, config, similarity, trial, k) or 0.0
                if score > best + 1e-9:
                    weights, best, improved = trial, score, True
        if not improved:
            break
    return weights, best


def stratified_folds(items: Sequence, folds: int, seed: int) -> list[list]:
    """Shuffle (seeded) within each language and deal round-robin so every fold has every language."""
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
        weights, _score = coordinate_ascent(training, config, similarity, config.weights, k=k)
        scores.append(evaluate_weights(held_out, config, similarity, weights, k) or 0.0)
    return {"foldScores": scores, "cvScore": sum(scores) / len(scores), "foldSpread": max(scores) - min(scores)}


def tune(prepared, config, similarity, *, folds: int = 5, seed: int = 7, k: int = 10) -> dict:
    default_score = evaluate_weights(prepared, config, similarity, config.weights, k) or 0.0
    weights, in_sample = coordinate_ascent(prepared, config, similarity, config.weights, k=k)
    cv = cross_validate(prepared, config, similarity, folds=folds, seed=seed, k=k)
    return {"defaultWeights": dict(config.weights), "defaultScore": default_score, "recommendedWeights": weights,
            "inSampleScore": in_sample, **cv}
