# build_image_embeddings Progress and Metrics — Design

Status: approved

**Date:** 2026-08-06.

Adds visible progress reporting, outcome metrics, and periodic commits to
`batch/build_image_embeddings.py`, bringing it in line with the pattern already
established by `build_tags_from_ocr.py`, `build_image_description_embeddings.py`,
and `extract_text_from_memes.py`.

---

## Motivation

`batch/build_image_embeddings.py` currently prints almost nothing while it runs: no
total image count, no per-image progress, no end-of-run summary. For a full-corpus
rebuild (thousands of images, GPU-bound CLIP embedding), there is no way to tell how
far along a run is or how long it will take.

Worse, all embeddings computed during a run stay uncommitted until a single
`await session.commit()` at the very end. If the process is killed or crashes partway
through — including by the ~10-minute hard timeout this repo's own `CLAUDE.md`
documents for `run_in_background` Bash/PowerShell calls on Windows — every embedding
computed in that run is lost, even though `--incremental` exists specifically to let a
run resume by skipping already-embedded images. Progress reporting on a run whose work
can vanish entirely on interrupt is only half useful; this spec fixes both problems
together.

## Scope

**In scope:** `batch/build_image_embeddings.py` only — progress reporting, outcome
metrics, and periodic commits.

**Out of scope:**
- No new CLI flags, no change to `main()`'s signature (`main(incremental: bool,
  target_status: str = "active")`) — every existing caller (the documented pipeline
  order in `CLAUDE.md`, the ingestion sub-pipeline's `build_image_embeddings --status
  pending --incremental`) keeps working unchanged.
- Not wired into run tracking (`tracked_run`/`batch_runs`) or the admin batch
  controller's registry — the script isn't registered there today, and adding that is
  a separate concern from progress/metrics visibility.
- No per-image timing metrics (e.g. embed time per image). `ProgressTracker`'s own
  `summary()`/periodic report already surfaces elapsed/avg/eta timing at the
  whole-run level; a redundant per-image timing bucket wasn't judged worth adding.
- No new automated test file. The two closest-precedent scripts in this codebase for
  this exact shape — a simple sequential per-row loop building embeddings/tags,
  `build_tags_from_ocr.py` and `build_image_description_embeddings.py` — have no
  dedicated test file either. Verification is manual (see Testing below).

## Design

### Full updated `batch/build_image_embeddings.py`

```python
import argparse
import asyncio
import os

from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from ai.clip import ClipModel
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from embeddingutils.image import load_image
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import Embedding

from Storage.models import Image as Img


async def main(incremental: bool, target_status: str = "active"):

    status_filter = () if target_status == "all" else (Img.status == target_status,)

    async with AsyncSessionLocal() as session:
        if not incremental:
            print(f"Deleting embeddings (status={target_status})...")
            in_scope_ids = select(Img.id).where(*status_filter).scalar_subquery()
            await session.execute(
                delete(Embedding).where(Embedding.image_id.in_(in_scope_ids))
            )
            await session.commit()
            print("Done")

        total_images = (await session.execute(
            select(count(Img.id)).where(*status_filter)
        )).scalar_one()
        print(f"Total images (status={target_status}): {total_images}")

        if incremental:
            has_embedding = select(Embedding.image_id).distinct().scalar_subquery()
            stmt = select(Img.filename, Img.id).where(Img.id.not_in(has_embedding), *status_filter)
        else:
            stmt = select(Img.filename, Img.id).where(*status_filter)

        rows = (await session.execute(stmt)).all()
        print(f"Found {len(rows)} image(s) needing embeddings")

        clip_model = ClipModel()

        BASE_PATH = settings.BASE_PATH
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        batch_size = settings.GENERAL.BATCH_SIZE
        metrics = SimpleMetricsListener()
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        print(f"Processing on {clip_model.device}")
        for i, (filename, image_id) in enumerate(rows):
            path = os.path.join(base_path, filename)
            if os.path.isdir(path):
                metrics.increment("skipped.directory")
                tracker.skip()
            elif not os.path.exists(path):
                metrics.increment("skipped.missing_file")
                tracker.skip()
            else:
                try:
                    image = load_image(path)
                    vector = clip_model.embed_image(image)
                    session.add(Embedding(image_id=image_id, embedding=vector.tolist()))
                    metrics.increment("embedded")
                except Exception as e:
                    print(f"Can't read {path}: {e}")
                    metrics.increment("error.embed_failed")
                tracker.mark_done()

            if (i + 1) % batch_size == 0:
                await session.commit()

        print("Committing...")
        await session.commit()
        print("Done")

        tracker.summary()
        metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only embed images that have no embedding yet (default: clear all and reprocess)")
    parser.add_argument("--status", choices=["pending", "active", "all"], default="active",
                        help="Only embed images with this registration status (default: active). "
                             "Ingestion's own duplicate-review stage calls this with --status pending.")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental, target_status=args.status))
