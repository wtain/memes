# Ingestion Review — Progress Count Query Consolidation

status: planned
Plan: docs/superpowers/plans/2026-09-13-ingestion-review-progress-count-query.md
Originates from: docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md's
final whole-branch review (2026-09-13) — Important finding #3, deferred there as a tracked follow-up
rather than reopening that branch. No separate review file exists for this finding beyond the branch's
own review report (`docs/superpowers/reviews/2026-09-13-ingestion-review-progress-visibility-review.md`);
the reasoning below extends it with fresh live measurement of the actual fix, not just the problem.
Related: docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md — same root cause
class (an aggregate query over `tmp_duplicates` that a partial index doesn't yet help, because the live
batches are still mostly unreviewed) affecting a *different* query. This spec explicitly does not add a
new index (see Non-goals); it leans on that spec's own measured conclusion instead of re-deriving it.

## Problem

`IngestionService.get_run_status` (`Backend/app/services/ingestion_service.py:121-148`) computes
`tier_remaining`/`blocked_total` via three separate repository round trips whenever the run has an
active review tier:

```python
tier_remaining = await self.repo.count_unreviewed_subjects(resolved_id, current_tier, low, high)
tier_a_low, tier_a_high = _tier_band("tier_a")
tier_b_low, tier_b_high = _tier_band("tier_b")
blocked_ids = await self.repo.unreviewed_subject_ids(resolved_id, "tier_a", tier_a_low, tier_a_high)
blocked_ids |= await self.repo.unreviewed_subject_ids(resolved_id, "tier_b", tier_b_low, tier_b_high)
blocked_total = len(blocked_ids)
```

Each of these three queries (`Backend/app/repositories/ingestion_repository.py:233-296`) independently
scans `tmp_duplicates` for its own tier's band. When the run's current tier is `tier_b` — the common
case, since Tier B is where reviewers spend most of their time — the tier_b band gets scanned **twice**:
once by `count_unreviewed_subjects` for the count, again by `unreviewed_subject_ids` for the union that
feeds `blocked_total`.

This endpoint is now called far more often than before: the progress-visibility feature this finding
came from (see Originates from above) deliberately widened `get_run_status`'s call frequency — every
page load *and* every partial submit, not just when the queue empties — specifically to fix "the numbers
rarely change." Making the numbers refresh more often means this query's cost is now paid far more
often, which is what makes fixing it worth doing now rather than deferring again.

