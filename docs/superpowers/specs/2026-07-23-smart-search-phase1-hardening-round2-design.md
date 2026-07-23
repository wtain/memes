# Smart Search Phase 1 Hardening Round 2 — Design

Status: Draft

## Context

`docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md` (implemented,
merged) fixed three Minor findings from the original smart-search feature's final review.
Implementing that round surfaced two further, explicitly-deferred issues in
`batch/build_ocr_lemmas.py` and its supporting modules:

1. `repository.ocr_lemmas.OCRLemmasSaver.add_lemmas` inserts via plain ORM `session.add()`
   — a duplicate `(image_id, lemma)` write (e.g. from an overlapping/concurrent job
   invocation) crashes the whole batch with an `IntegrityError` rather than being a no-op.
2. `batch.utils.ocr_lemmas.group_lemmas_by_image` only creates a `lemmas_by_image` entry
   for an image once at least one of its OCR rows survives the confidence/lang-score
   filter. An image where *every* row is filtered out never gets an entry, so
   `build_ocr_lemmas.py`'s main loop — which iterates `lemmas_by_image.items()` — never
   sees it and never marks it done. Confirmed against real data: ~585 images in the
   `metal` environment hit this and are reprocessed on every `--incremental` run forever.

This spec fixes both.

## Fix 1: Upsert-safe `OCRLemmasSaver.add_lemmas`

**Change:** replace the per-lemma `self.session.add(OCRLemma(...))` loop with one
Postgres `INSERT ... ON CONFLICT (image_id, lemma) DO NOTHING` per image, batched as a
single multi-row `.values([...])` statement — matching the existing upsert convention
already used elsewhere in this codebase (`repository/image_extras.py`'s
`ImageExtrasRepository.set_flagged`, `on_conflict_do_update`; this is the same pattern
with `on_conflict_do_nothing`, since lemma presence is binary — there's no value to
update on conflict, just "already there, fine").

This makes `add_lemmas` an `async def` (it needs `session.execute`, not the synchronous
`session.add`). Its one caller (`batch/build_ocr_lemmas.py`) and the one test that calls
it directly (`tests/integration/test_ocr_lemmas_repository.py::test_saver_writes_one_row_per_lemma`)
both need an added `await`.

## Fix 2: Every fetched image gets marked done, not just ones with a surviving lemma

**Change:** `group_lemmas_by_image`'s return signature changes from
`(lemmas_by_image, stats)` to `(lemmas_by_image, all_image_ids, stats)`, where
`all_image_ids` is the set of every distinct `image_id` seen in the input rows,
regardless of whether any of its rows passed the confidence/lang-score filter.

`build_ocr_lemmas.py`'s main loop switches from iterating `lemmas_by_image.items()` to
iterating `all_image_ids`, looking up `lemmas_by_image.get(image_id, set())` for each
(empty set for an image whose rows were all filtered out — `add_lemmas` on an empty set
is already a no-op, matching existing zero-lemma-image behavior). Every image reaching
this loop gets both `add_lemmas` and `mark_done_by_id` called, closing the convergence
gap for the ~585-image case the same way Round 1 already closed it for the
passes-filter-but-zero-lemmas case.

## Testing

- `batch/tests/test_ocr_lemmas_grouping.py`: update every existing test for the 3-tuple
  return; add a new test proving an image whose every row fails the filter still appears
  in `all_image_ids` (while correctly absent from `lemmas_by_image`, unchanged).
- `tests/integration/test_ocr_lemmas_repository.py`: update
  `test_saver_writes_one_row_per_lemma` for the `await`; add a new test proving a second
  `add_lemmas` call for the same `(image_id, lemma)` pair is a no-op, not an error.
- Manual: after implementation, re-run `--incremental` against `metal` and confirm the
  previously-known ~585 images are now included in a run once (converging to done) and
  excluded from the next.

## Out of scope

Everything already deferred by the original smart-search spec and Round 1
(ranking/BM25, fuzzy/typo tolerance, erratives normalization, non-Russian
lemmatization, the accepted OCR-language lemmatization asymmetry) stays deferred.
