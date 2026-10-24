# R2 Crop Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the batch job `detect_recut_crops`, which stores one `detected` `image_cuts` proposal for each active image with a flat margin around the meme.

**Architecture:** A pure detector module (Pillow and numpy only) finds the margin box and applies an OCR-clipping guard. A repository selects candidates and writes proposals. A run-tracked batch job, shaped like `classify_text_heavy`, ties them together, remembers checked images in `image_processing_status` under a versioned pipeline name, and is registered for the admin trigger. A calibration script run once outside CI fixes the guard's threshold.

**Tech Stack:** Python 3.11, Pillow, numpy, SQLAlchemy async, PostgreSQL (`ocrdb_test` for integration tests), pytest, Dynaconf/YAML settings.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md` (findings: `.../2026-10-24-r2-spike-findings.md`).

## Global Constraints

- Detector: name `margin_trim`, version `1` (constant `DETECTOR_VERSION` in code, never a setting). Pipeline name `recut_detect_v1` (`f"recut_detect_v{DETECTOR_VERSION}"`).
- A proposal removes between 7% and 75% of the image area (`min_removed_share: 0.07`, `max_removed_share: 0.75`); images under 120 px on a side get none; downscale to at most 600 px on the long side; `flat_tolerance: 7.0`; `ocr_overlap_max: 0.15` (final value from Task 5; `1.0` switches the guard off); `commit_interval: 200`.
- Confidence is `0.6 + 0.4 * min(1, removed_share / 0.3)`, rounded to 4 places. It ranks proposals; it is not a probability.
- Job writes only `image_cuts` rows (state `detected`) and `image_processing_status` rows. No cut files, no status change, no `process_events` (R3).
- Stats keys: `images_selected`, `proposed`, `no_cut`, `clipped_text`, `unreadable`, `images_remaining`.
- Settings group `recut` in `environments/settings.yaml`, every key required, read as `settings.RECUT.<KEY>`.
- Repositories never commit (the job commits). Use `.venv311`. Integration tests: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/<file>`; batch unit tests: `pytest batch/tests/<file>`. Run the test roots separately.
- Never touch a live metal/general/IT database. The calibration script (Task 5) uses `DATABASE_URL_READONLY` only.
- Plan ruling (addition to the spec): images with `image_extras.flagged` true are not candidates, because they are already on their way out of the library. Task 6 adds this to the spec.

## Review Focus

Each line has a test in the task that owns the code.

1. Palette, RGBA, grayscale and animated images must not crash the detector (Task 1: `test_non_rgb_modes_are_handled`).
2. A file whose real size differs from the database `width`/`height` must give a box inside the file (Task 1: `test_box_comes_from_the_file_size_not_the_database`; Task 4: job test with stale dimensions).
3. An image with no OCR rows must pass the guard (Task 1: `test_no_regions_never_clips`).
4. A malformed OCR `bbox` (null, wrong shape, strings) must be ignored, never crash the run (Task 1: `test_malformed_regions_are_ignored`).
5. A flagged image, or one with a live, cut or rejected-for-this-version history, must not be re-proposed (Task 3: one test per exclusion).

## File Structure

- Create `batch/utils/crop_detection.py`: `DETECTOR`, `DETECTOR_VERSION`, `DetectParams`, `Proposal`, `detect_margin_box`, `region_rect`, `clips_ocr`.
- Create `repository/image_cuts.py`: `ImageCutsRepository` (`select_candidates`, `count_candidates`, `insert_detected`, `ocr_regions`).
- Create `batch/detect_recut_crops.py`: the job (`run`, `main`, CLI).
- Modify `environments/settings.yaml` (group `recut`), `environments/batch_registry.yaml` (entry), `docs/batches/inventory.md`, `CLAUDE.md` (batch list).
- Create tests: `batch/tests/test_crop_detection.py`, `batch/tests/test_recut_settings.py`, `batch/tests/test_detect_recut_crops_main.py`, `tests/integration/test_image_cuts_repository.py`, `tests/integration/test_detect_recut_crops.py`.
- Create (throwaway, deleted in Task 6) `batch/tools/recut_spike/calibrate_guard.py`.

---

### Task 1: Detector module

**Files:**
- Create: `batch/utils/crop_detection.py`
- Test: `batch/tests/test_crop_detection.py`

**Interfaces:**
- Produces:
  - `DETECTOR = "margin_trim"`, `DETECTOR_VERSION = "1"`.
  - `DetectParams(min_removed_share: float, max_removed_share: float, flat_tolerance: float, ocr_overlap_max: float)` frozen dataclass.
  - `Proposal(box: tuple[int, int, int, int], confidence: float, removed_share: float)` frozen dataclass; box is `(x0, y0, x1, y1)` in source pixels.
  - `detect_margin_box(path: str, params: DetectParams) -> Proposal | None`; raises on an unreadable file.
  - `region_rect(bbox) -> tuple[float, float, float, float] | None`.
  - `clips_ocr(box, regions, max_overlap: float) -> bool`, where `regions` is an iterable of raw OCR `bbox` values.

- [ ] **Step 1: Write the failing tests**

