# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Semantic search engine for memes. Images are enriched offline (OCR, CLIP embeddings, LLM descriptions, tags) via batch jobs; the FastAPI backend serves pre-computed results; the React frontend provides search and browse UI. Three independent environments run in parallel: **metal** (port 8081), **general** (8082), **IT** (8083), each with its own database and config.

These three environments run continuously on the developer's workstation — their backend, frontend, and database ports are always occupied. See `environments/Environments.md` for the full port table before binding any port for manual testing or verification.

## First-time repo setup

After cloning, activate the git hooks (one command, all platforms):

```sh
git config core.hooksPath .githooks
```

The `post-merge` hook warns you when Python or frontend dependency files
change after a `git pull`, so you know to re-run `pip install` or `pnpm install`.

## Commands

### Backend (run from repo root)

```powershell
# Windows — always required before uvicorn
set WATCHFILES_FORCE_POLLING=1

uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082 --host 0.0.0.0
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.it --port 8083 --host 0.0.0.0
```

### Frontend (from `Frontend/memes-frontend/`)

```bash
pnpm dev          # metal, port 5173
pnpm dev-gen      # general, port 5174
pnpm dev-it       # IT, port 5175
tsc -b && vite build   # production build
eslint .               # lint (use --max-warnings 0 for CI)
vitest run             # tests (single run)
vitest                 # tests (watch mode)
```

### Tests

```bash
# Backend API tests (mocked DB)
cd Backend && pytest

# Rules engine unit tests (no DB, no I/O)
pytest tests/rules/

# Config loading integration tests (fixture .env files, no real secrets touched)
pytest batch/tests/

# Documentation guards (no DB): docs/image-status.md must list exactly the ImageStatus values and the
# scripts with a --status flag; docs/image-derived-data.md must classify every table off images.
# Runs in CI after the integration tests.
pytest tests/docs/

# Dockerfile.backend smoke test — builds the image and verifies it actually boots
# (not just that `docker build` succeeds). Requires a local Docker daemon; skips
# otherwise. Runs in CI as part of Backend Docker Build.
pytest tests/docker/

# Single test file
cd Backend && pytest tests/test_images_endpoints.py
pytest tests/rules/test_engine.py
```

### Database migrations (from `Storage/`)

```powershell
# Load env vars, then run migrations
Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic upgrade head

alembic revision --autogenerate -m "description"
```

### Android (from `AndroidClient/`)

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew assembleDebug
.\gradlew :app:testDebugUnitTest --no-daemon
```

### Type generation

```bash
# Shared JSON schemas → TypeScript types (Frontend/memes-frontend/src/types/)
./Frontend/generate-types.sh

# Shared JSON schemas → Kotlin DTOs (AndroidClient/)
./AndroidClient/scripts/generate_dtos.py

# Shared JSON schemas → Python Pydantic models (Backend/app/types/generated/) -- run from Backend/,
# see documents/generation.md for the full command. Easy to forget since it has no wrapper script
# like the two above; a schema change with no corresponding diff under Backend/app/types/generated/
# means this step was skipped.
```

## Architecture

### Layer structure

```
environments/.env.*       ← per-environment secrets (DATABASE_URL, BASE_PATH, SERP_API_KEY, …)
environments/settings*.yaml ← per-environment tracked config, grouped by domain (rules.*, bow.*, concepts.*, ollama.*, lemma_clustering.*, ocr.*, general.*)
config/settings.py         ← Dynaconf loader shared by Backend and batch — see Configuration below
Storage/models.py         ← single source of truth for all SQLAlchemy ORM models
Storage/db.py             ← AsyncSessionLocal, get_async_db dependency
repository/               ← global data access layer (async, repo pattern)
Backend/app/api/          ← FastAPI routers (one file per resource)
Backend/app/repositories/ ← backend-specific queries (extend global repository/)
batch/                    ← offline enrichment jobs (run manually or on schedule)
ai/                       ← CLIP, Ollama, YOLOv8 integrations
rules/                    ← rules engine (tag derivation from text)
shared/schemas/           ← JSON schemas shared across frontend, backend, Android
```

### Backend pattern

Three layers: **Router → Service → Repository**. Service layer (`Backend/app/services/`) handles business logic; not every endpoint needs one — simple pass-throughs go Router → Repository directly.

- **Router** (`Backend/app/api/`): request validation, dependency injection, response model construction. No raw SQL.
- **Repository** (`Backend/app/repositories/` or root `repository/`): all DB queries via SQLAlchemy async ORM. Returns ORM rows or plain Python values — never Pydantic models.
- Sessions come from `get_async_db` via `Depends`. Repositories must **not** call `session.commit()` — `get_async_db` handles commit/rollback.
- ORM models live only in `Storage/models.py`. Never redefine tables elsewhere.
- `EMBEDDING_DIM = 512` (CLIP ViT-B-32 is 512-dimensional — architecture docs say 1536, which is outdated).

### API contract

**`backend_api.md` at the repo root is the authoritative API reference** for frontend and Android clients. Update it whenever you add, remove, or change an endpoint (signature, parameters, response shape, or behavior).

### Adding a new endpoint

1. Add the router file under `Backend/app/api/` (or extend an existing one).
2. Add the repository under `Backend/app/repositories/`.
3. Register the router in `Backend/app/main.py` with `app.include_router(..., prefix="/api")`.
4. Update `backend_api.md`.

Response models (Pydantic `BaseModel`) live in `Backend/app/types/generated/` if shared across routers, or inline in the router file if endpoint-specific.

### Before committing backend changes

There is no automated test gate for the backend. At minimum:

- Confirm the server starts without import errors.
- Hit the new endpoint manually and verify the response shape matches what you documented.
- Smoke-test existing endpoints: `/api/diagnostics/health` and `/api/images?limit=1`.

### Before committing frontend changes

```bash
# From Frontend/memes-frontend/
tsc -b          # type-check
eslint src/     # lint (must pass with 0 warnings)
vitest run      # tests

# If shared/schemas/ changed, regenerate types first (from Frontend/):
bash generate-types.sh
# then verify no diff:
git diff Frontend/memes-frontend/src/types/generated/
```

The CI gate runs all three checks plus a `git diff` on the generated types — a stale `all.d.ts` fails the build.

### Batch pipeline (execution order)

Keep this list in sync: when you add a new batch script under `batch/`, or
materially change an existing one's CLI/config surface, update its entry
here in the same change.

`docs/batches/` is the map of all batch jobs: `inventory.md` (every script, with its
tracked kind and whether it is admin-triggerable or scheduled, per environment),
`execution-model.md` (wrapper, registry, scheduler, locks, chaining) and `use-cases/`
(diagrams of the execution order for ingestion, tagging, descriptions, duplicates,
mark-and-move cleanup, ...). Update `inventory.md` too when you add a script or change its
registry or scheduler status.

```
extract_text_from_memes    → registers images + EasyOCR (EN/ES/RU). Run-tracked and admin-triggerable
                              from /admin/batches (always --status active there), manual-trigger only
build_image_embeddings     → CLIP 512-dim vectors. Run-tracked and admin-triggerable (always incremental,
                              --status active there; a full rebuild stays shell-only), manual-trigger only
