# build_lemma_clusters: Semantic Clustering of Unmatched OCR Lemmas

**Date:** 2026-07-01  
**Status:** Proposed  
**Scope:** new `batch/build_lemma_clusters.py` + `batch/utils/clustering.py`

---

## Summary

`build_bow` already identifies which lemmas from OCR texts are not covered by any rule (`bow.unmatched.<env>.json`). Currently ~33% of lemmas fall into this bucket. This batch takes that unmatched set, embeds each lemma with CLIP's text encoder, groups semantically similar lemmas into clusters using pairwise cosine similarity + UnionFind, then calls Ollama to propose a concept name for each cluster. Output is a human-readable YAML file that a human (or agent) uses to write new rules or extend existing concept definitions.

---

## Background and Motivation

The rules engine covers ~67% of OCR lemmas. The remaining 33% is a mix of:
- **Intentional misspellings / eratives** — "stonks", "мемасик", "succ" — related to existing concepts but not spelled canonically.
- **Genuinely new concepts** — things not yet in any rule.
- **OCR noise** — artifacts that shouldn't become rules.

By embedding and clustering, semantically proximal lemmas (including eratives and OCR variations of the same root) surface together. A human can then decide: "this cluster maps to existing concept X — add as synonyms" or "this is a new concept — write a rule."

---

## Design Decisions

| Question | Decision |
|----------|----------|
| What to embed | Individual lemmas (words) from BOW unmatched output — already tokenized, filtered by frequency |
| Input source | BOW unmatched JSON file (`BOW_UNMATCHED_FILE`) — file-in/file-out, composable with build_bow |
| Scope | Unmatched lemmas by default; `TEXT_SCOPE=all` overrides to full BOW output |
| Embedding model | CLIP text encoder (`ai/clip.py → ClipModel.embed_text`) — already available. `TEXT_EMBED_MODEL=sbert` enables sentence-transformers as alternative |
| Clustering algorithm | Pairwise cosine similarity + UnionFind (reuse `graph/uf.py`). Connect lemma pair if similarity ≥ threshold |
| Language | Per-language. `LANGUAGE=all` (default) runs each language in the input file independently |
| Cluster naming | Ollama (text-only model, default `qwen2`) proposes a concept name per cluster; human reviews |
| Nearest concept lookup | Optional, disabled by default. `LOOKUP_CONCEPTS=true` compares cluster centroid to concept embeddings in DB |
| Output format | YAML — human-readable, editable |
| Eratives | Naturally group with root concept via semantic similarity — no special handling needed |
| Output intent | Informs both new rule authoring and extension of existing concept YAML entries |

---

## Non-Goals

- Writing tags to the database automatically (human review required before tagging).
- Replacing `build_bow` — this batch consumes its output.
- Cross-language clustering (lemmas from `ru` and `en` are clustered separately).
- Modifying the UnionFind implementation (existing API is sufficient).

---

## New Components

### `batch/build_lemma_clusters.py`

Main entry point.

**Pipeline per language:**
1. Load unmatched lemmas + frequencies from input JSON for the target language.
2. Embed each lemma via CLIP text encoder → `dict[str, np.ndarray]`.
3. Compute pairwise cosine similarity matrix (numpy).
4. Connect pairs where similarity ≥ `SIMILARITY_THRESHOLD` in UnionFind.
5. Extract clusters (size ≥ `MIN_CLUSTER_SIZE`); collect singletons separately.
6. For each cluster: call Ollama with lemmas + frequencies → get concept name.
7. (Optional) Compare cluster centroid embedding to concept embeddings in DB → attach nearest concept.
8. Write YAML output.

**Sketch:**

