# Text-Heavy Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 4 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** Compute the three empirically-validated text-heavy signals (axis-aligned OCR coverage ratio,
background dominant-color fraction, text ink-color consistency) for every image with OCR text and real
dimensions, and store both the raw signals and the final classification in a new
`image_classifications` table.

**Architecture:** A pure, DB-free signal-computation module (`batch/utils/text_heavy_classifier.py`,
mirroring `image_format_fix.py`'s separation of pixel logic from DB orchestration) feeds a new repository
and a new batch script, modeled on `build_tags_from_ocr.py`'s exact shape. Task 1 lands the schema (model
+ migration, proved on the test DB — no live-data risk, this is a brand-new empty table). Task 2 builds
and unit-tests the pure classifier logic, independent of the DB entirely. Task 3 wires the repository and
batch script together with real-DB integration tests. Task 4 — the live rollout — is controller-executed,
gated on the user's explicit go-ahead before touching any live database.

**Tech Stack:** SQLAlchemy Core upsert (`postgresql.insert(...).on_conflict_do_update(...)`), Pillow +
numpy (both already dependencies used elsewhere in `batch/`), pytest with real Pillow-generated fixture
images (no mocking, matching `test_image_format_fix.py`'s style).

**Spec:** `docs/superpowers/specs/2026-09-15-text-heavy-classifier.md`

## Global Constraints

- `ImageClassification`'s primary key is the composite `(image_id, classifier)` — one row per image per
  classifier, upserted on re-run. `classifier` carries its own version in the string value
  (`"text_heavy_v1"`), not a separate version column — a future threshold/algorithm change is a new
  `classifier` string, never a schema change.
- The three signal thresholds (`COVERAGE_RATIO_MIN = 0.50`, `DOMINANT_COLOR_FRAC_MIN = 0.65`,
  `INK_CONSISTENCY_MIN = 0.50`) and the axis-alignment test (`polygon_area / aabb_area > 0.95`) are
  exact values from the spec's empirical validation — do not adjust them without re-reading the spec's
  Problem section for why each one is where it is.
- **Refinement over the spec's Design §4 sketch, for testability:** the spec's batch-script code creates
  its own DB session inside `_process()`. Every other batch script in this codebase
  (`fix_image_formats.py`, `ingest_validate_formats.py`) instead separates a testable
  `run(session, base_path, ..., status) -> SimpleMetricsListener` (takes an externally-provided session,
  called directly by integration tests with the `db_session` fixture) from `main()` (creates the session,
  wraps in `tracked_run`). This plan's Task 3 uses that established `run()`/`main()` split — a mechanical
  correction to match this codebase's real convention exactly, not a change to the spec's approach,
  formulas, or thresholds.
- No new dependency — Pillow and numpy are both already used in `batch/` (`batch/ocr_preprocess.py`
  already imports `numpy as np`).
- Per this repo's testing gotcha: never combine `Backend/tests/`, `tests/integration/`, and
  `batch/tests/` in one `pytest` invocation — run them as separate commands.
- **Never hand a subagent the main `DATABASE_URL` for any of `general`'s/`metal`'s/`it`'s environments.**
  Tasks 1-3 use only the disposable test database
  (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`). Task 4 is controller-only for
  exactly this reason.

---

## File Structure

**Modify:** `Storage/models.py` (new `ImageClassification` model + `Image.classifications` relationship).
**Create:** one new file under `Storage/alembic/versions/` (name assigned by `alembic revision
--autogenerate`).
**Create:** `batch/utils/text_heavy_classifier.py` (pure signal computation).
**Create:** `repository/image_classifications.py`.
**Create:** `batch/classify_text_heavy.py`.
**Create:** `batch/tests/test_text_heavy_classifier.py`.
**Create:** `tests/integration/test_classify_text_heavy.py`.
**Modify:** `CLAUDE.md` (new batch-pipeline entry).
**Docs — modify:** the spec's status line (final task, Task 4).

---

## Task 1: Model + migration, proved against the test database

**Files:**
- Modify: `Storage/models.py:62-64` (insert the new class after `Image`'s relationship block ends, and
  add the `classifications` relationship line inside that block)
- Create: `Storage/alembic/versions/<generated>_add_image_classifications_table.py`

**Interfaces:**
- Produces: `ImageClassification` ORM model (`image_classifications` table:
  `image_id, classifier, result, details, computed_at`), and `Image.classifications` relationship.
  Consumed by Task 3's repository.

- [ ] **Step 1: Add the `classifications` relationship to `Image`**

In `Storage/models.py`, the `Image` class's relationship block currently ends at lines 62-64:

```python
    description_note_lemmas = relationship(
        "DescriptionNoteLemma", back_populates="image", cascade="all, delete-orphan"
    )
```

Add immediately after:

```python
    classifications = relationship("ImageClassification", back_populates="image", cascade="all, delete-orphan")
```

- [ ] **Step 2: Add the `ImageClassification` model**

Insert as a new class — a natural location is right after `ImageExtras` (currently ending at line 424,
just before `class DescriptionNote(Base):` at line 427), matching the "auxiliary per-image data" grouping:

```python
class ImageClassification(Base):
    __tablename__ = "image_classifications"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    classifier = Column(String, primary_key=True)
    result = Column(String, nullable=False)
    details = Column(JSON)
    computed_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="classifications")
```

`Column`, `String`, `ForeignKey`, `DateTime`, `JSON`, `func`, `UUID`, and `relationship` are all already
imported at the top of this file (used by neighboring classes) — no new imports needed.

- [ ] **Step 3: Generate the migration**

From `Storage/`, with the test DB's URL set (needed only because `Storage/alembic/env.py` imports
`Storage.config.DATABASE_URL` at module level — not because this command connects to it):

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic revision --autogenerate -m "add image_classifications table"
```

This is a brand-new, empty table — no `CONCURRENTLY`/`autocommit_block()` concern like the dimension-
capture spec's index migration. A plain autogenerated migration is correct here; do not hand-edit the
generated `upgrade()`/`downgrade()` bodies unless the diff is wrong (see Step 5).

- [ ] **Step 4: Apply to the test database and verify**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic current
```

If this isn't already at the head from before your new migration, run
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head` first, then
re-run `alembic current` to confirm.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
```

Expected: succeeds, ends at your new revision.

```bash
psql "postgresql://ocr:ocr@localhost:5432/ocrdb_test" -c "\d image_classifications"
```

Expected: table exists with columns `image_id, classifier, result, details, computed_at`, composite
primary key on `(image_id, classifier)`.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic downgrade -1
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
```

Expected: both succeed — proves `downgrade()` cleanly drops the table and `upgrade()` cleanly recreates
it. Leave the test DB at `head` (upgraded) when done.

- [ ] **Step 5: Verify model/migration parity**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic revision --autogenerate -m "verify-empty-diff-DELETE-ME"
```

Open the newly generated file: its `upgrade()`/`downgrade()` bodies should both be empty — proof that
`Storage/models.py`'s new class matches what the migration actually created. **Delete this
verification-only file** — it must not be committed.

If the diff is NOT empty, fix `Storage/models.py` (never hand-edit the migration to match a mistaken
model, unless the model is what's wrong) and repeat this step.

- [ ] **Step 6: Full backend + integration regression (unmodified — this proves the change is
  behavior-neutral)**

```bash
cd Backend && pytest -q
```

Expected: PASS, unchanged from before this task — no `Backend/` code changed, only schema.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q
```

Expected: PASS, unchanged — this task adds a table nothing else reads or writes yet.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/
git commit -m "feat: add image_classifications table (test-db verified)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

Confirm `git status` shows no leftover verification-only migration file from Step 5 before committing.

---

## Task 2: Pure classifier logic (no DB)

**Files:**
- Create: `batch/utils/text_heavy_classifier.py`
- Create: `batch/tests/test_text_heavy_classifier.py`

**Interfaces:**
- Produces: `CLASSIFIER_NAME: str`, `TEXT_HEAVY`/`NOT_TEXT_HEAVY: str` constants, `Region` dataclass
  (`aabb: tuple[float,float,float,float]`, `area: float`),
  `filter_axis_aligned_regions(bboxes: list) -> list[Region]`,
  `coverage_ratio(regions, width, height) -> float`,
  `dominant_color_fraction(img: PILImage.Image) -> float`,
  `ink_consistency(img, regions) -> float`,
  `classify(base_path, filename, width, height, bboxes) -> tuple[str, dict] | None`. Consumed by Task 3's
  batch script.
- Consumes: nothing from earlier tasks — fully independent of Task 1's schema.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_text_heavy_classifier.py`:

```python
"""Unit tests for batch/utils/text_heavy_classifier.py -- pure signal computation, no DB. Real
Pillow-generated fixture images throughout, matching test_image_format_fix.py's no-mocking style."""
import os

import numpy as np
from PIL import Image as PILImage

from batch.utils.text_heavy_classifier import (
    NOT_TEXT_HEAVY,
    TEXT_HEAVY,
    Region,
    classify,
    coverage_ratio,
    dominant_color_fraction,
    filter_axis_aligned_regions,
    ink_consistency,
)


# --------------------------------------------------------------------------
# filter_axis_aligned_regions
# --------------------------------------------------------------------------

def test_axis_aligned_bbox_survives():
    bbox = [[10, 10], [50, 10], [50, 30], [10, 30]]
    regions = filter_axis_aligned_regions([bbox])
    assert len(regions) == 1
    assert regions[0].aabb == (10.0, 10.0, 50.0, 30.0)
    assert regions[0].area == (50 - 10) * (30 - 10)


def test_rotated_bbox_is_discarded():
    # A square rotated ~30 degrees -- polygon area << AABB area
    cx, cy, half = 50, 50, 20
    angle = np.radians(30)
    corners = []
    for dx, dy in [(-half, -half), (half, -half), (half, half), (-half, half)]:
        rx = cx + dx * np.cos(angle) - dy * np.sin(angle)
        ry = cy + dx * np.sin(angle) + dy * np.cos(angle)
        corners.append([rx, ry])
    regions = filter_axis_aligned_regions([corners])
    assert regions == []


def test_degenerate_zero_area_bbox_is_discarded_without_raising():
    bbox = [[10, 10], [10, 10], [10, 10], [10, 10]]
    regions = filter_axis_aligned_regions([bbox])
    assert regions == []


# --------------------------------------------------------------------------
# coverage_ratio
# --------------------------------------------------------------------------

def test_coverage_ratio_known_areas():
    regions = [Region(aabb=(0, 0, 10, 10), area=100.0), Region(aabb=(10, 0, 30, 10), area=200.0)]
    assert coverage_ratio(regions, width=100, height=10) == 300.0 / 1000.0


def test_coverage_ratio_capped_at_one():
    regions = [Region(aabb=(0, 0, 100, 100), area=10000.0), Region(aabb=(0, 0, 100, 100), area=10000.0)]
    assert coverage_ratio(regions, width=100, height=100) == 1.0


# --------------------------------------------------------------------------
# dominant_color_fraction
# --------------------------------------------------------------------------

def test_dominant_color_fraction_solid_image_is_near_one():
    img = PILImage.new("RGB", (100, 100), (255, 255, 255))
    assert dominant_color_fraction(img) > 0.99


def test_dominant_color_fraction_checkerboard_is_meaningfully_below_one():
    img = PILImage.new("RGB", (100, 100), (255, 255, 255))
    pixels = img.load()
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (128, 128, 128)]
    for x in range(100):
        for y in range(100):
            pixels[x, y] = colors[(x // 10 + y // 10) % len(colors)]
    assert dominant_color_fraction(img) < 0.5


# --------------------------------------------------------------------------
# ink_consistency
# --------------------------------------------------------------------------

def _region_image(size, bg, region_specs):
    """region_specs: [(x0, y0, x1, y1, ink_color, ink_frac), ...] -- fills the bottom `ink_frac`
    portion of each region's AABB with ink_color, leaving the rest as background. Mimics a real
    text line's background/ink pixel ratio (never 100% ink) so _region_ink_color's "minority
    color within the crop" extraction has real background pixels to compare against."""
    img = PILImage.new("RGB", size, bg)
    pixels = img.load()
    regions = []
    for x0, y0, x1, y1, ink, ink_frac in region_specs:
        ink_y0 = y1 - int((y1 - y0) * ink_frac)
        for x in range(x0, x1):
            for y in range(ink_y0, y1):
                pixels[x, y] = ink
        regions.append(Region(aabb=(x0, y0, x1, y1), area=(x1 - x0) * (y1 - y0)))
    return img, regions


def test_ink_consistency_matching_colors_is_near_one():
    img, regions = _region_image(
        (100, 100), (255, 255, 255),
        [(10, 10, 60, 30, (0, 0, 0), 0.3), (10, 40, 80, 60, (0, 0, 0), 0.3)],
    )
    assert ink_consistency(img, regions) > 0.95


def test_ink_consistency_large_minority_different_color_is_lower():
    img, regions = _region_image(
        (100, 100), (255, 255, 255),
        [(10, 10, 90, 40, (0, 0, 0), 0.3), (10, 50, 90, 80, (255, 0, 0), 0.3)],
    )
    assert ink_consistency(img, regions) < 0.6


def test_ink_consistency_returns_sentinel_when_no_ink_color_found():
    img = PILImage.new("RGB", (100, 100), (255, 255, 255))
    regions = [Region(aabb=(0, 0, 100, 100), area=10000.0)]  # solid color -- no second color to be "ink"
    assert ink_consistency(img, regions) == -1.0


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

def _fake_screenshot_bboxes(pixels):
    """8 text-line-shaped bboxes (170x20 each, thin 4px ink stroke near vertical center) on a
    200x200 white image -- bbox-area coverage 0.68 (>0.5), actual painted pixels only 13.6% of
    the image (dominant_color_frac ~0.86, >0.65), all ink black (ink_consistency ~1.0)."""
    bboxes = []
    for i in range(8):
        y0, y1 = 5 + i * 25, 25 + i * 25
        for x in range(10, 180):
            for y in range(y0 + 8, y0 + 12):
                pixels[x, y] = (0, 0, 0)
        bboxes.append([[10, y0], [180, y0], [180, y1], [10, y1]])
    return bboxes


def test_classify_fake_screenshot_is_text_heavy(tmp_path):
    path = os.path.join(str(tmp_path), "fake_screenshot.jpg")
    img = PILImage.new("RGB", (200, 200), (255, 255, 255))
    bboxes = _fake_screenshot_bboxes(img.load())
    img.save(path, "JPEG")

    result, details = classify(str(tmp_path), "fake_screenshot.jpg", 200, 200, bboxes)

    assert result == TEXT_HEAVY
    assert details["coverage_ratio"] > 0.5
    assert details["dominant_color_frac"] > 0.65
    assert details["ink_consistency"] > 0.5


def test_classify_fake_meme_is_not_text_heavy(tmp_path):
    path = os.path.join(str(tmp_path), "fake_meme.jpg")
    img = PILImage.new("RGB", (200, 200), (255, 255, 255))
    pixels = img.load()
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for x in range(200):
        for y in range(200):
            pixels[x, y] = colors[(x // 20 + y // 20) % len(colors)]
    # one small text region -- bbox-area coverage far below the 0.5 gate regardless of the busy
    # multi-color background (which would also fail dominant-color-fraction on its own)
    y0, y1 = 10, 30
    for x in range(10, 60):
        for y in range(y0 + 8, y0 + 12):
            pixels[x, y] = (0, 0, 0)
    bboxes = [[[10, y0], [60, y0], [60, y1], [10, y1]]]
    img.save(path, "JPEG")

    result, _ = classify(str(tmp_path), "fake_meme.jpg", 200, 200, bboxes)

    assert result == NOT_TEXT_HEAVY


def test_classify_unreadable_file_returns_none(tmp_path):
    bboxes = [[[10, 10], [60, 10], [60, 25], [10, 25]]]
    result = classify(str(tmp_path), "does_not_exist.jpg", 200, 200, bboxes)
    assert result is None
```

- [ ] **Step 2: Run to verify RED**

```bash
pytest batch/tests/test_text_heavy_classifier.py -v
```

Expected: every test fails with `ModuleNotFoundError: No module named 'batch.utils.text_heavy_classifier'`
(the module doesn't exist yet).

- [ ] **Step 3: Implement `batch/utils/text_heavy_classifier.py`**

```python
"""Pure signal computation for the text-heavy meme classifier. No DB access -- callers pass in
already-fetched OCR regions and open the image file themselves. See
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md for the empirical validation behind
every formula and threshold here."""
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image as PILImage

CLASSIFIER_NAME = "text_heavy_v1"

TEXT_HEAVY = "text_heavy"
NOT_TEXT_HEAVY = "not_text_heavy"

# Axis-alignment test: for a true rectangle, polygon area == AABB area; any rotation makes the
# AABB strictly larger. 0.95 tolerance absorbs OCR coordinate jitter on genuinely axis-aligned
# detections without admitting meaningfully rotated ones (empirically: 92.1% of confidence-
# filtered regions pass this on real data).
AXIS_ALIGNED_RATIO_MIN = 0.95

# Ink-color extraction: a region's second-most-common quantized color counts as "ink" only if
# it's a real, non-trivial fraction of the crop -- filters out anti-aliasing noise around the
# true background/ink boundary.
INK_MIN_FRACTION = 0.08
INK_COLOR_DISTANCE_MAX = 30.0  # Euclidean RGB distance treated as "the same ink color"

# Thresholds. Empirically grounded (see spec Problem section) but explicitly a *starting point*,
# not a final calibration -- details are stored precisely so these can be revisited against real
# stored data without re-opening every image. All three are AND-ed: coverage separates
# mostly-text from mostly-photo; dominant-color separates flat-background from busy/photo
# backgrounds; ink-consistency separates uniform UI text from multi-colored decorative text.
COVERAGE_RATIO_MIN = 0.50
DOMINANT_COLOR_FRAC_MIN = 0.65
INK_CONSISTENCY_MIN = 0.50


@dataclass
class Region:
    aabb: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    area: float


def _polygon_area(points: np.ndarray) -> float:
    """Shoelace formula. `points` is an (N, 2) array; only the first 4 points are used (every
    OCRText.bbox in this pipeline is a 4-point polygon)."""
    p = points[:4]
    n = len(p)
    return 0.5 * abs(sum(
        p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n)
    ))


def filter_axis_aligned_regions(bboxes: list) -> list[Region]:
    """bboxes is a list of raw OCRText.bbox values (already confidence-filtered by the caller).
    Discards non-axis-aligned (rotated) detections -- see the spec's Key Facts for why this test
    is correct and sufficient, no separate rotation-angle math needed."""
    regions = []
    for bbox in bboxes:
        pts = np.array(bbox, dtype=float)
        if pts.shape[0] < 3:
            continue
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        aabb_area = (x1 - x0) * (y1 - y0)
        if aabb_area <= 0:
            continue
        if _polygon_area(pts) / aabb_area <= AXIS_ALIGNED_RATIO_MIN:
            continue
        regions.append(Region(aabb=(x0, y0, x1, y1), area=aabb_area))
    return regions


def coverage_ratio(regions: list[Region], width: int, height: int) -> float:
    """Sum of axis-aligned region areas / image area, capped at 1.0 -- a small residual (~1.4%
    of images empirically) can still exceed 1.0 from same-region cross-language re-detection;
    capping is an accepted approximation, not a full geometric union (see spec Non-goals)."""
    total = sum(r.area for r in regions)
    return min(total / (width * height), 1.0) if width and height else 0.0


def dominant_color_fraction(img: "PILImage.Image") -> float:
    """Fraction of pixels in the single most common color after an 8-color quantization of a
    150px-wide thumbnail. High for flat-background UI screenshots (plain or single-brand-color),
    low for photographic/busy/gradient backgrounds."""
    small = img.resize((150, max(1, int(150 * img.height / img.width))))
    quant = small.quantize(colors=8, method=PILImage.FASTOCTREE)
    colors = quant.getcolors(maxcolors=1_000_000)
    total = sum(c for c, _ in colors)
    return max(c for c, _ in colors) / total


def _region_ink_color(img: "PILImage.Image", region: Region) -> np.ndarray | None:
    x0, y0, x1, y1 = (int(v) for v in region.aabb)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    crop = img.crop((x0, y0, x1, y1))
    if crop.width * crop.height < 20:
        return None
    quant = crop.quantize(colors=3, method=PILImage.FASTOCTREE).convert("RGB")
    arr = np.array(quant).reshape(-1, 3)
    uniq, counts = np.unique(arr, axis=0, return_counts=True)
    order = np.argsort(-counts)
    uniq, counts = uniq[order], counts[order]
    total = counts.sum()
    if len(uniq) < 2:
        return None
    for i in range(1, len(uniq)):
        if counts[i] / total > INK_MIN_FRACTION:
            return uniq[i].astype(float)
    return None


def ink_consistency(img: "PILImage.Image", regions: list[Region]) -> float:
    """Area-weighted fraction of regions whose ink color matches the single largest region's ink
    color (within INK_COLOR_DISTANCE_MAX). High for uniform UI text (one ink color used
    throughout, regardless of a link/username line in a different but still-uniform color -- the
    largest region, almost always the main body text, anchors the comparison). Low for decorative
    multi-colored lettering. Returns -1.0 (never passes any threshold) if no region yields a
    usable ink color."""
    ink_colors, areas = [], []
    for region in regions:
        color = _region_ink_color(img, region)
        if color is not None:
            ink_colors.append(color)
            areas.append(region.area)
    if not ink_colors:
        return -1.0
    colors_arr = np.array(ink_colors)
    areas_arr = np.array(areas)
    anchor = colors_arr[np.argmax(areas_arr)]
    dists = np.linalg.norm(colors_arr - anchor, axis=1)
    close = areas_arr[dists < INK_COLOR_DISTANCE_MAX].sum()
    return close / areas_arr.sum()


def classify(base_path: str, filename: str, width: int, height: int, bboxes: list) -> tuple[str, dict] | None:
    """Returns (result, details) where result is TEXT_HEAVY or NOT_TEXT_HEAVY, or None if the
    image file can't be opened (caller should skip -- not the same failure mode as "not text
    heavy"; a genuinely unreadable file has no basis for either classification)."""
    regions = filter_axis_aligned_regions(bboxes)
    if not regions:
        return NOT_TEXT_HEAVY, {"coverage_ratio": 0.0, "dominant_color_frac": None, "ink_consistency": None}

    path = os.path.join(base_path, filename)
    try:
        img = PILImage.open(path).convert("RGB")
    except Exception:
        return None

    cov = coverage_ratio(regions, width, height)
    dom = dominant_color_fraction(img)
    ink = ink_consistency(img, regions)

    is_text_heavy = cov > COVERAGE_RATIO_MIN and dom > DOMINANT_COLOR_FRAC_MIN and ink > INK_CONSISTENCY_MIN
    details = {"coverage_ratio": round(cov, 4), "dominant_color_frac": round(dom, 4), "ink_consistency": round(ink, 4)}
    return (TEXT_HEAVY if is_text_heavy else NOT_TEXT_HEAVY), details
```

- [ ] **Step 4: Run to verify GREEN**

```bash
pytest batch/tests/test_text_heavy_classifier.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full `batch/tests/` suite (regression check)**

```bash
pytest batch/tests/ -q
```

Expected: PASS, no regressions in any other batch unit test.

- [ ] **Step 6: Commit**

```bash
git add batch/utils/text_heavy_classifier.py batch/tests/test_text_heavy_classifier.py
git commit -m "feat: pure text-heavy classifier signal computation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 3: Repository + batch script + integration tests

**Files:**
- Create: `repository/image_classifications.py`
- Create: `batch/classify_text_heavy.py`
- Create: `tests/integration/test_classify_text_heavy.py`
- Modify: `CLAUDE.md` (batch-pipeline list)

**Interfaces:**
- Consumes: `ImageClassification` (Task 1), `CLASSIFIER_NAME`/`classify` (Task 2).
- Produces: `ImageClassificationsRepository.set_result`/`.get_candidate_regions`;
  `batch/classify_text_heavy.py`'s `run(session, base_path, confidence_min, status) ->
  SimpleMetricsListener` and `main(status, trigger, run_id)` — the `run()`/`main()` split matches this
  codebase's established batch-script convention exactly (see Global Constraints' note on this refining
  the spec's Design §4 sketch).

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_classify_text_heavy.py`:

```python
"""Integration tests for batch/classify_text_heavy.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.classify_text_heavy import run
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from Storage.models import Image, ImageClassification, OCRText

CONFIDENCE_MIN = 0.4


def _screenshot_image(path, size=(200, 200)):
    """Same fixture shape as batch/tests/test_text_heavy_classifier.py's
    _fake_screenshot_bboxes -- 8 text-line bboxes, coverage ~0.68, dominant-color ~0.86,
    ink-consistency ~1.0. Kept independently here rather than imported, since integration tests
    in this codebase don't import fixtures from batch/tests/ (separate test roots, separate
    asyncio_mode -- see this repo's testing gotcha)."""
    img = PILImage.new("RGB", size, (255, 255, 255))
    pixels = img.load()
    bboxes = []
    for i in range(8):
        y0, y1 = 5 + i * 25, 25 + i * 25
        for x in range(10, 180):
            for y in range(y0 + 8, y0 + 12):
                pixels[x, y] = (0, 0, 0)
        bboxes.append([[10, y0], [180, y0], [180, y1], [10, y1]])
    img.save(path, "JPEG")
    return bboxes


@pytest.mark.asyncio(loop_scope="session")
async def test_classifies_and_persists_a_text_heavy_image(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", width=200, height=200)
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()

    assert metrics.counters_dict() == {TEXT_HEAVY: 1}
    row = (await db_session.execute(
        select(ImageClassification).where(ImageClassification.image_id == image.id)
    )).scalar_one()
    assert row.classifier == CLASSIFIER_NAME
    assert row.result == TEXT_HEAVY
    assert isinstance(row.details["coverage_ratio"], float)
    assert row.details["coverage_ratio"] > 0.5


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_finds_no_candidates_for_already_classified_images(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", width=200, height=200)
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    first = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()
    assert first.counters_dict() == {TEXT_HEAVY: 1}

    second = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()
    assert second.counters_dict() == {}  # zero candidates -- already classified, not recomputed


@pytest.mark.asyncio(loop_scope="session")
async def test_image_without_dimensions_is_excluded(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active")  # width/height left NULL
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")

    assert metrics.counters_dict() == {}
```

- [ ] **Step 2: Run to verify RED**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_classify_text_heavy.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'batch.classify_text_heavy'` (neither the
repository nor the batch script exist yet).

- [ ] **Step 3: Implement `repository/image_classifications.py`**

```python
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from Storage.models import Image, ImageClassification, OCRText


class ImageClassificationsRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.session = session

    async def set_result(self, image_id, classifier: str, result: str, details: dict) -> None:
        stmt = (
            insert(ImageClassification)
            .values(image_id=image_id, classifier=classifier, result=result, details=details)
            .on_conflict_do_update(
                index_elements=["image_id", "classifier"],
                set_={"result": result, "details": details, "computed_at": func.now()},
            )
        )
        await self.session.execute(stmt)

    async def get_candidate_regions(self, classifier: str, confidence_min: float, status: str = "active"):
        """Returns (image_id, filename, width, height, bbox, confidence) rows, ordered by
        image_id, for images with real dimensions and at least one OCR region at or above
        confidence_min, excluding images that already have a row for `classifier`. Callers group
        consecutive rows by image_id (already ordered -- no need for a full dict-of-lists)."""
        already_classified = (
            select(ImageClassification.image_id)
            .where(ImageClassification.classifier == classifier)
            .scalar_subquery()
        )
        query = (
            select(self.img.id, self.img.filename, self.img.width, self.img.height,
                   self.ocr.bbox, self.ocr.confidence)
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(
                self.img.status == status,
                self.img.width.isnot(None), self.img.height.isnot(None),
                self.ocr.confidence >= confidence_min, self.ocr.bbox.isnot(None),
                self.img.id.not_in(already_classified),
            )
            .order_by(self.img.id)
        )
        result = await self.session.execute(query)
        return result.fetchall()
```

- [ ] **Step 4: Implement `batch/classify_text_heavy.py`**

```python
"""Computes the text-heavy classifier (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md) for images with OCR text and real
dimensions, storing both the raw signals and the final result in image_classifications. Safe to
re-run: only images without a row for CLASSIFIER_NAME are processed."""
import argparse
import asyncio
import uuid
from itertools import groupby

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, classify
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.image_classifications import ImageClassificationsRepository
from Storage.db import AsyncSessionLocal


async def run(session, base_path: str, confidence_min: float, status: str) -> SimpleMetricsListener:
    repo = ImageClassificationsRepository(session)
    rows = await repo.get_candidate_regions(CLASSIFIER_NAME, confidence_min, status=status)

    images = [(image_id, list(group)) for image_id, group in groupby(rows, key=lambda r: r[0])]
    print(f"Images to classify: {len(images)}")

    metrics = SimpleMetricsListener()
    tracker = ProgressTracker(len(images), report_every=100, report_interval_secs=10)

    for image_id, group in images:
        _, filename, width, height, _, _ = group[0]
        bboxes = [row.bbox for row in group]
        outcome = classify(base_path, filename, width, height, bboxes)
        if outcome is None:
            metrics.increment("unreadable")
            tracker.skip()
            continue
        result, details = outcome
        await repo.set_result(image_id, CLASSIFIER_NAME, result, details)
        metrics.increment(result)
        tracker.mark_done()

    tracker.summary()
    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    base_path = settings.BASE_PATH
    confidence_min = settings.OCR.CONFIDENCE_MIN

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, confidence_min, status)
                await session.commit()
    else:
        async with tracked_run(kind="classify_text_heavy", trigger=trigger):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, confidence_min, status)
                await session.commit()

    print("Classification results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
```

Note: `groupby` requires `rows` sorted by the grouping key, which `get_candidate_regions`'s
`.order_by(self.img.id)` guarantees — do not remove that `ORDER BY` without also changing the grouping
approach here.

- [ ] **Step 5: Run to verify GREEN**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_classify_text_heavy.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 6: Add the `CLAUDE.md` batch-pipeline entry**

In `CLAUDE.md`'s batch pipeline list, in the "Maintenance" section (near
`build_description_note_lemmas`/`build_description_note_embeddings`'s entries, matching their exact
"admin-triggerable, manual-trigger only, not scheduled" framing), add:

```
classify_text_heavy         → computes the text-heavy classifier (coverage ratio, background
                               color uniformity, ink-color consistency) for images with OCR text
                               and real dimensions, storing both the raw signals and the result in
                               image_classifications. Admin-triggerable from /admin/batches,
                               manual-trigger only, not scheduled. --status defaults to active
                               (the existing corpus); --status pending covers an in-flight
                               ingestion batch. See
                               docs/superpowers/specs/2026-09-15-text-heavy-classifier.md.
```

- [ ] **Step 7: Run the full backend suite (regression check)**

```bash
cd Backend && pytest -q
```

Expected: PASS, unchanged — no `Backend/` files touched, but `Storage/models.py` is shared, so this
confirms nothing broke.

- [ ] **Step 8: Run the full integration sweep**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: PASS. Per this repo's "run the whole root, not just the file that looks related" rule —
`Storage/models.py` is shared code, so the full root runs here, not just the new file.

- [ ] **Step 9: Run the full `batch/tests/` suite (regression check)**

```bash
pytest batch/tests/ -q
```

Expected: PASS, unchanged.

- [ ] **Step 10: Commit**

```bash
git add repository/image_classifications.py batch/classify_text_heavy.py \
        tests/integration/test_classify_text_heavy.py CLAUDE.md
git commit -m "feat: wire the text-heavy classifier into a batch script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 4: Live rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** Its migration step and its classification-run step both write
to live `metal`/`general`/`it` databases the developer's own running backends depend on continuously —
exactly the case `CLAUDE.md`'s "Live database access for agents" section and this repo's own established
pattern (the Tier B partial-index work, the dimension-capture work) exist for. The controller runs every
step below itself.

**Before Step 2 (the first live-environment write), stop and get the user's explicit go-ahead.** Name the
three environments this will touch and what it does (a brand-new, empty table via a plain migration, then
running the new `classify_text_heavy.py --status active` batch job) before proceeding.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-15-text-heavy-classifier.md` (status + a "Rollout outcome"
  section with the real counts).

- [ ] **Step 1: Confirm the plan and get the go-ahead**

Summarize for the user: Tasks 1-3's code is merged; Step 2 below applies the new migration (a brand-new,
empty table — no `CONCURRENTLY` concern, no existing-data risk) to `metal`, `general`, and `it` in turn,
then Step 3 runs `classify_text_heavy.py --status active` against each to classify the existing corpus.
Wait for explicit confirmation before continuing.

- [ ] **Step 2: Apply the migration to all three environments**

Per `CLAUDE.md`'s documented migration workflow, for each of `metal`, `general`, `it` in turn:

```powershell
Get-Content ..\environments\.env.<environment> | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
cd Storage
alembic upgrade head
```

Confirm each environment's backend (`/api/diagnostics/health`) still responds normally after its
migration.

- [ ] **Step 3: Run the classifier against all three environments**

```bash
python -m batch.classify_text_heavy --env <environment> --status active
```

(Recall this repo's gotcha: `DATABASE_URL` must be set in the shell before this command runs, even with
`--env` — `Storage/config.py`'s import-time guard fires before `load_env()` gets a chance to run. Export
it from the target environment's `.env.<environment>` file first, same as the dimension-capture rollout
did.)

Optionally also `--status pending`, to cover any in-flight ingestion batch. Confirm each environment's
backend health after each run.

- [ ] **Step 4: Verify the classification results (read-only)**

For each environment, via `DATABASE_URL_READONLY` (never the main `DATABASE_URL`):

```sql
SELECT result, count(*) FROM image_classifications WHERE classifier = 'text_heavy_v1' GROUP BY result;
```

Expected: a plausible split (not 100% one result, not 0 rows). Spot-check a handful of `text_heavy`
results by opening the actual image files (same manual-verification approach used throughout this
spec's validation) to catch any environment-specific surprise before considering this done.

- [ ] **Step 5: Mark the spec done**

`docs/superpowers/specs/2026-09-15-text-heavy-classifier.md`: `status: approved` → `status: done`; add
`Plan: docs/superpowers/plans/2026-09-15-text-heavy-classifier.md` under the status line; add a
"## Rollout outcome" section with each environment's `result` counts from Step 4 and any notable
spot-check findings.

```bash
git add docs/superpowers/specs/2026-09-15-text-heavy-classifier.md
git commit -m "docs: mark text-heavy classifier spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (`ImageClassification` model) → Task 1.
- Design §2 (pure classifier module) → Task 2, transcribed verbatim from the spec.
- Design §3 (repository) → Task 3 Step 3, transcribed verbatim from the spec.
- Design §4 (batch script) → Task 3 Step 4, **refined** for testability (see Global Constraints) to
  match this codebase's real `run()`/`main()` convention — flagged explicitly, not a silent deviation.
- Design §5 (migration) → Task 1 Steps 3-5.
- Design §6 (`CLAUDE.md` entry) → Task 3 Step 6.
- Testing section's exact test descriptions → Task 2 Step 1 and Task 3 Step 1 give complete, concrete
  test code for every case the spec's Testing section named (axis-aligned survives/rotated discarded/
  degenerate discarded; coverage known-areas/capped; dominant-color solid/checkerboard; ink-consistency
  matching/minority-different/sentinel; classify text-heavy/not-text-heavy/unreadable; persistence,
  re-run exclusion, missing-dimensions exclusion).
- Rollout (migration + classification run + verification + spec status flip) → Task 4 in full.
- Non-goals respected: no dedup-routing wiring, no admin UI, no fourth (CLIP) signal, no geometric union
  for the coverage residual, no re-classification tooling — this plan touches exactly the files the spec
  named.

**Placeholder scan:** none. Every code step has literal, complete content transcribed from the spec (or,
for the batch script, the established codebase convention) — no "similar to X," no "add appropriate
tests," no unresolved names. The fixture-design details in Task 2's `ink_consistency` tests (background/
ink split within each region, not 100%-ink-filled regions) are spelled out precisely because a naive
100%-fill fixture would silently break `_region_ink_color`'s minority-color extraction — documented
inline in the test code's own comment, not left as a trap for the implementer to discover.

**Type consistency:** `classify()`'s `(str, dict) | None` return, `Region`'s `(aabb, area)` fields, and
`ImageClassificationsRepository`'s method signatures are identical everywhere they appear — the pure
module (Task 2), the repository (Task 3 Step 3), and the batch script (Task 3 Step 4) all agree, and
every test calls them with the same argument order and shapes. `run(session, base_path, confidence_min,
status)`'s signature matches between its Task 3 Step 4 definition and every Task 3 Step 1 test's call
site.
