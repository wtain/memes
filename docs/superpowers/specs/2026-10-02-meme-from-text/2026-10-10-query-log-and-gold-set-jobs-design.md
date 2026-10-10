# Meme-from-text query log and gold-set jobs

Status: planned
Plan: docs/superpowers/plans/2026-10-11-query-log-and-gold-set-jobs.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5b-rating-widget-design.md (ratings, `export-ratings`), docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p7-agent-layer-decision-memo.md (reopen condition 2: enough real usage)
Follow-ups: none

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`.

## Goal

1. Record every fresh meme-from-text query, the same way searches are recorded today, so usage can be analysed.
2. Make the two steps that turn ratings into gold judgments (`export-ratings`, `import-judgments`) tracked batch jobs that an admin can start and that run on a schedule.
3. Record the access gap this creates in the productisation memo.

## Decisions (agreed 2026-10-10)

| # | Decision | Reason |
|---|---|---|
| 1 | The log reuses `search_history` and `search_history_tags`, with a `kind` column telling the two apart | Same table, repository and best-effort write path as search; one history |
| 2 | A log row holds the text, the parse and the outcome | Needed to diagnose weak queries and to join to ratings by `query_hash` |
| 3 | The import job merges into `evaluation/gold/judgments.json` in the working tree and never commits | The file is committed and baselines are compared against it; a person reviews and commits |
| 4 | Both jobs are registered for the admin page and scheduled, on `general` only | The gold set is frozen for the `general` corpus |
| 5 | `GET /api/history` stays open and gains a `kind` filter. There is no new access control in this work | The backend has no authentication anywhere; accepted for now, tracked in the productisation memo (decision 7) |
| 6 | "Forget text" on a rating also deletes the log rows with the same `query_hash` | Otherwise the button would promise something it does not do |
| 7 | `docs/security/admin-permissions-todo.md` is brought up to date with every endpoint and page that must become admin-only | That file is the productisation memo for access control |

## 1. Query log

### Schema (one migration, additive)

`search_history` gains nullable or defaulted columns:

| Column | Type | Meaning |
|---|---|---|
| `kind` | `varchar(20)` not null, default `'search'` | `search` or `meme_text`; existing rows become `search` |
| `language` | `varchar(8)` null | parsed language |
| `query_hash` | `varchar(64)` null, indexed | `meta.queryHash` (sha256 of the normalised message), the join key to `meme_text_ratings` |
| `situation` | `text` null | the parser's English situation phrase |
| `degraded` | `boolean` null | pipeline degraded |
| `fallback` | `varchar(32)` null | fallback reason when degraded |
| `parse_ms`, `retrieval_ms` | `integer` null | timings from `meta` |

The parsed axis values (emotion, stance, topic, tone) are stored as `search_history_tags` rows `(category = axis, value = taxonomy key)`. `query` holds the message text (already capped by `parse.max_input_chars`). `client` is derived from the user agent exactly as search does it. `result_count` is the number of returned items.

### Write path

- The best-effort write in `ImageService._record_history` (own session, errors swallowed) moves into a shared recorder used by both services; `HistoryRepository.add` takes the new optional fields. Behaviour of the search log does not change.
- `MemeFromTextService.search` records after a successful pipeline call when the request is a fresh message: no cursor, no edited `profile`, no `filters`. Pages of "Show more", edited readings and filter toggles are not new queries.
- Nothing is recorded when the pipeline is absent (`it`), when `meta.available` is false, or when `search.meme_from_text.query_log.enabled` is false (new key, default `true`, required in every settings file per the existing rule for this block).
- A failed write never fails the request.

### Read path

- `GET /api/history` gets an optional `kind` query parameter (`search` default, `meme_text`, `all`). With no parameter the response is what clients get today.
- Response items gain the optional new fields. `backend_api.md` and the hand-written response models in `Backend/app/api/history.py` are updated together (the repository's "hand-written router models" gotcha).
- No UI is built in this work.

### Forget text

`MemeTextRatingService.forget_text(hash)` additionally deletes `search_history` rows with `kind = 'meme_text'` and that `query_hash` (tags cascade). Grades stay, as before.

### Privacy statement (replaces the current wording)

The message text is never written to application log files and never appears in an error body. It is stored in `search_history` (and, with a rating, in `meme_text_ratings`). Both can be cleared per message with "Forget text". Where the current docs say the text is "stored only because a rating exists" they are corrected: CLAUDE.md (ratings entry and the retrieval/parse entries), the P4 and P5b specs, `backend_api.md`.

## 2. Gold-set jobs

### Shared code

The bodies of `cmd_export_ratings` and `cmd_import_judgments` move into functions that take their inputs and return a result dict:

- `evaluation/ratings_job.py`: `export_ratings(session, gold, out, round_number, today) -> dict`, and `import_exported(gold, export_dir) -> dict`.
- The CLI commands call them and keep their own behaviour, including `require_readonly()` for `export-ratings`. The batch jobs use the normal batch database URL (they only read ratings).

### `export_ratings` (batch module `batch/export_ratings.py`, kind `export_ratings`)

- Writes `judgments-<lang>.json`, `candidate-queries.json` and `summary.json` to `evaluation/work/ratings-export/current/`, replacing the previous export.
- Round number: highest round in `judgments.json` plus 1.
- Stats on the run row: ratings, importable, already judged, outside the corpus, new messages.
- Never merges anything.

### `import_judgments` (batch module `batch/import_judgments.py`, kind `import_judgments`)

- Merges the files in `current/` into `evaluation/gold/judgments.json` with an atomic write (temp file, then rename).
- The exporter already leaves out pairs that have a judgment, so the merge only adds. If a merge would overwrite, the job fails without writing.
- Stats: added, unchanged, path changed, and a note that the change is uncommitted and that baselines compared across the merge are not like for like.
- Does nothing and succeeds with `0 added` when `current/` is missing or empty.

### Both jobs

- Follow the wrapper contract (`main(trigger, run_id)`, `tracked_run` / `finish_existing_run`), so they work from `/admin/batches`, the scheduler and the shell.
- Only run for the environment the corpus was frozen for (`check_environment` against `corpus.json`); elsewhere they finish cleanly with "not configured", like the profile jobs on `it`.
- Registered in `environments/batch_registry.yaml`.
- Scheduled on `general` only: `export_ratings` daily (`interval_minutes: 1440`), `import_judgments` weekly (`interval_minutes: 10080`). The plan picks the mechanism (the `general` overlay if Dynaconf list merging allows it, otherwise the common list with the environment check making other environments a no-op).
- They need the repository checkout (`evaluation/`, `batch/`), which the host-run batch subprocess has; the backend Docker image does not, and the deployment path keeps the scheduler off.

## 3. Documentation

Updated in the same change:

- `docs/batches/inventory.md` (new section, summary counts: scheduled 2 to 4, admin-only 28 to 30), `execution-model.md` (schedule), `gaps-and-findings.md` (statements that only two jobs are scheduled), `README.md`, new `use-cases/ratings-and-gold-set.md`.
- Diagrams: new `diagrams/ratings-and-gold-set.dot` and `.svg`, `job-start-paths.dot` and `.svg` refreshed, `diagrams/README.md` index. Rendered with `@viz-js/viz`, as the README describes (no `dot` binary on this machine).
- CLAUDE.md: batch pipeline entries for the two jobs, ratings entry, privacy wording.
- `backend_api.md`: `GET /api/history` `kind` and new fields.
- P5b spec addendum, `tracker.md`.
- `docs/security/admin-permissions-todo.md`: rewritten current-state section and an endpoint and page table (see below).

### Productisation memo (`docs/security/admin-permissions-todo.md`)

The file lists five endpoints and says three batch scripts are registered; both are stale. It gets a complete inventory of what must be admin-only once a permission model exists, with the reason for each:

- Admin and batch: all `/api/admin/*` (batch trigger, run history, profile coverage), and the pages `/admin/batches` and `/admin/profiles`.
- Curator writes: `PUT`, `POST .../confirm` and `DELETE /api/meme-from-text/profiles/{id}`, `PUT` and `DELETE /api/images/{id}/description-note`, description feedback, flagging and other image mutations found by listing the routers.
- Ingestion: `/api/ingestion/*` and the `/ingestion` page.
- Personal data: `GET /api/history` (now also holds pasted meme-from-text messages), `GET`, `PUT`, `DELETE /api/meme-from-text/ratings*` and the "Forget text" endpoint, which expose or remove rated message text.
- The read-only database role and database dumps as a second route to the same data.
- Public or low-risk reads (search, browse) are listed separately so the memo shows what stays open.

The list is built by reading every router, not from memory.

## Out of scope

- Authentication or any access control (decision 5; recorded in the memo).
- A UI for the query log.
- Retention or automatic pruning of the log. Only the manual "Forget text" and the `enabled` switch exist. Retention is added to the memo as an open item.
- Committing the gold file automatically.
- Changes to ranking: neither the log nor ratings influence results.

## Testing

- Migration: upgrade and downgrade on the test database; existing rows get `kind = 'search'`.
- Recorder: search log unchanged (existing tests pass); meme-from-text row written with tags, hash, timings; not written for cursor, edited profile, filters, unavailable or disabled; a failing write does not fail the request.
- History endpoint: default response unchanged; `kind` filtering; an invalid `kind` is a 422 (the value is a short enum choice, not user text, so FastAPI's default error body is fine).
- Forget text deletes matching `meme_text` rows only.
- Jobs: export and import against a temporary gold directory and the test database; round numbering; no-overwrite guard; atomic write; non-`general` environment skips; wrapper contract (admin trigger, scheduler tick); CLI behaviour unchanged.
- Docs: SVGs regenerate from the `.dot` files with no diff.
- Whole roots run as separate pytest invocations (Backend, `tests/integration`, `batch/tests`, `tests/evaluation` if present), then the frontend gate only if a frontend file changes (none planned).
