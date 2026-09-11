# Tier B Query A Partial Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 2 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** Add a Postgres partial index (`tmp_duplicates(distance) WHERE tier_b_reviewed_at IS NULL`) so Query A (the Tier B review queue's per-page subject-ranking query) can prune to the unreviewed set instead of scanning the whole table, measure the real before/after cost on `general`'s live batch, and roll it out to all three environments.

**Architecture:** A declarative `Index(..., postgresql_where=...)` added to `TmpDuplicates.__table_args__` in `Storage/models.py`, materialized by a hand-written Alembic migration using `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()` — the same pattern this repo already uses for `embeddings`' HNSW index, needed because `tmp_duplicates` is large and continuously written. Task 1 writes and proves the migration against the disposable test database. Task 2 — live measurement and rollout to real environments — is controller-executed, never subagent-dispatched, and gated on the user's explicit go-ahead before touching any live database.

**Tech Stack:** SQLAlchemy declarative model + `Index`, Alembic (raw `op.execute`, not autogenerate, for the DDL itself), Postgres partial index + `EXPLAIN (ANALYZE, BUFFERS)`.

**Spec:** `docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md`

## Global Constraints

- Index name: `idx_tmp_duplicates_tier_b_unreviewed_distance`. Definition: `ON tmp_duplicates (distance) WHERE tier_b_reviewed_at IS NULL` — single column, partial predicate is `tier_b_reviewed_at IS NULL` only. **Never bake the tier band's literal bounds (`0.05`/`0.30`) into the predicate** — see the spec's "Key facts" section for why (the predicate would go silently stale if the band's settings ever change; `tier_b_reviewed_at IS NULL` is a structural invariant, the band bounds are not).
- Created with `CREATE INDEX CONCURRENTLY` inside `with op.get_context().autocommit_block():` — follow `Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py` verbatim as the structural template (same reasoning: large, continuously-written table).
- No application code, response shape, schema *table* change, or SQL query-text change anywhere. This plan only adds an index; Query A's SQL is untouched (Postgres either picks the new index up or it doesn't — nothing in the query text references an index by name).
- **Never hand a subagent the main `DATABASE_URL` for any of `general`'s/`metal`'s/`it`'s environments.** Task 1 uses only the disposable test database (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`). Task 2 is controller-only for exactly this reason — see its header.
- The three environment databases (metal/general/it) are always running and always in use by the developer's live backends — apply the migration to each only after Task 2's explicit user go-ahead, never speculatively.

---

## File Structure

**Modify:** `Storage/models.py` (`TmpDuplicates.__table_args__`).
**Create:** one new file under `Storage/alembic/versions/` (name assigned by `alembic revision`).
**Docs — modify:** the spec's status line (final task, Task 2).

---

## Task 1: Model + migration, proved against the test database

**Files:**
- Modify: `Storage/models.py:218-221` (`TmpDuplicates.__table_args__`)
- Create: `Storage/alembic/versions/<generated>_partial_index_for_unreviewed_tier_b_pairs.py`

**Interfaces:**
- Produces: a new Postgres index `idx_tmp_duplicates_tier_b_unreviewed_distance`, and a
  `Storage/models.py` declaration that keeps `alembic revision --autogenerate` in sync with it going
  forward. No Python-callable interface — this task is pure schema.

- [ ] **Step 1: Add the index declaration**

In `Storage/models.py`, `TmpDuplicates.__table_args__` (currently lines 218-221):

```python
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

`text` is already imported at the top of this file (used by `id`'s `server_default=text(...)`) — no new
import needed.

- [ ] **Step 2: Generate the migration skeleton**

From `Storage/`, with the test DB's URL set (needed only because `Storage/alembic/env.py` imports
`Storage.config.DATABASE_URL` at module level, which raises if unset — not because this command
actually connects to it):

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic revision -m "partial index for unreviewed tier-b pairs"
```

This creates a new file in `Storage/alembic/versions/` with an auto-assigned `revision` and
`down_revision` set to the current head — do not hand-pick either value. Note the generated filename
and revision id for the later steps.

- [ ] **Step 3: Write the migration body**

Open the generated file and replace its empty `upgrade()`/`downgrade()` stubs, following
`Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py`'s structure exactly:

```python
from alembic import op

# revision / down_revision / branch_labels / depends_on: leave exactly as `alembic revision` generated
# them -- do not edit those four lines.


def upgrade() -> None:
    # tmp_duplicates is large and continuously written (rebuild_duplicates.py inserts
    # incrementally, every Tier A/B review decision updates tier_*_reviewed_at) -- CONCURRENTLY
    # avoids a blocking lock, same reasoning as 2026_07_16_fix_embeddings_hnsw_index.py. Requires
    # autocommit_block() since CONCURRENTLY can't run inside a transaction. The partial predicate
    # is deliberately just "unreviewed", not the tier band's literal bounds -- see
    # docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md's "Key facts".
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance "
            "ON tmp_duplicates (distance) WHERE tier_b_reviewed_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance")
```

