"""Approach A: classical margin trim plus dominant inner rectangle. THROWAWAY."""
import cv2
import numpy as np
from PIL import Image

MAX_SIDE = 600


def _load(path: str):
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        s = min(1.0, MAX_SIDE / max(w, h))
        small = im.resize((max(1, round(w * s)), max(1, round(h * s))))
    return np.asarray(small), w, h, s


def _flat_trim(gray: np.ndarray, tol: float = 7.0):
    """Peel flat bands (low variance along the band) from each side. Returns (x0, y0, x1, y1) in small px."""
    h, w = gray.shape
    row_std = gray.std(axis=1)
    col_std = gray.std(axis=0)
    y0, y1, x0, x1 = 0, h, 0, w
    while y0 < y1 - 1 and row_std[y0] < tol:
        y0 += 1
    while y1 - 1 > y0 and row_std[y1 - 1] < tol:
        y1 -= 1
    while x0 < x1 - 1 and col_std[x0] < tol:
        x0 += 1
    while x1 - 1 > x0 and col_std[x1 - 1] < tol:
        x1 -= 1
    return x0, y0, x1, y1


def _inner_rect(gray: np.ndarray):
    """Largest near-axis-aligned rectangular contour covering 25-92% of the frame. Returns (box, score) or None."""
    h, w = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        ratio = bw * bh / (w * h)
        if not 0.25 <= ratio <= 0.92:
            continue
        rectness = cv2.contourArea(c) / float(bw * bh) if bw * bh else 0
        arc = cv2.arcLength(c, True)
        # contour hugging its own bbox: perimeter close to the bbox perimeter
        perim_fit = min(arc, 2 * (bw + bh)) / max(arc, 2 * (bw + bh))
        score = perim_fit * (1 - abs(ratio - 0.6) * 0.3)
        if perim_fit > 0.85 and (best is None or score > best[1]):
            best = ((x, y, x + bw, y + bh), score)
    return best


def detect(path: str):
    """Returns dict(box=(x0,y0,x1,y1) in source px, confidence, kind) or None."""
    rgb, w, h, s = _load(path)
    if min(w, h) < 120:
        return None
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sh, sw = gray.shape
    x0, y0, x1, y1 = _flat_trim(gray)
    trimmed_ratio = (x1 - x0) * (y1 - y0) / (sw * sh)
    cands = []
    if 0.25 <= trimmed_ratio <= 0.93:
        # the trimmed area removed >= 7% of pixels: a border/letterbox. Confidence grows with removed share.
        cands.append(((x0, y0, x1, y1), 0.6 + 0.4 * min(1.0, (1 - trimmed_ratio) / 0.3), "margin"))
    inner = _inner_rect(gray[y0:y1, x0:x1]) if (x1 - x0) > 40 and (y1 - y0) > 40 else None
    if inner:
        (ix0, iy0, ix1, iy1), sc = inner
        cands.append(((x0 + ix0, y0 + iy0, x0 + ix1, y0 + iy1), 0.5 * sc, "inner"))
    if not cands:
        return None
    box, conf, kind = max(cands, key=lambda c: c[1])
    bx = (round(box[0] / s), round(box[1] / s), min(w, round(box[2] / s)), min(h, round(box[3] / s)))
    return {"box": bx, "confidence": round(conf, 4), "kind": kind, "size": (w, h)}
