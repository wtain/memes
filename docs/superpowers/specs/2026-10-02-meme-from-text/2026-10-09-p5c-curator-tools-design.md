# P5c: Curator tools (profile editor and coverage view) — Design

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-08-p5-web-ui-design.md (Out of scope and handoff notes: the meme-profile editor US-9 and the coverage view US-8)
Follow-ups: none yet

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Inherited contracts: P1 data model (`meme_profiles`, `meme_profile_values`, `meme_profile_situation_embeddings`, taxonomy), P2 extraction (`batch/build_meme_profiles.py`, `repository/meme_profiles.py`, `batch/utils/meme_profile_selection.py`), P4 embedding client (`retrieval/embed.py`), P5 components (`ChipGroup`, `ValuePicker`, taxonomy endpoint) and user stories US-8 and US-9 (requirements FR-11, FR-12, FR-23).

## Problem

The profiles are the heart of the search, and today nobody can see or correct them. A model profile can be wrong (a meme mis-read as "pride" instead of "smugness") and the only remedy is a database edit. There is also no place to see how complete the enrichment is: how many active memes have no profile, how many were verified by a person, which failed, which a taxonomy change made stale. The data paths exist in the repository (`save_human_profile`, `coverage()`); there is no API and no UI. P5c adds both, and fixes the one place where "human-verified" is not yet treated as the strongest signal.

## Findings that shape the design (verified 2026-10-09)

- `save_human_profile` writes every value with `source: human` and sets `human_verified`; `save_profile` (the batch path) skips a row that is human-verified **at write time**, so a curator save during a running batch cannot be overwritten. When the situation text changes `_write` deletes the stored situation vector, so the existing `build_meme_profile_embeddings` job already finds it as "no embedding row". Axis values are plain SQL rows and take effect in search at once.
- `select_work` marks a human-verified profile stale (and "needs review") when the taxonomy version, the prompt version, the model **or the input fingerprint** differs. A hand-corrected profile does not depend on the prompt, the model or the extraction inputs, so after any prompt tweak or any new description every curated profile would be flagged. US-9 AC2 asks for the flag on a taxonomy bump only.
- The staleness the database cannot see (fingerprint) is computed only by the job; the job records its exact coverage (`stale`, `needs_review`, `failed`, counts) in `BatchRun.stats` at the end of every run. The prompt version is derived from the prompt text in `batch/`, which the backend image does not contain.
- Failures are rows in `image_processing_status` for the profile pipeline (`status_repo.record_failure`, message carries a context); the job retries a failure only when its context changed.
- `MemePage` is a 31-line shell around `MemeDetails`; `/admin/batches` lists runs (`GET /api/admin/batches/runs`); the retrieval package already has `OllamaEmbedClient` and the process builds one for the search pipeline.
- No new table is needed: "pending vector" is derived (profile with a non-blank situation and no vector row, or a vector from another model name), and the last runs are `BatchRun` rows.

## Decisions

### 1. What a human edit stores

- **Save edits** (`PUT`): replaces the whole profile (situation, use-when, every axis list) and goes through `save_human_profile`: all values `source: human`, confidence `1.0`, `human_verified` true. The row's meta is `taxonomy_version` = current, `extractor_model` = `human`, `extractor_prompt_version` = `human`, `inputs_fingerprint` = `human`. Works on an unprofiled meme too (creates the profile).
- **Confirm** (`POST .../confirm`): marks the existing profile `human_verified` and leaves every value, its source and the vector untouched (provenance stays honest: the values were model-made and a person accepted them). It refuses with 409 when there is no profile and with 422 when the profile holds a value key the current taxonomy no longer has (the curator must save an edit instead). Idempotent. Clears a taxonomy "needs review" flag only by also setting the taxonomy version to the current one, which is allowed only after that key check.
- **Reset to model** (`DELETE`): deletes the profile row (values and vector cascade) **and** the image's failure record for the profile pipeline, so the next `build_meme_profiles` run re-extracts it. Works on human-verified profiles too (the explicit undo). Refuses nothing else; deleting a missing profile is a no-op `204`.
- Validation of a save uses the existing taxonomy validator: unknown axes or keys are `422`; at most the per-axis cap the taxonomy defines; situation and use-when trimmed, at most 600 characters; an empty situation is allowed (the meme then has no vector and stays reachable through its axis values).

