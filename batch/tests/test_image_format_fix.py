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
