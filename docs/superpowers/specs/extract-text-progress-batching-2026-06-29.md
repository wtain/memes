# extract_text_from_memes: Progress Reporting, Batching & Reusability

Status: done

**Date:** 2026-06-29  
**Scope:** `batch/extract_text_from_memes.py` and supporting infrastructure

---

## Summary

Three improvements to the OCR extraction pipeline:

1. **Progress reporting** — print "X/~Y done, elapsed, ETA" every N images.
2. **Batched commits** — flush DB writes every M images instead of per-image, with correct resume on interruption.
3. **Reusability** — extract `ProgressTracker` and `BatchCommitter` into `batch/utils/` for reuse across other batch scripts; expose `run()` for programmatic use.

---

## Background

`extract_text_from_memes.py` runs an async three-stage pipeline:

```
io_producer  →  [io_queue]  →  cpu_worker  →  [cpu_queue]  →  gpu_consumer
(scan + read)               (decode/resize)                 (OCR × 3 langs, persist)
```

Current pain points:
- No progress output: a long run is a black box.
- `persist_ocr_result` opens a new session and commits for every image × language (up to 3 commits per image). On large libraries this is a bottleneck and aborts leave the DB in a clean but slow state.
- The pipeline logic is not callable from other scripts.

---

## Design Decisions

| Question | Decision |
|----------|----------|
| What is Y (total)? | Start with `len(os.listdir(path))` (directory file count), then **decrement dynamically** via `tracker.skip()` as `io_producer` skips files. Y converges to the real pending count as the scan progresses — no DB pre-query needed. |
| Batching unit | **Images** (not OCR passes). M images = up to 3M OCR rows per flush. |
| `should_process` for "processing" | **Retry** — treat as interrupted, not as in-progress. Fix in scope. |
| Reusability surface | `run(path, options)` function + shared `ProgressTracker` / `BatchCommitter` classes. |

### Incremental mode and ETA accuracy

In incremental mode most files are already `status=done` and are skipped by `io_producer`. If Y were fixed at the directory count, the ETA would be wildly wrong (e.g., 450 done + 50 pending → shows "50/~500", implying 450 more images still to come).

Fix: `io_producer` calls `tracker.skip()` for every file it decides not to enqueue (directories, `.mp4` files, already-done images). This decrements Y by 1 each time. By the time scanning is halfway through, Y has already converged close to the true pending count, and the ETA becomes meaningful.

Since `io_producer` and `gpu_consumer` run concurrently in the same asyncio event loop (single-threaded), updates to `tracker` state from both coroutines are naturally serialized — no locking needed.

---

## Non-Goals

- Parallelism changes (thread/GPU count).
- A `--force` flag to ignore "done" status (separate concern).
- Changing OCR languages or readers.
- Fixing the `OCRTextRepository.overwrite_texts` bug (deletes from `ImageMetrics` instead of `OCRText` — tracked separately).

---

## New Components

### `batch/utils/__init__.py`
Empty init file to make `batch/utils` a package.

---

### `batch/utils/progress.py` — `ProgressTracker`

Tracks per-image completion and prints progress every N images.

```python
class ProgressTracker:
    def __init__(self, total: int, report_every: int = 10):
        """
        total:        initial upper-bound (directory file count).
        report_every: print a line after every N completions, and always at the end.
        """

    def skip(self) -> None:
        """
        Decrement the effective total by 1.
        Call in io_producer for every file not enqueued for processing
        (directories, unsupported extensions, already-done images).
        Corrects Y so ETA converges toward the true pending count.
        """

    def mark_done(self) -> None:
        """Call once per completed image (after all OCR passes for that image)."""

    def summary(self) -> None:
        """Print final summary line. Call when the pipeline finishes."""
```

**Progress line format:**
```
[42/~58] elapsed=1m23s  avg=2.0s/img  eta≈32s
```
Y is labeled `~` throughout because it may still be decremented by concurrent skips in `io_producer`.

**ETA algorithm:**  
Rolling average over the last 50 completions (avoids GPU warm-up skewing early estimates).  
`eta = avg_seconds_per_image × (effective_total - done)`  
where `effective_total = initial_total - skip_count`.

**When to call `mark_done`:**  
In `gpu_consumer`, after the inner `for language, reader in readers.items()` loop completes for one image — i.e., once all 3 OCR passes for that image are done.

**When to call `skip`:**  
In `io_producer`, for every `continue` branch — directories, `.mp4` files, already-done images, read errors.