### 2. Staleness of human-verified profiles (change to P2 behaviour)

`select_work` and `coverage()` treat a human-verified profile as stale **only when its taxonomy version differs from the current one** (then it is reported as needing review and never selected). Model-made profiles keep the four-way rule. A human-verified profile no longer becomes "needs review" because the prompt, the model or the inputs changed. Unit tests pin both rules; the P2 spec gets a one-line pointer to this decision.

### 3. Vector refresh on save

The save request embeds the new situation inline with the Ollama embedding client under a short timeout (`profile_editor.embed_timeout_seconds`, default 8) using the same model name and digest source as `build_meme_profile_embeddings` (the plan extracts the shared embedder construction so both write identical `model_name`/`model_digest`). The database save is committed first; the embedding is a second step. The response says `vector: "fresh" | "pending" | "none"` (`none` when the situation is blank). On any embedding failure (timeout, Ollama down, wrong dimension) the profile stays saved without a vector, the response says `pending`, the UI shows "search vector pending" and the existing job picks it up. An unchanged situation keeps its vector (no call). The user's text is never logged; the situation text is not a secret but the log lines carry only the image id and the outcome.

### 4. Endpoints

New router `Backend/app/api/meme_profiles.py` (service `Backend/app/services/meme_profile_service.py`, repository additions in `repository/meme_profiles.py`, never committing); registered with `prefix="/api"`; the app-level 422 handler of P5b already covers `/api/meme-from-text/*`.

- `GET /api/meme-from-text/profiles/{imageId}` → `{imageId, exists, humanVerified, taxonomyVersion, currentTaxonomyVersion, needsReview, situation, situationConfidence, useWhen, values: {emotion|stance|topic|tone: [{key, confidence, source}]}, vector: "fresh"|"pending"|"none", extractedAt, updatedAt}`; for an unprofiled meme `exists: false` with empty fields. Unknown or non-active image → 404.
- `PUT /api/meme-from-text/profiles/{imageId}` body `{situation, useWhen, values: {axis: [key,...]}}` → the saved profile (same shape) with `vector`.
- `POST /api/meme-from-text/profiles/{imageId}/confirm` → the profile. `DELETE /api/meme-from-text/profiles/{imageId}` → `204`.
- `GET /api/admin/profile-coverage` → live counts `{active, profiled, withoutProfile, humanVerified, needsReview, staleTaxonomy, failed, pendingVector, currentTaxonomyVersion}` plus `lastRuns: {profiles: {...}, embeddings: {...}}` (the latest `BatchRun` of each kind with status, started, finished and its stats, including the exact `stale`/`needs_review`/`failed` of that run, labelled "as of the run").
- `GET /api/admin/profile-coverage/images?category=&cursor=&limit=` where category is `without_profile | needs_review | stale_taxonomy | failed | human_verified | pending_vector`; keyset-paged by image id (`limit` 1..100, default 48) → `{items: [{id, imageUrl, humanVerified}], nextCursor, total}`.
- Staleness beyond the taxonomy (prompt, model, fingerprint) has no per-image list: only the last run knows it, so the page shows the run's exact number with its timestamp and says so.
- Responses are schema-defined in `shared/schemas/` (`memeprofile`, `memeprofilesaverequest`, `profilecoverage`, `profilecoverageimages`) with regenerated Python, TypeScript and Kotlin types and `backend_api.md` updated. No authentication, like the rest of the API (documented).

### 5. UI

- **Meme page curator panel** (a new component under `src/components/curator/`, mounted by `MemePage` below `MemeDetails`): shows the profile with each value's source and confidence; edit mode uses the P5 `ChipGroup`/`ValuePicker` with labels from the taxonomy endpoint, a situation text area and a use-when text area; buttons **Save**, **Confirm as correct** (shown for a model profile or a needs-review one), **Reset to model** (with a confirm step); a "needs review" banner when the taxonomy changed; after a save "Saved" plus "search vector pending" when the vector is pending; unsaved-changes guard on navigation; all states announced in always-mounted status regions, focus kept, 44 px targets, StrictMode and React Compiler safe like P5.
- **`/admin/profiles`** (a new page, linked from the admin area): count cards that double as the category filter (selected state via `aria-pressed`), the "as of last run" numbers with their timestamps, the last runs of the two jobs, and a paged thumbnail grid of the selected category (`Show more`), every card linking to its meme page. The frontend never recomputes counts.

