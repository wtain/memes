# Ollama descriptions and notes in search, similarity and tagging — Design

Status: done
Plan: docs/superpowers/plans/2026-10-01-description-notes-search-similarity-tagging.md
Originates from: board/description-tagging-tracker.md tasks 151 and 152; docs/superpowers/specs/drafts/2026-10-01-description-tagging-audit-and-pipeline-notes.md
Related: 2026-08-20-description-notes-design.md (notes, done), 2026-07-21-smart-search-design.md, 2026-07-19-description-feedback-design.md, docs/data-flow.md
Follow-ups: task 149 (rejected feedback excluded everywhere), 148 (`--status pending`), 153 (pipeline orchestration), docs/superpowers/specs/2026-10-01-search-result-ranking-design.md (task 156, search ranking)

## Motivation

Two kinds of text describe an image beyond its OCR: LLM descriptions (Ollama, one per image and prompt) and a human note (one per
image). Today they take different, inconsistent paths:

| Consumer | Ollama description | Human note |
|---|---|---|
| Smart search | not searchable (only reachable through tags) | yes, via `description_note_lemmas` |
| Similarity | `source=description` (same-`prompt_key` pairs only) | `source=description_note` (notes only) |
| Tagging | `build_tags_from_descriptions` (source `Ollama`, done in task 150) | none |

Decisions already taken (user, 2026-10-01): notes join and never override descriptions; rejected description feedback is excluded from
search, similarity and tagging; description tagging uses `ConceptTagger`. This spec makes descriptions and notes first-class,
parallel text signals across all three consumers. It does not add any override or suppression between them.

## Findings that shape the design

- **Search has no ranking.** `matching_image_ids` (`repository/ocr_lemmas.py`) returns a set of image ids and results are ordered by
  `created_at desc`. Per query token, candidates are the union of sources; across tokens they are intersected. So "do notes rank above
  descriptions" has no meaning today, and this design does not add ranking.
- **Notes are indexed unstemmed** (no language tag, `language=None`), so `_stem_lemma_ids` skips them. Ollama descriptions are always
  English, so a description index can be stemmed at index time like OCR `en` rows, and the stemming fallback works for it.
- **`image_description_feedback.approved` is a non-null boolean; no row means unreviewed.** "Rejected" is `approved IS FALSE`. Unreviewed
  descriptions are included.
- **The backend cannot run `ConceptTagger` faithfully**: `Backend/requirements-backend.txt` has `pymorphy3` and `PyYAML` but not `rapidfuzz`,
  so fuzzy concept entries would be silently skipped. It also has no SBERT model. Only lemma indexing (`normalize`, already used for
  query normalization) is safe to run in the API process.
- **A note edit today changes nothing** until three manual batches run; the note's stored embedding stays stale in the meantime.

## Non-goals

- Ranking or scoring of search hits.
- Overriding or suppressing one text source with another.
- The rest of task 149: excluding rejected descriptions from description tagging, description embeddings and the existing
  `source=description` mode. This spec only defines the shared "rejected" predicate (below) and applies it to the new consumers.
- `--status pending` support for the new jobs (task 148) and orchestration (task 153).
- Inline tagging or embedding inside the API process.

## Shared definition: rejected description

A description is **rejected** when a feedback row exists with `approved = FALSE`. One helper in `repository/` (a SQL predicate, usable as an
`outerjoin` + `where` fragment or a `NOT EXISTS`) is the only place this is expressed, so task 149 reuses it.

## 1. Search: description lemma index

### Data model

New table `description_lemmas` (model in `Storage/models.py`, one Alembic migration):

```
image_description_id  UUID, FK -> image_descriptions.id, ON DELETE CASCADE   \_ composite PK
lemma                 String                                                  /
phonetic_code         String, nullable   (schema symmetry only; never queried, as for notes)
created_at            DateTime, server_default now()
```

Indexes: `ix_description_lemmas_lemma` and a trigram GIN index `ix_description_lemmas_lemma_trgm` (`gin_trgm_ops`), mirroring
`description_note_lemmas`. The table is keyed by description, not image, so feedback exclusion can be applied at query time.

### Batch job `build_description_lemmas`

- Selects descriptions that have no `description_lemmas` rows (`NOT EXISTS`). Descriptions are insert-only (re-describing deletes and
  re-inserts), so no staleness marker is needed. A description that normalizes to zero lemmas is re-evaluated each run, which is cheap
  and harmless.
- Indexes with `normalize(text, morph, min_length=settings.BOW.MIN_WORD_LENGTH, language="en", keep_digit_tokens=True)`, so English words
  are stemmed exactly as `en`-tagged OCR rows are.
- `main(trigger, run_id)` with `tracked_run` / `finish_existing_run`, registered in `environments/batch_registry.yaml`, manual trigger
  only. Active images only is not enforced at index time (matches `ocr_lemmas`); the status filter is applied at query time as for the
  other sources.
- `repository/description_lemmas.py` follows `repository/description_note_lemmas.py` (repository + saver, no commit inside the repository).

### Search integration (`repository/ocr_lemmas.py`)

- `_exact_lemma_ids`: add `description_subq` to the union: `DescriptionLemma.lemma == lemma`, joined to `ImageDescription` for the
  image id, with rejected descriptions excluded.
- `_fuzzy_lemma_ids`: same, using the `%` operator and the existing `SET LOCAL` threshold pattern.
- `_stem_lemma_ids`: also query `DescriptionLemma` for `lemma == stem`, rejected excluded (description lemmas are pre-stemmed). Its
  docstring explaining why notes are excluded stays accurate and gains a line on descriptions.
