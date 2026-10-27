# P2: Recording winners in each duplicate path

Status: done
Plan: `docs/superpowers/plans/2026-10-27-p2-recording-winners.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; read it first) and `2026-10-26-p1-data-model-and-absorption-rules-design.md` (the repository this design calls)
Follow-ups: P3 API and details page, P4 corrections and audit (not written yet)

## Purpose

P1 built the machinery (`PopularityRepository`: `absorb`, `record_dropped_copies`, `reverse`) and nothing calls it.
P2 wires every path that confirms a duplicate to that machinery, so the numbers start to move. After P2 the
database holds correct popularity; nothing shows it yet (P3).

## Decisions made with the owner

| # | Decision |
|---|---|
| E1 | **Bookkeeping never fails a dedup step.** Every popularity call runs in a savepoint (`session.begin_nested()`); any error (lock conflict, database error, `AlreadyDiscardedError`, `CycleError`, winner gone) is caught, logged with the ids involved, counted in the run's metrics where the path has any, and skipped. The dedup action itself (move, reject, flag) always proceeds. `check_image_popularity` is the repair path for drift. |
| E2 | **A flag only counts when it names a winner.** `mark_flagged` gets an optional `duplicateOf`. Without it the flag is pure exclusion and popularity is untouched, because flagging is also used for non-duplicate reasons (manual exclusion from the details or admin pages). |
| E3 | **Manual flags that were really duplicates wait for P4.** No "duplicate of" field on the flag button in P2. P4 adds an "attach to a winner" action that works on images already flagged (and so also fixes the past); it will need a ninth source `manual` (a CHECK-constraint migration the owner runs). |
| E4 | **Callers are updated:** the `/duplicates` page (keep best) and the `review-duplicates` agent command pass `duplicateOf`. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| F1 | **Hash dedup winner.** Per hash group: if an active image has that hash, every file of the group is a copy of it (the keeper file as `hash_cross_corpus`, the others as `hash_in_batch`). Several active images with the same hash: the one with the highest popularity, ties to the earliest `created_at` (the C3 rule). Otherwise the registered keeper gets one `hash_in_batch` copy per extra file, so it starts at the number of files in the drop. |
| F2 | **Hash dedup idempotency key** is the batch run id, content hash, inbox file name and a per-invocation id, not the destination path. `shutil.move` into `duplicates/` either fails or overwrites when the name exists, so the destination is not unique per file. A same-named file dropped again in a later invocation is another copy and counts. P1's key format (`batch:hash:destination`) is used with this string in the destination slot; nothing in the table changes. |
| F3 | **Source from reason.** `mark_flagged` maps `reason` to the source: `duplicate_review` gives `cluster_review`, `agent_review` gives `agent_review`. `duplicateOf` with any other or missing reason is a 422, so an unknown caller cannot silently pick a source. The agent command starts sending `reason=agent_review` (today it sends none). |
| F4 | **`ingest_abort` reverses** (reason `abort`) the open edges that rejected or pending batch images added to images that survive the abort. Edges whose both ends are deleted are left (orphaned tree, intended in P1). Abort never moves files in `duplicates/` back. Copies credited to an `active` winner stay counted; copies credited to a pending keeper that abort deletes disappear with it (the edge is orphaned and the files stay in `duplicates/`). |
| F5 | **Deletion jobs change nothing.** `move_flagged` and `unregister_deleted_images` delete rows only; edges outlive their loser (P1). A root removed without a winner just ends its popularity. |
| F6 | **Recut is a contract only.** At approve (R5, which does not exist yet) the code calls `absorb(source, cut, "recut")`: a weight-0 edge, so the cut ends with the source's popularity. P2 writes this in the epic and does not add code. |

## Behaviour per path

**Failure wrapper.** One helper (left to the builder where it lives) takes a label and an awaitable, runs it inside
`begin_nested()`, catches `Exception`, logs it and returns `None`. Every call below goes through it.

**`batch/ingest_hash_dedup.py`.** The moves into `duplicates/` happen before any row exists, and the keeper is
registered afterwards. So the moved copies are collected as `DroppedCopy` values (file name, content hash, destination)
while moving, then recorded in one `record_dropped_copies` call per winner after `register_and_move_to_base_path`
(in-batch winners) or right after the cross-corpus lookup (active winners), inside the run's transaction. Stats gain
`popularity_copies_recorded` and `popularity_skipped`. The existing accepted crash posture stays: a crash after some
files moved and before the commit loses their counts, and `check_image_popularity` cannot see it (the file has no
row). Known gap, not addressed.

**Ingestion review (`IngestionService.resolve`, `undo_reject`).** On a reject decision, after the database write
(`reject_image`) succeeds, the winner is chosen server-side by C3: the active-library member of the image's cluster in
this review's tier; else a surviving pending member (not rejected in this call or before); among several, the highest
popularity, ties to the earliest `created_at`. Then `absorb(rejected, winner, ingest_tier_a | ingest_tier_b,
batch_run_id)`. No eligible winner: skip and log. A keep decision does nothing. `undo_reject` reverses the open edge
whose loser is that image (reason `undo_reject`); no edge, no-op. The file-move outcome (`move_failed`) does not
affect popularity: the database rejection is what counts.

**Flagging (`PUT /api/images/meme/{id}/mark_flagged?duplicateOf=<id>&reason=...`).** After the flag is set, if
`duplicateOf` is present: `absorb(id, duplicateOf, source-from-reason)`. A repeat of the same call returns the existing
row (P1 retry rule); the same loser flagged against a different winner is `AlreadyDiscardedError`, which E1 logs and
skips while the flag stands. `unmark_flagged` reverses the open edge whose loser is the image and whose source is one
of `cluster_review`, `agent_review`, `file_duplicates` (reason `unflag`); none, no-op. `duplicateOf` equal to the
image, or not a valid id, is a 422. `backend_api.md` documents the parameter, and `Frontend` `markImageIsFlagged`
takes an optional winner id: the `/duplicates` page passes the kept image for each loser (it already has it; its
existing flag/undo test is extended to assert the parameter), and `.claude/commands/review-duplicates.md` passes
`duplicateOf` and `reason=agent_review`. Android and other clients are unchanged (the parameter is optional).

**`batch/detect_file_duplicates.py`.** The oldest image of each group is already the keeper: after
`set_flagged(dup, True)` it calls `absorb(dup, keeper, "file_duplicates")` through the wrapper. Its group-skip rule
("a group with a flagged member counts as reviewed") is unchanged.

**`batch/ingest_abort.py`.** Before the delete, for each open edge whose loser is an aborted image (pending or
rejected in the batch) and whose winner's root is not in the deleted set: `reverse(edge, "abort")` through the wrapper.
Tier edges carry the batch run id, which is how the builder finds them; the builder confirms that edges added by
`hash_*` sources are untouched.

## Changes to P1

- `PopularityRepository.absorb` raises a new `WinnerGoneError` when the locked root of the named winner has no
  `images` row, instead of silently dropping the credit. Callers hit it only after a race with a delete; E1 skips it.
- The advisory lock on deleted roots (a P1 deferral) is dropped: none of the wired paths can reverse or write into a
  tree whose root is gone while another writer is active (abort keeps its surviving winners, undo and unflag act on
  images that exist). The P1 spec's wording is amended to say so.
- Optional and the builder's: a multi-row insert in `record_dropped_copies`, and extracting the repeated lock/retry
  loop. Not required.

## Tests

Integration tests on `ocrdb_test`, plus the existing suites run whole per `CLAUDE.md`:
- hash dedup: three identical files in one drop leave the keeper at 3; a file identical to an active image raises that
  image by 1; two actives with one hash pick the higher popularity; a same-named file re-dropped in a later
  invocation counts again; a second recording with the same invocation id adds nothing;
- ingestion: reject then `undo_reject` returns every popularity to its start; no eligible winner skips without error;
  the winner is the active member when there is one;
- flagging: with `duplicateOf` the winner rises and the loser reads 0; without it nothing changes; unflag restores;
  unknown reason with `duplicateOf` is 422; re-flag with another winner leaves the flag set and the first edge intact;
- `detect_file_duplicates`: keeper rises by the number flagged;
- `ingest_abort`: edges into surviving images are reversed and their popularity returns; orphaned trees are ignored;
- failure policy: a forced error inside the savepoint (raising repository, lock timeout) leaves the dedup action done,
  the outer transaction usable, a log line and no popularity change; `check_image_popularity` reports no drift after
  each scenario;
- frontend `MemesDuplicatesList` test asserts `duplicateOf` is sent per loser.

## Goals

- Every confirming path records the winner it already knows, and every undo reverses it.
- No dedup step ever fails or changes behaviour because of popularity bookkeeping.
- Counts are idempotent across retries and re-runs.

## Non-goals

- No API response, schema or page change for reading popularity (P3); no correction or attribution UI (P4).
- No change to what is rejected, flagged, moved or deleted, and no backfill.
- No automatic recording for manual flags (E3).

## Anti-goals

- A dedup action that fails or is blocked because popularity could not be written.
- Counting a flag as a duplicate when the caller did not say it was one.
- Counting the same copy twice, or letting a reversed decision keep its count.

## Constraints

- Needs the P1 migration (`a4f7c1e92b68`) on every environment first. P2 itself adds no migration.
- Changes `backend_api.md` (`mark_flagged`) and the frontend: run `tsc -b`, `eslint src/`, `vitest run` there.
- Repositories do not commit; the savepoint wrapper must not either.
- Test database only (`ocrdb_test`); the three environments' ports are occupied.

## Left to the builder

- Where the wrapper lives and how skips are counted in paths without metrics.
- How a cluster is looked up from a reviewed image in each tier (use what the review UI already uses).
- The per-invocation id's form in hash dedup.
- Whether the abort reversal selects by batch run id or by loser set.
