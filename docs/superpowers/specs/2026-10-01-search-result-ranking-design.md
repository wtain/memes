# Search result ranking — Design

Status: draft
<!-- Design approved in conversation 2026-10-01; this written spec awaits user review. Becomes `approved` after that. -->
Originates from: board task 156 (board/analysis/156-search-result-ranking.md), follow-up of
docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md
ADR: docs/adr/adr-2026-10-01-search-ranking-in-python.md
Plan: (none yet)

## Motivation

Smart search (`GET /api/images?q=`) is a membership filter: `matching_image_ids` (`repository/ocr_lemmas.py`) returns the set of image ids
whose OCR lemmas, tags, note lemmas or Ollama-description lemmas cover every query token, and results are ordered by
`(created_at desc, id desc)`. The union of sources throws away which source and which match tier (exact, stem, fuzzy, phonetic) produced
each hit, so an image that merely fuzzy-matches an LLM description ranks the same as one whose human note contains the exact word. With
four text sources that is now a visible quality gap.

Decisions (user, 2026-10-01): when a text query is present, relevance is the primary order with recency as the tie-breaker; the score is
the per-token best hit, summed across tokens; default weights below; scoring is computed in Python, with an ADR recording that choice so
it can be revisited when the corpus outgrows it.

## Non-goals

- Changing which images match (matching semantics, tiers and their triggers are unchanged).
- Ranking when there is no `q` (browse, facet-only requests keep recency order and the existing cursor).
- Exposing the score in the API response, or a user-facing sort toggle.
- Tuning the weights against real query logs (follow-up once usage exists).
- Pushing scoring into SQL (see the ADR for the alternative and when to revisit it).

## Scoring model

For a query with lemmas `t1..tn` (all must match, as today), the score of an image is

```
score(image) = sum over tokens t of  max over hits h of t on image  ( source_weight(h) * tier_weight(h) )
```

A hit is "token t matched in source S at tier T". A token's score is its single best hit, so one strong hit is not diluted by weaker ones
and several weak hits in other sources add nothing. The image score is the sum over tokens, so with the AND semantics unchanged the score
only differs between images by match quality (and by how many tokens matched strongly).

Default weights (config, see below):

| Source | Weight | Notes |
|---|---|---|
| `note` | 1.0 | human-written `description_note_lemmas` |
| `ocr` | 0.9 | `ocr_lemmas`, text actually in the image |
| `tag` | 0.8 | `tags.value`, all tag sources (`OCR`, `Ollama`, `Note`, `CONCEPT`, `YOLO`) share this one weight |
| `description` | 0.6 | `description_lemmas` of non-rejected Ollama descriptions |

| Tier | Weight | Sources queried |
|---|---|---|
| `exact` | 1.0 | OCR, tags, notes, descriptions |
| `stem` | 0.8 | OCR, descriptions |
| `fuzzy` | 0.5 | OCR, tags, notes, descriptions |
| `phonetic` | 0.4 | OCR |

The tier trigger rules are exactly today's: the stem, fuzzy and phonetic fallbacks run only when the exact tier found nothing for that
token in any source, and are unioned (not sequential), so a token matches at exactly one tier; the sources within that tier are scored
separately. The rejected-description exclusion applies unchanged.

## Components

### `repository/ocr_lemmas.py`

- New `scored_image_matches(session, q) -> Optional[dict[uuid.UUID, float]]`. `None` means "no filter" (same conditions as today). An empty
  dict means no image matches. It is the single implementation of matching; the existing per-tier helpers change from one `UNION`
  query to one query per source, returning `dict[image_id -> source label]` hits, so the caller can weigh them. Description lemmas keep
  the `description_not_rejected` predicate and the English-stem forms from the previous design.
- `matching_image_ids(session, q)` stays with its current signature and becomes
  `None if scores is None else set(scores)`. Its callers (`recommendations_repository`, tests) are unchanged, and the existing
  equivalence and matching tests keep exercising the same semantics through it.
- A module-level pure function `score_hits(...)` (token hits to image scores) holds the arithmetic so it is unit-testable without a DB.
- Weights come from `settings.SEARCH.RANKING.SOURCE_WEIGHTS` / `.TIER_WEIGHTS`; a missing key is a startup error, not a silent default.
- Cost note: a token that needs the fallback tiers now issues up to ~10 small queries instead of ~4 unions. Each is index-assisted; the
  exact tier (the common case) is 4 queries instead of 1 union.

