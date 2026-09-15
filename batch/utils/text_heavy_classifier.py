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
