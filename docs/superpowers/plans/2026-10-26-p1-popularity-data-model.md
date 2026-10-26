# P1 Popularity Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `images.popularity`, the `popularity_absorptions` edge log, the `PopularityRepository` that owns every write to them, and a check/repair job, so later designs (P2 to P4) can record duplicates, show the number and correct it.

**Architecture:** Popularity is the size of a duplicate forest. Each confirmed duplicate is an open edge loser to winner; a root's popularity is 1 plus the weight of every open edge in its tree; a discarded image is 0. The cache on `images` is maintained by SQL-side increments under row locks and is always recomputable from open edges (`verify`).

**Tech Stack:** Python 3.11, SQLAlchemy 2 async, Alembic, PostgreSQL (recursive CTEs), pytest with `ocrdb_test`.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-26-p1-data-model-and-absorption-rules-design.md` (epic: `.../README.md` in the same folder)

## Global Constraints

- Work only in the worktree `H:\workspace_sandbox\memes\.claude\worktrees\meme-popularity` (branch `worktree-meme-popularity`). Never merge, never run `alembic upgrade` against metal, general or IT; the owner does that. Tests use `ocrdb_test` only.
- Python: the worktree has no venv; use `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest ...` (the shared Python 3.11 venv). Do not `pip install` anything.
- Integration tests: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` set on the command line, run from the repo root; one pytest root per invocation (never combine `Backend/tests`, `tests/integration`, `tests/docs`). The `db_engine` fixture does `create_all` and `drop_all` on `ocrdb_test` per session: do not run two integration sessions (this worktree and another) at the same time.
- Repositories never `commit()`; ORM models only in `Storage/models.py`; `repository/` is the global data layer.
- Migration head is `d7a3b9e2c451` in this branch. The new revision's `down_revision` is `d7a3b9e2c451`; the recut work in flight may add revisions, so re-chain at merge time (that is the owner's step, flag it in the final summary).
- The eight sources: `hash_in_batch`, `hash_cross_corpus`, `ingest_tier_a`, `ingest_tier_b`, `cluster_review`, `agent_review`, `file_duplicates`, `recut`. Reasons for reversals: `undo_reject`, `unflag`, `abort`, `correction`.
- Weights: 1 for an ordinary loser and for a re-dropped file, 0 for `recut`.
- Commit messages end with the trailer lines:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

- Naming a winner id that has no `images` row (a deleted image): absorb must succeed and credit the root; the cache update touching zero rows is not an error.
- Absorbing an image into itself or into a member of its own tree: `CycleError`, nothing written.
- Reversing an already reversed row, or a reverse row: error, nothing written.
- `record_dropped_copies` with an empty list, and with two copies sharing one destination in the same call: counts 0 and 1 respectively.
- A `recut` edge (weight 0) whose loser has its own children: the winner ends with exactly the loser's popularity.

---

## File Structure

- `Storage/models.py` (modify): `Image.popularity` + check, `PopularityAbsorption` model, constants `PopularitySource`, `PopularityKind`, `PopularityReason`.
- `Storage/alembic/versions/a4f7c1e92b68_add_image_popularity.py` (create): the one revision.
- `repository/popularity.py` (create): exceptions, `DroppedCopy`, `Drift`, `PopularityRepository`.
- `batch/check_image_popularity.py` (create): shell-only check/repair job.
- Tests: `tests/integration/test_popularity_model.py`, `test_popularity_migration.py`, `test_popularity_repository.py`, `test_popularity_properties.py`, `test_check_image_popularity.py`.
- Docs: `docs/image-derived-data.md`, `docs/batches/inventory.md`, `docs/schema.md`, `CLAUDE.md` (batch list).

---

### Task 1: Model, constants and migration

**Files:**
- Modify: `Storage/models.py` (`Image` at line 58; add the new classes after `ImageCut`, before `ProcessEvent`)
- Create: `Storage/alembic/versions/a4f7c1e92b68_add_image_popularity.py`
- Test: `tests/integration/test_popularity_model.py`, `tests/integration/test_popularity_migration.py`

