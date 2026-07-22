# Smart Search Phase 1 Hardening — Design

Status: Draft

## Context

`docs/superpowers/specs/2026-07-21-smart-search-design.md` (implemented, merged) replaced
substring-based OCR text search with lemma-based matching. Its final whole-branch review
(see the branch's merge history) came back "ready to merge" with three Minor,
non-blocking findings. This spec addresses those three findings. It does **not** revisit
any of Phase 1's explicitly deferred non-goals (ranking/BM25, fuzzy/typo tolerance,
erratives normalization, non-Russian lemmatization) — those remain out of scope here too.

## Problem 1: Index/query lemmatization language asymmetry

`rules.normalize.lemmatize_word` treats `language=None` (always used by
`matching_image_ids` when lemmatizing a query — script-based pymorphy3 fallback, real
Russian dictionary lookup for Cyrillic tokens) differently from a confidently-set
non-Russian language string (used by `group_lemmas_by_image` for OCR rows, per the
row's own detected language — pymorphy3 skipped entirely, token just lowercased).

Checked against the real corpus (`ocr_texts.language` distribution across all three
environments): the column is never `NULL` and never the literal string `"unknown"` —
EasyOCR always assigns a confident `ru`/`en`/`es` value (per CLAUDE.md, EasyOCR only
supports those three). So the "undetected language" sub-case this asymmetry could
otherwise apply to doesn't occur in practice, and there is nothing to fix there.

The asymmetry that *does* occur is narrower: a Cyrillic OCR row that EasyOCR
confidently — but wrongly — tagged `"en"` or `"es"` gets lowercase-only treatment on the
index side, while the same word in a query still gets full pymorphy3 lemmatization.
This is exactly the case the final review already judged acceptable ("consistent with
the spec's documented tradeoffs") — fixing systematic cross-language OCR misdetection is
a different, harder problem, out of scope here.

**Resolution: documentation only, no behavior change.** Add a code comment at
`group_lemmas_by_image`'s `normalize(...)` call (and optionally near
`matching_image_ids`'s `language=None` call) noting this accepted asymmetry, so a future
reader doesn't mistake it for an undiscovered bug. No test changes needed.

## Problem 2: Incremental mode never converges for lemma-less images

`build_ocr_lemmas.py --incremental` decides what to (re)process via
`ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language()`, which checks
for the *absence of `ocr_lemmas` rows*. An image whose OCR text legitimately yields zero
surviving lemmas (all tokens too short, or filtered by confidence/lang-score) never gets
a row there — so every incremental run reprocesses it forever. Pure inefficiency, not a
correctness bug (a zero-lemma image correctly matches nothing either way), but real waste
at scale.

The codebase already has the right tool for "did we finish processing image X for job Y,
regardless of what job Y produced": `ImageProcessingStatusRepository`
(`repository/image_procesing_status.py`), a generic `(image_id, pipeline)` status
tracker. It's currently used two different ways elsewhere:
- `extract_text_from_memes.py` uses `mark_started`/`mark_done`/`should_process` with full
  `Image` ORM objects, for interrupted-run resumability during OCR extraction itself.
- `record_failure`/`delete_all` already work with a bare `image_id` (no ORM object
  needed) — the pattern `build_ocr_lemmas.py` actually needs, since it only ever has
  `image_id` values from row tuples, never loaded `Image` instances.

**Fix:**
- Add `ImageProcessingStatusRepository.mark_done_by_id(image_id)` — same shape as the
  existing `record_failure` (id-only, no commit — caller controls commit timing),
  but marks `status="done"` instead of `"failed"`.
- `build_ocr_lemmas.py` uses `pipeline="ocr_lemmas"`: calls `mark_done_by_id(image_id)`
  for every image it processes (regardless of lemma count), and in full mode also calls
  `status_repo.delete_all()` alongside the existing `OCRLemmasRepository.delete_all()`,
  so a full rebuild starts both tables clean.
- `ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language()`'s "already
  indexed" subquery switches its source from `OCRLemma` to
  `ImageProcessingStatus(pipeline="ocr_lemmas", status="done")`. The method's name and
  external contract ("images not yet lemma-indexed") don't change — only which table
  backs the check, so no other caller needs to change.

## Problem 3: No automated cross-endpoint-equivalence test

The core promise of the merged branch — `/api/images` and `/api/recommendations` share
one matching implementation and therefore agree on which images match a given query —
was verified manually against real data, not locked in by an automated test.

**Fix:** add a new small integration test file,
`tests/integration/test_search_matching_equivalence.py`, seeding shared fixtures (no
flagged images, so the one intentional behavioral difference between the two endpoints
doesn't confound the comparison) and asserting `ImageRepository.search` and
`RecommendationsRepository.get_recommendations` return the same matched-ID set for the
same query — one single-lemma case, one multi-lemma AND case.

## Testing

- `tests/integration/test_image_processing_status_repository.py`: new test(s) for
  `mark_done_by_id`.
- `tests/integration/test_images_repository.py`: update the existing
  `test_get_images_and_ocr_texts_without_lemmas_excludes_indexed_images` test to seed
  `ImageProcessingStatus` instead of `OCRLemma`, and add a new regression test proving
  the actual bug fix: an image whose OCR text yields zero lemmas, but is marked
  `done` for `pipeline="ocr_lemmas"`, is excluded from the incremental fetch.
- New `tests/integration/test_search_matching_equivalence.py` per Problem 3.

## Out of scope

Everything Phase 1 already deferred (ranking/BM25, fuzzy/typo tolerance, erratives
normalization, non-Russian lemmatization) stays deferred. Problem 1's residual
"confidently cross-language-misdetected OCR row" asymmetry is explicitly accepted, not
fixed — documented via a code comment only. Fixing OCR language misdetection itself is a
different, out-of-scope problem.
