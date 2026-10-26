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
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.png", "b.png"]  # no temporary file left


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
