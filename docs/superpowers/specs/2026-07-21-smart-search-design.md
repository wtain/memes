# Smart Search (Phase 1: Cross-Line Join + Lemma Matching) — Design

Status: done
Plan: docs/superpowers/plans/2026-07-21-smart-search.md
Originates from: docs/superpowers/specs/drafts/2026-07-17-smart-search.md
Follow-ups: docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md

## Context

`docs/superpowers/specs/drafts/2026-07-17-smart-search.md` is a loose brainstorm
draft naming two concrete text-search failures and listing many possible
directions (BM25 ranking, fuzzy/Levenshtein matching, offline erratives
correction, caching, etc.). This spec scopes down to the two failures the
draft's summary actually names, and defers everything else to future specs.

## Problem

Two independent bugs in today's text search:

1. **Cross-line phrase matching.** OCR splits a meme's text into multiple
   independent rows (one per detected line/block). Searching `"полиция"`
   does not find a meme whose caption is OCR'd as two separate lines
   `"звоню в"` / `"полицию"`, because today's matching either requires the
   whole query to be a substring of a *single* OCR line
   (`/api/images`), or requires per-word substrings within lines joined by
   naive row-order concatenation (`/api/recommendations`) — see "Current
   behavior" below.
2. **Russian case/word-form mismatches.** Substring matching is
   morphology-blind: `"полиция"` (nominative) is not a substring of
   `"полицию"` (accusative). Any declined/conjugated form that doesn't
   happen to share a substring with the query silently fails to match.

### Current behavior (for reference)

Two endpoints implement text search today, and they disagree with each
other:

- **`/api/images`** (`Backend/app/repositories/image_repository.py`,
  `_build_filtered_ids_query`): the *entire* query string must be a
  substring of a *single* `OCRText.text` row (`UPPER(text) CONTAINS
  UPPER(q)`), case-insensitive. No confidence filter. No per-word
  splitting.
- **`/api/recommendations`** (`Backend/app/repositories/recommendations_repository.py`,
  `_get_matching_ids`): the query is split on whitespace; each word must be
  a substring of either (a) all of an image's OCR rows joined with
  `string_agg(text, ' ')` (confidence > 0.8 only), or (b) some `ImageTag.value`.
  Words are ANDed. `string_agg`'s row order is whatever the DB returns it in
  — not spatially ordered by `bbox`.

Both are boolean AND-of-terms retrieval with no ranking; this spec keeps
that retrieval model and only changes what counts as a "match" for a term.

## Goals

- A single, shared matching implementation used by both `/api/images` and
  `/api/recommendations`, replacing both `_build_filtered_ids_query`'s text
  branch and `_get_matching_ids`.
- Multi-word queries match across OCR line boundaries within the same
  image, regardless of which line each word's match came from.
- Russian (and, via the same code path, any language) query words match
  OCR text regardless of grammatical case/word form, via lemmatization.
- Numeric query tokens (years, model numbers, etc.) continue to work.

## Non-goals (deferred to future specs)

- **Ranking/relevance scoring.** Retrieval stays boolean AND-of-lemmas,
  same as today — no BM25/TF-IDF, no relevance ordering.
- **Fuzzy/typo tolerance** (Levenshtein, trigram similarity) — not
  addressed here.
- **Erratives normalization** (превед→привет) — not addressed here.
- **True morphological lemmatization for non-Russian text** —
  `rules.normalize.LEMMATIZABLE_LANGUAGES` stays `{"ru"}`; English/other
  text still only gets lowercased, same as every other existing consumer of
  `rules/normalize.py`. This spec's cross-line-join benefit applies to all
  languages; the case/word-form fix is Russian-specific, same as today's
  infrastructure.
- **Arbitrary substring/prefix matching** (e.g. `"кот"` finding
  `"котёнок"`) is intentionally lost as a side effect of switching from
  substring to lemma-token matching. This is a deliberate tradeoff, not an
  oversight: it's what makes the case-mismatch fix possible without a
  separate fuzzy-matching system.

## Design

### Data model: `ocr_lemmas` table

New table, one row per `(image_id, lemma)` pair — the per-image lemma set,
unioned across all of that image's OCR lines. This union *is* the
cross-line-join fix: a multi-word query matches as soon as each word's
lemma is present somewhere in the image's set, independent of which line
(or how many lines) contributed it. No bbox-based line ordering or joiner
character is needed.

```python
class OCRLemma(Base):
    __tablename__ = "ocr_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)

    image = relationship("Image", back_populates="ocr_lemmas")

    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
    )
```

Composite primary key `(image_id, lemma)` enforces set semantics (a lemma
appears at most once per image) without a separate surrogate key or unique
constraint. New Alembic migration adds the table + index.

### `rules/normalize.py`: one additive parameter

`normalize()` gains `keep_digit_tokens: bool = False`. When `True`, a
pure-digit token is kept as a literal lemma (itself, unchanged) instead of
being dropped, still subject to the existing `min_length` filter. Default
is `False`, so every existing caller (rules engine, `build_bow.py`) is
unaffected — digits stay dropped for tag/concept-vocabulary purposes, where
they were never meaningful. Only the new search code passes
`keep_digit_tokens=True`, preserving today's ability to search for years,
model numbers, etc., which would otherwise regress under plain
lemmatization.

### Batch job: `batch/build_ocr_lemmas.py`

Modeled directly on `batch/build_tags_from_ocr.py`:

- Reads OCR rows via `ImagesRepository.get_images_and_ocr_texts_with_language()`
  (full mode) or `..._without_tags_with_language()`-equivalent restricted to
  images with no `ocr_lemmas` rows yet (`--incremental` mode).
