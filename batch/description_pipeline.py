"""
Description and tagging pipeline driver: refreshes OCR tags, the human-note indexes, the
Ollama descriptions and everything derived from them with one admin-triggerable job, instead
of six hand-run scripts. Manual-trigger only (via /admin/batches); not scheduled. See
docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md.

Unlike ingest_auto_prep's strict chain, steps here form one dependent group (description
embeddings and tags need the descriptions) plus independent branches, so a failure only skips
the steps that need the failed one. Every child is called in process with run_id=None, so it
self-tracks under its own kind -- per-step history shows up in /admin/batches with no extra
API -- and its one-active-run-per-kind guard turns "an operator is already running this job
by hand" into a skipped_busy step instead of a failure. The driver's own run stats carry a
per-step summary.

Active images only: the description job itself never touches pending images, so new images
join after ingest_promote. The describe step is capped per run by
image_descriptions.max_per_run (unset means unlimited); re-trigger until the backlog is empty.
Everything is incremental, so resuming after a failure is just triggering again.
"""
import argparse
import asyncio
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from batch import (
    build_description_note_embeddings, build_description_note_lemmas,
    build_image_description_embeddings, build_image_descriptions,
    build_tags_from_descriptions, build_tags_from_ocr,
)
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings
from repository.batch_runs import BatchAlreadyRunningError

OK = "ok"
FAILED = "failed"
SKIPPED_DEPENDENCY = "skipped_dependency"
SKIPPED_BUSY = "skipped_busy"


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[str], Awaitable[None]]  # takes the trigger string
    needs: tuple[str, ...] = ()            # earlier steps whose failure (or skip) skips this one


async def _describe(trigger: str) -> None:
    await build_image_descriptions.main(trigger=trigger, limit=settings.get("image_descriptions.max_per_run"))


# Cheap, GPU-independent steps first: if the process is interrupted during the long describe
# step, the independent work has already been done.
STEPS = [
    Step("ocr_tags", lambda t: build_tags_from_ocr.main(trigger=t, incremental=True)),
    Step("note_lemmas", lambda t: build_description_note_lemmas.main(trigger=t)),
    Step("note_embeddings", lambda t: build_description_note_embeddings.main(trigger=t)),
    Step("describe", _describe),
    Step("description_embeddings", lambda t: build_image_description_embeddings.main(trigger=t),
         needs=("describe",)),
    Step("description_tags", lambda t: build_tags_from_descriptions.main(trigger=t, incremental=True),
         needs=("describe",)),
]


async def run_steps(steps: list[Step], trigger: str) -> dict[str, dict]:
    """Runs steps in order. A failure never aborts the loop; it only skips dependents."""
    results: dict[str, dict] = {}
    for step in steps:
        blocked = [n for n in step.needs if results[n]["status"] in (FAILED, SKIPPED_DEPENDENCY)]
        if blocked:
            results[step.name] = {"status": SKIPPED_DEPENDENCY, "error": f"needs {', '.join(blocked)}"}
            print(f"description_pipeline: skipping {step.name} (needs {', '.join(blocked)})")
            continue
        print(f"description_pipeline: running {step.name}")
        try:
            await step.run(trigger)
        except BatchAlreadyRunningError:
            results[step.name] = {"status": SKIPPED_BUSY}
            print(f"description_pipeline: {step.name} is already running elsewhere, skipping")
        except Exception as e:
            results[step.name] = {"status": FAILED, "error": str(e)}
            print(f"description_pipeline: {step.name} failed: {e}")
        else:
            results[step.name] = {"status": OK}
    return results


async def _run_pipeline(run_id: uuid.UUID, trigger: str) -> None:
    results = await run_steps(STEPS, trigger)
    await record_stats(run_id, {"steps": results})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"description_pipeline: steps failed: {', '.join(failed)}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run_pipeline(run_id, trigger)
    else:
        async with tracked_run(kind="description_pipeline", trigger=trigger) as new_run_id:
            await _run_pipeline(new_run_id, trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