- `_phonetic_lemma_ids`: not extended (same rationale as tags and notes).
- Matching semantics are unchanged: union across sources within a token, intersection across tokens.

## 2. Similarity: combined mode

New `GET /api/images/{id}/similar?source=description_all` (`Literal` gains `"description_all"`).

- **Vector set of an image:** its embeddings from non-rejected Ollama descriptions (`image_description_embeddings`) plus its note
  embedding (`description_note_embeddings`). Both are bge-large-en-v1.5, 1024-dim, so they share one space.
- **Distance between two images:** the minimum cosine distance over all pairs (source vector, candidate vector). There is no
  `prompt_key` restriction, so a note can be compared with an Ollama description and the reverse.
- **Candidates:** active images other than the source, with at least one vector. Same response shape and `flagged` handling as the
  existing modes (`cosineDistance` is the minimum distance).
- **404** when the source image has no vector at all (`"No description or note embedding found for this image"`).
- `description` and `description_note` are unchanged by this task.
- **Performance:** the first implementation is a single query that unions candidate vectors from both tables and takes `min` per image,
  like `get_similar_by_description`. The plan measures it on a real-size corpus. If too slow, fall back to one HNSW-ordered lookup per
  source vector against each embedding table, over-fetched and merged in the service. That choice is an implementation detail and does not
  change the API.
- `shared/schemas/` and generated types are untouched unless the response schema changes (it does not). `backend_api.md` documents the
  new `source` value.

## 3. Tagging from notes

New job `build_tags_from_notes`:

- `ConceptTagger` (same profile and data dir as OCR/description tagging), `engine.tag(text, language=None)` since notes have no language
  tag. Source `"Note"`.
- Incremental selection: notes with no `Note` tag, or whose `updated_at` is later than the image's newest `Note` tag. Selected images'
  `Note` tags are deleted and rewritten in one session (same shape as `build_tags_from_descriptions`, task 150). A full run deletes only
  `Note` tags. A note producing no tags is re-evaluated each run (harmless).
- `main(trigger, run_id, incremental)`, `tracked_run` / `finish_existing_run`, registered in `batch_registry.yaml`, manual trigger only.
- **Delete path:** `clear_description_note` (used by `DELETE` and by `PUT` with empty text) also deletes that image's `Note` tags in the same
  transaction, alongside the lemma rows it already removes. A cleared note must not keep producing tags.
- Smart search already matches tag values, so `Note` tags become searchable through the existing tag source with no search change.

## 4. Refresh behavior on note edit

- `PUT /api/images/{id}/description-note` rewrites that note's `description_note_lemmas` inside the request transaction, using the same
  `normalize(..., language=None, keep_digit_tokens=True)` call as the batch, and sets `lemmas_built_at = updated_at`. The batch's staleness
  predicate (`lemmas_built_at IS NULL OR < updated_at`) then skips the note; the batch remains as a catch-up for notes written before this
  change. The normalization logic is shared (one function used by both) rather than duplicated.
- Tags (`build_tags_from_notes`) and the note embedding (`build_description_note_embeddings`) remain batch-only. Until they run, an edited
  note is searchable by its words but its tags and its similarity vector are stale. Documented in `docs/data-flow.md`.
- A combined "refresh notes" chain is out of scope here (task 153).

## Interactions

- **Feedback (task 149):** rejected descriptions are excluded from the new search source and the new similarity mode via the shared
  predicate. Notes have no feedback and are always included. Task 149 applies the same predicate to description tagging, description
  embedding generation and `source=description`.
- **Task 150:** `build_tags_from_descriptions` is unchanged.
- **Duplicates:** unchanged; they never use descriptions or notes.

## Testing

- `tests/integration/` (whole root, since `repository/ocr_lemmas.py` is shared): description-lemma exact, fuzzy and stem matching; a rejected
  description does not match, an unreviewed or approved one does; an image with only a description (no OCR, no tags, no note) is found;
  an image matched by OCR only is unaffected (regression); token intersection across sources (one token from OCR, one from a description).
- Repository tests for the shared rejected predicate, the `description_lemmas` saver/selection, and the combined similarity query
  (note-vs-description pair, rejected excluded, source with no vectors).
- `Backend/tests/`: `source=description_all` (200 shape, 404), `PUT` writes lemmas and sets `lemmas_built_at`, `PUT`-empty and `DELETE` remove
  note lemmas and `Note` tags.
- `batch/tests/`: `build_description_lemmas` and `build_tags_from_notes` mains and processing logic (mock style of
  `test_build_tags_from_descriptions.py`).
- `tests/rules/` and `batch/tests/` run in full as separate commands, as does `Backend/tests/` (never combined, per CLAUDE.md).

## Migration and rollout

1. Alembic migration for `description_lemmas` (autogenerate from `Storage/`, review the generated trigram index).
2. Per environment (metal, general; IT has no tagging vocabulary): run `build_description_lemmas` once to backfill, then
   `build_tags_from_notes` once. Both are idempotent.
3. No backfill is needed for the combined similarity mode (it reads existing embedding tables).
4. Docs updated in the same change: `backend_api.md`, `docs/data-flow.md` (signal/consumer matrix, latency note), the `CLAUDE.md` batch list
   and `ARCHITECTURE.md` pipeline section.

## Implementation split (for the plan)

Independent enough to parallelize: (A) description lemma table, job, repository and search integration; (B) combined similarity mode;
(C) note tagging job and delete-path cleanup, plus inline lemmas on `PUT`. A depends on the shared rejected predicate, which is built first.
