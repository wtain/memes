# Smart Search: Fuzzy Matching (Trigram Similarity) — Design

Status: Draft

## Context

The original smart search design (`docs/superpowers/specs/2026-07-21-smart-search-design.md`)
deferred both "fuzzy/typo tolerance" and "erratives normalization" as explicit non-goals.
This spec picks up fuzzy/typo tolerance. Erratives normalization is **not** addressed
here — see "Why erratives are out of scope" below; it needs a genuinely different
mechanism and is left for a future spec.

## Problem

Search currently requires an exact lemma match (or an exact case-insensitive tag-value
match). A single OCR misread or user typo — a dropped, swapped, or doubled character —
produces zero results even though a near-identical, correct lemma exists in the index.

## Investigation: what actually works, empirically tested against the real `metal` database

`rapidfuzz` already exists in this codebase's batch/ML dependency stack and is used by
`rules/concept_tagger.py` for per-concept, hand-curated fuzzy matching — but it is **not**
in `Backend/requirements-backend.txt`, so using it at search-query time would mean adding
a new Backend dependency (this project has been burned by exactly this kind of
Backend/batch dependency drift before — see `dependencies.md`).

Postgres's `pg_trgm` extension is available on the server (confirmed: version 1.6) and
can be enabled by the app's own `ocr` database role directly (tested: `CREATE EXTENSION
IF NOT EXISTS pg_trgm;` succeeds without superuser escalation) — no new Backend
dependency, stays in SQL, and is natively indexable.

Real trigram `similarity()` scores, computed directly against `metal`'s database:

| Pair | Similarity | What it represents |
|---|---|---|
| `реклама` / `рекламо` | 0.60 | single-character OCR substitution |
| `реклама` / `рекламма` | 0.70 | doubled character |
| `телефон` / `телефн` | 0.50 | dropped character |
| `машина` / `машына` | 0.40 | single-character substitution |
| `собака` / `сабака` | 0.40 | common misspelling |
| `кот` / `код` (unrelated, 3 letters) | 0.33 | **false-positive risk** |
| `дом` / `дым` (unrelated, 3 letters) | 0.14 | false-positive risk (lower here) |
| `превед` / `привет` (erratives) | 0.17 | **below the false-positive risk pair above** |
| `кросавчег` / `красавчик` (erratives) | 0.25 | still below genuine-typo scores |
| `аффтар` / `автор` (erratives) | 0.08 | far below |

**Genuine OCR/typo noise scores 0.40–0.70 — comfortably separable from noise.**
**Erratives score 0.08–0.25 — at or below the false-positive risk from unrelated short
words**, because trigram similarity measures shared character substrings, not phonetics,
and erratives deliberately substitute characters (е↔и, а↔о, в↔ф) in ways that break
trigram overlap even though the words sound alike. There is no single threshold that
catches erratives without also flooding short-word queries with noise.

## Why erratives are out of scope here

The premise that trigram similarity would catch erratives "for free" (raised and
initially agreed during design discussion) is empirically false, per the table above.
Erratives need a fundamentally different mechanism — realistically, a hand-curated
substitution dictionary applied at normalize time — which is a separate, standalone
piece of work. Bolting a low-precision threshold onto this feature to chase erratives
would reintroduce the exact false-positive problem the codebase already learned to avoid
(`batch/rules_engine.md`: *"Deterministic by default — global fuzzy on short Russian
lemmas produces false positives"*).

## Design

### Match strategy: exact-first, fuzzy fallback

For each query lemma, independently:
1. Try exact matching (unchanged): `OCRLemma.lemma == lemma` OR `ImageTag.value`
   case-insensitive equal to `lemma`.
2. **Only if that returns zero image IDs**, and the lemma is at least
   `search.fuzzy_min_lemma_length` characters, retry using trigram similarity:
   `similarity(OCRLemma.lemma, lemma) >= search.fuzzy_similarity_threshold` OR
   `similarity(ImageTag.value, lemma) >= search.fuzzy_similarity_threshold`.

This preserves full precision whenever the user's spelling already works (the common
case) and only takes on fuzzy matching's noise risk when exact match would otherwise
return nothing. This composes with the existing AND-across-lemmas logic in
`matching_image_ids` unchanged — the fallback is a per-lemma decision, made before that
lemma's result feeds into the intersection with other query lemmas.

### Thresholds

New settings (domain group `search`, added to the common `environments/settings.yaml` —
not per-environment, since the underlying tradeoff is the same across metal/general/it):

- `search.fuzzy_min_lemma_length` = **5**. The length guard is the primary defense
  against short-word false positives (a 3-letter word has so few trigrams that almost
  any 1-character difference produces a deceptively high similarity score — see `кот`/`код`
  above) — excluding lemmas below this length from fuzzy fallback entirely, regardless of
  threshold, is more robust than trying to tune a single global threshold low enough for
  short words without also matching noise.
- `search.fuzzy_similarity_threshold` = **0.35**. Comfortably below every genuine-typo
  score measured (0.40–0.70) and above the higher end of unrelated-short-word risk
  measured (0.33) — though note the margin against `кот`/`код` (0.33) is only 0.02, which
  is exactly why the length guard, not the threshold alone, is what excludes 3-letter
  pairs like that one in the first place.

### Schema changes

New Alembic migration:
- `CREATE EXTENSION IF NOT EXISTS pg_trgm;` — confirmed runnable by the app's own DB
  role, no admin escalation needed.
- `CREATE INDEX ix_ocr_lemmas_lemma_trgm ON ocr_lemmas USING gin (lemma gin_trgm_ops);` —
  GIN chosen over GiST per Postgres's standard guidance (GIN favors read/lookup speed
  over write speed; `ocr_lemmas` is rebuilt/updated only by the offline batch job, not
  per-request, so read speed is what matters).
- No index added on `ImageTag.value` for the fuzzy path — tag-value cardinality is small
  (a bounded, curated vocabulary) and a plain `similarity()` scan there doesn't need
  index acceleration at this scale.
- No data backfill needed — this only adds schema (extension + index) on top of the
  already-populated `ocr_lemmas` table from prior smart-search rollouts; the new index
  builds against existing rows automatically.

### Query implementation

`repository/ocr_lemmas.py`'s `matching_image_ids` gains a fuzzy-fallback path per lemma:

```python
async def _exact_lemma_ids(session, lemma: str) -> set:
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}


