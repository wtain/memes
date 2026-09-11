# Ingestion Review — Tier B Query A Partial Index

status: planned
Plan: docs/superpowers/plans/2026-09-11-ingestion-tier-b-review-partial-index.md
Originates from: docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md's final
whole-branch review (2026-09-10/11) — a Recommendation alongside Important finding #3 ("land the partial
index now rather than 'if needed'"), deferred as a tracked follow-up rather than reopening that branch. No
separate review file exists; the reasoning below is original to this spec, not copied verbatim from the
review (which only sketched the idea).
Follow-ups from: docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md — a sibling
follow-up for the same branch's Query B (the per-page candidate fetch); this spec is Query A (the
per-page subject ranking). Independent designs, no dependency either direction.

## Problem

`IngestionRepository.list_tier_b_review_page`'s Query A (the subject-ranking query behind every page of
the Tier B review queue, including page 1 of every `GET /api/ingestion/review/tier_b` request) aggregates
over the `pair` CTE — a `UNION ALL` of both directions of `tmp_duplicates`, filtered to
`tier_b_reviewed_at IS NULL AND distance >= :low AND distance < :high`, joined to `images` twice per row to
check batch/status — then `GROUP BY subject_id` to rank subjects by their tightest candidate distance.

`tmp_duplicates` has no index that helps this filter. Its existing indexes (`Storage/models.py`'s
`TmpDuplicates`) are a plain btree on `image_id1`, one on `image_id2`, one on `id`, and one on `distance`
(`idx_tmp_duplicates_distance`) — none partial, none combining "unreviewed" with "in range". Without an
index that prunes on `tier_b_reviewed_at IS NULL` before or alongside the distance range, Postgres has no
cheap way to skip the reviewed majority of the table, and query cost scales with the table's *total* size
rather than with the *unreviewed* set the query actually cares about.

This is the query the per-image-review design's own spec measured directly: **~3–5s** on `general`'s live
batch (~232k in-band pair rows aggregating to ~7,200 subject groups), accepted at the time as "acceptable
for v1... a partial index... is a fast follow if needed." It re-runs on *every page* — Query A is not
memoized or cached across requests, so a reviewer paging through ~180 pages of a dense batch (7,200 subjects
÷ `limit=40`) pays this cost ~180 times to walk the queue once, and it gets marginally *cheaper* over the
life of a run as pairs get reviewed (`tier_b_reviewed_at` gets set, shrinking the unreviewed set) but starts
at its most expensive on page 1 of a freshly-prepped batch — exactly when a reviewer is deciding whether the
page is usably fast.

## Goal

Add a Postgres partial index that lets Query A prune to the unreviewed Tier B pair set directly, instead of
scanning (or filtering post-scan) the whole `tmp_duplicates` table on every page load.

## Non-goals

- Query B (the per-page candidate fetch) — a separate cost problem with a separate fix; see
  `docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md`.
- A parallel index for Tier A's equivalent query (`get_tier_candidate_rows`, which filters on
  `tier_a_reviewed_at IS NULL` — a *different* column). Tier A's components are small (the whole reason
  `list_clusters` stays cluster-shaped for that tier); it hasn't shown the cost problem Tier B has, and nothing in this branch's measurements or reviews flagged it. Out of scope until it does.
- Caching or memoizing Query A across requests/pages — a different kind of fix (application-level, not a
  schema change) that trades staleness risk for speed; not considered here.
- Changing `tmp_duplicates`' existing four indexes, the unique constraint, or any other table's schema.
- Any change to Query A's SQL text, the cursor, or the response contract — this is a pure infrastructure
  change; the query keeps working identically (same results, same ordering) whether or not Postgres chooses
  to use the new index. It only changes how cheaply Postgres can find the answer.

## Key facts this rests on

- `TmpDuplicates` (`Storage/models.py:182-221`): `image_id1`/`image_id2`/`id` are indexed individually;
  `distance` has a dedicated non-partial index (`idx_tmp_duplicates_distance`); `tier_a_reviewed_at` and
  `tier_b_reviewed_at` have no index at all.
- `tier_b_reviewed_at` is set exactly once per row, by `mark_reviewed`/`reject_image`'s cascade, and never
  unset — so `WHERE tier_b_reviewed_at IS NULL` is a genuinely shrinking, one-directional predicate over a
  batch's review lifetime, not a value that flips back and forth.
- `rebuild_duplicates.py` inserts into `tmp_duplicates` incrementally via `ON CONFLICT DO NOTHING` — it's a
  live, continuously-written table, not a static one. A migration that adds an index here needs to account
  for concurrent writers, same as the precedent below.
- Direct precedent in this repo for a large-table, concurrently-created index in a migration:
  `Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py` uses
  `with op.get_context().autocommit_block(): op.execute("CREATE INDEX CONCURRENTLY ...")` to avoid a
  blocking lock on `embeddings`, a table with the same "large + live + continuously written" shape as
  `tmp_duplicates`. This spec follows the same pattern.
- Baking the tier-B band's literal bounds (`0.05`, `0.30` today) into the index's partial predicate would
  make the index silently go stale if `PROXIMITY_THRESHOLD`/`settings.DUPLICATES.THRESHOLD` ever change —
  a partial index's predicate is fixed at creation time and does not track application config. This design
  deliberately avoids that: the partial predicate is only `tier_b_reviewed_at IS NULL`, a real structural
  invariant, not a tunable; `distance` stays an ordinary indexed column so the query's runtime `low`/`high`
  bind parameters keep working as a range scan regardless of what the settings say later.

