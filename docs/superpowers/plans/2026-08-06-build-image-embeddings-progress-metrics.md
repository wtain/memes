# build_image_embeddings Progress and Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible progress reporting, outcome metrics, and periodic commits to `batch/build_image_embeddings.py`.

**Architecture:** Single-file change. Materialize the query result list up front (instead of iterating a lazy cursor) so a count is available for `ProgressTracker`; add a `SimpleMetricsListener` for outcome counts; restructure the per-image loop from early-`continue` to if/elif/else so a periodic-commit check runs on every iteration regardless of outcome.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, this repo's existing `batch/utils/progress.ProgressTracker` and `metrics/listener.SimpleMetricsListener` utilities (both already used by `build_tags_from_ocr.py`, `build_image_description_embeddings.py`, `extract_text_from_memes.py` — no new utility code needed).

## Global Constraints

- No change to `main()`'s signature (`main(incremental: bool, target_status: str = "active")`) — every existing caller must keep working unchanged.
- No new CLI flags.
- Not wired into run tracking (`tracked_run`/`batch_runs`) or the admin batch controller's registry — out of scope.
- No per-image timing metrics — `ProgressTracker`'s own elapsed/avg/eta reporting is sufficient at the whole-run level.
- No new automated test file — verification is manual, matching this codebase's precedent for scripts of this shape (`build_tags_from_ocr.py`, `build_image_description_embeddings.py` have none either).
- Use the exact shared config keys already read elsewhere in this codebase: `settings.GENERAL.PROGRESS_EVERY` (progress report interval) and `settings.GENERAL.BATCH_SIZE` (commit interval) — both already defined in `environments/settings.yaml` (`general.progress_every: 10`, `general.batch_size: 100`), no new settings keys needed.

---

### Task 1: Add progress reporting, metrics, and periodic commits to build_image_embeddings.py

**Files:**
- Modify: `batch/build_image_embeddings.py` (full file, 84 lines currently)

**Interfaces:**
- Consumes: `batch.utils.progress.ProgressTracker` — constructor `ProgressTracker(total: int, report_every: int = 10)`; methods `.skip()`, `.mark_done()`, `.summary()`. `metrics.listener.SimpleMetricsListener` — constructor `SimpleMetricsListener()`; methods `.increment(name: str)`, `.print()`. Both are existing, already-tested utility classes — nothing new to implement here, only to wire in.
- Produces: nothing consumed by other tasks — this is the only task in this plan.

This task has no meaningful sub-steps to TDD against (no new logic, only wiring two existing, already-tested utilities plus a loop restructure) and the spec explicitly excludes automated tests for this script. The steps below are: make the exact change specified in the design spec, then manually verify it behaves as intended against a real environment.

- [x] **Step 1: Read the current file**

Run: `cat batch/build_image_embeddings.py` (or open it in your editor) so you can see exactly what you're replacing. It should be 84 lines, ending with the `if __name__ == "__main__":` block that defines `--env`, `--incremental`, and `--status` arguments.

- [x] **Step 2: Replace the full file contents**

Replace the entire contents of `batch/build_image_embeddings.py` with:

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

Note the two behavior-relevant differences from the original file: (1) `result = await session.execute(stmt)` (a lazy cursor, iterated directly) becomes `rows = (await session.execute(stmt)).all()` (materialized into a list first, so `len(rows)` is available); (2) the two early `continue` statements for the directory/missing-file cases become `if`/`elif`/`else` branches, so the periodic-commit check after the block is reached on every loop iteration — including skip-only stretches — not just on the `else` (embed-attempt) branch.

- [x] **Step 3: Confirm the module still imports cleanly**

Run (from repo root, with the batch venv active): `python -c "import batch.build_image_embeddings"`
Expected: no output, no traceback (a clean import confirms no syntax errors and that `batch.utils.progress` / `metrics.listener` resolve correctly).

- [x] **Step 4: Manually verify against the `general` environment (incremental — safe, no deletes)**

Run: `python -m batch.build_image_embeddings --env general --incremental`

Expected, in order:
- A `Total images (status=active): N` line before any per-image work starts.
- A `Found M image(s) needing embeddings` line (M ≤ N; likely 0 or a small number if this environment's corpus is already fully embedded — that's fine, it exercises the `total=0` / small-`M` path).
- If M > 0: periodic `[done/~total] elapsed=... avg=...s/img eta≈...` lines from `ProgressTracker`, spaced according to `general.progress_every: 10` in `environments/settings.yaml` (so no progress line at all is expected if M < 10 — only the final summary).
- A `Processing on <device>` line (cpu or cuda, depending on this machine's torch install) before the loop.
- After the loop: a `Committing...` / `Done` pair (existing behavior, unchanged), then a `Done: X/~Y images in ...` line from `tracker.summary()`, then a metrics block from `metrics.print()` (e.g. `embedded = M` if all succeeded, or a mix of `embedded`/`skipped.*`/`error.embed_failed` counters if the corpus has stale filenames).

If M is 0 for `general`, additionally run against `metal` or `it` (whichever environment's `.env` is available) with `--incremental` — at least one environment should have a non-zero `Found N image(s)...` count to actually exercise the loop, progress lines, and metrics output.

- [x] **Step 5: Manually verify the non-incremental `--status pending` delete-and-rebuild path (regression check)**

The delete-and-print block at the top of `main()` (`if not incremental: ... delete(Embedding)...`) is unchanged from the original file, so this step is confirming no regression, not new behavior — skip it only if no environment currently has any `pending` images to scope this against (check via the ingestion pipeline's state, or skip and note why in the task report).

Run: `python -m batch.build_image_embeddings --env <env> --status pending` (no `--incremental` — this is the default full-rebuild path, but scoped to `--status pending` only, so it deletes and rebuilds embeddings only for images not yet in the active corpus, not the whole library).

Expected: `Deleting embeddings (status=pending)...` / `Done`, then the same `Total images (status=pending): N` → `Found N image(s) needing embeddings` → progress/metrics output as Step 4, confirming the delete-then-rebuild path still works end to end with the new code.

- [x] **Step 6: Manually verify the periodic-commit / resume behavior**

Only if Step 4 found an environment with a `Found N image(s)...` count large enough (at least a few multiples of `general.batch_size: 100`, or use whatever environment has the largest gap) to meaningfully test this:

1. Start `python -m batch.build_image_embeddings --env <env> --incremental` and let it run past at least one `batch_size` (100) interval — watch for at least one full `ProgressTracker` progress line past the 100-image mark, then interrupt with Ctrl-C.
2. Re-run the same command: `python -m batch.build_image_embeddings --env <env> --incremental`.
3. Confirm the second run's `Found N image(s)...` count is smaller than the first run's — proving the periodic commit persisted the embeddings computed before the interrupt, and `--incremental`'s existing "skip already-embedded" query correctly picks up from there.

If no environment has enough unembedded images to make this a meaningful test (e.g. everything is already embedded, or the gap is under 100), skip this step and note it in the task report — the periodic-commit code path was still exercised in Step 4 as long as `Found N image(s)...` was ≥ 1 (any commit happens at least once at the final `await session.commit()` after the loop, which Step 4 already covers; only the *mid-run* commit is unverified in that case).

- [x] **Step 7: Commit**

```bash
git add batch/build_image_embeddings.py
git commit -m "feat: add progress reporting, metrics, and periodic commits to build_image_embeddings"
```
