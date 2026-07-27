# build_lemma_clusters: Semantic Clustering of Unmatched OCR Lemmas

Status: done
Follow-ups: docs/superpowers/specs/2026-07-02-draft-multi-model-concept-embeddings.md, docs/superpowers/specs/2026-07-03-draft-concepts-from-clusters.md, docs/superpowers/specs/2026-07-05-build-lemma-clusters-cli-overrides.md

**Date:** 2026-07-01  
**Scope:** new `batch/build_lemma_clusters.py` + `batch/utils/clustering.py`

---

## Summary

`build_bow` already identifies which lemmas from OCR texts are not covered by any rule (`bow.unmatched.<env>.json`). Currently ~33% of lemmas fall into this bucket. This batch takes that unmatched set, embeds each lemma with a multilingual sentence-embedding model, groups semantically similar lemmas into clusters using HDBSCAN, then calls Ollama to propose a concept name for each cluster. Output is a human-readable YAML file that a human (or agent) uses to write new rules or extend existing concept definitions.

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
| Embedding model | `sbert` (`sentence-transformers`, `paraphrase-multilingual-MiniLM-L12-v2`) — **default**. Empirically validated (see Side Notes) to give real within/across-group separation for `ru`, `en`, and `es`, unlike CLIP. `TEXT_EMBED_MODEL=clip` (`ai/clip.py → ClipModel.embed_text`) remains available as a fallback, primarily for `en`-only runs or environments that can't add the `sentence-transformers` dependency |
| Clustering algorithm | HDBSCAN (`hdbscan.HDBSCAN`, already a project dependency — see `batch/experimental/clusterization.py` for precedent). Density-based, no fixed K, native noise/singleton handling via label `-1` |
| Language | Per-language. `LANGUAGE=all` (default) runs each language in the input file independently. Descriptions-sourced BOW files (`TEXT_SOURCE=descriptions`) are flat (no per-language keys) and are effectively English-only (LLM-generated) — treated as a single implicit `en` block |
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
- Extending concept-embedding storage to support multiple embedding models. `LOOKUP_CONCEPTS` only works when `TEXT_EMBED_MODEL=clip`, because DB concept embeddings (`Storage/models.py` `Concept.embedding`, populated by `batch/build_concept_embeddings.py`) live in CLIP's vector space. Making `LOOKUP_CONCEPTS` work against `sbert`-clustered centroids requires a data-model change (separate table or model-discriminant column) — tracked as intent only in `2026-07-02-draft-multi-model-concept-embeddings.md` (draft, no design yet), and out of scope here. `LOOKUP_CONCEPTS` stays CLIP-only for now.

---

## New Components

### `batch/build_lemma_clusters.py`

Main entry point.

**Pipeline per language:**
1. Load unmatched lemmas + frequencies from input JSON for the target language.
2. Embed each lemma via the configured text encoder (`sbert` default, `clip` fallback) → `dict[str, np.ndarray]`.
3. L2-normalize embeddings and run HDBSCAN (`metric='euclidean'`, `min_cluster_size=MIN_CLUSTER_SIZE`) → cluster labels, with `-1` marking noise.
4. Group lemmas by label; label `-1` lemmas become singletons, all other labels become clusters.
5. For each cluster: call Ollama with lemmas + frequencies → get concept name.
6. (Optional, `TEXT_EMBED_MODEL=clip` only) Compare cluster centroid embedding to concept embeddings in DB → attach nearest concept.
7. Write YAML output.

**Sketch:**

```python
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
) -> None: ...

async def main() -> None:
    await run(
        input_file   = os.getenv("BOW_UNMATCHED_FILE"),
        output_file  = os.getenv("CLUSTER_OUTPUT_FILE"),
        language     = os.getenv("LANGUAGE", "all"),
        min_cluster_size = int(os.getenv("MIN_CLUSTER_SIZE", "2")),
        min_samples      = int(os.getenv("MIN_SAMPLES")) if os.getenv("MIN_SAMPLES") else None,
        cluster_selection_epsilon = float(os.getenv("CLUSTER_SELECTION_EPSILON", "0.0")),
        embed_model  = os.getenv("TEXT_EMBED_MODEL", "sbert"),
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2"),
        ollama_enabled   = os.getenv("OLLAMA_ENABLED", "true").lower() == "true",
        lookup_concepts  = os.getenv("LOOKUP_CONCEPTS", "false").lower() == "true",
    )
```

---

### `batch/utils/clustering.py`

Reusable clustering primitive — extracted here so other batches that embed items and need to group similar ones (e.g. description clustering) can reuse it.

```python
def build_clusters_from_embeddings(
    keys: list[str],
    embeddings: np.ndarray,           # shape (N, D), L2-normalised
    min_cluster_size: int,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
) -> dict[int, list[str]]:
    """
    Run HDBSCAN over L2-normalised embeddings (metric='euclidean' — for
    normalised vectors this is a monotonic function of cosine similarity,
    since ||a-b||² = 2 - 2·cos_sim(a,b)). Returns {label: [keys]}, where
    label -1 is HDBSCAN's noise bucket (maps directly to "singletons").

    Complexity: driven by hdbscan's internal tree construction, roughly
    O(N²) at this scale — suitable up to ~5 000 items. For larger N
    consider approximate nearest-neighbour search.
    """
```

