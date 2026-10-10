"""Ratings to gold judgments, as callable functions (P5b CLI commands and the export_ratings / import_judgments jobs)."""
import json
import os
from pathlib import Path
from typing import Sequence

from evaluation.gold import (
    GoldError, dump_json, load_corpus, load_judgments, load_queries, merge_judgments, parse_export, save_judgments,
)
from evaluation.ratings_export import build_export
from repository.meme_text_ratings import MemeTextRatingRepository

EXPORT_STALE_NAMES = ("candidate-queries.json", "summary.json")


def check_environment(app_env: str | None, corpus_environment: str) -> str | None:
    """A refusal message when the shell's APP_ENV names a different environment than the corpus was frozen for."""
    if app_env and app_env != corpus_environment:
        return (f"APP_ENV is {app_env!r} but the corpus was built for {corpus_environment!r}; scoring a different "
                "database than the one the gold data was judged against is meaningless")
    return None


def prepare_output_dir(out: Path, force: bool) -> str | None:
    """A refusal message when `out` holds files and force is off; with force, deletes only the files an earlier
    export owns (stale judgments-<lang>.json would otherwise be re-imported over newer judgments)."""
    if out.exists() and any(out.iterdir()):
        if not force:
            return f"{out} already contains an export (use --force to replace it)"
        for stale in [*out.glob("judgments-*.json"), *(out / name for name in EXPORT_STALE_NAMES)]:
            if stale.is_file():
                stale.unlink()
    return None


def next_round(judgments: Sequence) -> int:
    return max((judgment.round for judgment in judgments), default=0) + 1


async def export_ratings(session, gold: Path, out: Path, *, round_number: int, max_chars: int, today: str) -> dict:
    """Write the review files for the stored ratings into `out` (never merges into the gold set)."""
    ratings = await MemeTextRatingRepository(session).all_ratings()
    result = build_export(ratings, load_queries(gold / "queries.json"), load_judgments(gold / "judgments.json"),
                          set(load_corpus(gold / "corpus.json").image_ids()), max_chars=max_chars,
                          round_number=round_number, today=today)
    out.mkdir(parents=True, exist_ok=True)
    for language, data in result.judgments_by_language.items():
        (out / f"judgments-{language}.json").write_text(dump_json(data), encoding="utf-8", newline="\n")
    (out / "candidate-queries.json").write_text(dump_json(result.candidates), encoding="utf-8", newline="\n")
    (out / "summary.json").write_text(dump_json(result.report), encoding="utf-8", newline="\n")
    return {"ratings": result.report["summary"]["total"], "importable": result.report["importable"],
            "files": len(result.judgments_by_language), "already_judged": len(result.report["alreadyJudged"]),
            "outside_corpus": len(result.report["outsideCorpus"]), "new_messages": result.report["candidateMessages"],
            "round": round_number}


def import_judgment_files(files: Sequence[Path], gold: Path, *, allow_overwrite: bool) -> dict:
    """Merge exported judgment files into `<gold>/judgments.json` (atomic write; nothing is committed)."""
    queries = load_queries(gold / "queries.json")
    corpus = load_corpus(gold / "corpus.json")
    existing = load_judgments(gold / "judgments.json")
    incoming = []
    for name in files:
        with open(name, encoding="utf-8") as handle:
            data = json.load(handle)
        incoming.extend(parse_export(data, valid_query_ids={q.id for q in queries},
                                     valid_image_ids=set(corpus.image_ids())))
    result = merge_judgments(existing, incoming)
    if result.overwritten and not allow_overwrite:
        raise GoldError(f"the merge would overwrite {result.overwritten} existing judgment(s); nothing was written")
    target = gold / "judgments.json"
    temporary = gold / "judgments.json.tmp"
    save_judgments(result.merged, temporary)
    os.replace(temporary, target)
    return {"added": result.added, "overwritten": result.overwritten, "unchanged": result.unchanged,
            "total": len(result.merged)}
