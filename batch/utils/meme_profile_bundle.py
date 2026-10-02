"""Deterministic input bundle and fingerprint for meme profile extraction (P2 spec, decision 3).

Pure functions: no database, no Ollama. The fingerprint is the single source of truth for "have the
inputs changed", so rejected descriptions, edited notes and re-OCR text are all detected by one comparison.
"""
import hashlib
import json
from dataclasses import dataclass

from rules.lang_plausibility import passes_language_filter

PATH_TEXT = "text"
PATH_VISION = "vision"


@dataclass(frozen=True)
class BundleLimits:
    note_chars: int
    description_chars: int
    ocr_chars: int


@dataclass(frozen=True)
class Bundle:
    note: str | None
    descriptions: tuple[tuple[str, str], ...]   # (prompt_key, text), sorted by prompt_key
    ocr: str | None

    def total_chars(self) -> int:
        return len(self.note or "") + sum(len(text) for _, text in self.descriptions) + len(self.ocr or "")

    def canonical_json(self) -> str:
        return json.dumps(
            {"note": self.note, "descriptions": [list(pair) for pair in self.descriptions], "ocr": self.ocr},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )


def truncate_at_word(text: str, limit: int) -> str:
    """Cut to at most `limit` characters, at a word boundary when the cut would land inside a word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit].isspace():
        return cut.rstrip()
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > 0 else cut).rstrip()


def _reading_key(row) -> tuple:
    """Top-to-bottom, then left-to-right by polygon corner; rows without a usable bbox follow in id order."""
    try:
        ys = [float(point[1]) for point in row.bbox]
        xs = [float(point[0]) for point in row.bbox]
        return (0, min(ys), min(xs), str(row.row_id))
    except (TypeError, ValueError, IndexError):
        return (1, 0.0, 0.0, str(row.row_id))


def build_bundle(note, descriptions, ocr_rows, *, ocr_confidence_min: float, ocr_lang_score_min: float,
                 limits: BundleLimits) -> Bundle:
    clean_note = (note or "").strip()
    note_text = truncate_at_word(clean_note, limits.note_chars) if clean_note else None

    described = []
    for prompt_key, text in sorted(descriptions, key=lambda pair: pair[0]):
        clean = (text or "").strip()
        if clean:
            described.append((prompt_key, truncate_at_word(clean, limits.description_chars)))

    kept = [row for row in ocr_rows
            if passes_language_filter(row.confidence, row.lang_score, ocr_confidence_min, ocr_lang_score_min)]
    pieces = [row.text.strip() for row in sorted(kept, key=_reading_key) if row.text and row.text.strip()]
    joined = " ".join(pieces)
    ocr_text = truncate_at_word(joined, limits.ocr_chars) if joined else None

    return Bundle(note=note_text, descriptions=tuple(described), ocr=ocr_text)


def fingerprint(bundle: Bundle, content_hash: str | None) -> str:
    """sha256 of the canonical bundle plus the image content hash (so a replaced file re-extracts on the
    vision path). Versions are deliberately not part of it: they are compared separately."""
    payload = bundle.canonical_json() + "\x1f" + (content_hash or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def choose_path(bundle: Bundle, min_text_chars: int) -> str:
    return PATH_TEXT if bundle.total_chars() >= min_text_chars else PATH_VISION
