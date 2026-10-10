"""Export the owner's stored meme-from-text ratings as gold-set review files (general only).

Writes evaluation/work/ratings-export/current/ (replaced on every run). Never merges into the gold set:
batch.import_judgments does that. See docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md.
"""
import argparse
import asyncio
import os
import uuid
from datetime import date
from pathlib import Path

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings
from evaluation import ratings_job
from evaluation.gold import load_corpus, load_judgments
from Storage.db import AsyncSessionLocal

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = REPO_ROOT / "evaluation" / "gold"
EXPORT_DIR = REPO_ROOT / "evaluation" / "work" / "ratings-export" / "current"


async def _process(gold: Path = GOLD_DIR, export_dir: Path = EXPORT_DIR) -> dict:
    corpus = load_corpus(gold / "corpus.json")
    refusal = ratings_job.check_environment(os.environ.get("APP_ENV"), corpus.environment)
    if refusal:
        print(f"not configured for this environment: {refusal}")
        return {"skipped": True, "reason": refusal}
    round_number = ratings_job.next_round(load_judgments(gold / "judgments.json"))
    problem = ratings_job.prepare_output_dir(export_dir, force=True)
    if problem:
        raise RuntimeError(problem)
    max_chars = int(settings.get("parse.max_input_chars", 600))
    async with AsyncSessionLocal() as session:
        stats = await ratings_job.export_ratings(session, gold, export_dir, round_number=round_number,
                                                 max_chars=max_chars, today=date.today().isoformat())
    print(f"{stats['ratings']} ratings, {stats['importable']} importable, round {stats['round']}; wrote {export_dir}")
    return stats


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await record_stats(run_id, await _process())
    else:
        async with tracked_run(kind="export_ratings", trigger=trigger) as new_run_id:
            await record_stats(new_run_id, await _process())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
