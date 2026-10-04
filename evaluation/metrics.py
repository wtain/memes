"""Ranking metrics for the meme-from-text gold set (P6 spec, decision 8). Pure: standard library only."""
import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class QueryMetrics:
    ndcg: float | None
    recall: float | None
    hit: float | None
    judged_share: float | None
    has_positive: bool


def _dcg(grades: Sequence[int]) -> float:
    return sum(grade / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


def query_metrics(ranked: Sequence[str], grades: Mapping[str, int], k: int = 10) -> QueryMetrics:
    """Metrics of one ranked list against one query's judgments. Unjudged results count as grade 0.
    ndcg, recall and hit are None when the query has no judged positive (it cannot be scored)."""
    top = list(ranked[:k])
    gains = [grades.get(image_id, 0) for image_id in top]
    positives = {image_id for image_id, grade in grades.items() if grade >= 1}
    ideal_dcg = _dcg(sorted(grades.values(), reverse=True)[:k])

    ndcg = _dcg(gains) / ideal_dcg if ideal_dcg > 0 else None
    recall = len(positives.intersection(top)) / len(positives) if positives else None
    hit = (1.0 if any(grade >= 1 for grade in gains) else 0.0) if positives else None
    judged_share = sum(1 for image_id in top if image_id in grades) / len(top) if top else None
    return QueryMetrics(ndcg, recall, hit, judged_share, bool(positives))


def mean(values) -> float | None:
    defined = [value for value in values if value is not None]
    return sum(defined) / len(defined) if defined else None


def aggregate(items: Sequence[QueryMetrics]) -> dict:
    return {
        "queries": len(items),
        "noPositive": sum(1 for item in items if not item.has_positive),
        "ndcg": mean(item.ndcg for item in items),
        "recall": mean(item.recall for item in items),
        "hit": mean(item.hit for item in items),
        "judged": mean(item.judged_share for item in items),
    }


def language_ratio(by_language: Mapping[str, Mapping]) -> dict[str, float | None]:
    """nDCG of each language relative to English (user story US-7); None when English is missing or zero."""
    base = by_language.get("en", {}).get("ndcg")
    ratios: dict[str, float | None] = {}
    for language, stats in by_language.items():
        value = stats.get("ndcg")
        ratios[language] = value / base if base and value is not None else None
    return ratios