**Interfaces:**
- Produces: `Image.popularity` (`Integer`, not null, `server_default="1"`, check `ck_images_popularity` `popularity >= 0`); `PopularityAbsorption` with exactly the columns, constraints and indexes of the spec's schema table; classes `PopularitySource` (`HASH_IN_BATCH`, `HASH_CROSS_CORPUS`, `INGEST_TIER_A`, `INGEST_TIER_B`, `CLUSTER_REVIEW`, `AGENT_REVIEW`, `FILE_DUPLICATES`, `RECUT`, `ALL`), `PopularityKind` (`ABSORB`, `REVERSE`), `PopularityReason` (`UNDO_REJECT`, `UNFLAG`, `ABORT`, `CORRECTION`, `ALL`), in the style of `CutState`.
- Constraint names: `ck_popularity_absorptions_kind`, `_weight`, `_source`, `_reason`, `_shape` (absorb rows: `reverses_id` and `reason` null; reverse rows: both not null); unique `uq_popularity_dedupe_key`, `uq_popularity_reverses_id`; partial unique `uq_popularity_open_loser` on `loser_image_id` where `kind = 'absorb' AND reversed_by_id IS NULL AND loser_image_id IS NOT NULL`; partial `ix_popularity_open_winner` on `winner_image_id` where `kind = 'absorb' AND reversed_by_id IS NULL`; `ix_popularity_batch_run_id`. Foreign keys only: `batch_run_id` to `batch_runs.run_id` ON DELETE SET NULL, `reverses_id` and `reversed_by_id` to `popularity_absorptions.id`. No foreign key to `images`.

- [ ] **Step 1: Write the failing model tests** in `test_popularity_model.py` (use `db_session`):
  - `test_a_new_image_defaults_to_popularity_one`: insert `Image(filename="a.jpg")`, flush, refresh, `assert image.popularity == 1`.
  - `test_negative_popularity_is_rejected`: `Image(filename="b.jpg", popularity=-1)` flush raises `IntegrityError`.
  - `test_the_open_loser_index_allows_one_open_edge_per_loser`: two absorb rows with the same `loser_image_id`, both `reversed_by_id` null, second flush raises `IntegrityError`; with the first row's `reversed_by_id` set to a reverse row's id the second insert succeeds.
  - `test_the_shape_check_rejects_a_reverse_row_without_reverses_id` and `test_a_source_outside_the_eight_is_rejected`.
  - `test_dedupe_key_is_unique_but_null_is_repeatable`.
  - `test_there_is_no_foreign_key_from_the_log_to_images`: `{fk.column.table.name for fk in PopularityAbsorption.__table__.foreign_keys}` equals `{"batch_runs", "popularity_absorptions"}`.
- [ ] **Step 2: Run to see them fail**
  Run: `DATABASE_URL=... python -m pytest tests/integration/test_popularity_model.py -v` — Expected: FAIL (ImportError on `PopularityAbsorption`).
- [ ] **Step 3: Implement the model changes** in `Storage/models.py`: add the column and check to `Image.__table_args__`, the three constant classes and the model with the names listed under Interfaces. `created_at` is `DateTime(timezone=True)`, `server_default=func.now()`; `weight` has no default.
- [ ] **Step 4: Run the model tests** — Expected: PASS.
- [ ] **Step 5: Write the migration test** `test_popularity_migration.py`, patterned on `tests/integration/test_image_cuts_migration.py` (same `_run`, `_norm`, shape helpers, loaded by file path):
  - `test_the_migration_matches_the_model_and_downgrades`: capture the model-created shape of `popularity_absorptions` (columns, checks, indexes with `postgresql_where`, primary key, foreign keys) and of `images.popularity` (type, nullable, default, plus check `ck_images_popularity`); in a rolled-back transaction `DROP TABLE popularity_absorptions` and `ALTER TABLE images DROP COLUMN popularity`; run `upgrade`; assert shapes equal; run `downgrade`; assert the table and column are gone.
  - `test_existing_images_read_one_after_the_migration`: after dropping the column, insert an image row, run `upgrade`, `SELECT popularity` is `[1]`.
  - `test_the_shape_guard_catches_drift` (parametrized negative control: drop `uq_popularity_open_loser`; drop `ck_popularity_absorptions_weight`).
  - `test_the_migration_continues_the_chain`: `revision == "a4f7c1e92b68"` and `down_revision == "d7a3b9e2c451"`.
