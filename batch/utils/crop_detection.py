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
EXIF_ORIENTATION = 0x0112
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
    share, an image under MIN_IMAGE_SIDE, an animated image, or an image with an EXIF orientation other than 1:
    pixel boxes of a rotated image would not match the frame OCR and browsers use). Raises on an unreadable
    file; the caller counts it."""
    with Image.open(path) as im:
        w, h = im.size
        if min(w, h) < MIN_IMAGE_SIDE or getattr(im, "is_animated", False):
            return None
        if im.getexif().get(EXIF_ORIENTATION, 1) != 1:
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
