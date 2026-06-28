"""
Tesseract-based Russian OCR reader with EasyOCR-compatible interface.

Requires Tesseract + Russian language data installed:
  winget install --id UB-Mannheim.TesseractOCR --override "/S /LANG=Russian"
  pip install pytesseract

Tesseract handles Impact/condensed-bold Cyrillic (the dominant Russian meme font)
far better than EasyOCR's ru model.
"""
import numpy as np
import cv2

try:
    import pytesseract
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

# Default install path from UB-Mannheim Windows installer.
_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_MIN_CONFIDENCE = 30  # Tesseract word-level confidence 0–100


def is_available() -> bool:
    if not _AVAILABLE:
        return False
    import shutil
    return shutil.which("tesseract") is not None or __import__("os").path.exists(_TESSERACT_CMD)


class TesseractReader:
    """
    Wraps Tesseract word-level output into (bbox, text, confidence) tuples
    matching EasyOCR's readtext() return format.

    bbox format: [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]  (4-corner polygon, top-left CW)
    confidence:  0.0–1.0  (converted from Tesseract's 0–100 int)
    """

    def __init__(self, lang: str = "rus"):
        if not _AVAILABLE:
            raise RuntimeError("pytesseract not installed — pip install pytesseract")
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
        self._lang = lang

    def readtext(self, img: np.ndarray) -> list[tuple]:
        # Tesseract works best on grayscale; convert if needed.
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img

        data = pytesseract.image_to_data(
            gray,
            lang=self._lang,
            output_type=pytesseract.Output.DICT,
            config="--psm 6",  # assume a single uniform block of text
        )

        results = []
        for i, word in enumerate(data["text"]):
            word = word.strip()
            if not word:
                continue
            conf = int(data["conf"][i])
            if conf < _MIN_CONFIDENCE:
                continue

            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            bbox = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            results.append((bbox, word, conf / 100.0))

        return results