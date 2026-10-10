"""Merge the current ratings export into the gold judgments (general only, no database).

Reads evaluation/work/ratings-export/current/judgments-*.json and writes evaluation/gold/judgments.json.
The change is left uncommitted for review; an existing judgment is never overwritten (the job fails instead).
See docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md.
"""
import argparse
import asyncio
import os
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env
from evaluation import ratings_job
from evaluation.gold import load_corpus, load_judgments

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = REPO_ROOT / "evaluation" / "gold"
EXPORT_DIR = REPO_ROOT / "evaluation" / "work" / "ratings-export" / "current"


async def _process(gold: Path = GOLD_DIR, export_dir: Path = EXPORT_DIR) -> dict:
    corpus = load_corpus(gold / "corpus.json")
    refusal = ratings_job.check_environment(os.environ.get("APP_ENV"), corpus.environment)
    if refusal:
        print(f"not configured for this environment: {refusal}")
        return {"skipped": True, "reason": refusal}
    files = sorted(export_dir.glob("judgments-*.json")) if export_dir.exists() else []
    if not files:
        print("no export to import")
        return {"added": 0, "overwritten": 0, "unchanged": 0, "total": len(load_judgments(gold / "judgments.json")),
                "files": 0}
    stats = ratings_job.import_judgment_files(files, gold, allow_overwrite=False)
    stats.update({"files": len(files), "changed_file": str(gold / "judgments.json"),
                  "note": "uncommitted change to the gold judgments: review and commit it; baselines recorded "
                          "before this merge no longer match the judgments file"})
    print(f"added {stats['added']} judgment(s); {stats['total']} in total (uncommitted: review and commit)")
    return stats


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await record_stats(run_id, await _process())
    else:
        async with tracked_run(kind="import_judgments", trigger=trigger) as new_run_id:
            await record_stats(new_run_id, await _process())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
