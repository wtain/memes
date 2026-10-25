# R3a: Cut and copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run-tracked batch job `create_recut_cuts` turns each `detected` recut proposal into a cut file, a `cut_proposed` image, copied derived data (groups 1 and 3) and a `proposed` metric event, one transaction per cut.

**Architecture:** Pixel work lives in `batch/utils/recut_cut.py` (crop, atomic write, bbox shift; no DB). Database copying lives in `repository/cut_copier.py` (`CutCopier`, generic `INSERT ... SELECT` built from the model columns, so a new column is copied automatically). `ImageCutsRepository` gains the detected-proposal queries. The job in `batch/create_recut_cuts.py` ties them together with one `begin_nested()` savepoint and one commit per cut.

**Tech Stack:** Python 3.11, SQLAlchemy async, Alembic, Pillow, pytest (integration root with `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`).

**Spec:** docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md

## Global Constraints

- The cut keeps the source format: JPEG at `recut.jpeg_quality` (95) with the source's subsampling, PNG as PNG (spec decision 1).
- Every `detected` proposal is cut, highest confidence first, optional `--limit` (decision 2).
- One proposal is one transaction; the file is written to a temporary name and renamed (decision 3, NFR-2).
- Group 1 rows copied with `stale = false`, group 3 rows with `stale = true`; group 2 and the "none" tables are not copied (decision 4).
- Failure reasons are exactly `source_missing`, `source_changed`, `unreadable`, `write_error`; 20 consecutive failures stop the run.
- The `stale` migration rule (NFR-4) applies to the new column too: nullable, no default, no table rewrite.
- Repositories and the copier never commit; the job commits.
- Test roots run separately; integration tests need the `DATABASE_URL` above. On Windows set `PYTHONIOENCODING=utf-8` for scripts using `ProgressTracker`.
- Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A source whose `source_content_hash` is NULL (legacy rows): the hash check is skipped, the cut is still made (Task 2 test).
- A malformed or `NULL` OCR `bbox`: the OCR row is kept with `bbox = None` rather than failing the whole cut (Task 2 and Task 4 tests).
- A crash or DB error after the file is written: the file is deleted and the proposal ends `failed` / `write_error` with no `images` row (Task 5 test).
- A box that does not fit the source file's real dimensions (the stored width/height can drift from the file): `source_changed`, no cut (Task 2 test).
- A re-run must not re-cut or double-count the event (Task 5 test).

## File Structure

- Create `Storage/alembic/versions/d7a3b9e2c451_add_image_cuts_failure_reason.py` - nullable column.
- Modify `Storage/models.py` - `ImageCut.failure_reason`.
- Modify `environments/settings.yaml`, `batch/tests/test_recut_settings.py` - `recut.jpeg_quality`.
- Create `batch/utils/recut_cut.py` - `CutError`, `CutFile`, `crop_to_file`, `shift_bbox`.
- Create `batch/tests/test_recut_cut.py`.
- Modify `repository/image_cuts.py` - detected-proposal queries, `mark_failed`, `mark_proposed`.
- Modify `tests/integration/test_image_cuts_repository.py`.
- Create `repository/cut_copier.py` - `CutCopier`, `COPIED_TABLES`, `NOT_COPIED_TABLES`.
- Create `tests/integration/test_cut_copier.py`, `tests/docs/test_cut_copier_inventory.py`.
- Create `batch/create_recut_cuts.py`, `tests/integration/test_create_recut_cuts.py`, `batch/tests/test_create_recut_cuts_main.py`.
- Modify `environments/batch_registry.yaml`, `docs/batches/inventory.md`, `CLAUDE.md`, `docs/image-derived-data.md` (only if wording needs it), the R3a spec (status, feedback correction), the tracker.

---

### Task 1: Schema column and setting

**Files:**
- Create: `Storage/alembic/versions/d7a3b9e2c451_add_image_cuts_failure_reason.py`
- Modify: `Storage/models.py` (class `ImageCut`)
- Modify: `environments/settings.yaml` (group `recut`), `batch/tests/test_recut_settings.py`
- Test: `tests/integration/test_image_cuts_repository.py`, `batch/tests/test_recut_settings.py`

**Interfaces:**
- Produces: `ImageCut.failure_reason` (`String(40)`, nullable); `settings.RECUT.JPEG_QUALITY` (int 95).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_image_cuts_repository.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_a_cut_can_store_a_failure_reason(db_session):
    img = await _image(db_session)
    await _cut(db_session, img, state=CutState.FAILED)
    row = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalar_one()
    assert row.failure_reason is None
    row.failure_reason = "source_missing"
    await db_session.flush()
    assert row.failure_reason == "source_missing"
```

In `batch/tests/test_recut_settings.py` change `KEYS` to include `"jpeg_quality"` and add:

```python
def test_jpeg_quality_is_a_valid_quality():
    assert 1 <= _group()["jpeg_quality"] <= 100
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_image_cuts_repository.py -k failure_reason -v` and `.venv311/Scripts/python -m pytest batch/tests/test_recut_settings.py -v`
Expected: FAIL (`failure_reason` is not a mapped attribute; the settings key set differs).

- [ ] **Step 3: Implement**

In `Storage/models.py`, in `ImageCut` after `decided_at`:

```python
    # Why a detected proposal could not be cut (R3a): source_missing, source_changed, unreadable, write_error.
    failure_reason = Column(String(40), nullable=True)
```

In `environments/settings.yaml` under `recut:` after `commit_interval: 200`:

```yaml
  jpeg_quality: 95              # quality of a cut saved from a JPEG source (R3a); the source's subsampling is kept
