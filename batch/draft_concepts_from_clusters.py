import argparse
import os
import re

import yaml


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


def _yaml_scalar(value: str) -> str:
    """Render `value` as a YAML scalar, quoting it only when needed.

    yaml.safe_dump() on a bare scalar appends a document-end marker
    ("...") for plain (unquoted) scalars but not for quoted ones, so we
    strip that marker if present. This keeps normal lemmas unquoted
    (matching how a human would write them) while safely quoting values
    that collide with YAML reserved tokens (no, yes, on, off, null,
    true, false, numeric-looking strings, etc.).
    """
    dumped = yaml.safe_dump(value, allow_unicode=True).strip()
    if dumped.endswith("..."):
        dumped = dumped[: -len("...")].strip()
    return dumped


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
        lines.append(f"  - {_yaml_scalar(member)}")
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


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(
    cluster_file: str,
    env: str,
    language: str,
    top: int,
    data_dir: str = "batch/data/tagging",
) -> None:
    cluster_data = load_yaml(cluster_file)
    languages = cluster_data.get("languages") or {}
    if language not in languages:
        raise ValueError(f"Language {language!r} not found in {cluster_file}")
    clusters = languages[language].get("clusters") or []

    concepts_path = os.path.join(data_dir, f"concepts.{env}.yaml")
    tags_path = os.path.join(data_dir, f"tags.{env}.yaml")

    if not os.path.exists(concepts_path):
        raise FileNotFoundError(f"{concepts_path} does not exist")
    if not os.path.exists(tags_path):
        raise FileNotFoundError(f"{tags_path} does not exist")

    concepts_data = load_yaml(concepts_path) or {}
    tags_data = load_yaml(tags_path) or {}

    existing_words = collect_existing_words(concepts_data)
    existing_keys = collect_existing_keys(concepts_data)
    declared_tags = collect_declared_tags(tags_data)

    accepted, skipped = select_top_clusters(clusters, existing_words, top)

    added = []
    for cluster in accepted:
        lemma = top_lemma(cluster)
        key = resolve_key(cluster.get("ollama_concept"), lemma, existing_keys)
        existing_keys.add(key)

        append_to_file(concepts_path, format_concept_block(key, cluster, lemma))

        tag = f"тема:{lemma}"
        if tag not in declared_tags:
            append_to_file(tags_path, format_tag_declaration(lemma))
            declared_tags.add(tag)

        added.append((key, lemma, cluster))

    _print_summary(added, skipped, top, concepts_path, tags_path)


def _print_summary(added, skipped, top, concepts_path, tags_path):
    for key, lemma, cluster in added:
        print(f"  + {key} (тема:{lemma}, {cluster['size']} members, ollama: {cluster.get('ollama_concept')!r})")
    for cluster in skipped:
        print(f"  - skipped cluster with top lemma {top_lemma(cluster)!r} (already covered)")
    print(f"Added {len(added)} concept(s) (requested {top}) to {concepts_path} and {tags_path}")
    if len(added) < top:
        print(f"WARNING: only {len(added)} non-colliding cluster(s) were available (requested {top})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-file", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--data-dir", default="batch/data/tagging")
    args = parser.parse_args()

    run(args.cluster_file, args.env, args.language, args.top, args.data_dir)


if __name__ == "__main__":
    main()