# ADR 2026-10-01: Search relevance is scored and paged in Python, not in SQL

STATUS: ACCEPTED

Related: `docs/superpowers/specs/2026-10-01-search-result-ranking-design.md` (the design this records the key trade-off of),
`docs/superpowers/specs/2026-07-21-smart-search-design.md`, board task 156.

## Context

Smart search used to be a membership filter ordered by recency. Adding a relevance order (note and OCR hits above LLM-description hits,
exact above fuzzy) needs a score per matching image and pagination that is stable under that order.

Where the matching happens decides where scoring can happen. `matching_image_ids` (`repository/ocr_lemmas.py`) already runs one small
indexed query per source and tier per query token and intersects the per-token candidate sets **in Python**; the repository then passes the
whole surviving id set back to SQL as `img.id IN (...)` for the page query and the facet counts. The corpus today is roughly 18k active
images on `metal` and 32k on `general`; a typical text query matches from a handful to a few thousand images.

## Decision

Compute relevance in Python and page in Python:

- `scored_image_matches` returns `{image_id: score}` (best hit per token, `source weight x tier weight`, summed across tokens).
- The repository fetches `(id, filename, created_at, flagged)` for the surviving ids in one query; the service sorts by
  `(score desc, created_at desc, id desc)` and returns the page strictly after a keyset cursor `(score, created_at, id)`.
- Queries without `q` keep the existing SQL path and `(created_at, id)` cursor.

## Alternatives considered

1. **Push scores into SQL**: send `VALUES (id, score)` (or a temp table) and join it, ordering and applying the keyset predicate in the database.
   Scales better for very large match sets and avoids the Python sort, but needs long bind-parameter lists or a temp table per request,
   more fragile SQL (asyncpg caps a statement at 32,767 bind parameters; the existing code already chunks around that elsewhere), and is
   more machinery than the current corpus needs.
2. **Database-side relevance (Postgres full-text `ts_rank`, or a trigram score column)**: would replace the tiered, per-source scoring with
   the engine's own ranking and require reworking the lemma indexes into tsvectors. The current matching tiers (stemming, trigram, phonetic
   erratives, rejected-description exclusion) are bespoke, so this is a rewrite, not an incremental step.
3. **Offset pagination**: simpler cursor, but results can shift between pages when data changes (notes edited, descriptions rejected), so
   rows could repeat or be skipped. Rejected in favor of a keyset cursor.
4. **No ranking**: rejected by the user (2026-10-01); recency alone buries strong matches.

## Consequences

- No new SQL machinery, no new tables, no migration. Matching semantics are unchanged.
- Each ranked request materializes the full match set in Python and sorts it: time and memory are linear in the number of **matches**
  (not the corpus). Page cost is therefore dominated by how common the query words are.
- The match-set size is unbounded in principle; a very common word on a large corpus is the failure mode.
- Cursor format changes for ranked pages; a stale-format cursor restarts at page 1 (web and Android treat the cursor as opaque).
- The score is deterministic for a given query and data, so pages are consistent unless the underlying data changes mid-pagination (same
  caveat as any keyset pagination).

## Revisit when

Any of these holds, then move to alternative 1 (SQL-side scoring and keyset) or reconsider alternative 2:

- The scale guard fires routinely: the backend logs a warning when a query matches more than `search.ranking.warn_match_count`
  (default 10,000) images. Treat sustained warnings as the signal.
- p95 latency of a ranked `GET /api/images?q=` exceeds about 1 s on the largest environment.
- Memory per search request becomes a concern (many concurrent common-word queries).
- The corpus grows by roughly an order of magnitude (hundreds of thousands of images), at which point common-word match sets stop being "a few thousand".

What to measure first: the logged match counts and scoring time, then `EXPLAIN ANALYZE` of the per-source queries on the read-only role.
