# S1: data classification for dev/prod sync

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/README.md

The classification is now enforced as code: `sync/registry.py`, rendered in `docs/sync-registry.md` (project P1). The tables below
are the reasoning; the registry is the source of truth.

Source: code reading only (Storage/models.py at alembic head `a9d3e6b1c847`, `docs/image-derived-data.md`, routers and batch
jobs). No database was queried: row counts and sizes are unknown until spike S2. Where the code does not settle a question it is
listed under "Open questions", not guessed.

## Classes

| Class | Meaning | Direction | On prod? |
|---|---|---|---|
| **D** | Dev-origin, served by prod: promoted | dev to prod | yes |
| **P** | Prod-origin reader activity | prod to dev (pull) | yes, owned by prod |
| **X** | Dev-only pipeline or admin scratch | never synced | no |
| **R** | Recomputed on the receiving side | none | built locally |

Rule: prod never writes a D or X table; a pull never changes prod. Dev's own backend also writes P tables when used (S2 finding 3), so a P row on dev is not necessarily from prod. Promotion is limited to
images with `status = active` and no `image_extras.flagged`; every D row below is promoted only for those images.

## Tables

### Core

| Table | Class | Why / notes |
|---|---|---|
| `images` | D | Rows promoted with their UUIDs; `ingestion_batch_id` set to NULL on prod; `popularity` promoted as a value (cache of dev-only absorptions). Only active, unflagged |
| `image_extras` | D | `remarks` only; flagged rows are not promoted, so `flagged` is always false on prod |
| `image_metrics` | X | OCR timing scratch |
| `image_processing_status` | X | Skip markers for pipelines; prod runs none (see Open question 4) |
| `processing_errors` | X | Model exists, no writers found |
| `batch_runs` | X | Referencing FKs are SET NULL |
| `process_events` | X | Business metrics of dev review flows |

### Derived search data (batch output on dev, served by prod)

| Table | Class | Why / notes |
|---|---|---|
| `ocr_texts` | D | Detail views, search. Largest row count per image |
| `ocr_lemmas` | D | Smart search; very large, GIN trigram index |
| `embeddings` (CLIP 512) | D | Similar, visual search, meme retrieval |
| `tags` | D | All sources (OCR, Ollama, Note, YOLO, CONCEPT) |
| `image_descriptions` | D | Served, filters by feedback |
| `image_description_embeddings` (1024) | D | `/similar?source=description` |
| `description_lemmas` | D | Search |
| `meme_profiles`, `meme_profile_values`, `meme_profile_situation_embeddings` (1024) | D | Meme-from-text; includes human-verified profiles. Vectors are bound to a model name and digest |
| `ocr_text_embeddings` (384) | X | Only for duplicate matching, not read by backend routes |
| `image_classifications` | X | Text-heavy classifier, review UI only |

### Curation (owner, dev only)

| Table | Class | Why / notes |
|---|---|---|
| `description_notes`, `description_note_lemmas`, `description_note_embeddings` | D | Served in search and similar |
| `image_description_feedback` | D | Filters descriptions in search and similar |
| `comics`, `comics_panels` | D | Built comics are images; panel snapshots carry source ids and hashes without FKs |
| `comics_candidates`, `comics_candidate_images` | X | Review suggestions |
| `image_cuts` | X | Recut proposals and review state (admin only) |
| `duplicate_decisions` | X | Durable human data but only consumed by `clusterize`; needs dev backup, not prod space |
| `tmp_duplicates`, `tmp_clusters` | X | Rebuildable, massive pair table |
| `popularity_absorptions` | D | Decided 2026-11-12: shipped (tiny, append-only, written only by dev) so trending `window=30d`, the popularity breakdown and impression folding work on prod. No FK to images, so promote it as a set |
| `concepts`, `concept_image_sets`, `concept_images`, `concept_text_sets`, `concept_texts` | D | Served through the concept repository; integer ids and `CONCEPT_IMAGES_DIR` files; see Open question 6 |
| `statistics_snapshots` | D | Decided 2026-11-12: the single dev snapshot row is promoted; the prod route is read-only and never recomputes |

### Reader activity (prod-origin)

