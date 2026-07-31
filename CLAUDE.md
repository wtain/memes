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

```
extract_text_from_memes    → registers images + EasyOCR (EN/ES/RU)
build_image_embeddings     → CLIP 512-dim vectors
rebuild_duplicates         → near-duplicate candidate pairs, incremental by default (only images
                              without an existing tmp_duplicates row are probed); HNSW-assisted
                              KNN, not a full cross join — see
                              docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.
                              --full clears active-library pairs and re-probes everything;
                              --k/--threshold override settings.DUPLICATES.K/THRESHOLD.
clusterize                 → optimize cluster index

build_tags_from_ocr        → rule-based tags from OCR text
build_ocr_lemmas           → per-image lemma index for smart search (see
                              docs/superpowers/specs/2026-07-21-smart-search-design.md);
                              --incremental skips images already indexed
build_image_descriptions   → multi-prompt Ollama LLM descriptions (optional), one row per
                              (image, prompt) pair; configurable prompts/models/context size
                              per environment; incremental with its own commit interval;
                              permanently-failed pairs are skipped by default (--retry-failed
                              to re-attempt, --reset to clear everything, --limit to cap a run).
                              See docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md
                              and docs/superpowers/specs/2026-07-15-image-description-failure-tracking-and-context-size.md
build_tags_from_descriptions → rule-based tags from descriptions
build_concept_embeddings   → concept CLIP embeddings + mappings

# Maintenance (run as needed)
detect_file_duplicates     deduplicate_ocr_texts     move_flagged     unregister_deleted_images
detect_entities_and_tag    tag_images_from_concepts  build_bow

move_flagged                → also runs unregister_deleted_images automatically afterward,
                               reconciling the DB with whatever was moved; --no-chain skips this.

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
#   build_image_embeddings --status pending --incremental
#   extract_text_from_memes --status pending   <-- before Tier A, not between the tiers (see
#                                                   Decision #10 in the design spec: empirical
#                                                   validation found Tier A's "thumbnails alone
#                                                   are decisive" premise doesn't hold for all
#                                                   content, e.g. same-format-different-text
#                                                   meme cards -- both tiers need OCR now)
#   ingest_find_duplicates --tier tier_a   (review in UI, reject/keep)
#   ingest_find_duplicates --tier tier_b   (review in UI, reject/keep)
#   ingest_promote
#
ingest_hash_dedup           → Stage 1: hashes every file in PATH_INGESTION_SOURCE, dedupes
                               in-batch and against the active corpus's content_hash, registers
                               survivors as `pending` images (content_hash + ingestion_batch_id
                               set at registration) and moves them into BASE_PATH. Refuses to
                               start if another ingestion run (batch_runs, kind="ingestion") is
                               already in progress.
build_image_embeddings --status pending --incremental
                             → embeds Stage 1's survivors (existing script/flag, no ingestion-
                               specific code)
extract_text_from_memes --status pending
                             → OCR for Stage 1's survivors, run before either tier's review (see
                               run-order note above; existing script/flag, no ingestion-specific
                               code)
ingest_find_duplicates      → Tier A (--tier tier_a, default): populates tmp_duplicates for the
                               active ingestion run's pending images via the same merged
                               probe/corpus find_duplicates() primitive rebuild_duplicates.py
                               uses, at clusterize.py's PROXIMITY_THRESHOLD (0.05). --tier tier_b
                               uses settings.DUPLICATES.THRESHOLD (0.3) as its outer bound.
                               Review (listing clusters, resolving reject/keep decisions) is the
                               /api/ingestion/* endpoints (Backend/app/api/ingestion.py), with a
                               frontend page at /ingestion covering both tiers (switches queue
                               based on the run's stage) -- both tiers show OCR text per member.
ingest_promote              → Stage 4 (final): promotes pending images with no remaining
                               unresolved Tier A/B candidate pairs to `active` (pure status flip
                               -- files are already in BASE_PATH from Stage 1). Marks the run
                               `completed` once every pending image in the batch is resolved
                               (promoted or rejected); safe to re-run as more images clear review.
```

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

