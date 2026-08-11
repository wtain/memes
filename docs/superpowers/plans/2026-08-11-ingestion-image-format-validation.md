# Ingestion Image Format Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect images whose file extension doesn't match their real content (and WebP
content specifically, which several libraries in this pipeline can't consume), fix them
automatically, and apply this both at ingestion time (before the expensive
embeddings/OCR/description pipeline touches a new image) and retroactively over the
existing active corpus.

**Architecture:** One pure-logic helper module (`batch/utils/image_format_fix.py`) detects
the real format via Pillow and either renames a mislabeled file in place or converts WebP
content to JPEG (moving the original into `converted_originals/` as an audit trail). Two
thin scripts call it: `batch/ingest_validate_formats.py` (new ingestion Stage 1.5, over a
batch's `pending` images) and `batch/fix_image_formats.py` (standalone retroactive
maintenance batch, default `--status active`). Both update the *same* `Image` row in place
(filename, and content_hash only when bytes actually changed) — no new rows, so existing
embeddings/OCR/tags stay valid.

**Tech Stack:** Python 3.11, Pillow (already a pinned dependency, `pillow==12.0.0`),
SQLAlchemy async ORM, pytest / pytest-asyncio.

## Global Constraints

- No database migration is needed — every column this feature touches (`images.filename`,
  `images.content_hash`, `image_extras.flagged`, `image_extras.remarks`) already exists.
- Both new scripts must be safe to re-run at any time — an already-fixed image is a no-op
  on a subsequent pass (spec: "Idempotency").
- A file Pillow can't identify at all is flagged via `ImageExtras.flagged = True` +
  `remarks`, never auto-modified or deleted (spec: "Non-goals").
- Only WebP content is force-converted; every other real format is left alone except for a
  possible extension rename (spec: "Non-goals").
- `converted_originals/` lives at `<BASE_PATH>/converted_originals/`, untracked by the DB,
  same role as the existing `duplicates/`, `rejected/`, `excluded/` audit folders (spec:
  "Error handling / edge cases").
- Any change touching `repository/images.py` requires running the **whole**
  `tests/integration/` root before merging, not just the new test file — see CLAUDE.md's
  "Running the right test scope" gotcha.
- Windows dev environment: file-handle-open-during-rename issues are real (see Task 2) —
  close Pillow file handles before renaming/moving the file they were opened from.

---

### Task 1: Extract `available_filename` collision helper in `safe_move.py`

`move_without_overwrite`'s numeric-suffix collision logic (`name.ext` → `name_1.ext` → ...)
currently only computes a target name derived from the *source*'s own basename. Task 2
needs the same collision logic but for a caller-supplied *desired* filename (e.g. renaming
`bar.jpg` to `bar.png`) — so extract it into a standalone function first.

**Files:**
- Modify: `batch/utils/safe_move.py`
- Test: `batch/tests/test_safe_move.py`

**Interfaces:**
- Produces: `available_filename(dest_dir: str, filename: str) -> str` — returns a filename
  guaranteed not to collide with anything already in `dest_dir`. Truncates the stem to
  leave room for a numeric suffix, then appends `_1`, `_2`, ... on collision. Does not
  create, move, or touch anything.
- `move_without_overwrite(src_path: str, dest_dir: str) -> str` keeps its existing
  signature/behavior — internally calls `available_filename` now, this is a pure
  refactor.

- [ ] **Step 1: Write the failing test for the extracted function**

Add to `batch/tests/test_safe_move.py` (below the existing imports, before
`test_moves_as_is_when_no_collision`):

```python
from batch.utils.safe_move import MAX_FILENAME_LENGTH, available_filename, move_without_overwrite


def test_available_filename_returns_as_is_when_no_collision(tmp_path):
    result = available_filename(str(tmp_path), "a.jpg")

    assert result == "a.jpg"


def test_available_filename_adds_suffix_on_collision(tmp_path):
    _write(tmp_path / "a.jpg", b"existing")

    result = available_filename(str(tmp_path), "a.jpg")

    assert result == "a_1.jpg"


def test_available_filename_does_not_touch_the_filesystem(tmp_path):
    available_filename(str(tmp_path), "a.jpg")

    assert list(tmp_path.iterdir()) == []
```