**Usage by other scripts:**
```python
from batch.utils.progress import ProgressTracker

tracker = ProgressTracker(total=len(image_ids), report_every=10)
for image in images:
    process(image)
    tracker.mark_done()
tracker.summary()
```

---

### `batch/utils/batch_commit.py` — `BatchCommitter`

Accumulates OCR results in a long-lived session and commits every M images.

```python
class BatchCommitter:
    def __init__(self, session, batch_size: int = 100, pipeline: str = ""):
        """
        session:    an AsyncSession — caller owns creation; BatchCommitter owns flushing.
        batch_size: commit after this many images complete all their OCR passes.
        pipeline:   pipeline name for status repo.
        """

    async def add_language_result(
        self,
        image: Image,
        language: str,
        ocr_result: list,
        metrics: dict,
    ) -> None:
        """
        Write one OCR pass result to the session (no commit).
        Call once per language per image.
        """

    async def on_image_done(self, image: Image) -> None:
        """
        Mark image status as done (no commit), increment pending count.
        Auto-flushes if pending count >= batch_size.
        Call once per image after all language passes are complete.
        """

    async def flush(self) -> None:
        """Commit all pending writes. Resets the pending counter."""

    async def close(self) -> None:
        """Flush remaining writes and close the session."""
```

**Session lifecycle:**
```python
async with AsyncSessionLocal() as session:
    committer = BatchCommitter(session, batch_size=M, pipeline=PIPELINE)
    try:
        # ... processing loop ...
        await committer.flush()  # final flush
    except Exception:
        await session.rollback()
        raise
```

**`add_language_result` internals:**
1. Call `OCRTextRepository(session).overwrite_texts(image, ocr_result, language)` — no commit.
2. Call `ImageMetricsRepository(session).overwrite_metrics(image, metrics)` — no commit.

**`on_image_done` internals:**
1. Call `ImageProcessingStatusRepository(session, pipeline).mark_done(image)` — **no commit** (see below).
2. `self._pending += 1`
3. If `self._pending >= self.batch_size`: `await self.flush()`

**Usage by other scripts:**
```python
from batch.utils.batch_commit import BatchCommitter

async with AsyncSessionLocal() as session:
    committer = BatchCommitter(session, batch_size=100, pipeline="mypipeline")
    for image in images:
        results = run_model(image)
        await committer.add_language_result(image, "en", results, metrics)
        await committer.on_image_done(image)
    await committer.close()
```

---

## Modified Components

### `repository/image_procesing_status.py`

**Change: `mark_done` must not auto-commit.**

Current:
```python
async def mark_done(self, image):
    ...
    await self.session.commit()   # ← remove this
```

After:
```python
async def mark_done(self, image):
    ...
    # no commit — caller is responsible
```

**Impact:** Every existing caller of `mark_done` must now commit explicitly.  
Callers to audit:

| Caller | Action |
|--------|--------|
| `persist_ocr_result` in `extract_text_from_memes.py` | Replaced by `BatchCommitter` — covered |
| Any other batch scripts calling `mark_done` directly | Add explicit `await session.commit()` after the call |

Run `grep -rn "mark_done" batch/ repository/` before implementing to catch all callers.

**Change: `should_process` must retry "processing" status.**

Current:
```python
if existing.status == "processing":
    return False   # ← wrong: silently skips interrupted images
```

After:
```python
if existing.status == "processing":
    return True    # treat as interrupted, retry
```

Rationale: with batched commits a crash mid-batch leaves images in `"processing"`. They must be retried on the next run, not silently skipped.

---

### `extract_text_from_memes.py`

#### Add `run()` function

Extract `main(path)` logic into a callable `run` function:

```python
async def run(path: str, batch_size: int = 100, progress_every: int = 10) -> None:
    """
    Run the full OCR pipeline on all images under `path`.

    batch_size:    commit every N completed images (default 100).
    progress_every: print progress every N completed images (default 10).
    """
    total = len([f for f in os.listdir(path) if not os.path.isdir(os.path.join(path, f))])
    tracker = ProgressTracker(total=total, report_every=progress_every)

    io_queue = asyncio.Queue(maxsize=200)
    cpu_queue = asyncio.Queue(maxsize=20)
    cpu_executor = ThreadPoolExecutor(max_workers=16)
    metrics_listener = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        committer = BatchCommitter(session, batch_size=batch_size, pipeline=PIPELINE)
        await asyncio.gather(
            io_producer(path, io_queue, PIPELINE, metrics_listener, tracker),  # tracker for skip()
            cpu_worker(io_queue, cpu_queue, cpu_executor, metrics_listener),
            gpu_consumer(cpu_queue, PIPELINE, metrics_listener, committer, tracker),
        )
        await committer.close()

    tracker.summary()
    metrics_listener.print()


async def main(path: str) -> None:
    batch_size = int(os.getenv("BATCH_SIZE", "100"))
    progress_every = int(os.getenv("PROGRESS_EVERY", "10"))
    await run(path, batch_size=batch_size, progress_every=progress_every)
```

