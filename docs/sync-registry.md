# Sync registry

Generated from `sync/registry.py` by `python -m sync.render --write`; do not edit by hand (`tests/sync/` fails when this page is
stale). It lists every table of `Storage/models.py` with its dev/prod sync class: **D** dev-origin, promoted dev to prod; **P**
prod-origin reader activity, pulled prod to dev; **X** dev-only, never synced. A new table needs an entry in the registry. The
rationale of each class is in `docs/superpowers/specs/2026-11-12-environment-data-sync/` (S1 classification, decisions log in the
README).

Scope: `images` is the promotable set (status `active`, not flagged); `image_fk(c)` rows whose `c` is in that set; `via(parent.c)`
rows reached through a scoped parent; `global_set` no image relation. Level is the load order (parents first).


## Class D: dev-origin, shipped to prod (25 tables)

| Table | Mode | Scope | Level | Digest group | Prod writer | Notes |
|---|---|---|---|---|---|---|
| `concepts` | upsert_by_key | global_set | 0 |  | sync_writer | integer ids: one origin, replaced as a set (prunes rows deleted on dev) |
| `images` | upsert_by_key | images | 0 | core | sync_writer | active and unflagged only; popularity promoted as a value; ingestion_batch_id blanked (blank on missing target: ingestion_batch_id) |
| `popularity_absorptions` | upsert_by_key | global_set | 0 |  | sync_writer | append-only, no FK to images; load in created_at order (absorb row, then its reverse row, then UPDATE the absorb row's reversed_by_id): the shape check and the open-loser unique index forbid a NULL-first load (blank on missing target: batch_run_id) |
| `statistics_snapshots` | upsert_by_key | global_set | 0 |  | sync_writer | the prod route is read-only and never recomputes |
| `comics` | upsert_by_key | global_set | 1 |  | sync_writer | owner curation; built_image_id blanked when the built image is not shipped (blank on missing target: built_image_id) (prunes rows deleted on dev) |
| `concept_image_sets` | upsert_by_key | global_set | 1 |  | sync_writer |  (prunes rows deleted on dev) |
| `concept_text_sets` | upsert_by_key | global_set | 1 |  | sync_writer | empty today; kept for parity (prunes rows deleted on dev) |
| `description_note_lemmas` | replace_per_image | image_fk(image_id) | 1 | note | sync_writer |  |
| `description_notes` | replace_per_image | image_fk(image_id) | 1 | note | sync_writer | owner curation |
| `embeddings` | replace_per_image | image_fk(image_id) | 1 | clip | sync_writer | CLIP 512-d |
| `image_descriptions` | replace_per_image | image_fk(image_id) | 1 | desc | sync_writer | parent of the description tables |
| `image_extras` | upsert_by_key | image_fk(image_id) | 1 | core | sync_writer | remarks only: flagged images are not shipped, so flagged is always false on prod |
| `meme_profiles` | replace_per_image | image_fk(image_id) | 1 | profile | sync_writer | includes human-verified profiles |
| `ocr_lemmas` | replace_per_image | image_fk(image_id) | 1 | ocr | sync_writer | includes the stale flag |
| `ocr_texts` | replace_per_image | image_fk(image_id) | 1 | ocr | sync_writer | includes the stale flag |
| `tags` | replace_per_image | image_fk(image_id) | 1 | tags | sync_writer | all sources; includes the stale flag |
| `comics_panels` | upsert_by_key | via(comics.comics_id) | 2 |  | sync_writer | image_id blanked for unkept panels (flagged comics_source); source snapshots stay (blank on missing target: image_id) (prunes rows deleted on dev) |
| `concept_images` | upsert_by_key | global_set | 2 |  | sync_writer |  (prunes rows deleted on dev) |
| `concept_texts` | upsert_by_key | global_set | 2 |  | sync_writer | empty today; kept for parity (prunes rows deleted on dev) |
| `description_lemmas` | replace_per_image | via(image_descriptions.image_description_id) | 2 | desc | sync_writer |  |
| `description_note_embeddings` | replace_per_image | via(description_notes.description_note_id) | 2 | note | sync_writer | 1024-d |
| `image_description_embeddings` | replace_per_image | via(image_descriptions.image_description_id) | 2 | desc | sync_writer | 1024-d |
| `image_description_feedback` | replace_per_image | via(image_descriptions.image_description_id) | 2 | desc | sync_writer | owner curation; must stay in the digest |
| `meme_profile_situation_embeddings` | replace_per_image | via(meme_profiles.image_id) | 2 | profile | sync_writer | 1024-d, bound to a model name and digest |
| `meme_profile_values` | replace_per_image | via(meme_profiles.image_id) | 2 | profile | sync_writer |  |

## Class P: prod-origin, pulled to dev (6 tables)

| Table | Mode | Scope | Level | Digest group | Prod writer | Notes |
|---|---|---|---|---|---|---|
| `meme_impressions` | mirror_replace | prod_all | 0 |  | backend_app | bigint id: the dev sequence moves past max(id) after a pull |
| `meme_saves` | mirror_replace | prod_all | 0 |  | backend_app |  |
| `meme_text_ratings` | mirror_replace | prod_all | 0 |  | backend_app | FK to images with CASCADE: pull before any promote retires an image; skip ratings of images missing on dev (pull before any promote) |
| `search_history` | mirror_replace | prod_all | 0 |  | backend_app |  |
| `search_not_found` | mirror_replace | prod_all | 0 |  | backend_app |  |
| `search_history_tags` | mirror_replace | prod_all | 1 |  | backend_app | FK to search_history |

## Class X: dev-only, never synced (15 tables)

| Table | Mode | Scope | Level | Digest group | Prod writer | Notes |
|---|---|---|---|---|---|---|
| `batch_runs` | skip | never |  |  | none | run tracking |
| `comics_candidate_images` | skip | never |  |  | none | review suggestions |
| `comics_candidates` | skip | never |  |  | none | review suggestions |
| `duplicate_decisions` | skip | never |  |  | none | consumed by clusterize only; back up on dev |
| `image_classifications` | skip | never |  |  | none | text-heavy classifier, review UI only |
| `image_cuts` | skip | never |  |  | none | recut proposals, admin only |
| `image_metrics` | skip | never |  |  | none | OCR timing scratch |
| `image_processing_status` | skip | never |  |  | none | pipeline skip markers; prod runs no pipelines |
| `ocr_text_embeddings` | skip | never |  |  | none | duplicate matching only |
| `process_events` | skip | never |  |  | none | business metrics of dev review flows |
| `processing_errors` | skip | never |  |  | none | no writers |
| `tmp_clusters` | skip | never |  |  | none | rebuildable clusters |
| `tmp_duplicates` | skip | never |  |  | none | rebuildable candidate pairs |
| `trend_sources` | skip | never |  |  | none | trends_batch stays on dev; hidden on prod |
| `trends_run_results` | skip | never |  |  | none | trends_batch stays on dev; hidden on prod |

## Digest groups

- `clip`: `embeddings`
- `core`: `image_extras`, `images`
- `desc`: `description_lemmas`, `image_description_embeddings`, `image_description_feedback`, `image_descriptions`
- `note`: `description_note_embeddings`, `description_note_lemmas`, `description_notes`
- `ocr`: `ocr_lemmas`, `ocr_texts`
- `profile`: `meme_profile_situation_embeddings`, `meme_profile_values`, `meme_profiles`
- `tags`: `tags`

## Prod write sets

- `backend_app` (6): `meme_impressions`, `meme_saves`, `meme_text_ratings`, `search_history`, `search_history_tags`, `search_not_found`
- `sync_writer` (25): `comics`, `comics_panels`, `concept_image_sets`, `concept_images`, `concept_text_sets`, `concept_texts`, `concepts`, `description_lemmas`, `description_note_embeddings`, `description_note_lemmas`, `description_notes`, `embeddings`, `image_description_embeddings`, `image_description_feedback`, `image_descriptions`, `image_extras`, `images`, `meme_profile_situation_embeddings`, `meme_profile_values`, `meme_profiles`, `ocr_lemmas`, `ocr_texts`, `popularity_absorptions`, `statistics_snapshots`, `tags`

## Integer-keyed tables (sequence moved past max(id) after a load)

`concept_image_sets`, `concept_images`, `concept_text_sets`, `concept_texts`, `concepts`, `meme_impressions`
