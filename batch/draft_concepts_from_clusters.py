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


def select_top_clusters(
    clusters: list[dict],
    existing_words: set[str],
    top: int,
) -> tuple[list[dict], list[dict]]:
    """
    Walk clusters in order (already frequency-descending, per
    build_lemma_clusters). Returns (accepted, skipped): accepted has at
    most `top` clusters whose top lemma is not in existing_words; skipped
    holds every cluster visited and passed over because it was already
    covered. Clusters beyond what's needed to fill `top` slots are never
    visited.
    """
    accepted: list[dict] = []
    skipped: list[dict] = []
    for cluster in clusters:
        if len(accepted) >= top:
            break
        lemma = top_lemma(cluster)
        if lemma in existing_words:
            skipped.append(cluster)
            continue
        accepted.append(cluster)
    return accepted, skipped


def format_concept_block(key: str, cluster: dict, lemma: str) -> str:
    ollama_concept = cluster.get("ollama_concept")
    if ollama_concept:
        comment = (
            f'# Ollama suggested: "{ollama_concept}" '
            f'(freq={cluster["total_frequency"]}, size={cluster["size"]}, from build_lemma_clusters)'
        )
    else:
        comment = "# from build_lemma_clusters (no Ollama name)"

    lines = [comment, f"{key}:", "  words:"]
    for member in cluster["members"]:
        lines.append(f"  - {member}")
    lines.append("  votes:")
    lines.append(f"    тема:{lemma}: 1.0")
    return "\n".join(lines) + "\n"


def format_tag_declaration(lemma: str) -> str:
    return f"  тема:{lemma}: {{}}\n"


def append_to_file(path: str, text: str) -> None:
    with open(path, encoding="utf-8") as f:
        existing = f.read()
    prefix = "" if existing.endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + text)