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
