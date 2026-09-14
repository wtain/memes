# Image Dimension Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 2 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** Populate `Image.width`/`Image.height` for every newly-ingested image going forward, and
backfill them for the existing corpus, by threading dimensions through the shared format-fix pipeline
both `ingest_validate_formats.py` and `fix_image_formats.py` already call per image.

**Architecture:** `batch/utils/image_format_fix.py`'s `detect_actual_format()` already opens every image
with Pillow — capturing `img.size` there is free. Task 1 threads `(width, height)` through every
`FixOutcome` return path, adds a `repository/images.py` setter, and makes
`batch/utils/image_format_apply.py` persist dimensions unconditionally whenever a file is readable
(including the previously-untouched no-op case). Proved entirely against the disposable test database —
no schema change, no migration (the columns already exist live). Task 2 — the live backfill rollout for
the existing corpus — is controller-executed, gated on the user's explicit go-ahead before touching any
live database, exactly like the Tier B partial-index work's rollout task.

**Tech Stack:** Pillow (`img.size` on an already-open file handle), SQLAlchemy Core `update()`, pytest
with real `tmp_path` fixture images (no mocking of Pillow itself, matching this module's existing test
style).

**Spec:** `docs/superpowers/specs/2026-09-14-image-dimension-capture.md`

## Global Constraints

- `detect_actual_format`'s return type changes from `str | None` to `tuple[str, tuple[int, int]] | None`
  — every existing caller/test must be updated for this, not left broken.
- `FixOutcome` gains `width: int | None = None` and `height: int | None = None`, both defaulting to
  `None` (the unreadable case has nothing to report).
- `apply_format_fix()`'s new `images_repo.update_dimensions(...)` call is **unconditional** whenever
  `outcome.unreadable` is `False` — including the no-op case, which previously made no DB write at all.
  Deliberately not conditional on "was previously NULL" — see the spec's Key Facts for why (simpler, cheap
  single-row UPDATE, batch job is never scheduled).
- No new migration, no schema change — `Image.width`/`Image.height` already exist live (verified against
  `general` via `DATABASE_URL_READONLY` before this plan was written).