rebuild_duplicates         → near-duplicate candidate pairs; HNSW-assisted KNN, not a full cross
                              join — see
                              docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.
                              Issues three probes per run: the general CLIP probe (incremental by
                              default -- only images with no existing tmp_duplicates row for that
                              probe's distance_source are re-probed), a tight CLIP "safety net"
                              probe for text-heavy-vs-text-heavy pairs, and an OCR-text-embedding
                              probe for text-heavy-vs-text-heavy pairs — see
                              docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
                              The safety-net probe is deliberately NOT incremental -- it always
                              re-probes every active text-heavy image on every run, relying
                              entirely on ON CONFLICT DO NOTHING for idempotency; this is the
                              single most load-bearing invariant in that feature (sharing an
                              incremental marker with the general probe would silently and
                              permanently disable the safety net for affected images), so don't
                              "simplify" it into sharing the general probe's incremental marker.
                              The OCR-text probe additionally requires a lexical-overlap
                              corroboration check against ocr_lemmas before inserting a candidate
                              (MIN_LEMMA_OVERLAP_COEFFICIENT=0.2, MIN_LEMMA_COUNT_FLOOR=3) -- see
                              docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md.
                              --full clears active-library pairs and re-probes everything;
                              --k/--threshold override settings.DUPLICATES.K/THRESHOLD (the general
                              CLIP probe's threshold only -- the safety-net and OCR-text
                              thresholds are fixed module constants). Chains
                              into clusterize when it finishes (--no-chain to skip); both are
                              independently admin-triggerable from /admin/batches, manual-trigger
                              only (not scheduled).
clusterize                 → optimize cluster index; admin-triggerable from /admin/batches,
                              manual-trigger only. Union-find over tmp_duplicates pairs, gated
                              per distance_source: clip-sourced pairs below PROXIMITY_THRESHOLD
                              (0.05), ocr_text-sourced pairs below the separate
                              PROXIMITY_THRESHOLD_OCR_TEXT (0.05, independently configured, not
                              coupled to the CLIP constant even though they're numerically equal
                              today). ocr_text-sourced pairs are trustworthy here specifically
                              because batch/rebuild_duplicates.py's/batch/ingest_find_duplicates.py's
                              own OCR-text probes already require a lexical-overlap corroboration
                              check (against ocr_lemmas) before inserting a row at all — see
                              docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md.
                              (A 2026-09-18 live-rollout finding briefly disabled ocr_text
                              auto-clustering entirely before that corroboration check existed --
                              see docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's
                              "Known limitation" section for that incident.) Then shapes the
                              result: clusters bigger
                              than settings.CLUSTERING.SPLITTING.MAX_CLUSTER_SIZE are recursively
                              re-clustered at progressively tighter thresholds (stepping down by
                              .DECREMENT until a group is small enough, the next threshold would
                              drop below .FLOOR, or a tightening step severs every remaining edge
                              among the group's members at once (see
                              docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md)
                              — whichever comes first; in any of the latter two cases the cluster
                              is accepted oversized rather than split further); any member left
                              with no surviving edge at a given threshold is dropped as an implicit
                              singleton instead of being written out as a size-1 cluster.
                              settings.CLUSTERING.SPLITTING.ENABLED: false restores the original
                              behavior (every union-find cluster written out as-is, however large;
                              singletons can't occur since the algorithm only creates clusters from
                              a connected pair). See environments/settings.yaml's `clustering`
                              block for defaults.

build_tags_from_ocr        → rule-based tags from OCR text
build_ocr_lemmas            → per-image lemma index for smart search (see
                               docs/superpowers/specs/2026-07-21-smart-search-design.md) and, since
                               2026-09-19, for the OCR-text duplicate-matching corroboration check
                               (see docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md).
                               --incremental skips images already indexed for this pipeline.
                               --status defaults to active; --status pending covers an in-flight
                               ingestion batch -- also chained automatically as part of
                               ingest_auto_prep (see Ingestion below). --status pending requires
                               --incremental (a full reprocess deletes the ENTIRE table
                               unconditionally, not just pending-status rows).
build_description_note_lemmas → per-image lemma index for human description notes (see
                              docs/superpowers/specs/2026-08-20-description-notes-design.md);
                              admin-triggerable from /admin/batches, manual-trigger only, not
                              scheduled
build_description_lemmas   → per-description lemma index for Ollama descriptions, indexed as English
                              (see docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md);
                              incremental by construction; feeds smart search, with rows of rejected
                              descriptions excluded at query time; admin-triggerable from
                              /admin/batches, manual-trigger only, not scheduled; --status pending
                              is not supported (task 148)
build_description_note_embeddings → SBERT embeddings (bge-large-en-v1.5, 1024-dim) for
                              non-empty description notes; admin-triggerable from
                              /admin/batches, manual-trigger only, not scheduled
build_image_descriptions   → multi-prompt Ollama LLM descriptions (optional), one row per
                              (image, prompt) pair; configurable prompts/models/context size
                              per environment; incremental with its own commit interval;
                              permanently-failed pairs are skipped by default (--retry-failed
                              to re-attempt, --reset to clear everything, --limit to cap a run).
                              Run-tracked (kind build_image_descriptions, stats include
                              images_selected/images_remaining) and admin-triggerable from
                              /admin/batches (no --limit there: one trigger covers the whole backlog),
                              manual-trigger only, not scheduled. Always active images only.
                              See docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md
                              and docs/superpowers/specs/2026-07-15-image-description-failure-tracking-and-context-size.md
build_image_description_embeddings → SBERT embeddings (bge-large-en-v1.5, 1024-dim), one per
                              Ollama description; used only by /similar?source=description (NOT by
                              duplicates). Run-tracked (kind build_image_description_embeddings) and admin-triggerable from
                              /admin/batches, manual-trigger only, not scheduled; no --status pending.
                              See docs/data-flow.md.
build_tags_from_descriptions → ConceptTagger tags from descriptions (same vocabulary/profile as
                              build_tags_from_ocr, source "Ollama"; each description tagged on its own
                              as "en", tags unioned per image; no OCR confidence/language filters).
                              --incremental selects images with no Ollama tag or whose newest
                              description is newer than their newest Ollama tag, and rewrites those
                              images' Ollama tags from all their non-rejected descriptions (rejected
                              feedback excluded; any feedback change in the API deletes the image's
                              Ollama tags so the next run rebuilds them). Full run
                              (no --incremental) deletes every Ollama tag first. See
                              docs/data-flow.md for which signals feed which consumers.
build_tags_from_notes      → ConceptTagger tags from human description notes (same vocabulary/profile as
                              build_tags_from_ocr, source "Note", language=None). --incremental selects
                              notes with no Note tag or edited after their newest Note tag and rewrites
                              those images' Note tags; full run (no --incremental) deletes every Note
                              tag first. Clearing a note deletes its Note tags immediately. The CLI default is a FULL
                              run (pass --incremental for staleness mode), while main() and the admin
                              trigger default to incremental. On `it` (no tagging vocabulary files) it
                              cannot run, same as the other ConceptTagger jobs.
                              admin-triggerable from /admin/batches, manual-trigger only, not scheduled.
description_pipeline      → one admin-triggerable driver (manual-trigger only, not scheduled) that refreshes the
                              description and tagging data: build_tags_from_ocr, build_description_note_lemmas,
                              build_description_note_embeddings, build_tags_from_notes, build_image_descriptions
                              (capped per run by image_descriptions.max_per_run, unset = unlimited; re-trigger until
                              the backlog is empty), then build_image_description_embeddings, build_description_lemmas
                              and build_tags_from_descriptions (all need the describe step), build_meme_profiles and
                              build_meme_profile_embeddings (build_meme_profiles needs the describe step and build_meme_profile_embeddings needs build_meme_profiles). Active images only, so new images join after ingest_promote. A failed
                              step only skips the steps that need it; each child self-tracks under its own kind and an
                              already-running child is skipped, not failed. Excludes tag_images_from_concepts,
                              detect_entities_and_tag and concept drafting. See
                              docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md.
build_concept_embeddings   → concept CLIP embeddings + mappings; also runs tag_images_from_concepts
                              automatically afterward (--no-chain skips this); both are
                              admin-triggerable, manual-trigger only, not scheduled

# Maintenance (run as needed)
detect_file_duplicates     deduplicate_ocr_texts     move_flagged     unregister_deleted_images
detect_entities_and_tag    tag_images_from_concepts  build_bow          remove_singletons
fix_image_formats          classify_text_heavy       build_ocr_text_embeddings

move_flagged                → also runs unregister_deleted_images automatically afterward,
                               reconciling the DB with whatever was moved; --no-chain skips this.
unregister_deleted_images   → also runs remove_singletons automatically afterward, deleting
                               tmp_clusters rows for clusters its cascade-deletes reduced to a
                               single member; --no-chain skips this.
fix_image_formats           → retroactively applies ingest_validate_formats' fix logic to
                               the existing corpus (default --status active); safe to
                               re-run, already-fixed images are no-ops. Admin-triggerable from
                               /admin/batches (manual-trigger only, not scheduled; always the
                               default --status active there). Also the only path that
                               backfills Image.width/height (see
                               docs/superpowers/specs/2026-09-14-image-dimension-capture.md) --
                               images registered directly via extract_text_from_memes.py's
                               register_image() (bypassing the ingestion pipeline) need this
                               re-run too, since they never pass through apply_format_fix().
