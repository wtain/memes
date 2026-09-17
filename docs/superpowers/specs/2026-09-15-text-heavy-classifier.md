# Text-Heavy Meme Classifier — Compute and Store

status: done
Plan: docs/superpowers/plans/2026-09-15-text-heavy-classifier.md
Follow-ups: docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md
Originates from: the 2026-09-13/15 conversation continuing the Tier B review-noise thread B
(text-heavy image classification, e.g. chat/Twitter/Threads screenshots where OCR text should drive
duplicate detection instead of weak CLIP visual similarity for that subset). Builds directly on
`docs/superpowers/specs/2026-09-14-image-dimension-capture.md` (the prerequisite `Image.width`/`.height`
data this classifier's coverage-ratio signal needs) and on extensive empirical validation performed
in-conversation (not summarized separately in a review file — the validation methodology and findings
are captured in this spec's Key Facts and Design sections below, since they directly justify every
threshold and formula choice).

## Problem

No signal in this codebase distinguishes "a screenshot of a chat/social-media post, where OCR text should
be trusted over CLIP visual similarity for dedup" from "a normal meme." Building that signal from scratch
risks guessing at a formula that doesn't hold up on the real corpus. Before this spec, the following was
validated empirically, live, against real images and real OCR data on `general` (session
2026-09-15 — every claim below was checked against actual images via `DATABASE_URL_READONLY` and local
`BASE_PATH` file access, not assumed):

- A naive "sum of OCR bbox areas ÷ image area" coverage signal is badly broken on raw data: ratios up to
  11.45× the image's actual area. Root causes, each confirmed against real rows: (a) some detected text
  regions are rotated quadrilaterals, and using their axis-aligned bounding box instead of true polygon
  area inflates area; (b) low-confidence spurious OCR detections (as low as 0.006 confidence) on
  non-text images; (c) the OCR pipeline runs once per configured language (EN/ES/RU) and only dedupes
  overlapping detections *within* one language's pass (`batch/ocr_preprocess.py`'s `merge_results`), not
  *across* languages — the same physical text region can produce a separate, overlapping bbox row once
  per language it happens to get (mis)detected under.