```python
# batch/tests/test_crop_detection.py
import numpy as np
import pytest
from PIL import Image

from batch.utils.crop_detection import DetectParams, clips_ocr, detect_margin_box, region_rect

PARAMS = DetectParams(min_removed_share=0.07, max_removed_share=0.75, flat_tolerance=7.0, ocr_overlap_max=0.15)


def _noise(w, h, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def _bordered(path, w, h, border, colour=255):
    """Noisy content inside a flat border of `border` px on every side."""
    arr = np.full((h, w, 3), colour, dtype=np.uint8)
    arr[border:h - border, border:w - border] = _noise(w - 2 * border, h - 2 * border)
    Image.fromarray(arr).save(path, "PNG")


def test_bordered_image_gives_the_content_box(tmp_path):
    p = tmp_path / "a.png"
    _bordered(p, 400, 300, 30)
    got = detect_margin_box(str(p), PARAMS)
    assert got.box == (30, 30, 370, 270)
    assert got.removed_share == pytest.approx(1 - 340 * 240 / (400 * 300), abs=1e-3)


def test_large_image_box_is_scaled_back_within_two_pixels(tmp_path):
    p = tmp_path / "big.png"
    _bordered(p, 1200, 900, 60)
    got = detect_margin_box(str(p), PARAMS)
    assert all(abs(a - b) <= 2 for a, b in zip(got.box, (60, 60, 1140, 840)))


def test_plain_image_gives_none(tmp_path):
    p = tmp_path / "plain.png"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(p)
    assert detect_margin_box(str(p), PARAMS) is None


def test_trim_under_the_minimum_gives_none(tmp_path):
    p = tmp_path / "thin.png"
    _bordered(p, 400, 300, 5)  # removes about 5.7%
    assert detect_margin_box(str(p), PARAMS) is None


def test_trim_over_the_maximum_gives_none(tmp_path):
    p = tmp_path / "tiny_content.png"
    _bordered(p, 400, 300, 100)  # keeps a 200x100 centre, removes about 83%
    assert detect_margin_box(str(p), PARAMS) is None


def test_image_under_120_px_gives_none(tmp_path):
    p = tmp_path / "small.png"
    _bordered(p, 110, 110, 20)
    assert detect_margin_box(str(p), PARAMS) is None


def test_larger_trim_has_higher_confidence(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _bordered(a, 400, 300, 15)
    _bordered(b, 400, 300, 30)
    assert detect_margin_box(str(b), PARAMS).confidence > detect_margin_box(str(a), PARAMS).confidence


def test_confidence_is_capped_at_one(tmp_path):
    p = tmp_path / "c.png"
    _bordered(p, 400, 300, 60)  # removes about 52%
    assert detect_margin_box(str(p), PARAMS).confidence == 1.0


def test_non_rgb_modes_are_handled(tmp_path):
    base = Image.fromarray(_noise(400, 300))
    canvas = Image.new("RGB", (400, 300), (255, 255, 255))
    canvas.paste(base.crop((0, 0, 340, 240)), (30, 30))
    for mode in ("L", "P", "RGBA"):
        p = tmp_path / f"{mode}.png"
        canvas.convert(mode).save(p)
        assert detect_margin_box(str(p), PARAMS).box == (30, 30, 370, 270)


def test_box_comes_from_the_file_size_not_the_database(tmp_path):
    p = tmp_path / "a.png"
    _bordered(p, 400, 300, 30)
    got = detect_margin_box(str(p), PARAMS)  # the function never sees database dimensions
    assert 0 <= got.box[0] < got.box[2] <= 400 and 0 <= got.box[1] < got.box[3] <= 300


def test_unreadable_file_raises(tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"not an image")
    with pytest.raises(Exception):
        detect_margin_box(str(p), PARAMS)


def test_region_rect_reads_polygons_and_xywh():
    assert region_rect([[10, 20], [60, 20], [60, 40], [10, 40]]) == (10, 20, 60, 40)
    assert region_rect([10, 20, 50, 20]) == (10, 20, 60, 40)


def test_malformed_regions_are_ignored():
    for bad in (None, "x", [], [1, 2], [["a", "b"]], {"x": 1}, [[1, 2], [3]]):
        assert region_rect(bad) is None
    assert clips_ocr((0, 0, 100, 100), [None, "x", []], 0.15) is False


def test_no_regions_never_clips():
    assert clips_ocr((0, 0, 100, 100), [], 0.15) is False


def test_region_inside_the_box_does_not_clip():
    assert clips_ocr((0, 0, 100, 100), [[10, 10, 20, 20]], 0.15) is False


def test_region_cut_through_clips():
    assert clips_ocr((50, 0, 100, 100), [[10, 10, 80, 20]], 0.15) is True  # 40 of 80 px outside


def test_region_exactly_at_the_limit_does_not_clip():
    # 10x10 region with 2 px outside the box: outside share exactly 0.2
    assert clips_ocr((2, 0, 100, 100), [[0, 10, 10, 10]], 0.2) is False
    assert clips_ocr((2, 0, 100, 100), [[0, 10, 10, 10]], 0.19) is True


def test_guard_off_at_one():
    assert clips_ocr((90, 90, 100, 100), [[0, 0, 50, 50]], 1.0) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv311/Scripts/python.exe -m pytest batch/tests/test_crop_detection.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'batch.utils.crop_detection'`.

- [ ] **Step 3: Write the implementation**

```python
# batch/utils/crop_detection.py
"""Margin-trim crop detection for the screenshot recut epic (R2; see
docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md). Pure: Pillow and
numpy only, no database. A proposal is the content box left after peeling flat rows and columns from the four
edges of the image."""
from dataclasses import dataclass

import numpy as np
from PIL import Image

DETECTOR = "margin_trim"
DETECTOR_VERSION = "1"  # bump when the algorithm changes: the job's pipeline name carries it, so every image is checked again

MAX_SIDE = 600
MIN_IMAGE_SIDE = 120


@dataclass(frozen=True)
class DetectParams:
    min_removed_share: float
    max_removed_share: float
    flat_tolerance: float
    ocr_overlap_max: float


@dataclass(frozen=True)
class Proposal:
    box: tuple[int, int, int, int]   # x0, y0, x1, y1 in source pixels
    confidence: float
    removed_share: float


def _span(std: np.ndarray, tolerance: float):
    """First and last-plus-one index whose standard deviation reaches the tolerance, or None if all are flat."""
    idx = np.flatnonzero(std >= tolerance)
    if idx.size == 0:
        return None
    return int(idx[0]), int(idx[-1]) + 1


def detect_margin_box(path: str, params: DetectParams) -> Proposal | None:
    """Returns the content box, or None when there is nothing to propose (no margin, margin outside the allowed
    share, or an image under MIN_IMAGE_SIDE). Raises on an unreadable file; the caller counts it."""
    with Image.open(path) as im:
        w, h = im.size
        if min(w, h) < MIN_IMAGE_SIDE:
            return None
        scale = min(1.0, MAX_SIDE / max(w, h))
        small = im.convert("L").resize((max(1, round(w * scale)), max(1, round(h * scale))))
    gray = np.asarray(small, dtype=np.float32)
    rows = _span(gray.std(axis=1), params.flat_tolerance)
    cols = _span(gray.std(axis=0), params.flat_tolerance)
    if rows is None or cols is None:
        return None
    y0, y1 = rows
    x0, x1 = cols
    box = (round(x0 / scale), round(y0 / scale), min(w, round(x1 / scale)), min(h, round(y1 / scale)))
    removed = 1 - (box[2] - box[0]) * (box[3] - box[1]) / (w * h)
    if not params.min_removed_share <= removed <= params.max_removed_share:
        return None
    confidence = round(0.6 + 0.4 * min(1.0, removed / 0.3), 4)
    return Proposal(box=box, confidence=confidence, removed_share=removed)


def region_rect(bbox) -> tuple[float, float, float, float] | None:
    """Axis-aligned rectangle (x0, y0, x1, y1) of an OCR bbox, which is either a polygon of [x, y] points or
    [x, y, w, h]. Anything else is None."""
    try:
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x, y, bw, bh = bbox
            return float(x), float(y), float(x + bw), float(y + bh)
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 3:
            xs = [float(p[0]) for p in bbox if len(p) == 2]
            ys = [float(p[1]) for p in bbox if len(p) == 2]
            if len(xs) == len(bbox):
                return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError):
        return None
    return None


def clips_ocr(box, regions, max_overlap: float) -> bool:
    """True when more than `max_overlap` of any OCR region's area lies outside `box` (so the cut would clip
    text). Malformed regions are ignored. A `max_overlap` of 1.0 switches the guard off."""
    bx0, by0, bx1, by1 = box
    for raw in regions:
        rect = region_rect(raw)
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        area = (x1 - x0) * (y1 - y0)
        if area <= 0:
            continue
        iw = max(0.0, min(x1, bx1) - max(x0, bx0))
        ih = max(0.0, min(y1, by1) - max(y0, by0))
        if (area - iw * ih) / area > max_overlap:
            return True
    return False
```

