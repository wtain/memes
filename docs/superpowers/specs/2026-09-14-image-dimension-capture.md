# Image Dimension Capture

status: approved
Originates from: a conversation continuing the 2026-09-12/13 Tier B review-noise discussion — after
metrics landed (`2026-09-13-ingestion-review-progress-visibility-design.md` and its query-consolidation
follow-up), the conversation moved to thread B (text-heavy image classification, e.g. chat/Twitter/Threads
screenshots where OCR text should drive duplicate detection instead of weak CLIP visual similarity for
that subset). The chosen classifier signal — OCR text bounding-box coverage ratio (text area ÷ image
area) — needs real image dimensions, which nothing in this codebase captures today. This spec is that
prerequisite: dimension capture alone, with no classifier logic yet. Related (same OCR-for-dedup problem
space, still unresolved, not superseded by this spec):
`docs/superpowers/specs/drafts/2026-08-19-ocr-assisted-deduplication-draft.md`.

## Problem

`Image.width`/`Image.height` (`Storage/models.py:28-29`) are declared nullable integer columns — and
confirmed live on `general` via a direct read-only query, so the columns genuinely exist in production —
but nothing anywhere in this codebase ever populates them. No migration ever added them either (they
predate this repo's Alembic history, likely created directly via `Base.metadata.create_all()` before
migrations were adopted); they've simply sat unpopulated and unread since. A future text-heavy classifier
needs real dimensions to compute an OCR-text-area-coverage ratio, and that classifier can't be designed
or calibrated against real corpus data without this in place first.

## Goal

Populate `Image.width`/`Image.height` for every newly-ingested image going forward, and backfill them for
the existing corpus, with the smallest possible change — no new extraction pass, no new batch script if
an existing one can be reused.

## Non-goals

- **The text-heavy classifier itself** (coverage-ratio calculation, threshold calibration, wiring into
  Tier A/B dedup logic) — a separate future spec, once real coverage-ratio data can be computed from the
  dimensions this spec captures.
- **A new migration.** The columns already exist live; this is purely an application-code change that
  starts writing to them.
- **Retroactive backfill for `rejected`-status images.** Mirrors `fix_image_formats.py`'s existing
  `--status` choices (`active`/`pending` only) — rejected images are on their way out of the corpus and
  irrelevant to a future classifier that only ever needs to reason about `active`/`pending` images.
- **Any API/frontend exposure of width/height.** Purely internal data capture for a future feature; no
  endpoint, response field, or UI surface changes here.
- **Any change to the format-fix/WebP-conversion logic itself** (renaming rules, canonical extensions,
  animated-frame handling) — purely additive alongside it.

## Key facts this rests on

- Confirmed via `DATABASE_URL_READONLY` against `general`: `\d images` shows `width`/`height` as real
  `integer`, nullable, no default columns — genuinely present in the live schema, not just the ORM model.
- Confirmed via `grep -rli width Storage/alembic/versions/*.py` (and `create_table('images'...)`): zero
  hits. No migration ever created or touched these columns, and no migration creates the `images` table
  at all — this table predates full migration coverage in this repo.
- Confirmed via `grep` across `Backend/`, `batch/`, `repository/`: nothing reads `Image.width`/`.height`
  anywhere today. Populating them is purely additive — zero risk of changing any existing behavior.
- `batch/utils/image_format_fix.py`'s `detect_actual_format()` (`:47-57`) already does
  `PILImage.open(path)` for every image passing through format validation, for both new ingestion
  (`ingest_validate_formats.py`, Stage 1.5) and retroactive maintenance (`fix_image_formats.py`, already
  documented as safe to re-run over the whole corpus). `img.size` is available immediately on open —
  Pillow reads dimensions from the file header, no full pixel decode — so capturing it here costs nothing
  extra: no new file open, no new pass over the corpus.
