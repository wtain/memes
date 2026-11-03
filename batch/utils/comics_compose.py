"""Pixel-free layout arithmetic of the comics tiling composer (C2).
No database access: plans where each panel goes on a canvas."""
from dataclasses import dataclass

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
