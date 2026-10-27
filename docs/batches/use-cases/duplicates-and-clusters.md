# Use case: duplicates and clusters

Status: draft (verified 2026-10-10). Specs: `2026-07-25-duplicate-clustering-incremental-design.md`,
`2026-09-15-text-heavy-classifier.md`, `2026-09-17-ocr-text-embeddings.md`,
`2026-09-18-text-embedding-duplicate-matching.md`, `2026-09-19-ocr-lemma-overlap-corroboration.md`,
`2026-09-20-clusterize-oversized-cluster-data-loss.md`.

Execution order (Graphviz): [diagrams/duplicates-refresh.svg](../diagrams/duplicates-refresh.svg)

Two independent notions of "duplicate":

| | Exact | Near |
|---|---|---|
| Signal | SHA-256 of bytes (`images.content_hash`) | CLIP vectors and OCR-text vectors |
| Jobs | `ingest_hash_dedup` (intake), `detect_file_duplicates` (existing corpus) | `rebuild_duplicates`, `ingest_find_duplicates`, then `clusterize` |
| Outcome | Duplicates rejected or flagged | Candidate pairs, clusters for human review in the UI |

## Near-duplicate pipeline for the active library

```mermaid
flowchart TD
    CLIP[(embeddings<br/>CLIP)] --> RD
    TH[classify_text_heavy] --> OTE[build_ocr_text_embeddings] --> RD
    LEM[(ocr_lemmas)] -. corroboration .-> RD
    RD["rebuild_duplicates<br/>3 probes, HNSW KNN"] --> TMP[(tmp_duplicates<br/>pairs + distance_source)]
    RD -->|chain unless --no-chain| CL[clusterize]
    TMP --> CL
    CL --> CLU[(tmp_clusters)]
    CLU --> UI[Explore, Duplicates<br/>human keep / flag]
    UI --> FLAG[(flagged images)]
    FLAG --> MF[move_flagged] --> UD[unregister_deleted_images] --> RS[remove_singletons]
```

### The three probes (each `rebuild_duplicates` run)

```mermaid
flowchart LR
    subgraph P1[General CLIP probe]
        A1[all pairs below DUPLICATES.THRESHOLD<br/>excluding pairs where both sides have an OCR-text embedding]
        A2[incremental: only images with no tmp_duplicates row for this source]
    end
    subgraph P2[Safety-net CLIP probe]
        B1[tight threshold, text-heavy vs text-heavy]
        B2[NOT incremental: always re-probes every active text-heavy image<br/>relies on ON CONFLICT DO NOTHING]
    end
    subgraph P3[OCR-text probe]
        C1[text-heavy vs text-heavy by OCR-text embedding]
        C2[needs lemma overlap: coefficient 0.2, at least 3 lemmas]
    end
```

Do not "simplify" the safety net into sharing the general probe's incremental marker: that would silently and
permanently disable it for affected images. `--k` and `--threshold` override only the general probe. `--full` clears
the active-library pairs and re-probes everything.

### Clustering rules (`clusterize`)

- Union-find over `tmp_duplicates`, gated per `distance_source`: CLIP pairs under `PROXIMITY_THRESHOLD` (0.05),
  OCR-text pairs under `PROXIMITY_THRESHOLD_OCR_TEXT` (0.05, a separate constant).
- Clusters larger than `clustering.splitting.max_cluster_size` are re-clustered at progressively tighter thresholds
  (step `decrement`, stop at `floor`, or when a tightening step severs every remaining edge). Such clusters are accepted
  oversized instead of split further. Members left without an edge become implicit singletons, never size-1 clusters.
- `clustering.splitting.enabled: false` restores write-everything-as-is.

## Run matrix

| Job | Shell | Admin | Sched | Chain |
|---|---|---|---|---|
| `classify_text_heavy` | `--status` | yes | no | none |
| `build_ocr_text_embeddings` | `--status` | yes | no | none |
| `rebuild_duplicates` | `--full --k --threshold --no-chain` | yes | no | `clusterize` |
| `clusterize` | yes | yes | no | none |
| `ingest_find_duplicates` | `--tier tier_a\|tier_b --k` | via `ingest_find_duplicates_tier_b` / `ingest_auto_prep` | no | none |
| `detect_file_duplicates` | yes | no | no | none |
| `move_reference_duplicates` | `--reference-dir --dry-run`; moves unregistered `BASE_PATH` files identical to a reference-dir file into `possible_duplicates/` | no | no | none |
| `deduplicate_ocr_texts` | yes | no | no | none |

Recommended full refresh on the existing corpus:
`classify_text_heavy` then `build_ocr_text_embeddings` then `build_ocr_lemmas --incremental`, then
`rebuild_duplicates` (which chains `clusterize`). For an in-flight ingestion batch, the same three prerequisite steps
run with `--status pending` (see [ingestion.md](ingestion.md)).

## Byte-level duplicates

`detect_file_duplicates`: hashes every registered file (backfilling `content_hash`), groups identical hashes, verifies
content, keeps the oldest by `created_at` and flags the rest. A group that already has a flagged member is treated as
reviewed and skipped. It also records the keeper as the winner of each flagged copy (popularity, metrics key
`popularity_skipped`). Follow with `move_flagged`.

## Related

- `/review-duplicates` command (`.claude/commands/review-duplicates.md`, fed by `tools/agent_duplicates.py`) reviews one
  cluster and marks lower-quality images excluded.
- Safe-guarding note: duplicates never use descriptions or notes (`docs/data-flow.md` section 3).
- `docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md` covers the `tmp_duplicates` FK index incident and recovery.