**Measured live** against `general`'s real active batch (`run_id 7f16392b…`, stage `tier_b_review`,
953,084 total `tmp_duplicates` rows, ~7,369 pending images in the batch) via
`EXPLAIN (ANALYZE, BUFFERS)` over `DATABASE_URL_READONLY` (read-only role, controller-run, never handed
to a subagent — see this repo's live-database-access rule):

- `unreviewed_subject_ids(batch_id, "tier_b", 0.05, 0.30)` alone: **~500ms**, a `Parallel Seq Scan` on
  `tmp_duplicates` — it does not use `idx_tmp_duplicates_tier_b_unreviewed_distance` (the existing
  partial index), for the same reason the sibling partial-index spec's own "Measured outcome" documents:
  96%+ of the table is still unreviewed at this point in the batch's review lifecycle, so a sequential
  scan is genuinely cheaper for the planner right now.
- `count_unreviewed_subjects(batch_id, "tier_b", 0.05, 0.30)` — structurally the same query (identical
  predicates and joins, different aggregate), same cost profile.
- `unreviewed_subject_ids(batch_id, "tier_a", 0.0, 0.05)` — cheap by comparison: tier_a's band is much
  smaller live (~3.4k rows), and Postgres already picks an index scan on the plain `idx_tmp_duplicates_distance`
  index for it.

A single `get_run_status(current_tier="tier_b")` call therefore spends roughly
**2 × (~500ms) + one fast query ≈ 1000ms+ of database time across 3 round trips**, before any
network/Python overhead for materializing and unioning two full UUID sets.

## Goal

Cut `get_run_status`'s progress-count cost to a single round trip that computes both `tier_remaining`
and `blocked_total` together, with identical semantics to today.

## Non-goals

- **Any new Postgres index** (a tier_a-equivalent partial index, or a broader one). Per the sibling
  partial-index spec's own measured conclusion, an index only pays off once a tier's unreviewed fraction
  drops low enough for the planner to prefer it over a sequential scan — every live environment
  (`metal`, `general`, `it`) is at 96%+ unreviewed on its active batch today, so a new index would sit
  unused, same as the existing tier_b one does right now. Revisit only if a batch's review progress and
  a fresh measurement show otherwise.
- **`random_page_cost` tuning.** Already tried and explicitly rejected elsewhere in this exact
  repository (`ingestion_repository.py`'s comment on `list_tier_b_review_page`'s `SET LOCAL work_mem`)
  after careful, repeated live measurement showed no real improvement over the sequential-scan plan —
  not re-litigated here.
- **Caching, memoizing, or incrementally maintaining these counts** (e.g. a running counter written
  into `batch_runs.stats`). This is precisely the pattern the progress-visibility feature was built to
  replace (`stats` written once at intake, never recomputed — "the numbers rarely change"). Reintroducing
  a cached counter anywhere in this path risks the same staleness bug for a different reason. Live,
  recomputed-per-request counts stay the contract; this spec only makes computing them cheaper.
- **Any change to `get_run_status`'s response shape**, `tier_remaining`/`blocked_total`'s semantics, or
  any caller (`Backend/app/api/ingestion.py`, the frontend `IngestionReviewPage.tsx`). Purely an internal
  repository/service implementation change — same inputs, same outputs, faster path underneath.
- `list_tier_b_review_page`'s Query A/Query B (the review-queue pagination queries themselves) — separate
  code, already covered by their own specs, untouched here.

## Key facts this rests on

- `count_unreviewed_subjects`/`unreviewed_subject_ids` have exactly **one caller each**:
  `IngestionService.get_run_status` — confirmed via `grep -rn` across `Backend/` and `batch/`, not
  assumed. Safe to retire both in favor of one combined method without touching any other code path.
- Both existing methods already share byte-identical predicates
  (`reviewed_col IS NULL AND distance in [low,high) AND subject.status='pending' AND
  subject.ingestion_batch_id=:batch_id AND other.status<>'rejected'`), differing only in `reviewed_col`
  (`tier_a_reviewed_at` vs `tier_b_reviewed_at`) and which `UNION ALL` direction of `tmp_duplicates` each
  half covers — exactly the shape a single 4-branch CTE (2 sides × 2 tiers) can express in one query.
- `_tier_band("tier_a")`/`_tier_band("tier_b")` (`Backend/app/services/ingestion_service.py:23-25`) are
  already both computed by `get_run_status` today (needed for the existing 2 `unreviewed_subject_ids`
  calls) — the new method takes the same 4 band bounds plus `current_tier`; no new inputs anywhere in
  the call chain.
- `list_tier_b_review_page`'s existing `SET LOCAL work_mem = '256MB'`
  (`Backend/app/repositories/ingestion_repository.py:207`) is transaction-scoped and already proven safe
  in this exact repository/session pattern — `test_query_a_session_tuning_does_not_leak_past_the_transaction`
  confirms Postgres resets `SET LOCAL` at the end of a transaction regardless of commit/rollback, and
  this codebase's connection pooling doesn't leak it across pooled-connection reuse. The new method
  follows the identical pattern; no new risk surface.
- Measured live (read-only role, `general`, `EXPLAIN ANALYZE`, same batch as Problem's baseline): the
  proposed single-query design took **472ms unindexed**, **397ms with the same `work_mem` tuning
  applied** — both real, measured reductions from the current ~1000ms+/3-round-trip baseline, not
  estimates. The `work_mem` win here is smaller in relative terms than Query A's (the aggregate's sort
  only partially spilled — one of two parallel workers used an external disk merge instead of an
  in-memory quicksort) but is real and free to take, using a pattern already proven safe in this file.

## Design

### 1. `IngestionRepository` — retire `count_unreviewed_subjects`/`unreviewed_subject_ids`, add `get_review_progress`

Replace both methods (`Backend/app/repositories/ingestion_repository.py:233-296`) with one:

```python
async def get_review_progress(
    self, batch_id, current_tier: str,
    tier_a_low: float, tier_a_high: float,
    tier_b_low: float, tier_b_high: float,
) -> tuple[int, int]:
    """Live progress counts for the ingestion review banner: (tier_remaining, blocked_total).
    tier_remaining is the count of distinct pending subjects with an open (unreviewed, other
    side not rejected) candidate pair in `current_tier`'s band. blocked_total is the same count
    unioned across BOTH tiers -- a subject open in both bands counts once, never a sum (see
    docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md's "don't
    reuse get_blocked_pending_ids" note). One round trip, one scan of each tier's band, via
    COUNT(DISTINCT ...) FILTER instead of materializing id sets and unioning them in Python --
    see docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md."""
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
    # Scoped to this request's transaction only -- SET LOCAL never outlives it (see get_async_db).
    # Same reasoning as list_tier_b_review_page: this aggregate sorts/merges up to ~210k rows
    # (both tiers, both UNION ALL directions) before the final count -- at Postgres's stock 4MB
    # work_mem that partially spills to disk (confirmed via EXPLAIN's "Sort Method: external
    # merge Disk..." on one parallel worker). 256MB keeps it in memory -- measured live on
    # general's real batch: 472ms -> 397ms.
    await self.session.execute(text("SET LOCAL work_mem = '256MB'"))
    row = (await self.session.execute(sql, {
        "ta_low": tier_a_low, "ta_high": tier_a_high,
        "tb_low": tier_b_low, "tb_high": tier_b_high,
        "batch_id": batch_id, "current_tier": current_tier,
    })).one()
    return row.tier_remaining, row.blocked_total
```

The `current_tier` bind parameter only ever selects which branch's rows the `FILTER` counts — it is
never interpolated into SQL text, so there is no injection surface despite being a runtime string
(same pattern the retired methods already used for their `reviewed_col` assert-then-interpolate
guard, except here the tier string is a bind parameter, not interpolated at all).

### 2. `IngestionService.get_run_status` — one call instead of three

Current (`Backend/app/services/ingestion_service.py:129-137`):

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

New:

```python
        current_tier = _tier_for_stage(run.stage)
        if current_tier is not None:
            tier_a_low, tier_a_high = _tier_band("tier_a")
            tier_b_low, tier_b_high = _tier_band("tier_b")
            tier_remaining, blocked_total = await self.repo.get_review_progress(
                resolved_id, current_tier, tier_a_low, tier_a_high, tier_b_low, tier_b_high)
```

`_tier_band`/`_tier_for_stage` (module-level helpers, `ingestion_service.py:17-37`) and the surrounding
`if current_tier is not None: ... else: tier_remaining = None; blocked_total = None` structure
(`:127-128` initializes both to `None` before the branch) are otherwise untouched — same functions,
same band values, just fed into one repository call instead of three.

### Interface change

`count_unreviewed_subjects(batch_id, tier, distance_low, distance_high) -> int` and
`unreviewed_subject_ids(batch_id, tier, distance_low, distance_high) -> set` are removed.
`get_review_progress(batch_id, current_tier, tier_a_low, tier_a_high, tier_b_low, tier_b_high) -> tuple[int, int]`
replaces both. Its only caller is `IngestionService.get_run_status`, updated in the same change.

## Testing

- **Correctness (no behavior change to what's returned, only how it's computed):** port
  `tests/integration/test_ingestion_review_progress.py`'s existing coverage onto `get_review_progress`,
  preserving every case it currently proves:
  - excludes reviewed (own tier) and rejected-other-side pairs
  - respects tier + band (a tier_a-band pair doesn't count toward tier_b, and vice versa)
  - the tier_a-reviewed-column test added in the progress-visibility branch's final-review fix wave — a
    pair reviewed via `tier_a_reviewed_at` is excluded from tier_a's count, while an equivalent pair
    reviewed via `tier_b_reviewed_at` only still counts as open for tier_a — carries forward in spirit,
    adapted to the new method's `(tier_remaining, blocked_total)` return shape instead of a bare count
  - union-dedup: a subject open in both tiers' bands counts once in `blocked_total`, never twice
  - `test_get_run_status_end_to_end_real_db` (added in the same fix wave) already exercises this
    end-to-end through `IngestionService` against a real DB — keep it; update only if
    `get_review_progress`'s signature changes what it asserts against
- **`Backend/tests/test_ingestion_service.py::TestGetRunStatus`:** update the mocked tests to mock
  `get_review_progress` returning `(tier_remaining, blocked_total)` directly instead of mocking
  `count_unreviewed_subjects`/`unreviewed_subject_ids` separately. Continue the progress-visibility
  branch's final-review fix-wave precedent (Finding #2) of asserting the *full* call-args tuple
  (`batch_id`, `current_tier`, all 4 band bounds, derived from the real `_tier_band()` — not hardcoded)
  rather than just the tier string, so a transposed band or a tier mix-up can't silently pass.
- `cd Backend && pytest -q` (full suite) +
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
  (full sweep — this repo's "run the whole root, not just the file that looks related" rule applies to
  any change touching shared repository code).
- **Live before/after measurement** (controller runs this directly via `DATABASE_URL_READONLY`; never
  hand a subagent the live `DATABASE_URL`, per this repo's rule): re-run `EXPLAIN (ANALYZE, BUFFERS)` on
  the new query against a live environment's real active batch, with and without the `work_mem` tuning,
  and report the real numbers in the plan's review — same rigor as the sibling partial-index spec's
  "Measured outcome" section. This spec's own design-time measurement (472ms / 397ms vs. the ~1000ms+
  three-round-trip baseline, both on `general`'s `run_id 7f16392b…` batch) should be reconfirmed post-
  implementation, since that batch's review progress — and therefore its unreviewed fraction and query
  cost — will have moved on by then.

## Rollout

Pure query/method consolidation — no schema change, no migration, no API contract change, no frontend
change. `get_run_status`'s response is byte-identical for the same inputs; only the repository call
count and total database time change. Low risk: worst case, the new query's plan doesn't improve on
today's for some future data shape, and the result is a no-op relative to current cost — there's no path
where consolidating three round trips into one, with correctness enforced by test parity, makes the
result slower or wrong. Ships as its own small PR/commit off `main`, independent of any other open work.
