# Human Description Notes on Images — Design

Status: done
Plan: docs/superpowers/plans/2026-08-20-description-notes.md

## Motivation

Images today carry OCR text, CLIP embeddings, rule-derived tags, and optional LLM-generated
("ollama") descriptions — but nothing a human can freely write about an image. Some images have
little or no OCR text (pure-image memes, non-text templates) and are effectively unsearchable by
the main search box today. A short human-written note per image — searchable the same way OCR text
is, plus embeddable for similarity search — closes that gap and gives a manual override/annotation
path independent of what the automated pipeline extracted.

This is distinct from `ImageDescription`/`image_descriptions` (the ollama-generated, per-prompt
descriptions) — to avoid name collision, the new concept is called a **description note**
throughout this spec: `DescriptionNote` / `description_notes`.

## Goals

- One free-text note per image, editable in place (no version history).
- Notes participate in the main `q` search box exactly the way OCR text does — including as a
  fallback path for images with no OCR text at all.
- Notes get their own embeddings (SBERT, matching the existing `ImageDescriptionEmbedding`
  convention) for similarity search, computed by a dedicated batch job.
- Notes get their own lemma index (mirroring `OCRLemma`) for text search, computed by a dedicated
  batch job.
- Both batch jobs are admin-triggerable (manual only, like `rebuild_duplicates`/`clusterize`), not
  scheduled.
- Everyone can read and write the note today (no auth exists yet) — but the write endpoints are
  explicitly logged in `docs/security/admin-permissions-todo.md` as needing a guard once a
  permission model exists.

## Non-goals

- Building an authorization system. Out of scope entirely; only the TODO-doc entry is in scope.
- Edit history / audit trail. Explicitly current-value-only, per the `ImageExtras.remarks`
  precedent, not the `DuplicateDecision` append-only precedent.
- `updated_by` / editor identity. Deferred until real auth exists.
- Cross-modal (CLIP-space) note embeddings. Notes use the same SBERT model and 1024-dim space as
  `ImageDescriptionEmbedding`, not CLIP.
- Phonetic-erratives fallback for note lemmas (see Search integration below) — deliberately
  excluded, same rationale as `ImageTag` already being excluded from that fallback.

## Data model

Three new tables in `Storage/models.py`, added via `alembic revision --autogenerate`.

### `DescriptionNote` (table `description_notes`)

```
image_id            UUID, PK, FK -> images.id, ON DELETE CASCADE
text                Text, not null
updated_at          DateTime, not null, set on every write
lemmas_built_at      DateTime, nullable
embedding_built_at   DateTime, nullable
```

One row per image, 1:1, upserted in place — mirrors `ImageExtras`' shape, not
`ImageDescription`'s (image, prompt_key) shape, since there is only ever one note per image.

`lemmas_built_at` / `embedding_built_at` are the staleness markers the two batch jobs use (see
Batch jobs below). This is a deliberate departure from the `ImageDescription` precedent: ollama
descriptions are never edited after creation, so `build_image_description_embeddings.py` only
needs to ask "is there an embedding row at all?" A description note *can* be edited repeatedly by
anyone at any time, so "row exists" is not sufficient — the batch jobs need to detect "note changed
since we last indexed/embedded it," which these two timestamps provide.

Add `Image.description_note` relationship (`uselist=False`, `cascade="all, delete-orphan"`),
consistent with `Image.image_extras`.

### `DescriptionNoteEmbedding` (table `description_note_embeddings`)

```
description_note_id  UUID, PK, FK -> description_notes.image_id, ON DELETE CASCADE
embedding             Vector(TEXT_EMBEDDING_DIM)   -- 1024, same constant as ImageDescriptionEmbedding
```

Mirrors `ImageDescriptionEmbedding` exactly (PK is the FK, 1:1, HNSW cosine index named
`ix_description_note_embeddings_embedding`). Only created for notes with non-empty `text`.

### `DescriptionNoteLemma` (table `description_note_lemmas`)

```
image_id       UUID, FK -> images.id, ON DELETE CASCADE  \_ composite PK
lemma          Text                                       /
phonetic_code  Text, nullable
```

Mirrors `OCRLemma` exactly, including the trigram GIN index on `lemma` and the phonetic-code index
(the index exists for schema symmetry with `OCRLemma`; see Search integration for why the phonetic
*fallback query* itself does not use description-note lemmas).

## Backend API

CRUD on the note itself is real-time — the text is a direct user edit, not a batch artifact. Only
the derived search artifacts (embedding, lemmas) are batch-computed, same latency model OCR already
has (text extracted instantly, lemma index built by a separate batch step).

