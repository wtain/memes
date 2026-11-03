"""Pixel-free layout arithmetic of the comics tiling composer (C2).
No database access: plans where each panel goes on a canvas."""
import io
import os
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import ImageOps

from batch.utils.file_hash import sha256_file

MAX_SIDE = 16000
PER_ROW = 2


class ComposeError(Exception):
    """A composition that cannot be done. `position` is the 0-based panel it concerns, or None."""

    def __init__(self, reason: str, position: int | None = None, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.position = position
        self.detail = detail


@dataclass(frozen=True)
class Placement:
    index: int
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Layout:
    canvas_width: int
    canvas_height: int
    gutter: int
    placements: tuple[Placement, ...]


def plan_layout(sizes: list[tuple[int, int]]) -> Layout:
    """`sizes` are (width, height) of the panels in displayed orientation, in reading order."""
    if not sizes:
        raise ComposeError("no_panels")
    groups = [list(range(i, min(i + PER_ROW, len(sizes)))) for i in range(0, len(sizes), PER_ROW)]
    row_heights: list[int] = []
    for k, idxs in enumerate(groups):
        h = min(sizes[i][1] for i in idxs)
        if len(idxs) == 1 and k > 0:
            h = min(h, row_heights[-1])
        row_heights.append(h)
    rows: list[list[tuple[int, int, int]]] = []  # (index, width, height)
    for idxs, rh in zip(groups, row_heights):
        row = []
        for i in idxs:
            w, h = sizes[i]
            row.append((i, w, h) if h == rh else (i, max(1, round(w * rh / h)), rh))
        rows.append(row)
    g = max(4, round(0.01 * min(row_heights)))
    row_widths = [sum(w for _, w, _ in r) + g * (len(r) - 1) for r in rows]
    widest = max(row_widths)
    canvas_w = widest + 2 * g
    canvas_h = sum(row_heights) + g * (len(rows) + 1)
    if canvas_w > MAX_SIDE or canvas_h > MAX_SIDE:
        raise ComposeError("too_large", None, f"{canvas_w}x{canvas_h} exceeds {MAX_SIDE}")
    placements = []
    y = g
    for r, rw, rh in zip(rows, row_widths, row_heights):
        x = g + (widest - rw) // 2
        for i, w, h in r:
            placements.append(Placement(i, x, y, w, h))
            x += w + g
        y += rh + g
    return Layout(canvas_w, canvas_h, g, tuple(placements))


# ---- Pixel composition and preview (C2 task 2) ----

PREVIEW_QUALITY = 85
_ACCEPTED = {"JPEG", "PNG", "WEBP"}
_CAN_ANIMATE = {"GIF", "PNG", "WEBP"}
_ICC_MODES = {"RGB", "RGBA", "L", "LA", "P"}  # other modes (CMYK, ...) carry a profile unfit for RGB output
_PILLOW_ERRORS =(OSError, ValueError, SyntaxError, PILImage.DecompressionBombError)


@dataclass(frozen=True)
class ComposedFile:
    width: int
    height: int
    content_hash: str
    format: str


@dataclass(frozen=True)
class _Header:
    size: tuple[int, int]  # displayed orientation
    format: str
    icc: bytes | None


def _unreadable(position: int, e: Exception) -> ComposeError:
    return ComposeError("unreadable", position, f"{type(e).__name__}: {e}"[:200])


def _read_header(path: str, position: int) -> _Header:
    try:
        with PILImage.open(path) as im:
            fmt = im.format
            if fmt == "MPO":  # multi-picture JPEG (phone photos): a static JPEG, first frame
                fmt = "JPEG"
            elif fmt in _CAN_ANIMATE and (getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1):
                raise ComposeError("animated", position)
            if fmt not in _ACCEPTED:
                raise ComposeError("unsupported_format", position, str(fmt))
            w, h = im.size
            if im.getexif().get(0x0112) in (5, 6, 7, 8):
                w, h = h, w
            icc = im.info.get("icc_profile") if im.mode in _ICC_MODES else None
            return _Header((w, h), fmt, icc)
    except ComposeError:
        raise
    except _PILLOW_ERRORS as e:
        raise _unreadable(position, e) from e


def read_panel_sizes(paths: list[str]) -> list[tuple[int, int]]:
    """Header-only: validates every panel and returns its displayed (width, height)."""
    return [_read_header(p, i).size for i, p in enumerate(paths)]


def _load_panel(path: str, position: int, size: tuple[int, int] | None = None):
    """Decode one panel as RGB in displayed orientation, transparency flattened on white; resampled once to `size`
    (Lanczos) when that differs from the decoded size."""
    try:
        with PILImage.open(path) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                flat = PILImage.new("RGB", rgba.size, "white")
                flat.paste(rgba, mask=rgba.getchannel("A"))
                im = flat
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if size is not None and im.size != size:
                im = im.resize(size, PILImage.Resampling.LANCZOS)
            return im
    except _PILLOW_ERRORS as e:
        raise _unreadable(position, e) from e


def _headers(paths: list[str]) -> list[_Header]:
    if not paths:
        raise ComposeError("no_panels")
    return [_read_header(p, i) for i, p in enumerate(paths)]


def compose_to_file(paths: list[str], dst: str, jpeg_quality: int) -> ComposedFile:
    """Tile the panels into `dst` (PNG when any panel is PNG, else JPEG). `dst` appears atomically via `dst + '.tmp'`;
    on any failure an existing `dst` is untouched and no `.tmp` stays."""
    headers = _headers(paths)
    layout = plan_layout([h.size for h in headers])
    fmt = "PNG" if any(h.format == "PNG" for h in headers) else "JPEG"
    profiles = {h.icc for h in headers}
    icc = next(iter(profiles)) if len(profiles) == 1 else None

    canvas = PILImage.new("RGB", (layout.canvas_width, layout.canvas_height), "white")
    for pl in layout.placements:
        panel = _load_panel(paths[pl.index], pl.index, (pl.width, pl.height))
        canvas.paste(panel, (pl.x, pl.y))
        panel.close()

    kwargs: dict = {}
    if fmt == "JPEG":
        kwargs["quality"] = jpeg_quality
    if icc:
        kwargs["icc_profile"] = icc
    tmp = dst + ".tmp"
    try:
        canvas.save(tmp, format=fmt, **kwargs)
        os.replace(tmp, dst)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if isinstance(e, (OSError, ValueError)):
            raise ComposeError("write_error", None, f"{type(e).__name__}: {e}"[:200]) from e
        raise
    return ComposedFile(layout.canvas_width, layout.canvas_height, sha256_file(dst), fmt)


def compose_preview(paths: list[str], max_width: int) -> bytes:
    """JPEG preview (quality 85) of the same layout at scale min(1, max_width / canvas_width). Each panel is resampled
    once, directly from its file, to its preview size; the full-size canvas is never built."""
    headers = _headers(paths)
    layout = plan_layout([h.size for h in headers])
    scale = min(1.0, max_width / layout.canvas_width)
    pw = max(1, round(layout.canvas_width * scale))
    ph = max(1, round(layout.canvas_height * scale))
    canvas = PILImage.new("RGB", (pw, ph), "white")
    for pl in layout.placements:
        x0 = min(max(0, round(pl.x * scale)), pw - 1)
        y0 = min(max(0, round(pl.y * scale)), ph - 1)
        x1 = min(max(x0 + 1, round((pl.x + pl.width) * scale)), pw)
        y1 = min(max(y0 + 1, round((pl.y + pl.height) * scale)), ph)
        panel = _load_panel(paths[pl.index], pl.index, (x1 - x0, y1 - y0))
        canvas.paste(panel, (x0, y0))
        panel.close()
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=PREVIEW_QUALITY)
    return out.getvalue()
