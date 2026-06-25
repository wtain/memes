import cv2
import numpy as np


def generate_variants(img: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """
    Return (name, preprocessed_image) pairs to run through OCR.

    Three variants:
    - original: raw color image (baseline)
    - inverted: inverted grayscale — turns white-text-with-black-outline on
      white background into a form OCR detectors handle well
    - clahe: CLAHE-enhanced color image — boosts local contrast for faded
      or low-contrast text without blowing out highlights
    """
    variants = [("original", img)]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    variants.append(("inverted", cv2.bitwise_not(gray)))

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    clahe_op = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe_op.apply(lab[:, :, 0])
    variants.append(("clahe", cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)))

    return variants


def _bbox_aabb(bbox) -> tuple[float, float, float, float]:
    pts = np.array(bbox, dtype=np.float32)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = _bbox_aabb(a)
    bx1, by1, bx2, by2 = _bbox_aabb(b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0.0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def merge_results(results_by_variant: list[list], iou_threshold: float = 0.3) -> list:
    """
    Merge OCR results from multiple preprocessing variants into one list.
    When two detections overlap (IoU > threshold) they are assumed to be the
    same text region; the entry with the higher confidence is kept.
    """
    merged: list = []
    for variant_results in results_by_variant:
        for bbox, text, confidence in variant_results:
            best_idx, best_iou = -1, iou_threshold
            for i, (m_bbox, _, _) in enumerate(merged):
                iou = _iou(bbox, m_bbox)
                if iou > best_iou:
                    best_idx, best_iou = i, iou
            if best_idx == -1:
                merged.append([bbox, text, float(confidence)])
            elif float(confidence) > merged[best_idx][2]:
                merged[best_idx] = [bbox, text, float(confidence)]
    return merged