- [ ] **Step 4: Apply to the test database and verify**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic current
```
If this isn't already at the head from before your new migration, run
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head` first to
catch the test DB up, then re-run `alembic current` to confirm.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
```
Expected: succeeds, ends at your new revision.

Verify the index exists and is valid (a `CONCURRENTLY` build that failed partway leaves an `INVALID`
index rather than erroring the migration):
```bash
psql "postgresql://ocr:ocr@localhost:5432/ocrdb_test" -c "\d tmp_duplicates" | grep tier_b_unreviewed
psql "postgresql://ocr:ocr@localhost:5432/ocrdb_test" -c "SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text = 'idx_tmp_duplicates_tier_b_unreviewed_distance';"
```
Expected: the index is listed, `indisvalid` is `t`.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic downgrade -1
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
```
Expected: both succeed — proves `downgrade()` cleanly drops the index and `upgrade()` cleanly
recreates it (the reversibility check the spec's Testing section asks for). Leave the test DB at
`head` (upgraded) when done.

- [ ] **Step 5: Verify model/migration parity**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic revision --autogenerate -m "verify-empty-diff-DELETE-ME"
```
Open the newly generated file: its `upgrade()`/`downgrade()` bodies should both be empty (`pass`, or
no `op.*` calls) — proof that `Storage/models.py`'s `Index(...)` declaration matches what your
hand-written migration actually created, so future `--autogenerate` runs won't try to re-add or drop
this index. **Delete this verification-only file** — it exists only to prove the empty diff, it is not
a real migration and must not be committed.

If the diff is NOT empty, the model declaration and the hand-written DDL disagree (usually the index
name or the `postgresql_where` predicate text) — fix `Storage/models.py` (never hand-edit the DDL
migration to match a mistaken model, unless the model is what's wrong) and repeat this step.

- [ ] **Step 6: Full backend regression (unmodified — this proves the change is behavior-neutral)**

```bash
cd Backend && pytest -q
```
Expected: PASS, unchanged from before this task — no backend code changed, only schema.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k "ingestion or clusterize"
```
Expected: PASS, unchanged — same rows, same order; only how Postgres finds them changed.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/
git commit -m "feat: partial index for unreviewed Tier B pairs (test-db verified)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

Confirm `git status` shows no leftover verification-only migration file from Step 5 before committing.

---

## Task 2: Live measurement + rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** It reads and writes live `metal`/`general`/`it` databases the
developer's own running backends depend on continuously — exactly the case
`docs/superpowers/specs/...` and this repo's own rules (`CLAUDE.md`'s "Live database access for agents")
exist for: a subagent's read-only instruction is only as strong as the sentence enforcing it, and this
task needs a real write (`alembic upgrade head` against three live databases). The controller runs every
step below itself.

**Before Step 3 (the first live-environment write), stop and get the user's explicit go-ahead.** This is
a schema change to databases outside this task's own worktree that other running services use right now
— the same class of action `superpowers:subagent-driven-development` and
`superpowers:finishing-a-development-branch` both treat as requiring a check-in, not a autonomous ruling.
Present what Step 1's measurement found, name the three environments this will touch, and wait for a
clear yes before Step 3.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md` (status + the
  measured numbers, final step).

- [ ] **Step 1: Live "before" measurement on `general` (readonly connection)**

Look up the active ingestion run's batch id — either `curl http://localhost:8082/api/ingestion/run-status`
(the `run_id` field) if a run is active, or a direct read via `DATABASE_URL_READONLY` if not (see
`environments/.env.general` for the readonly DSN — it connects as `ocr_readonly`, SELECT-only, exactly as
documented in `CLAUDE.md`'s "Live database access for agents"). If no ingestion run is currently active on
`general`, note that in your report and use a synthetic/older completed run's batch id instead purely to
exercise the query shape, or skip the live measurement and note why — do not fabricate numbers.

Run `EXPLAIN (ANALYZE, BUFFERS)` on Query A's exact SQL (from
`IngestionRepository.list_tier_b_review_page`'s `subjects_sql`, no `HAVING` clause — page 1, the worst
case per the spec's Problem section), bound to the real batch id and the Tier B band, `LIMIT 40`:

```sql
EXPLAIN (ANALYZE, BUFFERS)
WITH pair AS (
    SELECT td.image_id1 AS subject_id, td.image_id2 AS cand_id, td.distance, td.match_source
    FROM tmp_duplicates td
    JOIN images s ON s.id = td.image_id1
    JOIN images o ON o.id = td.image_id2
    WHERE td.tier_b_reviewed_at IS NULL
      AND td.distance >= 0.05 AND td.distance < 0.30
      AND s.ingestion_batch_id = '<real batch id>' AND s.status = 'pending'
      AND o.status <> 'rejected'
    UNION ALL
    SELECT td.image_id2 AS subject_id, td.image_id1 AS cand_id, td.distance, td.match_source
    FROM tmp_duplicates td
    JOIN images s ON s.id = td.image_id2
    JOIN images o ON o.id = td.image_id1
    WHERE td.tier_b_reviewed_at IS NULL
      AND td.distance >= 0.05 AND td.distance < 0.30
      AND s.ingestion_batch_id = '<real batch id>' AND s.status = 'pending'
      AND o.status <> 'rejected'
)
SELECT p.subject_id, i.filename, i.status,
       MIN(p.distance) AS min_distance, COUNT(*) AS total_candidates
FROM pair p JOIN images i ON i.id = p.subject_id
GROUP BY p.subject_id, i.filename, i.status
ORDER BY MIN(p.distance), p.subject_id::text
LIMIT 40;
```

Run it via `psql "<DATABASE_URL_READONLY for general, converted to a psql-style DSN>"` or an ad hoc
asyncpg script — either way, the readonly role only, never the main `DATABASE_URL`. Record the total
execution time and the plan shape (sequential scan vs. index scan/bitmap on `tmp_duplicates`) — this is
the "before" number.

- [ ] **Step 2: Present the finding and stop**

Report Step 1's measured time and plan shape to the user. State plainly that Steps 3+ apply a schema
migration to `metal`, `general`, and `it` in turn. Wait for explicit confirmation before continuing —
do not proceed on an assumption that the earlier spec-writing request implied approval to modify live
databases; that's a distinct action requiring its own go-ahead.

- [ ] **Step 3: Apply the migration to all three environments**

Only after Step 2's go-ahead. Per `CLAUDE.md`'s documented migration workflow, for each of `metal`,
`general`, `it` in turn:

```powershell
Get-Content ..\environments\.env.<environment> | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
cd Storage
alembic upgrade head
```

Confirm each environment's backend (`/api/diagnostics/health`) still responds normally after its
migration — this is an additive index, not expected to cause any disruption, but confirm anyway since
these are live, continuously-used services.

- [ ] **Step 4: Live "after" measurement on `general`**

Same `EXPLAIN (ANALYZE, BUFFERS)` query as Step 1, same batch id if it's still the active run (if the
run advanced or completed between Step 1 and Step 4, note that and use whatever batch id is comparable
— reviewed rows only shrink the unreviewed set over a run's life, which would make a same-run
after-measurement look *better* than a fair comparison; call this out explicitly if it applies rather
than letting it read as a bigger win than the index alone produced). Record the new time and plan shape.