Note: `region_rect` treats a 4-number list as `[x, y, w, h]`; a 4-point polygon is a list of lists, so the two cannot be confused. The `[[1, 2], [3]]` case must return None (the length check on each point does that).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv311/Scripts/python.exe -m pytest batch/tests/test_crop_detection.py -q`
Expected: PASS, all tests green. If `test_trim_under_the_minimum_gives_none` or the over-maximum test fails on a boundary, fix the fixture border width, not the thresholds.

- [ ] **Step 5: Commit**

```bash
git add batch/utils/crop_detection.py batch/tests/test_crop_detection.py
git commit -m "feat(recut): margin-trim detector and OCR clipping guard"
```

---

### Task 2: Settings group

**Files:**
- Modify: `environments/settings.yaml` (add the `recut` group after `ocr:`)
- Test: `batch/tests/test_recut_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.RECUT.MIN_REMOVED_SHARE`, `.MAX_REMOVED_SHARE`, `.FLAT_TOLERANCE`, `.OCR_OVERLAP_MAX`, `.COMMIT_INTERVAL`.

- [ ] **Step 1: Write the failing test**

```python
# batch/tests/test_recut_settings.py
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"min_removed_share", "max_removed_share", "flat_tolerance", "ocr_overlap_max", "commit_interval"}


def test_recut_group_has_every_key_in_the_common_file():
    group = yaml.safe_load((ROOT / "environments/settings.yaml").read_text(encoding="utf-8"))["recut"]
    assert set(group) == KEYS


