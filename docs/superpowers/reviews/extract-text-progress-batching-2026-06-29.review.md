# Review: extract_text_from_memes — Progress Reporting, Batching & Reusability

**Spec:** `docs/superpowers/specs/extract-text-progress-batching-2026-06-29.md`  
**Date:** 2026-06-30  
**Status:** Complete — one fix applied, all tests passing (161/161)

---

## Files Changed

| File | Change |
|------|--------|
| `batch/utils/__init__.py` | New — empty package init |
| `batch/utils/progress.py` | New — `ProgressTracker` class |
| `batch/utils/batch_commit.py` | New — `BatchCommitter` class |
| `repository/image_procesing_status.py` | Modified — `should_process` and `mark_done` |
| `batch/extract_text_from_memes.py` | Modified — wired new utils, added `run()`, removed `persist_ocr_result` |

---

## Spec Correctness

All requirements implemented:

- **ProgressTracker**: `total`, `skip()`, `mark_done()`, `summary()` — correct signatures. Rolling window via `deque(maxlen=50)`. ETA formula `avg × (effective_total - done)`. Print format matches spec exactly: `[42/~58] elapsed=1m23s  avg=2.0s/img  eta≈32s`. Zero-total edge case guarded.
- **BatchCommitter**: `add_language_result`, `on_image_done`, `flush`, `close` — all present and correct. Auto-flush when `_pending >= batch_size`. `close()` flushes remaining then closes session.
- **`should_process`**: `"processing"` → `return True` (retry) ✓
- **`mark_done`**: auto-commit removed ✓
- **`io_producer`**: `tracker.skip()` on all four `continue` branches (dirs, `.mp4`, already-done, read error) ✓
- **`gpu_consumer`**: `committer.add_language_result` per language, then `committer.on_image_done` + `tracker.mark_done` once per image after the language loop ✓
- **`run()`**: correct signature, `total` from non-directory file count, correct `gather` args, `tracker.summary()` after block ✓
- **`main()`**: reads `BATCH_SIZE` and `PROGRESS_EVERY` from env ✓
- **`persist_ocr_result`**: removed ✓
- **Unused imports**: cleaned up (`Image`, `ImageMetricsRepository`, `OCRTextRepository` no longer imported in the main script)

---

## Issues Found and Resolution

### FIXED — `committer.flush()` → `committer.close()` in `run()`

**Original code** (`extract_text_from_memes.py:203`):
```python
await committer.flush()
```
**Spec requires:**
```python
await committer.close()
```

`flush()` was functionally equivalent here (the `async with` context manager closes the session anyway), but it deviated from the spec's documented API contract and left `close()` untested in production. Fixed by replacing with `committer.close()`.

---

### NOT FIXED — `mark_done` missing `session.add()` for new status

`repository/image_procesing_status.py:39`: when `status is None`, a new `ImageProcessingStatus` is created but never added to the session with `self.session.add()`. This means the object is not tracked and the `mark_done` is a silent no-op for this path.

**Not fixed because:** this is a pre-existing bug introduced before this spec, not caused by this change. In normal pipeline flow `mark_started` always runs first (in `io_producer`, with its own session and commit), so by the time `mark_done` runs in the `BatchCommitter`'s session the DB row already exists and `.get()` returns it. The `None` branch is unreachable in practice. The spec explicitly scopes out fixing this.

---

### NOTED — `overwrite_metrics` called 3× per image

`batch_commit.py:22`: `add_language_result` calls `ImageMetricsRepository.overwrite_metrics` on each language iteration (delete + insert, 3×). Only the last language's timing survives in the DB.

**Not fixed because:** this is pre-existing behavior (the original `persist_ocr_result` had the same pattern), not introduced by this change, and out of scope per spec. Tracking timing per-language would require a schema change.

---

### NOTED — No unit tests for `ProgressTracker` / `BatchCommitter`

The new utility classes in `batch/utils/` have deterministic logic (ETA formula, flush threshold, pending counter) that would be straightforward to unit test. No tests were added.

**Not fixed because:** the spec doesn't require them, and no other batch-layer utilities have unit tests — adding tests here would be inconsistent with project norms without a broader testing initiative.

---

## Test Results

```
161 passed, 1 warning in 8.03s
```

All existing backend API tests (120) and rules engine tests (41) pass after changes.