- [ ] **Step 6: Run it to see it fail** — Expected: FAIL (file not found).
- [ ] **Step 7: Write the migration** `a4f7c1e92b68_add_image_popularity.py` (same file style as `d7a3b9e2c451_...py`): `upgrade` adds `images.popularity` (`sa.Integer`, `nullable=False`, `server_default="1"`) with `ck_images_popularity`, then creates the table with every constraint and index above; `downgrade` reverses in the opposite order. Before writing, run `grep -rn "a4f7c1e92b68" Storage/alembic` to confirm the id is unused.
- [ ] **Step 8: Run both test files** — Expected: PASS.
- [ ] **Step 9: Commit** `feat(popularity): images.popularity, popularity_absorptions model and migration`

### Task 2: Repository core: tree queries and `absorb`

**Files:**
- Create: `repository/popularity.py`
- Test: `tests/integration/test_popularity_repository.py`

**Interfaces:**
- Consumes: Task 1 models and constants.
- Produces, all in `repository/popularity.py`:
  - `class CycleError(ValueError)`; `class AlreadyDiscardedError(Exception)` with attribute `existing: PopularityAbsorption`; `class PopularityConflictError(RuntimeError)` (raised when the root keeps changing after 3 attempts).
  - `class PopularityRepository`: `__init__(self, session: AsyncSession)`.
  - `async def root_of(self, image_id: uuid.UUID) -> uuid.UUID`: follow open absorb edges upward (`loser_image_id == X` to `winner_image_id`); returns `image_id` when it has no open edge; raises `RuntimeError` after 10000 hops.
  - `async def tree_weight(self, image_id: uuid.UUID) -> int`: sum of `weight` over open edges in the tree below `image_id` (recursive CTE over `ix_popularity_open_winner`; `UNION`, not `UNION ALL`). Tree size is `1 + tree_weight`.
  - `async def absorb(self, loser_id: uuid.UUID, winner_id: uuid.UUID, source: str, batch_run_id: uuid.UUID | None = None) -> PopularityAbsorption`.
  - Private `async def _lock_images(self, ids) -> None`: `SELECT id FROM images WHERE id IN :ids ORDER BY id FOR UPDATE`.

**Algorithm for `absorb`** (the part the signature does not fix): loop at most 3 times: resolve `root = root_of(winner_id)`; raise `CycleError` if `root == loser_id` (also covers `winner_id == loser_id`); lock `{loser_id, root}`; re-resolve the root and `continue` if it changed (after the 3rd failure raise `PopularityConflictError`); load the loser image (missing: `ValueError`); look for an open edge with this loser: if found and `root_of(existing.winner_image_id) == root` return it, else raise `AlreadyDiscardedError(existing)`; otherwise insert the edge (`weight` 0 for `recut`, else 1; descriptors from the loser image and, if it exists, the named winner image), then `delta = weight + await tree_weight(loser_id)`, `UPDATE images SET popularity = popularity + :delta WHERE id = :root` and `UPDATE images SET popularity = 0 WHERE id = :loser`. Flush, never commit. Always write caches from the computed `delta`, never from the loser's cached value.

- [ ] **Step 1: Write failing tests** in `test_popularity_repository.py` with a helper `make_image(session, filename) -> Image` (flush) and `pop(session, image) -> int` (fresh `SELECT`):
  - `test_absorb_moves_the_loser_into_the_winner`: A, B; `absorb(B, A, "cluster_review")`; `pop(A) == 2`, `pop(B) == 0`; the row has `weight == 1`, `kind == "absorb"`, `loser_filename == "b.jpg"`.
  - `test_chain_of_three`: B into A, then A into C: `pop(C) == 3`, `pop(A) == 0`, `pop(B) == 0`.
  - `test_naming_a_discarded_image_credits_its_root`: B into A, A into C, then D into B: `pop(C) == 4`, the D row's `winner_image_id == B.id`.
  - `test_recut_edge_gives_the_cut_exactly_the_sources_popularity`: A has absorbed two (pop 3); `absorb(A, cut, "recut")` gives `pop(cut) == 3`, `pop(A) == 0`; with a source of popularity 1 the cut stays 1.
  - `test_retry_returns_the_same_row_and_changes_nothing`: second identical `absorb` returns an equal `id`, popularity unchanged; also after the winner itself was absorbed elsewhere (retry compares roots).
  - `test_absorbing_into_a_different_tree_raises_already_discarded`: B into A, then `absorb(B, C, ...)` raises `AlreadyDiscardedError` whose `existing.winner_image_id == A.id`; nothing changed.
  - `test_cycle_is_refused`: `absorb(A, A, ...)`, and B into A then `absorb(A, B, ...)`, both raise `CycleError`; no rows written.
  - `test_ghost_winner_is_accepted`: winner id `uuid.uuid4()` with no image row: row written, no error, loser becomes 0.
  - `test_a_drifted_loser_cache_does_not_spread`: set B's cached popularity to 50 by SQL while B has no edges below; after `absorb(B, A, ...)`, `pop(A) == 2`.
