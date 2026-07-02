import json


def load_lemma_source(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_language_blocks(data: dict, language: str) -> dict[str, dict[str, int]]:
    """
    Normalize raw BOW JSON into {lang: {lemma: freq}}.

    Flat dicts (descriptions-sourced BOW files have no per-language keys) are
    treated as a single implicit "en" block. `language="all"` returns every
    block found; a specific language returns only that block (empty dict +
    warning if the language is absent from the input).
    """
    is_flat = not data or not isinstance(next(iter(data.values())), dict)
    normalized = {"en": data} if is_flat else data

    if language == "all":
        return normalized

    if language not in normalized:
        print(f"WARNING: language {language!r} not found in input file, skipping")
        return {}

    return {language: normalized[language]}
