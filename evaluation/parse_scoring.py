"""Scoring of the query parser against the gold expected profiles (P3 spec, decision 9). Pure: standard library
and evaluation.gold only."""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from evaluation.gold import CONTROLLED_AXES, ExpectedProfile, _read, _write

FORMAT_VERSION = 1


@dataclass(frozen=True)
class AxisScore:
    precision: float
    recall: float
    f1: float
    top1: bool | None        # None when the query expects no value on this axis


@dataclass(frozen=True)
class QueryParseScore:
    query_id: str
    language: str
    detected_language: str
    degraded: bool
    fallback: str
    latency_ms: int
    axes: Mapping[str, AxisScore]
    situation_similarity: float | None


def score_axis(expected: Sequence[str], parsed: Sequence[str]) -> AxisScore:
    """Set-level precision, recall and F1 of the parsed keys against the expected keys; `top1` asks whether the
    first (highest-confidence) parsed key is expected. Both empty is a perfect agreement; one empty is zero."""
    expected_list, parsed_list = list(expected), list(parsed)
    if not expected_list and not parsed_list:
        return AxisScore(1.0, 1.0, 1.0, None)
    top1 = None if not expected_list else bool(parsed_list) and parsed_list[0] in expected_list
    if not expected_list or not parsed_list:
        return AxisScore(0.0, 0.0, 0.0, top1)
    hits = len(set(expected_list) & set(parsed_list))
    precision = hits / len(set(parsed_list))
    recall = hits / len(set(expected_list))
    f1 = 0.0 if hits == 0 else 2 * precision * recall / (precision + recall)
    return AxisScore(precision, recall, f1, top1)


def score_query(query_id: str, language: str, expected: ExpectedProfile, profile: Mapping, meta: Mapping,
                situation_similarity: float | None = None) -> QueryParseScore:
    axes = {}
    for axis in CONTROLLED_AXES:
        parsed = [value["key"] for value in profile.get(axis, [])]
        axes[axis] = score_axis(getattr(expected, axis), parsed)
    return QueryParseScore(
        query_id=query_id, language=language, detected_language=profile.get("language", ""),
        degraded=bool(profile.get("degraded")), fallback=meta.get("fallback", ""),
        latency_ms=int(meta.get("latencyMs", 0)), axes=axes, situation_similarity=situation_similarity)


def percentile(values: Sequence[float], p: float):
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(p / 100 * len(ordered)), 1)
    return ordered[rank - 1]


def _mean(values):
    defined = [value for value in values if value is not None]
    return sum(defined) / len(defined) if defined else None


def aggregate_parse(scores: Sequence[QueryParseScore]) -> dict:
    axes = {}
    for axis in CONTROLLED_AXES:
        per_query = [score.axes[axis] for score in scores]
        axes[axis] = {
            "precision": _mean(item.precision for item in per_query),
            "recall": _mean(item.recall for item in per_query),
            "f1": _mean(item.f1 for item in per_query),
            "top1": _mean(None if item.top1 is None else float(item.top1) for item in per_query),
        }
    latencies = [score.latency_ms for score in scores]
    return {
        "queries": len(scores),
        "languageAccuracy": _mean(float(score.detected_language == score.language) for score in scores),
        "degradedRate": _mean(float(score.degraded) for score in scores),
        "macroF1": _mean(_mean(item.f1 for item in score.axes.values()) for score in scores),
        "axes": axes,
        "situationSimilarity": _mean(score.situation_similarity for score in scores),
        "latencyP50": percentile(latencies, 50),
        "latencyP95": percentile(latencies, 95),
    }


def summarize_parse(scores: Sequence[QueryParseScore]) -> dict:
    by_language: dict[str, list[QueryParseScore]] = {}
    for score in scores:
        by_language.setdefault(score.language, []).append(score)
    return {"overall": aggregate_parse(scores),
            "byLanguage": {language: aggregate_parse(items) for language, items in sorted(by_language.items())}}


def build_parse_report(*, label: str, date: str, model: str, parser_version: str, taxonomy_version: int,
                       queries_sha: str, scores: Sequence[QueryParseScore], parsed: Mapping[str, Mapping]) -> dict:
    return {
        "version": FORMAT_VERSION, "label": label, "date": date, "model": model, "parserVersion": parser_version,
        "taxonomyVersion": taxonomy_version, "queriesSha": queries_sha,
        "summary": summarize_parse(scores),
        "queries": {
            score.query_id: {
                "language": score.language, "detectedLanguage": score.detected_language,
                "degraded": score.degraded, "fallback": score.fallback, "latencyMs": score.latency_ms,
                "situationSimilarity": score.situation_similarity,
                "f1": {axis: score.axes[axis].f1 for axis in CONTROLLED_AXES},
                "parsed": dict(parsed.get(score.query_id, {})),
            }
            for score in scores
        },
    }


def _fmt(value) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _row(name: str, stats: Mapping) -> str:
    return (f"| {name} | {stats['queries']} | {_fmt(stats['languageAccuracy'])} | {_fmt(stats['degradedRate'])} | "
            f"{_fmt(stats['macroF1'])} | {_fmt(stats['situationSimilarity'])} | {_fmt(stats['latencyP50'])} | "
            f"{_fmt(stats['latencyP95'])} |")


def format_parse_report(report: Mapping) -> str:
    summary = report["summary"]
    header = ("| group | queries | languageAccuracy | degradedRate | macroF1 | situationSimilarity | "
              "latencyP50 ms | latencyP95 ms |")
    lines = [f"# Parse evaluation: {report['label']}", "",
             f"Model `{report['model']}`, parser {report['parserVersion']}, taxonomy {report['taxonomyVersion']}, "
             f"{report['date']}.", "", "## Overall", "", header, "|---|---|---|---|---|---|---|---|",
             _row("all", summary["overall"]), "", "## By language", "", header, "|---|---|---|---|---|---|---|---|"]
    lines += [_row(language, stats) for language, stats in summary["byLanguage"].items()]
    lines += ["", "## Per axis (overall)", "", "| axis | precision | recall | f1 | top1 |", "|---|---|---|---|---|"]
    for axis, stats in summary["overall"]["axes"].items():
        lines.append(f"| {axis} | {_fmt(stats['precision'])} | {_fmt(stats['recall'])} | {_fmt(stats['f1'])} | "
                     f"{_fmt(stats['top1'])} |")
    return "\n".join(lines) + "\n"


def save_parse_report(path: Path, report: Mapping) -> None:
    _write(Path(path), report)


def load_parse_report(path: Path) -> dict:
    return _read(Path(path))