- [ ] **Step 2: Run them** — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** as specified above. Use SQLAlchemy Core `update()` with `Image.popularity + delta` expressions.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(popularity): PopularityRepository tree queries and absorb`

### Task 3: `record_dropped_copies` and `reverse`

**Files:**
- Modify: `repository/popularity.py`
- Test: `tests/integration/test_popularity_repository.py`

**Interfaces:**
- Consumes: Task 2's `root_of`, `tree_weight`, `_lock_images`, exceptions.
- Produces:
  - `@dataclass(frozen=True) class DroppedCopy: filename: str; content_hash: str; destination: str`.
  - `async def record_dropped_copies(self, winner_id: uuid.UUID, copies: Sequence[DroppedCopy], batch_run_id: uuid.UUID, source: str = PopularitySource.HASH_IN_BATCH) -> int`: `source` must be `hash_in_batch` or `hash_cross_corpus` (else `ValueError`).
  - `async def reverse(self, absorb_id: uuid.UUID, reason: str) -> PopularityAbsorption`: returns the new reverse row.

**Algorithms:** `record_dropped_copies`: return 0 for an empty list; resolve and lock the winner's root (same retry loop as `absorb`); insert one leaf edge per copy (`loser_image_id` null, `weight` 1, `loser_filename`, `loser_content_hash`, `dedupe_key = f"{batch_run_id}:{content_hash}:{destination}"`) with `INSERT ... ON CONFLICT (dedupe_key) DO NOTHING RETURNING id`; `inserted` = rows returned; `UPDATE images SET popularity = popularity + inserted` on the root; return `inserted`. `reverse`: load the row (must be `kind == "absorb"` and `reversed_by_id is None`, else `ValueError`); `reason` must be in `PopularityReason.ALL`; resolve and lock the loser (if any) and the root of the edge's winner; `s = tree_weight(loser)` (0 for a leaf edge); insert the reverse row (copy `source`, `weight`, ids and descriptors; `reverses_id`, `reason`), set the absorb row's `reversed_by_id`; `UPDATE` the root by `- (weight + s)`; if the loser image exists set its popularity to `1 + s`.

- [ ] **Step 1: Write failing tests** (same file):
  - `test_dropped_copies_raise_the_keepers_popularity`: three copies, `pop(keeper) == 4`, returns 3.
  - `test_the_same_destination_in_the_same_batch_counts_once` (retry) and `test_same_filename_at_a_different_destination_counts_again`.
  - `test_empty_list_and_duplicate_destination_in_one_call`: `[]` returns 0 and writes nothing; two copies with one destination in one call return 1.
  - `test_a_non_hash_source_is_rejected_for_copies`.
  - `test_reverse_restores_the_loser`: B into A then `reverse(row.id, "undo_reject")`: `pop(A) == 1`, `pop(B) == 1`; the original row's `reversed_by_id` equals the new row's id, new row `kind == "reverse"`, `reverses_id` set.
  - `test_reverse_in_the_middle_of_a_chain`: B into A, A into C, reverse B: `pop(C) == 2`, `pop(A) == 1`, `pop(B) == 1`; then reverse A's edge: `pop(C) == 1`, `pop(A) == 1`.
  - `test_reverse_in_the_other_order`: same setup, reverse A's edge first (`pop(A) == 2`, `pop(C) == 1`), then B's (`pop(A) == 1`, `pop(B) == 1`).
  - `test_reverse_takes_the_subtree_with_it`: B holds D (D into B), B into A: reverse B's edge: `pop(B) == 2`, `pop(A) == 1`.
  - `test_reverse_a_leaf_edge_lowers_the_root`: after dropped copies, reversing a leaf row with reason `correction` lowers the keeper by 1.
  - `test_reverse_when_the_loser_row_is_gone`: delete the loser image by SQL, reverse: root lowered, no error.
  - `test_double_reverse_and_reverse_of_a_reverse_row_raise_value_error`; `test_an_unknown_reason_raises`.
  - `test_after_a_reversal_the_loser_can_be_absorbed_again`: absorb, reverse, absorb again succeeds (unique open-loser index).
  - `test_recut_with_children_then_reverse`: the Review Focus recut case, plus its reversal returning the source to its old popularity.
- [ ] **Step 2: Run them** — Expected: FAIL.
- [ ] **Step 3: Implement** both methods as specified.
- [ ] **Step 4: Run the whole file** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(popularity): record_dropped_copies and reverse`

