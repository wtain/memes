# ADR 2026-07-10: tmp_duplicates cascade-delete stalls — missing FK indexes

STATUS: ACCEPTED

## Context

`batch/rebuild_duplicates.py` rebuilds `tmp_duplicates` as an unconditional
all-pairs cross join of every image against every image
(`JOIN image_embeddings ie2 ON true`, no filter) — O(n²) rows. On the
`general` env, 22,402 images produced **489,389,056 rows** (~84 GB).

A recent change added `ON DELETE CASCADE` foreign keys from
`tmp_duplicates.image_id1`/`image_id2` to `images.id`, so that a deleted
image's rows in `tmp_duplicates` get cleaned up automatically (matching
`tmp_clusters`, which already cascaded via its ORM-managed FK). But
`create_tmp_duplicates()` only ever created indexes on `(id, distance)`,
`(distance)`, and `(id)` — never on `image_id1`/`image_id2`.

Postgres does not auto-index the referencing side of a FK. Without an index
on the referencing columns, every `DELETE FROM images ...` forces a full
sequential scan of `tmp_duplicates` (once per FK) to find cascade matches.
`batch/unregister_deleted_images.py` does one bulk `DELETE ... WHERE id IN
(...)` per run — cheap on paper, but against an unindexed 489M-row table
this stalled for **7+ hours** on `general` before we caught it.

## Decision

Add `CREATE INDEX idx_tmp_duplicates_image_id1/2 ON tmp_duplicates
(image_id1/2)` to `create_tmp_duplicates()`, alongside the other index
statements. Since `tmp_duplicates` is dropped and fully recreated
(non-idempotent) on every `rebuild_duplicates` run, this guarantees the
indexes always exist going forward — no migration needed for future
rebuilds. See `batch/rebuild_duplicates.py`.

This does **not** address the underlying O(n²) cross join, which is a
separate, larger design question (most of those 489M rows are irrelevant
pairs with `distance` near 1.0). Out of scope here.

## Recovery procedure (already-affected live databases)

`general`'s `tmp_duplicates` predates this fix and needed a live repair —
dropping/rebuilding the whole 489M-row table would have been far more
expensive than just adding the two missing indexes to the existing table.
Steps, in order:

1. **Confirm the table is actually the bottleneck**, don't assume:
   ```sql
   SELECT relname, reltuples::bigint AS est_rows FROM pg_class
   WHERE relname = 'tmp_duplicates';           -- planner estimate, no scan
   SELECT indexname, indexdef FROM pg_indexes
   WHERE tablename = 'tmp_duplicates';          -- confirm image_id1/2 unindexed
   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conrelid = 'tmp_duplicates'::regclass; -- confirm CASCADE FKs exist
   ```

2. **Gotcha — killing the client process does not reliably kill the query.**
   We terminated the stuck `unregister_deleted_images` batch job client-side
   and believed it was gone. It wasn't: the Postgres backend kept running
   the DELETE for another 7+ hours, still holding `RowExclusiveLock` on
   `tmp_duplicates`/`images`, because the OS/Docker networking never
   surfaced the closed socket to Postgres. Always verify directly:
   ```sql
   SELECT pid, state, wait_event_type, wait_event,
          now()-query_start AS elapsed, query
   FROM pg_stat_activity
   WHERE datname = current_database() AND state != 'idle';
   ```
   If a query you thought was dead is still `active`, terminate it for
   real: `SELECT pg_terminate_backend(<pid>);`. A single-statement,
   uncommitted transaction (which both the bulk `DELETE` and a bare
   `CREATE INDEX` are here) rolls back cleanly — safe to kill.

3. **Diagnose lock contention**, not just "is it slow":
   ```sql
   SELECT l.pid, l.mode, l.granted, c.relname
   FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
   WHERE c.relname IN ('tmp_duplicates', 'images')
   ORDER BY c.relname, l.granted DESC;
   ```
   A `CREATE INDEX` blocked on `granted = false` isn't building anything —
   it's queued behind whatever holds the conflicting lock. Fix the blocker
   first, don't just wait.

4. **Bump `maintenance_work_mem` before building indexes on a large table.**
   Default was `64MB`; at that setting the first index-build attempt made
   no visible progress for 100+ minutes (small work_mem forces disk-based
   external sort passes). `SET maintenance_work_mem = '2GB'` (session-scoped,
   set on the same connection before `CREATE INDEX`) brought each of the two
   489M-row index builds down to **~10 minutes**.

5. **Windows-specific: launch long builds as a truly detached process**,
   not `Bash`/`PowerShell` with `run_in_background: true` — that flag still
   enforces the tool's 10-minute timeout and will kill the client mid-build
   (see gotcha in step 2 — the DB-side work may or may not survive that).
   Use `PowerShell`'s `Start-Process` pointed directly at the venv's
   `python.exe` (not through `bash -c`, which mangles Windows backslash
   paths) so the process is fully independent of the calling tool:
   ```powershell
   Start-Process -FilePath "<repo>\.venv311\Scripts\python.exe" `
     -ArgumentList @("-u", "<script>.py") -WorkingDirectory "<repo>" `
     -WindowStyle Hidden -RedirectStandardOutput <log> -RedirectStandardError <err>
   ```

6. **Monitor with `pg_stat_progress_create_index`**, not by guessing:
   ```sql
   SELECT pid, phase, blocks_total, blocks_done, tuples_total, tuples_done
   FROM pg_stat_progress_create_index;
   ```
   `phase` moves from `building index: scanning table` (blocks_*) to
   `building index: loading tuples in tree` (tuples_*). The row disappears
   the moment the index finishes or errors — cross-check with `pg_indexes`
   to confirm success vs. just "no longer running".

7. Only re-run `unregister_deleted_images` (or any bulk image delete) once
   both `idx_tmp_duplicates_image_id1` and `idx_tmp_duplicates_image_id2`
   show up in `pg_indexes`.

## Consequences

- `general` is fixed (indexes built live, `unregister_deleted_images`
  re-ran and completed in seconds).
- `metal` and `it` were not touched and almost certainly have the same
  unindexed FK today — they'll hit the identical multi-hour stall the next
  time `unregister_deleted_images` (or any image deletion) runs there,
  *unless* `rebuild_duplicates` is run first on that env (which now
  self-heals via the updated script). Apply the same live-repair procedure
  proactively, or run `rebuild_duplicates` before the next unregister pass.
- The O(n²) cross join itself is unaddressed — as the image count grows,
  `tmp_duplicates` grows quadratically and even indexed cascades /
  `rebuild_duplicates` runs will get more expensive over time. Worth a
  follow-up ADR if it becomes a recurring pain point.