```python
async def run(
    input_file: str,
    output_file: str,
    language: str = "all",
    similarity_threshold: float = 0.85,
    min_cluster_size: int = 2,
    embed_model: str = "clip",
    ollama_model: str = "qwen2",
    ollama_enabled: bool = True,
    lookup_concepts: bool = False,
) -> None: ...

async def main() -> None:
    await run(
        input_file   = os.getenv("BOW_UNMATCHED_FILE"),
        output_file  = os.getenv("CLUSTER_OUTPUT_FILE"),
        language     = os.getenv("LANGUAGE", "all"),
        similarity_threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
        min_cluster_size     = int(os.getenv("MIN_CLUSTER_SIZE", "2")),
        embed_model  = os.getenv("TEXT_EMBED_MODEL", "clip"),
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2"),
        ollama_enabled   = os.getenv("OLLAMA_ENABLED", "true").lower() == "true",
        lookup_concepts  = os.getenv("LOOKUP_CONCEPTS", "false").lower() == "true",
    )
```

---

### `batch/utils/clustering.py`

Reusable clustering primitive — extracted here so `clusterize.py` (or future batches) can also adopt it.

```python
def build_clusters_from_embeddings(
    keys: list[str],
    embeddings: np.ndarray,       # shape (N, D), L2-normalised
    threshold: float,             # cosine similarity lower bound
) -> UnionFind:
    """
    Compute pairwise cosine similarities and connect pairs above threshold
    in a UnionFind. Returns the populated UnionFind.

    Complexity: O(N²) — suitable up to ~5 000 items. For larger N consider
    approximate nearest-neighbour search.
    """
```

**Implementation notes:**
- Since CLIP embeddings are L2-normalised, `similarity_matrix = embeddings @ embeddings.T`.
- Upper-triangle iteration avoids double-counting.
- Each key (lemma string) is used directly as the UnionFind item — no int mapping needed.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BOW_UNMATCHED_FILE` | *(required)* | Input: BOW unmatched JSON from `build_bow` |
| `CLUSTER_OUTPUT_FILE` | *(required)* | Output: YAML cluster report |
| `LANGUAGE` | `all` | `all` / `ru` / `en` / `es` — which language blocks to process |
| `TEXT_SCOPE` | `unmatched` | `unmatched` reads `BOW_UNMATCHED_FILE`; `all` reads `BOW_OUTPUT_FILE` |
| `SIMILARITY_THRESHOLD` | `0.85` | Cosine similarity lower bound for connecting two lemmas |
| `MIN_CLUSTER_SIZE` | `2` | Clusters smaller than this are emitted as singletons |
| `TEXT_EMBED_MODEL` | `clip` | `clip` or `sbert` |
| `OLLAMA_MODEL` | `qwen2` | Ollama model for cluster naming. `qwen2` has strong Cyrillic coverage |
| `OLLAMA_ENABLED` | `true` | Set to `false` to skip Ollama (fast iteration / debugging) |
| `LOOKUP_CONCEPTS` | `false` | Compare cluster centroid to DB concept embeddings; attach nearest match |

---

## Output Format

One YAML file per run (all languages, one file).

```yaml
generated_at: "2026-07-01T12:34:56"
parameters:
  similarity_threshold: 0.85
  min_cluster_size: 2
  embed_model: clip
  ollama_model: qwen2

languages:
  ru:
    clusters:
      - id: 1
        ollama_concept: "meme"          # Ollama's suggestion; human edits this
        total_frequency: 298
        size: 4
        members:
          мем: 232
          мемасик: 45
          мемный: 18
          mematic: 3
        # nearest_concept omitted (LOOKUP_CONCEPTS=false)

      - id: 2
        ollama_concept: "metalhead"
        total_frequency: 156
        size: 3
        members:
          металхед: 89
          metalhead: 45
          метлхед: 22

    singletons:                         # size-1 clusters — no semantic neighbour found
      - lemma: "бандосик"
        frequency: 7
      - lemma: "стонкс"
        frequency: 12

  en:
    clusters:
      - id: 1
        ollama_concept: "internet humor"
        total_frequency: 201
        size: 5
        members:
          lol: 120
          lmao: 45
          lmfao: 21
          rofl: 10
          lawl: 5

    singletons:
      - lemma: "yeet"
        frequency: 3
```

**When `LOOKUP_CONCEPTS=true`**, each cluster gains an additional field:

```yaml
nearest_concept:
  name: "Classic Internet Memes"
  concept_id: 42
  cosine_distance: 0.08    # lower = closer
