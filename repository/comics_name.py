"""Comics name rules (pure, stdlib only).

A comic's name is also used as a directory name, so the rules must hold after
normalization.
"""

import re
import unicodedata

MAX_NAME_LENGTH = 80
_FORBIDDEN = set('<>:"/\\|?*')
# Control, format (zero-width, bidi overrides), line and paragraph separators.
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED |= {f"COM{i}" for i in range(1, 10)}
_RESERVED |= {f"LPT{i}" for i in range(1, 10)}


class ComicsNameError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_DEFAULT_PREFIX = "Comics "
_WHITESPACE = re.compile(r"\s+")


def default_comics_name(filename: str | None, image_id, with_id_suffix: bool = False) -> str:
    """Server-side twin of the frontend's defaultComicsName (src/comics/comicsNaming.ts): `Comics <file name
    without extension>`, or `Comics <first 8 characters of the id>` when nothing usable is left. with_id_suffix
    appends the id's first 8 characters (the retry after a name conflict). Never longer than MAX_NAME_LENGTH.
    Also drops format and separator characters, which normalize_name would reject."""
    short = str(image_id)[:8]
    file = filename or ""
    dot = file.rfind(".")
    base = file[:dot] if dot > 0 else file
    stem = "".join(ch for ch in base if ch not in _FORBIDDEN and unicodedata.category(ch) not in _FORBIDDEN_CATEGORIES)
    stem = _WHITESPACE.sub(" ", stem).strip()
    suffix = f" {short}" if with_id_suffix else ""
    room = MAX_NAME_LENGTH - len(_DEFAULT_PREFIX) - len(suffix)
    body = short if stem == "" else stem[:room].strip().rstrip(". ")
    return f"{_DEFAULT_PREFIX}{body or short}{suffix}"


def name_key_of(text: str) -> str:
    """The comparison key: NFC(casefold(NFC(text))); casefold can leave text unnormalized."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def normalize_name(raw: str) -> tuple[str, str]:
    """Return (name, name_key) or raise ComicsNameError."""
    name = unicodedata.normalize("NFC", raw.strip())
    # NFC can in principle expose new edge whitespace; trim again.
    name = name.strip()
    if not name:
        raise ComicsNameError("empty")
    if len(name) > MAX_NAME_LENGTH:
        raise ComicsNameError("too_long")
    if any(ch in _FORBIDDEN or unicodedata.category(ch) in _FORBIDDEN_CATEGORIES for ch in name):
        raise ComicsNameError("forbidden_character")
    if name.endswith(".") or name.endswith(" "):
        raise ComicsNameError("trailing_dot_or_space")
    if name.split(".", 1)[0].strip().upper() in _RESERVED:
        raise ComicsNameError("reserved_name")
    return name, name_key_of(name)
