"""
Ingestion prep driver: automates the ingestion pipeline's fully-automatable prep stages --
hash dedup through Tier A duplicate-finding -- so an operator no longer has to run 5 commands
by hand just to get newly-dropped inbox files into the Tier A review queue. Tier B
duplicate-finding and promotion stay manual; both depend on a human having finished the prior
tier's review, so auto-running them risks promoting images (or advancing the review queue) out
from under a reviewer. See
docs/superpowers/specs/2026-08-14-batch-scheduling-rollout-design.md.

Self-tracked under kind="ingestion_auto_prep", deliberately separate from the long-lived
kind="ingestion" row each real ingestion batch uses. That row can legitimately stay "started"
for days while a human works through Tier A/B review -- which is not orphaned, but the
scheduler's own orphan-recovery guard (max_runtime_minutes) would force-fail it if this
driver's own scheduler-tick bookkeeping shared that kind. This driver's kind only tracks "did a
tick run and did it fail" -- the real stats (files moved, embeddings created, candidates found)
already land in the kind="ingestion" run's own stats via each chained step's own tracking.

Note: ingest_hash_dedup.py's resolve_batch() always creates or joins a kind="ingestion" run,
even when the inbox is empty -- so the very first scheduled tick (in an environment with no
prior ingestion activity) creates an empty run that immediately advances through every stage
this driver touches (nothing to embed/OCR/search, so each step is a fast no-op) and then sits
open indefinitely at stage="tier_a_review" with zero pending images, until either real files are
later dropped (which correctly join it) or an operator manually runs ingest_promote.py (which
would immediately complete it, seeing zero pending). This is harmless -- the /ingestion UI just
shows an empty Tier A queue -- not a bug to work around here.
"""
import argparse
import asyncio
import uuid

from batch import (
    build_image_embeddings, extract_text_from_memes, ingest_find_duplicates,
    ingest_hash_dedup, ingest_validate_formats,
)
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings


async def _run_prep_chain() -> None:
    await ingest_hash_dedup.main(env=None)

    steps = [
        ("ingest_validate_formats", lambda: ingest_validate_formats.main(env=None)),
        ("build_image_embeddings", lambda: build_image_embeddings.main(incremental=True, target_status="pending")),
        ("extract_text_from_memes", lambda: extract_text_from_memes.main(settings.BASE_PATH, target_status="pending")),
        ("ingest_find_duplicates", lambda: ingest_find_duplicates.main(env=None, tier="tier_a", k=None)),
    ]
    for name, step in steps:
        try:
            await step()
        except RuntimeError as e:
            print(f"ingest_auto_prep: nothing to do this tick, stopping at {name} ({e})")
            return


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run_prep_chain()
    else:
        async with tracked_run(kind="ingestion_auto_prep", trigger=trigger):
            await _run_prep_chain()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
