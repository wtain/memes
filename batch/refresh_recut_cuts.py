"""
Refresh driver for recut cuts (screenshot recut R3c): replaces the stale derived data a cut inherited from its source
(OCR, OCR lemmas and tags, descriptions and what hangs off them, the meme profile) by running the refresh modes of the
group 3 jobs in dependency order, plus the text-heavy classifier and OCR-text embeddings that need the new OCR.
Manual-trigger only (via /admin/batches); not scheduled -- when to run it is decided in R7.

Same model as description_pipeline: every child is called in process with run_id=None, so it self-tracks under its own
kind; an already-running child is skipped, not failed; a failed step only skips the steps that need it. Every refresh
selects only what is still stale, so resuming after a failure is triggering again. The driver's own stats carry the
per-step outcome and the number of stale rows left per table across ALL image statuses (stale_rows_total). See
docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3c-refresh-stale-design.md.
"""
import argparse
import asyncio
import uuid

from batch import (
    build_description_lemmas, build_image_description_embeddings, build_image_descriptions,
    build_meme_profile_embeddings, build_meme_profiles, build_ocr_lemmas, build_ocr_text_embeddings,
    build_tags_from_descriptions, build_tags_from_ocr, classify_text_heavy, extract_text_from_memes,
)
from batch.description_pipeline import FAILED, Step, run_steps
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings
from Storage.stale import stale_counts

CUT = "cut_proposed"


async def _describe(trigger: str) -> None:
    await build_image_descriptions.main(
        trigger=trigger, refresh_stale=True, limit=settings.get("image_descriptions.max_per_run"))


async def _profiles(trigger: str) -> None:
    await build_meme_profiles.main(trigger=trigger, status=CUT, limit=settings.get("meme_profiles.max_per_run"))


STEPS = [
    Step("ocr", lambda t: extract_text_from_memes.main(trigger=t, refresh_stale=True)),
    Step("classify", lambda t: classify_text_heavy.main(status=CUT, trigger=t), needs=("ocr",)),
    Step("ocr_text_embeddings", lambda t: build_ocr_text_embeddings.main(status=CUT, trigger=t), needs=("classify",)),
    Step("ocr_lemmas", lambda t: build_ocr_lemmas.main(trigger=t, refresh_stale=True), needs=("ocr",)),
    Step("ocr_tags", lambda t: build_tags_from_ocr.main(trigger=t, refresh_stale=True), needs=("ocr",)),
    Step("describe", _describe),
    # Replacing a stale description cascades away its copied embedding and lemmas, so the normal incremental runs of
    # these two are their refresh.
    Step("description_embeddings", lambda t: build_image_description_embeddings.main(trigger=t),
         needs=("describe",)),
    Step("description_lemmas", lambda t: build_description_lemmas.main(trigger=t), needs=("describe",)),
    Step("description_tags", lambda t: build_tags_from_descriptions.main(trigger=t, refresh_stale=True),
         needs=("describe",)),
    Step("meme_profiles", _profiles, needs=("ocr", "describe")),
    Step("meme_profile_embeddings", lambda t: build_meme_profile_embeddings.main(trigger=t),
         needs=("meme_profiles",)),
]


async def _stale_rows_total() -> dict[str, int]:
    from Storage.db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await stale_counts(session)


async def _run(run_id: uuid.UUID, trigger: str) -> None:
    results = await run_steps(STEPS, trigger)
    await record_stats(run_id, {"steps": results, "stale_rows_total": await _stale_rows_total()})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"refresh_recut_cuts: steps failed: {', '.join(failed)}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run(run_id, trigger)
    else:
        async with tracked_run(kind="refresh_recut_cuts", trigger=trigger) as new_run_id:
            await _run(new_run_id, trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
