# P1: Popularity data model and absorption rules

Status: done
Plan: `docs/superpowers/plans/2026-10-26-p1-popularity-data-model.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; read it first, it holds the definition, D1 to D6 and C1 to C7)
Follow-ups: P2 recording winners in each path, P3 API and details page, P4 corrections and audit (not written yet)

## Purpose

Build the foundation the other designs call: where popularity is stored, what a confirmed duplicate writes, how it is
reversed, how the cached number is kept right, and how to prove it is right. No pipeline path, endpoint or page changes
in this design; P2 to P4 use the primitives defined here.

## Model: a forest of duplicate edges

Think of the union-find structure the owner described, with undo.

- Every image is a node worth 1.
- A **confirmed duplicate** is an **edge** from the loser to the winner. The loser's whole tree hangs under the winner.
- A byte-identical file re-dropped into the inbox has no image row. It is a **leaf edge**: no loser image, weight 1.
- A screenshot recut approval is an edge from the source to the cut with weight 0, because the cut is the same meme
  as its source and must not count twice: the cut ends up with the source's popularity exactly.
- **Popularity of a root** (an image that is not the loser of an open edge) = 1 + the sum of `weight` over all open
  edges in its tree. It does not depend on when edges were written, so undoing an edge in the middle of a chain is
  automatically right: the edge and everything under it leave the tree of whichever root holds it now.
- An image that is the loser of an open edge (discarded) has popularity **0** in the cache. It is out of the count
  until the edge is reversed.

Invariant: for every image, `popularity` equals the value recomputed from the open edges (0 for non-roots).
For every live root it is at least 1.

## Schema (one Alembic revision)

`images.popularity`: `Integer NOT NULL server_default '1'`, `CHECK (popularity >= 0)`. Adding a column with a constant
default is metadata-only in Postgres (no table rewrite). Every existing image becomes 1: that is the "no backfill"
rule. An older backend runs fine against the migrated schema, but the new code needs the migration first (every
`select(Image)` reads `images.popularity`), so migrate metal, general and IT before or at the merge. The CHECK
constraint add takes an ACCESS EXCLUSIVE lock on `images` and scans it (fast at ~22k rows) but queues behind long
transactions, so do not run it during a long batch (or set lock_timeout).

New table `popularity_absorptions`, append-only except `reversed_by_id` on an absorb row:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `kind` | String(10) not null | `absorb` or `reverse` |
| `source` | String(30) not null | absorb rows: one of the eight values in the epic (D3). reverse rows: the reversed row's source |
| `loser_image_id` | UUID null | **no foreign key**: ids outlive deleted images, so trees and audit survive deletion. Null for a re-dropped file |
| `winner_image_id` | UUID not null | no foreign key. The image the caller named as the kept one, even if it is itself discarded; the edge hangs under it, and the cache is credited to its current root (see "Winner resolution") |
| `weight` | Integer not null | copies this edge adds on its own: 1 for an ordinary loser or a re-dropped file, 0 for `recut`. Reverse rows copy the reversed row's weight |
| `loser_filename`, `loser_content_hash`, `winner_content_hash` | String null | descriptors so the audit reads after the images are gone |
| `dedupe_key` | String null, unique | only for re-dropped files: `<batch_run_id>:<content_hash>:<path the file was moved to under duplicates/>`. The identity of a copy is the file it became: the move gives every file its own destination (the builder confirms how `ingest_hash_dedup` resolves name collisions there, and the key must stay unique per file). A new file dropped later with the same name is a different file at a different destination, so it counts; retrying the same move cannot count twice |
| `batch_run_id` | UUID null, FK `batch_runs.run_id` ON DELETE SET NULL | the run that caused it, when there is one |
| `reverses_id` | UUID null, FK self, unique | reverse rows only: the absorb row undone; at most one reversal per row |
| `reversed_by_id` | UUID null, FK self | absorb rows only; set in the same statement batch that inserts the reverse row |
| `reason` | String(20) null | reverse rows only: `undo_reject`, `unflag`, `abort`, `correction` |
| `created_at` | timestamptz not null, default now | |

Constraints and indexes:
- `CHECK (kind IN ('absorb','reverse'))`, `CHECK (weight >= 0)`, a check that `source` is one of the eight values in the epic
  (adding a source later is a migration, like `image_cuts.state`).
- Unique partial index on `loser_image_id` where `kind = 'absorb' AND reversed_by_id IS NULL AND loser_image_id IS NOT NULL`:
  an image can be the loser of at most one open edge. This is the DB-level guard against counting the same duplicate twice.
- Partial index on `winner_image_id` where `kind = 'absorb' AND reversed_by_id IS NULL`: children lookup for tree walks.
- Index on `batch_run_id`.

The table has no foreign key to `images`, so the derived-data guard (`tests/docs/test_image_derived_data_inventory.py`)
does not force a row, but add one anyway to `docs/image-derived-data.md`: group **none**, not copied to a cut, with
the note that the `images.popularity` value of a cut is set by the recut approval edge (P2), not copied.

## Operations (repository `repository/popularity.py`, class `PopularityRepository`)

Global repository because both the backend and batch jobs call it. It never commits (house rule).

**Concurrency.** Row locks, not a global lock: ingestion runs record thousands of copies in one transaction and must not
block review actions elsewhere. Every write resolves the root, then `SELECT ... FOR UPDATE` the involved `images` rows
(loser, and the winner's root) in id order, then **re-resolves the root and retries (at most 3 attempts, then error)** if
it changed while waiting. Cache updates are SQL-side (`SET popularity = popularity + :n`), never read-modify-write on
loaded ORM values. Id-ordered locking rules out deadlock; two writes on different trees never wait on each other.

**The loser's size is computed, not trusted.** `absorb` computes the loser's tree size with the tree CTE and writes the
caches from that, so a drifted cache cannot spread into the winner (the check job then still reports the drift).

- `absorb(loser_id, winner_id, source, batch_run_id=None) -> row`:
  1. resolve the root of `winner_id`; refuse (error) if it is the loser (would form a cycle); lock as above;
  2. if the loser already has an open edge: return that row unchanged when the root of that edge's winner equals the
     root of `winner_id` (a retry, compared after resolving both to roots); otherwise raise `AlreadyDiscardedError`
     carrying the existing row. The unique index backstops this;
  3. insert the edge (`winner_image_id` = the named image) with `weight` 1 (0 for `recut`) and descriptors;
  4. `delta = weight + (tree_size(loser) - 1)`; `root.popularity += delta`; `loser.popularity = 0`.
- `record_dropped_copies(winner_id, copies, batch_run_id) -> int`: bulk form for ingestion; each copy (file name,
  content hash, destination path) is a leaf edge of weight 1 with no loser, skipped when its `dedupe_key` exists; one
  lock acquisition, one cache update of `+ inserted`. Returns the number recorded.
- `reverse(absorb_id, reason) -> row`:
  1. refuse if the row is not an open absorb; lock the loser and the root of the edge's winner;
  2. `s` = sum of `weight` over open edges in the loser's tree, excluding this edge (0 for a leaf edge);
  3. insert the reverse row, set `reversed_by_id`;
  4. `root.popularity -= weight + s` (nothing to update if that root image no longer exists);
     if the loser image still exists, `loser.popularity = 1 + s`.
  A chain is never a reason to refuse: the edge and the subtree under it leave whichever tree holds them.
- `popularity_of(image_id)` and `breakdown(image_id) -> {source: weight_sum}` for P3: the sum of open-edge weights by
  `source` over the tree rooted at the image, plus `own = 1`. Recursive CTE over the children index.
- `verify(image_ids=None) -> list[Drift]`: recompute every image's expected popularity from open edges and report rows
  where the cache differs. Used by tests and by the job below.

**Winner resolution:** the edge hangs under the image the caller named, even if that image is itself discarded; the
cache is credited to that image's current root. So if D is reported as a duplicate of B, and B was absorbed into A which
was absorbed into C, D sits under B, the popularity lands on C, and undoing A's absorption into C takes B and D back
with A. Callers normally name a root anyway; the rule only matters when they do not.

**Deleted images:** nothing in this table is touched when an image row is deleted (no cascade, no set null). Edges whose
endpoints are gone stay in place and keep their weight in whatever tree they belong to, so the root's cached number
stays correct. Reversing an edge whose loser row is gone only lowers the root and records the reversal.

**Deleting a root that is not absorbed** (a flagged image removed for non-duplicate reasons, a pending image removed by
`ingest_abort`) ends its popularity and leaves its tree's open edges behind as orphans. That is intended: the meme is
gone. `verify` and the check job ignore trees whose root no longer exists. P1 adds no automatic reaction to deletion;
P2 decides what `ingest_abort`, `unregister_deleted_images` and `move_flagged` do (for example reversing the edges an
aborted batch caused in trees that survive).

**Errors.** `absorb` raises `AlreadyDiscardedError` (typed, carries the existing row) when the loser already sits under a
different root, and `CycleError`. Neither half-applies anything. How a pipeline reacts is P2's decision; the default P2
should adopt is to skip and log, because the dedup pipelines must never fail on popularity bookkeeping.

## Check and repair job

`batch/check_image_popularity.py` (shell only for now, run-tracked is not required): prints drift from `verify()` and,
with `--fix`, rewrites `images.popularity` from the open edges. Without `--fix` it changes nothing. This is the
"recomputable from the log" guarantee of the epic (D1) made executable. Add it to `docs/batches/inventory.md` and the
batch list in `CLAUDE.md`, as the house rules require for any new batch script.

## Tests

Integration tests against `ocrdb_test` (`tests/integration/`, `DATABASE_URL` set on the command line, per `CLAUDE.md`):
- ordinary absorb, chain of three, re-drop leaves, recut weight 0;
- the chain-undo case: A absorbs B, A is absorbed by C, undo B's edge, then undo A's edge; every cache value checked
  against `verify()` at each step;
- idempotent retry of `absorb` and `record_dropped_copies`; a same-name file dropped again counts; `AlreadyDiscardedError`
  and cycle refusal; naming a discarded image as the winner, then undoing the chain above it;
- two concurrent absorbs touching the same tree (real transactions on the test database), no lost update;
- deleted loser, deleted winner (history intact, cache of the survivor correct);
- a randomized sequence of absorb and reverse calls over a small set of images, asserting `verify()` finds no drift
  after every step;
- migration test following the chain convention used by the other migration tests (the head is
  `d7a3b9e2c451` today; the recut work in flight may add revisions, re-chain `down_revision` at merge time).
Docs guards in `tests/docs/` must still pass.

## Goals

- One place that owns every write to popularity, safe under concurrency, never double-counting.
- The cached number is provably equal to the number recomputed from the log.
- Undo, correction and deletion of images never leave the cache wrong.

## Non-goals

- No path is wired (P2), no endpoint or UI (P3), no correction UI or list view of the log (P4), no sorting (P5).
- No backfill; no change to any pipeline's behaviour.

## Anti-goals

- A number that can drift silently, or that depends on the order edges were written.
- Losing audit history when an image is deleted.
- Anything that makes the dedup pipelines fail because popularity bookkeeping failed: P2 decides the failure policy,
  but this layer must raise clearly rather than half-apply (every operation is all-or-nothing inside the caller's
  transaction).

## Constraints

- Migration is created in this worktree but **not run**; the owner runs `alembic upgrade head` on metal, general and IT.
- Python 3.11, async SQLAlchemy, repositories never commit, ORM models only in `Storage/models.py`.
- Windows dev, the three environments' ports are always occupied: tests use the dedicated test database only.

## Left to the builder

- Exact names, the `Drift` shape, the advisory lock key, whether the tree CTE is one query or two.
- Whether `verify` is chunked for large tables.
- Whether `reverse` of a leaf edge needs its own entry point or the same one.
