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

    assert detect_actual_format(str(tmp_path / "a.jpg")) == "JPEG"


def test_detects_png_mislabeled_as_jpg(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")

    assert detect_actual_format(str(tmp_path / "a.jpg")) == "PNG"


def test_detects_webp(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")

    assert detect_actual_format(str(tmp_path / "a.webp")) == "WEBP"


def test_returns_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"this is not image data")

    assert detect_actual_format(str(path)) is None


def test_reports_raw_pillow_format_for_unmapped_formats(tmp_path):
    """A format this module has no canonical-extension mapping for is still identified --
    it must not be conflated with "Pillow couldn't read this at all" (which returns None)."""
    _save(tmp_path, "a.ppm", "PPM")

    assert detect_actual_format(str(tmp_path / "a.ppm")) == "PPM"


# --------------------------------------------------------------------------
# fix_image_file -- no-op / rename
# --------------------------------------------------------------------------

def test_noop_when_extension_already_matches(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")

    outcome = fix_image_file(str(tmp_path), "a.jpg")

    assert outcome.changed is False
    assert outcome.unreadable is False
    assert (tmp_path / "a.jpg").exists()


def test_noop_for_jpeg_saved_as_jpeg_extension(tmp_path):
    """'.jpeg' is a perfectly valid extension for JPEG content -- renaming it to '.jpg'
    would be a gratuitous change this feature was never meant to make."""
    _save(tmp_path, "a.jpeg", "JPEG")

    outcome = fix_image_file(str(tmp_path), "a.jpeg")

    assert outcome.changed is False
    assert outcome.unreadable is False
    assert (tmp_path / "a.jpeg").exists()


def test_noop_for_tiff_saved_as_tif_extension(tmp_path):
    """Same for '.tif' vs '.tiff'."""
    _save(tmp_path, "b.tif", "TIFF")

    outcome = fix_image_file(str(tmp_path), "b.tif")

    assert outcome.changed is False
    assert outcome.unreadable is False
    assert (tmp_path / "b.tif").exists()


def test_leaves_unmapped_but_readable_format_untouched(tmp_path):
    """PPM opens fine in Pillow but has no entry in FORMAT_ACCEPTABLE_EXTENSIONS -- it must
    be left alone, not flagged unreadable (which would feed the move_flagged ->
    unregister_deleted_images eviction chain and drop a valid image from the corpus)."""
    _save(tmp_path, "a.ppm", "PPM")

    outcome = fix_image_file(str(tmp_path), "a.ppm")

    assert outcome.changed is False
    assert outcome.unreadable is False
    assert (tmp_path / "a.ppm").exists()


def test_unmapped_format_under_a_wrong_extension_is_still_untouched(tmp_path):
    """Even when the extension disagrees with the real (unmapped) format, guessing a
    canonical extension for a format this module doesn't handle is worse than doing
    nothing."""
    _save(tmp_path, "a.jpg", "PPM")

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


def test_single_frame_webp_conversion_is_not_reported_as_animated(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")

    outcome = fix_image_file(str(tmp_path), "a.webp")

    assert outcome.changed is True
    assert outcome.animated is False


def test_animated_webp_conversion_reports_animated(tmp_path):
    """JPEG can't hold more than one frame, so conversion silently drops the animation --
    the outcome must surface that so the caller can count it separately."""
    frame_1 = PILImage.new("RGB", (4, 4), (255, 0, 0))
    frame_2 = PILImage.new("RGB", (4, 4), (0, 0, 255))
    frame_1.save(
        os.path.join(str(tmp_path), "anim.webp"), "WEBP", save_all=True, append_images=[frame_2],
    )

    outcome = fix_image_file(str(tmp_path), "anim.webp")

    assert outcome.changed is True
    assert outcome.animated is True
    assert outcome.new_filename == "anim.jpg"
    with PILImage.open(tmp_path / "anim.jpg") as img:
        assert img.format == "JPEG"
        assert getattr(img, "n_frames", 1) == 1
    # the animated original is still recoverable
    assert (tmp_path / CONVERTED_ORIGINALS_DIRNAME / "anim.webp").exists()


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
