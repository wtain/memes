# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Semantic search engine for memes. Images are enriched offline (OCR, CLIP embeddings, LLM descriptions, tags) via batch jobs; the FastAPI backend serves pre-computed results; the React frontend provides search and browse UI. Three independent environments run in parallel: **metal** (port 8081), **general** (8082), **IT** (8083), each with its own database and config.

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
environments/.env.*       ← per-environment config (DATABASE_URL, BASE_PATH, RULES_FILE, …)
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

```
extract_text_from_memes    → registers images + EasyOCR (EN/ES/RU)
build_image_embeddings     → CLIP 512-dim vectors
rebuild_duplicates         → near-duplicate clusters  [drops & recreates table — non-idempotent]
clusterize                 → optimize cluster index

build_tags_from_ocr        → rule-based tags from OCR text
build_image_descriptions   → Ollama LLM descriptions (optional)
build_tags_from_descriptions → rule-based tags from descriptions
build_concept_embeddings   → concept CLIP embeddings + mappings

# Maintenance (run as needed)
detect_file_duplicates     deduplicate_ocr_texts     move_flagged     unregister_deleted_images
detect_entities_and_tag    tag_images_from_concepts  build_bow

# Concept discovery for the new rules engine (see Rules engine below)
build_lemma_clusters       → draft_concepts_from_clusters
```

Most jobs are idempotent (clear and rebuild). Exception: `rebuild_duplicates` drops its table each run.

**Full re-run (re-process all images):**
Do NOT clear the `ocr_texts` table. Instead:
1. `python -m batch.reset_ocr_status --all` — resets processing status without touching OCR data.
2. `python -m batch.extract_text_from_memes` — re-processes all images, overwriting OCR per image.
This ensures OCR data is preserved if the run is interrupted.

### Rules engine

Two implementations coexist:

- **Current** (`rules/engine.py` + `batch/data/rules.*.json`): regex/substring matching → tags. Used by `build_tags_from_ocr` and `build_tags_from_descriptions`.
- **New design** (`rules/concept_tagger.py`, `batch/data/tagging/`): concept voting with YAML rule files. See `batch/rules_engine.md` for the full design. Not yet wired into the main pipeline.

`rules/normalize.py` is shared by both engines and `build_bow.py` — use it for all text normalization to keep behavior consistent.

**Concept discovery** (drafting new entries for the new design): `build_lemma_clusters` embeds `build_bow`'s unmatched lemmas (sbert) and clusters them per-language (HDBSCAN; `CLUSTER_SELECTION_METHOD=leaf` avoids one oversized "catch-all" cluster that the default `eom` tends to produce), optionally naming each cluster via Ollama. `draft_concepts_from_clusters` then takes the top N clusters and appends draft entries to `concepts.<env>.yaml` + `tags.<env>.yaml` for human review. Both are chained together by the `/draft-lemma-concepts` Claude Code command (`.claude/commands/draft-lemma-concepts.md`), which also commits the raw draft before review and again after.

`BOW_IGNORE_FILE` (`batch/data/ignore-words.<env>.json`) edits only take effect after the next `build_bow` run — `build_lemma_clusters` reads `build_bow`'s output (`bow.unmatched.<env>.json`), never the ignore file itself, so a stale unmatched file will keep surfacing already-ignored words until `build_bow` is rerun.

### Frontend

Vite reads env files from `../../environments/` (relative to `Frontend/memes-frontend/`). The `VITE_BACKEND_API_URL` var must be set per environment. TypeScript types for API responses are generated from `shared/schemas/` — do not hand-write them.

### Android

`imageUrl` from the API is a relative path (`/api/images/{id}`). Coil requires absolute URLs; callers prepend `http://localhost` and the OkHttp interceptor in `NetworkModule` rewrites the host at request time. All navigation is wired through `NavGraph.kt`; screens receive callbacks and never touch `NavController` directly.

### Python environments

Two venvs exist: `.venv` (Python 3.13) and `.venv311` (Python 3.11). The project targets **Python 3.11** — use `.venv311` for batch jobs and backend. Four requirements files:

| File | Use |
|------|-----|
| `requirements-backend.txt` | FastAPI server only (Docker, CI) |
| `requirements.txt` | Full ML/batch stack, CPU PyTorch — no dev tools |
| `requirements-cuda.txt` | Overrides for NVIDIA GPU — includes `--extra-index-url` for PyTorch |
| `requirements-dev.txt` | Dev tools: `autoflake`, `black`, `isort`, `pytest*`, `coverage` |

## Specs and implementation workflow

### Where specs live

All design specs are stored in `docs/superpowers/specs/`.

**Naming convention:** `YYYY-MM-DD-<short-kebab-summary>.md`  
Examples: `2026-06-29-ocr-safe-full-mode.md`, `2026-07-01-upload-endpoint.md`

### Implementing a spec

1. Read `docs/superpowers/specs/<spec-file>.md` in full before touching any code.
2. Start implementation; spin subagents when tasks are independent and can run in parallel.
3. When implementation is complete, review the changes:
   - Logic correctness against the spec
   - Code quality: duplication, readability, reusability, structure, test coverage
   - Run tests (`pytest` for backend, `vitest run` for frontend)
   - Confirm every requirement in the spec is addressed
4. Save the review report in `docs/superpowers/reviews/` named after the spec:  
   `YYYY-MM-DD-<spec-summary>-review.md`
5. If the review surfaces action points, spin another agent to fix them.
6. Do only **one** review iteration. Post a final summary here:
   - What was fixed
   - What was not fixed but explained (acceptable deviation)
   - What was intentionally ignored and why

## Key invariants

- Env files live in `environments/` (not `Storage/`).
- `backend_api.md` must stay in sync with the actual routers.
- Windows dev: `WATCHFILES_FORCE_POLLING=1` is required for uvicorn `--reload` to work.
- AGP 8.5.2 requires Java 11+; set `JAVA_HOME` to Android Studio JBR before Gradle commands (do not commit to `gradle.properties`).