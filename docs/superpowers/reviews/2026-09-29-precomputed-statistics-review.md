# Review: Precomputed Statistics Snapshot

Spec: `docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md`
Plan: `docs/superpowers/plans/2026-09-29-precomputed-statistics.md`
Range reviewed: `c12a311..c6d3dd4` (branch `worktree-precomputed-statistics`)

## Process

Executed subagent-driven: one implementer per task (8 tasks), each followed by a task review
(spec compliance + code quality). All eight task reviews came back approved with no Critical or
Important findings. A final whole-branch review (most capable model) then returned **With fixes**
(0 Critical, 2 Important, 5 Minor); one fix wave addressed it, and one scoped re-review confirmed
every finding addressed with no new breakage. Per CLAUDE.md, that is the single review iteration.

Verification (run independently by the controller, not only by the implementers):

| Root | Baseline | After Task 8 | After fix wave (controller-verified on final head) |
|---|---|---|---|
| `Backend/tests` | 330 | 336 | 338 |
| `batch/tests` | 157 | 168 | 168 |
| `tests/integration` (whole root) | 365 | 368 | 368 |

The frontend gate on the final head is also clean (`tsc -b`, `eslint src/ --max-warnings 0`,
`vitest run`: 26 files, 241 tests). The corrected plan smoke script (real app, `ocrdb_test`, no port bound) was also run by the
controller after the fix wave: health ok, identical `computed_at` on two consecutive statistics
calls (second served from the stored snapshot), `/api/images?limit=1` returned 200.

## Requirements coverage

Every spec section is implemented: storage (`statistics_snapshots` model, migration `b7e2c4a91d30`
on the single head `29a039fa4457`, upserting repository), compute (shared
`refresh_corpus_snapshot`, `batch/build_statistics.py`), all three triggers (hourly scheduler entry,
best-effort post-batch hook in `batch/run_wrapper.py`, admin-triggerable registry entry), read path
with fallback, contract (schema, TS/Kotlin/Python generated types, the router's hand-written model,
`backend_api.md`), frontend freshness line, error handling and tests.

## What was fixed

From the final whole-branch review:

- **Important 1 — rollout order.** The spec's Rollout section had the migration after the merge, but
  the always-on backends run `uvicorn --reload`, so merged code goes live immediately and the
  statistics endpoint would return 500 until the table existed. Rollout now applies the additive
  migration first.
- **Important 2 — frozen snapshot where no scheduler runs.** The Docker image loads only the base
  `settings.yaml` (`scheduler.enabled: false`) and ships neither `run_wrapper.py` nor
  `build_statistics.py`, so the first request's snapshot would be served forever. The endpoint now
  treats a snapshot older than `SNAPSHOT_MAX_AGE` (2 hours, twice the hourly interval) like a
  missing one and recomputes and stores it. Documented in spec §4, `backend_api.md` and the
  `CLAUDE.md` entry.
- **Minor 1** — spec named the repository methods `upsert_snapshot`/`get_snapshot`; reconciled to the
  real `upsert(name, payload, computed_at, duration_ms)` / `get(name)`.
- **Minor 2** — the unusable-payload fallthrough is now logged (`logger.warning`, no payload
  contents) instead of silent.
- **Plan defect** — Task 8 Step 3's smoke script used `TestClient` without a `with` block, which
  fails on the second request ("Event loop is closed": a fresh event loop per request kills the
  pooled asyncpg connection). Replaced with an `httpx.ASGITransport` variant in one event loop.

Carried from earlier per-task reviews and folded into the same edits: snapshot-hit test now pins the
UTC `Z` designator with a fresh timestamp built at test time; fallback test asserts the upsert
payload; `caplog` assertion on the unusable-payload warning; two new age-bound tests (180 min
recomputed, 119 min served); missing second blank line before `class StatisticsSnapshot`.

Earlier in the run (before the final review) the controller also corrected, by ruling: the TypeScript
generator must run from `Frontend/` (the plan ran it from the repo root, which would write outside
the repo); `test_swallows_refresh_failures` gained a `caplog` assertion so it cannot pass vacuously;
duplicated session-mock setup in `test_build_statistics.py` was factored into one helper.

## Not fixed, but explained

- **Concurrent writers are last-writer-wins by commit order, not data age** (Minor 3). `computed_at`
  is stamped after the query, so a slower concurrent compute that started earlier can overwrite a
  fresher row. Staleness is bounded to seconds and both writers store equally valid data.
- **The post-batch hook has no timeout** (Minor 4). The statistics query is plain `SELECT`s, which
  block only behind an ACCESS EXCLUSIVE lock (DDL/TRUNCATE). If that happened the wrapper process would
  hang after the parent batch had already completed. Low likelihood; revisit if seen.
- **Hourly `statistics` runs add ~24 rows/day/environment to the `/admin/batches` history** (Minor 5).
  Same pattern as `trends_batch`; accepted.

## Intentionally ignored, and why

Deferred Minor findings from per-task reviews that the final review triaged as not worth changing:
`__main__` logger has no handler in the wrapper subprocess (the warning still reaches the job log via
Python's last-resort handler, unformatted); repeated four-patch `with` blocks in `test_run_wrapper.py`
(matches the file's existing style); no `CancelledError` test (correctly not swallowed by
`except Exception`); `test_scheduled_hourly_with_a_registered_script` does not assert the script
exists in the registry (a sibling test already does); the "just now" threshold is not pinned at its
60-second edge; schema file gained a trailing newline; `--env` only applied under `__main__` (same as
sibling batch scripts); `diagnostics.py` lacks a trailing newline (pre-existing); repository tests are
happy-path only (two-method repository).

## Follow-ups outside this change

- After merge: set the spec status to `done`.
- Apply the migration to metal, general and IT (see the spec's Rollout section) before or together
  with the merge. This was intentionally not done by any agent; it touches the live databases.