detect_recut_crops          → screenshot recut, crop detection (R2): for each active image with a flat margin around
                               the meme (margin trim, removing 7% to 75% of the area), stores one `detected`
                               `image_cuts` proposal with the box in source pixels. Creates no files, changes no image
                               status and records no metric event (R3 does). Skips the frozen evaluation corpus, flagged
                               images, images that already have a live cut or are themselves a cut, and sources rejected
                               for the same detector version. Each checked image is remembered in
                               image_processing_status under `recut_detect_v<detector version>`, so a re-run sees only
                               new images and a new detector version checks all again (an unreadable file is recorded `failed` and is not
                               retried under the same version; 20 unreadable files in a row stop the run; animated and
                               EXIF-rotated images get no proposal). Run-tracked (kind
                               detect_recut_crops, stats images_selected/proposed/no_cut/clipped_text/unreadable/images_remaining) and
                               admin-triggerable from /admin/batches, manual-trigger only, not scheduled; `--limit`.
                               Settings: `recut.*`. The OCR clipping guard exists but is off (ocr_overlap_max 1.0). See
                               docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md.
classify_text_heavy         → computes the text-heavy classifier (coverage ratio, background
                               color uniformity, ink-color consistency) for images with OCR text
                               and real dimensions, storing both the raw signals and the result in
                               image_classifications. Admin-triggerable from /admin/batches,
                               manual-trigger only, not scheduled. --status defaults to active
                               (the existing corpus); --status pending covers an in-flight
                               ingestion batch -- also chained automatically as part of
                               ingest_auto_prep (see Ingestion below). Uses ProgressTracker, so
                               when run interactively on Windows it needs PYTHONIOENCODING=utf-8
                               set first -- see the ProgressTracker gotcha below. See
                               docs/superpowers/specs/2026-09-15-text-heavy-classifier.md.
build_ocr_text_embeddings   → computes sentence embeddings (paraphrase-multilingual-MiniLM-
                               L12-v2, 384-dim) for the OCR text of every text_heavy-classified
                               image, storing them in ocr_text_embeddings. Consumed by
                               rebuild_duplicates/ingest_find_duplicates for text-heavy-vs-
                               text-heavy duplicate matching (see
                               docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md)
                               -- see docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md for
                               the embedding computation itself. Admin-triggerable
                               from /admin/batches, manual-trigger only, not scheduled. --status
                               defaults to active; --status pending covers an in-flight
                               ingestion batch -- also chained automatically as part of
                               ingest_auto_prep (see Ingestion below).
build_taxonomy_similarity   → regenerates batch/data/taxonomy/similarity.json for the meme-from-text
                               vocabulary keys from the 25 hand-curated near-synonym pairs in
                               batch/data/taxonomy/similarity_overrides.yaml, plus any within-axis embedding
                               neighbours above settings.TAXONOMY.SIMILARITY_THRESHOLD (0.9, so currently
                               none: definition cosines max out at 0.81 and auto-neighbours were noise).
                               Still needs a live Ollama with settings.TAXONOMY.EMBEDDING_MODEL for the
                               model digest and definition embeddings. Manual-run only, no DB, not
                               run-tracked, not admin-triggerable. Re-run after any change under
                               batch/data/taxonomy/. A vocabulary/axis change needs a `version` bump in
                               taxonomy.yaml and `python -m taxonomy.lock` first; editing
                               similarity_overrides.yaml needs only a regenerated similarity.json (no
                               version bump, no lock refresh, no re-extraction) --
                               tests/taxonomy/test_shipped_similarity.py fails when it is stale. See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md.