#### Modify `io_producer` signature

```python
async def io_producer(path, io_queue, pipeline, metrics_listener, tracker: ProgressTracker):
```

Call `tracker.skip()` on every `continue` branch:

```python
if os.path.isdir(fullFilePath):
    metrics_listener.increment("skipped.directory")
    tracker.skip()
    continue
if file.lower().endswith(".mp4"):
    metrics_listener.increment("skipped.file")
    tracker.skip()
    continue
if image and not await status_repo.should_process(image.id):
    metrics_listener.increment("skipped.existing")
    tracker.skip()
    continue
# read errors:
except Exception as e:
    metrics_listener.increment("error.reading")
    tracker.skip()
    continue
```

#### Modify `gpu_consumer` signature

```python
async def gpu_consumer(
    queue,
    pipeline,
    metrics_listener,
    committer: BatchCommitter,      # new
    tracker: ProgressTracker,       # new
):
```

Replace the per-image `persist_ocr_result(...)` call with `BatchCommitter` calls:

```python
# Before (per language):
await persist_ocr_result(merged, metrics_dict, image, pipeline, language)

# After:
await committer.add_language_result(image, language, merged, metrics_dict)

# After the language loop (once per image):
await committer.on_image_done(image)
tracker.mark_done()
```

#### Remove `persist_ocr_result`

This function becomes dead code once `BatchCommitter` is wired in. Remove it.

---

## Configuration

All parameters configurable via environment variables (consistent with existing env-var pattern):

| Variable | Default | Description |
|----------|---------|-------------|
| `BATCH_SIZE` | `100` | Commit every N completed images |
| `PROGRESS_EVERY` | `10` | Print progress line every N completed images |

These are read in `main()` and passed through to `run()`. Direct callers of `run()` pass values explicitly.

---

## Error Handling and Edge Cases

**Interrupt mid-batch (SIGINT / crash):**  
Images processed since the last flush have `status="processing"` (set by `mark_started` in `io_producer`, which still commits immediately). On the next run, `should_process` now returns `True` for "processing", so they are retried correctly.  
The `BATCH_SIZE` window determines the maximum duplicate work on resume: up to M images may be re-OCR'd.

**Exception in `gpu_consumer`:**  
The `async with AsyncSessionLocal()` context manager in `run()` rolls back on exception. `io_producer` and `cpu_worker` will naturally drain/exit when the gather fails.

**Empty directory:**  
`ProgressTracker(total=0)` — `summary()` should handle the zero case gracefully (no division by zero).

**Single-language failure:**  
If OCR fails for one language but succeeds for others, the current pipeline logs and continues. This behaviour is unchanged; the `BatchCommitter` simply doesn't receive a result for that (image, language) pair.

---

## Implementation Order

Implement in this order to keep the codebase working at each step:

1. **`batch/utils/__init__.py`** and **`batch/utils/progress.py`** — standalone, no dependencies on other changes.
2. **Fix `should_process`** in `repository/image_procesing_status.py` — standalone bugfix, safe to ship independently.
3. **Remove auto-commit from `mark_done`** in `repository/image_procesing_status.py` — after auditing all callers and updating them.
4. **`batch/utils/batch_commit.py`** — depends on (3).
5. **Wire `BatchCommitter` and `ProgressTracker` into `extract_text_from_memes.py`**, add `run()`, remove `persist_ocr_result` — depends on (1) and (4).
6. **Smoke-test** the full pipeline on a small directory (5–10 images), verify progress output and that a mid-run kill + resume processes correct images.

---

## Side Finding (Out of Scope)

`OCRTextRepository.overwrite_texts` contains a likely copy-paste bug:

```python
await self.session.execute(
    delete(ImageMetrics).where(   # ← should be delete(OCRText)
        OCRText.image_id == image.id
    )
)
```

This deletes from the wrong table. It should be tracked and fixed separately.
