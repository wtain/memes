# Batch script inventory

Status: draft (verified 2026-10-11 against `batch/*.py`, `environments/batch_registry.yaml`,
`environments/settings*.yaml`, `Backend/app/scheduler.py`; effective scheduler config read by loading
`config.settings` under each `APP_ENV`)

Image statuses (`--status active|pending|cut_proposed`) are defined in `docs/image-status.md`; this document does not redefine them.

Legend for the access columns:

- **Tracked**: writes a `batch_runs` row (`kind` shown). Required for admin/scheduler use.
- **Admin**: listed in `environments/batch_registry.yaml`, so `POST /api/admin/batches/{name}/run` and the
  `/admin/batches` page can start it. The registry name is the script name; the registry `kind` can differ
  (`build_statistics` has kind `statistics`, `trends_batch` has `trends`, `ingest_auto_prep` has `ingestion_auto_prep`).
- **Sched**: appears in `scheduler.jobs`. See [Schedule per environment](#schedule-per-environment): two jobs on every environment, plus two more on
  `general` only.
- **CLI**: `python -m batch.<script> --env {metal,general,it}` (from repo root, `.venv311`). `--env` is optional when
  `APP_ENV` is set. Use `PYTHONIOENCODING=utf-8` on Windows for anything that uses `ProgressTracker`.

## Summary counts

| Class | Count | Scripts |
|---|---|---|
| Scheduled (and admin) | 4 | `trends_batch`, `build_statistics` (all environments), `export_ratings`, `import_judgments` (`general` only) |
| Admin-triggerable, manual only | 32 | everything else in the registry (`fix_image_formats`, `build_image_embeddings` and `extract_text_from_memes` were registered 2026-10-10, `detect_recut_crops` 2026-10-25, `create_recut_cuts` 2026-10-26, `refresh_recut_cuts` 2026-10-26, `recut_pipeline` 2026-10-29); the registry holds 36 entries in all |
| Run-tracked but not in registry (shell only) | 1 | `ingest_hash_dedup` (long-lived `ingestion` row) |
| Shell-only, untracked | the rest | see sections below |

## 1. Intake (ingestion)

Detail and diagrams: [use-cases/ingestion.md](use-cases/ingestion.md).

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `ingest_hash_dedup` | Stage 1. SHA-256 every file in `PATH_INGESTION_SOURCE`; drop in-batch and cross-corpus exact duplicates; register survivors as `pending`; move them into `BASE_PATH` | `ingestion` (long-lived review run, stage `hash_dedup`) | no | no | Joins the active run if one exists. Postgres advisory lock against concurrent copies of itself. Records each dropped copy as a popularity absorption into its keeper (stats `popularity_copies_recorded`, `popularity_skipped`) |
| `ingest_validate_formats` | Stage 1.5. Fix extension/content mismatch, convert WebP to JPEG (originals to `converted_originals/`), flag unreadable files | none (advances `ingestion` stage to `format_validation`) | no | no | Safe to re-run |
| `build_image_embeddings --status pending --incremental` | CLIP 512-dim vectors for the batch | `build_image_embeddings` | yes (active, incremental only) | no | Skipping it silently excludes images from review |
| `extract_text_from_memes --status pending` | OCR (EasyOCR EN/ES/RU, with Tesseract/TrOCR fallbacks) | `extract_text_from_memes` | yes (active only) | no | Must run before Tier A |
| `build_ocr_lemmas --status pending --incremental` | Lemma index; needed by the OCR-text duplicate corroboration | `build_ocr_lemmas` | yes | no | `--status pending` requires `--incremental` |
| `classify_text_heavy --status pending` | Text-heavy classifier | `classify_text_heavy` | yes | no | |
| `build_ocr_text_embeddings --status pending` | 384-dim OCR-text embeddings for text-heavy images | `build_ocr_text_embeddings` | yes | no | Needs `classify_text_heavy` first |
| `ingest_find_duplicates --tier tier_a\|tier_b` | Candidate pairs into `tmp_duplicates`, advances stage to `tier_a_review` / `tier_b_review`; records the tier's `proposed` business-metric events (M2a) | none | no (use the wrapper below) | no | Stage can only move forward |
| `ingest_find_duplicates_tier_b` | Thin driver: `ingest_find_duplicates.main(tier="tier_b")` | `ingest_find_duplicates_tier_b` | yes | no | Manual: needs Tier A review finished |
| `ingest_auto_prep` | Driver: hash dedup, validate, embeddings, OCR, lemmas, text-heavy, OCR-text embeddings, Tier A. Stops the tick quietly if a tracked child is already running | `ingestion_auto_prep` | yes | **no** (was designed for a 15 min schedule, removed) | Distinct kind so the scheduler's orphan guard never touches the long-lived `ingestion` run |
| `ingest_promote` | Stage 4. `pending` to `active` for images with no unresolved candidates; completes the run when none remain pending; records `kept` metric events for proposed images nobody decided (M2a) | `ingest_promote` | yes | no | Fails (visibly) if no active run |
| `ingest_abort` | Abandon the active run: move files back to the inbox, delete rows, mark run `aborted` | none (aborts the `ingestion` row) | no | no | Does not undo WebP conversion. Reverses popularity edges into surviving images (metrics `popularity_reversed`, `popularity_skipped`) |

## 2. Core signals: OCR, embeddings, lemmas, text classification

Detail: [use-cases/ocr-lemmas-tags.md](use-cases/ocr-lemmas-tags.md).

| Script | What it does | Tracked kind | Admin | Sched | Key flags / notes |
|---|---|---|---|---|---|
| `extract_text_from_memes` | Register files, OCR to `ocr_texts` | `extract_text_from_memes` | yes | no | `--status {pending,active,all}`; the admin path is always `active` over `BASE_PATH`. Auto-registers only in `active` mode. `--refresh-stale` re-runs OCR on recut cuts that still carry the source's stale OCR (and clears their classification and OCR-text embedding). Do not clear `ocr_texts` for a re-run, use `reset_ocr_status` |
| `build_image_embeddings` | CLIP embeddings | `build_image_embeddings` | yes | no | `--status` (`pending`, `active`, `cut_proposed`, `all`), `--incremental`. `main()` defaults to incremental and `active`, so admin runs never delete embeddings; a full rebuild is shell-only (omit `--incremental`). Recut cuts: `--status cut_proposed --incremental`, shell only |
| `build_ocr_lemmas` | Per-image lemma index (smart search, duplicate corroboration) | `build_ocr_lemmas` | yes | no | `--incremental`, `--status`. Admin path is always incremental. A full run deletes the whole table. `--refresh-stale` replaces the stale lemmas of recut cuts |
| `build_ocr_text_embeddings` | 384-dim MiniLM embeddings of OCR text (text-heavy images only) | `build_ocr_text_embeddings` | yes | no | `--status` (also `cut_proposed`, shell only, after the cut's OCR refresh and `classify_text_heavy`) |
| `classify_text_heavy` | Coverage / background / ink-colour classifier into `image_classifications` | `classify_text_heavy` | yes | no | `--status` (also `cut_proposed`, shell only, after the cut's OCR refresh) |

## 3. Tagging

| Script | Source label | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `build_tags_from_ocr` | `OCR` | `build_tags_from_ocr` | yes | no | ConceptTagger. `--incremental`; `--refresh-stale` replaces the stale OCR tags of recut cuts. Not available on `it` |
| `build_tags_from_descriptions` | `Ollama` | `build_tags_from_descriptions` | yes | no | `--incremental`; `--refresh-stale` replaces the stale Ollama tags of recut cuts. Not on `it` |
| `build_tags_from_notes` | `Note` | `build_tags_from_notes` | yes | no | CLI default is a full run, `main()` and admin default to incremental. Not on `it` |
| `build_concept_embeddings` | n/a (feeds `CONCEPT`) | `build_concept_embeddings` | yes | no | **Chains into `tag_images_from_concepts`** unless `--no-chain` |
| `tag_images_from_concepts` | `CONCEPT` | `tag_images_from_concepts` | yes | no | Full rebuild |
| `detect_entities_and_tag` | `YOLO` | `detect_entities_and_tag` | yes | no | YOLOv8 animals, full rebuild |

## 4. Descriptions, notes and meme-from-text profiles

Detail: [use-cases/descriptions-and-profiles.md](use-cases/descriptions-and-profiles.md).

| Script | What it does | Tracked kind | Admin | Sched | Flags / notes |
|---|---|---|---|---|---|
| `build_image_descriptions` | Multi-prompt Ollama vision descriptions | `build_image_descriptions` | yes | no | `--limit`, `--reset`, `--retry-failed`. Admin has no limit. Active images only; `--refresh-stale` (with `--limit`) re-describes recut cuts |
| `build_image_description_embeddings` | bge-large 1024-dim, one per description | `build_image_description_embeddings` | yes | no | `--reset` |
| `build_description_lemmas` | Lemma index of descriptions (English) | `build_description_lemmas` | yes | no | Incremental by construction |
| `build_description_note_lemmas` | Lemma index of human notes | `build_description_note_lemmas` | yes | no | |
| `build_description_note_embeddings` | bge-large embeddings of notes | `build_description_note_embeddings` | yes | no | |
| `build_meme_profiles` | Profile (situation, emotion, stance, topic, tone, use-when) per image | `build_meme_profiles` | yes | no | `--limit`, `--reset`, `--retry-failed`, `--ids-file`, `--status {active,cut_proposed}` (cuts only after their refresh; not with `--reset`). `it` exits cleanly (no `text_model`) |
| `build_meme_profile_embeddings` | Situation embedding via Ollama | `build_meme_profile_embeddings` | yes | no | `--limit`, `--reset`, `--ids-file`. `it` exits cleanly |
| `description_pipeline` | Driver over 10 steps (see use-case page) | `description_pipeline` | yes | no | Failure only skips dependents. Children self-track under their own kinds |
| `build_taxonomy_similarity` | Regenerates `batch/data/taxonomy/similarity.json` | none | no | no | Needs live Ollama; manual after taxonomy edits |

## 5. Duplicates and clusters

Detail: [use-cases/duplicates-and-clusters.md](use-cases/duplicates-and-clusters.md).

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `rebuild_duplicates` | Three probes (general CLIP, tight CLIP safety net, OCR-text) into `tmp_duplicates` | `rebuild_duplicates` | yes | no | `--full`, `--k`, `--threshold`. **Chains into `clusterize`** unless `--no-chain` |
| `clusterize` | Union-find over `tmp_duplicates`, splits oversized clusters, writes `tmp_clusters` | `clusterize` | yes | no | Config: `clustering.*`. The library `/duplicates` review's business metrics (`library_duplicates`) are attributed to its latest completed run |
| `detect_file_duplicates` | Byte-level duplicates: backfills `content_hash`, flags all but the oldest of each identical group | none | no | no | Skips clusters that already have a flagged member. Records the keeper as winner of each flagged copy (metric `popularity_skipped` for failed recordings); unflagging reverses it |
| `deduplicate_ocr_texts` | Deletes duplicate `ocr_texts` rows | none | no | no | No `--env` flag, relies on `APP_ENV` / `DATABASE_URL` |
| `remove_singletons` | Deletes `tmp_clusters` rows of single-member clusters | none | no | no | Auto-run by `unregister_deleted_images` |
| `move_reference_duplicates` | Moves unregistered files in `BASE_PATH` that are byte-identical to a file in a reference dir into `BASE_PATH/possible_duplicates` | none | no | no | `--reference-dir`, `--dry-run` |

## 6. Maintenance

Detail: [use-cases/maintenance-and-cleanup.md](use-cases/maintenance-and-cleanup.md).

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `move_flagged` | Move flagged images to `BASE_PATH/excluded`; a `comics_source` image with a comics panel goes to `BASE_PATH/comics-sources/<comics name>/` instead (counter `moved_to_comics_sources`) | `move_flagged` | yes | no | **Chains `unregister_deleted_images`** (then `remove_singletons`); `--no-chain` |
| `unregister_deleted_images` | Delete DB rows whose files are gone | `unregister_deleted_images` | yes | no | **Chains `remove_singletons`**; `--no-chain` |
| `fix_image_formats` | Retroactive format fix for the existing corpus; only path that backfills `Image.width/height` | `fix_image_formats` | yes | no | `--status` (default `active`, the only value the admin path uses) |
| `detect_recut_crops` | Screenshot recut: stores a `detected` `image_cuts` proposal for active images with a flat margin (R2) | `detect_recut_crops` | yes | no | `--limit`; checks each image once per detector version (`recut_detect_v1`); skips the frozen evaluation corpus, flagged images and images that already have a live cut; unreadable files are recorded `failed` and not retried under the same detector version, 20 in a row stop the run. Scheduling: see `recut_pipeline` |
| `refresh_recut_cuts` | Screenshot recut: replaces the stale derived data of the cuts by running the refresh modes in order (R3c); 11 steps | `refresh_recut_cuts` | yes | no | Children self-track under their own kinds; failure only skips dependents; stats carry the steps and `stale_rows_total` (all statuses) |
| `recut_pipeline` | Screenshot recut: turns `detected` proposals into reviewable cuts: `create_recut_cuts`, `build_image_embeddings --status cut_proposed`, `refresh_recut_cuts` (R7) | `recut_pipeline` | yes | no | Detection stays a separate manual step. `recut.max_cuts_per_run` caps the cut step. Steps are independent; a failed step does not stop the others and the driver fails. Not scheduled by decision (R7): review, not detection, is the bottleneck. Revisit once the first pass is reviewed: then `detect_recut_crops` + `recut_pipeline` weekly on `general` is a settings change |
| `create_recut_cuts` | Screenshot recut: cuts each `detected` proposal into a same-format file, a `cut_proposed` image and copied derived data (R3a) | `create_recut_cuts` | yes | no | `--limit`, `--retry-failed` (shell only: re-queues `failed` proposals); highest confidence first; one transaction per cut; skips sources that are not active, flagged or in the frozen corpus; a proposal that cannot be cut ends `failed` with a reason; 20 in a row stop the run |
| `check_image_popularity` | Reports drift of the cached `images.popularity` against the value derived from the open `popularity_absorptions` edges | none | no | no | Shell only, not run-tracked; `--env`, `--fix` (rewrites the cache to the expected value) |
| `reset_ocr_status` | Reset OCR processing status (`--all`) | none | no | no | Use before a full OCR re-run |
| `score_ocr_language` | Backfill `lang_score` for old `ocr_texts` rows | none | no | no | `--rescore-all` |
| `load_images_from_internet` | Fetch images via SerpAPI (`SERP_API_KEY`); queries are hardcoded in `main()`, output under `images/<concept>/<query>/` | none | no | no | Ad hoc, edit the script to change queries |

## 7. Concept discovery (human in the loop)

Detail: [use-cases/concept-discovery.md](use-cases/concept-discovery.md).

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `build_bow` | Bag of words / unmatched lemmas | `build_bow` | yes | no | Writes `bow.*` output files. `bow.text_source` is `ocr` or `descriptions` |
| `build_lemma_clusters` | Embed unmatched lemmas, HDBSCAN per language, optional Ollama naming | none | no | no | Many flags; `lemma_clustering.selection_method: leaf` |
| `draft_concepts_from_clusters` | Append drafts to `concepts.<env>.yaml` and `tags.<env>.yaml` | none | no | no | `--cluster-file --env --language [--top]` |

Both manual scripts are deliberately excluded from scheduling (they would pile up unreviewed drafts).
The `/draft-lemma-concepts` command in `.claude/commands/` runs them with a commit before and after review.

## 8. Trends and statistics

Detail: [use-cases/trends-and-statistics.md](use-cases/trends-and-statistics.md).

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `trends_batch` | GLiNER NER over each configured source; counts `label:entity` per run | `trends` | yes | **yes, 360 min** | Sources seeded by `batch/trends/seed_sources.py` (not a scheduled job) |
| `build_statistics` | Precompute the statistics page into `statistics_snapshots` | `statistics` | yes | **yes, 60 min** | Also run best-effort after every wrapped job |

## 9. Evaluation and ratings

Detail: [use-cases/ratings-and-gold-set.md](use-cases/ratings-and-gold-set.md). Both jobs are meaningful on `general`
only (the gold corpus belongs to `general`): started on another environment they refuse and finish with
`skipped: true` in their stats. Both are in the registry, so every environment's admin page lists them, but only
`general` schedules them.

| Script | What it does | Tracked kind | Admin | Sched | Notes |
|---|---|---|---|---|---|
| `export_ratings` | Turns the owner's stored result ratings (`meme_text_ratings`) into gold-set review files | `export_ratings` | yes | **yes, `general` only, 1440 min** | Replaces `evaluation/work/ratings-export/current/` on every run (git-ignored; `candidate-queries.json` holds message text). Never touches `evaluation/gold/` |
| `import_judgments` | Merges `current/judgments-*.json` into the committed `evaluation/gold/judgments.json` | `import_judgments` | yes | **yes, `general` only, 10080 min** | No database. Never commits: the change stays in the working tree for a person to review. Never overwrites an existing judgment (the run fails instead). No-op ("no export to import") when `current/` has no judgment files |

## 10. Support modules and dev tools (not jobs)

| Path | Role |
|---|---|
| `batch/run_wrapper.py` | Uniform subprocess entry point for scheduler and admin |
| `batch/run_tracking.py` | `tracked_run`, `finish_existing_run`, `record_stats` |
| `batch/registry.py` | Reads `environments/batch_registry.yaml` (+ optional `batch_registry.<env>.yaml`, none exist) |
| `batch/ocr_preprocess.py`, `tesseract_reader.py`, `trocr_fallback.py` | OCR helpers used by `extract_text_from_memes` |
| `batch/tasks/SourceTasks.py`, `batch/trends/*` | Trends sources, connectors, NER processing |
| `batch/utils/*` | `ProgressTracker`, hashing, safe move |
| `batch/diff_rules.py`, `eval_rules.py`, `eval_ocr_language_filter.py` | Evaluate tagging rules and the language filter against golden sets |
| `batch/tools/*` | Spot checks, rule conversion, meme-profile pilot, probes (one-off) |
| `python -m evaluation ...` | Meme-from-text evaluation harness (read-only DB role) |
| `tools/agent_duplicates.py`, `tools/agent_untagged.py` | Feed the `/review-duplicates` and `/suggest-tags` Claude commands |

## Schedule per environment

Source of truth: `scheduler.jobs` in `environments/settings.yaml`, plus `scheduler.jobs` in
`environments/settings.general.yaml`, which Dynaconf merges onto the common list (so `general` has four jobs,
`metal` and `it` two). Each per-environment file also sets `scheduler.enabled: true`. Values below were read by
loading `config.settings` under each `APP_ENV` and calling `Backend.app.scheduler._load_job_configs`.

| Job | Kind | Interval | Orphan timeout | metal | general | it |
|---|---|---|---|---|---|---|
| `trends_batch` | `trends` | 360 min | 60 min | enabled | enabled | enabled (see caveat) |
| `build_statistics` | `statistics` | 60 min | 10 min | enabled | enabled | enabled |
| `export_ratings` | `export_ratings` | 1440 min (daily) | 10 min | not scheduled | enabled | not scheduled |
| `import_judgments` | `import_judgments` | 10080 min (weekly) | 10 min | not scheduled | enabled | not scheduled |

Caveats:

- `scheduler.enabled` defaults to `false` in the common file on purpose (the Docker backend image does not carry
  `batch.trends_batch` or its ML dependencies and has several workers with no cross-worker lock). Each dev
  environment opts in.
- `it` has no `trends:` section, so `trends_batch` there has no default labels or model. It only produces results if
  each source row carries its own `extraction` config. Whether that is so in the `it` database was not checked.
- Every other registry job is manual only, in all three environments. Admin availability is identical in all three
  because no `batch_registry.<env>.yaml` override exists, but some jobs cannot do real work on `it`:
  `build_tags_from_*` (no tagging YAML for profile `it`), `build_meme_profiles` and `build_meme_profile_embeddings`
  (exit cleanly, no `text_model`).
- Runs are restart-safe: the first fire after a backend restart waits for the remainder of the interval, based on the
  most recent `batch_runs` row of that kind. A `started` row older than `max_runtime_minutes` is marked failed as
  orphaned so it stops blocking.

The shell-only readiness check `python -m tools.rollout_preflight` (rollout-prep, see `use-cases/screenshot-recut-rollout.md`) is not a batch job: it is read-only, not run-tracked and not registered.