def test_recut_values_are_sane():
    g = yaml.safe_load((ROOT / "environments/settings.yaml").read_text(encoding="utf-8"))["recut"]
    assert 0 < g["min_removed_share"] < g["max_removed_share"] < 1
    assert g["flat_tolerance"] > 0
    assert 0 < g["ocr_overlap_max"] <= 1
    assert g["commit_interval"] >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv311/Scripts/python.exe -m pytest batch/tests/test_recut_settings.py -q`
Expected: FAIL with `KeyError: 'recut'`.

- [ ] **Step 3: Add the settings**

Insert after the `ocr:` block in `environments/settings.yaml`:

```yaml
recut:                          # screenshot recut, crop detection (docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md)
  min_removed_share: 0.07       # a proposal removes at least this share of the image area (spike floor)
  max_removed_share: 0.75       # ... and at most this share
  flat_tolerance: 7.0           # row/column intensity std-dev below this counts as flat margin
  ocr_overlap_max: 0.15         # drop a proposal when more than this share of an OCR region falls outside the box (1.0 = guard off)
  commit_interval: 200
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv311/Scripts/python.exe -m pytest batch/tests/test_recut_settings.py batch/tests -q -k "recut or settings"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add environments/settings.yaml batch/tests/test_recut_settings.py
git commit -m "feat(recut): recut settings group"
```

---

### Task 3: Repository

**Files:**
- Create: `repository/image_cuts.py`
- Test: `tests/integration/test_image_cuts_repository.py`

**Interfaces:**
- Consumes: `Storage.models` (`Image`, `ImageCut`, `CutState`, `ImageExtras`, `ImageProcessingStatus`, `OCRText`).
- Produces: `ImageCutsRepository(session)` with
  - `select_candidates(pipeline: str, detector_version: str, exclude_ids: set[uuid.UUID], limit: int | None = None)` returning rows `(id, filename, content_hash)` ordered by `id`;
  - `count_candidates(pipeline, detector_version, exclude_ids) -> int`;
  - `insert_detected(*, source_image_id, source_content_hash, box, detector, detector_version, confidence, detect_run_id) -> None` (flushes, never commits);
  - `ocr_regions(image_id, confidence_min) -> list` of raw `bbox` values.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_image_cuts_repository.py
import uuid

import pytest
from sqlalchemy import select

from repository.image_cuts import ImageCutsRepository
from Storage.models import CutState, Image, ImageCut, ImageExtras, ImageProcessingStatus, OCRText

PIPE, VER = "recut_detect_v1", "1"


async def _image(s, **kw):
    kw.setdefault("filename", f"{uuid.uuid4()}.png")
    kw.setdefault("status", "active")
    kw.setdefault("width", 400)
    kw.setdefault("height", 300)
    img = Image(**kw)
    s.add(img)
    await s.flush()
    return img


async def _cut(s, source, state=CutState.DETECTED, version=VER, cut_image=None):
    s.add(ImageCut(source_image_id=source.id if source else None, cut_image_id=cut_image.id if cut_image else None,
                   crop_x=1, crop_y=1, crop_w=10, crop_h=10, detector="margin_trim", detector_version=version,
                   state=state))
    await s.flush()


async def _ids(s, exclude=frozenset()):
    rows = await ImageCutsRepository(s).select_candidates(PIPE, VER, set(exclude))
    return {r.id for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_plain_active_image_is_a_candidate(db_session):
    img = await _image(db_session)
    assert img.id in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_corpus_images_are_excluded(db_session):
    img = await _image(db_session)
    assert img.id not in await _ids(db_session, {img.id})


@pytest.mark.asyncio(loop_scope="session")
async def test_non_active_images_are_excluded(db_session):
    for status in ("pending", "rejected", "cut_proposed"):
        img = await _image(db_session, status=status)
        assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_images_without_dimensions_are_excluded(db_session):
    img = await _image(db_session, width=None, height=None)
    assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_images_are_excluded(db_session):
    img = await _image(db_session)
    db_session.add(ImageExtras(image_id=img.id, flagged=True))
    await db_session.flush()
    assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_unflagged_extras_do_not_exclude(db_session):
    img = await _image(db_session)
    db_session.add(ImageExtras(image_id=img.id, flagged=False))
    await db_session.flush()
    assert img.id in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_live_cut_excludes_the_source(db_session):
    for state in CutState.LIVE:
        img = await _image(db_session)
        await _cut(db_session, img, state=state)
        assert img.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_image_that_is_itself_a_cut_is_excluded(db_session):
    source = await _image(db_session)
    cut = await _image(db_session)
    await _cut(db_session, source, state=CutState.APPROVED, cut_image=cut)
    assert cut.id not in await _ids(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_for_this_version_is_excluded_but_not_for_another(db_session):
    same = await _image(db_session)
    other = await _image(db_session)
    await _cut(db_session, same, state=CutState.REJECTED, version=VER)
    await _cut(db_session, other, state=CutState.REJECTED, version="0")
    ids = await _ids(db_session)
    assert same.id not in ids
    assert other.id in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_a_processing_row_for_the_pipeline_excludes_done_and_failed(db_session):
    done = await _image(db_session)
    failed = await _image(db_session)
    other_pipe = await _image(db_session)
    db_session.add_all([
        ImageProcessingStatus(image_id=done.id, pipeline=PIPE, status="done"),
        ImageProcessingStatus(image_id=failed.id, pipeline=PIPE, status="failed"),
        ImageProcessingStatus(image_id=other_pipe.id, pipeline="recut_detect_v0", status="done"),
    ])
    await db_session.flush()
    ids = await _ids(db_session)
    assert done.id not in ids and failed.id not in ids
    assert other_pipe.id in ids  # an older detector version does not block the new one


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_and_count(db_session):
    for _ in range(3):
        await _image(db_session)
    repo = ImageCutsRepository(db_session)
    total = await repo.count_candidates(PIPE, VER, set())
    assert total >= 3
    assert len(await repo.select_candidates(PIPE, VER, set(), limit=2)) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_insert_detected_writes_a_detected_row(db_session):
    img = await _image(db_session, content_hash="h1")
    await ImageCutsRepository(db_session).insert_detected(
        source_image_id=img.id, source_content_hash="h1", box=(10, 20, 110, 220),
        detector="margin_trim", detector_version=VER, confidence=0.9, detect_run_id=None)
    row = (await db_session.execute(select(ImageCut).where(ImageCut.source_image_id == img.id))).scalar_one()
    assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (10, 20, 100, 200)
    assert row.state == CutState.DETECTED and row.confidence == 0.9 and row.source_content_hash == "h1"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_regions_respects_the_confidence_floor(db_session):
    img = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=img.id, text="hi", confidence=0.9, bbox=[[0, 0], [5, 0], [5, 5], [0, 5]]),
        OCRText(image_id=img.id, text="lo", confidence=0.1, bbox=[[9, 9], [10, 9], [10, 10], [9, 10]]),
        OCRText(image_id=img.id, text="nb", confidence=0.9, bbox=None),
    ])
    await db_session.flush()
    regions = await ImageCutsRepository(db_session).ocr_regions(img.id, 0.4)
    assert regions == [[[0, 0], [5, 0], [5, 5], [0, 5]]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_repository.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'repository.image_cuts'`.

- [ ] **Step 3: Write the implementation**

```python
# repository/image_cuts.py
"""Data access for the screenshot recut epic's detection step (R2). Never commits: the caller (the batch job)
controls commit timing."""
import uuid

from sqlalchemy import exists, func, select

from Storage.models import CutState, Image, ImageCut, ImageExtras, ImageProcessingStatus, OCRText


class ImageCutsRepository:

    def __init__(self, session):
        self.session = session

    def _candidates(self, pipeline: str, detector_version: str, exclude_ids):
        """Active images with real dimensions that are not flagged, not in `exclude_ids` (the frozen evaluation
        corpus), have no live cut as source, are not themselves a cut, were not rejected for this detector
        version, and have no processing row for `pipeline` (done or failed)."""
        live_source = exists().where(ImageCut.source_image_id == Image.id, ImageCut.state.in_(CutState.LIVE))
        is_cut = exists().where(ImageCut.cut_image_id == Image.id)
        rejected_here = exists().where(
            ImageCut.source_image_id == Image.id,
            ImageCut.state == CutState.REJECTED,
            ImageCut.detector_version == detector_version,
        )
        checked = exists().where(ImageProcessingStatus.image_id == Image.id, ImageProcessingStatus.pipeline == pipeline)
        flagged = exists().where(ImageExtras.image_id == Image.id, ImageExtras.flagged.is_(True))
        query = (
            select(Image.id, Image.filename, Image.content_hash)
            .where(Image.status == "active", Image.width.isnot(None), Image.height.isnot(None))
            .where(~live_source, ~is_cut, ~rejected_here, ~checked, ~flagged)
        )
        if exclude_ids:
            query = query.where(Image.id.not_in(list(exclude_ids)))
        return query

    async def select_candidates(self, pipeline: str, detector_version: str, exclude_ids, limit: int | None = None):
        query = self._candidates(pipeline, detector_version, exclude_ids).order_by(Image.id)
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).fetchall()

    async def count_candidates(self, pipeline: str, detector_version: str, exclude_ids) -> int:
        sub = self._candidates(pipeline, detector_version, exclude_ids).subquery()
        return (await self.session.execute(select(func.count()).select_from(sub))).scalar_one()

    async def insert_detected(self, *, source_image_id: uuid.UUID, source_content_hash: str | None, box,
                              detector: str, detector_version: str, confidence: float,
                              detect_run_id: uuid.UUID | None) -> None:
        x0, y0, x1, y1 = box
        self.session.add(ImageCut(
            source_image_id=source_image_id, source_content_hash=source_content_hash,
            crop_x=x0, crop_y=y0, crop_w=x1 - x0, crop_h=y1 - y0,
            detector=detector, detector_version=detector_version, confidence=confidence,
            detect_run_id=detect_run_id, state=CutState.DETECTED,
        ))
        await self.session.flush()

    async def ocr_regions(self, image_id: uuid.UUID, confidence_min: float) -> list:
        """Raw `bbox` values of the image's OCR regions at or above `confidence_min`."""
        result = await self.session.execute(
            select(OCRText.bbox).where(
                OCRText.image_id == image_id, OCRText.confidence >= confidence_min, OCRText.bbox.isnot(None)
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 4: Run to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_repository.py -q`
Expected: PASS. If a test fails because earlier tests in the file left rows behind, check that `db_session` rolls back per test (see `tests/integration/conftest.py`) before touching the repository.