- **Both** `ingest_validate_formats.py` and `fix_image_formats.py` call the same shared
  `apply_format_fix()` (`batch/utils/image_format_apply.py`) for every image they process. Extending that
  one shared function to persist dimensions covers both "new ingestion" and "retroactive backfill" at
  once — no second script needed. Re-running the *existing* `fix_image_formats.py --status active` (and
  optionally `--status pending`, per its own existing flag) against a live environment is the entire
  backfill mechanism.
- In `_convert_webp_to_jpeg` (`image_format_fix.py:103-138`), the WebP→JPEG flatten only changes color
  mode (`img.convert("RGBA")`/`.convert("RGB")`), never dimensions — `PILImage.new("RGB", rgba.size, ...)`
  explicitly reuses the source image's `.size`. So dimensions captured from the original `detect_actual_format`
  open are accurate for the final converted file too; no need to re-measure post-conversion.
- `apply_format_fix()` currently does **no DB write at all** for the common no-op case (format already
  correct) — it only calls `images_repo.update_filename_and_hash(...)` when something changed. Persisting
  dimensions needs a new, unconditional write (whenever the file was readable), independent of that
  branch.

## Design

### 1. `batch/utils/image_format_fix.py` — thread `(width, height)` through every return path

`detect_actual_format` returns both the format and the size in one open, instead of just the format:

```python
def detect_actual_format(path: str) -> tuple[str, tuple[int, int]] | None:
    """Returns (Pillow's own format name, (width, height)) for the file's real content
    (e.g. ("JPEG", (1080, 1350)), or a format this module has no specific handling for,
    like "MPO"/"AVIF"), or None if Pillow can't identify it at all (corrupt/truncated file,
    or the file doesn't exist). A format Pillow *can* open successfully is never
    "unreadable", even if this module has no specific handling for it."""
    try:
        with PILImage.open(path) as img:
            return img.format, img.size
    except Exception:
        return None
```

`FixOutcome` gains two new fields, both defaulting to `None` (the unreadable case has nothing to report):

```python
@dataclass
class FixOutcome:
    changed: bool
    unreadable: bool = False
    new_filename: str | None = None
    new_content_hash: str | None = None
    animated: bool = False
    width: int | None = None
    height: int | None = None
```

`fix_image_file` unpacks the new tuple and threads `width`/`height` into every `FixOutcome` it returns
(all four exit points: unmapped-format no-op, already-correct no-op, rename, WebP conversion):

```python
def fix_image_file(base_path: str, filename: str) -> FixOutcome:
    """filename must already exist directly under base_path. See module docstring for the
    possible outcomes (unreadable / renamed / converted / no-op). A format Pillow opens
    successfully but that isn't in FORMAT_ACCEPTABLE_EXTENSIONS (e.g. MPO, AVIF, ICO) is
    left untouched -- guessing a canonical extension for a format this module doesn't
    otherwise handle risks corrupting a valid file's name, and "unreadable" would be wrong
    since the file opens fine."""
    path = os.path.join(base_path, filename)
    detected = detect_actual_format(path)

    if detected is None:
        return FixOutcome(changed=False, unreadable=True)
    actual_format, (width, height) = detected

    if actual_format == "WEBP":
        return _convert_webp_to_jpeg(base_path, filename, path, width, height)

    acceptable_extensions = FORMAT_ACCEPTABLE_EXTENSIONS.get(actual_format)
    if acceptable_extensions is None:
        return FixOutcome(changed=False, width=width, height=height)

    current_ext = os.path.splitext(filename)[1].lower()
    if current_ext not in acceptable_extensions:
        return _rename_in_place(base_path, filename, CANONICAL_EXTENSION[actual_format], width, height)

    return FixOutcome(changed=False, width=width, height=height)


def _rename_in_place(base_path: str, filename: str, actual_ext: str, width: int, height: int) -> FixOutcome:
    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}{actual_ext}")
    os.rename(os.path.join(base_path, filename), os.path.join(base_path, final_name))
    return FixOutcome(changed=True, new_filename=final_name, width=width, height=height)


def _convert_webp_to_jpeg(base_path: str, filename: str, path: str, width: int, height: int) -> FixOutcome:
    with PILImage.open(path) as img:
        # ... unchanged body ...
        animated = getattr(img, "n_frames", 1) > 1
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flattened = PILImage.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[3])
        else:
            flattened = img.convert("RGB")

    converted_originals_dir = os.path.join(base_path, CONVERTED_ORIGINALS_DIRNAME)
    os.makedirs(converted_originals_dir, exist_ok=True)
    original_dest_name = available_filename(converted_originals_dir, filename)
    os.rename(path, os.path.join(converted_originals_dir, original_dest_name))

    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}.jpg")
    final_path = os.path.join(base_path, final_name)
    flattened.save(final_path, "JPEG", quality=JPEG_QUALITY)

    new_content_hash = sha256_file(final_path)
    return FixOutcome(
        changed=True, new_filename=final_name, new_content_hash=new_content_hash, animated=animated,
        width=width, height=height,
    )
```