### Task 4: `popularity_of`, `breakdown`, `verify`

**Files:**
- Modify: `repository/popularity.py`
- Test: `tests/integration/test_popularity_repository.py`

**Interfaces:**
- Produces:
  - `async def popularity_of(self, image_id: uuid.UUID) -> int`: `0` if the image has an open edge as loser, else `1 + tree_weight`.
  - `async def breakdown(self, image_id: uuid.UUID) -> dict[str, int]`: `{"own": 1, <source>: weight sum, ...}` over the open edges in the tree below the image; only sources that occur; `{"own": 1}` for a lone image.
  - `@dataclass(frozen=True) class Drift: image_id: uuid.UUID; cached: int; expected: int`.
  - `async def verify(self, image_ids: Collection[uuid.UUID] | None = None) -> list[Drift]`: expected value per existing image computed from all open edges loaded into memory (adjacency dict, iterative; no per-image query); `None` checks every image; trees whose root image no longer exists are ignored because only existing images are compared.

- [ ] **Step 1: Write failing tests:**
  - `test_breakdown_sums_weights_by_source_over_the_tree`: A absorbs B (`cluster_review`), C (`ingest_tier_a`); B absorbed D (`agent_review`), and two dropped copies: `breakdown(A) == {"own": 1, "cluster_review": 1, "ingest_tier_a": 1, "agent_review": 1, "hash_in_batch": 2}`; the numbers add up to `pop(A) == 6`.
  - `test_breakdown_of_a_lone_image` and `test_popularity_of_matches_the_cache_after_operations` (compare `popularity_of` with `pop` for every image after a mixed sequence).
  - `test_verify_reports_nothing_when_consistent` and `test_verify_reports_drift`: set A's cache to 9 by SQL, `verify() == [Drift(A.id, 9, expected)]`; `verify([B.id])` limits the check.
  - `test_verify_ignores_orphan_trees`: absorb B into A, delete A's image row: `verify()` has no entry for A and does not fail; B still reads 0.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** the three methods.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(popularity): popularity_of, breakdown and verify`

### Task 5: Randomized and concurrent behaviour

**Files:**
- Test: `tests/integration/test_popularity_properties.py` (no production code unless a test finds a bug)

- [ ] **Step 1: Write the tests:**
  - `test_random_absorb_and_reverse_sequences_never_drift`: parametrized over seeds `range(25)`; 8 images; 60 steps, each step randomly one of: `absorb(random loser, random winner, random source)` (swallow `CycleError` and `AlreadyDiscardedError`), `record_dropped_copies` to a random image with fresh destinations, `reverse` of a random open row (swallow `ValueError`), delete a random image row by SQL; after every step `assert await repo.verify() == []`, and for every existing non-discarded image `pop >= 1`.
  - `test_concurrent_absorbs_in_one_tree_lose_no_update` (real transactions, **no** `db_session`): with `db_engine`, commit images A, B, C, D in one connection; then in two separate `AsyncSession`s run concurrently with `asyncio.gather` `absorb(B, A, ...)` + commit, and `absorb(C, A, ...)` + commit, and also `absorb(A, D, ...)` in a third; afterwards a fresh session shows `verify() == []` and `pop(D) == 4`. Clean up in `finally` by deleting the rows it created (images and their log rows). Mark with a docstring that it commits for real on `ocrdb_test`.
