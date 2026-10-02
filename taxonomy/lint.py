import re

from taxonomy.model import FREE_TEXT, LANGUAGES, VOCABULARY, Taxonomy

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_DEFINITION_CHARS = 200
MAX_AXIS_NAME_CHARS = 16
REQUIRED_AXES = ("situation", "emotion", "stance", "topic", "tone")
SIZE_RANGES = {"emotion": (20, 30), "stance": (10, 18), "tone": (8, 12), "topic": (12, 20)}


def lint(tax: Taxonomy, *, check_sizes: bool = True) -> list[str]:
    """Return human-readable problems with the vocabulary content; an empty list means clean."""
    problems: list[str] = []
    for axis in REQUIRED_AXES:
        if axis not in tax.axes:
            problems.append(f"missing required axis {axis!r}")
    if "situation" in tax.axes and tax.axes["situation"].type != FREE_TEXT:
        problems.append("situation: must be a free_text axis")

    for name, spec in tax.axes.items():
        if len(name) > MAX_AXIS_NAME_CHARS:
            problems.append(f"axis {name!r}: name longer than {MAX_AXIS_NAME_CHARS} characters")
        if spec.type != VOCABULARY:
            continue
        values = tax.values.get(name, {})
        if check_sizes and name in SIZE_RANGES:
            low, high = SIZE_RANGES[name]
            if not low <= len(values) <= high:
                problems.append(f"{name}: {len(values)} values outside {low}-{high}")
        for key, value in values.items():
            if not KEY_RE.match(key):
                problems.append(f"{name}: key {key!r} must be lowercase snake_case, at most 48 characters")
            if not value.definition.strip():
                problems.append(f"{name}.{key}: empty definition")
            elif len(value.definition) > MAX_DEFINITION_CHARS:
                problems.append(f"{name}.{key}: definition longer than {MAX_DEFINITION_CHARS} characters")
            for language in LANGUAGES:
                if not value.labels.get(language, "").strip():
                    problems.append(f"{name}.{key}: missing {language} label")
    return problems
