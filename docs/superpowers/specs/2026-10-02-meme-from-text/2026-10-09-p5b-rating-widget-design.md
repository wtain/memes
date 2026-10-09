# P5b: Rating widget — Design

Status: planned
Plan: docs/superpowers/plans/2026-10-09-p5b-rating-widget.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-08-p5-web-ui-design.md (Out of scope: the rating widget, US-5)
Follow-ups: none yet (P5c curator tools is a separate spec)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Inherited contracts: the P5 page and handoff notes (`2026-10-08-p5-web-ui-design.md`, addendum), the P4 endpoint (`POST /api/meme-from-text/search`), the P6 gold set and its import format (`evaluation/gold/`), NFR-2 (privacy: query text goes only to the local Ollama and the local database, and is stored only if search history already stores it).

## Problem

The pipeline is measured on a frozen gold set of 60 queries, but real use produces far more signal: every time the owner pastes a message and looks at the results, they know which memes fit. Nothing records that. US-5 (priority "could") asks for a one-click rating per result, stored with the query, the parse version, the meme id and the rank, never used to change ranking in the first release, and exportable into the gold-set format. This spec covers that rating widget, its storage and the export. It does not change retrieval.

## Findings that shape the design (verified 2026-10-09)

- The message endpoint stores nothing today (no text, no log): P4 promised a stateless, read-only search. `search_history` stores a plain `query` column for the keyword search with no retention policy; it has the wrong shape here (one row per search, no per-result grade).
- Ratings are therefore the first place a pasted message would be stored. Pasted messages are often someone else's words, so the design stores the text only when a rating exists, keeps it clearable, and logs nothing.
- The gold set is graded 0 to 2 (not a fit / acceptable / good fit) with a labelling rubric; `judgments.json` rows are `{queryId, imageId, grade, rater, date, round}` and `import-judgments` accepts per-language browser exports `{round, language, judgments}`. Gold queries are hand-reviewed with an expected profile, and the gold set is frozen and fingerprint-checked, so new queries cannot be added automatically.
- The search response `meta` has no query identifier; the browser should not hash text itself.
- A result's rank on screen is its position in the duplicate-collapsed list, not the raw candidate rank.

## Decisions

### 1. Storage: a dedicated table

New table `meme_text_ratings` (ORM model in `Storage/models.py`, one Alembic migration, additive and reversible):

