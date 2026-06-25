import numpy as np
import cv2
from PIL import Image as PILImage
import torch
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

MODEL_ID = "microsoft/trocr-base-scene"
CONFIDENCE_THRESHOLD = 0.5
# Synthetic confidence assigned to text that TrOCR re-read (real score not exposed).
TROCR_SYNTHETIC_CONFIDENCE = 0.55


class TrOCRFallback:
    """
    Re-recognizes low-confidence EasyOCR crops using TrOCR (scene-text model).

    Designed for stylized / script fonts (e.g. Lobster, Impact with heavy
    distortion) where EasyOCR detects the bounding box correctly but mis-reads
    the characters.  English-only — don't apply to ru/es readers.
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

        for i, (bbox, _, confidence) in enumerate(detections):
            if confidence < CONFIDENCE_THRESHOLD:
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