- `PUT /api/images/{id}/description-note` — body `{ "text": str }`. Upserts `description_notes`,
  sets `updated_at = now()`. If `text` is empty/whitespace-only, treat as a clear: delete the row
  (see below) rather than storing an empty string.
- `DELETE /api/images/{id}/description-note` — deletes the note row, and synchronously cleans up
  both `description_note_embeddings` and `description_note_lemmas` rows in the same transaction.
  `description_note_embeddings`'s FK is to `description_notes.image_id`, so it genuinely cascades
  via `ON DELETE CASCADE`. `description_note_lemmas`'s FK is to `images.id` (mirroring `OCRLemma`'s
  shape), so it does *not* cascade when only the note is cleared — the repository deletes those rows
  explicitly instead. This must be synchronous either way, not wait for the next batch run —
  otherwise a cleared note keeps producing stale search/similarity hits until someone thinks to
  re-run the batch jobs.
- Note text is included in the existing single-image response (the same endpoint that already
  returns OCR text, tags, etc.) rather than requiring a separate `GET`. No auth on read (per your
  answer, everyone can already see it).
- Extend `GET /api/images/{id}/similar?source=` with `source=description_note`, mirroring the
  existing `source=description` path: cosine distance over `DescriptionNoteEmbedding` instead of
  `ImageDescriptionEmbedding`. No `prompt_key` join needed (notes have none).

Update `backend_api.md` for all of the above, per the project's API-contract convention.

### `docs/security/admin-permissions-todo.md`

Append to the "Endpoints needing permission controls once a model exists" list:

```
- `PUT /api/images/{id}/description-note` — anyone can currently overwrite any image's note.
- `DELETE /api/images/{id}/description-note` — anyone can currently clear any image's note.
```

## Search integration

Confirmed by reading `repository/ocr_lemmas.py`: `matching_image_ids` normalizes the query into
per-token lemmas, and for each token builds a candidate `image_id` set via `_exact_lemma_ids`
(`UNION` of an `OCRLemma` subquery and an `ImageTag` subquery — a set union of independent
`SELECT`s, not a SQL `JOIN`), falling back to `_stem_lemma_ids`/`_fuzzy_lemma_ids`/
`_phonetic_lemma_ids` when the exact union is empty. Candidate sets are then **intersected across
tokens** (every token must match somewhere), but **unioned across sources within one token**.

Because it's already source-union / token-intersection, adding `DescriptionNoteLemma` as a third
unioned source gives exactly the desired fallback behavior with no special-casing: an image with
zero OCR text and zero tags, but a note whose lemmas cover every query token, still matches — since
each token only needs to hit *one* source, not all three, and a missing source for a given image
simply contributes nothing to that token's union rather than excluding the image.

Concretely:

- `_exact_lemma_ids`: extend `union(ocr_subq, tag_subq)` to `union(ocr_subq, tag_subq, note_subq)`,
  `note_subq = select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma == lemma)`.
- `_fuzzy_lemma_ids`: same extension, trigram `%` operator against `DescriptionNoteLemma.lemma`,
  using the same GIN index / `SET LOCAL` threshold pattern already in place.
- `_stem_lemma_ids` (English stemming fallback): extend to also query `DescriptionNoteLemma`,
  same reasoning as OCR (stems are stored at index time by the note-lemma batch job, same as OCR).
- `_phonetic_lemma_ids`: **not** extended. This fallback exists for OCR-specific noise (deliberate
  misspellings/erratives in low-quality OCR text) and is already excluded for `ImageTag` on the
  grounds that a controlled/deliberate text source doesn't need errative matching. A human-typed
  note is the same kind of deliberate text as a tag, so the same exclusion applies.

Language handling (per-lemma stemmed/normalized form, English/Spanish/Russian detection) reuses
`rules/normalize.py` exactly as `build_ocr_lemmas.py` / `batch/utils/ocr_lemmas.py` already do —
no new normalization logic.

## Batch jobs

Two new scripts under `batch/`, following existing conventions (async, repository pattern,
`AsyncSessionLocal`, `ProgressTracker`, incremental-by-default, commit every
`settings.GENERAL.BATCH_SIZE` rows):

### `batch/build_description_note_lemmas.py`

Mirrors `build_ocr_lemmas.py`. Selects notes needing (re)indexing:

```sql
lemmas_built_at IS NULL OR lemmas_built_at < updated_at
```

(no `text != ''` filter needed — the API layer's delete-on-empty guarantees any existing row has
non-empty text, per the Data model section above.)

