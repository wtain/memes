"""Pixel work of the screenshot recut cut step (R3a; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md).
No database access: cropping a source file into a cut file in the same format, and moving OCR boxes into the cut's frame."""
import os
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import JpegImagePlugin

from batch.utils.file_hash import sha256_file

SOURCE_MISSING = "source_missing"
SOURCE_CHANGED = "source_changed"
UNREADABLE = "unreadable"
WRITE_ERROR = "write_error"


class CutError(Exception):
    """A proposal that cannot be cut. `reason` is stored in image_cuts.failure_reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class CutFile:
    width: int
    height: int
    content_hash: str


def _save_kwargs(im, fmt: str, jpeg_quality: int) -> dict:
    kwargs = {}
    icc = im.info.get("icc_profile")
    if fmt == "JPEG":
        kwargs["quality"] = jpeg_quality
        sampling = JpegImagePlugin.get_sampling(im)  # -1 when the source carries none (grayscale)
        if sampling != -1:
            kwargs["subsampling"] = sampling
    if icc and fmt in ("JPEG", "PNG"):
        kwargs["icc_profile"] = icc
    return kwargs


def crop_to_file(src: str, dst: str, box: tuple[int, int, int, int], expected_hash: str | None,
                 jpeg_quality: int) -> CutFile:
    """Crop `box` = (x, y, w, h) of `src` into `dst`, same format as the source. `dst` appears atomically (written to
    `dst + '.tmp'`, then renamed). A NULL `expected_hash` skips the unchanged-source check."""
    if not os.path.isfile(src):
        raise CutError(SOURCE_MISSING, src)
    try:
        if expected_hash is not None and sha256_file(src) != expected_hash:
            raise CutError(SOURCE_CHANGED, "content hash differs from the one recorded at detection")
        with PILImage.open(src) as im:
            im.load()
            fmt = im.format
            if fmt is None:
                raise CutError(UNREADABLE, "unknown format")
            x, y, w, h = box
            if x < 0 or y < 0 or x + w > im.width or y + h > im.height:
                raise CutError(SOURCE_CHANGED, f"box {box} does not fit {im.width}x{im.height}")
            cut = im.crop((x, y, x + w, y + h))
            kwargs = _save_kwargs(im, fmt, jpeg_quality)
    except CutError:
        raise
    except (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError) as e:
        raise CutError(UNREADABLE, f"{type(e).__name__}: {e}"[:200]) from e

    tmp = dst + ".tmp"
    try:
        cut.save(tmp, format=fmt, **kwargs)
        os.replace(tmp, dst)
    except (OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CutError(WRITE_ERROR, f"{type(e).__name__}: {e}"[:200]) from e
    return CutFile(width=cut.width, height=cut.height, content_hash=sha256_file(dst))


def _clip(value, low, high):
    return max(low, min(high, value))


def shift_bbox(bbox, crop: tuple[int, int, int, int]):
    """Move one OCR box into the cut's frame. `crop` = (x, y, w, h) in source pixels. Returns (keep, new_bbox): a box
    whose centre lies outside the crop is dropped; one inside is shifted and clipped to the crop. Both stored shapes are
    handled (list of [x, y] points, flat [x, y, w, h]). A NULL or unparseable box is kept as None: the text may well
    belong to the cut and the OCR refresh will redo it."""
    if bbox is None:
        return True, None
    cx, cy, cw, ch = crop
    try:
        if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x, y, w, h = bbox
            centre_x, centre_y = x + w / 2, y + h / 2
            shape = "rect"
        else:
            points = [(p[0], p[1]) for p in bbox]
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            if not all(isinstance(v, (int, float)) for v in xs + ys):
                raise TypeError("non-numeric point")
            centre_x, centre_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            shape = "poly"
    except (TypeError, ValueError, IndexError, KeyError):
        return True, None
    if not (cx <= centre_x < cx + cw and cy <= centre_y < cy + ch):
        return False, None
    if shape == "rect":
        x0, y0 = max(x, cx) - cx, max(y, cy) - cy
        x1, y1 = min(x + w, cx + cw) - cx, min(y + h, cy + ch) - cy
        return True, [x0, y0, x1 - x0, y1 - y0]
    return True, [[_clip(px - cx, 0, cw), _clip(py - cy, 0, ch)] for px, py in points]
