import numpy as np
import cv2
from PIL import Image as PILImage
import torch
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

from rules.lang_plausibility import score as lang_plausibility_score

MODEL_ID = "microsoft/trocr-base-str"
CONFIDENCE_THRESHOLD = 0.5
# Synthetic confidence assigned to text that TrOCR re-read (real score not exposed).
TROCR_SYNTHETIC_CONFIDENCE = 0.55
# Deliberately stricter than settings.OCR.LANG_SCORE_MIN (0.3, tuned for
# build_bow.py/build_tags_from_ocr.py's golden-set eval -- see
# docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md).
# That threshold is calibrated for "don't lose genuine minority-language OCR
# rows"; this one is calibrated for "don't let TrOCR hallucinate fluent
# English over real Cyrillic text". Short filler words ("a", "no", "ere",
# "tak") coincidentally score as known English at a high rate, inflating a
# short garbled phrase's ratio -- measured against real garbled-Cyrillic
# detections 2026-08-02: 0.3 let everything through, 0.6 rejects the clear
# majority (scores of 0.33-0.5) while two coincidental-heavy outliers (0.67,
# 0.8) still slip through. A pure ratio threshold can't fully close that gap
# without a real per-token-length weighting or language-ID model -- not
# attempted here as disproportionate to a heuristic gate.
TROCR_MIN_LANG_SCORE = 0.6


class TrOCRFallback:
    """
    Re-recognizes low-confidence EasyOCR crops using TrOCR (scene-text model).

    Designed for stylized / script fonts (e.g. Lobster, Impact with heavy
    distortion) where EasyOCR detects the bounding box correctly but mis-reads
    the characters.  English-only — don't apply to ru/es readers.

    Low confidence isn't only caused by font stylization -- it's also what a
    wrong-language misread looks like (the "en" reader forced into Latin
    glyphs on a Cyrillic image, see docs/superpowers/specs/2026-07-02-ocr-
    language-plausibility-filtering.md). TrOCR can't recover those; tested
    2026-08-02 against real low-confidence "en" detections and found it
    re-reading garbled Cyrillic-as-Latin text into fluent-looking but
    unrelated English words (e.g. "AMOXET HA TEBa" -> "AMORETRATECRACY"),
    then stamping the result with TROCR_SYNTHETIC_CONFIDENCE as if it were
    *more* trustworthy than the EasyOCR score it replaced. `rerecognize()`
    gates on the same lang_plausibility.score() used by build_bow.py /
    build_tags_from_ocr.py to skip those before ever cropping/running them.
    """

    def __init__(self, device: str = "cuda"):
        self.device = device
        self.processor = TrOCRProcessor.from_pretrained(MODEL_ID)
        self.model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID).to(device)
        self.model.eval()

    def rerecognize(self, img_bgr: np.ndarray, detections: list) -> list:
        """
        For each detection whose confidence is below CONFIDENCE_THRESHOLD, crop
        the region from the original (color) image and re-read with TrOCR.
        Returns the same list with low-confidence entries updated in-place.
        """
        low_indices: list[int] = []
        crops: list[PILImage.Image] = []

        for i, (bbox, text, confidence) in enumerate(detections):
            if confidence < CONFIDENCE_THRESHOLD and self._is_plausibly_english(text):
                crop = self._crop(img_bgr, bbox)
                if crop is not None:
                    crops.append(crop)
                    low_indices.append(i)

        if not crops:
            return detections

        pixel_values = (
            self.processor(images=crops, return_tensors="pt")
            .pixel_values
            .to(self.device)
        )
        with torch.no_grad():
            generated_ids = self.model.generate(pixel_values)
        trocr_texts = self.processor.batch_decode(generated_ids, skip_special_tokens=True)

        updated = [list(d) for d in detections]
        for idx, trocr_text in zip(low_indices, trocr_texts):
            if trocr_text.strip():
                updated[idx][1] = trocr_text
                updated[idx][2] = TROCR_SYNTHETIC_CONFIDENCE

        return updated

    @staticmethod
    def _is_plausibly_english(text: str) -> bool:
        lang_score = lang_plausibility_score(text, "en")
        # None = too few tokens to judge (e.g. a single short word) -- exactly
        # the case TrOCR is meant to help with, so pass it through rather than
        # guessing garbage.
        return lang_score is None or lang_score >= TROCR_MIN_LANG_SCORE

    def _crop(self, img_bgr: np.ndarray, bbox) -> PILImage.Image | None:
        pts = np.array(bbox, dtype=np.float32)
        x1, y1 = int(pts[:, 0].min()), int(pts[:, 1].min())
        x2, y2 = int(pts[:, 0].max()), int(pts[:, 1].max())
        x1, y1 = max(0, x1), max(0, y1)
        x2 = min(img_bgr.shape[1], x2)
        y2 = min(img_bgr.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            return None
        rgb = cv2.cvtColor(img_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
        return PILImage.fromarray(rgb)