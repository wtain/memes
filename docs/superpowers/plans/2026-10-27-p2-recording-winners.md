# P2 Recording Winners Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire every path that confirms a duplicate (hash dedup, ingestion review, flagging, `detect_file_duplicates`, `ingest_abort`) to `PopularityRepository`, so popularity moves and every undo reverses it, without ever failing a dedup step.

**Architecture:** One guard helper runs each popularity call inside a savepoint and swallows and logs any error. Each path calls it after its own database write, naming a winner it already knows. `mark_flagged` gains an optional `duplicateOf`; the `/duplicates` page and the `review-duplicates` command pass it. No migration.

**Tech Stack:** Python 3.11, async SQLAlchemy, FastAPI, pytest (separate roots), React/TypeScript with vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-27-p2-recording-winners-design.md` (read it and the P1 spec `2026-10-26-p1-data-model-and-absorption-rules-design.md` first; the epic README holds C1 to C7).

## Global Constraints

- Work only in the worktree `H:\workspace_sandbox\memes\.claude\worktrees\meme-popularity` (branch `worktree-meme-popularity`). No merging, no migrations run, nothing against metal, general or IT. Never use the main `DATABASE_URL`.
- Python: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest`. Integration tests need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line. Run `Backend/tests`, `tests/integration`, `tests/docs` as separate invocations; never run two integration sessions at once.
- Repositories and the guard never commit; the caller owns the transaction (`get_async_db`, `IngestionRepository.commit`, the batch's own commit).
- A popularity failure must never change or fail the dedup action it follows (E1). Input validation (422) happens before any write.
- Sources: `hash_in_batch`, `hash_cross_corpus`, `ingest_tier_a`, `ingest_tier_b`, `cluster_review`, `agent_review`, `file_duplicates`, `recut` (constants on `PopularitySource`). Reversal reasons: `undo_reject`, `unflag`, `abort`, `correction` (`PopularityReason`).
- `backend_api.md` must match the router. Frontend changes need `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` from `Frontend/memes-frontend/` (run `pnpm install --frozen-lockfile` there first if `node_modules` is missing).
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`. Use plain separate git commands.

## Review Focus

Failure modes the spec implies; each has a test in the task that owns the code.

1. A popularity error in the middle of a reject, flag or file move leaves the reject, flag or move done and the outer transaction usable (Tasks 1, 2, 3, 4).
2. A reject whose image has no candidate neighbour, or only rejected ones, records nothing and does not error (Task 3).
3. Unflag or undo-reject of an image that never had an edge is a quiet no-op (Tasks 3, 4).
4. The same file name dropped again in a later hash-dedup invocation counts again; recording twice in one invocation does not (Task 2).
5. Flagging with `duplicateOf` and a missing or unknown `reason` is a 422 that writes nothing; re-flagging against a second winner leaves the flag and the first edge (Task 4).
6. A batch with no duplicates at all makes no popularity calls and reports zeros (Task 2).

---

### Task 1: Repository support (guard, WinnerGoneError, reverse by loser)

**Files:**
- Modify: `repository/popularity.py`
- Modify: `tests/integration/test_popularity_repository.py` (the ghost-winner test changes meaning)
- Modify: `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-26-p1-data-model-and-absorption-rules-design.md` (wording of the deleted-root locking; see step 5)
- Test: `tests/integration/test_popularity_guard.py` (new)

**Interfaces:**
- Produces:
  - `class WinnerGoneError(Exception)` in `repository/popularity.py`.
  - `PopularityRepository.reverse_open_edge(self, loser_id: uuid.UUID, reason: str, sources: Collection[str] | None = None) -> PopularityAbsorption | None`: finds the open absorb edge whose `loser_image_id == loser_id` (and whose `source` is in `sources`, when given), reverses it with `reverse`, returns the reverse row; returns `None` when there is no such edge.
  - `async def guarded(session: AsyncSession, label: str, call: Callable[[], Awaitable[T]]) -> T | None`: runs `await call()` inside `session.begin_nested()`; on any `Exception` logs at WARNING via the module logger with `label` and the exception, and returns `None`; the savepoint is rolled back and the outer transaction stays usable. `BaseException` (cancellation) is not caught.

- [ ] **Step 1: Write the failing tests.** In `tests/integration/test_popularity_guard.py`:
  - `test_guarded_returns_the_value_on_success`: `guarded(db_session, "x", lambda: repo.absorb(b.id, a.id, "cluster_review"))` returns a row; `pop(a) == 2`.
  - `test_guarded_swallows_a_repository_error_and_keeps_earlier_work`: add image `c` and absorb it into `a` first (outside the guard), then `guarded(...)` an `absorb(b.id, a.id, "bogus_source")`; result is `None`, `pop(a) == 2`, and a following `db_session.execute(select(1))` still works.
  - `test_guarded_rolls_back_a_database_error_without_poisoning_the_transaction`: `call` executes an `INSERT` violating a constraint (for example `UPDATE images SET popularity = -1`); result `None`; the session still executes a query and earlier flushed rows are intact.
  - `test_guarded_logs_the_label` using `caplog`: the label appears in a WARNING record.
  - `test_reverse_open_edge_returns_none_without_an_edge`, and `test_reverse_open_edge_respects_the_sources_filter` (an edge of source `cluster_review` is not reversed when `sources={"ingest_tier_a"}`; it is when `sources={"cluster_review"}`; after reversal the winner is back to 1 and the loser to 1).
  - `test_absorb_into_a_winner_with_no_image_row_raises_winner_gone`: absorb `b` into `uuid.uuid4()` raises `WinnerGoneError` and writes no row (`edge_count == 0`). Same for `record_dropped_copies` with a random winner id (use `make_run` and `copies`).
- [ ] **Step 2: Run them to see them fail.** `DATABASE_URL=... python -m pytest tests/integration/test_popularity_guard.py -v` expects ImportError/failures.
- [ ] **Step 3: Implement** the three items above in `repository/popularity.py`. `absorb` and `record_dropped_copies` raise `WinnerGoneError` when, after the lock-and-re-resolve step, `await self._image(root)` is `None`, before writing anything. Add a module `logger = logging.getLogger(__name__)` if none exists.
- [ ] **Step 4: Update the old ghost test.** In `tests/integration/test_popularity_repository.py` replace `test_ghost_winner_is_accepted` with `test_ghost_winner_raises_winner_gone` (expects `WinnerGoneError`, loser still 1, no row). Check the other P1 tests that name a ghost root (grep `uuid.uuid4()` used as a winner) and adjust only those that relied on acceptance.
- [ ] **Step 5: Amend the P1 spec.** In its "Deleted images" part, state that `absorb` and `record_dropped_copies` raise `WinnerGoneError` when the winner's root has no `images` row, and that no advisory lock on deleted roots is needed (the wired paths cannot write into a tree whose root is gone). Add the P2 design to its Follow-ups if not present.
- [ ] **Step 6: Run** `tests/integration/test_popularity_guard.py`, `test_popularity_repository.py`, `test_popularity_properties.py`, `test_check_image_popularity.py`: all pass.
- [ ] **Step 7: Commit** `feat(popularity): guard helper, WinnerGoneError, reverse by loser`.

---

### Task 2: Hash dedup records copies

**Files:**
- Modify: `batch/ingest_hash_dedup.py`
- Modify: `tests/integration/test_ingest_hash_dedup.py` (stats expectations in the end-to-end test)
- Test: `tests/integration/test_ingest_hash_dedup_popularity.py` (new)

**Interfaces:**
- Consumes: `PopularityRepository.record_dropped_copies(winner_id, copies, batch_run_id, source)`, `DroppedCopy(filename, content_hash, destination)`, `guarded` (Task 1).
- Produces, in `batch/ingest_hash_dedup.py`:
  - `async def active_winners_by_hash(session, hashes: Collection[str]) -> dict[str, uuid.UUID]`: for each hash with at least one `active` image, the image with the highest `popularity`, ties to the earliest `created_at`.
  - `def plan_dropped_copies(hash_to_files: dict[str, list[str]], invocation_id: str) -> dict[str, tuple[str, list[DroppedCopy]]]`: per hash with more than one file, the keeper name (lexicographically first, same rule as `dedupe_in_batch`) and a `DroppedCopy` for each extra file, `destination = f"{invocation_id}:{filename}"`.
  - `run(...)` stats gain `popularity_copies_recorded` and `popularity_skipped` (integers, summed by `accumulate_stats`).

- [ ] **Step 1: Write the failing tests** (`test_ingest_hash_dedup_popularity.py`, real files in `tmp_path`, `run(db_session, source, base, batch_id)` as in the existing end-to-end test):
  - `test_three_identical_files_leave_the_keeper_at_three`: three byte-identical files, none in the corpus; after `run`, the pending keeper's `popularity == 3`, stats `popularity_copies_recorded == 2`, `popularity_skipped == 0`, and `verify()` reports no drift.
  - `test_a_file_identical_to_an_active_image_raises_it_by_one`: active image whose `content_hash` equals the file's sha256; after `run` its `popularity == 2`; no pending image exists for the file.
  - `test_in_batch_extras_of_an_active_match_all_count_for_the_active_image`: two identical files matching an active image: active `popularity == 3`.
  - `test_two_actives_with_one_hash_pick_the_more_popular` (set one active's `popularity` to 5 directly): that one rises; ties go to the earlier `created_at`.
  - `test_the_same_name_dropped_in_a_later_invocation_counts_again`: run once, drop a new file with the same name and content, run again with the same `batch_id`; the keeper's popularity rises by 1 more. Move the first batch's `duplicates/` file away first if the move would collide (the existing behaviour of `shutil.move`).
  - `test_recording_twice_in_one_invocation_adds_nothing`: call the recording step twice with the same copies and invocation id (call `record_dropped_copies` through the same code path the run uses, or test `plan_dropped_copies` output reused); second call inserts 0.
  - `test_no_duplicates_means_no_popularity_calls`: unique files only; stats zeros; no `popularity_absorptions` rows.
  - `test_a_popularity_error_does_not_fail_the_run`: `monkeypatch` `PopularityRepository.record_dropped_copies` to raise; `run` still registers survivors and moves files, `popularity_skipped` equals the copies not recorded, the session still usable.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** In `run()`: build `invocation_id = uuid.uuid4().hex` and `plan = plan_dropped_copies(hash_to_files, invocation_id)` before the moves; after `register_and_move_to_base_path`, resolve winners per hash: the active winner from `active_winners_by_hash(session, plan.keys() ∪ survivor hashes)` if any (then the keeper's own file is also a copy, source `hash_cross_corpus`; the extras source `hash_in_batch`), otherwise the registered keeper's id (map keeper filename → id by zipping the remaining survivors' keys, in order, with the ids `register_and_move_to_base_path` returns), extras as `hash_in_batch`. Each `record_dropped_copies` call goes through `guarded(session, f"hash copies {content_hash}", ...)`; a `None` result adds the call's copy count to `popularity_skipped`, otherwise the return value to `popularity_copies_recorded`. Note a hash group with a single file and an active match is also a copy (cross-corpus keeper only), so plan entries must cover it: handle that case in the resolver rather than in `plan_dropped_copies`. Do not change `dedupe_in_batch` or `dedupe_cross_corpus` (existing tests call them).
- [ ] **Step 4: Adjust** the stats assertions in `test_run_end_to_end_stats_and_final_state` for the two new keys.
- [ ] **Step 5: Run** the whole `tests/integration/test_ingest_hash_dedup*.py` set: pass.
- [ ] **Step 6: Commit** `feat(popularity): hash dedup records dropped copies`.

---

### Task 3: Ingestion review rejects and undoes

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py`
- Modify: `Backend/app/services/ingestion_service.py`
- Test: `tests/integration/test_ingestion_popularity.py` (new; follow the helpers in `test_ingestion_partial_resolve.py`)

**Interfaces:**
- Consumes: `PopularityRepository.absorb`, `reverse_open_edge`, `guarded` (Task 1); `_tier_band(tier) -> (low, high)` in the service.
- Produces:
  - `IngestionRepository.pick_duplicate_winner(self, image_id, tier: str, distance_low: float, distance_high: float) -> uuid.UUID | None`: among images paired with `image_id` in `tmp_duplicates` inside the band whose tier `reviewed_at` is NULL and whose status is `active` or `pending`, excluding `image_id` itself, return the best by: active before pending, then highest `popularity`, then earliest `created_at`; `None` when there is none. Its result must not depend on `image_id`'s own status.
  - `IngestionService.resolve` and `undo_reject` also reverse or record popularity as below.

- [ ] **Step 1: Write the failing tests:**
  - `test_pick_duplicate_winner_prefers_active_then_popularity_then_age` (three neighbours; then remove the active one and expect the higher-popularity pending; equal popularity picks earlier `created_at`).
  - `test_pick_ignores_rejected_reviewed_and_out_of_band_neighbours`.
  - `test_reject_records_the_edge_and_undo_reverses_it`: pending `a` paired with active `w` (distance 0.02), `resolve("tier_a", [{a, reject}])` (patch `image_store.move_to_rejected` as in the partial-resolve test): `w` popularity 2, `a` 0, edge source `ingest_tier_a` with `batch_run_id` equal to the image's `ingestion_batch_id`; then `undo_reject(a)` (patch `move_from_rejected`): `w` 1, `a` 1, the reverse row has reason `undo_reject`.
  - `test_tier_b_uses_its_own_source`.
  - `test_reject_with_no_neighbour_records_nothing`: lone pending image; reject succeeds, `rejected == [a]`, no edge rows.
  - `test_reject_when_all_neighbours_are_rejected_records_nothing`.
  - `test_keep_changes_nothing`.
  - `test_undo_reject_without_an_edge_is_a_no_op` (reject with no neighbour, then undo: returns the pending dict, no error).
  - `test_a_popularity_error_does_not_undo_the_reject`: `monkeypatch` `PopularityRepository.absorb` to raise; the image is `rejected`, `rejected == [a]`, `failed == []`, and a following decision in the same call still works.
  - `test_move_failed_still_counts`: `move_to_rejected` raising leaves the edge recorded (the database rejection is what counts).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** In `resolve`, after `reject_image` returns a filename and before `self.repo.commit()`: look up the winner with `pick_duplicate_winner(image_id, tier, *_tier_band(tier))`; if found, `await guarded(self.repo.session, "ingest reject", lambda: PopularityRepository(self.repo.session).absorb(image_id, winner, source_for_tier, batch_run_id))` where `source_for_tier` maps `tier_a`→`ingest_tier_a`, `tier_b`→`ingest_tier_b` and `batch_run_id` is the image's `ingestion_batch_id` (add a small repository getter if the service lacks it). In `undo_reject`, after the repository call succeeds and before the file move, `guarded(... reverse_open_edge(image_id, "undo_reject", {"ingest_tier_a", "ingest_tier_b"}))`. The existing commit points in the repository/service are unchanged; make sure the undo path commits as it did before (check whether the router or service commits after `undo_reject` and keep that).
- [ ] **Step 4: Run** `tests/integration/test_ingestion_popularity.py` and all `tests/integration/test_*ingestion*.py`: pass.
- [ ] **Step 5: Commit** `feat(popularity): ingestion rejects and undo record and reverse edges`.

---

### Task 4: `mark_flagged` / `unmark_flagged` backend

**Files:**
- Modify: `Backend/app/api/images.py`, `Backend/app/services/image_service.py`
- Modify: `backend_api.md` (the Mark Meme as Flagged and Unmark sections)
- Modify: `Backend/tests/test_image_service.py` (existing asserts at the `set_flagged` tests keep passing; add new ones)
- Test: `tests/integration/test_popularity_flagging.py` (new)

**Interfaces:**
- Consumes: `PopularityRepository.absorb`, `reverse_open_edge`, `guarded`.
- Produces:
  - Router: `PUT /api/images/meme/{image_id}/mark_flagged?reason=...&duplicateOf=<image id>`; `duplicateOf` optional.
  - `ImageService.mark_flagged(self, image_id, reason: str | None = None, duplicate_of: str | None = None)`.
  - A module constant in `image_service.py`: `FLAG_REASON_SOURCES = {"duplicate_review": "cluster_review", "agent_review": "agent_review"}`.
  - Rules: `duplicate_of` set and `reason` not a key of `FLAG_REASON_SOURCES` → `HTTPException(422)`; `duplicate_of` not a valid UUID, or equal to `image_id` → 422; all checked before `set_flagged`. After `set_flagged(image_id, True, reason)`, `guarded(self.repo.session, "flag", lambda: PopularityRepository(self.repo.session).absorb(UUID(image_id), UUID(duplicate_of), source))`. `unmark_flagged` calls `set_flagged(image_id, False)` then `guarded(... reverse_open_edge(UUID(image_id), "unflag", {"cluster_review", "agent_review", "file_duplicates"}))`.

- [ ] **Step 1: Write the failing tests.** Integration (`ImageService(ImageRepository(db_session), MagicMock(), MagicMock())`):
  - `test_flag_with_duplicate_of_raises_the_winner_and_zeroes_the_loser` (reason `duplicate_review`, source `cluster_review`; reason `agent_review`, source `agent_review`).
  - `test_flag_without_duplicate_of_changes_no_popularity` (even with reason `duplicate_review`).
  - `test_duplicate_of_with_another_or_no_reason_is_422_and_writes_nothing` (flag not set, no edge).
  - `test_duplicate_of_not_a_uuid_or_equal_to_the_image_is_422`.
  - `test_unflag_restores_both_and_records_reason_unflag`.
  - `test_unflag_without_an_edge_is_a_no_op`.
  - `test_reflag_with_the_same_winner_is_idempotent` (popularity unchanged by the second call).
  - `test_reflag_against_another_winner_keeps_the_flag_and_the_first_edge` (second winner's popularity unchanged; no exception).
  - `test_a_popularity_error_does_not_fail_the_flag` (patch `absorb` to raise; the flag is set; session usable).
  Backend mocked tests: `mark_flagged(id, "duplicate_review", other_id)` calls `set_flagged` once, and with `PopularityRepository` patched in `Backend.app.services.image_service`, `absorb` is awaited with the two UUIDs and `"cluster_review"`; an invalid combination raises `HTTPException` with `status_code == 422` before `set_flagged` is awaited.
- [ ] **Step 2: Run them to see them fail** (Backend: `cd Backend; python -m pytest tests/test_image_service.py`).
- [ ] **Step 3: Implement** the router parameter (`duplicateOf: str | None = Query(None, max_length=36)`) and the service changes as specified.
- [ ] **Step 4: Update `backend_api.md`:** document `duplicateOf`, the reason-to-source rule, the 422 cases, that without it a flag never changes popularity, and that unflag reverses the edge the flag recorded. Add an example with `duplicateOf` and `reason=agent_review`.
- [ ] **Step 5: Run** `Backend/tests` (whole root) and `tests/integration/test_popularity_flagging.py`: pass.
- [ ] **Step 6: Commit** `feat(popularity): mark_flagged duplicateOf, unflag reverses`.

---

### Task 5: Frontend and agent command pass `duplicateOf`

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts` (and any other class implementing `MemesApi`; grep `markImageIsFlagged`)
- Modify: `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`, `MemesDuplicatesList.test.tsx`
- Modify: `.claude/commands/review-duplicates.md`

**Interfaces:**
- Produces: `markImageIsFlagged(id: string, reason?: string, duplicateOf?: string): Promise<void>`; `HttpMemesApi` appends `duplicateOf=<encoded>` to the query string when given, alongside `reason`.

- [ ] **Step 1: Write the failing tests.** In `MemesDuplicatesList.test.tsx` change the two existing assertions (`toHaveBeenCalledWith('b', 'duplicate_review')`) to expect `('b', 'duplicate_review', 'a')` where `a` is the keeper, and keep `not.toHaveBeenCalledWith('a', expect.anything())`. Add an `HttpMemesApi` test (create `HttpMemesApi.test.ts` only if none exists; otherwise extend it) that `markImageIsFlagged('x','duplicate_review','y')` calls fetch with a URL containing `reason=duplicate_review&duplicateOf=y`, and that without the third argument the URL has no `duplicateOf`. `MemeCard`/`MemeDetails` callers keep their one-argument calls (existing tests `toHaveBeenCalledWith('meme-1')` stay green).
- [ ] **Step 2: Run** `vitest run` to see the failures.
- [ ] **Step 3: Implement** the signature, the URL building and `handleKeepBest` passing `keeper` as the third argument.
- [ ] **Step 4: Update `review-duplicates.md`** "Applying decisions": the call becomes `PUT .../mark_flagged?reason=agent_review&duplicateOf={kept_id}` with the kept member's id, and say that a cluster with no kept member (all excluded) must not be flagged through this command.
- [ ] **Step 5: Run** `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` from `Frontend/memes-frontend/`: all clean.
- [ ] **Step 6: Commit** `feat(popularity): duplicates page and review command pass duplicateOf`.

---

### Task 6: `detect_file_duplicates` records its winner

**Files:**
- Modify: `batch/detect_file_duplicates.py`
- Test: `tests/integration/test_detect_file_duplicates_popularity.py` (new)

**Interfaces:**
- Produces: `async def flag_cluster_duplicates(session, extras_repo, keeper_id, duplicate_ids, metrics) -> None` extracted from the loop in `main()`: for each duplicate `await extras_repo.set_flagged(dup_id, True)`, `metrics.increment("flagged")`, then `guarded(session, "file duplicates", lambda: PopularityRepository(session).absorb(dup_id, keeper_id, "file_duplicates"))`; a `None` result increments `metrics` key `popularity_skipped`. `main()` calls it with the keeper and duplicates it already computed; behaviour otherwise unchanged (the already-flagged group skip stays).

- [ ] **Step 1: Write the failing tests:** `test_keeper_rises_by_the_number_flagged` (three images, keeper plus two duplicates: keeper 3, others 0, edges source `file_duplicates`); `test_a_popularity_error_still_flags` (patch `absorb` to raise: both flagged, `popularity_skipped == 2`); `test_flags_have_no_reason` (flag stored as before).
- [ ] **Step 2: Run to see them fail; Step 3: extract and implement; Step 4: run** these and `tests/integration/test_*file_duplicates*.py`: pass.
- [ ] **Step 5: Commit** `feat(popularity): detect_file_duplicates records its keeper as winner`.

---

### Task 7: `ingest_abort` reverses surviving-winner edges

**Files:**
- Modify: `batch/ingest_abort.py`
- Modify: `tests/integration/test_ingest_abort.py` only if an existing assertion needs the new metric keys
- Test: `tests/integration/test_ingest_abort_popularity.py` (new; use `_make_run`/`_make_image` style helpers from `test_ingest_abort.py`)

**Interfaces:**
- Consumes: `PopularityRepository.reverse`, `guarded`.
- Produces: inside `run(...)`, before the `delete(Image)`, for every open absorb edge whose `loser_image_id` is in the aborted set (ids of `list_abortable_images`) and whose `winner_image_id` is not in that set: `guarded(session, "abort", lambda: repo.reverse(edge.id, "abort"))`. Metrics: `popularity_reversed`, `popularity_skipped`. Edges with both ends in the set are left alone; edges with no loser image (hash leaf edges) are never touched.

- [ ] **Step 1: Write the failing tests:** `test_abort_returns_the_surviving_winners_popularity` (active `w`, pending rejected `r` absorbed into it with source `ingest_tier_a` and the batch id: after `run`, `w == 1`, a reverse row with reason `abort` exists, `r` is gone); `test_a_chain_inside_the_batch_gives_the_survivor_back_its_count` (pending `c` into pending `b` into active `w`: `w == 1` after abort, only `b`'s edge reversed); `test_edges_between_aborted_images_are_left_orphaned` (no winner outside: no reverse rows, no error); `test_hash_copies_recorded_for_the_batch_stay_counted` (leaf edges on an active image survive an abort); `test_a_popularity_error_does_not_stop_the_abort` (patch `reverse` to raise: images are still unregistered, `popularity_skipped >= 1`); `test_abort_with_nothing_open_changes_nothing`. Finish each with `verify()` on surviving ids reporting no drift.
- [ ] **Step 2: Run to see them fail; Step 3: implement; Step 4: run** both abort test files: pass.
- [ ] **Step 5: Commit** `feat(popularity): ingest_abort reverses edges into surviving images`.

---

### Task 8: Docs, spec status and full verification

**Files:**
- Modify: `CLAUDE.md` (the `ingest_hash_dedup`, `ingest_abort`, `detect_file_duplicates` entries: one sentence each on what popularity now does; the ingestion review paragraph)
- Modify: `docs/batches/` pages that describe those jobs (grep `docs/batches` for `ingest_hash_dedup`, `ingest_abort`, `detect_file_duplicates` and update only passages describing their effects; `inventory.md` rows are unchanged)
- Modify: the P2 spec status line to `done`, adding `Plan:` link; the epic README status of P2 in its Designs list if it tracks status

- [ ] **Step 1: Update the docs** as above, keeping each addition to one or two sentences.
- [ ] **Step 2: Run the full roots, each separately:** `Backend/tests`; `tests/integration` (full, with `DATABASE_URL`); `tests/docs`; frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. All must pass; report counts.
- [ ] **Step 3: Check the branch is clean** (`git status`), then commit `docs(popularity): P2 docs and spec status`.