```

### Key changes from the current implementation

1. **Materialize the query result.** `result = await session.execute(stmt)` (a lazy
   cursor) becomes `rows = (await session.execute(stmt)).all()`, so `len(rows)` is
   available up front for both the `Found N image(s)...` line and `ProgressTracker`'s
   `total`.

2. **Progress reporting.** `ProgressTracker(total=len(rows),
   report_every=settings.GENERAL.PROGRESS_EVERY)` — same shared config key
   (`general.progress_every: 10`) every other progress-reporting batch script already
   reads. `tracker.mark_done()` fires for every real embed attempt (success or
   failure); `tracker.skip()` fires for directory/missing-file entries, which were
   never going to be processed and would otherwise skew the ETA math (mirrors
   `build_tags_from_ocr.py`'s language-filter skip handling).

3. **Metrics.** `SimpleMetricsListener` counts four outcomes: `embedded`,
   `skipped.directory`, `skipped.missing_file`, `error.embed_failed`. Printed via
   `metrics.print()` after `tracker.summary()` at the end of the run — matching the
   `tracker.summary()` then `metrics.print()` ordering `build_tags_from_ocr.py` uses.

4. **Periodic commits.** `if (i + 1) % batch_size == 0: await session.commit()`, using
   the shared `settings.GENERAL.BATCH_SIZE` (100) — same key
   `build_image_description_embeddings.py` already reads for its own periodic commit.
   The check is placed **after** the if/elif/else outcome handling (not inside a
   `continue`-based early exit), so it fires on every iteration regardless of whether
   that iteration was a skip, a success, or an error — a skip-heavy stretch (e.g. many
   missing files in a row) still reaches the commit check on schedule. The existing
   final `await session.commit()` after the loop remains, as a flush for whatever
   partial batch didn't hit the interval.

### Why the if/elif/else restructure (not `continue`)

The current code uses two early `continue` statements for the directory/missing-file
cases. Keeping those while adding a periodic-commit check after the loop body would
silently skip the commit check on every skipped file — for a source directory with a
long, contiguous run of already-deleted or non-image files, the interval could reset
or never trigger. Restructuring into `if directory: ... elif missing: ... else: try
embed`, with the commit-interval check unconditionally after that block, avoids this
without duplicating the check three times.

## Testing

No new automated test file (see Scope — this matches the two nearest-precedent
scripts). Verification is manual, against one dev environment (`general`, since it's
the largest corpus and best exercises progress-bar behavior over a real run of
meaningful length):

1. Run `python -m batch.build_image_embeddings --env general --incremental` (safe:
   `--incremental` only touches images currently missing an embedding, doesn't delete
   anything) and confirm:
   - The `Total images...` and `Found N image(s) needing embeddings` lines print
     before the loop starts.
   - Periodic `[done/~total] elapsed=... avg=...s/img eta≈...` lines appear at the
     configured interval.
   - The run ends with a `tracker.summary()` line and a `metrics.print()` block
     showing `embedded = N` (and any skip/error counts, if the corpus has stale
     filenames).
2. Interrupt a run partway through (Ctrl-C after a few periodic-commit intervals have
   elapsed) and re-run with `--incremental`; confirm the previously-committed
   embeddings are not re-processed (i.e. `Found N image(s)...` on the second run is
   smaller than the first run's total minus what was embedded before the interrupt).
3. Confirm `--env general` (non-incremental, small `--status pending` scope from a
   real or synthetic pending image if available) still deletes and rebuilds correctly
   — the delete-and-print block at the top of `main()` is unchanged, so this is a
   regression check, not new behavior.

## Rollout

Single-file change, one task: update `batch/build_image_embeddings.py` as shown above,
then run the manual verification steps against `general`.
