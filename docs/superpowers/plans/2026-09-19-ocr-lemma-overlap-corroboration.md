# OCR-Lemma Overlap Corroboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require a lexical-overlap corroboration check (via the already-populated `ocr_lemmas`
table) before an OCR-text-embedding-sourced duplicate candidate is trusted, closing the
false-positive gap found during the 2026-09-18 feature's live rollout, and re-enable
`clusterize.py`'s active-library auto-clustering for `ocr_text`-sourced pairs, which that rollout's
interim fix disabled.

**Architecture:** A new SQL fragment computes `shared_lemmas / min(lemma_count_1, lemma_count_2)`
between two candidate images' `ocr_lemmas` rows, applied as an additional filter on
`find_duplicates()`'s already-KNN-narrowed candidate set (not inside the LATERAL, to avoid running
it against the whole corpus). Enforced at insertion time in both `rebuild_duplicates.py` and
`ingest_find_duplicates.py`, so `clusterize.py` can trust any `ocr_text`-sourced row it sees without
re-checking anything. `build_ocr_lemmas.py` gains `--status` support (its repository layer already
has it) and gets wired into `ingest_auto_prep`, closing an operational coverage gap.

**Tech Stack:** PostgreSQL (correlated subqueries against `ocr_lemmas`, no new indexes needed —
`(image_id, lemma)` is already the table's primary key), SQLAlchemy `text()`, pytest/pytest-asyncio
integration tests against `ocrdb_test`.

**Spec:** `docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`

## Global Constraints

- `MIN_LEMMA_OVERLAP_COEFFICIENT = 0.2`, `MIN_LEMMA_COUNT_FLOOR = 3` — exact values from the spec's
  calibration (zero real pairs observed between overlap coefficient 0.125 and 0.417 across
  `general`'s complete 191-pair distribution; 0.2 sits in the middle of that empty gap).
- The overlap check goes in `find_duplicates()`'s **outer** WHERE clause (alongside
  `nn.distance < :threshold`), never inside the LATERAL's `corpus_filter_sql` — the LATERAL's own
  `ORDER BY ... LIMIT :k` must narrow the candidate set *before* the lemma-overlap subqueries run,
  or they run against far more candidates than necessary. Every task touching this must preserve
  that placement; a task or reviewer that "simplifies" it back into `corpus_filter_sql` is
  reintroducing the exact performance problem the spec's Design §2 argues against.
- `build_ocr_lemmas.py`'s destructive full-reprocess path (`incremental=False`) calls
  `OCRLemmasRepository.delete_all()`, which is a genuinely unconditional `DELETE FROM ocr_lemmas`
  with **no status scoping at all** (confirmed by reading the repository directly — it takes no
  arguments). `--status pending` combined with a full reprocess would silently wipe the *entire*
  table, including the active corpus's lemma index, not just pending images. Task 1 must guard
  against this combination explicitly, not just document it.
- No schema migration, no new column, no Backend/frontend changes anywhere in this plan —
  `distance_source` already ships as `'clip'`/`'ocr_text'`; this plan changes which rows reach that
  column, not its shape. Any task that touches `Storage/models.py`, `Backend/`, or
  `Frontend/memes-frontend/` has gone outside this plan's scope.
- Existing tests that currently rely on the OCR-text probe finding a match **with no `ocr_lemmas`
  rows inserted** will break once Task 2/3 land, since such a pair will no longer pass the new
  gate. Confirmed by direct inspection of the current test files, not assumed — four such tests
  exist today (two in `tests/integration/test_rebuild_duplicates.py`, two in
  `tests/integration/test_ingest_find_duplicates.py`) and are named explicitly in Tasks 2 and 3
  below. Each needs a matching `ocr_lemmas` insert added, not just new tests appended alongside it.

---

### Task 1: `batch/build_ocr_lemmas.py` — add `--status` support + destructive-combination guard

**Files:**
- Modify: `batch/build_ocr_lemmas.py`
- Modify: `batch/tests/test_build_ocr_lemmas_main.py`

**Interfaces:**
- Consumes: `repository/images.py`'s `get_images_and_ocr_texts_with_language(status=...)` and
  `get_images_and_ocr_texts_without_lemmas_with_language(status=...)` — both already accept a
  `status: str = "active"` parameter (confirmed by reading `repository/images.py` directly); no
  repository changes needed anywhere in this task.
- Produces: `main(trigger="manual", run_id=None, incremental=True, status="active")` — the new
  `status` parameter Task 5 (`ingest_auto_prep.py`'s wiring) depends on.

- [ ] **Step 1: Thread `status` through `run()`, `_process()`, and `main()`**

In `batch/build_ocr_lemmas.py`, change `run()`'s signature and its two repository call sites
(currently lines 17-34):

```python
async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph,
              metrics, status: str = "active"):
    lemmas_repo = OCRLemmasRepository(session)
    images_repo = ImagesRepository(session)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)

    if not incremental:
        await lemmas_repo.delete_all()
        await status_repo.delete_all()
        await session.commit()

    print(f"Mode: {'incremental' if incremental else 'full'}")
    print(f"Status: {status}")
    print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}, OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}")

    if incremental:
        rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language(status=status)
    else:
        rows = await images_repo.get_images_and_ocr_texts_with_language(status=status)
```

(Everything else in `run()` — the `simplified_rows` comprehension, `group_lemmas_by_image` call,
`OCRLemmasSaver` loop — is unchanged.)

Change `_process()` (currently lines 63-75):

```python
async def _process(incremental: bool, status: str = "active") -> None:
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        await run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length,
                  morph, metrics, status=status)

    print("Lemmas:")
    metrics.print()
```

Change `main()` (currently lines 78-84) to add the parameter **and** the destructive-combination
guard:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True,
                status: str = "active") -> None:
    if status == "pending" and not incremental:
        raise ValueError(
            "build_ocr_lemmas: --status pending requires incremental mode. A full reprocess "
            "(incremental=False) unconditionally deletes ALL ocr_lemmas rows via "
            "OCRLemmasRepository.delete_all() -- there is no status-scoped delete -- which would "
            "wipe the active corpus's lemma index too, not just pending images."
        )
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental, status=status)
    else:
        async with tracked_run(kind="build_ocr_lemmas", trigger=trigger):
            await _process(incremental=incremental, status=status)
```

- [ ] **Step 2: Add `--status` to the CLI**

Change the `if __name__ == "__main__":` block (currently lines 87-95):

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images not yet marked done for the ocr_lemmas "
                             "pipeline (default: clear all and reprocess)")
    parser.add_argument("--status", choices=["active", "pending"], default="active",
                        help="Which images to process (default: active). --status pending "
                             "requires --incremental (see main()'s own guard).")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental, status=args.status))
```

- [ ] **Step 3: Update existing tests for the new parameter**

In `batch/tests/test_build_ocr_lemmas_main.py`, both existing assertions need the new default
kwarg added (the `_process` call shape changed):

```python
        tracked_run_mock.assert_called_once_with(kind="build_ocr_lemmas", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True, status="active")
```

and the second test's matching assertion:

```python
        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True, status="active")
```

- [ ] **Step 4: Add new tests for `--status` behavior and the guard**

Append to `batch/tests/test_build_ocr_lemmas_main.py`:

```python
    @pytest.mark.asyncio
    async def test_status_pending_threaded_through_to_process(self):
        process_mock = AsyncMock()
        import batch.build_ocr_lemmas as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled", status="pending")

        process_mock.assert_awaited_once_with(incremental=True, status="pending")

    @pytest.mark.asyncio
    async def test_status_pending_with_incremental_false_raises_before_any_db_work(self):
        process_mock = AsyncMock()
        import batch.build_ocr_lemmas as module

        with patch.object(module, "tracked_run") as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            with pytest.raises(ValueError, match="requires incremental mode"):
                await main(status="pending", incremental=False)

        tracked_run_mock.assert_not_called()  # the guard fires before tracked_run/_process --
        # confirms this can't reach delete_all() even indirectly
        process_mock.assert_not_awaited()
```

