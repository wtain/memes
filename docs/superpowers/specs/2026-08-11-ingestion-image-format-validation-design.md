# Ingestion Image Format Validation Design

Status: approved
Originates from: docs/runbooks/ingestion-pipeline.md (known WebP-mislabeled-as-.jpg gotcha, also
documented in CLAUDE.md's "Known gotchas" section)

**Date:** 2026-08-11

---

## Goal

Some inbound meme files have an extension that doesn't match their actual content (most commonly
a WebP file saved with a `.jpg` extension). This causes two distinct problems:

1. **Wrong extension, otherwise fine format** (e.g. a real PNG saved as `.jpg`) — cosmetic/metadata
   bug, but confusing for humans and anything that trusts the extension.
2. **WebP content**, regardless of what extension it currently has — many libraries in this
   pipeline can't consume WebP at all (confirmed: Ollama's llava/qwen2.5vl vision backend used by
   `build_image_descriptions.py` fails with "Failed to load image or audio file").

This design adds validation + automatic remediation for both problems, in two places:

- **At ingestion time**, before the expensive embeddings/OCR/description pipeline ever touches a
  new image, so those steps always operate on correctly-typed, universally-consumable files.
- **Retroactively**, as a one-off/rerunnable maintenance batch over the existing active corpus,
  since the bug predates this fix and images already ingested need the same treatment.

## Non-goals

- General image corruption repair. A file Pillow can't identify at all is flagged for human
  review, not auto-fixed.
- Normalizing every image to a single canonical format. Only WebP is force-converted (the one
  format known to be unconsumeable downstream); other real formats (PNG, GIF, BMP, TIFF, real
  JPEG) are left as-is, just renamed to match their actual content if mislabeled.
- Any change to Tier A/B duplicate review, promotion, or the rest of the ingestion pipeline's
  existing stages — this slots in as an additional stage, not a redesign of the others.

## Detection

`detect_actual_format(path) -> str | None` (new, in `batch/utils/image_format.py`) opens the file
with Pillow and reads its real format via `Image.open(path).format`, mapping it to a canonical
extension:

| Pillow format | Canonical extension |
|---|---|
| `JPEG` | `.jpg` |
| `PNG` | `.png` |
| `WEBP` | `.webp` |
| `GIF` | `.gif` |
| `BMP` | `.bmp` |
| `TIFF` | `.tiff` |

Returns `None` if Pillow raises `UnidentifiedImageError` (or any other decode failure) — the file
is unreadable, not just mislabeled.

## Remediation

`fix_image_file(base_path, filename) -> FixOutcome` (same module) is the single entry point both
callers use. Given a filename that already exists directly under `base_path`:

1. **Unreadable** (`detect_actual_format` returned `None`): `FixOutcome.unreadable = True`. Nothing
   on disk changes.
2. **Actual format is WebP** (regardless of current extension): **convert**.
   - Load with Pillow. If the image has an alpha channel (`RGBA`, `LA`, or palette mode with
     transparency), flatten it onto a white background before converting to `RGB` — JPEG has no
     alpha channel.
   - Save as `<stem>.jpg` at quality 95, using a collision-safe filename within `base_path` (see
     Collision handling below).
   - Move the *original* WebP file (unmodified bytes, original filename) into
     `<BASE_PATH>/converted_originals/`, collision-safe within that directory too. This is a
     human-reviewable audit trail only — no DB row is created for it (see Database updates below).
   - Compute the new file's sha256 for the caller to persist as the updated `content_hash`.
   - `FixOutcome.changed = True`, `new_filename = <the collision-safe .jpg name>`,
     `new_content_hash = <sha256 of the new file>`.
3. **Extension doesn't match a non-WebP real format** (e.g. `foo.jpg` that's actually a PNG):
   **rename**.
   - Rename in place to the canonical extension, collision-safe within `base_path`.
   - Bytes are untouched — nothing moves to `converted_originals/`, there's nothing lossy to
     preserve; the original content is fully intact at its new name.
   - `FixOutcome.changed = True`, `new_filename = <the collision-safe renamed name>`,
     `new_content_hash = None` (unchanged).
4. **Otherwise** (real format is not WebP and the extension already matches it):
   `FixOutcome.changed = False`.

### Collision handling

`batch/utils/safe_move.py`'s `move_without_overwrite` already implements numeric-suffix collision
avoidance (`name.ext` → `name_1.ext` → `name_2.ext` …) but only for moves where the target filename
is derived from the source's own basename. This design extracts that suffix-loop into a reusable
`available_filename(dest_dir, filename) -> str` helper in the same module, so:

- `move_without_overwrite` calls it internally (no behavior change for existing callers).
- `image_format.py` calls it directly for both the in-place rename case (target dir == source dir,
  different desired filename) and the convert case (new `.jpg` in `base_path`, moved original in
  `converted_originals/`).