```

Migration:

```python
"""add image_cuts.failure_reason

Revision ID: d7a3b9e2c451
Revises: c5e2a9d7b140
Create Date: 2026-10-26 10:00:00.000000

Epic screenshot-recut, project R3a. Nullable with no default, so adding it does not rewrite the table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd7a3b9e2c451'
down_revision: Union[str, Sequence[str], None] = 'c5e2a9d7b140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('image_cuts', sa.Column('failure_reason', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('image_cuts', 'failure_reason')
```

- [ ] **Step 4: Run to verify they pass**

Run the two commands from Step 2 again, plus `cd Storage && ../.venv311/Scripts/alembic heads` (do NOT run `upgrade`; live databases are rolled out only with the user's go-ahead).
Expected: PASS; exactly one head, `d7a3b9e2c451`.

- [ ] **Step 5: Commit**

```bash
git add Storage/alembic/versions/d7a3b9e2c451_add_image_cuts_failure_reason.py Storage/models.py environments/settings.yaml batch/tests/test_recut_settings.py tests/integration/test_image_cuts_repository.py
git commit -m "feat(recut): image_cuts.failure_reason column and recut.jpeg_quality setting"
```

---

### Task 2: Pixel work (`batch/utils/recut_cut.py`)

**Files:**
- Create: `batch/utils/recut_cut.py`
- Test: `batch/tests/test_recut_cut.py`

**Interfaces:**
- Produces:
  - `class CutError(Exception)` with `.reason: str` (one of `SOURCE_MISSING`, `SOURCE_CHANGED`, `UNREADABLE`, `WRITE_ERROR`) and `.detail: str`.
  - `@dataclass(frozen=True) class CutFile: width: int; height: int; content_hash: str`.
  - `crop_to_file(src: str, dst: str, box: tuple[int, int, int, int], expected_hash: str | None, jpeg_quality: int) -> CutFile` where `box = (x, y, w, h)` in source pixels.
  - `shift_bbox(bbox, crop: tuple[int, int, int, int]) -> tuple[bool, object]` returning `(keep, new_bbox)`.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib

import numpy as np
import pytest
from PIL import Image as PILImage, JpegImagePlugin

from batch.utils.recut_cut import (SOURCE_CHANGED, SOURCE_MISSING, UNREADABLE, CutError, crop_to_file, shift_bbox)


def _noise(w=200, h=150, seed=1):
    return PILImage.fromarray(np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8))


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_png_cut_is_the_exact_pixel_crop(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "b.png"
    _noise().save(src, "PNG")
    out = crop_to_file(str(src), str(dst), (10, 20, 50, 40), _sha(src), 95)
    cut = PILImage.open(dst)
    assert cut.format == "PNG" and cut.size == (50, 40) == (out.width, out.height)
    assert np.array_equal(np.array(cut), np.array(PILImage.open(src))[20:60, 10:60])
    assert out.content_hash == _sha(dst)
    assert [p.name for p in tmp_path.iterdir()] == ["a.png", "b.png"]  # no temporary file left


@pytest.mark.parametrize("subsampling", [0, 2])
def test_jpeg_cut_stays_jpeg_and_keeps_the_subsampling(tmp_path, subsampling):
    src, dst = tmp_path / "a.jpg", tmp_path / "b.jpg"
    _noise().save(src, "JPEG", quality=90, subsampling=subsampling)
    crop_to_file(str(src), str(dst), (8, 8, 64, 64), _sha(src), 95)
    cut = PILImage.open(dst)
    assert cut.format == "JPEG" and cut.size == (64, 64)
    assert JpegImagePlugin.get_sampling(cut) == subsampling


def test_missing_source(tmp_path):
    with pytest.raises(CutError) as e:
        crop_to_file(str(tmp_path / "nope.png"), str(tmp_path / "b.png"), (0, 0, 5, 5), None, 95)
    assert e.value.reason == SOURCE_MISSING


def test_changed_source_hash(tmp_path):
    src = tmp_path / "a.png"
    _noise().save(src, "PNG")
    with pytest.raises(CutError) as e:
        crop_to_file(str(src), str(tmp_path / "b.png"), (0, 0, 5, 5), "0" * 64, 95)
    assert e.value.reason == SOURCE_CHANGED
    assert not (tmp_path / "b.png").exists()


def test_null_expected_hash_skips_the_check(tmp_path):
    src = tmp_path / "a.png"
    _noise().save(src, "PNG")
    assert crop_to_file(str(src), str(tmp_path / "b.png"), (0, 0, 5, 5), None, 95).width == 5


def test_box_outside_the_file_counts_as_a_changed_source(tmp_path):
    src = tmp_path / "a.png"
    _noise().save(src, "PNG")
    with pytest.raises(CutError) as e:
        crop_to_file(str(src), str(tmp_path / "b.png"), (150, 0, 100, 10), None, 95)
    assert e.value.reason == SOURCE_CHANGED


def test_unreadable_source(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"not an image")
    with pytest.raises(CutError) as e:
        crop_to_file(str(src), str(tmp_path / "b.png"), (0, 0, 5, 5), None, 95)
    assert e.value.reason == UNREADABLE


CROP = (10, 10, 100, 50)  # x, y, w, h


def test_polygon_inside_is_shifted():
    keep, bbox = shift_bbox([[20, 20], [40, 20], [40, 30], [20, 30]], CROP)
    assert keep and bbox == [[10, 10], [30, 10], [30, 20], [10, 20]]


def test_polygon_with_centre_outside_is_dropped():
    keep, _ = shift_bbox([[200, 20], [220, 20], [220, 30], [200, 30]], CROP)
    assert not keep


def test_polygon_straddling_the_edge_is_clipped_when_its_centre_is_inside():
    keep, bbox = shift_bbox([[0, 20], [60, 20], [60, 30], [0, 30]], CROP)  # centre x = 30, inside
    assert keep and bbox[0][0] == 0 and bbox[1] == [50, 10]


def test_rect_form_is_shifted_and_clipped():
    keep, bbox = shift_bbox([0, 20, 60, 10], CROP)
    assert keep and bbox == [0, 10, 50, 10]


def test_null_and_malformed_bbox_are_kept_with_no_bbox():
    assert shift_bbox(None, CROP) == (True, None)
    assert shift_bbox([{"x": 1}, {"x": 2}], CROP) == (True, None)
    assert shift_bbox("garbage", CROP) == (True, None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv311/Scripts/python -m pytest batch/tests/test_recut_cut.py -v`
Expected: FAIL (`ModuleNotFoundError: batch.utils.recut_cut`).

- [ ] **Step 3: Implement**

```python
"""Pixel work of the screenshot recut cut step (R3a; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md).
No database access: cropping a source file into a cut file in the same format, and moving OCR boxes into the cut's frame."""
import os
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import JpegImagePlugin

from batch.utils.file_hash import sha256_file

SOURCE_MISSING = "source_missing"
SOURCE_CHANGED = "source_changed"
UNREADABLE = "unreadable"
WRITE_ERROR = "write_error"