- **Current** (`rules/engine.py` + `batch/data/rules.*.json`): regex/substring matching → tags. Used by `build_tags_from_descriptions`.
- **New design** (`rules/concept_tagger.py`, `batch/data/tagging/`): concept voting with YAML rule files. See `batch/rules_engine.md` for the full design. Already wired into the main pipeline via `build_tags_from_ocr`.

`rules/normalize.py` is shared by both engines and `build_bow.py` — use it for all text normalization to keep behavior consistent.

**Concept discovery** (drafting new entries for the new design): `build_lemma_clusters` embeds `build_bow`'s unmatched lemmas (sbert) and clusters them per-language (HDBSCAN; `lemma_clustering.selection_method: leaf` avoids one oversized "catch-all" cluster that the default `eom` tends to produce), optionally naming each cluster via Ollama. `draft_concepts_from_clusters` then takes the top N clusters and appends draft entries to `concepts.<env>.yaml` + `tags.<env>.yaml` for human review. Both are chained together by the `/draft-lemma-concepts` Claude Code command (`.claude/commands/draft-lemma-concepts.md`), which also commits the raw draft before review and again after.

`bow.ignore_file` (`batch/data/ignore-words.<env>.json`) edits only take effect after the next `build_bow` run — `build_lemma_clusters` reads `build_bow`'s output (`bow.unmatched.<env>.json`), never the ignore file itself, so a stale unmatched file will keep surfacing already-ignored words until `build_bow` is rerun.

### Frontend

Vite reads env files from `../../environments/` (relative to `Frontend/memes-frontend/`). The `VITE_BACKEND_API_URL` var must be set per environment. TypeScript types for API responses are generated from `shared/schemas/` — do not hand-write them.

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

Both layers are read through the single `settings` object in `config/settings.py`. Tracked config is grouped by domain — `ocr`, `rules`, `bow`, `lemma_clustering`, `concepts`, `ollama`, `general` (cross-cutting keys with no single domain owner) — so access is `settings.GROUP.KEY` for keys guaranteed present in that group across all three environments, or `settings.get("GROUP.KEY")` (one dotted-path string) for keys that may be entirely absent for an environment (e.g. `it` has no `rules.file`). See `docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md` for the full key inventory and grouping rationale. `os.environ` always wins over tracked YAML (e.g. `DATABASE_URL` only ever comes from the `.env` layer, never from `settings.yaml`).

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
- Windows dev: `WATCHFILES_FORCE_POLLING=1` is required for uvicorn `--reload` to work.
- AGP 8.5.2 requires Java 11+; set `JAVA_HOME` to Android Studio JBR before Gradle commands (do not commit to `gradle.properties`).

## Known gotchas (debugging notes)