For each, deletes any existing `description_note_lemmas` rows for that `image_id` (in case of a
re-index after edit), builds the new lemma set via `rules/normalize.py`, inserts, and sets
`description_notes.lemmas_built_at = now()`. `--incremental` is effectively always-on given the
staleness predicate above (there's no meaningful "full rebuild" distinction the way OCR has, since
there's exactly one note per image); no `--full` flag needed.

### `batch/build_description_note_embeddings.py`

Near-identical copy of `batch/build_image_description_embeddings.py`: same `SbertModel`
(`bge-large-en-v1.5`), same commit-interval/`ProgressTracker` pattern. Selects notes needing
embedding via the same staleness predicate as above but against `embedding_built_at`; deletes any
existing `description_note_embeddings` row for the note (re-embed on edit) before inserting the
new one; sets `description_notes.embedding_built_at = now()`.

Both follow the `main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None` signature
convention (`tracked_run`/`finish_existing_run`, see `build_ocr_lemmas.py:78`) so they plug into
the existing admin-batch-run machinery unchanged.

### Registration (admin-triggerable, manual only — not scheduled)

Add to `environments/batch_registry.yaml`:

```yaml
build_description_note_lemmas:
  module: batch.build_description_note_lemmas
  kind: build_description_note_lemmas
build_description_note_embeddings:
  module: batch.build_description_note_embeddings
  kind: build_description_note_embeddings
```

This alone makes both triggerable from `/admin/batches` — `scheduler.jobs` (the scheduled-jobs
config) is untouched, matching `rebuild_duplicates`/`clusterize`'s "manual-trigger only" precedent
described in CLAUDE.md.

### CLAUDE.md batch pipeline list

Add both jobs to the pipeline list in CLAUDE.md, following the existing entry style (short
one-line description of what each does, admin-triggerable/manual-only note), placed near
`build_tags_from_ocr`/`build_ocr_lemmas` since they're the closest analog.

## Frontend

- Description note becomes a visible, editable text field on the main image detail view (not
  admin-panel-only) — a textarea with a save action calling `PUT`, and a clear action calling
  `DELETE`. No gating logic (matches the "everyone can edit for now" decision) — add a one-line
  code comment pointing at `docs/security/admin-permissions-todo.md` so a future auth pass finds
  it.
- No new admin page needed — this isn't routed through `AdminBatchesPage.tsx`. It's ordinary
  image-detail-view functionality, same tier as OCR text display.
- The two new batch jobs are surfaced in the existing `/admin` batch-trigger list automatically
  once registered in `batch_registry.yaml` (no separate frontend work needed beyond what
  `AdminBatchesPage.tsx` already does generically for any registered job).
- Regenerate/verify TypeScript types if the shared image response schema
  (`shared/schemas/`) changes to include the note field.

## Testing

- `Backend/tests/` — new tests for the `PUT`/`DELETE` endpoints (upsert, clear-on-empty-string,
  cascade-delete of embedding/lemma rows on delete) and the `source=description_note` similar-images
  path.
- `tests/integration/` (full root, per CLAUDE.md's gotcha about shared-code changes) — since this
  touches `repository/ocr_lemmas.py`'s shared `matching_image_ids`, run the entire
  `tests/integration/` root, not just a note-specific file, before merging. Add cases: an image
  with only a note (no OCR, no tags) is found by a query matching note text; an image with OCR text
  only still matches unaffected (regression guard on the union extension).
- `batch/tests/` and/or `tests/rules/` — config-loading / normalization tests for the two new batch
  scripts if they introduce new config keys (none anticipated; they reuse existing
  `settings.GENERAL.BATCH_SIZE`).
- Manual smoke test per CLAUDE.md's "before committing backend changes": confirm server starts,
  hit the new endpoints, verify `/api/diagnostics/health` and `/api/images?limit=1` still work.

## Migration

Single Alembic migration adding all three tables (`description_notes`,
`description_note_embeddings`, `description_note_lemmas`) plus their indexes, generated via
`alembic revision --autogenerate -m "add description notes"` from `Storage/`. No backfill needed —
starts empty, notes are added going forward.

## Consequences / things to know

- Editing a note is instant, but it won't affect search results or similarity search until the two
  batch jobs are next run — identical latency behavior to OCR (text extraction is instant, lemma
  indexing is a separate batch step). Worth remembering so a "why doesn't my new note show up in
  search yet" report isn't mistaken for a bug.
- Until a permission model exists, the note is fully open read/write to anyone who can reach the
  backend — same accepted-gap status as everything else in `admin-permissions-todo.md`.
- `source=description_note` (the similar-images embedding-similarity mode) has no frontend entry
  point today — it's reachable only via the API directly. The main UI only exposes the note-editing
  textarea, not a third similarity-mode toggle button. This is intentional scope (the spec's Frontend
  section never required a UI for it), not dead code — a future spec can add the toggle if wanted.
