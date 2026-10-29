"""
Recut pipeline driver (screenshot recut R7): turns the `detected` proposals into cuts that are ready to review, with one
admin-triggerable job instead of three hand-run ones, one of them shell-only. Detection (`detect_recut_crops`) stays a
separate manual step on purpose: it decides how large the review queue gets. Manual-trigger only; not scheduled (the
decision and its trigger are in docs/batches/inventory.md). See
docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md.

Same model as description_pipeline and refresh_recut_cuts: every child is called in process with run_id=None, so it
self-tracks under its own kind; an already-running child is skipped, not failed. The steps have no dependencies on each
other -- each selects only what is still waiting, so a failed `cuts` step (20 failures in a row) still lets the embeddings
and the refresh serve the cuts that exist -- and resuming after a failure is triggering again.
"""
import argparse
import asyncio
import uuid

from batch import build_image_embeddings, create_recut_cuts, refresh_recut_cuts
from batch.description_pipeline import FAILED, Step, run_steps
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings


async def _cuts(trigger: str) -> None:
    await create_recut_cuts.main(trigger=trigger, limit=settings.get("recut.max_cuts_per_run"))


STEPS = [
    Step("cuts", _cuts),
    Step("cut_embeddings", lambda t: build_image_embeddings.main(
        trigger=t, incremental=True, target_status="cut_proposed")),
    Step("refresh", lambda t: refresh_recut_cuts.main(trigger=t)),
]


async def _run(run_id: uuid.UUID, trigger: str) -> None:
    results = await run_steps(STEPS, trigger)
    await record_stats(run_id, {"steps": results})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"recut_pipeline: steps failed: {', '.join(failed)}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run(run_id, trigger)
    else:
        async with tracked_run(kind="recut_pipeline", trigger=trigger) as new_run_id:
            await _run(new_run_id, trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
