# Review: OCR Pipeline Safe Full-Mode Execution

**Spec:** `docs/superpowers/specs/ocr-safe-full-mode-2026-06-29.md`  
**Date:** 2026-06-30  
**Status:** Complete — no post-review fixes needed. 161/161 tests passing.

---

## Files Changed

| File | Change |
|------|--------|
| `repository/ocr_text.py` | Fixed `overwrite_texts` — wrong table and missing language filter |
| `CLAUDE.md` | Added full-mode re-run procedure under Batch pipeline section |

---

## Spec Correctness

**`overwrite_texts` fix** — fully correct:
```python
# Before (buggy):
delete(ImageMetrics).where(OCRText.image_id == image.id)

# After (fixed):
delete(OCRText).where(OCRText.image_id == image.id, OCRText.language == language)
```
- Table corrected: `ImageMetrics` → `OCRText` ✓
- Language filter added ✓
- Stale `ImageMetrics` import removed from the file ✓

**CLAUDE.md documentation** — matches spec wording; placed under the Batch pipeline section directly after the idempotency note, which is the natural location ✓

---

## Logic Correctness

**Language filter is critical for correctness.** Without it, `overwrite_texts("en", ...)` would delete all OCR rows for that image — including `"ru"` and `"es"` rows written earlier in the same image's language loop. With the filter, each of the three `add_language_result` calls operates only on its own language rows. The per-language calls become truly independent: a failure on the second language cannot corrupt the first language's data (the whole batch rolls back, but no language silently wipes another).

**Safety invariant holds.** The delete+insert remain in the same session with no mid-image commit, and the `BatchCommitter` rollback-on-exception path in `run()` ensures old data survives a crash. This satisfies the spec's stated guarantee:

> An image loses its old OCR data only if and when the new OCR data for that same image is committed.

---

## Issues Found

None. The spec identified exactly one code change (the `overwrite_texts` bug) and one documentation change. Both are implemented correctly with no side effects.

---

## Notes

- The two `# todo` comments inside `overwrite_texts` (confidence threshold, session reuse) are pre-existing and out of scope for this spec.
- `batch/reset_ocr_status.py` was not modified — the spec confirms it already does the right thing (status reset only, OCR data untouched).
- No schema changes required.