- Filtering to `confidence >= 0.4` (this repo's existing `ocr.confidence_min` convention) and discarding
  non-axis-aligned regions fixes the bulk of this: on a 24,199-image sample, coverage ratio's distribution
  dropped to median 23%, p90 57%, p99 ~106%, with only 1.4% of images still exceeding 100% (an acceptable
  residual, capped at 1.0 rather than solved with a full geometric union — see Non-goals).
- Coverage ratio alone still can't distinguish a genuine text-heavy screenshot from a normal meme with a
  large bold caption (confirmed: a "bro you just posted CRINGE!" caption meme scored 0.84, nearly as high
  as a genuine tweet screenshot's 0.86) or from a busy promotional poster (confirmed: a concert poster
  with many small scattered text blocks scored 0.80 with 21 OCR regions, matching a genuine
  multi-line-paragraph screenshot's region count too).
- A second signal — background color uniformity (what fraction of the image is a single dominant color
  after an 8-color quantization) — closes most of that gap: genuine screenshots consistently score
  0.63–0.88 dominant-color fraction; the caption-meme and poster false positives score 0.27–0.50. Verified
  against messenger-app screenshots specifically (Instagram DM colored bubbles, a WhatsApp dark-theme
  conversation over a busy doodle-pattern wallpaper) — both still score correctly high (0.71–0.87),
  confirming the signal survives branded/colored chat UI backgrounds, not just plain white/black posts.
- Background uniformity alone still misses one case: a flat-single-color *decorative/illustrated* graphic
  (confirmed: a "Which duck are you?" quiz card, solid flat orange background, illustrated multi-colored
  lettering) scores 0.845 — as high as a genuine screenshot — because a flat background isn't unique to
  UI screenshots.
- A third signal — text ink-color consistency (do the OCR regions' actual text/ink colors cluster around
  one color, anchored on the single largest-area region, rather than varying across regions) — catches
  that case: the duck quiz scores 0.27–0.56 across refinements, while genuine screenshots score 0.77–1.00.
- **The three signals combined are not perfect.** On a 129-image stratified sample (general random +
  messenger/social-screenshot filenames), the candidate combined rule correctly separated real
  screenshots (plain-text posts, colored chat bubbles, dark-theme WhatsApp with a busy background) from
  correctly-rejected decorative content (a bingo-card graphic, a labeled infographic diagram) — but 2
  confirmed false positives remain: plain white-background memes with a large, uniformly-colored bold
  caption (a frog-boxing-drugs meme, a helicopter/dog meme) are structurally indistinguishable from a
  screenshot by geometry and color alone. **This is accepted, not solved, by this spec** — see Non-goals
  and the Key Facts note on why.

## Goal

Compute the three validated signals (coverage ratio, dominant-color fraction, ink-color consistency) for
every image with OCR text and real dimensions, derive a `text_heavy` / `not_text_heavy` classification
from them, and store both the raw signal values and the final classification — durably, so the
classification's quality can be audited and the thresholds re-tuned against real stored data later,
without re-running image analysis from scratch.

## Non-goals

- **Wiring this classification into the actual Tier A/B duplicate-detection routing logic.** This spec
  computes and stores the signal only. A separate future spec decides how `tmp_duplicates`
  candidate-finding or review changes based on it (e.g. OCR-text-embedding comparison for the
  `text_heavy` bucket vs. today's CLIP-only comparison) — deliberately kept independent, per this
  project's own established pattern of decomposing a multi-stage initiative into separately-shippable
  specs (see the dimension-capture spec's own Non-goals for the same reasoning).
- **An admin UI to browse/filter classification results.** Explicitly deferred — see the brainstorming
  conversation this spec originates from. The `image_classifications` table this spec adds is designed
  to make such a UI easy to build later (structured, queryable, not bespoke), but building it is not part
  of this spec.
- **Closing the remaining false-positive gap** (plain-background, bold-caption memes indistinguishable
  from screenshots by geometry/color). A fourth signal (a CLIP zero-shot check against the existing but
  unwired `"a screenshot of a dialog/messenger"` concept phrase in
  `batch/data/text-concepts.general.json`) was identified as the likely next step during brainstorming,
  but is out of scope here — accepted imprecision, not a blocker, because this classification is a
  *routing* signal for a future comparison method, not an automatic duplicate-merge decision: a
  false-positive meme gets compared via OCR-text embedding instead of CLIP, which is a worse-than-ideal
  but not wrong comparison method for it, not a silent bad merge.
- **A proper geometric union for the coverage-ratio residual overcounting** (the 1.4% of images still
  exceeding 100% after axis-alignment filtering, from same-region cross-language re-detection). Capping
  at 1.0 is an accepted approximation — see Problem.
- **Retroactively re-classifying every threshold change as a zero-cost operation.** `details` stores the
  raw signals specifically so a *future* threshold change can recompute `result` from stored `details`
  without re-opening images — but this spec ships one classifier version with one set of thresholds;
  building an explicit "recompute result from stored details" tool is not part of this spec (a simple
  enough follow-up that it doesn't need its own design).
- **A new migration for `Image.width`/`.height`.** Already shipped (dimension-capture spec) — this spec
  only adds the new `image_classifications` table.

## Key facts this rests on

- `ocr.confidence_min` (`environments/settings.yaml:2`, value `0.4`) is the existing convention for
  "trustworthy enough OCR" elsewhere in this pipeline (`build_ocr_lemmas.py`); reused here rather than
  inventing a second confidence threshold.
- `OCRText.bbox` (`Storage/models.py:90`) is a 4-point polygon `[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]`
  (EasyOCR's native format) — confirmed via direct inspection of real stored rows, not assumed from the
  column's own ambiguous "polygon or x,y,w,h" comment.
  `batch/ocr_preprocess.py:46-48`'s `_bbox_aabb` already computes an axis-aligned bounding box from this
  exact shape via min/max, confirming the format assumption independently.
- **Axis-alignment test:** for a genuinely axis-aligned rectangle, the shoelace-formula polygon area
  equals its AABB's area exactly; any rotation makes the AABB strictly larger. So
  `polygon_area / aabb_area > 0.95` is a robust, ordering-independent axis-aligned test that reuses both
  area formulas already needed elsewhere — no separate rotation-angle computation needed. Confirmed
  empirically: only 7.9% of confidence-filtered regions in a live sample are rotated by this test.
- `Image.width`/`.height` (shipped by the dimension-capture spec) are the coverage ratio's denominator.
  Images without them (not yet backfilled/captured) simply can't be classified yet — this classifier's
  image-selection query requires both to be non-null.
- All three signal computations only need Pillow + numpy, both already dependencies used elsewhere in
  `batch/` (`batch/ocr_preprocess.py` already imports `numpy as np`). No new dependency.
- `repository/image_extras.py`'s `set_flagged` (`:12-26`) is the exact upsert pattern this spec's
  repository method follows: `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`.
- `batch/build_tags_from_ocr.py` is the closest existing batch-script precedent: `argparse --env
  --incremental`, `tracked_run`/`finish_existing_run` from `batch/run_tracking.py`, `ProgressTracker`,
  `SimpleMetricsListener`, an async-context-manager "saver" that batches writes and commits once at exit
  (`repository/tags.py`'s `TagsSaver`). This spec's batch script follows the identical shape.
- `957d8e420fd5_add_image_extras_table.py` is the migration precedent for a new small per-image table
  with a `CASCADE` FK to `images.id` — this spec's migration follows the same
  `op.create_table`/`op.create_index` shape (via `alembic revision --autogenerate`, no `CONCURRENTLY`
  needed since this is a brand-new, empty table — unlike the dimension-capture spec's index-on-a-large-
  live-table case).

## Design

### 1. `Storage/models.py` — new `ImageClassification` model

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

Composite primary key `(image_id, classifier)` — one row per image per classifier, upserted on re-run;
no history retained beyond the current row (see Non-goals on re-classification). `classifier` carries its
own version in the value (`"text_heavy_v1"`, see Design §2) rather than a separate version column, so a
future threshold/algorithm change is a new `classifier` string, not a schema change, and old and new
results can coexist during a transition.

Add to `Image`'s relationship block (`Storage/models.py:51-64`, alongside `tags`/`image_extras`):

```python
    classifications = relationship("ImageClassification", back_populates="image", cascade="all, delete-orphan")
```

### 2. `batch/utils/text_heavy_classifier.py` — pure signal computation, no DB/network access

Mirrors `batch/utils/image_format_fix.py`'s separation of pure file/pixel logic from DB orchestration.

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

# Ink-color extraction: a region's second-most-common quantized color counts as "ink" only if it's
# a real, non-trivial fraction of the crop -- filters out anti-aliasing noise around the true
# background/ink boundary.
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
    import os
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

### 3. `repository/image_classifications.py` — new repository

```python
from sqlalchemy import select
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

`func` needs importing from `sqlalchemy` at the top of this file alongside the existing imports.

### 4. `batch/classify_text_heavy.py` — new batch script

Follows `batch/build_tags_from_ocr.py`'s exact shape: `argparse --env --incremental`,
`tracked_run`/`finish_existing_run`, `ProgressTracker`, `SimpleMetricsListener`. `--incremental`
(default behavior, matching the repository query's built-in exclusion of already-classified images) is
implicit in `get_candidate_regions` itself here, so there's no separate `delete_tags`-equivalent full-
reprocess step needed for the common case — re-running with a *new* `classifier` version string
(Design §1) is how a full recompute happens, not a flag.

```python
"""Computes the text-heavy classifier (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md) for images with OCR text and real
dimensions, storing both the raw signals and the final result in image_classifications. Safe to
re-run: only images without a row for CLASSIFIER_NAME are processed (upsert on the repository side
handles any race, but the selection query already excludes them for efficiency)."""
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


async def _process(status: str) -> None:
    base_path = settings.BASE_PATH
    confidence_min = settings.OCR.CONFIDENCE_MIN

    async with AsyncSessionLocal() as session:
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

        await session.commit()
        tracker.summary()

    print("Classification results:")
    metrics.print()


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(status)
    else:
        async with tracked_run(kind="classify_text_heavy", trigger=trigger):
            await _process(status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
```

Note: `groupby` requires `rows` sorted by the grouping key, which `get_candidate_regions`'s
`.order_by(self.img.id)` guarantees — do not remove that `ORDER BY` without also changing this script's
grouping approach.

### 5. Migration — new table, plain `alembic revision --autogenerate`

Unlike the dimension-capture spec's index-on-a-large-live-table case, `image_classifications` is a brand
new, empty table — no `CONCURRENTLY`/`autocommit_block()` needed, a normal transactional migration is
fine. Generate via `alembic revision --autogenerate -m "add image_classifications table"` from `Storage/`
with the test DB's URL set, following `957d8e420fd5_add_image_extras_table.py`'s exact resulting shape
(`op.create_table` with the FK/composite-PK, `op.create_index` for the FK column).

### 6. `CLAUDE.md` — new batch-pipeline entry

Add under the "Maintenance" or a new small "Classification" grouping in the batch pipeline list
(`CLAUDE.md`'s existing block, following `build_description_note_lemmas`/`build_description_note_embeddings`'s
exact framing for a manual-trigger-only, non-blocking-pipeline batch job):

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

## Testing

- **`batch/tests/test_text_heavy_classifier.py`** (new, pure-logic unit tests, no DB, matching
  `test_image_format_fix.py`'s real-Pillow-fixture style):
  - `filter_axis_aligned_regions`: a real axis-aligned bbox survives; a genuinely rotated 4-point polygon
    (constructed by hand, e.g. a square rotated 30°) is discarded; an empty/degenerate bbox (zero area) is
    discarded without raising.
  - `coverage_ratio`: known region areas against a known image size produce the expected ratio; a
    contrived case where summed area exceeds the image area is capped at exactly 1.0, not left uncapped.
  - `dominant_color_fraction`: a solid-color synthetic image (`PILImage.new`) returns ~1.0; a
    checkerboard/multi-color synthetic image returns a value meaningfully below 1.0.
  - `ink_consistency`: synthetic regions with matching ink colors return a value near 1.0; synthetic
    regions with a large, clearly-different-colored minority region return a proportionally lower value
    (construct via `PILImage.new` rectangles with known fill colors, not real photos — deterministic,
    no fixture files needed).
  - `classify`: end-to-end on a synthetic "fake screenshot" fixture (solid white background, several
    axis-aligned black-text-colored rectangles covering >50% of the image) returns `TEXT_HEAVY`; a
    synthetic "fake meme" fixture (busy multi-color background, small text regions) returns
    `NOT_TEXT_HEAVY`; an unreadable path returns `None`.
- **`tests/integration/test_classify_text_heavy.py`** (new, real DB, matching
  `test_fix_image_formats.py`'s style): real `Image` + `OCRText` rows via `tmp_path` fixture files,
  confirming `_process` persists an `ImageClassification` row with the expected `result` and that
  `details` round-trips through the DB as real floats (not strings) via the JSON column. A re-run test
  proving `get_candidate_regions` correctly excludes already-classified images (mirrors
  `test_rerun_is_a_noop_on_already_fixed_images`'s no-op-on-rerun pattern, but here "no-op" means "zero
  candidates found," not "same result recomputed" — confirm that distinction explicitly in the test).
  A test confirming an image with OCR text but `width`/`.height` still `NULL` (not yet backfilled) is
  correctly excluded from candidates.
- `cd Backend && pytest -q` (regression check — no `Backend/` files touched, but confirms nothing shared
  broke). `pytest batch/tests/ -q` for the new unit tests.
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
  (full sweep, since `Storage/models.py` is shared code) for the new integration tests.

## Rollout

1. Ship the migration + code. No behavior change to any existing path — this is a new table and a new,
   independent, manually-triggered batch script; nothing else reads or writes it.
2. Apply the migration to all three live environments (a genuinely new, empty table — no `CONCURRENTLY`
   concern, no live-data risk, but still a live-database write requiring the same controller-only,
   explicit-go-ahead handling as any live migration per this repo's established pattern).
3. With explicit go-ahead, run `classify_text_heavy.py --status active` against each environment to
   backfill the existing corpus (images that already have OCR text and dimensions).
4. Verify via a read-only count (`SELECT result, count(*) FROM image_classifications WHERE classifier =
   'text_heavy_v1' GROUP BY result`) that the run produced a plausible split, and spot-check a handful of
   `text_heavy` results against the real image files (same manual-verification approach used throughout
   this spec's validation) to catch any environment-specific surprise before considering this done.

Downstream consumption (wiring this into actual dedup routing) is explicitly future work — see Non-goals.

## Rollout outcome (2026-09-16)

Migration applied and `classify_text_heavy.py --status active` run against all three live
environments, controller-executed per the plan's Task 4. Each environment's backend health
(`/api/diagnostics/health`) was confirmed after the migration and after the classification run.
Final counts, independently re-verified via `DATABASE_URL_READONLY`:

| Environment | Images classified | `text_heavy` | `not_text_heavy` | `text_heavy` rate |
|---|---|---|---|---|
| metal   | 17,150 | 406   | 16,744 | 2.4%  |
| general | 24,324 | 3,349 | 20,975 | 13.8% |
| it      | 1,405  | 178   | 1,227  | 12.7% |

`metal`'s much lower rate than `general`/`it` is plausible given the corpus content, not
investigated further — no crash, no 100%-one-result or 0-row result, no unreadable-file spike in
any environment.

**Spot-check (7 `text_heavy` images opened directly, 1-3 per environment):** 6/7 correct —
genuine dialog/tweet/text screenshots (Instagram and Twitter/X screenshots in English and Russian,
one dense magazine text column). 1/7 was a false positive: a 4-panel rage-comic strip
(`general/E8lCLTPICD0.jpg`) with a mostly-white background and a few short black captions — this is
exactly the already-documented, accepted false-positive class from this spec's Problem section
(plain-background, uniformly-colored bold caption over line art/photo), not a new failure mode.
Consistent with the ~1.6% false-positive rate found in the original 129-image validation sample;
no recalibration performed, per this spec's Non-goals.

All three `classify_text_heavy.py` runs completed without the Windows `UnicodeEncodeError`
(`PYTHONIOENCODING=utf-8` set per the fix-wave's CLAUDE.md update) and without losing partial
progress on failure (the fix-wave's `COMMIT_INTERVAL=500` periodic commit, unexercised in practice
since none of the three runs crashed).