Update the existing `from batch.utils.safe_move import MAX_FILENAME_LENGTH,
move_without_overwrite` line at the top of the file to the combined import shown above
(one import line, not two).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest batch/tests/test_safe_move.py -v`
Expected: FAIL — `ImportError: cannot import name 'available_filename'`

- [ ] **Step 3: Extract the function in `safe_move.py`**

Replace the full contents of `batch/utils/safe_move.py` with:

```python
import os
import shutil

MAX_FILENAME_LENGTH = 255  # conservative common denominator across NTFS/ext4
_SUFFIX_RESERVE = 6        # room for "_" + up to 4 digits, plus a little headroom


def available_filename(dest_dir: str, filename: str) -> str:
    """Returns a filename guaranteed not to collide with anything already in dest_dir --
    does not create, move, or touch anything itself. The stem is truncated up front, before
    any collision check, to leave room for a numeric suffix -- an already-near-the-
    filesystem-limit name would otherwise become unwritable the moment a suffix is
    appended. Short names are unaffected (a no-op slice).

    Shared by move_without_overwrite (cross-directory moves) and
    batch/utils/image_format_fix.py (same-directory renames and the converted_originals
    move) so both use the same collision-avoidance rule.
    """
    stem, ext = os.path.splitext(filename)
    max_stem_length = max(1, MAX_FILENAME_LENGTH - len(ext) - _SUFFIX_RESERVE)
    stem = stem[:max_stem_length]

    candidate = f"{stem}{ext}"
    counter = 0
    while os.path.exists(os.path.join(dest_dir, candidate)):
        counter += 1
        candidate = f"{stem}_{counter}{ext}"
    return candidate


def move_without_overwrite(src_path: str, dest_dir: str) -> str:
    """Move src_path into dest_dir. If a file with the same name already exists there,
    renames with a numeric suffix (name_1.ext, name_2.ext, ...) instead of silently
    overwriting it. Returns the actual filename used at the destination -- callers that
    register this filename elsewhere (e.g. a DB row) must use the returned value, not
    the original.
    """
    candidate = available_filename(dest_dir, os.path.basename(src_path))
    shutil.move(src_path, os.path.join(dest_dir, candidate))
    return candidate
```

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `pytest batch/tests/test_safe_move.py -v`
Expected: PASS — all 8 tests (5 pre-existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add batch/utils/safe_move.py batch/tests/test_safe_move.py
git commit -m "refactor: extract available_filename collision helper from move_without_overwrite"
```

---

### Task 2: `batch/utils/image_format_fix.py` — detection + fix logic

The pure-logic core both scripts call. No DB access, no `Storage`/`repository` imports.

**Files:**
- Create: `batch/utils/image_format_fix.py`
- Test: `batch/tests/test_image_format_fix.py`

**Interfaces:**
- Consumes: `available_filename(dest_dir, filename) -> str` from Task 1;
  `sha256_file(path) -> str` from `batch/utils/file_hash.py` (existing).
- Produces:
  - `detect_actual_format(path: str) -> str | None` — canonical extension (`.jpg`, `.png`,
    `.webp`, `.gif`, `.bmp`, `.tiff`) or `None` if unreadable.
  - `FixOutcome` dataclass: `changed: bool`, `unreadable: bool = False`,
    `new_filename: str | None = None`, `new_content_hash: str | None = None`.
  - `fix_image_file(base_path: str, filename: str) -> FixOutcome` — `filename` must
    already exist directly under `base_path`. Tasks 5 and 6 call this per image.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_image_format_fix.py`:

```python
"""
Unit tests for batch/utils/image_format_fix.py -- real Pillow-generated fixture images
throughout (no static binary fixtures), matching test_safe_move.py's real-filesystem,
no-mocking style. No DB.
"""
import os

from PIL import Image as PILImage

from batch.utils.image_format_fix import (
    CONVERTED_ORIGINALS_DIRNAME,
    detect_actual_format,
    fix_image_file,
)


def _save(base_path, filename: str, pillow_format: str, mode: str = "RGB", color=(255, 0, 0)):
    path = os.path.join(str(base_path), filename)
    size = (4, 4)
    img = PILImage.new(mode, size, color)
    img.save(path, pillow_format)
    return path


# --------------------------------------------------------------------------
# detect_actual_format
# --------------------------------------------------------------------------