### Idempotency

Both callers are safe to re-run. A file already fixed produces `FixOutcome.changed = False` on a
subsequent pass — its extension already matches its real format, and it's no longer WebP.

## Database updates

`repository/images.py` gains `update_filename_and_hash(image_id, filename, content_hash=None)`,
sibling to the existing `update_content_hash`. Same `Image` row is updated in place — no new row is
created for either the rename or the convert case, because the underlying picture is unchanged (a
rename touches no pixels; a WebP→JPEG re-encode is visually equivalent). This means any
already-computed embedding, OCR text, or tags for that image stay valid — the point of the
retroactive maintenance batch is exactly to *avoid* forcing full re-enrichment.

`repository/image_extras.py`'s `set_flagged` is extended to optionally accept `remarks`, so an
unreadable file gets both `flagged=True` and a short machine-generated note (e.g. `"unreadable
during format validation"`) in one upsert, instead of only being visible in batch-run log output.

## Two callers

### `batch/ingest_validate_formats.py` (new) — ingestion Stage 1.5

Runs after `ingest_hash_dedup.py`, before `build_image_embeddings.py --status pending`. Joins the
same active ingestion run Stage 1 used (`BatchRunRepository.get_active_run(kind="ingestion")`, same
`batch_id`) rather than starting a separate tracked run — this is an additional stage of the same
run, not an independent batch.

- Iterates `Image` rows with `status = "pending"` under the run's `batch_id`.
- Calls `fix_image_file(base_path, filename)` per image.
- Unreadable → `ImageExtrasRepository.set_flagged(image_id, True, remarks=...)`, metrics counter,
  file/row untouched, loop continues.
- Changed → `ImagesRepository.update_filename_and_hash(...)`.
- No-op → nothing.
- `update_stats`/`set_stage` on the run like Stage 1 does.

CLI: `python -m batch.ingest_validate_formats --env <env>`. No extra flags — always scoped to the
active run's pending images.

Runbook/CLAUDE.md's documented ingestion run order becomes:

```
ingest_hash_dedup
ingest_validate_formats          <-- NEW
build_image_embeddings --status pending --incremental
extract_text_from_memes --status pending
ingest_find_duplicates --tier tier_a
ingest_find_duplicates --tier tier_b
ingest_promote
```

### `batch/fix_image_formats.py` (new) — retroactive maintenance batch

Standalone, `tracked_run(kind="fix_image_formats", trigger=...)`-wrapped, following
`move_flagged.py`'s shape (supports being invoked with a pre-created `run_id` the same way, for
consistency with the admin-batch-service pattern, even though nothing chains after it).

- `--status` flag, default `"active"` — covers the corpus retroactively. Overridable (e.g.
  `--status pending`) to cover an old in-flight ingestion batch that predates this feature and
  therefore never went through Stage 1.5.
- Same per-image logic and repository calls as the ingestion-time caller.

CLI: `python -m batch.fix_image_formats --env <env> [--status active]`.

Listed under CLAUDE.md's "Maintenance (run as needed)" batch scripts.

## Error handling / edge cases

- Unreadable file: flagged + remarks, metrics counter, never aborts the batch — matches
  `move_flagged.py`'s existing catch/log/continue posture for per-file failures.
- Filename collisions: numeric-suffix avoidance, shared with `move_without_overwrite`.
- `converted_originals/` is not tracked in the DB and has no retention policy — it's a human audit
  dump, same role as the existing `duplicates/`, `rejected/`, `excluded/` subfolders of `BASE_PATH`.
- Both scripts are safe to re-run at any time; already-fixed images are no-ops on subsequent runs.

## Testing

- Unit tests for `batch/utils/image_format.py` (no DB): tiny real fixture images covering WebP
  with alpha, WebP without alpha, a mislabeled PNG-as-`.jpg`, a correctly-labeled file (no-op), and
  a truncated/corrupt file. Assert correct detection, correct `FixOutcome`, and correct collision
  handling when the target name is already taken.
- `tests/integration/` coverage for both scripts: assert `Image.filename`/`content_hash` update
  correctly end-to-end, and that `ImageExtras.flagged`+`remarks` get set for an unreadable fixture.
  Per CLAUDE.md's shared-code testing gotcha, run the **whole** `tests/integration/` root (not just
  the new test file) before merging, since this touches `repository/images.py`.

## Docs to update in the same change

- `docs/runbooks/ingestion-pipeline.md` — new step in the TL;DR command block and the numbered
  "Running a batch" walkthrough.
- `CLAUDE.md` — `ingest_validate_formats` added to the ingestion run-order comment block in the
  Batch pipeline section; `fix_image_formats` added to the "Maintenance (run as needed)" list.