**Implementation notes:**
- Embeddings must be L2-normalised before calling HDBSCAN with `metric='euclidean'` (`hdbscan` does not support `metric='cosine'` directly — confirmed empirically, it raises `Unrecognized metric 'cosine'`).
- `cluster_selection_epsilon` (HDBSCAN's native param, in euclidean distance space) replaces the old fixed cosine-similarity threshold idea; default `0.0` (no epsilon-based merging) is fine as a starting point — see Configuration.
- No int-mapping needed: `keys[i]` corresponds to `embeddings[i]`; group by returned label using the same index alignment.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BOW_UNMATCHED_FILE` | *(required)* | Input: BOW unmatched JSON from `build_bow` |
| `CLUSTER_OUTPUT_FILE` | *(required)* | Output: YAML cluster report |
| `LANGUAGE` | `all` | `all` / `ru` / `en` / `es` — which language blocks to process. If the input file has no per-language keys (flat dict — happens for `TEXT_SCOPE=all` against a descriptions-sourced BOW file), it is treated as a single `en` block |
| `TEXT_SCOPE` | `unmatched` | `unmatched` reads `BOW_UNMATCHED_FILE`; `all` reads `BOW_OUTPUT_FILE` |
| `MIN_CLUSTER_SIZE` | `2` | HDBSCAN `min_cluster_size` — smallest group of lemmas HDBSCAN will call a cluster; everything else is noise → singleton |
| `MIN_SAMPLES` | *(unset → HDBSCAN defaults to `MIN_CLUSTER_SIZE`)* | HDBSCAN `min_samples` — higher values make clustering more conservative (more noise/singletons) |
| `CLUSTER_SELECTION_EPSILON` | `0.0` | HDBSCAN `cluster_selection_epsilon`, in euclidean distance space over L2-normalised embeddings (`dist = sqrt(2 - 2·cos_sim)`). Raise slightly (e.g. `0.15`, ≈ cos_sim 0.99) to merge very close small clusters; `0.0` disables epsilon-based merging |
| `CLUSTER_SELECTION_METHOD` | `eom` | HDBSCAN `cluster_selection_method` — `eom` (default, excess-of-mass; prefers fewer, larger, more stable clusters) or `leaf` (selects finer leaf clusters from the condensed tree instead of merging into the most stable ancestor). Empirically, dense text corpora with lots of generic/common vocabulary tend to produce one dominant "catch-all" cluster under `eom`; `leaf` breaks that up into many smaller, more specific clusters at the cost of more lemmas landing as singletons — worth trying when a run produces a single oversized cluster per language |
| `TEXT_EMBED_MODEL` | `sbert` | `sbert` (default, multilingual — see Side Notes) or `clip` (fallback, `en`-only recommended; required for `LOOKUP_CONCEPTS`) |
| `OLLAMA_MODEL` | `qwen2` | Ollama model for cluster naming. `qwen2` has strong Cyrillic coverage |
| `OLLAMA_ENABLED` | `true` | Set to `false` to skip Ollama (fast iteration / debugging) |
| `LOOKUP_CONCEPTS` | `false` | Compare cluster centroid to DB concept embeddings; attach nearest match. Requires `TEXT_EMBED_MODEL=clip` (see Error Handling) |

Every variable above also has a same-purpose CLI flag on `batch/build_lemma_clusters.py` that overrides it when passed (e.g. `--cluster-selection-method leaf` overrides `CLUSTER_SELECTION_METHOD`; `--ollama-enabled`/`--no-ollama-enabled` overrides `OLLAMA_ENABLED`). Running with no CLI flags reproduces the pure-env-var behavior described above exactly — the CLI is a convenience for ad hoc runs, not a replacement for the env-var-driven pipeline usage.

---

## Output Format

One YAML file per run (all languages, one file).

```yaml
generated_at: "2026-07-01T12:34:56"
parameters:
  min_cluster_size: 2
  min_samples: null
  cluster_selection_epsilon: 0.0
  embed_model: sbert
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

    singletons:                         # HDBSCAN noise (label -1) — no semantic neighbour found
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

### `graph/uf.py` — not used

`UnionFind` is not used by this batch. HDBSCAN replaces the originally-proposed pairwise-threshold + UnionFind approach (see Design Decisions) to avoid single-linkage chaining and to get native noise/singleton handling. This is unrelated to `graph/uf.py`'s other uses elsewhere in the codebase — no changes there.

### `batch/utils/clustering.py` — new utility, consistent with existing precedent

`build_clusters_from_embeddings` wraps `hdbscan.HDBSCAN` and is extracted here (rather than inlined in `build_lemma_clusters.py`) so other batches that embed items and need to group similar ones (e.g. description clustering) can reuse it. This also improves codebase consistency rather than introducing a second clustering paradigm: `batch/experimental/clusterization.py` already clusters image embeddings with `hdbscan.HDBSCAN(min_cluster_size=5, metric='euclidean')`; this batch follows the same precedent for lemma embeddings.

`clusterize.py` itself is **not changed** in this spec — it still reads precomputed distances from `TmpDuplicates` in DB via its own (non-HDBSCAN) approach. Migrating it to `hdbscan` or to this utility is a separate task.

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
| Configured embed model (`sbert` or `clip`) not available | Fail immediately |
| `TEXT_SCOPE=all` reads a flat (no per-language keys) BOW file | Treat as a single implicit `en` block — do not error |
| `LOOKUP_CONCEPTS=true` but DB has no concept embeddings | Warn, set `nearest_concept: null` for all clusters, continue |
| `LOOKUP_CONCEPTS=true` with `TEXT_EMBED_MODEL=sbert` | Fail fast with a clear error — `sbert` centroids are not comparable to DB concept embeddings (CLIP vector space). Do not silently produce garbage `nearest_concept` results |

---

## Implementation Order

1. **`batch/utils/clustering.py`** — standalone, wraps `hdbscan` (already a dependency; no new install needed for clustering itself).
2. **`batch/build_lemma_clusters.py` without Ollama** (`OLLAMA_ENABLED=false`) — validate clustering output on a real unmatched JSON file with `TEXT_EMBED_MODEL=sbert`, tune `MIN_CLUSTER_SIZE` / `MIN_SAMPLES` / `CLUSTER_SELECTION_EPSILON`.
3. **Add Ollama naming** — wire in once cluster quality is confirmed.
4. **Add `LOOKUP_CONCEPTS` path** — optional, `TEXT_EMBED_MODEL=clip` only, requires concept embeddings to exist in DB.
5. **Manual review** of first output on each environment; adjust `MIN_CLUSTER_SIZE` / `MIN_SAMPLES` if clusters are too broad or too narrow.

---

## Side Notes

- **Embedding model choice is empirically settled, not speculative.** A throwaway benchmark (pairwise cosine similarity, within-group vs across-group, for curated related/unrelated lemma sets in `ru`/`en`/`es`) compared CLIP (`ViT-B-32`, `openai` pretrained) against `sbert` (`paraphrase-multilingual-MiniLM-L12-v2`):

  | Group pair | CLIP within vs across | sbert within vs across |
  |---|---|---|
  | ru "meme" family vs ru control (unrelated) | 0.969 vs 0.925 (gap 0.04) | 0.866 vs 0.494 (gap 0.37) |
  | ru "metalhead" eratives vs ru control | 0.877 vs 0.875 (gap ~0.00 — **no separation**) | 0.618 vs 0.371 (gap 0.25) |
  | ru "meme" vs ru "metalhead" (different concepts) | 0.969 vs 0.916 (barely distinguishable from each other) | 0.866 vs 0.470 (correctly distinguished) |
  | en "lol" family vs en control | 0.986 vs 0.877 (gap 0.11) | 0.632 vs 0.343 (gap 0.29) |
  | es animals vs es vehicles (different concepts) | 0.882 vs 0.842 (gap ~0.04, weak) | 0.404 vs 0.269 (gap 0.14, present) |
  | es animals/vehicles vs es control | ~0.84 vs ~0.84 (**no separation**) | ~0.42 vs ~0.33 (gap present) |

  CLIP also has a very high, mostly-flat similarity floor (~0.83–0.93) across *all* pairs regardless of relatedness, which compresses the usable dynamic range and makes a single global similarity threshold hard to pick well. `sbert` produces meaningfully wider, more separated ranges (~0.27–0.93) across all three languages, including for eratives (the exact case this batch exists to catch) and for Spanish, where CLIP showed effectively zero separation.

  **Conclusion: `sbert` is the default embedding model for this batch, not just an alternative.** CLIP remains available (`TEXT_EMBED_MODEL=clip`) as a fallback for `en`-only runs, and is *required* (not optional) if `LOOKUP_CONCEPTS=true`, since DB concept embeddings are CLIP-based (see Non-Goals and Error Handling).

  `sentence-transformers==5.6.0` has been added to `requirements.txt` (main ML/batch stack, not `requirements-dev.txt`) as part of this spec update.

- HDBSCAN's `min_cluster_size` / `min_samples` are the primary tuning knobs (see Configuration). Start with defaults (`min_cluster_size=2`, `min_samples` unset) and adjust based on manual review (Implementation Order step 5) rather than guessing up front — `sbert`'s wider similarity range makes these easier to reason about than the old fixed cosine threshold.
- The O(N²)-ish pairwise cost is fine for the expected scale (hundreds of unmatched lemmas per language). If the unmatched set ever exceeds ~5 000 items, consider approximate nearest-neighbour search (e.g. `faiss`) ahead of HDBSCAN, or HDBSCAN's own approximate/parallel modes.