- [ ] **Step 5: Commit**

```bash
git add repository/image_cuts.py tests/integration/test_image_cuts_repository.py
git commit -m "feat(recut): image cuts repository (candidates, insert, OCR regions)"
```

---

### Task 4: The job and its registration

**Files:**
- Create: `batch/detect_recut_crops.py`
- Modify: `environments/batch_registry.yaml` (add entry after `classify_text_heavy`)
- Test: `tests/integration/test_detect_recut_crops.py`, `batch/tests/test_detect_recut_crops_main.py`

**Interfaces:**
- Consumes: `batch.utils.crop_detection` (Task 1), `ImageCutsRepository` (Task 3), `settings.RECUT.*` (Task 2), `ImageProcessingStatusRepository(session, pipeline)` with `mark_done_by_id(image_id)` and `record_failure(image_id, error)`, `tracked_run`/`finish_existing_run`, `BatchRunRepository.update_stats`.
- Produces:
  - `KIND = "detect_recut_crops"`, `PIPELINE`.
  - `load_corpus_ids(path=CORPUS_PATH) -> set[uuid.UUID]`.
  - `params_from_settings() -> DetectParams`.
  - `async run(session, base_path, params, ocr_confidence_min, run_id, corpus_ids, commit_interval, limit=None) -> SimpleMetricsListener`.
  - `async main(trigger="manual", run_id=None, limit=None)`.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_detect_recut_crops.py
import uuid

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.detect_recut_crops import PIPELINE, load_corpus_ids, run
from batch.utils.crop_detection import DETECTOR, DETECTOR_VERSION, DetectParams
from repository.image_cuts import ImageCutsRepository
from Storage.models import BatchRun, CutState, Image, ImageCut, ImageProcessingStatus, OCRText

PARAMS = DetectParams(min_removed_share=0.07, max_removed_share=0.75, flat_tolerance=7.0, ocr_overlap_max=0.15)