| column | meaning |
|---|---|
| `id` | UUID primary key |
| `query_hash` | sha256 hex of the normalised message (the parser's `normalise_text`, first `parse.max_input_chars` characters) |
| `query_text` | the normalised message text, nullable (cleared by "forget the text") |
| `image_id` | FK to `images`, `ON DELETE CASCADE` |
| `grade` | 0, 1 or 2 (database check constraint) |
| `rank` | position in the list shown, 1-based |
| `reading` | `parsed` or `edited` |
| `parser_version`, `taxonomy_version` | from the response the rating was made on |
| `degraded`, `fallback` | whether that response was degraded and why |
| `language` | detected language |
| `rated_at`, `updated_at` | timestamps |

Unique on (`query_hash`, `image_id`): rating the same meme again for the same message updates the row (the latest rating's metadata wins). Index on `rated_at`. The table comment records the normalisation rule (a change of `parse.max_input_chars` changes new hashes; old ratings stay under their old hash). Ratings never influence retrieval: nothing in `retrieval/` reads the table.

### 2. Privacy

The text is stored only because a rating exists (the owner's decision), only in the local database, and never written to logs or error bodies (the router uses the generic-error pattern of the search endpoint). `DELETE /api/meme-from-text/ratings/text?queryHash=...` sets `query_text` to null for that message and keeps the hash, grades and ids; rating again stores the text again. No automatic expiry in this version.

### 3. Endpoints (a new router next to the search router; the search endpoint stays read-only)

- `PUT /api/meme-from-text/ratings` with `{text, imageId, grade, rank, reading, parserVersion, taxonomyVersion, degraded, fallback, language}`: validates (grade 0 to 2, rank at least 1, text 1 to 600 characters after normalisation, `reading` in `parsed|edited`, the image exists and is active), computes the hash from `text` itself (the client never supplies it), upserts, returns the stored rating.
- `DELETE /api/meme-from-text/ratings?queryHash=...&imageId=...`: clears one rating (the active button clicked again).
- `GET /api/meme-from-text/ratings?queryHash=...`: `[{imageId, grade}]` for that message.
- `DELETE /api/meme-from-text/ratings/text?queryHash=...`: forget the text (decision 2).
- `meta.queryHash` is added to the search response (server-computed with the same helper; a small additive change to the P4 contract: shared schema `memefromtextresponse`, regenerated TypeScript, Kotlin and Python types, `backend_api.md`). It is identical on a cache hit.
- New shared schemas (`ratingrequest`, `ratingresponse`, `ratingsresponse`) with regenerated types; `backend_api.md` documents all four calls.

### 4. UI

- **Rating bar** on each result card, below the existing row ("Why this meme", the similar badge): a group labelled "Rate this result" with three native toggle buttons, "Good fit" (grade 2), "Acceptable" (grade 1), "Not a fit" (grade 0), `aria-pressed`, at least 44 px high; clicking the active one clears the rating.
- **Optimistic with rollback:** the click updates at once and sends the PUT or DELETE; on failure the button rolls back and an inline "Couldn't save the rating" appears. A `role="status"` line announces "Saved: Good fit" / "Rating cleared"; focus stays on the clicked button. Requests are ordered per image, so rapid clicks cannot reorder and the last click wins.
- **What is sent:** the text of the search being shown (`state.submitted`, never the box's current text), the card's position as `rank`, `reading: "edited"` when an edited profile is in use, otherwise `parsed`, and the parser version, taxonomy version, degraded flag, fallback and language of the response.
- **Remembered grades:** a `useRatings` hook loads `GET /ratings?queryHash=<meta.queryHash>` when results arrive and keeps image id to grade, so ratings survive a refine, a re-search, paging and a reload. A failed load is silent (the bar starts empty).
- **Forget the text:** once a message has any rating the results header shows a small "Forget this message's text" action with a two-step confirm that says rating again stores the text again.
- Ratings are allowed on degraded results (they carry the flag); nothing renders when the page is unavailable. Accessibility follows P5's rules (labelled group, live status, no focus loss).

### 5. Export to the gold-set format: `python -m evaluation export-ratings`

Reads the database with the read-only role (like the other evaluation commands; it refuses otherwise and never calls `load_env()`), writes only under `--out` (default `evaluation/work/ratings-export/`, git-ignored). The gold set is frozen and hash-checked, so the export produces review files, never an automatic merge:
1. **Known gold queries.** The hash of each gold query's normalised text is matched against the ratings' hashes. Matches become judgments in the exact shape `import-judgments` accepts, written per language as `judgments-<lang>.json` with `"round": N` (`--round`) and `rater: "rating-ui"`. A pair that already has a judgment is excluded from the file and listed in the report with both grades; an image outside the frozen corpus is listed and excluded.
2. **New messages.** Written to `candidate-queries.json` (hash, text, language, rated pairs with grade, rank, reading, parser version) for review. Turning a candidate into a gold query (a reviewed query with an expected profile, a new frozen fingerprint) stays a deliberate P6 step.
3. **A summary** (printed and saved): counts by grade, language, `reading`, degraded and parser version.
Ratings only cover results the owner was shown, so they are not equivalent to pooled judgments: the export labels them `rating-ui` and the P6 README states they must not be mixed into metrics without a judging round.

## Out of scope

Using ratings to rank or learn, per-user ratings or authentication, bulk rating, a page for browsing ratings, editing grades after an export, turning candidates into gold queries automatically, Android, rating from the existing `/search` page, automatic text expiry.

## Testing

- Backend pure and router (mocked services): hash helper (whitespace and Unicode normal-form variants equal, different texts differ), request validation, the four calls, 422s, unknown or inactive image, a generic 500 whose body and log never contain the message; `meta.queryHash` equals the rating hash for the same text, also on a cache hit.
- Backend integration (the test database, the whole `tests/integration` root because `Storage/models.py` changes): upsert changes the grade without adding a row; delete; text clearing keeps grades and ids; the check constraint rejects grade 3; the unique key; cascade on image deletion; GET by hash.
- Export: the builder is a pure function with unit tests (gold matching by hash, exclusion of already-judged pairs with both grades reported, corpus exclusion, per-language files in the import format, candidate file, summary counts, deterministic output); the database reader against the test database; the CLI refuses without the read-only role.
- Frontend: `useRatings` (load, optimistic update and rollback, clear, per-image ordering, a new message resets the map, StrictMode double mount); `RatingBar` (`aria-pressed`, toggling, keyboard, status and error lines); page tests (rating survives a refine and a re-search; `rank`; `reading` becomes `edited`; the stored text is the submitted one; the forget flow). Gates: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, `vite build`; Backend root and the retrieval roots stay green; generated types have no diff.
- Live check by the controller on the test database seeded with a few synthetic profiled images (real Ollama, a minimal backend on a free port): rate, re-rate, clear, reload, forget text; never against a real environment's database.

## Acceptance criteria

1. The table, migration, repository, the four endpoints, `meta.queryHash`, shared schemas, regenerated types and `backend_api.md` exist with tests; the rating never affects ranking.
2. Each result shows the three-button rating bar; ratings are saved with text, hash, rank, reading, versions and flags, survive refine, re-search and reload, and can be cleared; failures roll back with a visible message.
3. The text is stored only with a rating, never logged, and can be forgotten per message without losing grades.
4. `export-ratings` produces import-ready `judgments-<lang>.json` for known gold queries (no silent overwrite, corpus-checked), `candidate-queries.json` for new messages and a summary, and refuses without the read-only role.
5. Accessibility holds (labelled group, toggle semantics, live status, focus stays) and all gates pass.
6. The addendum records the live check and the migration commands per environment; the tracker, `CLAUDE.md` and the review report are updated.

## Risks

- Private text in the database: only on rating, local, clearable, never logged.
- Hash drift if the normalisation or `parse.max_input_chars` changes: documented; old ratings keep their old hash.
- Rating bias (only shown results are rated): exports are labelled and never merged silently.
- Migration not applied on an environment: loading ratings fails silently, saving shows the inline error, nothing else breaks.
- Rapid clicks and rollbacks: per-image request ordering in the hook.

## Rollout

Apply the additive migration per environment (`alembic upgrade head` from `Storage/` on metal, general and it, each with its own database), restart the backends, rate for a while, then run `export-ratings` and review the files. `it` has no parser, so the page and the ratings are unavailable there.

## Plan outline (for writing-plans)

1. Backend data: ORM model, migration, repository, integration tests.
2. Backend API: hash helper, shared schemas, regenerated types, service and router, `meta.queryHash`, `backend_api.md`, tests.
3. Frontend API layer: `MemesApi` methods, types, fixtures.
4. `useRatings`, `RatingBar`, the `ResultCard` integration and the forget-text action, with tests.
5. Page wiring and page tests.
6. Evaluation export: builder, CLI, tests, README.
7. Controller steps: the seeded-database live check, migration notes, docs and review report.
