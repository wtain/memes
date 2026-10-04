"""Baseline files, Markdown reports and compare (P6 spec, decision 8). Pure apart from the git lookup."""
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from evaluation.gold import _read, _write
from evaluation.runner import QueryResult

METRICS = (("ndcg", "nDCG@10"), ("recall", "recall@10"), ("hit", "hit@10"), ("judged", "judged@10"))


class CompareError(ValueError):
    pass


def git_commit() -> str | None:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def make_baseline(*, label: str, retriever_name: str, retriever_config: Mapping, date: str,
                  git_commit: str | None, gold_hashes: Mapping[str, str], profile_state: Mapping,
                  results: Sequence[QueryResult], summary: Mapping) -> dict:
    return {
        "label": label,
        "date": date,
        "retriever": {"name": retriever_name, "config": dict(retriever_config)},
        "gitCommit": git_commit,
        "gold": dict(gold_hashes),
        "profileState": dict(profile_state),
        "summary": dict(summary),
        "queries": {
            r.query_id: {"language": r.language, "ndcg": r.metrics.ndcg, "recall": r.metrics.recall,
                         "hit": r.metrics.hit, "judged": r.metrics.judged_share}
            for r in results
        },
    }


def save_baseline(path: Path, baseline: Mapping) -> None:
    _write(Path(path), baseline)


def load_baseline(path: Path) -> dict:
    return _read(Path(path))


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def _table(groups: Mapping[str, Mapping]) -> list[str]:
    lines = ["| group | queries | no positive | nDCG@10 | recall@10 | hit@10 | judged@10 |", "|---|---|---|---|---|---|---|"]
    for name, stats in groups.items():
        lines.append(f"| {name} | {stats['queries']} | {stats['noPositive']} | {_fmt(stats['ndcg'])} | "
                     f"{_fmt(stats['recall'])} | {_fmt(stats['hit'])} | {_fmt(stats['judged'])} |")
    return lines


def format_report(baseline: Mapping, bias: Mapping | None = None) -> str:
    summary = baseline["summary"]
    lines = [f"# Evaluation: {baseline['label']}", "",
             f"Retriever `{baseline['retriever']['name']}` {baseline['retriever']['config']}, {baseline['date']}, "
             f"commit {baseline.get('gitCommit') or 'unknown'}.",
             f"Profile state: {baseline.get('profileState')}", "", "## Overall", ""]
    lines += _table({"all": summary["overall"]})
    for title, key in (("By language", "byLanguage"), ("By difficulty", "byDifficulty"), ("By length", "byLength")):
        lines += ["", f"## {title}", ""] + _table(summary[key])
    ratios = ", ".join(f"{language} {_fmt(ratio)}" for language, ratio in summary["languageRatio"].items())
    lines += ["", f"nDCG@10 relative to English: {ratios}", "", "## By input stratum", "",
              "| stratum | retrieved share of top 10 | mean grade of judged | judged count |", "|---|---|---|---|"]
    for stratum, stats in summary["byStratum"].items():
        lines.append(f"| {stratum} | {_fmt(stats['retrievedShare'])} | {_fmt(stats['meanGradeJudged'])} | "
                     f"{stats['judgedCount']} |")
    if bias is not None:
        lines += ["", "## Pooling bias", "",
                  "Top-10 results by the source that proposed them to the judge (an image can have several "
                  "sources); `unpooled` results were never shown to the judge and count as grade 0:", ""]
        lines += [f"- {name}: {count}" for name, count in bias.items()]
    return "\n".join(lines) + "\n"


def pooling_bias(results: Sequence[QueryResult], pool_sources: Mapping[str, Mapping[str, Sequence[str]]]) -> dict:
    """How many top-10 results each pool source proposed, and how many were in no pool at all."""
    counts: dict[str, int] = {}
    unpooled = top_results = 0
    for result in results:
        sources_of = pool_sources.get(result.query_id, {})
        for image_id in result.ranked:
            top_results += 1
            sources = sources_of.get(image_id)
            if not sources:
                unpooled += 1
                continue
            for source in sources:
                counts[source] = counts.get(source, 0) + 1
    counts["unpooled"] = unpooled
    counts["topResults"] = top_results
    return counts


def compare_baselines(a: Mapping, b: Mapping, *, force: bool = False, drop_threshold: float = 0.1,
                      query_texts: Mapping[str, str] | None = None) -> str:
    if not force:
        for key in sorted(set(a["gold"]) | set(b["gold"])):
            if a["gold"].get(key) != b["gold"].get(key):
                raise CompareError(f"gold data differs ({key}): scores against different judgments are not "
                                   "comparable; re-run the older retriever or pass force=True")
    lines = [f"# Compare: {a['label']} -> {b['label']}", ""]
    lines += ["| group | metric | " + a["label"] + " | " + b["label"] + " | delta |", "|---|---|---|---|---|"]
    groups = {"all": (a["summary"]["overall"], b["summary"]["overall"])}
    for language in sorted(set(a["summary"]["byLanguage"]) | set(b["summary"]["byLanguage"])):
        groups[language] = (a["summary"]["byLanguage"].get(language, {}), b["summary"]["byLanguage"].get(language, {}))
    for name, (left, right) in groups.items():
        for key, title in METRICS[:3]:
            old, new = left.get(key), right.get(key)
            delta = None if old is None or new is None else new - old
            sign = "" if delta is None or delta < 0 else "+"
            lines.append(f"| {name} | {title} | {_fmt(old)} | {_fmt(new)} | "
                         f"{'-' if delta is None else sign + format(delta, '.3f')} |")
    worse = []
    for query_id, new in b["queries"].items():
        old = a["queries"].get(query_id)
        if old and old["ndcg"] is not None and new["ndcg"] is not None and new["ndcg"] - old["ndcg"] <= -drop_threshold:
            worse.append((new["ndcg"] - old["ndcg"], query_id))
    lines += ["", f"## Queries that got worse (nDCG@10 down by at least {drop_threshold})", ""]
    if not worse:
        lines.append("No query got worse by that much.")
    for delta, query_id in sorted(worse):
        text = (query_texts or {}).get(query_id, "")
        lines.append(f"- {query_id} ({delta:.3f}): {text}")
    return "\n".join(lines) + "\n"
