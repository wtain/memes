# OCR Pipeline: Safe Full-Mode Execution (Restorable OCR Data)

**Date:** 2026-06-29  
**Status:** Proposed  
**Scope:** `batch/extract_text_from_memes.py`, `batch/reset_ocr_status.py`, `repository/ocr_text.py`

---

## Summary

OCR data represents tens of hours of GPU processing. The pipeline must never silently discard it. This spec defines two execution modes, clarifies which operations are safe, and adds a guard against accidental wipe-then-rebuild patterns.

---

## Background

The OCR pipeline currently behaves as follows:

- **Incremental (default):** skips images with `status=done`; new and interrupted images are (re-)processed.
- There is no explicit "full" mode — to force a full re-run, the user calls `reset_ocr_status.py --all` first, which sets all statuses back to pending. The OCR data itself (`ocr_texts` table) is NOT cleared.

`OCRTextRepository.overwrite_texts` deletes and re-inserts rows **per image**, inside the same session that the calling code will later commit. This means:

- If a crash occurs before the batch commit, the per-image delete+insert is rolled back and the old OCR data survives.
- No image ever loses its OCR data unless the new data for that image is successfully committed.

This is the correct invariant. The risk is that a future developer adds a "wipe entire OCR table then rebuild" shortcut, which would destroy all data before any replacement is written.

---

## The Risk: Wipe-Then-Rebuild

A "clear everything and rebuild" approach would look like:

```python
# NEVER DO THIS
await session.execute(delete(OCRText))
await session.commit()
# ... then re-run OCR across all images ...
```

If this runs and the process is interrupted (power loss, OOM, SIGKILL), **all OCR data is gone** — there is nothing to roll back to.

---

## Correct Full-Mode Design

Full mode must mean: **re-process all images, overwriting OCR data per image as each one completes**.

```
Full mode = reset_ocr_status --all  +  incremental run
```

The two-step process:
1. `python -m batch.reset_ocr_status --all` — sets all `status` rows to "pending" (no OCR data touched).
2. `python -m batch.extract_text_from_memes` — runs in incremental mode; now all images are pending, so all are processed. Each image's OCR is overwritten only after its new OCR succeeds and commits.

This is already the correct mental model. The spec formalises it.

---

## Per-Image Overwrite Safety

`OCRTextRepository.overwrite_texts` performs delete+insert for one image per call. Its safety guarantee:

> An image loses its old OCR data only if and when the new OCR data for that same image is committed.

This guarantee holds as long as:
- The delete and the insert share the same session.
- The session is not committed between the delete and the insert.
- On failure, the session is rolled back.

All three conditions are met by the `BatchCommitter` design in [extract-text-progress-batching-2026-06-29.md].

### Known bug (fix in scope)

`overwrite_texts` currently deletes from the wrong table:

```python
# Current (buggy):
await self.session.execute(
    delete(ImageMetrics).where(OCRText.image_id == image.id)  # wrong table
)

# Correct:
await self.session.execute(
    delete(OCRText).where(OCRText.image_id == image.id)
)
```

This bug causes old OCR rows to accumulate (they are never deleted) and `ImageMetrics` rows to be incorrectly deleted. Fix this as part of the OCR pipeline work.

---

## Rules (Enforced by Convention)

1. **Never add a bulk `delete(OCRText)` without an immediate per-image rewrite in the same transaction.** Any script that clears the entire `ocr_texts` table must be treated as destructive and must not be committed to the main pipeline.

2. **`reset_ocr_status.py` is the correct way to trigger a full re-run.** It resets processing status only — OCR data is untouched. Document this in the README/CLAUDE.md.

3. **`overwrite_texts` is the only write path for OCR data.** Do not add INSERT-only paths that bypass the delete step, as they would accumulate duplicate rows.

---

## Changes

### `repository/ocr_text.py` — fix `overwrite_texts`

```python
async def overwrite_texts(self, image, ocr_result, language):
    await self.session.execute(
        delete(OCRText).where(OCRText.image_id == image.id, OCRText.language == language)
        #     ^^^^^^^^ was: delete(ImageMetrics) — wrong table
        # Filter by language too so other languages' rows are preserved
    )
    for bbox, text, confidence in ocr_result:
        self.session.add(OCRText(
            image_id=image.id,
            text=text,
            confidence=float(confidence),
            bbox=...,
            language=language,
        ))
```

Note the additional `OCRText.language == language` filter: each `overwrite_texts` call is for a single language, so only that language's rows should be replaced. Without this filter, all languages are wiped on the first language call, and subsequent language calls re-add correctly — but if the second or third language call fails after the first has committed, the first language's old data is lost. Adding the language filter makes each per-language call truly independent.

### `Readme.md` / `CLAUDE.md` — document full-mode procedure

Add under the batch job notes:

> **Full re-run (re-process all images):**  
> Do NOT clear the `ocr_texts` table. Instead:  
> 1. `python -m batch.reset_ocr_status --all` — resets processing status without touching OCR data.  
> 2. `python -m batch.extract_text_from_memes` — re-processes all images, overwriting OCR per image.  
> This ensures OCR data is preserved if the run is interrupted.

---

## Non-Goals

- Versioning or timestamped history of OCR data (only latest result is kept).
- Backup/export of OCR data before a full re-run.
- Diff between old and new OCR results.

---

## Implementation Order

1. Fix `overwrite_texts` delete target and add language filter — isolated bugfix, no dependencies.
2. Update README/CLAUDE.md with full-mode procedure.
3. (Coordinate with batching spec) — the `BatchCommitter` rollback safety makes step 1 fully atomic.