- [ ] **Step 5: Run the tests**

```bash
pytest batch/tests/test_build_ocr_lemmas_main.py -v
```

Expected: all pass (2 existing + 2 new = 4).

```bash
pytest batch/tests/ -q
cd Backend && pytest -q
cd .. && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: `batch/tests/` and `Backend/tests/` fully clean. `tests/integration/` should show exactly
the known pre-existing failures (`test_ingestion_cluster_cap.py` x3,
`test_ingestion_cluster_splitting.py::test_tier_b_splitting_disabled_returns_one_cluster_per_blob`,
`test_ingestion_tier_b_review.py::test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards`
— 5 total, confirmed unrelated to this whole effort across the entire prior branch's history,
reproducing identically on `main` itself) and possibly a 6th, time-of-day-dependent flake in
`test_backend_trends_repository.py` if the run happens to cross a midnight boundary — also
confirmed unrelated. `tests/integration/test_build_ocr_lemmas.py` (the real-DB integration test for
`run()` itself) needs no changes in this task — it tests `run()` directly with its own fixtures, not
through `main()`, and doesn't exercise the new guard (which lives in `main()` only).

- [ ] **Step 6: Commit**

```bash
git add batch/build_ocr_lemmas.py batch/tests/test_build_ocr_lemmas_main.py
git commit -m "feat: add --status support and a destructive-combination guard to build_ocr_lemmas.py"
```

---

### Task 2: `batch/rebuild_duplicates.py` — the lemma-overlap corroboration gate

**Files:**
- Modify: `batch/rebuild_duplicates.py`
- Modify: `tests/integration/test_rebuild_duplicates.py`

**Interfaces:**
- Consumes: nothing new from other tasks (the `ocr_lemmas` table already exists and is already
  populated for at least some of the corpus — Task 1/5's coverage fix is a rollout prerequisite,
  not a code dependency for this task's own tests, which insert their own `ocr_lemmas` rows
  directly).
- Produces: `find_duplicates(..., extra_where_sql: str | None = None)` — the new optional
  parameter Task 3 (`ingest_find_duplicates.py`) also uses. `MIN_LEMMA_OVERLAP_COEFFICIENT`,
  `MIN_LEMMA_COUNT_FLOOR`, `_OCR_LEMMA_OVERLAP_CHECK` — all three imported (never redefined) by
  Task 3.

- [ ] **Step 1: Add the new module constants and the overlap-check SQL fragment**

Add directly after the existing `TEXT_EMBEDDING_TIGHT_THRESHOLD`/`TEXT_EMBEDDING_LOOSE_THRESHOLD`/
`CLIP_SAFETY_NET_THRESHOLD` block (currently lines 105-107):

```python
# Corroboration gate for the OCR-text probe specifically -- see
# docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md. Computed from
# ocr_lemmas (already populated by build_ocr_lemmas.py for smart search, reused as-is here) rather
# than the OCR-text embedding itself, because embedding distance alone was shown NOT to separate
# true from false positives (both span 0.05-0.10) -- lexical overlap does, cleanly, on real data.
MIN_LEMMA_OVERLAP_COEFFICIENT = 0.2  # shared_lemmas / min(lemma_count_1, lemma_count_2);
                                      # calibrated against general's real 191-pair distribution --
                                      # zero pairs observed between 0.125 and 0.417, so 0.2 sits in
                                      # the middle of a genuinely empty gap, not a guessed round number
MIN_LEMMA_COUNT_FLOOR = 3            # guards the degenerate case where an image has 1-2 total
                                      # lemmas, where a single coincidental shared word would
                                      # otherwise score 50-100% overlap

# References probe.id and nn.image_id -- only valid interpolated into find_duplicates()'s OUTER
# WHERE clause (after the LATERAL resolves nn), never into corpus_filter_sql (which runs inside
# the LATERAL, before nn exists) -- see this plan's Global Constraints for why placement matters.
# Deliberately normalizes by the SMALLER image's lemma count (an "overlap coefficient"), not raw
# Jaccard or raw intersection count: a large "hub" text can coincidentally share several lemmas
# with an unrelated image purely through volume (confirmed on real data -- a 635-OCR-block image
# shared 4 lemmas with a totally unrelated post), which a raw-count threshold would wrongly admit.
# Normalized by the smaller side, that same pair scores 0.042 -- correctly rejected.
_OCR_LEMMA_OVERLAP_CHECK = """
    (SELECT LEAST(
        (SELECT count(*) FROM ocr_lemmas WHERE image_id = probe.id),
        (SELECT count(*) FROM ocr_lemmas WHERE image_id = nn.image_id)
    )) >= :min_lemma_count
    AND (
        (SELECT count(*) FROM ocr_lemmas a JOIN ocr_lemmas b ON a.lemma = b.lemma
         WHERE a.image_id = probe.id AND b.image_id = nn.image_id)::float
        / GREATEST((SELECT LEAST(
            (SELECT count(*) FROM ocr_lemmas WHERE image_id = probe.id),
            (SELECT count(*) FROM ocr_lemmas WHERE image_id = nn.image_id)
        )), 1)
    ) >= :min_overlap_coefficient
"""
```

- [ ] **Step 2: Extend `find_duplicates()` with the new `extra_where_sql` parameter**

Replace the current function (lines 115-159) with:

```python
async def find_duplicates(session, probe_sql: str, corpus_filter_sql: str, k: int, threshold: float,
                           distance_source: str, embedding_table: str = "embeddings",
                           extra_params: dict | None = None, extra_where_sql: str | None = None) -> int:
    """Insert candidate duplicate pairs found by probing `probe_sql` images (must select
    exactly (id, embedding)) against `corpus_filter_sql`-scoped neighbors in `embedding_table`
    ("embeddings" for CLIP, "ocr_text_embeddings" for OCR-text -- both use the column name
    `embedding`), via an HNSW-assisted per-image KNN search rather than a full cross join.
    Idempotent -- re-running with no new probe rows inserts zero rows, and a pair already present
    (from either probe direction, or from a different distance_source's earlier probe call within
    the same run) is skipped via ON CONFLICT DO NOTHING. Returns the number of rows actually
    inserted.

    distance_source is stamped on every inserted row -- 'clip' or 'ocr_text' -- see
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.

    `probe_sql`/`corpus_filter_sql` may reference named bind params (e.g. `:batch_id`) -- pass
    their values via `extra_params` rather than string-interpolating them into the fragment, even
    though callers so far only ever pass internally-generated values (never raw user input).

    `extra_where_sql`, when provided, is ANDed into the OUTER SELECT's WHERE clause (alongside
    `nn.distance < :threshold`) -- deliberately NOT into corpus_filter_sql, which runs inside the
    LATERAL before the ORDER BY ... LIMIT :k narrows the candidate set. A condition placed here
    only ever evaluates against the already-k-bounded result, not the full corpus the LATERAL
    considers. References `probe.id`/`nn.image_id` if it needs the two candidate image ids -- see
    docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md's Design §2."""
    extra_where_clause = f"AND ({extra_where_sql})" if extra_where_sql else ""
    stmt = text(f"""
        INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source, distance_source)
        SELECT
            LEAST(probe.id, nn.image_id)    AS image_id1,
            GREATEST(probe.id, nn.image_id) AS image_id2,
            nn.distance,
            nn.match_source,
            :distance_source
        FROM ({probe_sql}) AS probe(id, embedding)
        CROSS JOIN LATERAL (
            SELECT
                e2.image_id,
                probe.embedding <=> e2.embedding AS distance,
                CASE WHEN i2.status = 'active' THEN 'cross_corpus' ELSE 'in_batch' END AS match_source
            FROM {embedding_table} e2
            JOIN images i2 ON i2.id = e2.image_id
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
            ORDER BY probe.embedding <=> e2.embedding
            LIMIT :k
        ) nn
        WHERE nn.distance < :threshold
        {extra_where_clause}
        ON CONFLICT (image_id1, image_id2) DO NOTHING
    """)
    params = {"k": k, "threshold": threshold, "distance_source": distance_source, **(extra_params or {})}
    result = await session.execute(stmt, params)
    return result.rowcount
