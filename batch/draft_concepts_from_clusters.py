import re


def slugify(name: str | None) -> str:
    if not name:
        return ""
    text = name.strip().strip('"').strip("'").lower()
    text = re.sub(r'[^a-z0-9Ѐ-ӿ]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text


def top_lemma(cluster: dict) -> str:
    return next(iter(cluster["members"]))


def collect_existing_words(concepts_data: dict | None) -> set[str]:
    words: set[str] = set()
    for concept in (concepts_data or {}).values():
        for w in (concept.get("words") or []):
            words.add(w)
        for fe in (concept.get("fuzzy") or []):
            words.add(fe["word"])
    return words


def collect_existing_keys(concepts_data: dict | None) -> set[str]:
    return set((concepts_data or {}).keys())


def collect_declared_tags(tags_data: dict) -> set[str]:
    return set((tags_data.get("tags") or {}).keys())


def resolve_key(ollama_concept: str | None, lemma: str, existing_keys: set[str]) -> str:
    base = slugify(ollama_concept) or slugify(lemma)
    key = base
    suffix = 2
    while key in existing_keys:
        key = f"{base}_{suffix}"
        suffix += 1
    return key