build_meme_profiles         → extracts the meme-from-text profile (situation, emotion, stance, topic, tone,
                               "use when") for every active image with none or a stale one, with one
                               schema-constrained Ollama call per image: text-first (note, non-rejected
                               descriptions, filtered OCR) with a vision fallback only when there is no usable
                               text (meme_profiles.min_text_chars). Staleness is derived: taxonomy version,
                               prompt version (auto-derived from the prompt text), model or input fingerprint
                               differs; human-verified profiles are never rewritten (reported as needing
                               review). Run-tracked, admin-triggerable from /admin/batches, manual-trigger only,
                               not scheduled; also a description_pipeline step capped by
                               meme_profiles.max_per_run. --reset keeps human-verified profiles; --retry-failed
                               re-attempts permanently failed images; --limit caps a run. Needs
                               meme_profiles.text_model (metal and general only; `it` exits cleanly with "not
                               configured"). An answer with no situation, no use-when and no axis values counts as
                               a failed extraction (retried once, then recorded as failed, re-attempted with
                               --retry-failed). Failures carry a context (input fingerprint, prompt version,
                               model) and are skipped only while it is unchanged, so new inputs or a new prompt
                               or model retry them automatically; connection errors and a missing model are not
                               recorded as failures and three in a row stop the run. See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md.
build_meme_profile_embeddings → embeds each profile's situation_text with the Ollama-served model from
                               settings taxonomy.embedding_model into meme_profile_situation_embeddings;
                               re-embeds when the stored vector's model name or digest differs. Run-tracked,
                               admin-triggerable, manual-trigger only; the second description_pipeline step
                               after build_meme_profiles. --reset deletes every situation embedding.
                               Both meme-profile jobs accept --ids-file PATH (JSON list or one UUID per line,
                               at most 20000) to restrict them to those images; --reset then only resets those.

python -m evaluation        → meme-from-text evaluation harness (P6): frozen 1,500-image corpus,
                               60 reviewed queries, human judgments and stored baselines under
                               evaluation/gold/ (committed; sheets, pools and thumbnails stay in
                               evaluation/work/, gitignored). Commands: build-corpus, build-pools --round N,
                               sheet --round N, import-judgments, run --retriever {random,lexical_and,
                               lexical_or,situation_embedding,axis_match,parsed_axis_match,fused} --label L, compare A B, list-baselines,
                               tune-weights (P4: fusion-weight search, see the retrieval entry),
                               export-ratings (P5b: turns the owner's stored result ratings into review files, see the
                               ratings entry below).
                               Database commands read only: set APP_ENV and DATABASE_URL to the READ-ONLY URL
                               (role ocr_readonly) in the shell first; the CLI refuses otherwise and never calls
                               load_env() (it would swap in the write-capable URL). Profile the corpus with the
                               P2 jobs and --ids-file evaluation/work/corpus.ids, not with this CLI. See
                               evaluation/gold/README.md and
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p6-evaluation-and-gold-set-design.md.

parse (package parse/)       → the meme-from-text query parser (P3): turns a pasted message (EN/RU/ES) into a
                               schema-valid query profile (language, English situation phrase, emotion, stance,
                               topic, tone as taxonomy keys with coarse confidences) with one schema-constrained
                               Ollama call (temperature 0, fixed seed, model from parse.model, only set for general
                               and metal). Standard library plus taxonomy only (an AST test enforces it); the
                               backend has no HTTP client dependency, so the client uses urllib in a worker thread.
                               `parse_query` (parse/tool.py) is the typed tool; the backend builds the process-wide
                               parser in Backend/app/services/query_parser.py (get_query_parser(), called in the
                               lifespan). The parser never raises: failures return a degraded profile
                               (degraded true, empty axes, meta.fallback unavailable | timeout | invalid_output |
                               empty_input), never cached; one retry only after an unusable answer and only if
                               parse.retry_min_seconds of the parse.timeout_seconds budget remain. The user's text is
                               never written to the application logs. Settings: parse.* in
                               environments/settings*.yaml (parse.num_ctx must equal
                               meme_profiles.num_ctx while both use the same model, otherwise Ollama reloads the model;
                               OLLAMA_HOST wins over parse.ollama_url; in Docker set it, 127.0.0.1 is the container).
                               The backend image needs parse/, taxonomy/ and batch/data/taxonomy (.dockerignore has
                               the matching re-include rules). Measured on the gold set with python -m evaluation
                               parse-eval --model M --label L (live Ollama, no database; reports in
                               evaluation/gold/parse/) and the parsed_axis_match retriever (run --retriever
                               parsed_axis_match --parse-model M, read-only database); build-pools accepts
                               --sources/--random/--depth/--parse-model to pool a parser's own top ten for a judging
                               round. See docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-05-p3-query-understanding-design.md
                               (addendum: model comparison, handoff notes for P4).

retrieval (package retrieval/) → the meme-from-text retrieval stage (P4): turns the P3 query profile into a ranked,
                               explained, paginated page of memes. Pure modules (stdlib, taxonomy, parse and retrieval
                               only, an AST test enforces it): config, scoring, weighted-sum fusion, duplicate-cluster
                               collapse and facets, cursor, the Ollama embedding client (urllib, like parse/), the
                               pipeline (result cache, parser circuit breaker, degraded paths) and the typed tools.
                               SQL lives in repository/meme_retrieval.py (eligible set: active, not flagged, has a
                               profile; three candidate sources: situation-embedding top-K, axis overlap, lexical;
                               never commits). Endpoint POST /api/meme-from-text/search (Backend/app/api/meme_from_text.py,
                               service Backend/app/services/meme_from_text_service.py; process-wide pipeline in
                               meme_from_text_pipeline.py, built at startup with a model warm-up task every 20 minutes;
                               `it` has no parser and answers `meta.available: false`). Never fails because a model
                               failed (degraded: unavailable, timeout, invalid_output, breaker_open, embed_unavailable;
                               lexical matching only runs in that case, lexical.only_when_degraded). The user's text is
                               never written to the application logs (the backend records it in search_history, see
                               ratings). Settings:
                               search.meme_from_text.* (every key required, including query_log.enabled; weights from
                               `python -m evaluation tune-weights --parse-model M --label L`, which collects the
                               candidates once and runs coordinate ascent with cross-validation, then you commit the
                               printed weights; situation and lexical weights must stay above 0). Evaluate with
                               `python -m evaluation run --retriever fused --parse-model M --label L` (read-only DB,
                               prints retrieval/parse/embed latency and degraded queries) and pool its top ten with
                               `build-pools --sources fused`. tests/load/synthetic_load_check.py fills the TEST database
                               (ocrdb_test only, never drops the schema) with 25,000 synthetic profiles and times the
                               retrieval stage (p95 budget 300 ms). Windows gotcha: a client request over 16,384 bytes
                               over the IPv6 loopback stalls ~44 ms, hence the compact vector literal and the array
                               parameters in the repository. The backend image needs retrieval/. See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-07-p4-retrieval-fusion-and-api-design.md
                               (addendum: results, decisions, handoff notes for P5).

ratings (P5b)                → the /reply page lets the owner rate each result (good fit 2 / acceptable 1 / not a fit 0).
                               Table meme_text_ratings (one row per message hash and image, migration e6b1c4d8f203);
                               PUT/DELETE/GET /api/meme-from-text/ratings and DELETE .../ratings/text; the hash is
                               computed server-side by retrieval/query_hash.py (sha256 of the parser-normalised message)
                               and returned as meta.queryHash. The message text is never written to the application logs
                               and never appears in an error body (Backend/app/error_handlers.py strips the echoed
                               input of 422s under /api/meme-from-text). It is stored, normalised and capped at
                               parse.max_input_chars, in search_history (kind meme_text, one row per fresh message:
                               no cursor, edited profile or filters; best effort; switch
                               search.meme_from_text.query_log.enabled; read with
                               GET /api/history?kind=search|meme_text|all, default search) and, with a rating, in
                               meme_text_ratings; "Forget text" (DELETE .../ratings/text) clears both (grades stay). Caveat: every fresh
                               message is logged, but the Forget button shows only once a message has a rating (the API call works for any). Ratings and the log
                               never influence ranking. `python -m evaluation
                               export-ratings --round N` (read-only role) writes import-ready judgments-<lang>.json for
                               ratings of gold queries, candidate-queries.json (the only file with text, in the
                               git-ignored evaluation/work/) and summary.json; merging into the gold judgments is a
                               deliberate `import-judgments` step; on `general` the batch jobs export_ratings and import_judgments do
                               both on a schedule (see their entries). See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5b-rating-widget-design.md
                               (addendum: live check, migration commands, handoff) and
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md
                               (query log, the two jobs; supersedes the "text stored only with a rating" rule).

curator tools (P5c)          → the meme page (`MemePage`) has a curator panel and `/admin/profiles` shows coverage. Endpoints:
                               GET/PUT /api/meme-from-text/profiles/{imageId} (PUT = save as human-verified: every value
                               source `human`, confidence 1, meta `human/human/human`), POST .../confirm (mark a model profile
                               verified without touching values; refuses 409 without a profile and 422 when the stored profile no
                               longer validates), DELETE (reset to model: deletes the profile and this pipeline's failure row so
                               the next build_meme_profiles run re-extracts it), GET /api/admin/profile-coverage and
                               /images?category= (live counts, last runs of the two jobs, keyset-paged lists). A human save
                               embeds the new situation inline (profile_editor.embed_timeout_seconds, computed before the write,
                               stored in the same transaction; failure = saved with vector `pending`, the embeddings job
                               refreshes it). A human-verified profile is stale ONLY when its taxonomy version differs
                               (select_work and coverage()); the batch never overwrites it. Curator and batch writers of one
                               image are serialised by a row lock (MemeProfileRepository._lock_profile); the curator path waits
                               at most 10 s. No migration. See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5c-curator-tools-design.md
                               (addendum: live check, deviations, handoff).

export_ratings              → turns the owner's stored result ratings (meme_text_ratings) into gold-set review files:
                               judgments-<lang>.json, candidate-queries.json (the only file with message text) and summary.json in
                               evaluation/work/ratings-export/current/ (git-ignored, replaced on every run). Round = highest round in
                               judgments.json + 1; pairs that already have a judgment are left out; never merges anything. Run-tracked
                               (kind export_ratings), admin-triggerable from /admin/batches, scheduled daily (1440 min) on `general` ONLY
                               (settings.general.yaml); on any other environment it finishes with skipped: true. Shares its code with the
                               CLI `python -m evaluation export-ratings` (evaluation/ratings_job.py), which keeps its read-only-role rule.
                               See docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md.
import_judgments            → merges the files of evaluation/work/ratings-export/current/ into the committed
                               evaluation/gold/judgments.json (atomic write, no database). NEVER commits: the change stays in the working
                               tree for a person to review and commit, and baselines recorded before it no longer match the judgments
                               file (judgmentsSha). NEVER overwrites an existing judgment (the run fails instead); with no export it
                               succeeds having added nothing. Run-tracked (kind import_judgments), admin-triggerable, scheduled weekly
                               (10080 min) on `general` ONLY. See docs/batches/use-cases/ratings-and-gold-set.md.

build_statistics            → precomputes the statistics page's numbers into the single-row
                               statistics_snapshots table (name='corpus'), so GET
                               /api/diagnostics/statistics is a row read instead of ~28
                               subqueries. Scheduled hourly (scheduler.jobs), admin-triggerable
                               from /admin/batches, and also run best-effort by
                               batch/run_wrapper.py after every other successful wrapped batch
                               (failures there are logged and swallowed, and it is skipped for
                               build_statistics itself). Scripts run directly from a shell
                               bypass the wrapper, and inputs that change outside any batch
                               (flagged, description feedback, ingestion promote/reject) are
                               only picked up by the hourly run -- there is deliberately no
                               per-mutation invalidation. The endpoint itself recomputes a
                               snapshot older than 2 hours, so deployments without a scheduler
                               stay bounded. The statistics page also requests `?live=true` on
                               every open, which recomputes and stores the snapshot, so the
                               snapshot is in practice refreshed whenever someone views the page;
                               the hourly job is the backstop. See
                               docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.

# Concept discovery for the new rules engine (see Rules engine below)
build_lemma_clusters       → draft_concepts_from_clusters

# Trends (independent of the image pipeline; scrapes/tags news sources on its own schedule)
trends_batch                → GLiNER NER over each configured trend source's fetched text,
                               counts label:entity mentions per run, stores results for the
                               Trends UI/API. Sources (RSS/API connectors) are registered via
                               batch/trends/seed_sources.py, not part of the regular run.

# Ingestion (new-image intake from PATH_INGESTION_SOURCE into the active library; fully
# implemented end to end -- hash dedup through promotion -- see
# docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md)
#
# Run order:
#   ingest_hash_dedup
#   ingest_validate_formats
#   build_image_embeddings --status pending --incremental
#   extract_text_from_memes --status pending   <-- before Tier A, not between the tiers (see
#                                                   Decision #10 in the design spec: empirical
#                                                   validation found Tier A's "thumbnails alone
#                                                   are decisive" premise doesn't hold for all
#                                                   content, e.g. same-format-different-text
#                                                   meme cards -- both tiers need OCR now)
#   build_ocr_lemmas --status pending --incremental   (needs OCR text; populates ocr_lemmas for
#                                                   smart search and for the OCR-text duplicate-
#                                                   matching corroboration check -- see
#                                                   docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md;
#                                                   no ordering dependency on classify_text_heavy,
#                                                   placed here to get coverage as early as possible)
#   classify_text_heavy --status pending   (needs OCR text; only affects the review UI's
#                                           text_heavy badge, not matching -- see its own entry)
#   build_ocr_text_embeddings --status pending   (needs classify_text_heavy's output -- only
#                                           text_heavy-classified images get embedded; needed by
#                                           ingest_find_duplicates' OCR-text probe for full
#                                           text-heavy-vs-text-heavy coverage, see
#                                           docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md)
#   ingest_find_duplicates --tier tier_a   (review in UI, reject/keep)
#   ingest_find_duplicates --tier tier_b   (review in UI, reject/keep)
#   ingest_promote
#
ingest_hash_dedup           → Stage 1: hashes every file in PATH_INGESTION_SOURCE, dedupes
                               in-batch and against the active corpus's content_hash, registers
                               survivors as `pending` images (content_hash + ingestion_batch_id
                               set at registration) and moves them into BASE_PATH. Safe to re-run
                               at any point while an ingestion run is active -- joins the active
                               run (same batch_id, stats accumulate across invocations) instead of
                               refusing, so newly-dropped files can be added to an in-progress
                               batch. A Postgres advisory lock serializes concurrent invocations of
                               this script against each other (not the other ingestion scripts --
                               don't run this concurrently with ingest_promote/ingest_abort either,
                               see their own entries). Newly-added images need
                               ingest_validate_formats.py, build_image_embeddings --status
                               pending --incremental, extract_text_from_memes --status pending,
                               build_ocr_lemmas --status pending --incremental, classify_text_heavy
                               --status pending, build_ocr_text_embeddings
                               --status pending, and ingest_find_duplicates.py
                               (both tiers, as applicable) re-run afterward to get review coverage
                               -- all seven are already safe to re-run against the same batch.
                               Skipping the embeddings step is not just incomplete --
                               ingest_find_duplicates.py's probe is an inner join
                               against embeddings, so an image with none is silently excluded from
                               review entirely and can reach ingest_promote unreviewed.
ingest_validate_formats     → Stage 1.5: renames files whose extension doesn't match their
                               real content, and converts any WebP content to JPEG
                               (original moved to converted_originals/ for audit) --
                               several downstream libraries (e.g. Ollama's vision backend)
                               can't consume WebP. Updates the same Image row's
                               filename/content_hash in place; unreadable files are flagged
                               via ImageExtras instead of touched. See
                               docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.
build_image_embeddings --status pending --incremental
                             → embeds Stage 1's survivors (existing script/flag, no ingestion-
                               specific code)
extract_text_from_memes --status pending
                             → OCR for Stage 1's survivors, run before either tier's review (see
                               run-order note above; existing script/flag, no ingestion-specific
                               code)
build_ocr_lemmas --status pending --incremental
                             → populates ocr_lemmas for Stage 1's survivors -- needed both for
                               smart search and, since 2026-09-19, for the OCR-text duplicate-
                               matching corroboration check (see
                               docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md).
                               --status pending requires --incremental (see build_ocr_lemmas'
                               own entry above); no ordering dependency on classify_text_heavy,
                               so it runs directly after OCR to get lemma coverage as early in
                               the chain as possible (existing script/flag, no ingestion-specific
                               code).
classify_text_heavy --status pending
                             → computes the text-heavy classifier for Stage 1's survivors, so the
                               review UI's text_heavy badge (see its own entry below) is populated
                               for newly-ingested images by the time either tier's review starts.
                               Purely additive metadata -- doesn't affect matching, candidate
                               selection, or promotion; existing script/flag, no ingestion-specific
                               code.
build_ocr_text_embeddings --status pending
                             → embeds Stage 1's text_heavy-classified survivors for the OCR-text
                               duplicate-matching probe -- needs classify_text_heavy's output, so
                               it runs after it (existing script/flag, no ingestion-specific code;
                               see build_ocr_text_embeddings' own entry above)
ingest_find_duplicates      → Tier A (--tier tier_a, default): populates tmp_duplicates for the
                               active ingestion run's pending images via the same find_duplicates()
                               primitive rebuild_duplicates.py uses, at clusterize.py's
                               PROXIMITY_THRESHOLD (0.05). --tier tier_b uses
                               settings.DUPLICATES.THRESHOLD (0.12, lowered from 0.3 on
                               2026-09-18 -- see the setting's own comment in
                               environments/settings.yaml for the empirical basis) as its
                               outer bound.
                               Each tier call now issues three probes, mirroring
                               rebuild_duplicates.py's own split (see
                               docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md):
                               general CLIP (excluding pairs where both sides have an
                               ocr_text_embeddings row -- not the same as both being text_heavy-
                               classified, see rebuild_duplicates.py's own _EXCLUDE_TEXT_HEAVY_PAIR
                               comment), a tight CLIP safety net (text_heavy-classification-scoped,
                               a deliberately broader superset), and an OCR-text-embedding probe
                               (embedding-existence-scoped, same as the general probe's exclusion). The OCR-text probe deliberately
                               ALWAYS inserts at TEXT_EMBEDDING_LOOSE_THRESHOLD (0.10) regardless
                               of which tier called it -- never derived from the tier's own
                               threshold (e.g. via min()) -- a real bug caught during that
                               feature's design; don't reintroduce a tier-derived OCR-text
                               threshold. The OCR-text probe additionally requires a lexical-overlap
                               corroboration check against ocr_lemmas before inserting a candidate
                               (MIN_LEMMA_OVERLAP_COEFFICIENT=0.2, MIN_LEMMA_COUNT_FLOOR=3) -- see
                               docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md.
                               Review (listing clusters, resolving reject/keep decisions) is the
                               /api/ingestion/* endpoints (Backend/app/api/ingestion.py), with a
                               frontend page at /ingestion covering both tiers (switches queue
                               based on the run's stage) -- both tiers show OCR text per member.
ingest_promote              → Stage 4 (final): promotes pending images with no remaining
                               unresolved Tier A/B candidate pairs to `active` (pure status flip
                               -- files are already in BASE_PATH from Stage 1). Marks the run
                               `completed` once every pending image in the batch is resolved
                               (promoted or rejected); safe to re-run as more images clear review.
                               Admin-triggerable from /admin/batches (kind="ingest_promote"),
                               manual-trigger only; a click with no active ingestion run surfaces
                               as a failed run rather than being silently swallowed, since it's an
                               explicit human action, not a scheduler tick.
ingest_abort                → Abandons the currently active ingestion run instead of continuing
                               it: undoes every pending/rejected image it registered (moves each
                               file back to PATH_INGESTION_SOURCE, deletes its row -- FK cascades
                               clean up embeddings/OCR/tmp_duplicates automatically) and marks the
                               run `aborted`, freeing the one-active-run-per-kind lock. Never
                               touches already-`active` (promoted) images in the batch. Can be run
                               at any point before the run completes -- right after Stage 1, mid-
                               review, etc.
```

`ingest_auto_prep` is a one-job alternative to running Stage 1 through the Tier A step by
hand: it chains `ingest_hash_dedup` → `ingest_validate_formats` → `build_image_embeddings
--status pending --incremental` → `extract_text_from_memes --status pending` →
`build_ocr_lemmas --status pending --incremental` → `classify_text_heavy --status pending` →
`build_ocr_text_embeddings --status pending` → `ingest_find_duplicates --tier tier_a`, self-tracked
under `kind="ingestion_auto_prep"` —
deliberately distinct from the long-lived `kind="ingestion"` review-run row, since that row
can stay open for days during human review and must not be mistaken for an orphaned/crashed
job. It's manual-trigger-only (via `/admin/batches`, not `scheduler.jobs`) — no automatic
schedule. Tier B and promotion remain manual either way, but are individually
admin-triggerable too: `ingest_find_duplicates_tier_b` (a thin driver around
`ingest_find_duplicates.main(tier="tier_b")` — needed because the admin-trigger contract
calls `module.main(trigger=, run_id=)` with no way to pass `tier` through) and
`ingest_promote` itself.

Most jobs are idempotent (clear and rebuild). `rebuild_duplicates` is also idempotent as of
2026-07-25 — it inserts incrementally into a persistent, migration-managed `tmp_duplicates` table
(`ON CONFLICT DO NOTHING`) rather than dropping and recreating it every run.

**Full re-run (re-process all images):**
Do NOT clear the `ocr_texts` table. Instead:
1. `python -m batch.reset_ocr_status --all` — resets processing status without touching OCR data.
2. `python -m batch.extract_text_from_memes` — re-processes all images, overwriting OCR per image.
This ensures OCR data is preserved if the run is interrupted.

### Rules engine

Two implementations coexist:

- **Legacy** (`rules/engine.py` + `batch/data/rules.*.json`): regex/substring matching → tags. No batch job uses it any more (only dev tools under `batch/tools/` and `batch/diff_rules.py`).
- **Current** (`rules/concept_tagger.py`, `batch/data/tagging/`): concept voting with YAML rule files. See `batch/rules_engine.md` for the full design. Used by both `build_tags_from_ocr` and `build_tags_from_descriptions`.

`rules/normalize.py` is shared by both engines and `build_bow.py` — use it for all text normalization to keep behavior consistent.

**Concept discovery** (drafting new entries for the new design): `build_lemma_clusters` embeds `build_bow`'s unmatched lemmas (sbert) and clusters them per-language (HDBSCAN; `lemma_clustering.selection_method: leaf` avoids one oversized "catch-all" cluster that the default `eom` tends to produce), optionally naming each cluster via Ollama. `draft_concepts_from_clusters` then takes the top N clusters and appends draft entries to `concepts.<env>.yaml` + `tags.<env>.yaml` for human review. Both are chained together by the `/draft-lemma-concepts` Claude Code command (`.claude/commands/draft-lemma-concepts.md`), which also commits the raw draft before review and again after.

`bow.ignore_file` (`batch/data/ignore-words.<env>.json`) edits only take effect after the next `build_bow` run — `build_lemma_clusters` reads `build_bow`'s output (`bow.unmatched.<env>.json`), never the ignore file itself, so a stale unmatched file will keep surfacing already-ignored words until `build_bow` is rerun.

### Frontend

Vite reads env files from `../../environments/` (relative to `Frontend/memes-frontend/`). The `VITE_BACKEND_API_URL` var must be set per environment. TypeScript types for API responses are generated from `shared/schemas/` — do not hand-write them.

The meme-from-text page is `/reply` (`src/pages/ReplyPage.tsx`): logic lives in `src/reply/` (pure helpers, a reducer, `useMessageSearch`, `useTaxonomy`, sessionStorage persistence), presentational components in `src/components/reply/`, and the vocabulary comes from `GET /api/meme-from-text/taxonomy`. The app renders in `StrictMode` and uses the React Compiler ESLint rules, so every effect must survive a double mount and refs are only touched in handlers and effects. A paging cursor is tied to the exact request that produced it: "Show more" replays the request of the current list. See `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-08-p5-web-ui-design.md` (addendum: live check, deviations, handoff).

### Android

`imageUrl` from the API is a relative path (`/api/images/{id}`). Coil requires absolute URLs; callers prepend `http://localhost` and the OkHttp interceptor in `NetworkModule` rewrites the host at request time. All navigation is wired through `NavGraph.kt`; screens receive callbacks and never touch `NavController` directly.

### Python environments

Two venvs exist: `.venv` (Python 3.13) and `.venv311` (Python 3.11). The project targets **Python 3.11** — use `.venv311` for batch jobs and backend. Four requirements files:

| File | Use |
|------|-----|
| `Backend/requirements-backend.txt` | FastAPI server only — single source of truth for both CI (tests/coverage/integration) and `Dockerfile.backend` (production image). Built with `pip wheel --no-deps`, so this file must be the full closure (direct + transitive pins), not just top-level packages — regenerate via a clean-venv `pip freeze` when changing it, don't hand-edit individual versions. |
| `requirements.txt` (root) | Full ML/batch stack, CPU PyTorch — no dev tools |
| `requirements-cuda.txt` (root) | Overrides for NVIDIA GPU — includes `--extra-index-url` for PyTorch |
| `requirements-dev.txt` (root) | Dev tools: `autoflake`, `black`, `isort`, `pytest*`, `coverage` |

There used to be a second, near-duplicate `requirements-backend.txt` at the repo root — `Dockerfile.backend` used it while CI used `Backend/requirements-backend.txt`, and the two drifted apart (root stuck on `fastapi==0.128.0` while `Backend/`'s stayed current at `0.139.2`). The root copy was removed; `Dockerfile.backend` now points at `Backend/requirements-backend.txt` for both. See `dependencies.md` for the full investigation.

### Configuration

Config is split by whether it's safe to commit:

- **Secrets** (`environments/.env.<environment>`, gitignored): `APP_ENV`, `DATABASE_URL`, `BASE_PATH`, `SERP_API_KEY`, LAN-facing origins. Loaded by uvicorn's `--env-file` flag (Backend) or `config.settings.load_env()` (batch).
- **Tracked config** (`environments/settings.yaml` + `settings.<environment>.yaml`, committed): tuning parameters, feature flags, repo-relative paths, deterministic localhost origins. Loaded by `config/settings.py` (Dynaconf), merging the common file with the active environment's override file.

Both layers are read through the single `settings` object in `config/settings.py`. Tracked config is grouped by domain — `ocr`, `rules`, `bow`, `lemma_clustering`, `concepts`, `ollama`, `general` (cross-cutting keys with no single domain owner) — so access is `settings.GROUP.KEY` for keys guaranteed present in that group across all three environments, or `settings.get("GROUP.KEY")` (one dotted-path string) for keys that may be entirely absent for an environment (e.g. `it` has no `rules.file`). See `docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md` for the full key inventory and grouping rationale. `os.environ` always wins over tracked YAML (e.g. `DATABASE_URL` only ever comes from the `.env` layer, never from `settings.yaml`). Search ranking is tuned under `search.ranking.*` (`source_weights`, `tier_weights`, `warn_match_count`); a missing weight key is a startup error. See `docs/adr/adr-2026-10-01-search-ranking-in-python.md` when revisiting Python-side scoring.

`APP_ENV` (`metal`/`general`/`it`) selects which environment's YAML overlay and `.env.<environment>` file are used; it defaults to `general` if unset. Backend gets it from `--env-file` (already in `os.environ` by the time `config.settings` is imported). Batch scripts must call `config.settings.load_env(...)` as the first thing in `main()` — never read `settings.X` before that call, since the module-level `settings` object is built once at import time using whatever `APP_ENV` happens to already be in the shell. `load_env()` itself falls back to `APP_ENV` when called with no argument (or `None`), so an explicit `--env {metal,general,it}` CLI flag is not required — most batch scripts add one anyway for convenience (`load_env(args.env)`), but a script can just call `load_env()` and rely on `APP_ENV` already being set in the shell (see `batch/move_reference_duplicates.py`).

Caveat: batch scripts that import `Storage.db` at module level still require `DATABASE_URL` to already be in the shell — `Storage/config.py`'s own `RuntimeError` guard fires at import time, before `--env`/`load_env()` ever run. `--env` only replaces implicit shell-sourcing for *tracked* `settings.X` values read inside `main()`, not for this secret. See `docs/adr/adr-2026-07-05-config-management.md` for the full design rationale and `docs/superpowers/specs/2026-07-05-config-management-migration.md` for the migration details.

## Specs and implementation workflow

### Where specs live

All design specs are stored in `docs/superpowers/specs/`.

**Naming convention:** `YYYY-MM-DD-<short-kebab-summary>.md`  
Examples: `2026-06-29-ocr-safe-full-mode.md`, `2026-07-01-upload-endpoint.md`

### Specification status and cross-references

Every spec (including drafts in `docs/superpowers/specs/drafts/`) starts with a
status line directly under its title, using exactly one of these values:

- `draft` — idea captured, not yet reviewed/approved by the user.
- `approved` — design reviewed and approved, ready for an implementation plan.
- `planned` — an implementation plan exists (link it, see below).
- `implementation` — a plan is actively being executed (subagent-driven-development
  in progress).
- `done` — implemented and merged.

Update the status line in place as the spec moves through its lifecycle — a spec
that's actually merged should not still say `approved` or `planned`.

Carry whichever of these cross-reference links are applicable directly under the
status line:

- `Plan:` — path to the implementation plan, once `writing-plans` creates one.
- `Originates from:` — path to the spec (or backlog draft item) this one was
  spawned from, if any. A spec discovered as a side effect of another feature's
  final whole-branch review, or picked up from a backlog draft item, always gets
  this link.
- `Follow-ups:` — paths to specs this one later spawned. Add this link to the
  *earlier* spec retroactively once the follow-up spec is written, so the chain is
  navigable in both directions — don't leave it as a link only the newer spec
  carries.

### Implementing a spec

1. Read `docs/superpowers/specs/<spec-file>.md` in full before touching any code.
2. Start implementation; spin subagents when tasks are independent and can run in parallel.
3. When implementation is complete, review the changes:
   - Logic correctness against the spec
   - Code quality: duplication, readability, reusability, structure, test coverage
   - Run tests (`pytest` for backend, `vitest run` for frontend) — see the
     "Running the right test scope" gotcha below before deciding which test roots
     a given change actually needs
   - Confirm every requirement in the spec is addressed
4. Save the review report in `docs/superpowers/reviews/` named after the spec:  
   `YYYY-MM-DD-<spec-summary>-review.md`
5. If the review surfaces action points, spin another agent to fix them.
6. Do only **one** review iteration. Post a final summary here:
   - What was fixed
   - What was not fixed but explained (acceptable deviation)
   - What was intentionally ignored and why

## Key invariants

- Env files (secrets) and tracked settings YAML both live in `environments/` (not `Storage/`) — see Configuration above.
- The metal/general/IT backend, frontend, and database ports are always occupied by the developer's running environments — never bind to them for testing. See `environments/Environments.md`.
- `backend_api.md` must stay in sync with the actual routers.
- Image `status` values (`active`, `pending`, `rejected`, `cut_proposed`) are defined in one place, `docs/image-status.md`. Add a row there in the same change as any new status; link to it instead of redefining one. What hangs off an image, and how a recut copies it, is `docs/image-derived-data.md` (kept in sync by `tests/docs/`).
- Windows dev: `WATCHFILES_FORCE_POLLING=1` is required for uvicorn `--reload` to work.
- AGP 8.5.2 requires Java 11+; set `JAVA_HOME` to Android Studio JBR before Gradle commands (do not commit to `gradle.properties`).
- **Never give a subagent the main `DATABASE_URL`.** If a subagent needs to query a live
  environment's database for any reason (verification, cost estimation, debugging), it gets
  `DATABASE_URL_READONLY` only, explicitly labeled as such in the dispatch prompt — see "Live
  database access for agents" below.

### Live database access for agents

Each `environments/.env.<environment>` file has a `DATABASE_URL_READONLY` alongside the normal
`DATABASE_URL` — same host/port/database, but connects as `ocr_readonly`, a role granted `SELECT`
only (`GRANT SELECT ON ALL TABLES IN SCHEMA public`, plus a matching `ALTER DEFAULT PRIVILEGES` so
it stays readable after future migrations — no `INSERT`/`UPDATE`/`DELETE`/DDL rights at all,
verified by a real rejected `DELETE` at role-creation time, 2026-08-06).

**Rule:** a subagent must never be handed the main `DATABASE_URL` to run ad hoc queries against a
live metal/general/IT database — only `DATABASE_URL_READONLY`, and the dispatch prompt must say so
explicitly (don't just paste a DSN with no label; state "this connects with a read-only role,
writes will be rejected" so the subagent doesn't attempt one and waste a turn on a permission
error). Prefer having the **controller** run any live-DB verification query directly instead of
delegating it to a subagent at all — the controller is a single, known execution context, whereas
a subagent's read-only instruction is only as strong as the sentence enforcing it (this project's
own gotchas already established the pattern of the controller re-verifying a subagent's claims
independently — DB queries are the same principle).

**Why this exists:** on 2026-08-05, a final-review subagent given `DATABASE_URL` to "measure query
cost" ran `EXPLAIN ANALYZE DELETE FROM tmp_clusters WHERE ...` against the live `general` and `it`
databases. **`EXPLAIN ANALYZE` executes the statement for real — it is not a dry run for DML.**
The delete was blocked by the harness's own safety classifier before it reached the real
connection (confirmed after the fact: `general`'s row/cluster counts exactly matched the
pre-incident baseline), but the instruction "your review is read-only on this checkout" was scoped
to the git working tree only and never said anything about the databases the checkout's own config
could reach — a subagent with `DATABASE_URL` and a legitimate-sounding reason to query it had
every ability to mutate live, shared, continuously-running databases with no explicit
authorization for that specific action. Giving out only a role that physically cannot write closes
this regardless of how a future dispatch prompt happens to be worded.

## Known gotchas (debugging notes)

- **`data/...` config paths (e.g. `image_descriptions.prompts_file`, `concepts.text_concepts_file`, `rules.file`) are opened as bare relative paths, so they only resolve if cwd happens to be `batch/`.** Running a script the documented way (`python -m batch.xxx` from repo root) leaves cwd at the repo root, not `batch/`, and raises `FileNotFoundError`. Fixed in `batch/build_image_descriptions.py` by resolving `prompts_file` relative to `os.path.dirname(__file__)`. Other scripts reading `data/...` paths directly (e.g. `build_concept_embeddings.py`) likely still have this latent bug — if you hit a `FileNotFoundError` for a `data/...` path, this is probably why.
- **Never combine `Backend/tests/`, `tests/integration/`, and the other test roots (`batch/tests/`, `tests/rules/`, `tests/ai/`) in one `pytest` invocation.** They have separate `pytest.ini` files with different `asyncio_mode` (Backend's is `Mode.AUTO`, the rest is `Mode.STRICT`); combining roots in one command breaks Backend's `async def` test collection ("async def functions are not natively supported") even though each root passes cleanly on its own. Always run them as separate `pytest` commands.
- **`tests/integration/` needs `DATABASE_URL` set explicitly on the command line**, e.g. `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`. A top-level `tests/conftest.py` sets a dummy placeholder `DATABASE_URL` via `os.environ.setdefault(...)` before `tests/integration/conftest.py`'s own default gets a chance to apply, so omitting it fails with `password authentication failed for user "test"` instead of actually connecting. The dedicated test database is `ocrdb_test` (user/password `ocr`) on the `ocr-db` docker container, port 5432 — a genuinely separate database from the real `ocrdb` dev database on the same server, safe to run tests against.
- **Running the right test scope: the one integration test file that looks relevant to a change is not enough — run the whole root.** Confirmed twice across two consecutive merged branches: a shared-code change (`rules/normalize.py`'s stemming dispatch, then a follow-up vocabulary-loading change touching `batch/build_bow.py`) broke assertions in `tests/integration/test_build_ocr_lemmas.py` and `tests/integration/test_build_ocr_bow_lang_filter.py` — neither file seemed related to the change under review (reviews were scoped to `tests/rules/`/`batch/tests/`, or to the one integration file most directly tied to the feature, e.g. `test_ocr_lemmas_repository.py` for search-matching work), so neither got run, and both sat broken through two merges before being caught. Any change to shared normalization/matching code (`rules/normalize.py`, `repository/ocr_lemmas.py`, `rules/concept_tagger.py`, `batch/build_bow.py`, or anything else many callers share) needs the **entire** `tests/integration/` root run (`DATABASE_URL=... pytest tests/integration/ -v`) before merging, and that same full-root scope should be handed to any subagent doing a task or final whole-branch review of such a change — not just the file(s) that look directly on-topic.
- **`EnterWorktree` defaults to branching from `origin/<default-branch>`, not local HEAD.** In a sandboxed dev environment with no live GitHub access (`git fetch`/`pull` fail with `Permission denied (publickey)`), the cached `origin/main` ref can be many commits stale, so a new worktree can silently miss recent local-only commits. After creating a worktree, compare `git rev-parse main` against the worktree's `HEAD`; if behind, `git merge --ff-only <target-sha>` inside the worktree before starting work.
- **Windows: `run_in_background: true` on Bash/PowerShell tool calls still enforces a hard ~10 minute timeout that kills the process**, not just stops watching it — confirmed against a real long-running batch job that was silently gone at exactly the 10-minute mark. For anything that needs longer, either scope it down to finish within ~10 minutes (e.g. a `--limit` flag, rerun as needed) or launch it as a truly detached OS-level process (see `docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md`'s recovery step 5 for a Windows `Start-Process` pattern).
- **Some images in the `general` corpus are WebP files saved with a `.jpg` extension**, which Ollama's llava/qwen2.5vl vision backend cannot decode ("Failed to load image or audio file") — the existing `path.lower().endswith("webp")` skip in `build_image_descriptions.py` only checks the extension, not actual content, so these slip through. Rare (a ~2,000-file sample of ~22,000 images found none beyond the two already known) but non-zero. This is now handled by content sniffing: `batch/ingest_validate_formats.py` (ingestion Stage 1.5, for every newly-ingested image) and `batch/fix_image_formats.py` (retroactive maintenance over the existing corpus) open each file with Pillow, convert any real WebP content to JPEG regardless of its extension (original preserved in `<BASE_PATH>/converted_originals/`), and rename other extension/content mismatches in place — see `docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md`. The older behavioral mitigation still stands as a backstop for anything that slips through: the failure-tracking feature marks a failed (image, prompt) pair permanently failed after one attempt, so it isn't retried forever.
- **`.dockerignore` has no relationship to `.gitignore` — a gitignored (untracked) directory is still sent as Docker build context unless it's separately listed in `.dockerignore`.** `Storage/backups/` (local Postgres dumps, tracked in `Storage/.gitignore`, 33GB on a real dev machine) and `batch/images/` (the meme corpus) were both being transferred on every `docker build -f Dockerfile.backend` despite neither ever being `COPY`'d, making the build context 500MB+ and climbing, sometimes taking 10+ minutes just to transfer. If a Docker build seems to hang at "load build context", run `du -sh */` at the repo root and compare against `.dockerignore` — don't assume "it's gitignored" means "Docker won't see it." See `dependencies.md` for the full incident.
- **Any batch script using `batch/utils/progress.py`'s `ProgressTracker` can crash with `UnicodeEncodeError: 'charmap' codec can't encode character '≈'` when run interactively on Windows** — its progress lines contain "≈" (U+2248), which isn't encodable in Windows' default console codepage (cp1252) unless the console/process is explicitly UTF-8. Confirmed against a real run of `build_ocr_lemmas.py --env metal`: it crashed 1,800 images into a 17,634-image full rebuild. This is worse than a clean failure — `OCRLemmasSaver.__aexit__` (and the equivalent in other batch scripts using a similar `async with ... Saver` pattern) commits unconditionally as the exception unwinds through it, so the crash left a **partial** rebuild committed (1,800 images with fresh lemmas, the other ~15,800 with none at all) rather than either a clean full rebuild or a no-op failure. Fix: set `PYTHONIOENCODING=utf-8` in the environment before running any batch script interactively on Windows (e.g. `PYTHONIOENCODING=utf-8 python -m batch.build_ocr_lemmas --env metal`), not just for `build_ocr_lemmas.py` — any script pulling in `ProgressTracker` is exposed the same way.
- **Some `Backend/app/api/*.py` routers hand-write their own Pydantic response models (e.g. `ingestion.py`'s `ClusterMember`/`ClusterPage`/`TierBReviewPage`) instead of importing the schema-generated ones from `Backend/app/types/generated/`.** Adding a field to a `shared/schemas/*.schema.json` file and regenerating all three trees (TypeScript, Kotlin, Python) is not sufficient in that case — the generated Python model changes, but the actual `response_model=` FastAPI uses is the hand-written class, which silently drops any field it doesn't declare (Pydantic strips unknown fields on serialization, no error). Confirmed while adding `text_heavy` to `IngestionClusterMember` (2026-09-16): the service layer and the generated `Schema` class both had the field, but `GET /api/ingestion/clusters/{tier}` kept omitting it from real responses until `ingestion.py`'s own `ClusterMember` was edited directly. If a schema field isn't showing up in a live response after regenerating types, grep the relevant router file for a same-named hand-written `BaseModel` before assuming the regeneration failed.
- **A long-running `.venv311` process staying healthy proves nothing about whether that venv can still start a fresh process.** Confirmed 2026-09-24: the three always-on `metal`/`general`/`it` backend dev servers (see Environments.md) had been running for a long time and looked completely fine — but restarting `metal` after a routine merge (to confirm the merge landed) immediately hit `ModuleNotFoundError: No module named 'annotated_doc'`, then `anyio`, then `asyncpg.connection` (the last one especially deceptive: `pip show asyncpg` reported it fully installed, but its files were actually missing from disk — a corrupted, not just absent, install). None of this was caused by the merge; a long-running process already has everything it needs loaded into memory, so venv rot like this only surfaces the moment something forces a fresh import — a restart, a fresh script invocation, a new worker. **Root cause, confirmed 2026-09-26 (this had been symptomatically patched three separate times before being diagnosed):** pip's atomic package-replace mechanism renames a package's old version to a `~`-prefixed stash directory (e.g. `asyncpg` → `~syncpg`, first character replaced with `~`) before installing the replacement, and only deletes the stash on success. When the install process is killed mid-operation — e.g. a Bash/PowerShell tool call's own timeout expiring during a slow install, or the `run_in_background` ~10-minute hard kill documented elsewhere in this section — that stash is orphaned permanently, and the real package is left either entirely missing (matches `alembic` vanishing outright) or partially written (matches `asyncpg` "installed" per metadata but missing `connection.py`). Forensic evidence in the old `.venv311`: orphaned `~`-prefixed stash directories for `numpy`, `torch`, `torchvision`, `sqlalchemy` (3 variants), `asyncpg`, `greenlet` (4 variants), `uvicorn`, `yaml`, and `pydantic_core` (6 variants — meaning that one package alone had been hit by this at least six separate times), dated January through August 2026 by real filesystem timestamps — this was a chronic problem predating this session, not something introduced by it. Large/slow-to-install packages (compiled C extensions, big wheels) are disproportionately likely to still be mid-install when a timeout fires, which is why the same handful of heavy packages keep recurring. **Fix applied 2026-09-26:** rebuilt `.venv311` from a clean `pip install` of the frozen package list into a fresh venv on a genuinely local disk (`C:\dev\memes-venv311` — confirmed local via `Get-Volume`'s distinct volume ID, as opposed to `H:`, which some background sync-client registry state falsely suggested was cloud-backed; that was checked and ruled out, `H:` is a real local fixed disk), then replaced `H:\workspace_sandbox\memes\.venv311` with an NTFS junction pointing at it (old broken venv preserved as `.venv311.broken-backup-2026-09-26`, not deleted). **Going forward:** give any `pip install`/`upgrade` touching this ML/batch stack a long explicit timeout (up to the tool's max) rather than the default, and never run one via `run_in_background` for longer than it can plausibly finish inside ~10 minutes; periodically check `Lib\site-packages` for orphaned `~`-prefixed directories (`ls .venv311/Lib/site-packages | grep '^~'`) as an early warning that an install got killed. Separately, `pip check` on the same venv found the `paddleocr`/`paddlepaddle-gpu`/`datamodel-code-generator` batch/ML stack missing several of its own listed dependencies too (`attrdict`, `cython`, `fire`, `lxml`, `openpyxl`, `pdf2docx`, `premailer`, `python-docx`, `visualdl`, `astor`, `argcomplete`) — same pattern, just never triggered because nothing had tried a fresh import of those paths recently either. Fixed by reinstalling the missing packages (`pip install --force-reinstall --no-deps asyncpg==0.31.0` for the corrupted one; plain installs for the rest, pinned to this repo's own `requirements.txt` version where one exists) and iterating on `pip check` until only genuine, unfixable conflicts remained. One real conflict surfaced this way: `paddleocr==2.7.0.3` pins `PyMuPDF<1.21.0`, but `pdf2docx` — itself one of `paddleocr`'s own listed dependencies — needs `PyMuPDF>=1.26.7`; no single version satisfies both. Kept the newer `PyMuPDF` (pdf2docx's floor is more likely load-bearing than paddleocr's stale ceiling from an old release) and left `paddleocr`'s mismatch, plus two similar `opencv-python`/`opencv-contrib-python` ceiling mismatches against paddleocr's old pins, as accepted `pip check` noise — downgrading widely-used imaging libraries to satisfy one old package's conservative pin risks breaking newer code that depends on current APIs. Separately, `paddleocr` is currently unimportable regardless of venv (`import paddleocr` → `AttributeError: np.sctypes was removed in the NumPy 2.0 release`, via its `imgaug` dependency) — a pre-existing incompatibility with this repo's pinned `numpy==2.2.6`, confirmed present in both the old and rebuilt venv, not caused or fixed by the 2026-09-26 rebuild. Takeaway: run `pip check` in `.venv311` periodically, especially before restarting any of the three always-on backends — a green `pip check` is cheap insurance a healthy-looking running process can't give you.