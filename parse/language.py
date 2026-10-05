"""Script-based language guess, used only for degraded results (P3 spec, decision 4). Pure."""
import unicodedata

from parse.schema import LANGUAGES

SPANISH_MARKERS = set("¿¡ñÑ")
SPANISH_WORDS = {"el", "la", "los", "las", "que", "una", "por", "con", "para", "pero", "como", "muy", "mi", "es",
                 "del", "al"}


def _script(char: str) -> str | None:
    if not char.isalpha():
        return None
    name = unicodedata.name(char, "")
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    if name.startswith("LATIN"):
        return "latin"
    return None


def guess_language(text: str, hint: str | None = None) -> str:
    cyrillic = latin = 0
    for char in text:
        script = _script(char)
        if script == "cyrillic":
            cyrillic += 1
        elif script == "latin":
            latin += 1
    if cyrillic == 0 and latin == 0:
        return hint if hint in LANGUAGES else "other"
    if cyrillic > latin:
        return "ru"
    words = {word.strip(".,;:!?¿¡\"'()").lower() for word in text.split()}
    if any(char in SPANISH_MARKERS for char in text) or len(words & SPANISH_WORDS) >= 2:
        return "es"
    return "en"