- Filters rows with the existing `passes_language_filter(confidence,
  lang_score, settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN)` —
  reusing the same thresholds `build_tags_from_ocr`/`build_bow` already use;
  no new settings introduced.
- For each image, unions
  `rules.normalize.normalize(text, morph, min_length=settings.BOW.MIN_WORD_LENGTH, language=language, keep_digit_tokens=True)`
  across all its (surviving) OCR rows into one lemma set, then upserts one
  row per lemma into `ocr_lemmas`.
- Default mode: delete-and-rebuild (like `build_tags_from_ocr`'s default);
  `--incremental` flag skips images that already have rows.
- Same `ProgressTracker` + `SimpleMetricsListener` instrumentation as
  `build_tags_from_ocr.py`.
- `--env {metal,general,it}` CLI flag, same convention as other batch
  scripts.

Added to the batch pipeline list in `CLAUDE.md`, immediately after
`build_tags_from_ocr` (same input dependency: OCR text + language, already
populated by `extract_text_from_memes`).

### Shared matching function: `repository/ocr_lemmas.py`

New repository module, analogous to `repository/tags.py`, home for both the
writer used by `build_ocr_lemmas.py` and the matching function shared by
the two backend repositories. `repository/` is already the common
data-access layer both `Backend/` and `batch/` depend on today
(`Backend/app/repositories/concept_repository.py` extends
`repository/concepts.py`; multiple `batch/` scripts import
`repository.images`, `repository.tags`, `repository.ocr_text` directly) —
this introduces no new dependency direction.

Matching algorithm, given a query string:

1. Lemmatize the query: `normalize(query, morph, min_length=settings.BOW.MIN_WORD_LENGTH, language=None, keep_digit_tokens=True)`.
   `language=None` is deliberate — it's `rules/normalize.py`'s existing
   "no per-word language signal" path, which already lemmatizes
   script-by-script (real Russian dictionary lookup for Cyrillic tokens,
   lowercase passthrough for Latin), exactly what's needed for a query
   string of unknown language.
2. For each query lemma, find matching image IDs: rows in `ocr_lemmas`
   with that lemma, **union** with images having an `ImageTag.value`
   (case-insensitive) **equal to** that lemma. (Tag values in this repo are
   already single-word canonical/base forms — e.g. `животное:кот`, never
   `животное:коты` — so lemma-equality is the correct comparison here too,
   not substring; this also fixes tag search for inflected query forms,
   e.g. querying `"коты"` now matches the tag `"кот"`, which substring
   matching never did.)
3. Intersect (AND) across all query lemmas' matching-ID sets, short-
   circuiting to empty on the first empty set — same control flow
   `RecommendationsRepository._get_matching_ids` already uses today, just
   against the new lemma-equality queries instead of substring ones.

Both `ImageRepository._build_filtered_ids_query` (text branch) and
`RecommendationsRepository._get_matching_ids` call this one function.
`RecommendationsRepository.OCR_CONFIDENCE_THRESHOLD` (0.8, applied at query
time today) is removed — confidence/lang-score filtering now happens once,
at index-build time in `build_ocr_lemmas.py`, not per-query.

## Error handling / edge cases

- **Empty query**: no filter applied, same as today on both endpoints.
- **Query lemma matches no image**: empty result set for the whole query
  (AND short-circuit), same as today.
- **A query word normalizes away to nothing** (shorter than `min_length`
  after lemmatization): that word is simply dropped from the AND, same as
  how `min_length` already silently discards short tokens in
  `build_bow`/the rules engine. If *every* word in the query drops out this
  way, the AND has no terms left and the query behaves as if empty (matches
  everything), matching today's behavior for an empty `q`.
- **Non-Cyrillic, non-Latin scripts**: unchanged — `tokenize()`'s
  `[^\W_]+` character class and pymorphy3's own fallback behavior are
  untouched by this spec.

## Rollout

Breaking until backfilled: `ocr_lemmas` starts empty, so search would
return nothing for any text query until populated. `build_ocr_lemmas.py`
(full mode) must run once per environment (metal/general/it) as part of
deploying this change — a required rollout step, not an optional follow-up.

No feature flag: this is a straight replacement of the existing matching
logic, not a parallel mode.

## Testing

- **`tests/rules/`**: unit tests for `normalize()`'s new
  `keep_digit_tokens` parameter — digit run kept as a literal lemma
  (subject to `min_length`); all existing non-digit behavior byte-for-byte
  unchanged when the parameter is omitted or `False`.
- **`batch/tests/`**: unit tests for `build_ocr_lemmas.py`'s per-image
  lemma-union logic — given OCR rows across multiple lines and languages
  for one image, assert the correct unioned lemma set; independent of a
  real DB, per existing batch test conventions.
- **`Backend/tests/`** (mocked DB, existing pattern): tests for the shared
  matching function covering AND-across-lemmas, the tag-equality branch,
  the empty-query-lemma-set edge case, and case-insensitivity. Existing
  tests for `/api/images` and `/api/recommendations` search behavior
  updated to reflect lemma-based matching instead of substring matching.
- **Manual**: after running `build_ocr_lemmas.py` against a real
  environment, confirm the two motivating cases from the Problem section —
  a cross-line query and a Russian case-mismatched query — now return
  results that failed before.

## Out of scope, revisited

Everything under "Non-goals" above remains explicitly deferred: ranking
quality (BM25/TF-IDF), fuzzy/typo tolerance, erratives normalization, and
true morphological lemmatization for non-Russian OCR text. Each is a
candidate for its own future spec once this phase ships and its real-world
match quality can be observed.