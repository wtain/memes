# Smart Search Phase 1 Hardening Round 3 — Design

Status: Draft

## Context

Two Minor findings remain from the smart-search hardening rounds' final reviews:

1. `ImageProcessingStatusRepository.mark_done_by_id` does a `session.get()` (SELECT)
   followed by an ORM add/update — two round-trips per image, in a loop that runs once
   per image across the whole corpus (tens of thousands of images per environment).
2. No automated test exercises `batch/build_ocr_lemmas.py`'s actual `main()` code path
   end-to-end (grouping → saving → status-marking together) — coverage today is
   unit-level (`group_lemmas_by_image`) and repository-level (`OCRLemmasSaver`,
   `ImageProcessingStatusRepository`) separately, with the real end-to-end proof resting
   on manual verification against the `metal` environment.

Fixing (2) properly surfaced a third, more consequential item: doing it well requires
extracting `main()`'s body into a session-accepting function (matching this codebase's
established pattern — see `batch/build_image_descriptions.py`'s `_images_missing_prompts`),
and testing that function against the shared `db_session` fixture. But
`OCRLemmasSaver.__aexit__` (and other repository code) calls `session.commit()` for
real, which conflicts with `db_session`'s own wrapping transaction — an issue already
worked around three separate times across the two prior hardening rounds (`tests/integration/test_ocr_lemmas_repository.py`'s
`test_saver_writes_one_row_per_lemma`, `test_delete_all_clears_table`, and
`test_add_lemmas_is_safe_to_call_twice_for_same_pair`, each via a hand-rolled
`_fresh_session(db_engine)` verification session plus manual cleanup). Round 1's
implementer explicitly recommended fixing this at the fixture level rather than
continuing to patch around it per-test. This spec does that.

## Fix 1: Upsert `mark_done_by_id`

**Change:** replace the `session.get()` + ORM add/update with a single
`INSERT ... ON CONFLICT (image_id, pipeline) DO UPDATE SET status=..., finished_at=...`,
matching the same `sqlalchemy.dialects.postgresql.insert` convention already used by
`repository/image_extras.py`'s `ImageExtrasRepository.set_flagged` and by this round's
sibling fix in `OCRLemmasSaver.add_lemmas` (Round 2) — this one uses `on_conflict_do_update`
rather than `on_conflict_do_nothing`, since `status`/`finished_at` genuinely need to
change on a repeat call (e.g. re-marking an image done in a later run), unlike
`ocr_lemmas`'s binary presence-only semantics.

Scope check: `mark_started`/`mark_done`/`mark_failed`/`record_failure` in the same file
are untouched — only `mark_done_by_id` (added in Round 1 specifically for this
batch job's id-only calling convention) changes.

## Fix 2: Shared `db_session` fixture — SAVEPOINT pattern

**Change:** `tests/integration/conftest.py`'s `db_session` fixture switches from
`sessionmaker(db_engine, ...)` (a fresh connection per test, wrapped in one
`session.begin()`) to binding the session to a single, explicitly-held connection with
`join_transaction_mode="create_savepoint"` (supported since SQLAlchemy 2.0, confirmed
installed: 2.0.45) — the documented pattern for testing code that calls
`session.commit()` internally. Concretely:

```python
@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine):
    async with db_engine.connect() as conn:
        await conn.begin()
        async_session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        async with async_session:
            yield async_session
        await conn.rollback()
```

With this in place, any repository code's `await self.session.commit()` commits (and
SQLAlchemy automatically restarts) an inner SAVEPOINT — invisible to calling test code,
which keeps using the same `db_session` object normally before and after. The outer
connection-level transaction is still rolled back at fixture teardown, so no test's
writes ever persist past it — full isolation preserved, just no more "closed
transaction" errors when inner code commits.

**Retrofit:** the three existing workaround tests in
`tests/integration/test_ocr_lemmas_repository.py` (`test_saver_writes_one_row_per_lemma`,
`test_delete_all_clears_table`, `test_add_lemmas_is_safe_to_call_twice_for_same_pair`)
no longer need `_fresh_session(db_engine)` or manual cleanup — they simplify back to
querying `db_session` directly, like every other test in the file. Leaving them in their
workaround form after fixing the root cause would be actively confusing (a future reader
would see the workaround and reasonably assume the fixture still has the problem).

**Validation:** because this fixture is shared by every file under `tests/integration/`
(~30 files), the full `tests/integration/` suite must pass, not just the files this round
touches, before and after the change.

## Fix 3: End-to-end test for `build_ocr_lemmas.py`

**Change:** extract `main()`'s body (everything currently inside
`async with AsyncSessionLocal() as session:`) into a new function,
`async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics)`,
taking the session and thresholds as explicit parameters — matching
`group_lemmas_by_image`'s existing style (explicit params, no hidden settings reads) and
`batch/rebuild_duplicates.py`'s `create_tmp_duplicates(session)` precedent (a
session-accepting, directly-testable top-level function). `main()` becomes a thin
wrapper: read settings, construct `morph`/`metrics`, open the session, call `run(...)`,
print the final metrics.

A new integration test file, `tests/integration/test_build_ocr_lemmas.py`, calls `run()`
directly against the now-properly-isolated `db_session` fixture, seeds real `Image`/`OCRText`
fixtures (including at least one image whose OCR text entirely fails the confidence
filter, to prove Round 2's convergence fix holds through the *real* code path, not just
the unit-tested `group_lemmas_by_image` function in isolation), and asserts the resulting
`ocr_lemmas` and `image_processing_status` rows directly — no workaround needed, thanks
to Fix 2.

## Testing

- `tests/integration/test_image_processing_status_repository.py`: existing
  `mark_done_by_id` tests re-run to confirm the upsert behaves identically
  (idempotent, correct `status`/`finished_at` on repeat calls).
- `tests/integration/` (full suite, ~30 files): re-run in full after the fixture change,
  before touching anything else in this round, as its own checkpoint.
- `tests/integration/test_ocr_lemmas_repository.py`: the three retrofitted tests,
  simplified.
- New `tests/integration/test_build_ocr_lemmas.py`: `run()` exercised end-to-end for
  both full and incremental modes, including the all-filtered-image convergence case.

## Out of scope

The "batch all images into one multi-row status upsert per run" alternative for Fix 1
(discussed and explicitly declined in favor of the minimal single-row upsert) stays out
of scope — revisit only if `mark_done_by_id`'s per-image round-trip is ever actually
measured as a bottleneck. Everything already deferred by the prior two rounds (ranking,
fuzzy matching, erratives, non-Russian lemmatization, the accepted OCR-language
misdetection asymmetry) remains deferred.