def test_detects_real_jpeg(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")

    assert detect_actual_format(str(tmp_path / "a.jpg")) == ".jpg"


def test_detects_png_mislabeled_as_jpg(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")

    assert detect_actual_format(str(tmp_path / "a.jpg")) == ".png"


def test_detects_webp(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")

    assert detect_actual_format(str(tmp_path / "a.webp")) == ".webp"


def test_returns_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"this is not image data")

    assert detect_actual_format(str(path)) is None


# --------------------------------------------------------------------------
# fix_image_file -- no-op / rename
# --------------------------------------------------------------------------

def test_noop_when_extension_already_matches(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")

    outcome = fix_image_file(str(tmp_path), "a.jpg")

    assert outcome.changed is False
    assert outcome.unreadable is False
    assert (tmp_path / "a.jpg").exists()


def test_flags_unreadable_file_without_changing_it(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"garbage")

    outcome = fix_image_file(str(tmp_path), "broken.jpg")

    assert outcome.unreadable is True
    assert outcome.changed is False
    assert path.exists()  # untouched


def test_renames_mislabeled_non_webp_file(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")

    outcome = fix_image_file(str(tmp_path), "a.jpg")

    assert outcome.changed is True
    assert outcome.new_filename == "a.png"
    assert outcome.new_content_hash is None  # bytes unchanged, no hash to update
    assert (tmp_path / "a.png").exists()
    assert not (tmp_path / "a.jpg").exists()


def test_rename_avoids_collision_with_existing_file(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")   # will want to become a.png
    _save(tmp_path, "a.png", "PNG")   # already occupies that name

    outcome = fix_image_file(str(tmp_path), "a.jpg")

    assert outcome.new_filename == "a_1.png"
    assert (tmp_path / "a_1.png").exists()
    assert (tmp_path / "a.png").exists()  # the pre-existing one, untouched


# --------------------------------------------------------------------------
# fix_image_file -- webp conversion
# --------------------------------------------------------------------------

def test_converts_opaque_webp_to_jpeg(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")

    outcome = fix_image_file(str(tmp_path), "a.webp")

    assert outcome.changed is True
    assert outcome.new_filename == "a.jpg"
    assert outcome.new_content_hash is not None
    assert (tmp_path / "a.jpg").exists()
    with PILImage.open(tmp_path / "a.jpg") as img:
        assert img.format == "JPEG"
    # original preserved in converted_originals/, not deleted
    assert (tmp_path / CONVERTED_ORIGINALS_DIRNAME / "a.webp").exists()
    assert not (tmp_path / "a.webp").exists()


def test_converts_webp_mislabeled_as_jpg_reusing_the_same_name(tmp_path):
    """The common real-world case: webp content already sitting at '<stem>.jpg'. Moving
    the original out of the way first must free up that exact name for the new real jpg."""
    _save(tmp_path, "a.jpg", "WEBP")

    outcome = fix_image_file(str(tmp_path), "a.jpg")

    assert outcome.changed is True
    assert outcome.new_filename == "a.jpg"
    with PILImage.open(tmp_path / "a.jpg") as img:
        assert img.format == "JPEG"
    assert (tmp_path / CONVERTED_ORIGINALS_DIRNAME / "a.jpg").exists()


def test_flattens_transparent_webp_onto_white_background(tmp_path):
    _save(tmp_path, "a.webp", "WEBP", mode="RGBA", color=(0, 0, 255, 128))

    outcome = fix_image_file(str(tmp_path), "a.webp")

    assert outcome.changed is True
    with PILImage.open(tmp_path / "a.jpg") as img:
        assert img.mode == "RGB"  # JPEG has no alpha channel


def test_convert_avoids_collision_in_both_target_directories(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")
    _save(tmp_path, "a.jpg", "JPEG")  # unrelated file already occupying the desired name
    converted_dir = tmp_path / CONVERTED_ORIGINALS_DIRNAME
    converted_dir.mkdir()
    (converted_dir / "a.webp").write_bytes(b"already here")  # unrelated collision too

    outcome = fix_image_file(str(tmp_path), "a.webp")

    assert outcome.new_filename == "a_1.jpg"
    assert (tmp_path / "a_1.jpg").exists()
    assert (tmp_path / "a.jpg").exists()  # unrelated file, untouched
    assert (converted_dir / "a_1.webp").exists()
    assert (converted_dir / "a.webp").read_bytes() == b"already here"  # untouched
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest batch/tests/test_image_format_fix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.utils.image_format_fix'`

- [ ] **Step 3: Implement `batch/utils/image_format_fix.py`**

```python
"""
Detects images whose file extension doesn't match their actual content, and fixes them:
a mislabeled-but-otherwise-fine format gets renamed in place; WebP content (regardless of
its current extension) gets converted to JPEG, since several libraries in this pipeline
can't consume WebP at all (confirmed: Ollama's vision backend used by
build_image_descriptions.py fails with "Failed to load image or audio file" -- see
CLAUDE.md's known gotchas). Pure file-level logic, no DB access -- both
batch/ingest_validate_formats.py (new ingestion pending images) and
batch/fix_image_formats.py (retroactive maintenance over the existing corpus) call
fix_image_file() per image and persist whatever it reports changed.

See docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.
"""
import os
from dataclasses import dataclass

from PIL import Image as PILImage

from batch.utils.file_hash import sha256_file
from batch.utils.safe_move import available_filename

FORMAT_TO_EXTENSION = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}

JPEG_QUALITY = 95
CONVERTED_ORIGINALS_DIRNAME = "converted_originals"


def detect_actual_format(path: str) -> str | None:
    """Returns the canonical extension (e.g. ".jpg") for the file's real content, or None
    if Pillow can't identify it at all (corrupt/truncated/unsupported format)."""
    try:
        with PILImage.open(path) as img:
            fmt = img.format
    except Exception:
        return None
    return FORMAT_TO_EXTENSION.get(fmt)


@dataclass
class FixOutcome:
    changed: bool
    unreadable: bool = False
    new_filename: str | None = None
    new_content_hash: str | None = None


def fix_image_file(base_path: str, filename: str) -> FixOutcome:
    """filename must already exist directly under base_path. See module docstring for the
    three possible outcomes (unreadable / renamed / converted / no-op)."""
    path = os.path.join(base_path, filename)
    actual_ext = detect_actual_format(path)

    if actual_ext is None:
        return FixOutcome(changed=False, unreadable=True)

    if actual_ext == ".webp":
        return _convert_webp_to_jpeg(base_path, filename, path)

    current_ext = os.path.splitext(filename)[1].lower()
    if current_ext != actual_ext:
        return _rename_in_place(base_path, filename, actual_ext)

    return FixOutcome(changed=False)


def _rename_in_place(base_path: str, filename: str, actual_ext: str) -> FixOutcome:
    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}{actual_ext}")
    os.rename(os.path.join(base_path, filename), os.path.join(base_path, final_name))
    return FixOutcome(changed=True, new_filename=final_name)


def _convert_webp_to_jpeg(base_path: str, filename: str, path: str) -> FixOutcome:
    with PILImage.open(path) as img:
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flattened = PILImage.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[3])
        else:
            flattened = img.convert("RGB")
        # img.convert() fully materializes pixel data (it's not lazy like Image.open()),
        # so it's safe to let this `with` block close the source file handle here -- the
        # rename below must happen only after that handle is closed, since renaming a file
        # with an open handle can fail on Windows.

    converted_originals_dir = os.path.join(base_path, CONVERTED_ORIGINALS_DIRNAME)
    os.makedirs(converted_originals_dir, exist_ok=True)
    # Move the original out of the way *before* computing the new jpg's name: the common
    # case is webp content already sitting at "<stem>.jpg", so the desired output name
    # equals the current filename -- freeing it first lets that slot be reused directly
    # instead of spuriously colliding with itself.
    original_dest_name = available_filename(converted_originals_dir, filename)
    os.rename(path, os.path.join(converted_originals_dir, original_dest_name))

    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}.jpg")
    final_path = os.path.join(base_path, final_name)
    flattened.save(final_path, "JPEG", quality=JPEG_QUALITY)

    new_content_hash = sha256_file(final_path)
    return FixOutcome(changed=True, new_filename=final_name, new_content_hash=new_content_hash)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest batch/tests/test_image_format_fix.py -v`
Expected: PASS — all 12 tests

- [ ] **Step 5: Commit**

```bash
git add batch/utils/image_format_fix.py batch/tests/test_image_format_fix.py
git commit -m "feat: add image format detection and fix helper (rename mislabeled files, convert webp to jpeg)"
```

---

### Task 3: `ImagesRepository.update_filename_and_hash`

**Files:**
- Modify: `repository/images.py`
- Test: `tests/integration/test_images_repository.py`

**Interfaces:**
- Produces: `ImagesRepository.update_filename_and_hash(self, image_id, filename: str,
  content_hash: str | None = None) -> None` — updates `filename` unconditionally;
  updates `content_hash` only when a value is given (so a rename-only fix, which passes
  `content_hash=None`, doesn't clobber the existing hash).

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_images_repository.py` (below the existing imports — `Image`
is already imported there):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_update_filename_and_hash_updates_filename_only_when_hash_omitted(db_session):
    image = Image(filename="a.jpg", content_hash="original-hash")
    db_session.add(image)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    await images_repo.update_filename_and_hash(image.id, "a.png")
    await db_session.flush()

    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"
    assert refreshed.content_hash == "original-hash"  # untouched


@pytest.mark.asyncio(loop_scope="session")
async def test_update_filename_and_hash_updates_both_when_hash_given(db_session):
    image = Image(filename="a.webp", content_hash="original-hash")
    db_session.add(image)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    await images_repo.update_filename_and_hash(image.id, "a.jpg", content_hash="new-hash")
    await db_session.flush()

    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash == "new-hash"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: FAIL — `AttributeError: 'ImagesRepository' object has no attribute 'update_filename_and_hash'`

- [ ] **Step 3: Add the method to `repository/images.py`**

Add directly below the existing `update_content_hash` method:

```python
    async def update_filename_and_hash(self, image_id, filename: str, content_hash: str | None = None) -> None:
        values = {"filename": filename}
        if content_hash is not None:
            values["content_hash"] = content_hash
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(**values)
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: PASS — all tests in the file

- [ ] **Step 5: Commit**

```bash
git add repository/images.py tests/integration/test_images_repository.py
git commit -m "feat: add ImagesRepository.update_filename_and_hash"
```

---

### Task 4: `ImageExtrasRepository.set_flagged` gains an optional `remarks`

**Files:**
- Modify: `repository/image_extras.py`
- Test: `tests/integration/test_image_extras_repository.py`

**Interfaces:**
- Produces: `ImageExtrasRepository.set_flagged(self, image_id, flagged: bool, remarks: str
  | None = None) -> None` — backward compatible: omitting `remarks` behaves exactly as
  before (remarks column untouched on update, left at its default on insert).

- [ ] **Step 1: Write the failing test**

Add `from sqlalchemy import select` and `from Storage.models import Image, ImageExtras` to
the top of `tests/integration/test_image_extras_repository.py` (replacing the existing
`from Storage.models import Image` line), then add these two tests:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_set_flagged_with_remarks_stores_both(db_session):
    image = Image(filename=f"remarked-{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageExtrasRepository(db_session)
    await repo.set_flagged(image.id, True, remarks="unreadable during format validation")

    row = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert row.flagged is True
    assert row.remarks == "unreadable during format validation"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_flagged_without_remarks_leaves_remarks_untouched(db_session):
    image = Image(filename=f"norm-{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageExtrasRepository(db_session)
    await repo.set_flagged(image.id, True, remarks="first note")
    await repo.set_flagged(image.id, False)  # no remarks passed this time

    row = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert row.flagged is False
    assert row.remarks == "first note"  # untouched by the remarks-less call
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_extras_repository.py -v`
Expected: FAIL — `TypeError: set_flagged() got an unexpected keyword argument 'remarks'`

- [ ] **Step 3: Update `repository/image_extras.py`**

Replace the `set_flagged` method with:

```python
    async def set_flagged(self, image_id, flagged: bool, remarks: str | None = None) -> None:
        values = {"image_id": image_id, "flagged": flagged}
        update_set = {"flagged": flagged}
        if remarks is not None:
            values["remarks"] = remarks
            update_set["remarks"] = remarks
        stmt = (
            insert(ImageExtras)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_=update_set,
            )
        )
        await self.session.execute(stmt)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_extras_repository.py -v`
Expected: PASS — all tests in the file, including the two pre-existing ones (unchanged
behavior for calls without `remarks`)

- [ ] **Step 5: Commit**

```bash
git add repository/image_extras.py tests/integration/test_image_extras_repository.py
git commit -m "feat: allow ImageExtrasRepository.set_flagged to record an optional remarks note"
```

---

### Task 5: `batch/ingest_validate_formats.py` — ingestion Stage 1.5

**Files:**
- Create: `batch/ingest_validate_formats.py`
- Test: `tests/integration/test_ingest_validate_formats.py`

**Interfaces:**
- Consumes: `fix_image_file(base_path, filename) -> FixOutcome` (Task 2);
  `ImagesRepository.update_filename_and_hash` (Task 3);
  `ImageExtrasRepository.set_flagged(image_id, flagged, remarks=None)` (Task 4);
  `BatchRunRepository.get_active_run(kind) -> BatchRun | None`,
  `.update_stats(run_id, **kwargs)`, `.set_stage(run_id, stage)` (existing);
  `accumulate_stats(existing: dict, new: dict) -> dict` from `batch/ingest_hash_dedup.py`
  (existing, reused here for DRY — same "safe to re-run, stats add up" requirement Stage 1
  already has).
- Produces: `async def run(session, base_path: str, batch_id) -> SimpleMetricsListener` —
  the function Task 5's tests call directly. `main(env: str | None) -> None` is the CLI
  entry point, following `batch/ingest_hash_dedup.py`'s exact shape (loads env inside
  `main()`, not in the `__main__` block).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_ingest_validate_formats.py`:

```python
"""
Integration tests for batch/ingest_validate_formats.py (ingestion Stage 1.5: format
validation/fix for a batch's pending images).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.ingest_validate_formats import run
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from Storage.models import Image, ImageExtras


def _save(base_path, filename: str, pillow_format: str) -> None:
    PILImage.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(str(base_path), filename), pillow_format)


@pytest.mark.asyncio(loop_scope="session")
async def test_renames_mislabeled_pending_image_and_updates_filename(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"renamed": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"
    assert refreshed.content_hash == "orig"  # unchanged -- bytes weren't touched
    assert (tmp_path / "a.png").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_converts_webp_pending_image_and_updates_hash(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "WEBP")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"converted": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash != "orig"
    with PILImage.open(tmp_path / "a.jpg") as img:
        assert img.format == "JPEG"


@pytest.mark.asyncio(loop_scope="session")
async def test_flags_unreadable_pending_image_and_leaves_it_alone(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="broken.jpg", status="pending", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    (tmp_path / "broken.jpg").write_bytes(b"not an image")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"unreadable": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "broken.jpg"  # unchanged
    extras = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert extras.flagged is True
    assert extras.remarks == "unreadable during format validation"


@pytest.mark.asyncio(loop_scope="session")
async def test_noop_image_is_counted_and_untouched(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "JPEG")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"no_op": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash == "orig"


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_pending_images_from_a_different_batch(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    other_batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    other_image = Image(filename="a.jpg", status="pending", ingestion_batch_id=other_batch_id)
    db_session.add(other_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_active_images(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    active_image = Image(filename="a.jpg", status="active")
    db_session.add(active_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_validate_formats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.ingest_validate_formats'`

- [ ] **Step 3: Implement `batch/ingest_validate_formats.py`**

```python
"""
Ingestion Stage 1.5: validate/fix image format vs extension mismatches (and convert WebP
to JPEG) for a batch's pending images, before embeddings/OCR run on them. See
docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.

Runs after ingest_hash_dedup.py, before build_image_embeddings.py --status pending. Joins
the same active ingestion run Stage 1 used -- this is an additional stage of that run, not
an independent batch -- exactly like ingest_find_duplicates.py does for Tier A/B.

Safe to re-run at any point: an already-fixed image is a no-op (its extension already
matches its real, non-webp format) on a later pass. Stats accumulate across invocations
the same way Stage 1's do, via the same accumulate_stats helper.
"""
import argparse
import asyncio

from sqlalchemy import select

from batch.ingest_hash_dedup import accumulate_stats
from batch.utils.image_format_fix import fix_image_file
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image

STAGE = "format_validation"


async def get_pending_batch_images(session, batch_id) -> list:
    """Returns [(image_id, filename), ...] for this batch's still-pending images."""
    result = await session.execute(
        select(Image.id, Image.filename).where(
            Image.status == "pending", Image.ingestion_batch_id == batch_id,
        )
    )
    return result.all()


async def run(session, base_path: str, batch_id) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()
    images_repo = ImagesRepository(session)
    extras_repo = ImageExtrasRepository(session)

    for image_id, filename in await get_pending_batch_images(session, batch_id):
        outcome = fix_image_file(base_path, filename)

        if outcome.unreadable:
            await extras_repo.set_flagged(image_id, True, remarks="unreadable during format validation")
            metrics.increment("unreadable")
            continue

        if not outcome.changed:
            metrics.increment("no_op")
            continue

        await images_repo.update_filename_and_hash(
            image_id, outcome.new_filename, content_hash=outcome.new_content_hash,
        )
        metrics.increment("converted" if outcome.new_content_hash else "renamed")

    return metrics


async def main(env: str | None) -> None:
    load_env(env)
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError(
                "No ingestion run is currently in progress -- run ingest_hash_dedup.py first."
            )

        metrics = await run(session, base_path, active_run.run_id)
        existing_stats = active_run.stats or {}
        await runs_repo.update_stats(active_run.run_id, **accumulate_stats(existing_stats, metrics.counters_dict()))
        await runs_repo.set_stage(active_run.run_id, STAGE)
        await session.commit()

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.env))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_validate_formats.py -v`
Expected: PASS — all 6 tests

- [ ] **Step 5: Commit**

```bash
git add batch/ingest_validate_formats.py tests/integration/test_ingest_validate_formats.py
git commit -m "feat: add ingestion Stage 1.5 -- validate/fix image formats before embeddings/OCR"
```

---

### Task 6: `batch/fix_image_formats.py` — retroactive maintenance batch

**Files:**
- Create: `batch/fix_image_formats.py`
- Test: `tests/integration/test_fix_image_formats.py`

**Interfaces:**
- Consumes: same as Task 5's `fix_image_file`/repository calls; `tracked_run(kind,
  trigger)` and `finish_existing_run(run_id)` from `batch/run_tracking.py` (existing).
- Produces: `async def run(session, base_path: str, status: str) -> SimpleMetricsListener`
  — the function Task 6's tests call directly. `main(status: str = "active", trigger: str
  = "manual", run_id=None) -> None` follows `batch/move_flagged.py`'s exact shape (no `env`
  param on `main()` itself — `load_env` is called in the `__main__` block instead).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_fix_image_formats.py`:

```python
"""
Integration tests for batch/fix_image_formats.py (retroactive format-fix maintenance
batch).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.fix_image_formats import run
from Storage.models import Image, ImageExtras


def _save(base_path, filename: str, pillow_format: str) -> None:
    PILImage.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(str(base_path), filename), pillow_format)


@pytest.mark.asyncio(loop_scope="session")
async def test_fixes_active_images_by_default(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", content_hash="orig")
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {"renamed": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_pending_images_when_status_is_active(tmp_path, db_session):
    pending_image = Image(filename="a.jpg", status="pending")
    db_session.add(pending_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_status_flag_can_target_pending_images(tmp_path, db_session):
    pending_image = Image(filename="a.jpg", status="pending", content_hash="orig")
    db_session.add(pending_image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "WEBP")

    metrics = await run(db_session, str(tmp_path), "pending")

    assert metrics.counters_dict() == {"converted": 1}
    refreshed = await db_session.get(Image, pending_image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash != "orig"


@pytest.mark.asyncio(loop_scope="session")
async def test_flags_unreadable_active_image(tmp_path, db_session):
    image = Image(filename="broken.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    (tmp_path / "broken.jpg").write_bytes(b"not an image")

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {"unreadable": 1}
    extras = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert extras.flagged is True
    assert extras.remarks == "unreadable during format validation"


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_is_a_noop_on_already_fixed_images(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", content_hash="orig")
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    first = await run(db_session, str(tmp_path), "active")
    assert first.counters_dict() == {"renamed": 1}

    second = await run(db_session, str(tmp_path), "active")
    assert second.counters_dict() == {"no_op": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_fix_image_formats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.fix_image_formats'`

- [ ] **Step 3: Implement `batch/fix_image_formats.py`**

```python
"""
Retroactive maintenance batch: applies the same format validation/fix logic as
batch/ingest_validate_formats.py (ingestion Stage 1.5) to images that were already
ingested before that check existed. See
docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.

Safe to re-run at any time -- already-fixed images are no-ops on a subsequent pass.
Defaults to --status active (the existing corpus); pass --status pending to also cover an
old in-flight ingestion batch that predates this feature and never went through Stage 1.5.
"""
import argparse
import asyncio
import uuid

from sqlalchemy import select

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.image_format_fix import fix_image_file
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def get_images_by_status(session, status: str) -> list:
    result = await session.execute(
        select(Image.id, Image.filename).where(Image.status == status)
    )
    return result.all()


async def run(session, base_path: str, status: str) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()
    images_repo = ImagesRepository(session)
    extras_repo = ImageExtrasRepository(session)

    for image_id, filename in await get_images_by_status(session, status):
        outcome = fix_image_file(base_path, filename)

        if outcome.unreadable:
            await extras_repo.set_flagged(image_id, True, remarks="unreadable during format validation")
            metrics.increment("unreadable")
            continue

        if not outcome.changed:
            metrics.increment("no_op")
            continue

        await images_repo.update_filename_and_hash(
            image_id, outcome.new_filename, content_hash=outcome.new_content_hash,
        )
        metrics.increment("converted" if outcome.new_content_hash else "renamed")

    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    base_path = settings.BASE_PATH

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, status)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()
    else:
        async with tracked_run(kind="fix_image_formats", trigger=trigger) as run_id:
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, status)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_fix_image_formats.py -v`
Expected: PASS — all 5 tests

- [ ] **Step 5: Commit**

```bash
git add batch/fix_image_formats.py tests/integration/test_fix_image_formats.py
git commit -m "feat: add fix_image_formats maintenance batch for the existing corpus"
```

---

### Task 7: Docs + full verification pass

**Files:**
- Modify: `docs/runbooks/ingestion-pipeline.md`
- Modify: `CLAUDE.md`

**Interfaces:** None — documentation only, plus running the full test suites this change
touches.

- [ ] **Step 1: Update the runbook's TL;DR command block**

In `docs/runbooks/ingestion-pipeline.md`, the TL;DR section (around line 18-27), insert a
new line after `ingest_hash_dedup` and before `build_image_embeddings`:

```powershell
python -m batch.ingest_hash_dedup --env <env>
python -m batch.ingest_validate_formats --env <env>
python -m batch.build_image_embeddings --env <env> --status pending --incremental
```

- [ ] **Step 2: Update the runbook's numbered "Running a batch" walkthrough**

In the same file, renumber steps 3 onward (currently "Embeddings for the new pending
images") to make room, and insert a new step 3 after the existing step 2
(`ingest_hash_dedup`):

```markdown
3. **Validate/fix image formats:**
   ```powershell
   python -m batch.ingest_validate_formats --env <env>
   ```
   Fixes any file whose extension doesn't match its real content (renamed in place) and
   converts any WebP content to JPEG regardless of its current extension (original moved
   to `<BASE_PATH>\converted_originals\` for audit). An unreadable file is flagged via
   `ImageExtras` for human review instead of being touched. Safe to re-run.
```

Renumber the subsequent steps (old 3 → 4, old 4 → 5, ..., old 11 → 12) so the walkthrough
stays sequential.

- [ ] **Step 3: Update CLAUDE.md's ingestion run-order block**

In `CLAUDE.md`'s "Batch pipeline (execution order)" section, find the `# Ingestion` comment
block and its `ingest_hash_dedup` entry. Insert directly after the `ingest_hash_dedup`
entry's description (before the `build_image_embeddings --status pending --incremental`
line that follows it in both the run-order list and the prose describing what needs
re-running after a re-join):

```
ingest_validate_formats     → Stage 1.5: renames files whose extension doesn't match their
                               real content, and converts any WebP content to JPEG
                               (original moved to converted_originals/ for audit) --
                               several downstream libraries (e.g. Ollama's vision backend)
                               can't consume WebP. Updates the same Image row's
                               filename/content_hash in place; unreadable files are flagged
                               via ImageExtras instead of touched. See
                               docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.
```

Also add `ingest_validate_formats.py` to the re-run instructions in the same section's
"steps 3-8" callout (the paragraph starting "Don't skip the embeddings step") so a re-join
mid-batch is documented as needing this stage re-run too, consistent with how embeddings/
OCR/find_duplicates are already called out there.

- [ ] **Step 4: Add `fix_image_formats` to CLAUDE.md's maintenance list**

In the same "Batch pipeline" section's `# Maintenance (run as needed)` comment block, add:

```
fix_image_formats           → retroactively applies ingest_validate_formats' fix logic to
                               the existing corpus (default --status active); safe to
                               re-run, already-fixed images are no-ops.
```

- [ ] **Step 5: Run the full affected test roots**

Run: `pytest batch/tests/ -v`
Expected: PASS — all tests, including the new `test_safe_move.py` additions and
`test_image_format_fix.py`

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: PASS — the **entire** root, not just this feature's new files (CLAUDE.md's
shared-code testing gotcha: this change touches `repository/images.py`, used by many
callers)

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/ingestion-pipeline.md CLAUDE.md
git commit -m "docs: document the new ingestion Stage 1.5 and fix_image_formats maintenance batch"
```