async def _fuzzy_lemma_ids(session, lemma: str) -> set:
    threshold = settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD
    ocr_subq = select(OCRLemma.image_id).where(func.similarity(OCRLemma.lemma, lemma) >= threshold)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.similarity(ImageTag.value, lemma) >= threshold)
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}
```

Inside `matching_image_ids`'s existing per-lemma loop, replace the single `_lemma_ids`
call with: compute exact ids; if empty and `len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH`,
compute fuzzy ids instead. Everything else (the AND-intersection across lemmas, the
`None`/empty-set return semantics) is unchanged.

## Verification requirement

Before this ships, confirm via `EXPLAIN ANALYZE` that the fuzzy fallback query actually
uses the new GIN trigram index rather than sequentially scanning `ocr_lemmas` (46,879
distinct lemmas / 213,981 rows in `metal` alone) — `similarity(col, const) >= threshold`
is generally index-accelerated by a `gin_trgm_ops` index in modern Postgres, but this
must be confirmed against the real, populated table rather than assumed, the same way
the SQLAlchemy `join_transaction_mode` behavior was verified against the installed
library rather than assumed in a prior round.

## Testing

- Integration tests (real Postgres required — trigram `similarity()` has no mocked
  equivalent): exact match still takes precedence over fuzzy (fuzzy fallback not
  invoked when exact match already found something); a misspelled query lemma with no
  exact match falls back to fuzzy and finds the correct image; a short lemma (< 5 chars)
  with no exact match does NOT get fuzzy-matched even when a similar longer lemma exists;
  a lemma with no sufficiently similar match anywhere returns an empty set, not `None`.
- Manual: re-verify the `EXPLAIN ANALYZE` index-usage check against each of the three
  real environments after rollout, since index usage can depend on table statistics
  (`ANALYZE` may need to run after the new index is created).

## Rollout

Migration must run against all three environments (metal/general/it) — same
`CREATE EXTENSION`/`CREATE INDEX` migration, no per-environment variation. Additive
schema change only; existing search behavior for exact matches is completely unchanged,
so this is safe to roll out without the "breaking until backfilled" caveat the original
smart search rollout had.

## Out of scope

- **Erratives normalization** — needs a separate mechanism (most likely a curated
  substitution dictionary), a candidate for its own future spec.
- Ranking/relevance scoring, non-Russian lemmatization, and the previously-documented
  accepted OCR-language cross-detection asymmetry remain deferred, unchanged from prior
  specs.