- No new batch script for the backfill — Task 2 re-runs the *existing* `fix_image_formats.py`.
- **Never hand a subagent the main `DATABASE_URL` for any of `general`'s/`metal`'s/`it`'s environments.**
  Task 1 uses only the disposable test database
  (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`). Task 2 is controller-only for
  exactly this reason.
- Per this repo's testing gotcha: never combine `Backend/tests/`, `tests/integration/`, and `batch/tests/`
  in one `pytest` invocation — run them as separate commands.

---

## File Structure

**Modify:** `batch/utils/image_format_fix.py` (`detect_actual_format`, `FixOutcome`, `fix_image_file`,
`_rename_in_place`, `_convert_webp_to_jpeg`).
**Modify:** `repository/images.py` (new `update_dimensions` method).
**Modify:** `batch/utils/image_format_apply.py` (`apply_format_fix`, unconditional dimension persist).
**Modify:** `batch/tests/test_image_format_fix.py`, `batch/tests/test_image_format_apply.py`,
`tests/integration/test_fix_image_formats.py`, `tests/integration/test_ingest_validate_formats.py`.
**Docs — modify:** the spec's status line (final task, Task 2).

---

## Task 1: Thread dimensions through the shared format-fix pipeline

**Files:**
- Modify: `batch/utils/image_format_fix.py:47-57` (`detect_actual_format`), `:60-66` (`FixOutcome`),
  `:69-93` (`fix_image_file`), `:96-100` (`_rename_in_place`), `:103-138` (`_convert_webp_to_jpeg`)
- Modify: `repository/images.py` (new method, alongside the existing `update_filename_and_hash` at `:163-169`)
- Modify: `batch/utils/image_format_apply.py:14-54` (`apply_format_fix`)
- Modify: `batch/tests/test_image_format_fix.py`
- Modify: `batch/tests/test_image_format_apply.py`
- Modify: `tests/integration/test_fix_image_formats.py`
- Modify: `tests/integration/test_ingest_validate_formats.py`

**Interfaces:**
- Produces: `detect_actual_format(path) -> tuple[str, tuple[int, int]] | None`;
  `FixOutcome.width`/`.height: int | None`; `ImagesRepository.update_dimensions(image_id, width: int, height: int) -> None`.
- Consumes: nothing new from outside this task.

- [ ] **Step 1: Update `batch/tests/test_image_format_fix.py` for the new return shape and dimension assertions**

Change the 4 format-detecting assertions from bare string equality to tuple-index-0 (the 5th,
`test_returns_none_for_unreadable_file`, needs no change — still asserts bare `is None`):

```python
def test_detects_real_jpeg(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")

    assert detect_actual_format(str(tmp_path / "a.jpg"))[0] == "JPEG"


def test_detects_png_mislabeled_as_jpg(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")

    assert detect_actual_format(str(tmp_path / "a.jpg"))[0] == "PNG"


def test_detects_webp(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")

    assert detect_actual_format(str(tmp_path / "a.webp"))[0] == "WEBP"


def test_returns_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"this is not image data")

    assert detect_actual_format(str(path)) is None


def test_reports_raw_pillow_format_for_unmapped_formats(tmp_path):
    """A format this module has no canonical-extension mapping for is still identified --
    it must not be conflated with "Pillow couldn't read this at all" (which returns None)."""
    _save(tmp_path, "a.ppm", "PPM")

    assert detect_actual_format(str(tmp_path / "a.ppm"))[0] == "PPM"
```

Add dimension assertions to every existing `fix_image_file(...)` test that uses the shared `_save`
helper (all of which create 4×4 images per `_save`'s hardcoded `size = (4, 4)`). Add
`assert outcome.width == 4` and `assert outcome.height == 4` right after each existing
`assert outcome.changed is ...` line, in: `test_noop_when_extension_already_matches`,
`test_noop_for_jpeg_saved_as_jpeg_extension`, `test_noop_for_tiff_saved_as_tif_extension`,
`test_leaves_unmapped_but_readable_format_untouched`, `test_unmapped_format_under_a_wrong_extension_is_still_untouched`,
`test_renames_mislabeled_non_webp_file`, `test_rename_avoids_collision_with_existing_file`,
`test_converts_opaque_webp_to_jpeg`, `test_converts_webp_mislabeled_as_jpg_reusing_the_same_name`,
`test_flattens_transparent_webp_onto_white_background`, `test_single_frame_webp_conversion_is_not_reported_as_animated`,
`test_animated_webp_conversion_reports_animated`, `test_convert_avoids_collision_in_both_target_directories`.

Add to `test_flags_unreadable_file_without_changing_it`, right after `assert outcome.unreadable is True`:

```python
    assert outcome.width is None
    assert outcome.height is None
```

Add one new test, after the `detect_actual_format` block, proving width/height aren't swapped or
conflated:

```python
def test_dimensions_are_not_swapped_for_a_non_square_image(tmp_path):
    path = os.path.join(str(tmp_path), "wide.jpg")
    PILImage.new("RGB", (8, 4), (255, 0, 0)).save(path, "JPEG")

    outcome = fix_image_file(str(tmp_path), "wide.jpg")

    assert outcome.width == 8
    assert outcome.height == 4
```

- [ ] **Step 2: Run to verify RED**

```bash
pytest batch/tests/test_image_format_fix.py -v
```

Expected: every test that references `outcome.width`/`.height` or indexes `detect_actual_format(...)[0]`
fails — the former with `AttributeError: 'FixOutcome' object has no attribute 'width'`, the latter with
`TypeError: 'str' object is not subscriptable... ` (indexing a bare string with `[0]` doesn't error the
same way, but `detect_actual_format(...)[0] == "JPEG"` against a bare string returns the string's first
character, e.g. `"J"`, which then compares unequal to `"JPEG"` — a real assertion failure, not a crash).
Confirm the specific failures make sense before moving on.

- [ ] **Step 3: Implement the production changes in `batch/utils/image_format_fix.py`**

Replace `detect_actual_format` (currently lines 47-57):

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

Replace `FixOutcome` (currently lines 60-66):

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

Replace `fix_image_file` (currently lines 69-93):

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
```

Replace `_rename_in_place` (currently lines 96-100):

```python
def _rename_in_place(base_path: str, filename: str, actual_ext: str, width: int, height: int) -> FixOutcome:
    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}{actual_ext}")
    os.rename(os.path.join(base_path, filename), os.path.join(base_path, final_name))
    return FixOutcome(changed=True, new_filename=final_name, width=width, height=height)
```

In `_convert_webp_to_jpeg` (currently lines 103-138), change only the signature and the final `return`
statement — the body (flatten/save logic) is untouched:

```python
def _convert_webp_to_jpeg(base_path: str, filename: str, path: str, width: int, height: int) -> FixOutcome:
    with PILImage.open(path) as img:
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

- [ ] **Step 4: Run to verify GREEN**

```bash
pytest batch/tests/test_image_format_fix.py -v
```

Expected: all tests pass, including the new `test_dimensions_are_not_swapped_for_a_non_square_image`.

- [ ] **Step 5: Add `update_dimensions` to `repository/images.py`**

Add alongside `update_filename_and_hash` (currently lines 163-169):

```python
    async def update_dimensions(self, image_id, width: int, height: int) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(width=width, height=height)
        )
```

(`update` and `Image` are already imported in this file — confirm before adding; both are used by
`update_filename_and_hash` immediately above.)

- [ ] **Step 6: Update `batch/tests/test_image_format_apply.py` for dimension persistence**

Rename `test_noop_is_counted_and_touches_neither_repo` (its premise is no longer literally true) and add
a dimension assertion:

```python
async def test_noop_is_counted_and_persists_dimensions_only(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.jpg")

    assert metrics.counters_dict() == {"no_op": 1}
    images_repo.update_dimensions.assert_awaited_once_with("img-1", 4, 4)
    images_repo.update_filename_and_hash.assert_not_awaited()
    extras_repo.set_flagged.assert_not_awaited()
```

Add to `test_unreadable_flags_the_image`, right after `images_repo.update_filename_and_hash.assert_not_awaited()`:

```python
    images_repo.update_dimensions.assert_not_awaited()
```

Add to `test_rename_persists_the_new_filename_without_a_hash`, right after the existing
`images_repo.update_filename_and_hash.assert_awaited_once_with(...)`:

```python
    images_repo.update_dimensions.assert_awaited_once_with("img-1", 4, 4)
```

Add to `test_conversion_persists_the_new_filename_and_hash`, right after the existing
`assert kwargs["content_hash"]` line:

```python
    images_repo.update_dimensions.assert_awaited_once_with("img-1", 4, 4)
```

Update `test_animated_conversion_gets_its_own_counter`'s hand-constructed `FixOutcome` to include
realistic dimensions (a `changed=True` outcome always carries real width/height in production; the
existing fixture omitting them is unrealistic), and assert the persist call:

```python
@pytest.mark.asyncio
async def test_animated_conversion_gets_its_own_counter(tmp_path):
    """An animated source is flattened to its first frame -- counted separately so an
    operator can see from the run's stats that animation was lost."""
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()
    outcome = FixOutcome(
        changed=True, new_filename="a.jpg", new_content_hash="deadbeef", animated=True,
        width=4, height=4,
    )

    with patch("batch.utils.image_format_apply.fix_image_file", return_value=outcome):
        await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.webp")

    assert metrics.counters_dict() == {"converted_animated": 1}
    images_repo.update_filename_and_hash.assert_awaited_once_with(
        "img-1", "a.jpg", content_hash="deadbeef",
    )
    images_repo.update_dimensions.assert_awaited_once_with("img-1", 4, 4)
```

- [ ] **Step 7: Run to verify RED**

```bash
pytest batch/tests/test_image_format_apply.py -v
```

Expected: every test asserting `images_repo.update_dimensions...` fails (`AssertionError: Expected
update_dimensions to have been called once. Called 0 times.`) — `apply_format_fix` doesn't call it yet.

- [ ] **Step 8: Implement the change in `batch/utils/image_format_apply.py`**

Replace the full function body (currently lines 14-54):

```python
async def apply_format_fix(
    images_repo: ImagesRepository,
    extras_repo: ImageExtrasRepository,
    metrics: SimpleMetricsListener,
    base_path: str,
    image_id,
    filename: str,
) -> None:
    """Fixes one image's file and persists the result. Only fix_image_file() -- pure
    filesystem/Pillow logic, no DB access -- is wrapped in try/except: a failure there
    never touches the DB session, so catching it and moving on to the next image is safe.
    The persistence calls below are deliberately NOT wrapped: a failure in a DB statement
    aborts the whole Postgres transaction server-side, so catching it and continuing to
    issue more statements on the same session would just cascade-fail every remaining
    image with a misleading "error.fix_failed" instead of surfacing the real problem -- a
    DB-level failure is left to propagate and abort run() normally, exactly like every
    other batch script in this codebase already does."""
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

    # Unconditional whenever the file was readable -- including the no-op case below,
    # which previously made no DB write at all. Not conditional on "was previously NULL":
    # simpler than reading before writing, and cheap (one-row UPDATE) for a batch job
    # that's manually/admin-triggered, never scheduled. See
    # docs/superpowers/specs/2026-09-14-image-dimension-capture.md.
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

- [ ] **Step 9: Run to verify GREEN**

```bash
pytest batch/tests/test_image_format_apply.py -v
```

Expected: all tests pass.

- [ ] **Step 10: Update the two integration test files with dimension assertions**

In `tests/integration/test_fix_image_formats.py`, add to `test_fixes_active_images_by_default` right
after `assert refreshed.filename == "a.png"`:

```python
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_status_flag_can_target_pending_images` right after `assert refreshed.content_hash != "orig"`:

```python
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_rerun_is_a_noop_on_already_fixed_images` right after the `second` run's assertion
(`assert second.counters_dict() == {"no_op": 1}`):

```python
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_flags_unreadable_active_image` right after `assert extras.remarks == "unreadable during format validation"`:

```python
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.width is None
    assert refreshed.height is None
```

In `tests/integration/test_ingest_validate_formats.py`, add to
`test_renames_mislabeled_pending_image_and_updates_filename` right after `assert (tmp_path / "a.png").exists()`:

```python
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_converts_webp_pending_image_and_updates_hash` right after the `with PILImage.open(...)` block's
`assert img.format == "JPEG"`:

```python
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_noop_image_is_counted_and_untouched` right after `assert refreshed.content_hash == "orig"`:

```python
    assert refreshed.width == 4
    assert refreshed.height == 4
```

Add to `test_flags_unreadable_pending_image_and_leaves_it_alone` right after
`assert extras.remarks == "unreadable during format validation"`:

```python
    assert refreshed.width is None
    assert refreshed.height is None
```

- [ ] **Step 11: Run both integration test files to verify GREEN**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_fix_image_formats.py tests/integration/test_ingest_validate_formats.py -v
```

Expected: all tests pass. (These pass on the FIRST run here, since Steps 3 and 8 already landed the
production code the integration tests exercise — this step is proof, not a RED/GREEN cycle of its own.)

- [ ] **Step 12: Run the full `batch/tests/` suite**

```bash
pytest batch/tests/ -q
```

Expected: PASS, no regressions in any other batch unit test.

- [ ] **Step 13: Run the full backend suite (regression check)**

```bash
cd Backend && pytest -q
```

Expected: PASS, unchanged from before this task — no `Backend/` files were touched, but
`repository/images.py` is imported there, so this confirms nothing broke.

- [ ] **Step 14: Run the full integration sweep**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: PASS. Per this repo's "run the whole root, not just the file that looks related" rule —
`repository/images.py` is shared code, so the full root runs here, not just the two touched files.

- [ ] **Step 15: Commit**

```bash
git add batch/utils/image_format_fix.py repository/images.py batch/utils/image_format_apply.py \
        batch/tests/test_image_format_fix.py batch/tests/test_image_format_apply.py \
        tests/integration/test_fix_image_formats.py tests/integration/test_ingest_validate_formats.py
git commit -m "feat: capture image width/height through the format-fix pipeline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 2: Live backfill rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** Its Step 2 writes to live `metal`/`general`/`it` databases the
developer's own running backends depend on continuously — exactly the case `CLAUDE.md`'s "Live database
access for agents" section and this repo's own established pattern (the Tier B partial-index work) exist
for. The controller runs every step below itself.

**Before Step 2 (the first live-environment write), stop and get the user's explicit go-ahead.** Name the
three environments this will touch and what it does (re-runs the already-existing, already-safe-to-re-run
`fix_image_formats.py` — no new code path, no schema change) before proceeding.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-14-image-dimension-capture.md` (status + final verification
  note).

- [ ] **Step 1: Confirm the plan and get the go-ahead**

Summarize for the user: Task 1's code is merged; Step 2 below will re-run `fix_image_formats.py --status active`
(and optionally `--status pending`) against `metal`, `general`, and `it` in turn, backfilling
`width`/`height` for the existing corpus via the exact same batch job that already runs safely and
repeatedly for format validation — no new code path is exercised beyond what Task 1 already added to that
shared function. Wait for explicit confirmation before continuing.

- [ ] **Step 2: Run the backfill against all three environments**

Per `CLAUDE.md`'s documented batch-script workflow, for each of `metal`, `general`, `it` in turn:

```powershell
Get-Content ..\environments\.env.<environment> | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
python -m batch.fix_image_formats --env <environment> --status active
```

Optionally also `--status pending` per environment, to cover any in-flight ingestion batch that predates
this feature (per the script's own existing flag). Confirm each environment's backend
(`/api/diagnostics/health`) still responds normally after its run — this only adds data to two previously-
empty columns, not expected to cause any disruption, but confirm anyway since these are live,
continuously-used services.

- [ ] **Step 3: Verify the backfill actually reached the corpus (read-only)**

For each environment, via `DATABASE_URL_READONLY` (never the main `DATABASE_URL`):

```sql
SELECT count(*) FILTER (WHERE width IS NOT NULL) AS with_dims, count(*) AS total
FROM images WHERE status = 'active';
```

Expected: `with_dims` is at or very near `total` (any gap should correspond to images `fix_image_formats.py`
itself reported as `unreadable` — cross-check against that run's printed counters, don't just assume
100% coverage).

- [ ] **Step 4: Mark the spec done**

`docs/superpowers/specs/2026-09-14-image-dimension-capture.md`: `status: approved` → `status: done`; add
`Plan: docs/superpowers/plans/2026-09-14-image-dimension-capture.md` under the status line; add a short
"## Rollout outcome" section with each environment's before/after `with_dims`/`total` counts from Step 3.

```bash
git add docs/superpowers/specs/2026-09-14-image-dimension-capture.md
git commit -m "docs: mark image dimension capture spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (`detect_actual_format`/`FixOutcome`/`fix_image_file`/`_rename_in_place`/`_convert_webp_to_jpeg`)
  → Task 1 Steps 1-4.
- Design §2 (`repository/images.py`) → Task 1 Step 5.
- Design §3 (`apply_format_fix` unconditional persist) → Task 1 Steps 6-9.
- Design §4 (no changes needed to the two calling scripts) → correctly absent from the File Structure;
  neither script is touched anywhere in this plan.
- Testing section's exact test-by-test list → Task 1 Steps 1, 6, 10 name every test file and test
  function the spec calls out, with no gaps.
- Rollout (backfill mechanism, live-environment gate, verification) → Task 2 in full.
- Non-goals respected: no classifier logic, no migration, no rejected-status backfill, no API/frontend
  change, no change to WebP/rename logic itself beyond signature threading — this plan touches exactly
  the files the spec named.

**Placeholder scan:** none. Every code step has literal, complete content (the full rewritten
`detect_actual_format`/`FixOutcome`/`fix_image_file`/`_rename_in_place`/`_convert_webp_to_jpeg` in Task 1
Step 3, the full rewritten `apply_format_fix` in Task 1 Step 8, every test addition named to its exact
existing test function and insertion point) — no "similar to X," no "add appropriate assertions."

**Type consistency:** `update_dimensions(image_id, width: int, height: int)`'s signature and argument
order are identical everywhere they appear — the repository definition (Task 1 Step 5), the service call
site (Task 1 Step 8), and every mocked test assertion (Task 1 Step 6, all four
`assert_awaited_once_with("img-1", 4, 4)` calls use the same positional order). `FixOutcome`'s new
`width`/`height` fields are threaded identically through all four of `fix_image_file`'s exit points and
both helper functions' signatures — no path returns a `FixOutcome` missing them when the file was
actually readable.