### `Backend/app/repositories/image_repository.py` and `Backend/app/services/image_service.py`

- `search(q, tags, cursor, limit)` is split by whether `q` yields scores:
  - **no `q`, or `q` that normalizes away (scores is `None`)**: current SQL path and `(created_at, id)` cursor, untouched.
  - **scores present**: intersect with the tag-facet filter (`tags` AND semantics as today). Facet counts are computed over the same filtered
    set as today, unaffected by order. Then one query fetches `(id, filename, created_at, flagged)` for the surviving ids; the service
    sorts by `(score desc, created_at desc, id desc)` and returns the page strictly after the cursor, `limit + 1` rows to compute `hasNext`.
- The cursor for ranked pages is an opaque base64 JSON `{"score": float, "created_at": iso, "id": uuid}`. The decoder distinguishes the two
  formats by the presence of `score`. A recency-format cursor presented with a `q` that produces scores is treated as invalid: the request
  restarts at page 1 (no error). A ranked cursor presented without `q` is ignored the same way. Web and Android treat the cursor as an
  opaque string, so no client change is needed.
- Scores are compared as floats rounded to 6 decimals before sorting and in the cursor, so a cursor round-trips through JSON without
  drifting across the strict-after comparison.
- **Scale guard:** when a query matches more than `settings.SEARCH.RANKING.WARN_MATCH_COUNT` (default 10000) images, log one warning with
  the query length, match count and elapsed scoring time. No behavior change. This is the measurable trigger named in the ADR.
- `_record_history` and the response shape are unchanged.

### Configuration (`environments/settings.yaml`, group `search`)

```yaml
search:
  ranking:
    source_weights: {note: 1.0, ocr: 0.9, tag: 0.8, description: 0.6}
    tier_weights: {exact: 1.0, stem: 0.8, fuzzy: 0.5, phonetic: 0.4}
    warn_match_count: 10000
```

The existing `search.fuzzy_*` / `phonetic_*` keys are untouched. The `ocr`/`it` overrides need nothing (common file).

## Documentation

- `backend_api.md`: for `GET /api/images`, state that with `q` results are ordered by relevance then recency, without `q` by recency, and
  that the cursor is opaque and a stale-format cursor restarts the listing.
- `docs/data-flow.md`: search consumers row notes the weights and where they live.
- `CLAUDE.md`: mention the `search.ranking.*` settings in the Configuration paragraph if it lists search keys, and the new ADR.

## Testing

- Pure unit tests for `score_hits` in `tests/integration/test_search_scoring.py` (it needs no DB rows, but it imports the repository module
  and the integration root already sets `DATABASE_URL`): best hit per token wins, tokens sum, weights come from config, rounding.
- `tests/integration/` (whole root, since `repository/ocr_lemmas.py` is shared): ordering (a note hit outranks a description hit for the
  same word; exact outranks fuzzy; multi-token sums; equal scores fall back to recency); a token matched in two sources scores the
  best one, not the sum; `matching_image_ids` still returns the same sets as before for the existing fixtures; rejected descriptions
  contribute no score; the facet filter intersects with scores.
- `Backend/tests/`: paging a ranked query across several pages yields every match exactly once in non-increasing score order; stale
  recency-format cursor with `q` restarts at page 1; no-`q` path unchanged (existing tests); `hasNext` correct at the boundary.
- A warning is logged above `warn_match_count` (caplog).
- Run the four test roots separately (never combined), per CLAUDE.md.

## Rollout

No migration and no backfill. Deploying changes the order of every text search immediately. Weights are tunable in `settings.yaml` without a
code change. Rollback is reverting the change (ordering returns to recency).

## Risks

- **Large result sets:** the whole match set is materialized, scored and sorted in Python per request (already true for the id set). A
  query like a very common word can match thousands of images. Mitigated by the scale guard log and the ADR's revisit triggers.
- **Weights are a guess** until real queries inform them; they are config, not code, and the ordering is deterministic and explainable.
- **Cursor change** only affects a client mid-pagination across the deploy (restarts at page 1).
