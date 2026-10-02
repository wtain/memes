import json
import sys
from pathlib import Path

from taxonomy.loader import DEFAULT_DIR, TaxonomyError, load
from taxonomy.model import Taxonomy

LOCK_NAME = "taxonomy.lock"
REFRESH_HINT = "run `python -m taxonomy.lock` to refresh it"


def _read_lock(directory: Path) -> dict | None:
    path = directory / LOCK_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check(tax: Taxonomy, directory: Path) -> str | None:
    """Return None when the lock matches the vocabulary files, otherwise a message saying what to do."""
    lock = _read_lock(directory)
    if lock is None:
        return f"{LOCK_NAME} is missing; {REFRESH_HINT}"
    if lock.get("content_hash") == tax.content_hash and lock.get("version") == tax.version:
        return None
    if lock.get("content_hash") != tax.content_hash and lock.get("version") == tax.version:
        return (f"taxonomy content changed without bumping `version` (still {tax.version}); "
                f"bump it in taxonomy.yaml, then {REFRESH_HINT}")
    return f"{LOCK_NAME} is stale for version {tax.version}; {REFRESH_HINT}"


def write_lock(directory: Path) -> str:
    """Write the lock for the taxonomy in `directory`; refuse a content change with an unchanged version."""
    tax = load(directory)
    existing = _read_lock(directory)
    if existing and existing.get("version") == tax.version and existing.get("content_hash") != tax.content_hash:
        raise TaxonomyError(
            f"content changed but `version` is still {tax.version}: bump it in taxonomy.yaml first")
    payload = {"version": tax.version, "content_hash": tax.content_hash}
    (directory / LOCK_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return f"wrote {LOCK_NAME}: version {tax.version}, hash {tax.content_hash[:12]}"


def main(argv=None) -> int:
    try:
        print(write_lock(DEFAULT_DIR))
    except TaxonomyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