def _write_bordered(path, w=400, h=300, border=30):
    arr = np.full((h, w, 3), 255, dtype=np.uint8)
    arr[border:h - border, border:w - border] = np.random.default_rng(1).integers(0, 256, (h - 2 * border, w - 2 * border, 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(path, "PNG")


def _write_plain(path):
    PILImage.new("RGB", (400, 300), (255, 255, 255)).save(path)


async def _image(s, tmp_path, name, writer, **kw):
    kw.setdefault("width", 400)
    kw.setdefault("height", 300)
    img = Image(filename=name, status="active", **kw)
    s.add(img)
    await s.flush()
    if writer:
        writer(tmp_path / name)
    return img


async def _go(s, tmp_path, run_id=None, corpus=frozenset(), limit=None):
    m = await run(s, str(tmp_path), PARAMS, 0.4, run_id, set(corpus), 200, limit=limit)
    await s.commit()
    return m.counters_dict()


async def _cuts(s, image_id):
    return (await s.execute(select(ImageCut).where(ImageCut.source_image_id == image_id))).scalars().all()


async def _only(s, tmp_path, ids):
    """Corpus ids that make a run see only `ids`: every other current candidate is excluded."""
    rows = await ImageCutsRepository(s).select_candidates(PIPELINE, DETECTOR_VERSION, set())
    return {r.id for r in rows} - set(ids)


@pytest.mark.asyncio(loop_scope="session")
async def test_bordered_image_gets_a_detected_proposal(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered, content_hash="h")
    stats = await _go(db_session, tmp_path, corpus=await _only(db_session, tmp_path, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert (cut.crop_x, cut.crop_y, cut.crop_w, cut.crop_h) == (30, 30, 340, 240)
    assert cut.state == CutState.DETECTED and cut.detector == DETECTOR and cut.detector_version == DETECTOR_VERSION
    assert cut.source_content_hash == "h" and 0.6 <= cut.confidence <= 1.0
    status = await db_session.get(ImageProcessingStatus, {"image_id": img.id, "pipeline": PIPELINE})
    assert status.status == "done"
    assert stats["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_add_up_across_every_outcome(tmp_path, db_session):
    good = await _image(db_session, tmp_path, "good.png", _write_bordered)
    plain = await _image(db_session, tmp_path, "plain.png", _write_plain)
    clipped = await _image(db_session, tmp_path, "clipped.png", _write_bordered)
    missing = await _image(db_session, tmp_path, "missing.png", None)
    # a text region spilling out of the content box (content is x 30..370): 30% of it lies outside
    db_session.add(OCRText(image_id=clipped.id, text="t", confidence=0.9, bbox=[[0, 100], [100, 100], [100, 130], [0, 130]]))
    await db_session.flush()
    corpus = await _only(db_session, tmp_path, {good.id, plain.id, clipped.id, missing.id})

    stats = await _go(db_session, tmp_path, corpus=corpus)

    assert stats["images_selected"] == 4
    assert stats["proposed"] == 1 and stats["no_cut"] == 1 and stats["clipped_text"] == 1 and stats["unreadable"] == 1
    assert stats["images_selected"] == stats["proposed"] + stats["no_cut"] + stats["clipped_text"] + stats["unreadable"]
    assert stats["images_remaining"] == 0
    assert (await db_session.get(ImageProcessingStatus, {"image_id": clipped.id, "pipeline": PIPELINE})).status == "done"
    assert await _cuts(db_session, clipped.id) == []
    bad = await db_session.get(ImageProcessingStatus, {"image_id": missing.id, "pipeline": PIPELINE})
    assert bad.status == "failed" and bad.error_message


@pytest.mark.asyncio(loop_scope="session")
async def test_second_run_adds_nothing(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered)
    corpus = await _only(db_session, tmp_path, {img.id})
    await _go(db_session, tmp_path, corpus=corpus)
    again = await _go(db_session, tmp_path, corpus=corpus)
    assert again["images_selected"] == 0 and again.get("proposed", 0) == 0
    assert len(await _cuts(db_session, img.id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_reports_images_remaining(tmp_path, db_session):
    imgs = [await _image(db_session, tmp_path, f"l{i}.png", _write_bordered) for i in range(3)]
    corpus = await _only(db_session, tmp_path, {i.id for i in imgs})
    stats = await _go(db_session, tmp_path, corpus=corpus, limit=2)
    assert stats["images_selected"] == 2
    assert stats["images_remaining"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stale_database_size_still_gives_a_box_inside_the_file(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "a.png", _write_bordered, width=10, height=10)
    await _go(db_session, tmp_path, corpus=await _only(db_session, tmp_path, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert cut.crop_x + cut.crop_w <= 400 and cut.crop_y + cut.crop_h <= 300


@pytest.mark.asyncio(loop_scope="session")
async def test_run_id_is_stored_on_the_proposal(tmp_path, db_session):
    run_row = BatchRun(kind="detect_recut_crops", trigger="manual", status="completed")
    db_session.add(run_row)
    await db_session.flush()
    img = await _image(db_session, tmp_path, "a.png", _write_bordered)
    await _go(db_session, tmp_path, run_id=run_row.run_id, corpus=await _only(db_session, tmp_path, {img.id}))
    (cut,) = await _cuts(db_session, img.id)
    assert cut.detect_run_id == run_row.run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_a_new_detector_version_checks_images_again(tmp_path, db_session):
    img = await _image(db_session, tmp_path, "p.png", _write_plain)
    await _go(db_session, tmp_path, corpus=await _only(db_session, tmp_path, {img.id}))
    repo = ImageCutsRepository(db_session)
    assert img.id not in {r.id for r in await repo.select_candidates(PIPELINE, DETECTOR_VERSION, set())}
    assert img.id in {r.id for r in await repo.select_candidates("recut_detect_v2", "2", set())}


def test_load_corpus_ids_reads_the_committed_corpus():
    ids = load_corpus_ids()
    assert len(ids) == 1500 and all(isinstance(i, uuid.UUID) for i in ids)
```

`_only` restricts a run to the test's own images by passing every other current candidate in as `corpus_ids`, because `ocrdb_test` may hold rows from other tests. `_go` commits after the run; check in `tests/integration/conftest.py` that `db_session` cleans up after such commits, and if it does not, delete the test's rows at the end of each test.

```python
# batch/tests/test_detect_recut_crops_main.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.detect_recut_crops as module
from batch.registry import BatchRegistry


def _session_factory():
    session = MagicMock()
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _metrics():
    m = MagicMock()
    m.counters_dict.return_value = {"proposed": 1}
    return m


@pytest.mark.asyncio
async def test_main_with_run_id_finishes_the_existing_run():
    run_id = uuid.uuid4()
    finish = MagicMock()
    finish.return_value.__aenter__ = AsyncMock(return_value=None)
    finish.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(module, "finish_existing_run", finish), \
         patch.object(module, "AsyncSessionLocal", _session_factory()), \
         patch.object(module, "BatchRunRepository", return_value=MagicMock(update_stats=AsyncMock())), \
         patch.object(module, "load_corpus_ids", return_value=set()), \
         patch.object(module, "settings", MagicMock()), \
         patch.object(module, "params_from_settings", return_value=None), \
         patch.object(module, "run", AsyncMock(return_value=_metrics())):
        await module.main(trigger="manual", run_id=run_id)
    finish.assert_called_once_with(run_id)


@pytest.mark.asyncio
async def test_main_without_run_id_tracks_its_own_run_and_stores_the_stats():
    new_id = uuid.uuid4()
    tracked = MagicMock()
    tracked.return_value.__aenter__ = AsyncMock(return_value=new_id)
    tracked.return_value.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock(update_stats=AsyncMock())
    with patch.object(module, "tracked_run", tracked), \
         patch.object(module, "AsyncSessionLocal", _session_factory()), \
         patch.object(module, "BatchRunRepository", return_value=repo), \
         patch.object(module, "load_corpus_ids", return_value=set()), \
         patch.object(module, "settings", MagicMock()), \
         patch.object(module, "params_from_settings", return_value=None), \
         patch.object(module, "run", AsyncMock(return_value=_metrics())):
        await module.main(trigger="manual")
    tracked.assert_called_once_with(kind="detect_recut_crops", trigger="manual")
    repo.update_stats.assert_awaited_once_with(new_id, proposed=1)


def test_registered_in_batch_registry():
    assert BatchRegistry().get("detect_recut_crops") == {
        "module": "batch.detect_recut_crops", "kind": "detect_recut_crops"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_detect_recut_crops.py -q` and `.venv311/Scripts/python.exe -m pytest batch/tests/test_detect_recut_crops_main.py -q`
Expected: both FAIL at collection with `ModuleNotFoundError: No module named 'batch.detect_recut_crops'`.

- [ ] **Step 3: Write the job**

```python
# batch/detect_recut_crops.py
"""Screenshot recut, crop detection (R2; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md).
Finds active images with a flat margin around the meme and stores one `detected` proposal per image in
image_cuts. Creates no files, changes no image status and records no metric event (R3 does). Checked images
are remembered in image_processing_status under a pipeline name that carries the detector version, so a re-run
only sees new images and a new detector version sees everything again. Safe to re-run."""
import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.crop_detection import (DETECTOR, DETECTOR_VERSION, DetectParams, clips_ocr,
                                        detect_margin_box)
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_cuts import ImageCutsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from Storage.db import AsyncSessionLocal

KIND = "detect_recut_crops"
PIPELINE = f"recut_detect_v{DETECTOR_VERSION}"
CORPUS_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "gold" / "corpus.json"


def load_corpus_ids(path: Path = CORPUS_PATH) -> set[uuid.UUID]:
    """Image ids of the frozen evaluation corpus (FR-17: never proposed). The ids are for `general`; on the
    other environments they match nothing. A missing file is an error, not an empty set, so the exclusion can
    never silently switch off."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {uuid.UUID(item["imageId"]) for item in data["images"]}


def params_from_settings() -> DetectParams:
    r = settings.RECUT
    return DetectParams(min_removed_share=r.MIN_REMOVED_SHARE, max_removed_share=r.MAX_REMOVED_SHARE,
                        flat_tolerance=r.FLAT_TOLERANCE, ocr_overlap_max=r.OCR_OVERLAP_MAX)


async def run(session, base_path: str, params: DetectParams, ocr_confidence_min: float,
              run_id: uuid.UUID | None, corpus_ids, commit_interval: int,
              limit: int | None = None) -> SimpleMetricsListener:
    cuts = ImageCutsRepository(session)
    processed = ImageProcessingStatusRepository(session, PIPELINE)
    total = await cuts.count_candidates(PIPELINE, DETECTOR_VERSION, corpus_ids)
    rows = await cuts.select_candidates(PIPELINE, DETECTOR_VERSION, corpus_ids, limit=limit)
    print(f"Images to check: {len(rows)} of {total}")

    metrics = SimpleMetricsListener()
    metrics.add("images_selected", len(rows))
    metrics.add("images_remaining", total - len(rows))

    for i, row in enumerate(rows, start=1):
        try:
            proposal = detect_margin_box(os.path.join(base_path, row.filename), params)
        except Exception as e:  # one bad file never fails the run; it is recorded and counted
            await processed.record_failure(row.id, f"{type(e).__name__}: {e}"[:300])
            metrics.increment("unreadable")
        else:
            if proposal is None:
                await processed.mark_done_by_id(row.id)
                metrics.increment("no_cut")
            elif clips_ocr(proposal.box, await cuts.ocr_regions(row.id, ocr_confidence_min), params.ocr_overlap_max):
                await processed.mark_done_by_id(row.id)
                metrics.increment("clipped_text")
            else:
                await cuts.insert_detected(
                    source_image_id=row.id, source_content_hash=row.content_hash, box=proposal.box,
                    detector=DETECTOR, detector_version=DETECTOR_VERSION, confidence=proposal.confidence,
                    detect_run_id=run_id)
                await processed.mark_done_by_id(row.id)
                metrics.increment("proposed")
        if i % commit_interval == 0:
            await session.commit()
    return metrics


async def _execute(run_id: uuid.UUID, limit: int | None) -> SimpleMetricsListener:
    corpus_ids = load_corpus_ids()
    async with AsyncSessionLocal() as session:
        metrics = await run(session, settings.BASE_PATH, params_from_settings(), settings.OCR.CONFIDENCE_MIN,
                            run_id, corpus_ids, settings.RECUT.COMMIT_INTERVAL, limit=limit)
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
    print("Crop detection results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="check at most this many images this run")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(limit=args.limit))
```

Registry entry, added to `environments/batch_registry.yaml` after `classify_text_heavy`:

```yaml
detect_recut_crops:
  module: batch.detect_recut_crops
  kind: detect_recut_crops
```

Note on `_execute` with `finish_existing_run`: `finish_existing_run` yields nothing and `tracked_run` yields the new run id, so `main` passes each its own id. `update_stats` and the detection share one session and one final commit, as in `fix_image_formats`.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_detect_recut_crops.py tests/integration/test_image_cuts_repository.py -q` then `.venv311/Scripts/python.exe -m pytest batch/tests/test_detect_recut_crops_main.py batch/tests/test_crop_detection.py batch/tests/test_recut_settings.py -q`
Expected: PASS. A failure in `test_load_corpus_ids_reads_the_committed_corpus` means the corpus file changed size; assert on the file's own count, not 1500, in that case.

- [ ] **Step 5: Run the whole test roots, then commit**

Run (separately): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/ -q`; `.venv311/Scripts/python.exe -m pytest batch/tests -q`; `.venv311/Scripts/python.exe -m pytest tests/docs -q`.
Expected: all green. `batch/tests/test_registry.py` or an inventory guard may count registry entries; if one fails, update the count it checks in the same commit.

```bash
git add batch/detect_recut_crops.py environments/batch_registry.yaml tests/integration/test_detect_recut_crops.py batch/tests/test_detect_recut_crops_main.py
git commit -m "feat(recut): detect_recut_crops batch job, registered for the admin trigger"
```

---

### Task 5: Guard calibration (outside CI)

**Files:**
- Create: `batch/tools/recut_spike/calibrate_guard.py` (throwaway; removed in Task 6)
- Modify: `environments/settings.yaml` (`recut.ocr_overlap_max`), the spec (addendum, Task 6)

**Interfaces:**
- Consumes: `batch.utils.crop_detection.region_rect`; `evaluation/gold/recut/spike-round{1,2}-{judgments,key}.json`; `DATABASE_URL_READONLY` and `BASE_PATH` from `environments/.env.general` (read-only role; never `DATABASE_URL`).
- Produces: a table of guard drop rates by grade for a grid of thresholds, and a decision.

No TDD step: this is a measurement, not shipped code. The script prints numbers; the decision rule is fixed in advance (spec section 5).

- [ ] **Step 1: Write the script**

```python
# batch/tools/recut_spike/calibrate_guard.py
"""THROWAWAY. For each judged margin-kind box, the largest share of any OCR region lying outside the box, and how a
threshold sweep would have treated each grade. Read-only (DATABASE_URL_READONLY)."""
import asyncio
import json
from pathlib import Path

import asyncpg

from batch.tools.recut_spike.sample import env  # reads environments/.env.general
from batch.utils.crop_detection import region_rect

GOLD = Path("evaluation/gold/recut")
GRID = [0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]


def judged():
    items = []
    k1 = json.loads((GOLD / "spike-round1-key.json").read_text(encoding="utf-8"))
    j1 = json.loads((GOLD / "spike-round1-judgments.json").read_text(encoding="utf-8"))
    for k, v in j1.items():
        by = set(k1[k]["by"])
        if k1[k]["box"] and ("A" in by or "C" in by) and v in "012":
            items.append((k1[k]["id"], k1[k]["box"], v))
    k2 = json.loads((GOLD / "spike-round2-key.json").read_text(encoding="utf-8"))
    j2 = json.loads((GOLD / "spike-round2-judgments.json").read_text(encoding="utf-8"))
    for k, v in j2.items():
        if k2[k]["band"].startswith("margin") and v in "012":
            items.append((k2[k]["id"], k2[k]["box"], v))
    return items


def worst_outside(box, regions):
    bx0, by0, bx1, by1 = box
    worst = 0.0
    for raw in regions:
        r = region_rect(raw)
        if r is None or (r[2] - r[0]) * (r[3] - r[1]) <= 0:
            continue
        x0, y0, x1, y1 = r
        area = (x1 - x0) * (y1 - y0)
        inter = max(0.0, min(x1, bx1) - max(x0, bx0)) * max(0.0, min(y1, by1) - max(y0, by0))
        worst = max(worst, (area - inter) / area)
    return worst


async def main():
    items = judged()
    dsn = env("DATABASE_URL_READONLY").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "select image_id::text as id, bbox from ocr_texts where image_id = any($1::uuid[]) and confidence >= 0.4 and bbox is not null",
            [i for i, _, _ in items])
    finally:
        await conn.close()
    regions = {}
    for r in rows:
        regions.setdefault(r["id"], []).append(json.loads(r["bbox"]) if isinstance(r["bbox"], str) else r["bbox"])
    scored = [(worst_outside(box, regions.get(i, [])), g) for i, box, g in items]
    n = {g: sum(1 for _, x in scored if x == g) for g in "012"}
    print("judged margin boxes:", n)
    print("threshold | dropped grade 2 | dropped grade 1 | dropped grade 0")
    for t in GRID:
        d = {g: sum(1 for w, x in scored if x == g and w > t) for g in "012"}
        print(f"{t:>9} | {d['2']}/{n['2']} | {d['1']}/{n['1']} | {d['0']}/{n['0']}")
    bad = n["1"] + n["0"]
    ok = [t for t in GRID if sum(1 for w, x in scored if x in "01" and w > t) >= bad / 2
          and sum(1 for w, x in scored if x == "2" and w > t) <= 0.05 * n["2"]]
    print("decision: keep guard at", max(ok) if ok else "NONE (switch off with 1.0)")


asyncio.run(main())
```

- [ ] **Step 2: Run it**

Run: `PYTHONIOENCODING=utf-8 .venv311/Scripts/python.exe -m batch.tools.recut_spike.calibrate_guard`
Expected: a table and a decision line. The script opens one read-only connection and runs one SELECT. Paste the output into the ledger.

- [ ] **Step 3: Apply the decision**

If the decision line names a threshold, set `recut.ocr_overlap_max` in `environments/settings.yaml` to it (it may stay 0.15). If it says NONE, set `ocr_overlap_max: 1.0`, which switches the guard off. In both cases the number and the table go into the spec addendum in Task 6. Note that with only about 4 judged bad margin cuts, the evidence is thin; say so in the addendum.

- [ ] **Step 4: Commit**

```bash
git add environments/settings.yaml batch/tools/recut_spike/calibrate_guard.py
git commit -m "chore(recut): calibrate the OCR clipping guard on the labelled sample"
```

---

### Task 6: Documentation, spec update, cleanup

**Files:**
- Modify: `docs/batches/inventory.md`, `CLAUDE.md`, the R2 spec (status, plan link, flagged-image rule, calibration addendum), the findings note (code location), `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`
- Delete: `batch/tools/recut_spike/`

- [ ] **Step 1: Inventory and CLAUDE.md**

In `docs/batches/inventory.md`, add a row under the maintenance table, next to `fix_image_formats`:

```
| `detect_recut_crops` | Screenshot recut: stores a `detected` `image_cuts` proposal for active images with a flat margin (R2) | `detect_recut_crops` | yes | no | `--limit`; checks each image once per detector version (`recut_detect_v1`); skips the frozen evaluation corpus, flagged images and images that already have a live cut |
```

and change the class-count line from 28 admin-triggerable and 32 registry entries to 29 and 33. In `CLAUDE.md`, add after the `fix_image_formats` entry in the batch list a short entry for `detect_recut_crops` (what it stores, that it creates no files or status changes, `--limit`, the versioned pipeline name, admin-triggerable and manual-trigger only), linking the R2 spec.

- [ ] **Step 2: Spec, findings and tracker**

R2 spec: set `Status: planned` and add `Plan: docs/superpowers/plans/2026-10-24-r2-crop-detection.md`; add "flagged images (`image_extras.flagged`)" to the candidate list in section 2; add an addendum with the calibration table and decision from Task 5 and the caveat that few bad margin cuts were judged. Findings note: change "Throwaway code: `batch/tools/recut_spike/`" to say the code was deleted when R2 shipped and is recoverable from commit `6d68dad` and its successors on branch `r2-crop-detection`. Tracker: R2 row status `implementation`.

- [ ] **Step 3: Delete the throwaway code**

```bash
git rm -r batch/tools/recut_spike
```

- [ ] **Step 4: Verify and commit**

Run (separately): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/ -q`; `.venv311/Scripts/python.exe -m pytest batch/tests -q`; `.venv311/Scripts/python.exe -m pytest tests/docs -q`; `cd Backend && ../.venv311/Scripts/python.exe -m pytest -q`.
Expected: all green, nothing imports `batch.tools.recut_spike`.

```bash
git add -A docs CLAUDE.md batch environments
git commit -m "docs(recut): R2 inventory, CLAUDE.md, spec addendum; remove the spike code"
```

---

## Self-review

- **Spec coverage:** section 1 decisions → Tasks 1, 2, 4; section 2 candidates → Task 3, detection and guard → Task 1, outcomes table → Task 4 (`test_stats_add_up_across_every_outcome`); section 3 job and registration → Task 4 and Task 6; section 4 settings → Task 2; section 5 calibration → Task 5; section 6 tests → Tasks 1 to 4 (the "reproduces the spike's box within 2 px" fixture is `test_large_image_box_is_scaled_back_within_two_pixels`; the "missing key is a startup error" test became a check that the common file has every key, because `settings.RECUT.X` raises on access when a key is missing); section 8 rollout is a handoff item, not a task (no live run here). The flagged-image rule is a plan addition recorded in Global Constraints and Task 6.
- **Placeholders:** none. Task 6 prose steps name the exact rows and edits.
- **Type consistency:** `DetectParams`, `Proposal.box`, `select_candidates(pipeline, detector_version, exclude_ids, limit)`, `insert_detected(...)` keyword names, `run(...)` argument order and `main(trigger, run_id, limit)` are used identically in Tasks 1, 3 and 4.
