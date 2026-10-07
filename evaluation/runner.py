"""Run a retriever over the gold queries and summarise the result (P6 spec, decision 8). Pure."""
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from evaluation.gold import GoldQuery
from evaluation.metrics import QueryMetrics, aggregate, language_ratio, mean, query_metrics


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    language: str
    length: str
    difficulty: str
    ranked: tuple[str, ...]
    metrics: QueryMetrics


async def run_retriever(retriever, queries: Sequence[GoldQuery], corpus_ids: Sequence[str],
                        judgments: Mapping[str, Mapping[str, int]], *, k: int = 10) -> list[QueryResult]:
    """Evaluate every reviewed query. Unreviewed queries never take part in an official run."""
    reviewed = [query for query in queries if query.reviewed]
    prepare = getattr(retriever, "prepare", None)
    if prepare is not None:
        await prepare(reviewed)
    results = []
    for query in reviewed:
        ranked = await retriever.retrieve(query, corpus_ids, k)
        ids = tuple(str(image_id) for image_id, _ in ranked[:k])
        results.append(QueryResult(
            query_id=query.id, language=query.language, length=query.length, difficulty=query.difficulty,
            ranked=ids, metrics=query_metrics(list(ids), judgments.get(query.id, {}), k=k)))
    return results


def _percentile(sorted_values: Sequence[float], fraction: float) -> int:
    return int(sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))])


def latency_summary(query_meta: Sequence[Mapping]) -> dict:
    """p50/p95 of the per-query timings of a fused run plus the degraded queries with their fallback reasons."""
    summary: dict = {"queries": len(query_meta)}
    for field in ("retrievalMs", "parseMs", "embedMs"):
        values = sorted(item.get(field, 0) for item in query_meta)
        summary[field] = {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)} if values else None
    summary["degraded"] = [{"queryId": item["queryId"], "fallback": item["fallback"]}
                           for item in query_meta if item.get("degraded")]
    return summary


def format_latency(summary: Mapping) -> str:
    lines = [f"latency over {summary['queries']} queries (ms, p50 / p95):"]
    for field in ("retrievalMs", "parseMs", "embedMs"):
        stats = summary[field]
        lines.append(f"  {field}: " + ("-" if stats is None else f"{stats['p50']} / {stats['p95']}"))
    degraded = summary["degraded"]
    lines.append(f"  degraded queries: {len(degraded)}"
                 + ("" if not degraded else " (" + ", ".join(f"{d['queryId']}: {d['fallback']}" for d in degraded) + ")"))
    return "\n".join(lines)


def summarize(results: Sequence[QueryResult], strata: Mapping[str, str],
              judgments: Mapping[str, Mapping[str, int]], k: int = 10) -> dict:
    def group(key) -> dict:
        groups: dict[str, list[QueryMetrics]] = defaultdict(list)
        for result in results:
            groups[key(result)].append(result.metrics)
        return {name: aggregate(items) for name, items in sorted(groups.items())}

    by_language = group(lambda r: r.language)
    by_stratum = {}
    for stratum in sorted(set(strata.values())):
        shares, grades = [], []
        for result in results:
            top = result.ranked[:k]
            if top:
                shares.append(sum(1 for image_id in top if strata.get(image_id) == stratum) / len(top))
            judged = judgments.get(result.query_id, {})
            grades.extend(judged[i] for i in top if strata.get(i) == stratum and i in judged)
        by_stratum[stratum] = {"retrievedShare": mean(shares), "meanGradeJudged": mean(grades),
                               "judgedCount": len(grades)}
    return {
        "overall": aggregate([r.metrics for r in results]),
        "byLanguage": by_language,
        "byDifficulty": group(lambda r: r.difficulty),
        "byLength": group(lambda r: r.length),
        "byStratum": by_stratum,
        "languageRatio": language_ratio(by_language),
    }
