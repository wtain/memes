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