Only the function signatures and `return FixOutcome(...)` calls change in `_rename_in_place`/
`_convert_webp_to_jpeg`; their internal logic (renaming, flattening, saving) is untouched.

### 2. `repository/images.py` — new `update_dimensions` method

Same shape as the existing `update_filename_and_hash`:

```python
    async def update_dimensions(self, image_id, width: int, height: int) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(width=width, height=height)
        )
```

### 3. `batch/utils/image_format_apply.py` — persist dimensions unconditionally when readable

```python
async def apply_format_fix(
    images_repo: ImagesRepository,
    extras_repo: ImageExtrasRepository,
    metrics: SimpleMetricsListener,
    base_path: str,
    image_id,
    filename: str,
) -> None:
    try:
        outcome = fix_image_file(base_path, filename)
    except Exception as e:
        print(f"  error fixing {filename}: {e}")
        metrics.increment("error.fix_failed")
        return

    if outcome.unreadable:
        await extras_repo.set_flagged(image_id, True, remarks="unreadable during format validation")
        metrics.increment("unreadable")
        return

    await images_repo.update_dimensions(image_id, outcome.width, outcome.height)

    if not outcome.changed:
        metrics.increment("no_op")
        return

    await images_repo.update_filename_and_hash(
        image_id, outcome.new_filename, content_hash=outcome.new_content_hash,
    )
    if outcome.animated:
        metrics.increment("converted_animated")
    else:
        metrics.increment("converted" if outcome.new_content_hash else "renamed")
```

The new `update_dimensions` call sits between the `unreadable` early-return and the `changed` branch —
every readable image gets its dimensions persisted regardless of whether a rename/conversion also
happened, including the common no-op case that previously touched the DB not at all.

**Deliberately unconditional, not conditional on "was previously NULL":** re-running this over the whole
corpus rewrites the same values on every pass rather than skipping already-populated rows. Simpler than
reading-before-writing, and cheap — a single-row `UPDATE` per image, no cascading effects — for a batch
job that's manually/admin-triggered, never scheduled.

### 4. No changes needed to `ingest_validate_formats.py` or `fix_image_formats.py` themselves

Both already call `apply_format_fix()` per image; extending that one shared function is sufficient. New
ingestion gets dimensions automatically via the existing Stage 1.5 flow. The existing corpus gets
backfilled by re-running the existing, already-documented-as-safe-to-re-run `fix_image_formats.py`.

## Testing

- **`batch/tests/test_image_format_fix.py`:** the 5 existing `detect_actual_format(...) == "FORMAT"`
  assertions (`test_detects_real_jpeg`, `test_detects_png_mislabeled_as_jpg`, `test_detects_webp`,
  `test_returns_none_for_unreadable_file`, `test_reports_raw_pillow_format_for_unmapped_formats`) must be
  updated for the new `(format, size)` return shape — e.g.
  `detect_actual_format(...)[0] == "JPEG"` for the 4 format-detecting ones;
  `test_returns_none_for_unreadable_file` needs no change (still asserts bare `is None`). Every existing
  `fix_image_file(...)` test that constructs a 4×4 fixture via the shared `_save` helper should gain
  `assert outcome.width == 4` / `assert outcome.height == 4` (both no-op/rename tests and the WebP-conversion
  tests — `test_converts_opaque_webp_to_jpeg`, `test_flattens_transparent_webp_onto_white_background`, etc.).
  Add one new test with a non-square fixture (e.g. `PILImage.new("RGB", (8, 4), ...)`) asserting
  `width != height`, proving the two aren't accidentally swapped or conflated anywhere in the threading.
  `test_flags_unreadable_file_without_changing_it` should gain `assert outcome.width is None` /
  `assert outcome.height is None`.
