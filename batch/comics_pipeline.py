"""
Comics pipeline driver (multi-image comics C5): runs the after-build jobs a newly built comics needs (docs/batches/use-cases/
comics-build.md) with one admin-triggerable job instead of six hand-run ones. The build triggers it; it can also be
triggered by hand from /admin/batches. Manual-trigger only; not scheduled. See
docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md.

Same model as recut_pipeline and description_pipeline: every child is called in process with run_id=None, so it
self-tracks under its own kind; an already-running child is skipped, not failed. Every job is incremental over the active
library, so a comics is picked up wherever it is and resuming after a failure is triggering again. A failed OCR step skips
the steps that read the OCR text; the embeddings step has no dependency. The tag step cannot run where there is no tagging
vocabulary (`it`): it is recorded skipped_not_configured up front instead of failing the run. The descriptions and profiles
(description_pipeline) stay a separate step: they are slow and capped per run.
"""
import argparse
import asyncio
import uuid
from pathlib import Path

from batch import (
    build_image_embeddings, build_ocr_lemmas, build_ocr_text_embeddings, build_tags_from_ocr,
    classify_text_heavy, extract_text_from_memes,
)
from batch.description_pipeline import FAILED, Step, run_steps
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings

_TAGGING_DIR = Path(__file__).resolve().parent / "data" / "tagging"


def tagging_configured() -> bool:
    """Mirrors what ConceptTagger.load (build_tags_from_ocr) needs: the profile's tags and concepts files. `it` has none."""
    data_dir = Path(settings.get("RULES.TAGGING_DATA_DIR") or _TAGGING_DIR)
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    return (data_dir / f"tags.{profile}.yaml").is_file() and (data_dir / f"concepts.{profile}.yaml").is_file()


STEPS = [
    Step("ocr", lambda t: extract_text_from_memes.main(trigger=t)),
    Step("embeddings", lambda t: build_image_embeddings.main(trigger=t, incremental=True)),
    Step("ocr_lemmas", lambda t: build_ocr_lemmas.main(trigger=t, incremental=True), needs=("ocr",)),
    Step("ocr_tags", lambda t: build_tags_from_ocr.main(trigger=t, incremental=True), needs=("ocr",),
         configured=lambda: tagging_configured()),
    Step("classify", lambda t: classify_text_heavy.main(trigger=t), needs=("ocr",)),
    Step("ocr_text_embeddings", lambda t: build_ocr_text_embeddings.main(trigger=t), needs=("classify",)),
]


async def _run(run_id: uuid.UUID, trigger: str) -> None:
    results = await run_steps(STEPS, trigger)
    await record_stats(run_id, {"steps": results})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"comics_pipeline: steps failed: {', '.join(failed)}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run(run_id, trigger)
    else:
        async with tracked_run(kind="comics_pipeline", trigger=trigger) as new_run_id:
            await _run(new_run_id, trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