- [ ] **Step 2: Run** — Expected: PASS. If the randomized test finds drift, fix the repository (not the test) and add the failing sequence as a named regression test.
- [ ] **Step 3: Commit** `test(popularity): randomized and concurrent invariants`

### Task 6: Check and repair job

**Files:**
- Create: `batch/check_image_popularity.py`
- Test: `tests/integration/test_check_image_popularity.py`

**Interfaces:**
- Consumes: `PopularityRepository.verify`, `Drift`.
- Produces: `async def run(session, fix: bool) -> list[Drift]` (returns the drift found; when `fix`, rewrites `images.popularity` to `expected` for each; never commits); `async def main(fix: bool) -> None` (opens `AsyncSessionLocal`, runs, commits only when `fix`, prints one line per drift and a total; exit code unchanged); CLI flags `--env {metal,general,it}` and `--fix`, with `load_env(args.env)` as in `batch/remove_singletons.py`. No `ProgressTracker`, so no Windows encoding gotcha.

- [ ] **Step 1: Write failing tests:** `test_run_without_fix_reports_and_changes_nothing` (drift set by SQL; returns it; cache unchanged), `test_run_with_fix_rewrites_the_cache` (cache equals `expected`; second `run` returns `[]`), `test_run_is_clean_on_a_consistent_database`.
- [ ] **Step 2: Run** — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `run`, `main` and the `__main__` block following `batch/remove_singletons.py`.
- [ ] **Step 4: Run** the new tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat(popularity): check_image_popularity job`

### Task 7: Documentation

**Files:**
- Modify: `docs/image-derived-data.md`, `docs/batches/inventory.md`, `docs/schema.md`, `CLAUDE.md`

- [ ] **Step 1: `docs/image-derived-data.md`:** add under `## Inventory` the row ``| `popularity_absorptions` | none | Not copied; it has no foreign key to `images` (plain ids, so history survives deletion). A cut's `images.popularity` is set by the recut approval edge (popularity epic P2), never copied | not applicable |`` and one sentence under the table that `images.popularity` is a column, not a table, and a new image starts at 1.
- [ ] **Step 2: `docs/batches/inventory.md`:** add a row for `check_image_popularity` (shell only, not admin-triggerable, not scheduled, not run-tracked; `--fix`; reports drift of `images.popularity` against the open edges); do not change the registry counts in the summary table (it is not in the registry).
- [ ] **Step 3: `docs/schema.md`:** document `images.popularity` and `popularity_absorptions` in the style of the neighbouring entries, linking the spec.
- [ ] **Step 4: `CLAUDE.md`:** add a `check_image_popularity` entry to the "Batch pipeline" maintenance list (one short paragraph: what it checks, `--fix`, shell only, links the P1 spec).
- [ ] **Step 5: Run the docs guards:** `python -m pytest tests/docs/ -v` — Expected: PASS.
- [ ] **Step 6: Commit** `docs(popularity): inventory, schema and batch list for P1`

### Task 8: Whole-scope verification

- [ ] **Step 1:** Run the full `tests/integration/` root with `DATABASE_URL` set (a shared-schema change touches `images`; `CLAUDE.md` requires the whole root, not only the new files). Expected: all pass; any failure outside the new files is reported with its output, not skipped.
- [ ] **Step 2:** Run `python -m pytest tests/docs/ -v` and, separately, `cd Backend && python -m pytest` (the backend tests mock the DB but import `Storage.models`). Expected: PASS.
- [ ] **Step 3:** Set the P1 spec to `Status: done` (it reads `planned` with a `Plan:` line today); leave the epic as `approved`.
- [ ] **Step 4:** Hand back to the owner, with the list of owner-only steps: run `alembic upgrade head` on metal, general, IT (after re-chaining `down_revision` if other revisions landed), review and merge the branch `worktree-meme-popularity`, and run `python -m batch.check_image_popularity --env <env>` once per environment afterwards (expected: no drift, every image 1).
- [ ] **Step 5: Commit** `docs(popularity): P1 spec status`
