"""Reads an --ids-file for the meme profile jobs (P6 spec, decision 9)."""
import json
import uuid
from pathlib import Path

MAX_IDS = 20000


def load_ids_file(path: str) -> frozenset[uuid.UUID]:
    """A JSON list of UUID strings, or a text file with one UUID per line (blank lines and `#` comments ignored)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"ids file {path} is empty")
    if text.startswith("[") or text.startswith("{"):
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError(f"ids file {path} must contain a JSON list of UUIDs")
        items = [str(item) for item in raw]
    else:
        items = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    ids: set[uuid.UUID] = set()
    for item in items:
        try:
            ids.add(uuid.UUID(item))
        except ValueError:
            raise ValueError(f"not a UUID: {item!r}") from None
    if not ids:
        raise ValueError(f"ids file {path} lists no ids")
    if len(ids) > MAX_IDS:
        raise ValueError(f"an ids file may hold at most {MAX_IDS} ids, got {len(ids)}")
    return frozenset(ids)
