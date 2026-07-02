import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from batch.utils.clustering import build_clusters_from_embeddings
from repository.concepts import ConceptsRepository
from Storage.db import AsyncSessionLocal


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


def build_cluster_records(
    groups: dict[int, list[str]],
    frequencies: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """
    Turn HDBSCAN's {label: [lemma, ...]} into (clusters, singletons).
    Clusters are sorted by total_frequency descending and numbered from 1.
    Singleton (label -1) lemmas and cluster members keep their relative
    order from `frequencies` (build_bow writes frequency-descending order).
    """
    order = {lemma: i for i, lemma in enumerate(frequencies)}

    cluster_groups = [members for label, members in groups.items() if label != -1]
    cluster_groups.sort(key=lambda members: sum(frequencies[m] for m in members), reverse=True)

    clusters = []
    for idx, members in enumerate(cluster_groups, start=1):
        ordered_members = sorted(members, key=lambda m: order[m])
        member_freqs = {m: frequencies[m] for m in ordered_members}
        clusters.append({
            "id": idx,
            "ollama_concept": None,
            "total_frequency": sum(member_freqs.values()),
            "size": len(member_freqs),
            "members": member_freqs,
        })

    singleton_lemmas = sorted(groups.get(-1, []), key=lambda m: order[m])
    singletons = [{"lemma": lemma, "frequency": frequencies[lemma]} for lemma in singleton_lemmas]

    return clusters, singletons


def write_yaml_output(output_file: str, parameters: dict, languages: dict) -> None:
    os.makedirs(Path(output_file).parent, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": parameters,
        "languages": languages,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def nearest_concept(centroid: np.ndarray, concept_rows: list[tuple[int, str, np.ndarray]]) -> dict | None:
    if not concept_rows:
        return None

    def distance(row):
        _, _, embedding = row
        return 1.0 - float(np.dot(centroid, np.asarray(embedding)))

    best = min(concept_rows, key=distance)
    concept_id, name, _ = best
    return {"name": name, "concept_id": concept_id, "cosine_distance": round(distance(best), 4)}


def _get_embedder(name: str):
    if name == "sbert":
        from ai.sbert import SbertModel
        return SbertModel()
    if name == "clip":
        from ai.clip import ClipModel
        return ClipModel()
    raise ValueError(f"Unknown TEXT_EMBED_MODEL: {name!r}")


def _get_namer(model: str):
    from ai.ollama import OllamaConceptNamer
    return OllamaConceptNamer(model=model)


async def _load_concept_rows() -> list[tuple[int, str, np.ndarray]]:
    async with AsyncSessionLocal() as session:
        repo = ConceptsRepository(session)
        rows = await repo.get_all_with_embeddings()
        return [(r.id, r.name, np.asarray(r.embedding)) for r in rows]


def _cluster_centroid(members: dict, embeddings_by_lemma: dict) -> np.ndarray:
    vectors = np.stack([embeddings_by_lemma[lemma] for lemma in members])
    centroid = vectors.mean(axis=0)
    return centroid / np.linalg.norm(centroid)


def _process_language(
    lang: str,
    lemma_freqs: dict[str, int],
    embedder,
    namer,
    min_cluster_size: int,
    min_samples: int | None,
    cluster_selection_epsilon: float,
    lookup_concepts: bool,
    concept_rows: list[tuple[int, str, np.ndarray]],
) -> dict:
    if len(lemma_freqs) < 2:
        print(f"WARNING: language {lang!r} has fewer than 2 lemmas, skipping clustering")
        singletons = [{"lemma": lemma, "frequency": freq} for lemma, freq in lemma_freqs.items()]
        return {"clusters": [], "singletons": singletons}

    if len(lemma_freqs) > 5000:
        print(f"WARNING: language {lang!r} has {len(lemma_freqs)} lemmas (>5000) - O(N^2) clustering cost is high")

    keys = list(lemma_freqs)
    embeddings_by_lemma = {lemma: embedder.embed_text(lemma) for lemma in keys}
    embeddings = np.stack([embeddings_by_lemma[lemma] for lemma in keys])
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size, min_samples, cluster_selection_epsilon)
    clusters, singletons = build_cluster_records(groups, lemma_freqs)

    if namer is not None:
        for cluster in clusters:
            cluster["ollama_concept"] = namer.name_cluster(lang, list(cluster["members"].items()))

    if lookup_concepts:
        for cluster in clusters:
            if concept_rows:
                centroid = _cluster_centroid(cluster["members"], embeddings_by_lemma)
                cluster["nearest_concept"] = nearest_concept(centroid, concept_rows)
            else:
                cluster["nearest_concept"] = None

    return {"clusters": clusters, "singletons": singletons}


async def run(
    input_file: str,
    output_file: str,
    language: str = "all",
    min_cluster_size: int = 2,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
    embed_model: str = "sbert",
    ollama_model: str = "qwen2",
    ollama_enabled: bool = True,
    lookup_concepts: bool = False,
) -> None:
    if lookup_concepts and embed_model != "clip":
        raise ValueError(
            "LOOKUP_CONCEPTS=true requires TEXT_EMBED_MODEL=clip "
            "(DB concept embeddings are CLIP-based, not comparable to sbert centroids)"
        )

    data = load_lemma_source(input_file)
    blocks = resolve_language_blocks(data, language)

    embedder = _get_embedder(embed_model)
    namer = _get_namer(ollama_model) if ollama_enabled else None

    concept_rows: list[tuple[int, str, np.ndarray]] = []
    if lookup_concepts:
        concept_rows = await _load_concept_rows()
        if not concept_rows:
            print("WARNING: LOOKUP_CONCEPTS=true but no concept embeddings found in DB")

    languages_output = {}
    for lang, lemma_freqs in blocks.items():
        languages_output[lang] = _process_language(
            lang, lemma_freqs, embedder, namer, min_cluster_size, min_samples,
            cluster_selection_epsilon, lookup_concepts, concept_rows,
        )

    parameters = {
        "min_cluster_size": min_cluster_size,
        "min_samples": min_samples,
        "cluster_selection_epsilon": cluster_selection_epsilon,
        "embed_model": embed_model,
        "ollama_model": ollama_model,
    }
    write_yaml_output(output_file, parameters, languages_output)
    print(f"Written to {output_file}")


async def main() -> None:
    text_scope = os.getenv("TEXT_SCOPE", "unmatched")
    input_file = os.getenv("BOW_OUTPUT_FILE") if text_scope == "all" else os.getenv("BOW_UNMATCHED_FILE")
    output_file = os.getenv("CLUSTER_OUTPUT_FILE")

    if not input_file:
        raise SystemExit(
            "BOW_OUTPUT_FILE must be set when TEXT_SCOPE=all, otherwise BOW_UNMATCHED_FILE must be set"
        )
    if not output_file:
        raise SystemExit("CLUSTER_OUTPUT_FILE must be set")

    await run(
        input_file=input_file,
        output_file=output_file,
        language=os.getenv("LANGUAGE", "all"),
        min_cluster_size=int(os.getenv("MIN_CLUSTER_SIZE", "2")),
        min_samples=int(os.getenv("MIN_SAMPLES")) if os.getenv("MIN_SAMPLES") else None,
        cluster_selection_epsilon=float(os.getenv("CLUSTER_SELECTION_EPSILON", "0.0")),
        embed_model=os.getenv("TEXT_EMBED_MODEL", "sbert"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2"),
        ollama_enabled=os.getenv("OLLAMA_ENABLED", "true").lower() == "true",
        lookup_concepts=os.getenv("LOOKUP_CONCEPTS", "false").lower() == "true",
    )


if __name__ == "__main__":
    asyncio.run(main())
