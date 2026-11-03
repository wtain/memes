"""Comics name rules (pure, stdlib only).

A comic's name is also used as a directory name, so the rules must hold after
normalization.
"""

import unicodedata

MAX_NAME_LENGTH = 80
_FORBIDDEN = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED |= {f"COM{i}" for i in range(1, 10)}
_RESERVED |= {f"LPT{i}" for i in range(1, 10)}


class ComicsNameError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def normalize_name(raw: str) -> tuple[str, str]:
    """Return (name, name_key) or raise ComicsNameError."""
    name = unicodedata.normalize("NFC", raw.strip())
    # NFC can in principle expose new edge whitespace; trim again.
    name = name.strip()
    if not name:
        raise ComicsNameError("empty")
    if len(name) > MAX_NAME_LENGTH:
        raise ComicsNameError("too_long")
    if any(ch in _FORBIDDEN or unicodedata.category(ch) == "Cc" for ch in name):
        raise ComicsNameError("forbidden_character")
    if name.endswith(".") or name.endswith(" "):
        raise ComicsNameError("trailing_dot_or_space")
    if name.split(".", 1)[0].strip().upper() in _RESERVED:
        raise ComicsNameError("reserved_name")
    return name, name.casefold()
