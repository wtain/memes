"""
Detects images whose file extension doesn't match their actual content, and fixes them:
a mislabeled-but-otherwise-fine format gets renamed in place; WebP content (regardless of
its current extension) gets converted to JPEG, since several libraries in this pipeline
can't consume WebP at all (confirmed: Ollama's vision backend used by
build_image_descriptions.py fails with "Failed to load image or audio file" -- see
CLAUDE.md's known gotchas). Pure file-level logic, no DB access -- both
batch/ingest_validate_formats.py (new ingestion pending images) and
batch/fix_image_formats.py (retroactive maintenance over the existing corpus) call
fix_image_file() per image and persist whatever it reports changed.

See docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.
"""
import os
from dataclasses import dataclass

from PIL import Image as PILImage

from batch.utils.file_hash import sha256_file
from batch.utils.safe_move import available_filename

FORMAT_TO_EXTENSION = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}

JPEG_QUALITY = 95
CONVERTED_ORIGINALS_DIRNAME = "converted_originals"


def detect_actual_format(path: str) -> str | None:
    """Returns the canonical extension (e.g. ".jpg") for the file's real content, or None
    if Pillow can't identify it at all (corrupt/truncated/unsupported format)."""
    try:
        with PILImage.open(path) as img:
            fmt = img.format
    except Exception:
        return None
    return FORMAT_TO_EXTENSION.get(fmt)


@dataclass
class FixOutcome:
    changed: bool
    unreadable: bool = False
    new_filename: str | None = None
    new_content_hash: str | None = None


def fix_image_file(base_path: str, filename: str) -> FixOutcome:
    """filename must already exist directly under base_path. See module docstring for the
    three possible outcomes (unreadable / renamed / converted / no-op)."""
    path = os.path.join(base_path, filename)
    actual_ext = detect_actual_format(path)

    if actual_ext is None:
        return FixOutcome(changed=False, unreadable=True)

    if actual_ext == ".webp":
        return _convert_webp_to_jpeg(base_path, filename, path)

    current_ext = os.path.splitext(filename)[1].lower()
    if current_ext != actual_ext:
        return _rename_in_place(base_path, filename, actual_ext)

    return FixOutcome(changed=False)


def _rename_in_place(base_path: str, filename: str, actual_ext: str) -> FixOutcome:
    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}{actual_ext}")
    os.rename(os.path.join(base_path, filename), os.path.join(base_path, final_name))
    return FixOutcome(changed=True, new_filename=final_name)


def _convert_webp_to_jpeg(base_path: str, filename: str, path: str) -> FixOutcome:
    with PILImage.open(path) as img:
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flattened = PILImage.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[3])
        else:
            flattened = img.convert("RGB")
        # img.convert() fully materializes pixel data (it's not lazy like Image.open()),
        # so it's safe to let this `with` block close the source file handle here -- the
        # rename below must happen only after that handle is closed, since renaming a file
        # with an open handle can fail on Windows.

    converted_originals_dir = os.path.join(base_path, CONVERTED_ORIGINALS_DIRNAME)
    os.makedirs(converted_originals_dir, exist_ok=True)
    # Move the original out of the way *before* computing the new jpg's name: the common
    # case is webp content already sitting at "<stem>.jpg", so the desired output name
    # equals the current filename -- freeing it first lets that slot be reused directly
    # instead of spuriously colliding with itself.
    original_dest_name = available_filename(converted_originals_dir, filename)
    os.rename(path, os.path.join(converted_originals_dir, original_dest_name))

    stem = os.path.splitext(filename)[0]
    final_name = available_filename(base_path, f"{stem}.jpg")
    final_path = os.path.join(base_path, final_name)
    flattened.save(final_path, "JPEG", quality=JPEG_QUALITY)

    new_content_hash = sha256_file(final_path)
    return FixOutcome(changed=True, new_filename=final_name, new_content_hash=new_content_hash)