If the plan still shows a sequential scan (Postgres hasn't picked up the new index — can happen before
autovacuum runs `ANALYZE` on it), run `ANALYZE tmp_duplicates;` against `general` and re-measure before
concluding the index isn't helping.

- [ ] **Step 5: Record the outcome and mark the spec done**

`docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md`: `status: draft` → `status:
done`; add `Plan: docs/superpowers/plans/2026-09-11-ingestion-tier-b-review-partial-index.md` under the
status line; add a short "## Measured outcome" section above or below "## Rollout" with the before/after
numbers and plan shapes from Steps 1 and 4, replacing the spec's original "acceptable for v1... fast
follow if needed" framing with the real measured result — the same treatment the stopgap spec got after
its own live measurement.

```bash
git add Storage/models.py Storage/alembic/versions/ \
        docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md
git commit -m "perf: apply the Tier B unreviewed-pair partial index to all environments

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

(If Task 1's commit already covers `Storage/models.py`/`Storage/alembic/versions/`, this commit is just
the spec update — adjust the `git add` list to whatever actually changed.)

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (model index declaration) → Task 1 Step 1.
- Design §2 (migration, `CONCURRENTLY` + `autocommit_block`) → Task 1 Steps 2-3, following the named
  precedent file exactly.
- Testing: correctness/no-behavior-change → Task 1 Step 6; live before/after measurement → Task 2 Steps
  1 and 4; migration reversibility → Task 1 Step 4's downgrade/upgrade cycle; the "index not picked up,
  re-`ANALYZE`" caveat → Task 2 Step 4.
- "Key facts" — the staleness risk of baking band bounds into the predicate → called out in Global
  Constraints and in the migration's own code comment (Task 1 Step 3), not just the spec prose.
- Non-goals respected: no Query B change, no Tier A index, no query-caching, no other schema change —
  this plan touches exactly one table's index set.
- The spec's own live-database-access rule (`CLAUDE.md` "Live database access for agents") is
  operationalized as Task 2's header and Steps 1/4 (`DATABASE_URL_READONLY` only) and Step 3 (the one
  place this plan legitimately needs write access, gated on Step 2's explicit go-ahead) — not left as
  prose the executor has to remember to apply.

**Placeholder scan:** none. Every code/DDL step has literal text (the full migration body in Task 1 Step
3, the full `EXPLAIN` query in Task 2 Step 1) or an exact command; Task 2's live-data steps name the
lookup method for the one genuinely environment-specific value (the batch id) rather than hand-waving
"use the appropriate batch."

**Type consistency:** the index name (`idx_tmp_duplicates_tier_b_unreviewed_distance`) and its exact
definition (`(distance) WHERE tier_b_reviewed_at IS NULL`) are identical across the model declaration
(Task 1 Step 1), the migration body (Task 1 Step 3), the verification queries (Task 1 Steps 4-5), and the
spec's own Design section — no drift between what's declared, what's built, and what's checked.