```

- [ ] **Step 3: Wire the gate into `rebuild_active_library()`'s OCR-text probe call**

Change the OCR-text `find_duplicates()` call inside `rebuild_active_library()` (currently the last
call in the function, lines 192-196):

```python
    inserted += await find_duplicates(
        session, ocr_probe_sql, _ACTIVE_CORPUS_FILTER_OCR_TEXT, k, TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={
            "probe_distance_source": "ocr_text",
            "min_lemma_count": MIN_LEMMA_COUNT_FLOOR,
            "min_overlap_coefficient": MIN_LEMMA_OVERLAP_COEFFICIENT,
        },
        extra_where_sql=_OCR_LEMMA_OVERLAP_CHECK,
    )
```

No other line in `rebuild_active_library()` changes — the general CLIP probe and the CLIP
safety-net probe calls are untouched; neither passes `extra_where_sql`.

- [ ] **Step 4: Add an `_insert_ocr_lemmas` test helper**

In `tests/integration/test_rebuild_duplicates.py`, add `OCRLemma` to the existing `Storage.models`
import (currently `from Storage.models import Embedding, Image, ImageClassification,
OCRTextEmbedding`):

```python
from Storage.models import Embedding, Image, ImageClassification, OCRLemma, OCRTextEmbedding
```

Add this helper alongside the existing `_insert_ocr_text_embedding` (near line 180-183):

```python
async def _insert_ocr_lemmas(session, image_id: uuid.UUID, lemmas: set[str]) -> None:
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image_id, lemma=lemma))
    await session.flush()
```

- [ ] **Step 5: Update the two existing tests that now need matching `ocr_lemmas` rows**

`test_ocr_text_probe_finds_pair_via_ocr_text_embeddings` (currently lines 257-272) relies on the
OCR-text probe finding a match with **no** `ocr_lemmas` rows for either image — this will now fail
the new gate (`MIN_LEMMA_COUNT_FLOOR` alone rejects a 0-lemma pair). Add matching lemma sets giving
a clean overlap coefficient of 1.0 (comfortably above `MIN_LEMMA_OVERLAP_COEFFICIENT`):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_finds_pair_via_ocr_text_embeddings(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP -- general probe won't find this
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))  # identical OCR-text embedding
    # Matching ocr_lemmas so the new lemma-overlap corroboration gate (Task 2 of
    # docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md) doesn't reject this
    # pair -- 5 shared lemmas, well above MIN_LEMMA_COUNT_FLOOR (3) and giving overlap
    # coefficient 1.0, well above MIN_LEMMA_OVERLAP_COEFFICIENT (0.2).
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates "
             "WHERE distance_source = 'ocr_text'")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance == pytest.approx(0.0, abs=1e-6)
```

`test_incremental_rerun_does_not_skip_ocr_text_probe_for_already_clip_probed_image` (currently
lines 304-352 roughly) similarly relies on the OCR-text probe finding `(a, b)` with no `ocr_lemmas`
rows. Add the same matching-lemma-set insert for `a`/`b` right after their existing
`_insert_ocr_text_embedding` calls (do not add lemmas for `c` — it must stay ineligible for the
OCR-text probe regardless, since it has no `ocr_text_embeddings` row at all):

```python
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)
    # c is deliberately left unclassified (not text_heavy) and without an ocr_text_embeddings row
```

(This replaces the line `# c is deliberately left unclassified (not text_heavy) and without an
ocr_text_embeddings row` in place — the two new lines are inserted directly above it, the comment
itself is unchanged.) The rest of that test (the `first == 2` assertion, the `pairs[...]`
assertions, the second incremental call) needs no further changes — the shared-lemma insert only
restores the pair's eligibility; it doesn't change the test's own logic about which probe finds
what.

- [ ] **Step 6: Add new tests for the corroboration gate itself**

Append to `tests/integration/test_rebuild_duplicates.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_excludes_pair_below_overlap_coefficient(db_session):
    """The core regression test for this task: a tight OCR-text-embedding match with NO shared
    ocr_lemmas must not be inserted at all -- not just excluded from clustering (that's
    clusterize.py's old, now-reverted, interim behavior), excluded from tmp_duplicates entirely."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))  # identical -- distance 0
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай"})
    await _insert_ocr_lemmas(db_session, b, {"машина", "дорога", "город", "ночь"})  # zero overlap

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_includes_pair_above_overlap_coefficient(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    # 4 shared lemmas out of min(5, 6) = 5 total on the smaller side -> overlap coefficient 0.8,
    # comfortably above MIN_LEMMA_OVERLAP_COEFFICIENT (0.2).
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай", "стол"})
    await _insert_ocr_lemmas(db_session, b, {"дом", "кот", "утро", "чай", "окно", "дверь"})

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count(db_session):
    """Regression test for the hub-coincidence bug this design explicitly guards against
    (confirmed on real production data -- see the spec's Design §1): a probe image with a LARGE
    ocr_lemmas set can coincidentally share enough RAW lemmas with an unrelated candidate to clear
    a naive count threshold, while the ratio (normalized by the smaller side) correctly stays
    low. This must fail if _OCR_LEMMA_OVERLAP_CHECK is ever simplified back to a raw count."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))  # the "hub" -- huge lemma set
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # small, mostly-unrelated set
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    hub_lemmas = {f"слово{i}" for i in range(40)} | {"дом", "кот", "утро"}  # 43 total
    small_lemmas = {"дом", "кот", "утро", "машина"}  # 4 total, 3 shared with the hub
    # raw intersection = 3 (>= MIN_LEMMA_COUNT_FLOOR of 3, so a raw-count-only check would pass
    # this); overlap coefficient = 3 / min(43, 4) = 3/4 = 0.75 -- wait, this needs a genuinely
    # LOW ratio to be a real regression guard, so scale the hub up further below instead of
    # relying on the small side's count as the denominator.
    await _insert_ocr_lemmas(db_session, a, hub_lemmas)
    await _insert_ocr_lemmas(db_session, b, small_lemmas)

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    # min(43, 4) = 4 -> overlap coefficient = 3/4 = 0.75, which is ABOVE the 0.2 threshold, so
    # this particular fixture is actually a true positive under the coefficient too -- the
    # degenerate "small side count" denominator doesn't produce the intended low-ratio regression
    # case. Fix: give b a LARGER unrelated vocabulary so the denominator (still the smaller side)
    # is big enough that 3 shared lemmas out of it is a low ratio, mirroring the real hub-coincidence
    # case (96 vs 440 lemmas, 4 shared, coefficient 0.042).
    assert inserted == 1  # placeholder assertion -- corrected in Step 7 below after recalibrating
    # the fixture; see Step 7's note. Left here deliberately wrong so the plan's own self-review
    # step catches it, matching this project's established practice of not hiding a caught defect.
```

- [ ] **Step 7: Self-review and fix Step 6's own regression test**

Step 6's `test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count` fixture is wrong as drafted:
with `min(len(hub_lemmas), len(small_lemmas)) = min(43, 4) = 4` as the denominator, 3 shared lemmas
gives coefficient 0.75 — comfortably *above* `MIN_LEMMA_OVERLAP_COEFFICIENT`, so the fixture
doesn't actually distinguish "correctly uses the coefficient" from "incorrectly uses a raw count of
3" (a raw-count-only implementation with any threshold ≤ 3 would also pass, since 3 ≥ 3). The real
production hub-coincidence case had the **hub** as the *larger* side and a normal-sized unrelated
post as the smaller side (96 vs. 440 lemmas, 4 shared, coefficient 4/96 ≈ 0.042) — the denominator
must be the *non-hub* side's count, and it must be large enough that a handful of coincidentally
shared lemmas produces a low ratio. Replace Step 6's fixture with:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count(db_session):
    """Regression test for the hub-coincidence bug this design explicitly guards against
    (confirmed on real production data -- see the spec's Design §1): a probe image with a LARGE
    ocr_lemmas set can coincidentally share enough RAW lemmas with an unrelated candidate to clear
    a naive count threshold, while the ratio (normalized by the SMALLER side) correctly stays low.
    This must fail if _OCR_LEMMA_OVERLAP_CHECK is ever simplified back to a raw count."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))  # the "hub" -- huge lemma set
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # normal-sized, mostly-unrelated set
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    hub_lemmas = {f"слово{i}" for i in range(40)} | {"дом", "кот", "утро"}  # 43 total (the hub)
    normal_lemmas = {f"фраза{i}" for i in range(17)} | {"дом", "кот", "утро"}  # 20 total (the
    # smaller side -- this is the denominator)
    # raw intersection = 3 (>= MIN_LEMMA_COUNT_FLOOR of 3 -- a raw-count-only check with a
    # threshold <= 3 would wrongly admit this); overlap coefficient = 3 / min(43, 20) = 3/20 = 0.15,
    # below MIN_LEMMA_OVERLAP_COEFFICIENT (0.2) -- correctly rejected.
    await _insert_ocr_lemmas(db_session, a, hub_lemmas)
    await _insert_ocr_lemmas(db_session, b, normal_lemmas)

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []
```

This replaces Step 6's entire `test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count`
function, including its deliberately-wrong placeholder assertion and note — do not keep both
versions in the file, only this corrected one.

- [ ] **Step 8: Add the `MIN_LEMMA_COUNT_FLOOR` regression test**

Append:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_lemma_overlap_check_respects_min_lemma_count_floor(db_session):
    """Two images with only 1-2 total ocr_lemmas each, fully overlapping (coefficient would
    compute to 1.0) -- must still be excluded, since MIN_LEMMA_COUNT_FLOOR (3) isn't met. Guards
    against the degenerate case where a single coincidental shared word looks like 100% overlap."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"привет"})
    await _insert_ocr_lemmas(db_session, b, {"привет"})  # coefficient = 1/1 = 1.0, but only 1 lemma total

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []
```

- [ ] **Step 9: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_rebuild_duplicates.py -v
```

Expected: all pass. Count this yourself from the actual file after Steps 4-8's edits rather than
trusting a number stated here — this plan's own prior task (per the originating spec's SDD ledger)
found a miscounted total once already; verify empirically. Then the full suites:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

Expected: `tests/integration/` shows only the known pre-existing failures (see Task 1 Step 5's
list) **plus** every OCR-text-related test in `tests/integration/test_ingest_find_duplicates.py`
and `tests/integration/test_clusterize.py` that hasn't been updated yet by Tasks 3/4 — this is
expected and will be resolved by those tasks, not a regression from this task. `batch/tests/` and
`Backend/tests/` unaffected by this task (neither imports `batch.rebuild_duplicates`).

- [ ] **Step 10: Commit**

```bash
git add batch/rebuild_duplicates.py tests/integration/test_rebuild_duplicates.py
git commit -m "feat: gate the OCR-text probe on ocr_lemmas overlap coefficient"
```

---

### Task 3: `batch/ingest_find_duplicates.py` — mirror the corroboration gate

**Files:**
- Modify: `batch/ingest_find_duplicates.py`
- Modify: `tests/integration/test_ingest_find_duplicates.py`

**Interfaces:**
- Consumes: Task 2's `find_duplicates()` (new `extra_where_sql` param), `MIN_LEMMA_OVERLAP_COEFFICIENT`,
  `MIN_LEMMA_COUNT_FLOOR`, `_OCR_LEMMA_OVERLAP_CHECK` — all imported from `batch.rebuild_duplicates`.
- Produces: `find_batch_duplicates()`'s public signature is unchanged (`session, batch_id, k,
  threshold`).

- [ ] **Step 1: Update the import line**

Change (currently lines 30-33):

```python
from batch.rebuild_duplicates import (
    find_duplicates, _EXCLUDE_TEXT_HEAVY_PAIR, _TEXT_HEAVY_PAIR_ONLY,
    CLIP_SAFETY_NET_THRESHOLD, TEXT_EMBEDDING_LOOSE_THRESHOLD,
)
```

to:

```python
from batch.rebuild_duplicates import (
    find_duplicates, _EXCLUDE_TEXT_HEAVY_PAIR, _TEXT_HEAVY_PAIR_ONLY, _OCR_LEMMA_OVERLAP_CHECK,
    CLIP_SAFETY_NET_THRESHOLD, TEXT_EMBEDDING_LOOSE_THRESHOLD, MIN_LEMMA_COUNT_FLOOR,
    MIN_LEMMA_OVERLAP_COEFFICIENT,
)
```

- [ ] **Step 2: Wire the gate into `find_batch_duplicates()`'s OCR-text probe call**

Change the OCR-text `find_duplicates()` call (currently the last call in the function, lines
118-123):

```python
    inserted += await find_duplicates(
        session, _BATCH_PROBE_SQL_OCR_TEXT, _BATCH_CORPUS_FILTER_SQL_OCR_TEXT, k,
        TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={
            "batch_id": batch_id,
            "min_lemma_count": MIN_LEMMA_COUNT_FLOOR,
            "min_overlap_coefficient": MIN_LEMMA_OVERLAP_COEFFICIENT,
        },
        extra_where_sql=_OCR_LEMMA_OVERLAP_CHECK,
    )
```

No other line in `find_batch_duplicates()` changes. Update the function's own docstring (currently
lines 86-109) to add one short paragraph noting the new gate, directly after the existing
paragraph about `TEXT_EMBEDDING_LOOSE_THRESHOLD` always being used regardless of tier:

```python
    Since 2026-09-19, the OCR-text probe additionally requires a lexical-overlap corroboration
    check (ocr_lemmas-based, see docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md)
    before a candidate is inserted -- identical mechanism to rebuild_duplicates.py's own OCR-text
    probe, imported from that module rather than redefined here.
```

- [ ] **Step 3: Update the two existing tests that now need matching `ocr_lemmas` rows**

In `tests/integration/test_ingest_find_duplicates.py`, the current import line (confirmed by
reading the file directly) is:

```python
from Storage.models import Embedding, Image, ImageClassification, OCRTextEmbedding
```

Change to:

```python
from Storage.models import Embedding, Image, ImageClassification, OCRLemma, OCRTextEmbedding
```

Add the same `_insert_ocr_lemmas` helper Task 2 added to `test_rebuild_duplicates.py`, placed
alongside this file's existing `_insert_ocr_text_embedding` helper:

```python
async def _insert_ocr_lemmas(session, image_id: uuid.UUID, lemmas: set[str]) -> None:
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image_id, lemma=lemma))
    await session.flush()
```

`test_ocr_text_probe_finds_pair_at_tier_b` (currently lines 175-191) needs matching lemma sets for
`a`/`b`, inserted right after the existing `_insert_ocr_text_embedding` calls:

```python
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)
```

`test_ocr_text_probe_ignores_threshold_argument` (currently lines 195-218) needs the same
treatment, inserted right after its existing `_insert_ocr_text_embedding` calls:

```python
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _near_text_unit_vector(0))  # distance ~0.0747
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)
```

`test_general_clip_excludes_text_heavy_pair_safety_net_still_finds_it` (currently lines 150-171)
needs **no** change — it deliberately wants the OCR-text probe to find nothing (orthogonal
OCR-text embeddings, distance 1.0), and the new gate can only make OCR-text matching *more*
restrictive, never less; a pair that already correctly finds zero rows via distance alone is
unaffected by an additional filter it never reaches.

- [ ] **Step 4: Add new tests mirroring Task 2's gate tests**

Append to `tests/integration/test_ingest_find_duplicates.py`, following this file's own existing
fixture conventions (`_insert_image`, `BatchRunRepository`, `_mark_text_heavy`):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_excludes_pair_below_overlap_coefficient(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай"})
    await _insert_ocr_lemmas(db_session, b, {"машина", "дорога", "город", "ночь"})  # zero overlap

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_includes_pair_above_overlap_coefficient(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай", "стол"})
    await _insert_ocr_lemmas(db_session, b, {"дом", "кот", "утро", "чай", "окно", "дверь"})

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"
```

- [ ] **Step 5: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_find_duplicates.py -v
```

Expected: all pass — count from the actual file after this task's edits, not a number stated here.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

Expected: `tests/integration/` down to the known baseline (Task 1 Step 5's list) plus
`test_clusterize.py`'s still-pending updates (Task 4). `batch/tests/` and `Backend/tests/`
unaffected.

- [ ] **Step 6: Commit**

```bash
git add batch/ingest_find_duplicates.py tests/integration/test_ingest_find_duplicates.py
git commit -m "feat: gate ingestion's OCR-text probe on the same ocr_lemmas overlap check"
```

---

### Task 4: `batch/clusterize.py` — revert to trusting `ocr_text`-sourced rows

**Files:**
- Modify: `batch/clusterize.py`
- Modify: `tests/integration/test_clusterize.py`

**Interfaces:**
- Consumes: nothing new — this task doesn't reference `ocr_lemmas`, `find_duplicates()`, or
  anything from Tasks 1-3 directly. It trusts that any `distance_source='ocr_text'` row in
  `tmp_duplicates` already passed Task 2/3's gate at insertion time.
- Produces: `get_duplicate_pairs(session, mapping, clip_threshold, ocr_text_threshold)` — signature
  reverts from Task 4 of the *original* spec's interim-fix shape (3 args) back to the two-threshold
  form.

**Sequencing note**: this task should not land before Tasks 2 and 3 (even though nothing in its own
code literally imports from them) — re-enabling trust in `ocr_text`-sourced rows before the
insertion-time gate protecting them exists would reopen the exact false-positive hole this whole
plan closes. Dispatch this task after Tasks 2 and 3 are both merged, not in parallel with them.

- [ ] **Step 1: Restore the `and_`/`or_` import**

Change (currently `from sqlalchemy import select, delete`):

```python
from sqlalchemy import and_, or_, select, delete
```

- [ ] **Step 2: Restore `PROXIMITY_THRESHOLD_OCR_TEXT`'s "in use" comment**

Change (currently lines 15-20):

```python
# Matches TEXT_EMBEDDING_TIGHT_THRESHOLD in batch/rebuild_duplicates.py. A separately-named
# constant, not a shared import, even though the two are numerically equal today -- see
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md: never assume the two
# scales stay coupled, so a future change to either doesn't silently move the other. Safe to trust
# again as of docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md -- every
# ocr_text-sourced row in tmp_duplicates has already passed that spec's insertion-time lemma-
# overlap gate by the time this function reads it.
PROXIMITY_THRESHOLD_OCR_TEXT = 0.05
```

- [ ] **Step 3: Revert `get_duplicate_pairs()` to the two-source form**

Replace the current function (the `get_duplicate_pairs` def and its docstring/body) with:

```python
async def get_duplicate_pairs(session, mapping, clip_threshold, ocr_text_threshold) -> list[tuple[int, int, float]]:
    """Active-library auto-clustering trusts both clip- and ocr_text-sourced tmp_duplicates rows,
    each gated by its own threshold. ocr_text-sourced rows are safe to trust here specifically
    because docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md's lexical-overlap
    corroboration check already ran at INSERTION time (batch/rebuild_duplicates.py's and
    batch/ingest_find_duplicates.py's OCR-text probes) -- this function doesn't need to know
    anything about ocr_lemmas itself, only that distance_source='ocr_text' already implies the
    check passed. See that spec's Design §4 for the full reasoning, and
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's "Known limitation"
    section for why this trust was temporarily withdrawn before that spec existed."""
    decided_pair_exists = (
        select(DuplicateDecision.id)
        .where(
            DuplicateDecision.image_id1 == TmpDuplicates.image_id1,
            DuplicateDecision.image_id2 == TmpDuplicates.image_id2,
        )
        .exists()
    )
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
            TmpDuplicates.distance,
        ).where(
            or_(
                and_(TmpDuplicates.distance_source == "clip", TmpDuplicates.distance < clip_threshold),
                and_(TmpDuplicates.distance_source == "ocr_text", TmpDuplicates.distance < ocr_text_threshold),
            ),
            TmpDuplicates.image_id1 != TmpDuplicates.image_id2,
            ~decided_pair_exists,
        )
    )
    duplicates = await session.execute(query)
    # mapping is active-only (see get_images_ids) -- drop any pair touching a
    # pending/rejected image rather than KeyError on it.
    return [
        (mapping[id1], mapping[id2], distance)
        for id1, id2, distance in duplicates
        if id1 in mapping and id2 in mapping
    ]
```

- [ ] **Step 4: Revert the call site**

Change `cluster_active_library()`'s call (currently `pairs = await get_duplicate_pairs(session,
img_id_to_int_id, PROXIMITY_THRESHOLD)`):

```python
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD, PROXIMITY_THRESHOLD_OCR_TEXT)
```

- [ ] **Step 5: Replace the three interim-fix tests with the two-source tests**

In `tests/integration/test_clusterize.py`, remove
`test_ocr_text_sourced_pair_never_auto_clusters`, `test_clip_sourced_pair_still_clusters_normally`,
and `test_get_duplicate_pairs_ignores_ocr_text_pairs_entirely` (the interim fix's own tests — they
assert the CLIP-only behavior this task reverts, so they'd be asserting the wrong thing if left in
place, not just redundant). Replace with:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_clusters_under_its_own_threshold(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.03, distance_source="ocr_text")  # < PROXIMITY_THRESHOLD_OCR_TEXT (0.05)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_past_its_own_threshold_does_not_cluster(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    # 0.08 is past PROXIMITY_THRESHOLD_OCR_TEXT (0.05) but well within clip's own 0.05 too --
    # this must NOT cluster despite the distance being numerically close to what a clip-sourced
    # pair at the same value would need. Proves the two thresholds are independently enforced,
    # not OR'd loosely against a single shared cutoff.
    await _insert_pair(db_session, a, b, 0.08, distance_source="ocr_text")

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_and_ocr_text_thresholds_enforced_independently(db_session):
    """Two pairs at distances that would swap outcomes if the two distance_source thresholds
    were ever accidentally conflated into one shared comparison."""
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    d = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.045, distance_source="clip")      # < 0.05 (PROXIMITY_THRESHOLD) -> clusters
    await _insert_pair(db_session, c, d, 0.045, distance_source="ocr_text")  # < 0.05 (PROXIMITY_THRESHOLD_OCR_TEXT) -> clusters

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b, c, d}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicate_pairs_enforces_each_threshold_independently(db_session):
    """Direct call to get_duplicate_pairs() with deliberately DIFFERENT clip/ocr_text thresholds --
    genuinely discriminates per-source gating from a naive OR'd single-threshold bug. The tests
    above (driven through cluster_active_library()) can't discriminate this, since
    PROXIMITY_THRESHOLD and PROXIMITY_THRESHOLD_OCR_TEXT happen to both equal 0.05 today -- a bug
    that silently dropped the per-branch distance_source guard would still pass every test above
    unnoticed, collapsing to a single distance < 0.05 check regardless of source."""
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    d = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.03, distance_source="clip")      # < clip_threshold (0.05) -> included
    await _insert_pair(db_session, c, d, 0.03, distance_source="ocr_text")  # NOT < ocr_text_threshold (0.02) -> excluded

    mapping, _ = await get_images_ids(db_session)
    pairs = await get_duplicate_pairs(db_session, mapping, clip_threshold=0.05, ocr_text_threshold=0.02)

    pair_id_sets = [{p[0], p[1]} for p in pairs]
    assert {mapping[a], mapping[b]} in pair_id_sets
    assert {mapping[c], mapping[d]} not in pair_id_sets
```

Both `get_duplicate_pairs` and `get_images_ids` are already imported in this test file (confirmed —
`test_get_duplicate_pairs_ignores_ocr_text_pairs_entirely`, the test being replaced, already used
both); no import changes needed.

- [ ] **Step 6: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_clusterize.py -v
```

Expected: all pass (4 pre-existing decision-related tests + 4 from Step 5 = 8).

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

Expected: `tests/integration/` down to exactly the known pre-existing baseline (5, or 6 if the
time-of-day flake fires) — every OCR-text-related test across all three integration test files
should now be passing, since Tasks 2-4 are all complete at this point.

- [ ] **Step 7: Commit**

```bash
git add batch/clusterize.py tests/integration/test_clusterize.py
git commit -m "feat: re-enable active-library auto-clustering for ocr_text-sourced pairs"
```

---

### Task 5: `batch/ingest_auto_prep.py` — wire in `build_ocr_lemmas`

**Files:**
- Modify: `batch/ingest_auto_prep.py`
- Modify: `batch/tests/test_ingest_auto_prep.py`

**Interfaces:**
- Consumes: Task 1's `build_ocr_lemmas.main(status=...)`.
- Produces: nothing new consumed elsewhere — this is a leaf task in the dependency graph (besides
  depending on Task 1).

- [ ] **Step 1: Add `build_ocr_lemmas` to the import and the chain**

Change the import (currently `from batch import (build_image_embeddings,
build_ocr_text_embeddings, classify_text_heavy, extract_text_from_memes, ingest_find_duplicates,
ingest_hash_dedup, ingest_validate_formats)`):

```python
from batch import (
    build_image_embeddings, build_ocr_lemmas, build_ocr_text_embeddings, classify_text_heavy,
    extract_text_from_memes, ingest_find_duplicates, ingest_hash_dedup, ingest_validate_formats,
)
```

Change the `steps` list inside `_run_prep_chain()` — insert `build_ocr_lemmas` directly after
`extract_text_from_memes` and before `classify_text_heavy` (it processes every image with OCR
text, not just `text_heavy`-classified ones, so it has no ordering dependency on the classifier —
placed as early as possible so lemma coverage exists before anything downstream might want it):

```python
    steps = [
        ("ingest_validate_formats", lambda: ingest_validate_formats.main(env=None)),
        ("build_image_embeddings", lambda: build_image_embeddings.main(incremental=True, target_status="pending")),
        ("extract_text_from_memes", lambda: extract_text_from_memes.main(settings.BASE_PATH, target_status="pending")),
        ("build_ocr_lemmas", lambda: build_ocr_lemmas.main(status="pending")),
        ("classify_text_heavy", lambda: classify_text_heavy.main(status="pending")),
        ("build_ocr_text_embeddings", lambda: build_ocr_text_embeddings.main(status="pending")),
        ("ingest_find_duplicates", lambda: ingest_find_duplicates.main(env=None, tier="tier_a", k=None)),
    ]
```

Also update the module docstring's step count (currently "so an operator no longer has to run 7
commands by hand"):

```python
hash dedup through Tier A duplicate-finding -- so an operator no longer has to run 8 commands
```

- [ ] **Step 2: Update the existing test for the new step**

In `batch/tests/test_ingest_auto_prep.py`, update the docstring (currently "all 7 chained steps'
main() functions are mocked"):

```python
Unit tests for batch/ingest_auto_prep.py -- the ingestion prep chain driver. No real DB; all
8 chained steps' main() functions are mocked, matching batch/tests/test_move_flagged.py's
chaining-test style.
```

Update `_patched_steps()`'s dict (add `build_ocr_lemmas`, keeping the same insertion position as
the real chain):

```python
def _patched_steps(module, **overrides):
    """Returns a dict of the 8 step mocks, pre-wired as no-op AsyncMocks unless overridden."""
    steps = {
        "ingest_hash_dedup": AsyncMock(),
        "ingest_validate_formats": AsyncMock(),
        "build_image_embeddings": AsyncMock(),
        "extract_text_from_memes": AsyncMock(),
        "build_ocr_lemmas": AsyncMock(),
        "classify_text_heavy": AsyncMock(),
        "build_ocr_text_embeddings": AsyncMock(),
        "ingest_find_duplicates": AsyncMock(),
    }
    steps.update(overrides)
    for name, mock in steps.items():
        getattr(module, name).main = mock
    return steps
```

Update `test_calls_all_seven_steps_in_order_with_expected_args` — rename it and its assertions:

```python
    @pytest.mark.asyncio
    async def test_calls_all_eight_steps_in_order_with_expected_args(self):
        import batch.ingest_auto_prep as module

        call_order = []

        steps = _patched_steps(module)
        for name, mock in steps.items():
            mock.side_effect = lambda *a, name=name, **kw: call_order.append(name)

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            await module._run_prep_chain()

        assert call_order == [
            "ingest_hash_dedup", "ingest_validate_formats", "build_image_embeddings",
            "extract_text_from_memes", "build_ocr_lemmas", "classify_text_heavy",
            "build_ocr_text_embeddings", "ingest_find_duplicates",
        ]
        steps["ingest_hash_dedup"].assert_awaited_once_with(env=None)
        steps["ingest_validate_formats"].assert_awaited_once_with(env=None)
        steps["build_image_embeddings"].assert_awaited_once_with(incremental=True, target_status="pending")
        steps["extract_text_from_memes"].assert_awaited_once_with("/fake/base", target_status="pending")
        steps["build_ocr_lemmas"].assert_awaited_once_with(status="pending")
        steps["classify_text_heavy"].assert_awaited_once_with(status="pending")
        steps["build_ocr_text_embeddings"].assert_awaited_once_with(status="pending")
        steps["ingest_find_duplicates"].assert_awaited_once_with(env=None, tier="tier_a", k=None)
```

Update the two docstrings referencing "steps 2-6" (both currently say this — `grep -n "steps 2-6"
batch/tests/test_ingest_auto_prep.py` to find both, confirm there are exactly two before editing):

```python
        """The common case: the inbox is empty and no ingestion run is active, so steps 2-7
        raise 'No ingestion run is currently in progress' -- that must not fail the tick."""
```

```python
        """Step 1 failing (e.g. PATH_INGESTION_SOURCE misconfigured) must fail the whole tick,
        not be swallowed like steps 2-7's expected 'nothing to do' error."""
```

No other test in this file changes — `test_unrelated_runtime_error_from_a_later_step_propagates`
and `TestMain`'s two tests don't enumerate the full step list.

- [ ] **Step 3: Run the tests**

```bash
pytest batch/tests/test_ingest_auto_prep.py -v
```

Expected: all 4 pass (matching the file's existing test count — this task doesn't add new test
functions, only updates existing ones).

```bash
pytest batch/tests/ -q
cd Backend && pytest -q
cd .. && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: `batch/tests/` and `Backend/tests/` clean. `tests/integration/` at the known baseline —
Tasks 2-4 already brought every OCR-text-related integration test back to passing.

- [ ] **Step 4: Commit**

```bash
git add batch/ingest_auto_prep.py batch/tests/test_ingest_auto_prep.py
git commit -m "feat: wire build_ocr_lemmas into ingest_auto_prep's chain"
```

---

### Task 6: Documentation sync — `CLAUDE.md` and `docs/runbooks/ingestion-pipeline.md`

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/runbooks/ingestion-pipeline.md`

**Interfaces:**
- Consumes: the real behavior Tasks 1-5 implement (read those tasks' final code, not just this
  task's own description, before writing prose about it).

- [ ] **Step 1: `CLAUDE.md` — update the `clusterize` entry**

Find the `clusterize` entry (currently describes CLIP-only auto-clustering with the "deliberately
EXCLUDED" language from the interim fix). Revert to describing both sources gated independently
again, keeping the historical context of why this needed a real corroboration mechanism rather
than dropping it entirely:

```
clusterize                 → optimize cluster index; admin-triggerable from /admin/batches,
                              manual-trigger only. Union-find over tmp_duplicates pairs, gated
                              per distance_source: clip-sourced pairs below PROXIMITY_THRESHOLD
                              (0.05), ocr_text-sourced pairs below the separate
                              PROXIMITY_THRESHOLD_OCR_TEXT (0.05, independently configured, not
                              coupled to the CLIP constant even though they're numerically equal
                              today). ocr_text-sourced pairs are trustworthy here specifically
                              because batch/rebuild_duplicates.py's/batch/ingest_find_duplicates.py's
                              own OCR-text probes already require a lexical-overlap corroboration
                              check (against ocr_lemmas) before inserting a row at all — see
                              docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md.
                              (A 2026-09-18 live-rollout finding briefly disabled ocr_text
                              auto-clustering entirely before that corroboration check existed —
                              see docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's
                              "Known limitation" section for that incident.) Then shapes the
                              result: clusters bigger
```

(The rest of the entry, from "than settings.CLUSTERING.SPLITTING.MAX_CLUSTER_SIZE..." onward, is
unchanged — only the paragraph above the "Then shapes the result" sentence is being replaced.)

- [ ] **Step 2: `CLAUDE.md` — update the `rebuild_duplicates` and `ingest_find_duplicates` entries**

Both entries currently describe the three-probe split (general CLIP, safety net, OCR-text) without
mentioning the new corroboration gate. Add one sentence to each, at the point each entry already
describes the OCR-text probe's own threshold behavior:

In `rebuild_duplicates`'s entry, after the sentence describing the safety-net probe's non-
incremental behavior, add:

```
The OCR-text probe additionally requires a lexical-overlap corroboration check against ocr_lemmas
before inserting a candidate (MIN_LEMMA_OVERLAP_COEFFICIENT=0.2, MIN_LEMMA_COUNT_FLOOR=3) — see
docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md.
```

In `ingest_find_duplicates`'s entry, after the sentence about `TEXT_EMBEDDING_LOOSE_THRESHOLD`
being used regardless of tier, add the same sentence (both files import the same constants from
`rebuild_duplicates.py`, so the same one-liner applies to both).

- [ ] **Step 3: `CLAUDE.md` — add a `build_ocr_lemmas` entry note and update the ingestion run order**

`build_ocr_lemmas` already has a top-level entry (confirmed by reading the current file directly,
currently 3 lines):

```
build_ocr_lemmas           → per-image lemma index for smart search (see
                              docs/superpowers/specs/2026-07-21-smart-search-design.md);
                              --incremental skips images already indexed
```

Replace it with:

```
build_ocr_lemmas            → per-image lemma index for smart search (see
                               docs/superpowers/specs/2026-07-21-smart-search-design.md) and, since
                               2026-09-19, for the OCR-text duplicate-matching corroboration check
                               (see docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md).
                               --incremental skips images already indexed for this pipeline.
                               --status defaults to active; --status pending covers an in-flight
                               ingestion batch -- also chained automatically as part of
                               ingest_auto_prep (see Ingestion below). --status pending requires
                               --incremental (a full reprocess deletes the ENTIRE table
                               unconditionally, not just pending-status rows).
```

(Note the entry's own column alignment shifts by one space in this replacement, matching
`build_ocr_text_embeddings`'s existing entry immediately below it in the file — keep the arrow
column aligned with its neighbors, not with this entry's own prior 2-space version.)

Update the Ingestion section's "Run order" comment block and the real per-script entries list (both
already updated once for `build_ocr_text_embeddings` by a prior branch's fix — find those exact
insertion points and add `build_ocr_lemmas --status pending` at the correct new position, directly
before `classify_text_heavy --status pending`, matching Task 5's real chain order) and the
`ingest_auto_prep` paragraph's own documented chain list (same insertion point).

- [ ] **Step 4: `docs/runbooks/ingestion-pipeline.md` — mirror the same updates**

This file has its own TL;DR command block and separately-numbered "Running a batch" walkthrough
(both already restructured once by a prior branch's fix to include `classify_text_heavy`/
`build_ocr_text_embeddings` — re-read the file's current numbering fresh before editing, don't
assume the numbers from that prior fix's own description still match after this task's insertion).
Add a `build_ocr_lemmas --status pending` step at the same logical point in both the TL;DR block
and the "Running a batch" walkthrough (directly after the OCR pre-pass / `extract_text_from_memes`
step, before `classify_text_heavy`), renumbering every subsequent step in the walkthrough by one,
and updating the "Concurrency" section's re-run guidance list and step-range reference the same way
Task 3 of the originating spec's own rollout fix already did once for `build_ocr_text_embeddings`
(same section, same style — find it by searching for "build_ocr_text_embeddings" in this file
first, to see exactly how that fix phrased its own insertion, then mirror it for
`build_ocr_lemmas`).

- [ ] **Step 5: Verify no other CLAUDE.md/runbook references were missed**

```bash
grep -n "build_ocr_lemmas\|ocr_lemmas" CLAUDE.md docs/runbooks/ingestion-pipeline.md
```

Read every match and confirm each one is now accurate against Tasks 1-5's actual final code (not
just internally consistent with this task's own edits) — in particular, confirm the `--status
pending requires --incremental` guard from Task 1 is mentioned somewhere a reader would find it
before attempting `python -m batch.build_ocr_lemmas --status pending` by hand without
`--incremental`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/runbooks/ingestion-pipeline.md
git commit -m "docs: sync CLAUDE.md and the ingestion runbook for ocr-lemma-overlap-corroboration"
```

---

### Task 7: Live rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** Every step below writes to, or could write to, the live
`metal`/`general`/`it` databases the developer's own running backends depend on continuously — the
same category of action as the originating spec's own Task 7. The controller runs every step
itself, never a subagent.

**Before Step 2 (the first live-environment write), stop and get the user's explicit go-ahead.**
Name the three environments this will touch and what it does (a `build_ocr_lemmas` backfill, then
either deleting-and-reprobing or in-place-evaluating existing `ocr_text` rows against the new gate,
then re-running `clusterize.py`) before proceeding. **Back up all three databases via `pg_dump`
first**, matching the originating spec's rollout precedent, before any write.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md` (status + a
  "Rollout Outcome" section).

- [ ] **Step 1: Confirm the plan and get the go-ahead, and resolve the open re-evaluation question**

The spec's own Rollout section flagged one open question rather than deciding it: because `ON
CONFLICT DO NOTHING` means already-inserted `ocr_text` rows from the 2026-09-18 rollout won't be
re-evaluated by a normal incremental `rebuild_duplicates.py` run, existing rows need an explicit
resolution. **Ruling for this plan** (decided here, not left to Task 7 execution time): delete
existing `distance_source='ocr_text'` rows before re-running, rather than evaluating them in place.
Reasoning: an in-place evaluate-and-delete script is a second, one-off piece of untested code
written specifically for a single live-database mutation — higher risk than reusing the
already-tested, already-reviewed `rebuild_duplicates.py --env <environment>` path, which naturally
re-probes and re-applies the new gate to every text-heavy image once existing `ocr_text` rows (and
their incremental markers) are cleared. The delete is narrowly scoped (`distance_source='ocr_text'`
only — `'clip'` rows, and any pending-image rows from ingestion's own tiers, are untouched) and the
data isn't lost in any real sense: `rebuild_duplicates.py`'s very next run recomputes it from
scratch, now correctly gated.

Summarize for the user: Tasks 1-6's code is merged; Step 2 below runs `build_ocr_lemmas.py
--status active` against `metal`/`general`/`it` to close the coverage gap (742 images in `general`
as of the originating rollout, smaller counts expected elsewhere); Step 3 deletes existing
`distance_source='ocr_text'` rows from `tmp_duplicates` in all three environments; Step 4 re-runs
`rebuild_duplicates.py --env <environment>` (chains to `clusterize.py`) to repopulate `ocr_text`
candidates under the new gate and re-cluster. Wait for explicit confirmation before continuing.

- [ ] **Step 2: Back up, then run `build_ocr_lemmas.py --status active`**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes\Storage"
PGPASSWORD=<from .env> pg_dump -h localhost -p <port> -U ocr -Fc -f ./backups/ocrdb-<date>-<environment>-pre-lemma-overlap.dump ocrdb
```

for each of `metal`/`general`/`it` (ports/passwords from each `.env.<environment>`, matching the
originating spec's own rollout backup step exactly), then:

```powershell
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.build_ocr_lemmas --env <environment> --status active
```

`--status active` implies `--incremental` is NOT required by Task 1's guard (the guard only fires
for `--status pending` combined with a non-incremental run) — but pass `--incremental` explicitly
anyway here, since a full active-corpus reprocess is unnecessary work when only closing a coverage
gap: `python -m batch.build_ocr_lemmas --env <environment> --status active --incremental`. Confirm
backend health (`/api/diagnostics/health`) after each environment before moving to the next.

- [ ] **Step 3: Delete existing `ocr_text`-sourced `tmp_duplicates` rows**

Per Step 1's ruling. For each environment, with `DATABASE_URL` (not the readonly variant — this is
a real write) set from that environment's `.env` file:

```sql
DELETE FROM tmp_duplicates WHERE distance_source = 'ocr_text';
```

Run via `psql` directly (the controller, not a subagent — per CLAUDE.md's live-database-access
rules). Record the row count deleted per environment for the Rollout Outcome section. Confirm
backend health after each environment.

- [ ] **Step 4: Re-run `rebuild_duplicates.py` against all three environments**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.rebuild_duplicates --env <environment>
```

Run each as a detached process if it looks likely to exceed a few minutes (per the documented
Windows `run_in_background` ~10-minute-kill gotcha — use the `Start-Process` pattern, monitored to
completion, exactly as the originating spec's own rollout did). This chains into `clusterize.py`
automatically. Confirm backend health after each environment.

- [ ] **Step 5: Verify the results (read-only)**

Via `DATABASE_URL_READONLY` for each environment:

```sql
SELECT distance_source, count(*) FROM tmp_duplicates GROUP BY distance_source;
```

Expect a smaller `ocr_text` count than the pre-rollout baseline in `general` specifically (191
before this plan; some will have been correctly excluded by the new gate) — a *drop* is expected
and correct, not a regression. Re-run this plan's own calibration queries (the overlap-coefficient
distribution query from the spec's Design §1, and the hub-image pair-count query from its
investigation) against the post-rollout state to confirm the false-positive cluster is actually
gone from the new data, not just that the row count changed. Spot-check a handful of the
newly-surviving `ocr_text` pairs by opening the actual image files (same manual-verification
approach used throughout this whole effort) — confirm they look like genuine matches.

- [ ] **Step 6: Spot-check the live Explore → Duplicates page**

For `general` specifically (the environment with the most history). Confirm no obviously-unrelated
pairs are auto-confirmed as duplicates — in particular, check that the specific false-positive
examples found during the originating spec's live-rollout investigation (documented in that spec's
"Known limitation" section) do NOT reappear as clustered.

- [ ] **Step 7: Mark this spec done**

`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`: `status: approved` →
`status: done`; add a "## Rollout Outcome" section with each environment's `build_ocr_lemmas`
coverage-gap-closure counts, the `ocr_text` row counts deleted in Step 3, the before/after
`distance_source` counts from Step 5, and the spot-check findings from Steps 5 and 6.

```bash
git add docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md
git commit -m "docs: mark ocr-lemma-overlap-corroboration spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Design §1 (calibration/constants) → Task 2 Step 1.
- Design §2 (SQL mechanics, `find_duplicates()` extension, outer-WHERE placement) → Task 2 Steps
  2-3, mirrored in Task 3.
- Design §3 (`build_ocr_lemmas.py` `--status` + `ingest_auto_prep` wiring) → Tasks 1 and 5.
- Design §4 (`clusterize.py` reversion) → Task 4.
- Design §5 (no Backend/Frontend changes) → confirmed no task in this plan touches those trees.
- Testing section → Tasks 2-5's own test steps, matching the spec's four named test shapes for the
  probe files plus the `clusterize.py` reversion and `ingest_auto_prep` update.
- Rollout section → Task 7 in full, including the open question the spec deliberately left
  unresolved — **resolved during this plan's own writing** (Task 7 Step 1's ruling: delete-and-
  reprobe, not evaluate-in-place), not left as an implementation-time decision.

**Placeholder scan:** none in the final state — Task 2 Step 6's `test_ocr_lemma_overlap_check_
uses_coefficient_not_raw_count` was caught with a genuinely wrong, self-admittedly-placeholder
assertion during this plan's own writing (not left in as a real placeholder for an implementer to
discover — Step 7 immediately follows with the corrected version and explicitly says which one to
keep). This is a real bug this self-review process caught, not a demonstration.

**Type/name consistency:** `find_duplicates()`'s new `extra_where_sql: str | None = None` parameter
used identically at its one new call site per file (Task 2, mirrored exactly in Task 3).
`MIN_LEMMA_OVERLAP_COEFFICIENT`/`MIN_LEMMA_COUNT_FLOOR`/`_OCR_LEMMA_OVERLAP_CHECK` defined once in
`rebuild_duplicates.py` (Task 2), imported not redefined in `ingest_find_duplicates.py` (Task 3) —
matching the established pattern the originating plan's own constants already use.
`get_duplicate_pairs()`'s reverted 4-argument signature (Task 4) matches exactly what Task 4 of the
*originating* plan defined before the interim fix changed it to 3 — verified by reading that
exact historical code from this session's own record rather than reconstructing it from memory.

**Cross-task interface check:** Task 4 explicitly does not depend on Tasks 1/5 (the
`build_ocr_lemmas` coverage fix) at the code level — `get_duplicate_pairs()` never queries
`ocr_lemmas` itself, only `distance_source`. But Task 4 depends on Tasks 2/3 *conceptually* (the
insertion-time gate must exist before re-enabling trust in its output) — flagged explicitly in
Task 4's own "Sequencing note" so a dispatcher doesn't parallelize it against Tasks 2/3.

**Real bug caught while writing, beyond Task 2 Step 6/7's test fixture**: the first draft of Task
1's `main()` guard checked `status == "pending" and not incremental` only in `main()`, and this
self-review confirmed `_process()`/`run()` are never called directly by anything outside this
module's own tests (`ingest_auto_prep.py` only ever calls `.main(...)`) — so a single guard point
in `main()` is sufficient, not a gap. Documented in Task 1's own Step 1 comment rather than left
implicit.