class CutError(Exception):
    """A proposal that cannot be cut. `reason` is stored in image_cuts.failure_reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class CutFile:
    width: int
    height: int
    content_hash: str


def _save_kwargs(im, fmt: str, jpeg_quality: int) -> dict:
    kwargs = {}
    icc = im.info.get("icc_profile")
    if fmt == "JPEG":
        kwargs["quality"] = jpeg_quality
        sampling = JpegImagePlugin.get_sampling(im)  # -1 when the source carries none (grayscale)
        if sampling != -1:
            kwargs["subsampling"] = sampling
    if icc and fmt in ("JPEG", "PNG"):
        kwargs["icc_profile"] = icc
    return kwargs


def crop_to_file(src: str, dst: str, box: tuple[int, int, int, int], expected_hash: str | None,
                 jpeg_quality: int) -> CutFile:
    """Crop `box` = (x, y, w, h) of `src` into `dst`, same format as the source. `dst` appears atomically (written to
    `dst + '.tmp'`, then renamed). A NULL `expected_hash` skips the unchanged-source check."""
    if not os.path.isfile(src):
        raise CutError(SOURCE_MISSING, src)
    try:
        if expected_hash is not None and sha256_file(src) != expected_hash:
            raise CutError(SOURCE_CHANGED, "content hash differs from the one recorded at detection")
        with PILImage.open(src) as im:
            im.load()
            fmt = im.format
            if fmt is None:
                raise CutError(UNREADABLE, "unknown format")
            x, y, w, h = box
            if x < 0 or y < 0 or x + w > im.width or y + h > im.height:
                raise CutError(SOURCE_CHANGED, f"box {box} does not fit {im.width}x{im.height}")
            cut = im.crop((x, y, x + w, y + h))
            kwargs = _save_kwargs(im, fmt, jpeg_quality)
    except CutError:
        raise
    except (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError) as e:
        raise CutError(UNREADABLE, f"{type(e).__name__}: {e}"[:200]) from e

    tmp = dst + ".tmp"
    try:
        cut.save(tmp, format=fmt, **kwargs)
        os.replace(tmp, dst)
    except (OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CutError(WRITE_ERROR, f"{type(e).__name__}: {e}"[:200]) from e
    return CutFile(width=cut.width, height=cut.height, content_hash=sha256_file(dst))


def _clip(value, low, high):
    return max(low, min(high, value))


def shift_bbox(bbox, crop: tuple[int, int, int, int]):
    """Move one OCR box into the cut's frame. `crop` = (x, y, w, h) in source pixels. Returns (keep, new_bbox): a box
    whose centre lies outside the crop is dropped; one inside is shifted and clipped to the crop. Both stored shapes are
    handled (list of [x, y] points, flat [x, y, w, h]). A NULL or unparseable box is kept as None: the text may well
    belong to the cut and the OCR refresh will redo it."""
    if bbox is None:
        return True, None
    cx, cy, cw, ch = crop
    try:
        if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x, y, w, h = bbox
            centre_x, centre_y = x + w / 2, y + h / 2
            shape = "rect"
        else:
            points = [(p[0], p[1]) for p in bbox]
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            centre_x, centre_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            shape = "poly"
            if not all(isinstance(v, (int, float)) for v in xs + ys):
                raise TypeError("non-numeric point")
    except (TypeError, ValueError, IndexError, KeyError):
        return True, None
    if not (cx <= centre_x < cx + cw and cy <= centre_y < cy + ch):
        return False, None
    if shape == "rect":
        x0, y0 = max(x, cx) - cx, max(y, cy) - cy
        x1, y1 = min(x + w, cx + cw) - cx, min(y + h, cy + ch) - cy
        return True, [x0, y0, x1 - x0, y1 - y0]
    return True, [[_clip(px - cx, 0, cw), _clip(py - cy, 0, ch)] for px, py in points]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv311/Scripts/python -m pytest batch/tests/test_recut_cut.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add batch/utils/recut_cut.py batch/tests/test_recut_cut.py
git commit -m "feat(recut): crop a source into a same-format cut file and shift OCR boxes"
```

---

### Task 3: Detected-proposal queries on `ImageCutsRepository`

**Files:**
- Modify: `repository/image_cuts.py`
- Test: `tests/integration/test_image_cuts_repository.py`

**Interfaces:**
- Consumes: `Image`, `ImageCut`, `ImageExtras`, `CutState`, `ImageStatus`.
- Produces (all on `ImageCutsRepository`):
  - `select_cuttable(corpus_ids, limit=None) -> list[Row]` with attributes `cut_id, source_image_id, filename, created_at, crop_x, crop_y, crop_w, crop_h, source_content_hash, detect_run_id`; ordered by `confidence` descending (NULLs last) then `cut_id`.
  - `count_cuttable(corpus_ids) -> int`, `count_ineligible_detected(corpus_ids) -> int`.
  - `mark_proposed(cut_id, cut_image_id) -> None`, `mark_failed(cut_id, reason) -> None`.
- "Cuttable" = a `detected` row whose source exists, is `active`, is not flagged and is not in `corpus_ids`.

- [ ] **Step 1: Write the failing tests**

Append (the file already has `_image`, `_cut` helpers; add a helper with confidence):

```python
async def _detected(s, source, confidence=0.9):
    cut = ImageCut(source_image_id=source.id, crop_x=1, crop_y=1, crop_w=10, crop_h=10, detector="margin_trim",
                   detector_version=VER, confidence=confidence, state=CutState.DETECTED)
    s.add(cut)
    await s.flush()
    return cut


async def _cuttable_ids(s, corpus=frozenset(), limit=None):
    return [r.cut_id for r in await ImageCutsRepository(s).select_cuttable(set(corpus), limit)]


@pytest.mark.asyncio(loop_scope="session")
async def test_cuttable_orders_by_confidence_then_limits(db_session):
    low = await _detected(db_session, await _image(db_session), 0.2)
    high = await _detected(db_session, await _image(db_session), 0.95)
    mid = await _detected(db_session, await _image(db_session), 0.5)
    ids = [i for i in await _cuttable_ids(db_session) if i in {low.id, high.id, mid.id}]
    assert ids == [high.id, mid.id, low.id]
    assert len(await _cuttable_ids(db_session, limit=1)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cuttable_row_carries_what_the_job_needs(db_session):
    src = await _image(db_session, content_hash="h")
    cut = await _detected(db_session, src)
    (row,) = [r for r in await ImageCutsRepository(db_session).select_cuttable(set()) if r.cut_id == cut.id]
    assert row.source_image_id == src.id and row.filename == src.filename and row.source_content_hash is None
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (1, 1, 10, 10)
    assert row.created_at == src.created_at and row.detect_run_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_ineligible_sources_are_not_cuttable_but_are_counted(db_session):
    repo = ImageCutsRepository(db_session)
    before = await repo.count_ineligible_detected(set())
    inactive = await _detected(db_session, await _image(db_session, status="rejected"))
    flagged_img = await _image(db_session)
    db_session.add(ImageExtras(image_id=flagged_img.id, flagged=True))
    flagged = await _detected(db_session, flagged_img)
    in_corpus_img = await _image(db_session)
    in_corpus = await _detected(db_session, in_corpus_img)
    ok = await _detected(db_session, await _image(db_session))
    await db_session.flush()
    ids = await _cuttable_ids(db_session, corpus={in_corpus_img.id})
    assert ok.id in ids and not ({inactive.id, flagged.id, in_corpus.id} & set(ids))
    assert await repo.count_ineligible_detected({in_corpus_img.id}) == before + 3


@pytest.mark.asyncio(loop_scope="session")
async def test_only_detected_rows_are_cuttable(db_session):
    img = await _image(db_session)
    await _cut(db_session, img, state=CutState.PROPOSED)
    rows = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalars().all()
    assert rows[0].id not in await _cuttable_ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_proposed_and_mark_failed(db_session):
    repo = ImageCutsRepository(db_session)
    a, b, cut_img = await _image(db_session), await _image(db_session), await _image(db_session)
    ok, bad = await _detected(db_session, a), await _detected(db_session, b)
    await repo.mark_proposed(ok.id, cut_img.id)
    await repo.mark_failed(bad.id, "source_missing")
    await db_session.refresh(ok)
    await db_session.refresh(bad)
    assert ok.state == CutState.PROPOSED and ok.cut_image_id == cut_img.id
    assert bad.state == CutState.FAILED and bad.failure_reason == "source_missing" and bad.cut_image_id is None
```

(Add `ImageExtras` is already imported at the top of that file.)

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_image_cuts_repository.py -v`
Expected: the five new tests FAIL (`AttributeError: ... select_cuttable`); the earlier ones pass.

- [ ] **Step 3: Implement**

In `repository/image_cuts.py` change the import to `from sqlalchemy import and_, exists, func, select, update` and `from Storage.models import CutState, Image, ImageCut, ImageExtras, ImageProcessingStatus, ImageStatus, OCRText`, then add to the class:

```python
    def _detected(self, corpus_ids):
        """(select of detected proposals joined to their source, the eligibility predicate). Eligible: the source is
        active, not flagged and not in `corpus_ids` (the frozen evaluation corpus). A detected row whose source was
        deleted has no join partner and is in neither set."""
        flagged = exists().where(ImageExtras.image_id == Image.id, ImageExtras.flagged.is_(True))
        eligible = and_(Image.status == ImageStatus.ACTIVE, ~flagged)
        if corpus_ids:
            eligible = and_(eligible, Image.id.not_in(list(corpus_ids)))
        base = (
            select(
                ImageCut.id.label("cut_id"), ImageCut.source_image_id, Image.filename, Image.created_at,
                ImageCut.crop_x, ImageCut.crop_y, ImageCut.crop_w, ImageCut.crop_h,
                ImageCut.source_content_hash, ImageCut.detect_run_id,
            )
            .join(Image, Image.id == ImageCut.source_image_id)
            .where(ImageCut.state == CutState.DETECTED)
        )
        return base, eligible

    async def select_cuttable(self, corpus_ids, limit: int | None = None):
        base, eligible = self._detected(corpus_ids)
        query = base.where(eligible).order_by(ImageCut.confidence.desc().nulls_last(), ImageCut.id)
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).fetchall()

    async def count_cuttable(self, corpus_ids) -> int:
        base, eligible = self._detected(corpus_ids)
        sub = base.where(eligible).subquery()
        return (await self.session.execute(select(func.count()).select_from(sub))).scalar_one()

    async def count_ineligible_detected(self, corpus_ids) -> int:
        base, eligible = self._detected(corpus_ids)
        sub = base.where(~eligible).subquery()
        return (await self.session.execute(select(func.count()).select_from(sub))).scalar_one()

    async def mark_proposed(self, cut_id: uuid.UUID, cut_image_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ImageCut).where(ImageCut.id == cut_id)
            .values(state=CutState.PROPOSED, cut_image_id=cut_image_id))

    async def mark_failed(self, cut_id: uuid.UUID, reason: str) -> None:
        await self.session.execute(
            update(ImageCut).where(ImageCut.id == cut_id)
            .values(state=CutState.FAILED, failure_reason=reason))
```

Replace the smoke line in `test_only_detected_rows_are_cuttable` with nothing if it reads as noise (keep the final two lines).

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add repository/image_cuts.py tests/integration/test_image_cuts_repository.py
git commit -m "feat(recut): repository queries for detected proposals, mark proposed and failed"
```

---

### Task 4: `CutCopier`

**Files:**
- Create: `repository/cut_copier.py`
- Test: `tests/integration/test_cut_copier.py`, `tests/docs/test_cut_copier_inventory.py`

**Interfaces:**
- Consumes: `shift_bbox` (Task 2).
- Produces: `CutCopier(session)` with `async copy(source_id, cut_id, crop: tuple[int, int, int, int]) -> dict[str, int]` (rows written per table; never commits); module constants `COPIED_TABLES`, `NOT_COPIED_TABLES` (frozensets of table names); helper `clone_rows(model, where, replace)`.
- Rules: see spec section 3. Deviation from spec text: `image_description_feedback` IS copied with its description, as `docs/image-derived-data.md` (authoritative) says; Task 6 corrects the spec.

- [ ] **Step 1: Write the failing tests**

`tests/docs/test_cut_copier_inventory.py`:

```python
"""The cut copier must account for every table in docs/image-derived-data.md: copied, or deliberately not."""
from repository.cut_copier import COPIED_TABLES, NOT_COPIED_TABLES
from tests.docs.test_image_derived_data_inventory import DOC, documented_tables


def test_every_inventory_table_is_copied_or_deliberately_not():
    documented = set(documented_tables(DOC.read_text(encoding="utf-8")))
    assert not (COPIED_TABLES & NOT_COPIED_TABLES)
    assert COPIED_TABLES | NOT_COPIED_TABLES == documented, sorted((COPIED_TABLES | NOT_COPIED_TABLES) ^ documented)
```

`tests/integration/test_cut_copier.py`:

```python
import uuid

import pytest
from sqlalchemy import select

from repository.cut_copier import CutCopier
from Storage.models import (DescriptionLemma, DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma,
                            Embedding, Image, ImageClassification, ImageDescription, ImageDescriptionEmbedding,
                            ImageDescriptionFeedback, ImageExtras, ImageTag, MemeProfile, MemeProfileValue,
                            OCRLemma, OCRText)

CROP = (10, 10, 100, 50)


async def _image(s, **kw):
    img = Image(filename=f"{uuid.uuid4()}.png", status=kw.pop("status", "active"), width=200, height=100, **kw)
    s.add(img)
    await s.flush()
    return img


async def _rows(s, model, image_id):
    return (await s.execute(select(model).where(model.image_id == image_id))).scalars().all()


async def _source_with_everything(s):
    src, cut = await _image(s), await _image(s, status="cut_proposed")
    s.add(DescriptionNote(image_id=src.id, text="my note"))
    await s.flush()
    s.add_all([
        DescriptionNoteEmbedding(description_note_id=src.id, embedding=[0.1] * 1024),
        DescriptionNoteLemma(image_id=src.id, lemma="note"),
        ImageExtras(image_id=src.id, flagged=True, flagged_reason="x", remarks="keep me"),
        ImageTag(image_id=src.id, key="k", value="n", source="Note"),
        ImageTag(image_id=src.id, key="k", value="o", source="OCR"),
        ImageTag(image_id=src.id, key="k", value="l", source="Ollama"),
        ImageTag(image_id=src.id, key="k", value="y", source="YOLO"),
        ImageTag(image_id=src.id, key="k", value="c", source="CONCEPT"),
        OCRText(image_id=src.id, text="inside", confidence=0.9, bbox=[[20, 20], [40, 20], [40, 30], [20, 30]]),
        OCRText(image_id=src.id, text="outside", confidence=0.9, bbox=[[180, 80], [190, 80], [190, 90], [180, 90]]),
        OCRText(image_id=src.id, text="nobox", confidence=0.9, bbox=None),
        OCRLemma(image_id=src.id, lemma="inside"),
        Embedding(image_id=src.id, embedding=[0.1] * 512),
        ImageClassification(image_id=src.id, classifier="text_heavy", result="x"),
        MemeProfile(image_id=src.id, taxonomy_version=1, extractor_model="m", extractor_prompt_version="p",
                    inputs_fingerprint="f" * 64, situation_text="sit", human_verified=True),
    ])
    desc = ImageDescription(image_id=src.id, prompt_key="p", model_used="m", text="a cat")
    s.add(desc)
    await s.flush()
    s.add_all([
        ImageDescriptionEmbedding(image_description_id=desc.id, embedding=[0.2] * 1024),
        ImageDescriptionFeedback(image_description_id=desc.id, approved=False),
        DescriptionLemma(image_description_id=desc.id, lemma="cat"),
        MemeProfileValue(image_id=src.id, axis="emotion", value_key="joy", confidence=0.9, source="model"),
    ])
    await s.flush()
    return src, cut, desc


@pytest.mark.asyncio(loop_scope="session")
async def test_group_1_is_copied_unstale(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    note = await db_session.get(DescriptionNote, cut.id)
    assert note.text == "my note"
    assert await db_session.get(DescriptionNoteEmbedding, cut.id) is not None
    assert [r.lemma for r in await _rows(db_session, DescriptionNoteLemma, cut.id)] == ["note"]
    (extras,) = await _rows(db_session, ImageExtras, cut.id)
    assert extras.remarks == "keep me" and extras.flagged is None and extras.flagged_reason is None
    tags = await _rows(db_session, ImageTag, cut.id)
    note_tags = [t for t in tags if t.source == "Note"]
    assert len(note_tags) == 1 and note_tags[0].stale is False
    profile = await db_session.get(MemeProfile, cut.id)
    assert profile.human_verified is True and profile.situation_text == "sit"
    assert len(await _rows(db_session, MemeProfileValue, cut.id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_group_3_is_copied_stale_and_ocr_is_shifted(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    ocr = {r.text: r for r in await _rows(db_session, OCRText, cut.id)}
    assert set(ocr) == {"inside", "nobox"}  # "outside" has its centre outside the crop
    assert ocr["inside"].bbox == [[10, 10], [30, 10], [30, 20], [10, 20]] and ocr["inside"].stale is True
    assert ocr["nobox"].stale is True
    assert all(r.stale for r in await _rows(db_session, OCRLemma, cut.id))
    assert {t.source for t in await _rows(db_session, ImageTag, cut.id)} == {"Note", "OCR", "Ollama"}
    assert all(t.stale for t in await _rows(db_session, ImageTag, cut.id) if t.source in ("OCR", "Ollama"))


@pytest.mark.asyncio(loop_scope="session")
async def test_descriptions_are_copied_with_their_children_under_new_ids(db_session):
    src, cut, desc = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    (new,) = await _rows(db_session, ImageDescription, cut.id)
    assert new.id != desc.id and new.text == "a cat" and new.stale is True
    assert await db_session.get(ImageDescriptionEmbedding, new.id) is not None
    assert (await db_session.get(ImageDescriptionFeedback, new.id)).approved is False
    lemmas = (await db_session.execute(select(DescriptionLemma).where(DescriptionLemma.image_description_id == new.id))
              ).scalars().all()
    assert [l.lemma for l in lemmas] == ["cat"]
    assert len(await _rows(db_session, ImageDescription, src.id)) == 1  # the source keeps its own


@pytest.mark.asyncio(loop_scope="session")
async def test_group_2_and_the_source_are_untouched(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    assert await _rows(db_session, Embedding, cut.id) == []
    assert await _rows(db_session, ImageClassification, cut.id) == []
    assert {t.source for t in await _rows(db_session, ImageTag, cut.id)}.isdisjoint({"YOLO", "CONCEPT"})
    assert len(await _rows(db_session, OCRText, src.id)) == 3
    (src_extras,) = await _rows(db_session, ImageExtras, src.id)
    assert src_extras.flagged is True


@pytest.mark.asyncio(loop_scope="session")
async def test_a_source_with_nothing_to_copy_copies_nothing(db_session):
    src, cut = await _image(db_session), await _image(db_session, status="cut_proposed")
    counts = await CutCopier(db_session).copy(src.id, cut.id, CROP)
    assert sum(counts.values()) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_cut_copier.py -v` and `.venv311/Scripts/python -m pytest tests/docs/test_cut_copier_inventory.py -v`
Expected: FAIL (`ModuleNotFoundError: repository.cut_copier`).

- [ ] **Step 3: Implement**

```python
"""Copies the inherited derived data of a source image to its recut cut (screenshot recut R3a; the rules are
docs/image-derived-data.md and docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md,
section 3). Never commits: the job runs it inside the cut's savepoint.