- **`data/...` config paths (e.g. `image_descriptions.prompts_file`, `concepts.text_concepts_file`, `rules.file`) are opened as bare relative paths, so they only resolve if cwd happens to be `batch/`.** Running a script the documented way (`python -m batch.xxx` from repo root) leaves cwd at the repo root, not `batch/`, and raises `FileNotFoundError`. Fixed in `batch/build_image_descriptions.py` by resolving `prompts_file` relative to `os.path.dirname(__file__)`. Other scripts reading `data/...` paths directly (e.g. `build_concept_embeddings.py`) likely still have this latent bug — if you hit a `FileNotFoundError` for a `data/...` path, this is probably why.
- **Never combine `Backend/tests/`, `tests/integration/`, and the other test roots (`batch/tests/`, `tests/rules/`, `tests/ai/`) in one `pytest` invocation.** They have separate `pytest.ini` files with different `asyncio_mode` (Backend's is `Mode.AUTO`, the rest is `Mode.STRICT`); combining roots in one command breaks Backend's `async def` test collection ("async def functions are not natively supported") even though each root passes cleanly on its own. Always run them as separate `pytest` commands.
- **`tests/integration/` needs `DATABASE_URL` set explicitly on the command line**, e.g. `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`. A top-level `tests/conftest.py` sets a dummy placeholder `DATABASE_URL` via `os.environ.setdefault(...)` before `tests/integration/conftest.py`'s own default gets a chance to apply, so omitting it fails with `password authentication failed for user "test"` instead of actually connecting. The dedicated test database is `ocrdb_test` (user/password `ocr`) on the `ocr-db` docker container, port 5432 — a genuinely separate database from the real `ocrdb` dev database on the same server, safe to run tests against.
- **Running the right test scope: the one integration test file that looks relevant to a change is not enough — run the whole root.** Confirmed twice across two consecutive merged branches: a shared-code change (`rules/normalize.py`'s stemming dispatch, then a follow-up vocabulary-loading change touching `batch/build_bow.py`) broke assertions in `tests/integration/test_build_ocr_lemmas.py` and `tests/integration/test_build_ocr_bow_lang_filter.py` — neither file seemed related to the change under review (reviews were scoped to `tests/rules/`/`batch/tests/`, or to the one integration file most directly tied to the feature, e.g. `test_ocr_lemmas_repository.py` for search-matching work), so neither got run, and both sat broken through two merges before being caught. Any change to shared normalization/matching code (`rules/normalize.py`, `repository/ocr_lemmas.py`, `rules/concept_tagger.py`, `batch/build_bow.py`, or anything else many callers share) needs the **entire** `tests/integration/` root run (`DATABASE_URL=... pytest tests/integration/ -v`) before merging, and that same full-root scope should be handed to any subagent doing a task or final whole-branch review of such a change — not just the file(s) that look directly on-topic.
- **`EnterWorktree` defaults to branching from `origin/<default-branch>`, not local HEAD.** In a sandboxed dev environment with no live GitHub access (`git fetch`/`pull` fail with `Permission denied (publickey)`), the cached `origin/main` ref can be many commits stale, so a new worktree can silently miss recent local-only commits. After creating a worktree, compare `git rev-parse main` against the worktree's `HEAD`; if behind, `git merge --ff-only <target-sha>` inside the worktree before starting work.
- **Windows: `run_in_background: true` on Bash/PowerShell tool calls still enforces a hard ~10 minute timeout that kills the process**, not just stops watching it — confirmed against a real long-running batch job that was silently gone at exactly the 10-minute mark. For anything that needs longer, either scope it down to finish within ~10 minutes (e.g. a `--limit` flag, rerun as needed) or launch it as a truly detached OS-level process (see `docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md`'s recovery step 5 for a Windows `Start-Process` pattern).
- **Some images in the `general` corpus are WebP files saved with a `.jpg` extension**, which Ollama's llava/qwen2.5vl vision backend cannot decode ("Failed to load image or audio file") — the existing `path.lower().endswith("webp")` skip in `build_image_descriptions.py` only checks the extension, not actual content, so these slip through. Rare (a ~2,000-file sample of ~22,000 images found none beyond the two already known) but non-zero. Currently handled behaviorally (the failure-tracking feature marks these permanently failed after one attempt, so they're not retried forever) rather than via content sniffing.
- **`.dockerignore` has no relationship to `.gitignore` — a gitignored (untracked) directory is still sent as Docker build context unless it's separately listed in `.dockerignore`.** `Storage/backups/` (local Postgres dumps, tracked in `Storage/.gitignore`, 33GB on a real dev machine) and `batch/images/` (the meme corpus) were both being transferred on every `docker build -f Dockerfile.backend` despite neither ever being `COPY`'d, making the build context 500MB+ and climbing, sometimes taking 10+ minutes just to transfer. If a Docker build seems to hang at "load build context", run `du -sh */` at the repo root and compare against `.dockerignore` — don't assume "it's gitignored" means "Docker won't see it." See `dependencies.md` for the full incident.