```

---

## Ollama Prompt

One call per cluster. Prompt (system + user):

```
System: You are a concise tagger for a meme image database. Given a list of word forms
        and their frequency of occurrence in image texts, propose a single short concept
        name (1–3 English words) that best describes what they have in common.
        Respond with the concept name only, no explanation.

User:   Language: Russian
        Words and frequencies:
        - мем (232)
        - мемасик (45)
        - мемный (18)
        - mematic (3)
```

Expected response: `meme`

**Failure handling:** if Ollama is unavailable or times out for a cluster, set `ollama_concept: null` in the YAML and continue. Do not abort the whole run.

---

## Reuse and Refactoring

### `graph/uf.py` — no changes

The existing `UnionFind` already supports arbitrary hashable keys (lemma strings work directly), `connect()`, `list_clusters()`, and `get_cluster()`. No extension needed.

### `batch/utils/clustering.py` — new utility

`build_clusters_from_embeddings` is extracted here rather than inlined in `build_lemma_clusters.py` so that:
- Future refactoring of `clusterize.py` can adopt it (replacing its manual int-mapping + distance query with a generic call).
- Other batches that embed items and need to group similar ones (e.g. description clustering) can reuse it.

`clusterize.py` itself is **not changed** in this spec — it still reads precomputed distances from `TmpDuplicates` in DB. Migration of `clusterize.py` to use this utility is a separate task.

---

## Human Review Workflow

After running the batch, a human (or agent) opens the output YAML and for each cluster decides:

1. **Add synonyms to an existing concept** — the cluster members go into the `words:` or `fuzzy:` list of an existing entry in the concept YAML (`batch/data/tagging/<env>.yaml`).
2. **Create a new rule** — the cluster members become keys in a new rule entry in the rules JSON (`batch/data/rules.<env>.json`), with a chosen `key:value` tag.
3. **Discard** — the cluster is OCR noise; no action.
4. **Defer** — cluster needs more investigation; leave in YAML for next review.

The YAML is designed to be edited in-place: add a `decision:` field per cluster, run a future agent/script to apply decisions.

---

## Error Handling and Edge Cases

| Case | Behaviour |
|------|-----------|
| Input file missing | Fail immediately with clear error |
| Language not in input file | Warn and skip (not an error) |
| Fewer than 2 lemmas for a language | Skip clustering for that language, log warning |
| N > 5 000 lemmas | Warn that O(N²) cost is high; proceed anyway; note for future ANN upgrade |
| Ollama timeout / error | Set `ollama_concept: null`, continue |
| CLIP model not available | Fail immediately |
| `LOOKUP_CONCEPTS=true` but DB has no concept embeddings | Warn, set `nearest_concept: null` for all clusters, continue |

---

## Implementation Order

1. **`batch/utils/clustering.py`** — standalone, no dependencies beyond numpy and `graph/uf`.
2. **`batch/build_lemma_clusters.py` without Ollama** (`OLLAMA_ENABLED=false`) — validate clustering output on a real unmatched JSON file, tune `SIMILARITY_THRESHOLD`.
3. **Add Ollama naming** — wire in once cluster quality is confirmed.
4. **Add `LOOKUP_CONCEPTS` path** — optional, requires concept embeddings to exist in DB.
5. **Manual review** of first output on each environment; adjust threshold if clusters are too broad or too narrow.

---

## Side Notes

- `SIMILARITY_THRESHOLD=0.85` is a starting point. CLIP text embeddings for short single words can be noisy — if clusters are too coarse (unrelated words grouped), raise to 0.90; if too few clusters form, lower to 0.80.
- The O(N²) pairwise approach is fine for the expected scale (hundreds of unmatched lemmas per language). If the unmatched set ever exceeds ~5 000 items, replace with approximate nearest-neighbour search (e.g. `faiss`).
- `sbert` alternative: `sentence-transformers` with `paraphrase-multilingual-MiniLM-L12-v2` would give better Russian/English text similarity than CLIP. Add to `requirements.txt` if adopted.