Rows are cloned with INSERT ... SELECT built from the model's own columns, so a column added later is copied without
touching this file. Only ocr_texts goes through Python (its boxes are moved into the cut's frame)."""
import uuid

import sqlalchemy as sa
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from batch.utils.recut_cut import shift_bbox
from Storage.models import (DescriptionLemma, DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma,
                            ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback, ImageExtras,
                            ImageTag, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue, OCRLemma, OCRText)

# Every table in docs/image-derived-data.md is in exactly one of these; tests/docs/test_cut_copier_inventory.py enforces it.
COPIED_TABLES = frozenset({
    "description_notes", "description_note_lemmas", "description_note_embeddings", "image_extras", "tags",
    "meme_profiles", "meme_profile_values", "meme_profile_situation_embeddings",
    "ocr_texts", "ocr_lemmas", "image_descriptions", "image_description_embeddings", "image_description_feedback",
    "description_lemmas",
})
NOT_COPIED_TABLES = frozenset({
    "image_cuts", "meme_text_ratings", "embeddings", "image_classifications", "ocr_text_embeddings",
    "image_processing_status", "tmp_duplicates", "tmp_clusters", "duplicate_decisions", "processing_errors",
    "image_metrics",
})


def _uuid(value: uuid.UUID):
    return sa.literal(value, type_=PG_UUID(as_uuid=True))


def clone_rows(model, where, replace: dict):
    """INSERT ... SELECT copying every column of `model` for rows matching `where`; columns named in `replace` take the
    given SQL expression instead of the source value."""
    table = model.__table__
    names = [c.name for c in table.columns]
    values = [replace.get(name, table.c[name]) for name in names]
    return insert(table).from_select(names, select(*values).where(where))


class CutCopier:

    def __init__(self, session):
        self.session = session

    async def _run(self, statement) -> int:
        return (await self.session.execute(statement)).rowcount or 0

    async def copy(self, source_id: uuid.UUID, cut_id: uuid.UUID, crop: tuple[int, int, int, int]) -> dict[str, int]:
        new = _uuid(cut_id)
        new_pk = func.gen_random_uuid()
        counts: dict[str, int] = {}

        # Group 1: copied as is.
        counts["description_notes"] = await self._run(clone_rows(
            DescriptionNote, DescriptionNote.image_id == source_id, {"image_id": new}))
        counts["description_note_embeddings"] = await self._run(clone_rows(
            DescriptionNoteEmbedding, DescriptionNoteEmbedding.description_note_id == source_id,
            {"description_note_id": new}))
        counts["description_note_lemmas"] = await self._run(clone_rows(
            DescriptionNoteLemma, DescriptionNoteLemma.image_id == source_id, {"image_id": new}))
        counts["image_extras"] = await self._run(clone_rows(
            ImageExtras, sa.and_(ImageExtras.image_id == source_id, ImageExtras.remarks.isnot(None)),
            {"image_id": new, "flagged": sa.null(), "flagged_reason": sa.null()}))
        counts["meme_profiles"] = await self._run(clone_rows(
            MemeProfile, MemeProfile.image_id == source_id, {"image_id": new}))
        counts["meme_profile_values"] = await self._run(clone_rows(
            MemeProfileValue, MemeProfileValue.image_id == source_id, {"image_id": new}))
        counts["meme_profile_situation_embeddings"] = await self._run(clone_rows(
            MemeProfileSituationEmbedding, MemeProfileSituationEmbedding.image_id == source_id, {"image_id": new}))
        note_tags = await self._run(clone_rows(
            ImageTag, sa.and_(ImageTag.image_id == source_id, ImageTag.source == "Note"),
            {"id": new_pk, "image_id": new, "stale": sa.false()}))

        # Group 3: copied stale.
        counts["ocr_texts"] = await self._copy_ocr_texts(source_id, cut_id, crop)
        counts["ocr_lemmas"] = await self._run(clone_rows(
            OCRLemma, OCRLemma.image_id == source_id, {"image_id": new, "stale": sa.true()}))
        counts.update(await self._copy_descriptions(source_id, cut_id))
        stale_tags = await self._run(clone_rows(
            ImageTag, sa.and_(ImageTag.image_id == source_id, ImageTag.source.in_(("OCR", "Ollama"))),
            {"id": new_pk, "image_id": new, "stale": sa.true()}))
        counts["tags"] = note_tags + stale_tags
        return counts

    async def _copy_ocr_texts(self, source_id, cut_id, crop) -> int:
        rows = (await self.session.execute(
            select(OCRText.text, OCRText.confidence, OCRText.bbox, OCRText.language, OCRText.lang_score,
                   OCRText.created_at).where(OCRText.image_id == source_id))).all()
        out = []
        for row in rows:
            keep, bbox = shift_bbox(row.bbox, crop)
            if keep:
                out.append({"id": uuid.uuid4(), "image_id": cut_id, "text": row.text, "confidence": row.confidence,
                            "bbox": bbox, "language": row.language, "lang_score": row.lang_score,
                            "created_at": row.created_at, "stale": True})
        if out:
            await self.session.execute(insert(OCRText.__table__), out)
        return len(out)

    async def _copy_descriptions(self, source_id, cut_id) -> dict[str, int]:
        counts = {"image_descriptions": 0, "image_description_embeddings": 0, "image_description_feedback": 0,
                  "description_lemmas": 0}
        old_ids = (await self.session.execute(
            select(ImageDescription.id).where(ImageDescription.image_id == source_id))).scalars().all()
        for old in old_ids:
            new = _uuid(uuid.uuid4())
            counts["image_descriptions"] += await self._run(clone_rows(
                ImageDescription, ImageDescription.id == old,
                {"id": new, "image_id": _uuid(cut_id), "stale": sa.true()}))
            for key, model in (("image_description_embeddings", ImageDescriptionEmbedding),
                               ("image_description_feedback", ImageDescriptionFeedback),
                               ("description_lemmas", DescriptionLemma)):
                counts[key] += await self._run(clone_rows(
                    model, model.image_description_id == old, {"image_description_id": new}))
        return counts
```

Bug to avoid when running: `new` for a description must be the SAME literal for the description row and its children (the code above reuses the one `new` variable inside the loop, which is correct; do not call `uuid.uuid4()` per statement).

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 commands. Expected: PASS (5 integration tests, 1 docs test). If `tests.docs...` import fails under `pytest tests/docs/` from the repo root, ledger a ruling and duplicate the 12-line `documented_tables` helper instead.

- [ ] **Step 5: Commit**

```bash
git add repository/cut_copier.py tests/integration/test_cut_copier.py tests/docs/test_cut_copier_inventory.py
git commit -m "feat(recut): CutCopier copies group 1 and group 3 derived data to a cut"
```

---

### Task 5: The job `create_recut_cuts`

**Files:**
- Create: `batch/create_recut_cuts.py`
- Test: `tests/integration/test_create_recut_cuts.py`, `batch/tests/test_create_recut_cuts_main.py`

**Interfaces:**
- Consumes: `ImageCutsRepository` (Task 3), `CutCopier` (Task 4), `crop_to_file`, `CutError`, `WRITE_ERROR` (Task 2), `load_corpus_ids` from `batch.detect_recut_crops`, `BusinessMetricsService`.
- Produces: `KIND = "create_recut_cuts"`, `MAX_CONSECUTIVE_FAILURES = 20`, `async run(session, base_path, corpus_ids, jpeg_quality, limit=None) -> SimpleMetricsListener` (stats `proposals_selected`, `cut`, `skipped`, `failed`, `proposals_remaining`), `async main(trigger="manual", run_id=None, limit=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_create_recut_cuts.py`:

```python
import os
import uuid
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import batch.create_recut_cuts as job
from Storage.models import (CutState, DescriptionNote, Image, ImageCut, ImageExtras, ImageTag, OCRText, ProcessEvent)

BOX = dict(crop_x=10, crop_y=20, crop_w=50, crop_h=40)


def _write(path, fmt="PNG"):
    arr = np.random.default_rng(3).integers(0, 256, (100, 120, 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(path, fmt)


async def _proposal(s, tmp_path, name="a.png", confidence=0.9, write=True, **image_kw):
    from batch.utils.file_hash import sha256_file
    img = Image(filename=name, status="active", width=120, height=100, **image_kw)
    s.add(img)
    await s.flush()
    digest = None
    if write:
        _write(tmp_path / name, "JPEG" if name.endswith(".jpg") else "PNG")
        digest = sha256_file(str(tmp_path / name))
    cut = ImageCut(source_image_id=img.id, source_content_hash=digest, detector="margin_trim", detector_version="1",
                   confidence=confidence, state=CutState.DETECTED, **BOX)
    s.add(cut)
    await s.flush()
    return img, cut


async def _only(s, keep):
    """Corpus ids that make a run see only the proposals of `keep` images."""
    rows = await job.ImageCutsRepository(s).select_cuttable(set())
    return {r.source_image_id for r in rows} - {i.id for i in keep}


async def _go(s, tmp_path, corpus, limit=None):
    await s.commit()
    m = await job.run(s, str(tmp_path), corpus, 95, limit=limit)
    await s.commit()
    return m.counters_dict()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_proposal_becomes_a_cut_with_file_row_link_copies_and_event(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    db_session.add_all([DescriptionNote(image_id=src.id, text="n"),
                        OCRText(image_id=src.id, text="hi", confidence=0.9, bbox=[[20, 30], [40, 30], [40, 40], [20, 40]]),
                        ImageTag(image_id=src.id, key="k", value="v", source="OCR")])
    stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.PROPOSED and cut.cut_image_id is not None and cut.failure_reason is None
    new = await db_session.get(Image, cut.cut_image_id)
    assert new.status == "cut_proposed" and (new.width, new.height) == (50, 40)
    assert new.created_at == src.created_at and new.ingestion_batch_id is None and new.id != src.id
    assert os.path.splitext(new.filename)[1] == ".png" and (tmp_path / new.filename).is_file()
    assert PILImage.open(tmp_path / new.filename).size == (50, 40)
    assert (await db_session.get(DescriptionNote, new.id)).text == "n"
    (ocr,) = (await db_session.execute(select(OCRText).where(OCRText.image_id == new.id))).scalars().all()
    assert ocr.bbox == [[10, 10], [30, 10], [30, 20], [10, 20]] and ocr.stale is True
    (tag,) = (await db_session.execute(select(ImageTag).where(ImageTag.image_id == new.id))).scalars().all()
    assert tag.stale is True
    events = (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).scalars().all()
    assert [(e.process, e.event) for e in events] == [("recut", "proposed")]
    assert src.status == "active"
    assert stats["cut"] == 1 and stats["proposals_selected"] == 1 and stats.get("failed", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_jpeg_source_gives_a_jpeg_cut(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path, name="a.jpg")
    await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    new = await db_session.get(Image, cut.cut_image_id)
    assert new.filename.endswith(".jpg") and PILImage.open(tmp_path / new.filename).format == "JPEG"


@pytest.mark.asyncio(loop_scope="session")
async def test_changed_source_ends_failed_and_makes_nothing(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    PILImage.new("RGB", (120, 100), (1, 2, 3)).save(tmp_path / "a.png")
    stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "source_changed" and cut.cut_image_id is None
    assert stats["failed"] == 1 and stats.get("cut", 0) == 0
    assert sorted(os.listdir(tmp_path)) == ["a.png"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_source_file_ends_failed(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path, write=False)
    await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "source_missing"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_database_failure_rolls_back_the_cut_and_removes_the_file(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)

    async def boom(self, *a, **k):
        raise IntegrityError("stmt", {}, Exception("boom"))

    with patch.object(job.CutCopier, "copy", boom):
        stats = await _go(db_session, tmp_path, await _only(db_session, [src]))
    await db_session.refresh(cut)
    assert cut.state == CutState.FAILED and cut.failure_reason == "write_error" and cut.cut_image_id is None
    assert sorted(os.listdir(tmp_path)) == ["a.png"]
    assert (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).first() is None
    assert stats["failed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_highest_confidence_first_and_limit(db_session, tmp_path):
    lo_src, lo = await _proposal(db_session, tmp_path, name="lo.png", confidence=0.3)
    hi_src, hi = await _proposal(db_session, tmp_path, name="hi.png", confidence=0.95)
    stats = await _go(db_session, tmp_path, await _only(db_session, [lo_src, hi_src]), limit=1)
    await db_session.refresh(hi)
    await db_session.refresh(lo)
    assert hi.state == CutState.PROPOSED and lo.state == CutState.DETECTED
    assert stats["proposals_selected"] == 1 and stats["proposals_remaining"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_ineligible_sources_are_skipped_and_stay_detected(db_session, tmp_path):
    ok_src, _ = await _proposal(db_session, tmp_path, name="ok.png")
    rej_src, rej = await _proposal(db_session, tmp_path, name="rej.png")
    rej_src.status = "rejected"
    fl_src, fl = await _proposal(db_session, tmp_path, name="fl.png")
    db_session.add(ImageExtras(image_id=fl_src.id, flagged=True))
    await db_session.flush()
    corpus = await _only(db_session, [ok_src])
    stats = await _go(db_session, tmp_path, corpus)
    await db_session.refresh(rej)
    await db_session.refresh(fl)
    assert rej.state == CutState.DETECTED and fl.state == CutState.DETECTED
    assert stats["cut"] == 1 and stats["skipped"] >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rerun_cuts_nothing_and_does_not_double_count(db_session, tmp_path):
    src, cut = await _proposal(db_session, tmp_path)
    corpus = await _only(db_session, [src])
    await _go(db_session, tmp_path, corpus)
    stats = await _go(db_session, tmp_path, corpus)
    assert stats.get("cut", 0) == 0 and stats["proposals_selected"] == 0
    events = (await db_session.execute(select(ProcessEvent).where(ProcessEvent.subject_id == cut.id))).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_consecutive_failures_stop_the_run(db_session, tmp_path):
    srcs = [(await _proposal(db_session, tmp_path, name=f"m{i}.png", write=False))[0] for i in range(3)]
    corpus = await _only(db_session, srcs)
    await db_session.commit()
    with patch.object(job, "MAX_CONSECUTIVE_FAILURES", 2), pytest.raises(RuntimeError):
        await job.run(db_session, str(tmp_path), corpus, 95)
```

`batch/tests/test_create_recut_cuts_main.py`: copy `test_detect_recut_crops_main.py`, replacing `module` with `batch.create_recut_cuts`, `load_corpus_ids`/`params_from_settings` patches with `load_corpus_ids` only, kind `"create_recut_cuts"`, stats `{"cut": 1}`, and the `run` patch as `AsyncMock(return_value=_metrics())`; assert `finish.assert_called_once_with(run_id)` and `tracked.assert_called_once_with(kind="create_recut_cuts", trigger="manual")` and `repo.update_stats.assert_awaited_once_with(new_id, cut=1)`.

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_create_recut_cuts.py -v`
Expected: FAIL (`ModuleNotFoundError: batch.create_recut_cuts`).

- [ ] **Step 3: Implement**

```python
"""Screenshot recut, cut and copy (R3a; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md).
Turns each `detected` image_cuts proposal into a cut file (same format as the source), a `cut_proposed` image, copied
derived data (groups 1 and 3), a `proposed` state and the `proposed` metric event, one transaction per cut. Highest
confidence first; --limit caps a run. Safe to re-run: a proposal is cut once."""
import argparse
import asyncio
import contextlib
import os
import uuid

from sqlalchemy.exc import SQLAlchemyError

from Backend.app.services.business_metrics_service import BusinessMetricsService
from batch.detect_recut_crops import load_corpus_ids
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.recut_cut import WRITE_ERROR, CutError, crop_to_file
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.cut_copier import CutCopier
from repository.image_cuts import ImageCutsRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageStatus

KIND = "create_recut_cuts"
# This many failed proposals in a row means the drive or BASE_PATH is the problem, not the proposals: stop the run.
MAX_CONSECUTIVE_FAILURES = 20


def _remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


async def _cut_one(session, cuts: ImageCutsRepository, base_path: str, row, dst_name: str, jpeg_quality: int) -> None:
    crop = (row.crop_x, row.crop_y, row.crop_w, row.crop_h)
    cut_file = crop_to_file(os.path.join(base_path, row.filename), os.path.join(base_path, dst_name), crop,
                            row.source_content_hash, jpeg_quality)
    try:
        async with session.begin_nested():
            image = Image(id=uuid.uuid4(), filename=dst_name, content_hash=cut_file.content_hash,
                          width=cut_file.width, height=cut_file.height, status=ImageStatus.CUT_PROPOSED,
                          created_at=row.created_at)
            session.add(image)
            await session.flush()
            await CutCopier(session).copy(row.source_image_id, image.id, crop)
            await cuts.mark_proposed(row.cut_id, image.id)
            await BusinessMetricsService(session).record("recut", "proposed", row.detect_run_id, row.cut_id)
    except SQLAlchemyError as e:
        raise CutError(WRITE_ERROR, f"{type(e).__name__}: {e}"[:200]) from e


async def run(session, base_path: str, corpus_ids, jpeg_quality: int, limit: int | None = None) -> SimpleMetricsListener:
    if not os.path.isdir(base_path):
        raise FileNotFoundError(f"BASE_PATH is not a directory: {base_path}")
    cuts = ImageCutsRepository(session)
    skipped = await cuts.count_ineligible_detected(corpus_ids)
    total = await cuts.count_cuttable(corpus_ids)
    rows = await cuts.select_cuttable(corpus_ids, limit=limit)
    print(f"Proposals to cut: {len(rows)} of {total} ({skipped} skipped: source not active, flagged or in the corpus)")

    metrics = SimpleMetricsListener()
    metrics.add("proposals_selected", len(rows))
    metrics.add("proposals_remaining", total - len(rows))
    metrics.add("skipped", skipped)
    metrics.add("cut", 0)
    metrics.add("failed", 0)

    streak = 0
    for row in rows:
        dst_name = f"{uuid.uuid4()}{os.path.splitext(row.filename)[1]}"
        dst = os.path.join(base_path, dst_name)
        print(f"cut {row.cut_id}: {row.filename} -> {dst_name}")
        try:
            await _cut_one(session, cuts, base_path, row, dst_name, jpeg_quality)
        except CutError as e:
            _remove(dst)
            await cuts.mark_failed(row.cut_id, e.reason)
            await session.commit()
            metrics.increment("failed")
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(f"{streak} proposals failed in a row (last: {e}); check BASE_PATH and the drive")
        except BaseException:
            _remove(dst)
            raise
        else:
            await session.commit()
            metrics.increment("cut")
            streak = 0
    return metrics


async def _execute(run_id: uuid.UUID, limit: int | None) -> SimpleMetricsListener:
    corpus_ids = load_corpus_ids()
    async with AsyncSessionLocal() as session:
        metrics = await run(session, settings.BASE_PATH, corpus_ids, settings.RECUT.JPEG_QUALITY, limit=limit)
        await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
        await session.commit()
    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, limit: int | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            metrics = await _execute(run_id, limit)
    else:
        async with tracked_run(kind=KIND, trigger=trigger) as new_run_id:
            metrics = await _execute(new_run_id, limit)
    print("Recut cut results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="cut at most this many proposals this run")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(limit=args.limit))
```

Import only what is used (no `CutState`). Tests patch `job.CutCopier` and `job.ImageCutsRepository`, both imported at module level above, as required. A stats flush on a mid-run crash is a known R2 minor (M4) and stays out of scope.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_create_recut_cuts.py -v` and `.venv311/Scripts/python -m pytest batch/tests/test_create_recut_cuts_main.py -v`
Expected: PASS (9 integration, 2 main). If a test fails because `session.begin_nested()` interacts badly with the `create_savepoint` fixture, fix the production code or test setup (commit after setup is already in `_go`), ledger a ruling, and do not weaken an assertion.

- [ ] **Step 5: Commit**

```bash
git add batch/create_recut_cuts.py tests/integration/test_create_recut_cuts.py batch/tests/test_create_recut_cuts_main.py
git commit -m "feat(recut): create_recut_cuts batch job (cut file, cut_proposed image, copied data, proposed event)"
```

---

### Task 6: Registration, docs, spec and tracker

**Files:**
- Modify: `environments/batch_registry.yaml`, `docs/batches/inventory.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md`, `.../tracker.md`
- Test: the whole suite (all roots)

- [ ] **Step 1: Register the job**

In `environments/batch_registry.yaml` add after the `detect_recut_crops` entry:

```yaml
create_recut_cuts:
  module: batch.create_recut_cuts
  kind: create_recut_cuts
```

In `docs/batches/inventory.md`: change the counts on line 25 to 30 admin-triggerable and 34 registry entries (mention `create_recut_cuts` 2026-10-26), and add a row after `detect_recut_crops` in the same column layout: `create_recut_cuts` | Screenshot recut: cuts each `detected` proposal into a same-format file, a `cut_proposed` image and copied derived data (R3a) | `create_recut_cuts` | yes | no | `--limit`; highest confidence first; one transaction per cut; skips sources that are not active, flagged or in the frozen corpus; a proposal that cannot be cut ends `failed` with a reason; 20 in a row stop the run.

In `CLAUDE.md`, add after the `detect_recut_crops` entry a `create_recut_cuts` entry in the same style: what it does, the format rule, groups copied, stats `proposals_selected/cut/skipped/failed/proposals_remaining`, `--limit`, setting `recut.jpeg_quality`, admin-triggerable and manual-trigger only, link to the R3a spec; and change the `detect_recut_crops` entry's "records no metric event (R3 does)" to "(R3a does)".

- [ ] **Step 2: Correct the spec and update the tracker**

In the R3a spec section 3 replace "`image_description_feedback` is not copied (it is lost when the stale description is replaced)." with "`image_description_feedback` is copied with its description, as `docs/image-derived-data.md` says; it is lost when the stale description is replaced by the refresh." Set the spec header to `Status: implementation` and add `Plan: docs/superpowers/plans/2026-10-26-r3a-cut-and-copy.md`. In the tracker, set the R3a row to `implementation` and link the spec.

- [ ] **Step 3: Run every test root separately**

Run, one command each:
- `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/ -q`
- `cd Backend && ../.venv311/Scripts/python -m pytest -q`
- `.venv311/Scripts/python -m pytest batch/tests/ -q`
- `.venv311/Scripts/python -m pytest tests/rules/ -q`
- `.venv311/Scripts/python -m pytest tests/docs/ -q`

Expected: all green. A registry-versus-inventory guard in `batch/tests` or `tests/docs` may name the new job; fix the docs it points to.

- [ ] **Step 4: Commit**

```bash
git add environments/batch_registry.yaml docs/batches/inventory.md CLAUDE.md docs/superpowers/specs/2026-10-17-screenshot-recut
git commit -m "docs(recut): register create_recut_cuts; R3a spec correction and tracker"
```