- **`batch/tests/test_image_format_apply.py`:**
  - `test_noop_is_counted_and_touches_neither_repo` — rename (its own premise, "touches neither repo," is
    no longer literally true) and add `images_repo.update_dimensions.assert_awaited_once_with("img-1", 4, 4)`
    alongside the existing `update_filename_and_hash.assert_not_awaited()`.
  - `test_unreadable_flags_the_image` — add `images_repo.update_dimensions.assert_not_awaited()` (no
    dimensions available when Pillow can't open the file at all).
  - `test_rename_persists_the_new_filename_without_a_hash` and `test_conversion_persists_the_new_filename_and_hash`
    — add a `update_dimensions.assert_awaited_once_with("img-1", 4, 4)` assertion to each (both use the
    shared 4×4 `_save` fixture).
  - `test_animated_conversion_gets_its_own_counter` — its hand-constructed `FixOutcome(...)` currently
    omits `width`/`height` (defaulting to `None`, unrealistic for a `changed=True` outcome in real usage);
    add `width=4, height=4` to the constructor for realism, and assert
    `update_dimensions.assert_awaited_once_with("img-1", 4, 4)`.
- **`tests/integration/test_fix_image_formats.py`:** every existing test that creates a real `Image` row
  and a real 4×4 fixture file (`test_fixes_active_images_by_default`, `test_status_flag_can_target_pending_images`,
  `test_rerun_is_a_noop_on_already_fixed_images`) should gain `assert refreshed.width == 4` /
  `assert refreshed.height == 4` after the run — the real end-to-end proof that dimensions actually reach
  the database through the full repository/session path, not just the mocked unit tests.
  `test_flags_unreadable_active_image` should assert `refreshed.width is None` / `refreshed.height is None`
  (unchanged, since nothing was ever written).
- **`tests/integration/test_ingest_validate_formats.py`:** same treatment for
  `test_renames_mislabeled_pending_image_and_updates_filename`, `test_converts_webp_pending_image_and_updates_hash`,
  and `test_noop_image_is_counted_and_untouched` — add `refreshed.width`/`.height` assertions, proving
  dimension capture works through the ingestion path too, not just retroactive maintenance.
- `cd Backend && pytest -q` is unaffected (no `Backend/` files touched) — still run as a regression check
  since `repository/images.py` is imported there. `pytest batch/tests/ -q` for the unit-level changes.
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
  (full sweep, since `repository/images.py` is shared code) for the integration-level changes.

## Rollout

1. Ship the code change (no schema change, no migration) — behavior-neutral for every existing code path
   except the two touched batch scripts, which start writing to two previously-dead columns.
2. With explicit go-ahead (this writes to live, continuously-running databases — same gate the partial-index
   work used before its live rollout step), re-run `fix_image_formats.py --status active` against `metal`,
   `general`, and `it` in turn to backfill the existing corpus. Optionally also `--status pending` per
   environment, to cover any in-flight ingestion batch that predates this feature.
3. Verify via a read-only spot-check (`SELECT count(*) FILTER (WHERE width IS NOT NULL) FROM images WHERE status = 'active'`
   or similar) that the backfill actually reached the corpus, rather than assuming success from the batch
   job's own reported counters.

No further specs need this data yet — it lands purely as a prerequisite. The text-heavy classifier
(coverage-ratio calculation and threshold calibration against real data) is deliberately a separate,
future spec.