### 6. Race and consistency rules

- A curator save during a running batch is safe (decision above). A reset during a running batch is safe: the job re-reads the row at write time and writes a fresh model profile. Two curators editing at once: last write wins (single-owner tool, documented).
- The save is one transaction for the profile and its values; the vector write is a second transaction, so a crash between them leaves a saved profile without a vector, which is exactly the `pending` state.

## Out of scope

Authentication and per-curator audit, an edit history or undo of a reset beyond editing again, bulk edits, re-running vision extraction for one image, editing the situation vector directly, re-ranking from edits, per-image lists for non-taxonomy staleness, and anything on `MemeDetails` itself beyond mounting the panel.

## Testing

- Repository integration tests (test database): human save vs a simulated later batch write (batch skipped), situation change drops the vector and an unchanged one keeps it, confirm leaves values and sources untouched, reset deletes cascade and the failure row, coverage category queries and counts agree with each other, keyset paging.
- Selection and coverage unit tests: a human-verified profile is stale only on a taxonomy difference, model profiles keep the four-way rule.
- Service tests with a fake embedder: fresh, timeout, unavailable, wrong dimension, blank situation, unchanged situation (no call), validation errors, confirm refusals, taxonomy-removed key.
- Router tests: status codes (200, 204, 404, 409, 422), no text in error bodies, response shapes against the schemas.
- Frontend: component tests for the panel (modes, save, confirm, reset, banner, vector notice, unsaved guard, announcements, focus) and the coverage page (cards, filter, paging, last runs, as-of labels), `MemePage` mounting.
- Live check on the seeded test database (as in P5b), including a save while a batch-like write races, then the metal environment after the migration-free rollout.

## Acceptance criteria

1. A curator edits a meme's axis values, situation and use-when on the meme page, saves, and the edit is stored as human-verified (all values `human`) and used by search at once (axis path immediately, situation vector inline or reported `pending`).
2. A human-verified profile is never overwritten by the batch (also during a run) and is flagged for review only after a taxonomy version change (decision 2).
3. Confirm and Reset to model work as specified, including the refusals.
4. `/admin/profile-coverage` and its image list show consistent live counts, the last runs and the as-of numbers; every category list pages and links to the meme page.
5. No new table or migration; schemas, three type trees, `backend_api.md`, accessibility basics as in P5 and the gates (frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, `vite build`; backend roots; the whole `tests/integration` root because `repository/meme_profiles.py` is shared with the batch jobs) pass.
6. Addendum with the live check, tracker, `CLAUDE.md` entry and the review report exist.

## Risks

- **Race with a running batch:** the batch's write-time guard covers a save; covered by a repository test and the live check.
- **Embedding drift:** an inline vector with a different digest than the job would write makes the job re-embed it. Mitigation: one shared embedder construction (plan task).
- **Changed staleness rule:** decision 2 changes the P2 "needs review" semantics. It is intentional, documented, and affects only human-verified rows.
- **Open API:** anyone on the network can edit profiles. Same trust model as the rest of the API; noted in `backend_api.md`.

## Rollout

No migration. Restart the backends to expose the new routes. Until the `metal` profile backlog is finished, the coverage numbers show the in-progress state, which is correct. Curators can start on `general` (1,640 profiles) before `metal` completes.

## Plan outline (for writing-plans)

1. Repository: confirm, reset (with failure-record removal), coverage category queries and counts, keyset lists, profile read model; selection/coverage staleness rule change with tests.
2. Shared embedder construction and the profile service (validation, inline embed, vector status) with tests.
3. Router, schemas, regenerated types, `backend_api.md`, app wiring.
4. Frontend API layer and the curator panel with tests.
5. Coverage page, route and admin link with tests; `MemePage` mounting.
6. Controller steps: seeded-database live check including the race, docs addendum, tracker, `CLAUDE.md`, review report.
