"""Opaque relevance cursor with a request fingerprint (P4 spec, decision 6). Pure."""
import base64
import hashlib
import json
from typing import Sequence

from retrieval.facets import RankedItem

SCORE_DECIMALS = 6


def fingerprint(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def encode_cursor(score: float, image_id: str, fp: str) -> str:
    payload = {"s": round(score, SCORE_DECIMALS), "i": image_id, "f": fp}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str | None, fp: str) -> tuple[float, str] | None:
    """None when there is no cursor, it is not ours, or it belongs to another request (restart at page 1)."""
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if payload["f"] != fp:
            return None
        return float(payload["s"]), str(payload["i"])
    except (ValueError, KeyError, TypeError):
        return None


def paginate(items: Sequence[RankedItem], cursor: str | None, fp: str, limit: int):
    """Items are ordered by (-score, image id); the page starts after the cursor's (score, id)."""
    position = decode_cursor(cursor, fp)
    start = 0
    if position is not None:
        cursor_score, cursor_id = position
        start = len(items)
        for index, item in enumerate(items):
            score = round(item.scored.score, SCORE_DECIMALS)
            if score < cursor_score or (score == cursor_score and item.scored.candidate.image_id > cursor_id):
                start = index
                break
    page = list(items[start:start + limit])
    has_next = start + limit < len(items)
    next_cursor = encode_cursor(page[-1].scored.score, page[-1].scored.candidate.image_id, fp) if has_next else None
    return page, next_cursor, has_next
