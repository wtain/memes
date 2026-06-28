"""
Tesseract-based Russian OCR that uses EasyOCR for text region detection and
Tesseract for character recognition on each crop.

Why split detection and recognition:
- EasyOCR's detection stage finds Cyrillic text regions reliably even for
  Impact/bold meme fonts.
- EasyOCR's Russian recognition model can't read those fonts.
- Tesseract's Russian LSTM model reads Impact Cyrillic correctly given a
  clean, focused crop.

Requires Tesseract + Russian language data:
  winget install --id UB-Mannheim.TesseractOCR --override "/S /LANG=Russian"
  # download rus.traineddata from tessdata_best and place in tessdata/
  pip install pytesseract
"""
import numpy as np
import cv2

try:
    import pytesseract
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_PSM = "11"   # sparse text — best for meme layouts
_OEM = "1"    # LSTM only


def is_available() -> bool:
    if not _AVAILABLE:
        return False
    import os
    return os.path.exists(_TESSERACT_CMD)


class TesseractReader:
    """
    Detect text regions with EasyOCR (ru + en readers), then recognize each
    crop with Tesseract.  Returns (bbox, text, confidence) tuples compatible
    with EasyOCR's readtext() output format.
    """

    def __init__(self, lang: str = "rus", ru_detector=None, en_detector=None):
        if not _AVAILABLE:
            raise RuntimeError("pytesseract not installed — pip install pytesseract")
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
        self._lang = lang
        self._ru_det = ru_detector
        self._en_det = en_detector

    def readtext(self, img: np.ndarray) -> list[tuple]:
        bboxes = self._detect(img)
        results = []
        for bbox in bboxes:
            crop = self._crop(img, bbox)
            if crop is None:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            text = self._recognize(gray)
            if text:
                results.append((bbox, text, 0.85))
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect(self, img: np.ndarray) -> list:
        """Run ru + en EasyOCR detectors and merge bboxes (IoU dedup)."""
        detections: list[tuple] = []
        for det in (self._ru_det, self._en_det):
            if det is None:
                continue
            for bbox, _, _ in det.readtext(img):
                if not any(_iou(bbox, b) > 0.3 for b in detections):
                    detections.append(bbox)
        return detections

    def _crop(self, img: np.ndarray, bbox, pad: int = 6):
        pts = np.array(bbox, dtype=np.float32)
        x1 = max(0, int(pts[:, 0].min()) - pad)
        y1 = max(0, int(pts[:, 1].min()) - pad)
        x2 = min(img.shape[1], int(pts[:, 0].max()) + pad)
        y2 = min(img.shape[0], int(pts[:, 1].max()) + pad)
        return img[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None

    def _recognize(self, gray: np.ndarray) -> str:
        """Try multiple preprocessing variants, return the longest result."""
        best = ""
        for variant in (_bright_guided_fill(gray), cv2.bitwise_not(gray), gray):
            t = pytesseract.image_to_string(
                variant,
                lang=self._lang,
                config=f"--psm {_PSM} --oem {_OEM}",
            ).strip().replace("\n", " ").strip()
            if len(t) > len(best):
                best = t
        return best


# ------------------------------------------------------------------
# Image preprocessing helpers
# ------------------------------------------------------------------

def _bright_guided_fill(gray: np.ndarray) -> np.ndarray:
    """
    For white-fill / black-outline meme text on any background.
    Anchors on the bright fill pixels and expands inward to capture the outline,
    avoiding dark background regions that aren't adjacent to bright text fill.
    """
    _, bright = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    bright_exp = cv2.dilate(bright, k, iterations=2)
    _, dark = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    mask = cv2.bitwise_or(bright, cv2.bitwise_and(dark, bright_exp))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    # Drop tiny noise blobs left by background texture
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
    h, w = closed.shape
    min_area = max(30, h * w * 0.001)
    clean = np.zeros_like(closed)
    for i in range(1, n_labels):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            clean[labels == i] = 255
    return cv2.bitwise_not(clean)  # black text on white


def _iou(a, b) -> float:
    def aabb(pts):
        p = np.array(pts, dtype=np.float32)
        return p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()
    ax1, ay1, ax2, ay2 = aabb(a)
    bx1, by1, bx2, by2 = aabb(b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    return inter / ((ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter)