# Ingestion Review Progress Count Query Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 2 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** Replace `IngestionRepository`'s two `get_run_status` support queries
(`count_unreviewed_subjects`, `unreviewed_subject_ids`) — currently 3 DB round trips per call — with one
consolidated `get_review_progress` method, one round trip, same `tier_remaining`/`blocked_total`
semantics. Re-measure the real cost win live against a running environment once the change lands.

**Architecture:** A single 4-branch (`2 sides × 2 tiers`) CTE computes both numbers via
`COUNT(DISTINCT subject_id) FILTER (WHERE tier = :current_tier)` and a plain `COUNT(DISTINCT subject_id)`
in one query, replacing three separate queries and a Python-side set union. Task 1 implements this,
proved entirely against the disposable test database — pure query/method consolidation, no schema
change, no migration. Task 2 — live before/after re-measurement — is controller-executed only, read-only
(`DATABASE_URL_READONLY`), no live writes, so it needs no user go-ahead gate the way a live schema change
would.

**Tech Stack:** SQLAlchemy Core (`text()` SQL, `SET LOCAL work_mem`), pytest-asyncio integration tests
against a real Postgres test database.

**Spec:** `docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md`

## Global Constraints

- The new method's name is `get_review_progress`, signature
  `(self, batch_id, current_tier: str, tier_a_low: float, tier_a_high: float, tier_b_low: float, tier_b_high: float) -> tuple[int, int]`,
  returning `(tier_remaining, blocked_total)` in that order — exact names and order, since the service
  unpacks it positionally.
- `count_unreviewed_subjects` and `unreviewed_subject_ids` are **removed entirely**, not deprecated or
  kept alongside the new method — confirmed via `grep` that `IngestionService.get_run_status` is their
  only caller anywhere in `Backend/`/`batch/`. Leaving both old methods in place alongside the new one
  would be exactly the "two enforcement points for the same predicate, only one path actually used"
  anti-pattern this repo's own prior review flagged elsewhere (see
  `docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md`'s Design §2 rationale for
  removing dead duplicate-authority code, not just adding new code beside it).
- The new query's `SELECT ... AS tier_remaining, ... AS blocked_total` labels **must match** what
  `row.tier_remaining`/`row.blocked_total` reads — SQLAlchemy's `Row` object exposes result columns by
  their label, so a label typo is a silent-at-import, loud-at-runtime `AttributeError`, not a type error.
- `SET LOCAL work_mem = '256MB'` before the query, transaction-scoped (never outlives the request) — same
  pattern `list_tier_b_review_page` already uses in this same file, same proven-safe reasoning (see that
  method's own comment and `tests/integration/test_ingestion_tier_b_review.py::test_query_a_session_tuning_does_not_leak_past_the_transaction`).
  **Do not** add `random_page_cost` tuning — already tried and rejected elsewhere in this file after live
  measurement showed no real win; not re-litigated here.
- No response-shape change, no API contract change, no frontend change — `get_run_status`'s return dict
  keeps identical keys/semantics for `tier_remaining`/`blocked_total`. This plan is entirely internal to
  `Backend/app/repositories/ingestion_repository.py` and `Backend/app/services/ingestion_service.py`.
- **Never hand a subagent the main `DATABASE_URL` for any of `general`'s/`metal`'s/`it`'s environments.**
  Task 1 uses only the disposable test database
  (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`). Task 2 (live re-measurement)
  is controller-only and read-only (`DATABASE_URL_READONLY`) for exactly this reason.
- Per this repo's testing gotcha: never combine `Backend/tests/` and `tests/integration/` in one `pytest`
  invocation — run them as separate commands.

---

## File Structure

**Modify:** `Backend/app/repositories/ingestion_repository.py` (replace 2 methods with 1).
**Modify:** `Backend/app/services/ingestion_service.py` (`get_run_status`, 3 calls → 1).
**Modify:** `Backend/tests/test_ingestion_service.py` (`TestGetRunStatus`, mock the new method).
**Modify:** `tests/integration/test_ingestion_review_progress.py` (repository-level tests re-targeted at
`get_review_progress`; the existing end-to-end service test is untouched).
**Docs — modify:** the spec's status line (final task, Task 2).

---

## Task 1: Consolidate the repository/service query path

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py:233-296` (replace `count_unreviewed_subjects`
  and `unreviewed_subject_ids` with `get_review_progress`)
- Modify: `Backend/app/services/ingestion_service.py:129-137` (`get_run_status`, one call instead of
  three)
- Modify: `Backend/tests/test_ingestion_service.py:360-431` (`TestGetRunStatus`)
- Modify: `tests/integration/test_ingestion_review_progress.py:34-119` (repository-level tests; the file's
  final test, `test_get_run_status_end_to_end_real_db` at lines 122-149, is untouched)

**Interfaces:**
- Produces: `IngestionRepository.get_review_progress(batch_id, current_tier, tier_a_low, tier_a_high, tier_b_low, tier_b_high) -> tuple[int, int]`
  — `(tier_remaining, blocked_total)`.
- Consumes: nothing new — `_tier_band`/`_tier_for_stage` (`Backend/app/services/ingestion_service.py:17-37`)
  are unchanged and already compute everything this task's service-side call needs.

- [ ] **Step 1: Write the failing repository integration tests**

In `tests/integration/test_ingestion_review_progress.py`, replace the block from
`test_count_unreviewed_subjects_excludes_reviewed_and_rejected` (line 34) through the end of
`test_unreviewed_subject_ids_union_dedupes_a_subject_open_in_both_tiers` (line 119) — i.e. all 5 tests
between the `_pair` helper and `test_get_run_status_end_to_end_real_db` — with:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_excludes_reviewed_and_rejected(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    rejected = await _img(db_session, "rejected", bid)
    p4 = await _img(db_session, "pending", bid)
    p5 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.10)                        # open -> both count
    await _pair(db_session, p3, rejected, 0.12)                  # other side rejected -> excluded
    await _pair(db_session, p4, p5, 0.11, tier_b_reviewed=True)  # already reviewed -> excluded
    # NOTE: p4/p5's pair is a SEPARATE pair from p1/p2's -- uq_tmp_duplicates_pair is a real DB
    # unique constraint on (image_id1, image_id2), so a single pair of images can never have both
    # an open row and an already-reviewed row at once; use distinct images to test each exclusion.

    repo = IngestionRepository(db_session)
    tier_remaining, blocked_total = await repo.get_review_progress(
        bid, "tier_b", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert tier_remaining == 2  # p1, p2 -- p3 excluded (rejected other side), p4/p5 excluded (already reviewed)
    assert blocked_total == 2   # same open set; no tier_a activity in this test


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_tier_a_excludes_tier_a_reviewed(db_session):
    # Carries forward the case the progress-visibility branch's final review added (its own
    # Finding #1): the tier_a branch's reviewed-column handling must look only at
    # tier_a_reviewed_at, never tier_b_reviewed_at. A single batch run -- only one active
    # "ingestion" run is allowed at a time (ix_batch_runs_one_active_per_kind) -- so both halves
    # of this assertion share one bid, using distinct image pairs (uq_tmp_duplicates_pair forbids
    # reusing the same pair twice anyway).
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02, tier_a_reviewed=True)  # tier A band, reviewed for tier A

    p3 = await _img(db_session, "pending", bid)
    p4 = await _img(db_session, "pending", bid)
    await _pair(db_session, p3, p4, 0.02, tier_b_reviewed=True)  # same band, reviewed for tier B only

    repo = IngestionRepository(db_session)
    tier_remaining, _ = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert tier_remaining == 2  # p3, p4 only -- p1/p2 excluded (tier_a-reviewed)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_respects_band_and_tier(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)  # tier A band, not tier B

    repo = IngestionRepository(db_session)
    tier_a_remaining, _ = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    tier_b_remaining, _ = await repo.get_review_progress(
        bid, "tier_b", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert tier_a_remaining == 2
    assert tier_b_remaining == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_blocked_total_dedupes_a_subject_open_in_both_tiers(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)   # tier A band
    await _pair(db_session, p1, p3, 0.10)   # tier B band -- p1 open in BOTH tiers

    repo = IngestionRepository(db_session)
    _, blocked_total = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert blocked_total == 3   # p1, p2, p3 -- p1 counted once despite being open in both bands
```

Note: the old `test_unreviewed_subject_ids_returns_the_actual_ids` test is intentionally **not** ported
— it asserted the old method's raw-id-set return shape, which `get_review_progress` no longer has (it
returns counts, not ids). The scenario it exercised (an `active`-status image connected via a pair never
becoming a "subject" itself) is a property of the `s.status = 'pending'` predicate, which every remaining
test already exercises implicitly through its all-pending subject setup. `test_get_run_status_end_to_end_real_db`
(lines 122-149 of the same file, below where this block ends) is untouched — it already goes through
`IngestionService.get_run_status`, not the removed methods directly, and should keep passing unmodified
once Task 1 is complete.

- [ ] **Step 2: Run to verify RED**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_review_progress.py -v
```

Expected: the 4 new tests FAIL with `AttributeError: 'IngestionRepository' object has no attribute 'get_review_progress'`. `test_get_run_status_end_to_end_real_db` still PASSES (it goes through the service, which hasn't changed yet — the old repo methods it transitively calls still exist at this point).

- [ ] **Step 3: Replace the two repository methods with `get_review_progress`**

In `Backend/app/repositories/ingestion_repository.py`, replace lines 233-296 (the full bodies of
`count_unreviewed_subjects` and `unreviewed_subject_ids`, from `async def count_unreviewed_subjects`
through the `return {r.subject_id for r in rows}` that ends `unreviewed_subject_ids`) with:

```python
    async def get_review_progress(
        self, batch_id, current_tier: str,
        tier_a_low: float, tier_a_high: float,
        tier_b_low: float, tier_b_high: float,
    ) -> tuple[int, int]:
        """Live progress counts for the ingestion review banner: (tier_remaining, blocked_total).
        tier_remaining is the count of distinct pending subjects with an open (unreviewed, other
        side not rejected) candidate pair in `current_tier`'s band. blocked_total is the same
        count unioned across BOTH tiers -- a subject open in both bands counts once, never a sum
        (see docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md's
        "don't reuse get_blocked_pending_ids" note). One round trip, one scan of each tier's
        band, via COUNT(DISTINCT ...) FILTER instead of materializing id sets and unioning them
        in Python -- see
        docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md."""
        assert current_tier in ("tier_a", "tier_b"), f"unknown tier: {current_tier!r}"
        sql = text("""
            WITH open_pairs AS (
                SELECT 'tier_a' AS tier, td.image_id1 AS subject_id
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id1 JOIN images o ON o.id = td.image_id2
                WHERE td.tier_a_reviewed_at IS NULL AND td.distance >= :ta_low AND td.distance < :ta_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_a', td.image_id2
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id2 JOIN images o ON o.id = td.image_id1
                WHERE td.tier_a_reviewed_at IS NULL AND td.distance >= :ta_low AND td.distance < :ta_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_b', td.image_id1
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id1 JOIN images o ON o.id = td.image_id2
                WHERE td.tier_b_reviewed_at IS NULL AND td.distance >= :tb_low AND td.distance < :tb_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_b', td.image_id2
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id2 JOIN images o ON o.id = td.image_id1
                WHERE td.tier_b_reviewed_at IS NULL AND td.distance >= :tb_low AND td.distance < :tb_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
            )
            SELECT
                count(DISTINCT subject_id) FILTER (WHERE tier = :current_tier) AS tier_remaining,
                count(DISTINCT subject_id) AS blocked_total
            FROM open_pairs
        """)
        # Scoped to this request's transaction only -- SET LOCAL never outlives it (see
        # get_async_db). Same reasoning as list_tier_b_review_page: this aggregate sorts/merges
        # up to ~210k rows (both tiers, both UNION ALL directions) before the final count -- at
        # Postgres's stock 4MB work_mem that can partially spill to disk. 256MB keeps it in
        # memory -- measured live on general's real batch: 472ms -> 397ms.
        await self.session.execute(text("SET LOCAL work_mem = '256MB'"))
        row = (await self.session.execute(sql, {
            "ta_low": tier_a_low, "ta_high": tier_a_high,
            "tb_low": tier_b_low, "tb_high": tier_b_high,
            "batch_id": batch_id, "current_tier": current_tier,
        })).one()
        return row.tier_remaining, row.blocked_total
```

- [ ] **Step 4: Run the repository/integration tests to verify GREEN**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_review_progress.py -v
```

Expected: the 4 new tests PASS. `test_get_run_status_end_to_end_real_db` now FAILS — this is expected at
this point, since `IngestionService.get_run_status` (not yet updated) still calls the two now-removed
repository methods and will raise `AttributeError`. Step 6 below fixes this.

- [ ] **Step 5: Update `IngestionService.get_run_status` to call the new method**

In `Backend/app/services/ingestion_service.py`, replace lines 129-137:

```python
        current_tier = _tier_for_stage(run.stage)
        if current_tier is not None:
            low, high = _tier_band(current_tier)
            tier_remaining = await self.repo.count_unreviewed_subjects(resolved_id, current_tier, low, high)
            tier_a_low, tier_a_high = _tier_band("tier_a")
            tier_b_low, tier_b_high = _tier_band("tier_b")
            blocked_ids = await self.repo.unreviewed_subject_ids(resolved_id, "tier_a", tier_a_low, tier_a_high)
            blocked_ids |= await self.repo.unreviewed_subject_ids(resolved_id, "tier_b", tier_b_low, tier_b_high)
            blocked_total = len(blocked_ids)
```

with:

```python
        current_tier = _tier_for_stage(run.stage)
        if current_tier is not None:
            tier_a_low, tier_a_high = _tier_band("tier_a")
            tier_b_low, tier_b_high = _tier_band("tier_b")
            tier_remaining, blocked_total = await self.repo.get_review_progress(
                resolved_id, current_tier, tier_a_low, tier_a_high, tier_b_low, tier_b_high)
```

Leave the surrounding `tier_remaining = None` / `blocked_total = None` initialization (lines 127-128,
before this block) and the `else` fallthrough untouched — both still apply exactly as before when
`current_tier is None`.

- [ ] **Step 6: Run the integration tests again to confirm `test_get_run_status_end_to_end_real_db` is GREEN**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_review_progress.py -v
```

Expected: all 5 tests in this file PASS now (the 4 from Step 1 plus the untouched end-to-end test).

- [ ] **Step 7: Update `Backend/tests/test_ingestion_service.py`'s `TestGetRunStatus`**

Replace lines 360-431 (the entire `TestGetRunStatus` class body, from
`class TestGetRunStatus:` through the last line of `test_promoted_stage_still_computes_tier_b_remaining`)
with:

```python
class TestGetRunStatus:
    async def test_no_tier_stage_returns_none_progress(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="hash_dedup", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] is None
        assert status["blocked_total"] is None
        mock_repo.get_review_progress.assert_not_called()

    async def test_tier_a_review_computes_tier_a_remaining(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="tier_a_review", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.get_review_progress.return_value = (7, 9)

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] == 7
        assert status["blocked_total"] == 9
        mock_repo.get_review_progress.assert_awaited_once()
        # Continues the progress-visibility branch's final-review precedent (its own Finding #2):
        # assert the FULL call-args tuple (current_tier + all 4 band bounds), derived from the
        # real _tier_band(), not hardcoded -- a transposed (low, high) or a tier_a/tier_b band
        # mix-up would otherwise pass undetected.
        tier_a_low, tier_a_high = _tier_band("tier_a")
        tier_b_low, tier_b_high = _tier_band("tier_b")
        assert mock_repo.get_review_progress.call_args.args[1:] == (
            "tier_a", tier_a_low, tier_a_high, tier_b_low, tier_b_high)

    async def test_blocked_total_reflects_repo_result(self, service, mock_repo):
        # Renamed from test_blocked_total_unions_both_tiers: the union-across-both-tiers
        # arithmetic now lives entirely inside get_review_progress's own SQL (see its integration
        # test test_get_review_progress_blocked_total_dedupes_a_subject_open_in_both_tiers in
        # tests/integration/test_ingestion_review_progress.py) -- this unit test, being fully
        # mocked, can only confirm the service passes the repo's tuple through unchanged, not
        # that the union itself is correct; that correctness now belongs to the integration test.
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="tier_b_review", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.get_review_progress.return_value = (3, 5)

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] == 3
        assert status["blocked_total"] == 5
        tier_a_low, tier_a_high = _tier_band("tier_a")
        tier_b_low, tier_b_high = _tier_band("tier_b")
        assert mock_repo.get_review_progress.call_args.args[1:] == (
            "tier_b", tier_a_low, tier_a_high, tier_b_low, tier_b_high)

    async def test_promoted_stage_still_computes_tier_b_remaining(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="promoted", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.get_review_progress.return_value = (0, 0)

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] == 0
        tier_a_low, tier_a_high = _tier_band("tier_a")
        tier_b_low, tier_b_high = _tier_band("tier_b")
        assert mock_repo.get_review_progress.call_args.args[1:] == (
            "tier_b", tier_a_low, tier_a_high, tier_b_low, tier_b_high)
```

- [ ] **Step 8: Run the full backend suite**

```bash
cd Backend && pytest -q
```

Expected: PASS, same total count as before this task minus 1 (5 old `TestGetRunStatus` tests → 4 new
ones is a net -1; no other file in this suite is touched).

- [ ] **Step 9: Run the full integration sweep**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: PASS. Per this repo's "run the whole root, not just the file that looks related" rule —
`ingestion_repository.py` is shared code, so the full root runs here, not just
`test_ingestion_review_progress.py`.

- [ ] **Step 10: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py Backend/app/services/ingestion_service.py \
        Backend/tests/test_ingestion_service.py tests/integration/test_ingestion_review_progress.py
git commit -m "perf: consolidate ingestion review progress counts into one query

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 2: Live re-measurement + mark the spec done — CONTROLLER-ONLY

**This task is not a subagent dispatch.** It reads live `general` (or whichever environment has an
active ingestion run at the time) via `DATABASE_URL_READONLY` — read-only, no writes, so it doesn't need
the same explicit-go-ahead gate a live schema migration would, but per `CLAUDE.md`'s "Live database
access for agents" and this repo's own established pattern this session, the controller runs it directly
rather than delegating to a subagent.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md` (status + a
  "Measured outcome" section with the post-implementation numbers).

- [ ] **Step 1: Live re-measurement on whichever environment has an active ingestion run**

This spec's design-time measurement (472ms unindexed / 397ms with `work_mem` tuning, against `general`'s
`run_id 7f16392b…` batch) used hand-written SQL identical in shape to what Task 1 actually implemented,
but re-confirm against the real shipped method, since that batch's review progress — and therefore its
unreviewed fraction and query cost — will have moved on by the time this task runs.

Find the active run's batch id (e.g. `curl http://localhost:8082/api/ingestion/run-status` if a run is
active on `general`, or the equivalent for another environment). Run `EXPLAIN (ANALYZE, BUFFERS)` on
`get_review_progress`'s exact SQL (the query in Task 1 Step 3), bound to the real batch id, both tier
bands, and a real `current_tier` value, via `DATABASE_URL_READONLY` — same connection-string handling as
this spec's own design-time measurement (`DATABASE_URL_READONLY` from `environments/.env.<environment>`,
`+asyncpg` stripped for `psql`). Record the plan shape and execution time, with and without
`SET work_mem = '256MB'` set on the session first (matching what the shipped method does with
`SET LOCAL`).

If no environment currently has an active ingestion run, note that in the spec update and reuse this
spec's own design-time numbers (already real, already measured, just not re-confirmed post-implementation)
rather than fabricating a new figure.

- [ ] **Step 2: Record the outcome and mark the spec done**

`docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md`: `status: approved` →
`status: done`; add `Plan: docs/superpowers/plans/2026-09-13-ingestion-review-progress-count-query.md`
under the status line; add a short "## Measured outcome" section (below "## Rollout") with Step 1's
numbers and plan shape — same treatment the sibling partial-index spec's own "Measured outcome" section
got.

```bash
git add docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md
git commit -m "docs: mark ingestion review progress count query spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (`get_review_progress`, retiring the two old methods) → Task 1 Steps 1-4.
- Design §2 (`get_run_status`, one call instead of three) → Task 1 Steps 5-6.
- Interface change section (old methods removed, new signature) → Global Constraints + Task 1 Step 3.
- Testing: correctness/no-behavior-change → Task 1 Steps 1-9 (full repository test-suite parity, plus the
  full backend + integration sweeps); the mocked service tests' full-call-args assertion → Task 1 Step 7;
  live before/after measurement → Task 2 Step 1.
- Non-goals respected: no new index, no `random_page_cost` change, no caching, no response-shape/API/
  frontend change, no touch to `list_tier_b_review_page`'s Query A/B — this plan touches exactly the two
  named repository methods, the one service method, and their direct test files.
- The spec's own live-database-access rule is operationalized as Task 2's header (read-only,
  controller-only) — not left as prose the executor has to remember to apply.

**Placeholder scan:** none. Every code step has literal, complete content (the full `get_review_progress`
body in Task 1 Step 3, the full replaced `TestGetRunStatus` class in Task 1 Step 7, the full 4 new
integration tests in Task 1 Step 1) — no "similar to X," no "add appropriate tests," no unresolved names.

**Type consistency:** `get_review_progress`'s signature and its `(tier_remaining, blocked_total)` return
order are identical everywhere they appear — the repository definition (Task 1 Step 3), the service call
site (Task 1 Step 5), and every test that calls it (Task 1 Steps 1 and 7). The dropped
`test_unreviewed_subject_ids_returns_the_actual_ids` test is called out explicitly as an intentional,
reasoned omission (Task 1 Step 1's closing note), not a silent gap a later reader would have to notice on
their own.