## Design

### 1. `Storage/models.py` — add the partial index to `TmpDuplicates.__table_args__`

```python
from sqlalchemy import text as sa_text  # if not already imported under that name

__table_args__ = (
    UniqueConstraint("image_id1", "image_id2", name="uq_tmp_duplicates_pair"),
    Index("idx_tmp_duplicates_distance", "distance"),
    Index(
        "idx_tmp_duplicates_tier_b_unreviewed_distance",
        "distance",
        postgresql_where=text("tier_b_reviewed_at IS NULL"),
    ),
)
```

A single-column index on `distance`, scoped by the partial predicate to only unreviewed Tier B pairs.
Postgres can use it for both `UNION ALL` branches of Query A's `pair` CTE identically (the predicate and
leading column are direction-agnostic — `image_id1`/`image_id2` don't appear in the index at all, only
`distance` and the implicit row pointer).

**Not included, deliberately:** `INCLUDE (image_id1, image_id2, match_source)` for a true index-only scan.
It's a plausible further win (Query A only ever needs `image_id1`/`image_id2`/`distance`/`match_source`
from `tmp_duplicates` itself), but whether it actually pays off depends on the table's visibility-map state
under `general`'s real write pattern, which this spec doesn't have live numbers for. Left as a candidate
follow-up to measure once the plain partial index is live and its `EXPLAIN` numbers are in hand (see
Testing) — adding `INCLUDE` columns later is a plain `CREATE INDEX` swap, not a design change.

### 2. Migration — `CONCURRENTLY`, following the HNSW-index precedent

`tmp_duplicates` is large (hundreds of thousands of rows on `general`) and live (written by
`rebuild_duplicates.py`, updated by every review decision). A plain `CREATE INDEX` takes a lock that blocks
writers for the build's duration; `CREATE INDEX CONCURRENTLY` avoids that, at the cost of needing to run
outside Alembic's normal per-migration transaction:

```python
from alembic import op

revision = "<generated>"
down_revision = "<current head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tmp_duplicates is large and continuously written (rebuild_duplicates.py inserts, review
    # decisions update tier_*_reviewed_at) -- CONCURRENTLY avoids a blocking lock, same reasoning
    # as 2026_07_16_fix_embeddings_hnsw_index.py. Requires autocommit_block() since CONCURRENTLY
    # can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance "
            "ON tmp_duplicates (distance) WHERE tier_b_reviewed_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance")
```

Generate via `alembic revision -m "partial index for unreviewed tier-b pairs"` (NOT `--autogenerate` for
the DDL itself — autogenerate doesn't know to emit `CONCURRENTLY`/`autocommit_block()`; write the upgrade/
downgrade by hand following the template above, matching the HNSW precedent's structure). Confirm
`alembic revision --autogenerate` run *after* this migration produces an empty diff (proves the
`Storage/models.py` `Index(...)` declaration matches what the hand-written migration actually created), per
the existing model/migration parity convention.

Apply to all three environments per the repo's standard migration workflow (`environments/.env.<env>` +
`alembic upgrade head`).

## Testing

- **Correctness (no behavior change expected):** `cd Backend && pytest -q` and
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k
  "ingestion or clusterize"` stay green, unmodified — this change alters only how Postgres finds rows, never
  which rows a query returns. Any test failure after this migration is a real regression, not an accepted
  cost.
- **Live measurement, before/after (controller runs this directly — never hand a subagent the live
  `DATABASE_URL`, only `DATABASE_URL_READONLY`, per this repo's rule):**
  1. Before the migration: `EXPLAIN (ANALYZE, BUFFERS)` on Query A's live SQL against `general`'s readonly
     connection, with `:low`/`:high` bound to the real Tier B band and a realistic `:limit` (40), no cursor
     (page 1 — the worst case, per the Problem section). Record total time and whether the plan is a
     sequential scan or an index scan/filter on `tmp_duplicates`.
  2. After the migration (concurrently created, so no downtime needed to compare): same `EXPLAIN (ANALYZE,
     BUFFERS)`, same parameters. Record the new plan and timing.
  3. Report both — this spec's "fast follow" framing from the original design doc should be replaced with a
     real measured number in the design doc / plan review, the same way the stopgap spec replaced "the page
     is unusable" with a measured `200, 212 KB, ~30.7s` figure after a Ruling.
  4. If the new plan does NOT pick up the partial index (Postgres sometimes prefers a seq scan on a table
     it estimates is mostly matching, especially early in the migration before autovacuum has run `ANALYZE`
     on the new index's statistics) — run `ANALYZE tmp_duplicates;` and re-measure before concluding the
     index isn't helping.
- **Migration reversibility:** confirm `alembic downgrade -1` cleanly drops the index (dev/test DB only,
  never against a live environment without explicit sign-off) and `alembic upgrade head` re-creates it.

## Rollout

Additive, concurrently-created index; no application code changes beyond the `Storage/models.py` declaration
(which exists purely so `alembic revision --autogenerate` stays in sync going forward — Query A's SQL text
is untouched and doesn't need to reference the index by name). No downtime. Low risk: worst case, the
migration runs and the planner doesn't pick up the new index (verified in Testing) and the change is a
no-op; there's no path where adding this index makes Query A *slower* or *wrong*.