| Table | Class | Why / notes |
|---|---|---|
| `meme_saves` | P | Append-only; no FK to images or history |
| `meme_impressions` | P | Fastest-growing; bigserial id, no FKs. Retention policy needed (Open question 5) |
| `search_history`, `search_history_tags` | P | UUID ids, so they merge without collisions. Include meme-from-text message text |
| `search_not_found` | P | References `search_history_id` without FK |
| `meme_text_ratings` | P | Owner decision: reader activity. FK to images, unique `(query_hash, image_id)` |
| `trend_sources`, `trends_run_results` | X | Decided 2026-11-12: `trends_batch` stays on dev; the trends pages are hidden on prod |

## Disk assets

| Asset | Class | Notes |
|---|---|---|
| `BASE_PATH` image files of active, unflagged images | D | Required for serving; the bulk of the prod disk |
| `BASE_PATH/excluded/`, `rejected/`, `pre-cuts/`, `converted_originals/`, `comics-sources/` | X | Dev audit and recovery copies |
| `PATH_INGESTION_SOURCE` (dev inbox) | X | |
| Prod inbox | none | Decided 2026-11-12: `POST /api/uploads` is disabled on prod, so there is no prod inbox |
| `BASE_PATH/bug_reports/` | P | Pulled to dev |
| Tagging, taxonomy, prompts, settings YAML | git | Deployed with the code, not synced; taxonomy and settings versions must match, see Constraints |
| `evaluation/gold`, `batch/yolov8n.pt`, concept image sets, model caches | X | Dev tooling; the gold corpus is keyed by dev ids |
| `Storage/backups/`, `evaluation/work/`, `batch/output/` | X | |

## Constraints that shape the sync scripts

1. **Order by foreign key.** `images` before everything that references it; `description_notes` before its embeddings; descriptions
   before their embeddings, lemmas and feedback. The registry (P1) holds the order.
2. **Rows without FKs** (`meme_saves`, `meme_impressions`, `search_not_found`, `popularity_absorptions`, `comics_panels` source ids)
   mean a retired image leaves dangling ids; this is acceptable for P tables but needs a stated policy.
3. **Rows and files go together.** `unregister_deleted_images` removes rows whose file is gone, so a sync must never leave a row
   without its file on prod, and prod does not run that job.
4. **Schema gate.** Both ends must be at the same alembic head before any promote; the command refuses otherwise.
5. **Model-bound data.** CLIP, SBERT and Ollama vectors, taxonomy version, prompt versions: promoted as is; the receiving side
   must not mark them stale. Vector indexes (HNSW) and GIN indexes make bulk loading slow; loading strategy is part of S3.
6. **Integer-keyed tables** (`concepts*`, `trend_sources`) cannot be merged; each has one origin and is replaced as a set.
7. **Per-env asymmetry.** `it` has no tagging vocabulary, profiles or tagging jobs; the registry must tolerate empty tables.
8. **Writes to prod need their own role**, never the backend's, and never recorded in the repo.

## Open questions (for the owner)

**All answered on 2026-11-12; the answers are in the "Decisions log" of the epic README and applied in the tables above.** The
original questions are kept below for the record.

1. **Which tables may the prod backend serve at all?** Plan: prod is read-mostly except the P tables, uploads and bug reports.
   Is that right, i.e. do the dedupe, ingestion review, recut and batch admin pages stay off on prod?
2. **Flagged and excluded images**: never on prod (plan). Anything that must stay visible on prod after you flag it on dev?
3. **Retiring an image** on dev (flagged, moved, cut): should prod remove it on the next promote (plan), keeping its P rows?
4. **`image_processing_status`**: dev-only (plan), since prod runs no pipelines. Agree?
5. **Retention of `meme_impressions`**: it grows per search. Keep everything on prod, or prune after a successful pull?
6. **Concepts**: promoted as a set (plan) or considered dev tooling that prod does not need? Whether the backend needs them at
   runtime for search is to be confirmed by S3/S5.
7. **Trends**: does `trends_batch` run on prod, dev, or both?
8. **Prod uploads**: delete a file from prod's inbox after dev has pulled it (plan), or keep until ingestion promotes it?
10. **RESOLVED 2026-11-12: dev-made activity is disposable; a pull mirrors prod's P tables onto dev (replace), dev activity is never pushed.**
    Original question: **Dev also writes P tables** (found in S2): the dev instances already hold search history, impressions and ratings from your own
    use of dev. Plan: pull by natural key (search history UUID, ratings by `(query_hash, image_id)`), and give impressions a
    stage marker or a natural key, since their bigint ids would collide. Should dev-made activity be pushed to prod too, or stay on dev?
9. **Pull frequency and prod backups** of the P tables (the only copy of reader data on the VPS).
