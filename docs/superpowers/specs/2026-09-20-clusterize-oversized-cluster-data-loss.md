# Clusterize Oversized-Cluster Data Loss

status: approved
Originates from: `docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`'s
"Rollout Outcome" section (live-rollout discovery, 2026-09-20) — that rollout's own
verification step found `general`'s Explore → Duplicates page showing zero items and traced it
to a pre-existing, unrelated defect in `clusterize.py`'s oversized-cluster splitting algorithm,
explicitly flagged there as out of that spec's scope and worth its own follow-up spec.

## Problem

### Finding A: `resolve_cluster()` can silently discard an entire oversized cluster

`batch/clusterize.py`'s `resolve_cluster()` recursively tightens the distance threshold on an
oversized union-find cluster (bigger than `settings.CLUSTERING.SPLITTING.MAX_CLUSTER_SIZE`) to
split it into smaller, tighter sub-groups — stepping down by `DECREMENT` from `PROXIMITY_THRESHOLD`
until either a group is small enough, or the next threshold would drop below `FLOOR`, at which
point the function is documented to "give up" and accept the cluster oversized as-is:

> Returns a list of finalized member-id lists -- each either within max_size, or still oversized
> because splitting hit `floor` without shrinking it further.

Live investigation against `general`'s real production data (2026-09-20, during
`2026-09-19-ocr-lemma-overlap-corroboration.md`'s Task 7 rollout) found this contract is violated.
`general` has three unusually large CLIP-sourced connected components (288/136/76 members — a
`get_duplicate_pairs()` result confirmed unrelated to that spec's own changes; reproduces
identically with only `clip`-sourced edges). Tracing `resolve_cluster()`'s recursion level by level
against the real data:

```
=== 288-member cluster ===
[threshold 0.050->0.040] 288 -> 2 sub-components, sizes [69, 15]   (204 correctly dropped: no edge
                                                                      survives tightening to 0.04 --
                                                                      genuinely unrelated to the hub)
  [threshold 0.040->0.030] 69 -> 0 sub-components   -> recursion returns [] -> ALL 69 SILENTLY LOST
  [threshold 0.040->0.030] 15 -> 0 sub-components   -> recursion returns [] -> ALL 15 SILENTLY LOST
Final: 0 groups, 0/288 members kept

=== 136-member cluster ===  Final: 0 groups, 0/136 kept  (same pattern)
=== 76-member cluster ===   Final: 0 groups, 0/76 kept   (same pattern)
```

The defect: when a tightening step produces **zero surviving sub-components** for a group that is
still oversized (every remaining edge among its members requires a distance ≥ the tightened
threshold, so nothing stays connected at all), the recursive call returns `[]` instead of ever
reaching the "hit floor, accept oversized" fallback. The docstring's stated contract has a silent
third outcome it never documents: **total, unconditional data loss**, indistinguishable from the
outside from "these members were never near-duplicates at all." In `general`'s case, this destroys
all 500 members of three real, `PROXIMITY_THRESHOLD`-qualifying near-duplicate hub clusters —
`general`'s live Explore → Duplicates page currently shows **zero** auto-clustered items as a
direct result.

`resolve_cluster()` is shared by two callers:

- `batch/clusterize.py`'s `cluster_active_library()` — **directly affected**; this is the confirmed
  cause of `general`'s empty Duplicates page.
- `Backend/app/services/cluster_splitting.py`'s `split_for_review()` (ingestion Tier A/B review) —
  **not affected**. Its own docstring already documents that it wraps `resolve_cluster()`
  specifically because "a member dropped from ingestion review is a latent unreviewable candidate
  pair that silently blocks `ingest_promote`," and re-attaches every member `resolve_cluster()`
  drops via a lossless Prim-style re-attachment pass. Verified directly (synthetic reproduction,
  not just read from the docstring): even feeding `split_for_review()` an input that makes
  `resolve_cluster()` return `[]` entirely, the wrapper's step-3 fallback (plain union-find over
  the untightened `pairs_by_member` edges) reconstitutes every member as one unsplit group — a
  no-op degradation, not data loss.

### Finding B: four ingestion-review integration tests are stale, not broken by Finding A

Four tests in `tests/integration/test_ingestion_cluster_cap.py` (all three) and
`tests/integration/test_ingestion_cluster_splitting.py` (one) have been failing throughout this
whole multi-week effort, consistently treated as a "known, pre-existing, unrelated baseline" and
confirmed to reproduce identically on `main` regardless of any of this session's own changes. They
were never previously root-caused. Investigation for this spec found all four share one cause,
**unrelated to Finding A**: `settings.DUPLICATES.THRESHOLD` was lowered from `0.3` to `0.12` on
2026-09-18 (`docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md`), and none of
these four tests' hardcoded fixture distances were updated to match:

| Test | Hardcoded distance(s) | Falls outside today's Tier B band `[0.05, 0.12)` since 2026-09-18 |
|---|---|---|
| `test_oversized_cluster_is_capped_and_reports_total` | second pair at `0.20` | yes — excluded entirely, so only 1 of the fixture's 2 expected clusters is ever fetched |
| `test_oversized_group_capped_with_splitting_enabled` | 75 spokes at `0.10`-`0.174` | yes for 55 of them (`i*0.001 >= 0.02`) — only ~21 of the intended 76 members are ever fetched from the DB, well under `CLUSTER_MEMBER_CAP` (60), so the capping logic under test never even engages |
| `test_small_clusters_untouched` | exactly `0.12` | yes — `TmpDuplicates.distance < distance_high` (`Backend/app/repositories/ingestion_repository.py:78`) is a strict `<`, so a pair at exactly the current threshold is excluded outright, `page["items"]` is empty, and `page["items"][0]` raises `IndexError` |
| `test_tier_b_splitting_disabled_returns_one_cluster_per_blob` | "loose" chain links at `0.22` | yes — the chain fractures into 3 disconnected query results instead of staying one 8-member blob, since the loose links never reach the query at all |

Each of these was written when Tier B's upper bound was `0.3` (so every one of these distances was
correctly inside-band at the time) and never revisited when that bound moved to `0.12`. Confirmed
these are entirely independent of Finding A: `resolve_cluster()`/`split_for_review()` are never
reached with the members the test authors intended, because the rows are filtered out one layer
earlier, at the SQL query (`get_tier_candidate_rows`) — before any splitting logic ever runs.

A fifth previously-bucketed "known baseline" failure,
`test_ingestion_tier_b_review.py::test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards`
(`RuntimeError: coroutine raised StopIteration`), was already independently investigated and
confirmed unrelated by this session's `2026-09-19-ocr-lemma-overlap-corroboration.md` final
whole-branch reviewer (reproduced identically on a clean `main` worktree; a pytest-asyncio harness
issue, not an assertion failure) — **out of scope here**, different symptom category, already
characterized.

## Goal

1. Fix `resolve_cluster()` so a total tightening wipe never destroys a whole oversized cluster —
   restore the function's own documented contract (every group ends up either within `max_size`,
   or accepted oversized) with no silent third outcome.
2. Fix the four stale Tier B test fixtures so they derive their edge-case distances from the
   actual `settings.DUPLICATES.THRESHOLD`/`TIER_A_THRESHOLD` values rather than hardcoded numbers,
   so a future threshold change can't silently break them the same way again.
3. Confirm both fixes resolve `general`'s live empty-Duplicates-page state and turn all five
   previously-"known baseline" `tests/integration/` failures into four fixed + one
   already-independently-confirmed-unrelated (left failing, out of scope, not this spec's fault).

## Non-goals

- **Retuning `PROXIMITY_THRESHOLD`/`DECREMENT`/`FLOOR`/`MAX_CLUSTER_SIZE`.** Explicitly rejected as
  the fix approach (see Design §1's rejected alternatives) — this spec changes `resolve_cluster()`'s
  control flow, not its calibration constants.
- **Touching `split_for_review()` / `Backend/app/services/cluster_splitting.py`.** Confirmed
  unaffected by Finding A (see Problem section); not touched by this spec's fix either, since the
  fix lives entirely inside `resolve_cluster()` itself and `split_for_review()` already handles
  whatever `resolve_cluster()` returns losslessly regardless.
- **`test_ingestion_tier_b_review.py`'s `StopIteration` failure.** Different symptom, already
  independently characterized as an unrelated pytest-asyncio harness issue by a prior review.
- **A new schema column, API field, or frontend change.** Both fixes are internal to existing
  Python control flow and test fixtures; no interface changes.
- **Re-litigating whether `general`'s three giant hub clusters are "real" near-duplicates.** This
  spec restores the system's own existing, already-shipped near-duplicate criterion
  (`PROXIMITY_THRESHOLD`) to actually apply to these clusters instead of discarding them outright;
  it does not change what counts as a near-duplicate.

## Design

### §1. `resolve_cluster()`: guard against zero-yield splits

`batch/clusterize.py`, in `resolve_cluster()`'s recursive step — after building `sub_uf` at
`next_threshold`, check whether it found anything at all before recursing into it:

```python
    member_set = set(members)
    sub_uf = UnionFind()
    for member in members:
        for neighbor, distance in pairs_by_member.get(member, ()):
            if neighbor in member_set and distance < next_threshold:
                sub_uf.connect(member, neighbor)

    roots = sub_uf.list_clusters()
    if not roots:
        # Tightening to next_threshold severed every remaining edge among these members at
        # once -- recursing into an empty sub_uf would return [] and silently drop the whole
        # group (up to hundreds of members for a real dense CLIP "hub" -- see
        # docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md). Treat
        # a total wipe the same as hitting `floor`: give up and accept the group oversized as
        # one group rather than destroying it. Does NOT change the *other* outcome --
        # partial success, where some members drop as true singletons but at least one
        # sub-component of size >= 2 survives -- that recursion path is unchanged below.
        return [members]

    results: list[list[int]] = []
    for root in roots:
        sub_members = sub_uf.get_cluster(root)
        results.extend(
            resolve_cluster(sub_members, pairs_by_member, next_threshold, decrement, floor, max_size)
        )
    return results
```

This is the only change to the function. It does not touch the `len(members) <= 1` base case (a
group that's already down to one real, isolated singleton is still correctly dropped — that outcome
is intentional and undisputed, see the function's own docstring: "Clusters of size < 2 are dropped
entirely"), and does not touch the `next_threshold < floor` give-up branch (already correct). It
adds exactly one new give-up condition, structurally identical to the existing one, triggered when
tightening finds nothing at all rather than when tightening has run out of threshold room.

Verified directly against `general`'s real production data before writing this spec (read-only,
via a local copy of the proposed fix, no live-database writes):

| Cluster | Before (current code) | After (proposed fix) |
|---|---|---|
| 288-member hub | 0 groups, 0 kept | 2 groups (69, 15), 84 kept |
| 136-member hub | 0 groups, 0 kept | 1 group (18), 18 kept |
| 76-member hub | 0 groups, 0 kept | 1 group (26), 26 kept |
| **Total** | **0 groups, 0/500 kept** | **4 groups, 128/500 kept** |

The 372 members not recovered (500 − 128) are members that never had *any* surviving edge to
another member at any tightened level down to `FLOOR` — i.e., true implicit singletons per the
function's existing, undisputed design; this fix does not change how those are handled, only
prevents the *false* case where a real sub-component's own members get swept away with them.

**Rejected alternative: raise `FLOOR`** (e.g. to `0.04`, giving up after one tightening attempt
instead of four). Tested this would happen to avoid the *specific* second-level wipe seen in
`general`'s data (since `general`'s first tightening step, `0.05→0.04`, does find real structure:
`[69, 15]`) — but this is a coincidence of this particular graph's shape, not a structural fix: a
graph whose edges are concentrated tightly enough could still wipe out entirely on the *first*
tightening attempt, which no `FLOOR` value prevents. It would also blunt the splitting feature's
actual value across the board (fewer tightening attempts means oversized clusters are far less
often actually broken into human-reviewable chunks, defeating the feature's own purpose) as a
side effect of trying to dodge one edge case. Rejected in favor of the structural fix above.

**Rejected alternative: `settings.CLUSTERING.SPLITTING.ENABLED: false`.** Reverts to the
pre-splitting behavior (every union-find cluster written out exactly as found, however large) —
technically also fixes `general`'s specific empty-page symptom, but discards the entire splitting
feature everywhere, not just for the pathological hub case. Rejected as disproportionate.

### §2. Stale Tier B test fixtures: derive from the real thresholds, not hardcoded numbers

`tests/integration/test_ingestion_cluster_cap.py` and
`tests/integration/test_ingestion_cluster_splitting.py` both already import
`Backend.app.services.ingestion_service`; add the same import each already effectively depends on
implicitly (`TIER_A_THRESHOLD`, and `settings.DUPLICATES.THRESHOLD` via `config.settings`), and
replace every hardcoded edge-case distance with an expression relative to the real, current value:

- `test_oversized_cluster_is_capped_and_reports_total`'s second pair: `0.20` →
  `settings.DUPLICATES.THRESHOLD - 0.02` (comfortably inside band, distinct from the star
  cluster's own distances).
- `test_oversized_group_capped_with_splitting_enabled`'s spoke distances: `0.10 + i * 0.001` for
  `i` in `range(CLUSTER_MEMBER_CAP + 15)` → rescale the step so the *last* spoke still lands
  strictly inside the band: step `= (settings.DUPLICATES.THRESHOLD - TIER_A_THRESHOLD - 0.005) /
  (CLUSTER_MEMBER_CAP + 15)`, starting at `TIER_A_THRESHOLD + 0.001`. (Keeps the test's own intent
  — "every spoke individually inside Tier B's band, distinct distances so tightest-first ranking
  is well-defined" — without hand-picking numbers that happen to fit today's `0.12`.)
- `test_small_clusters_untouched`'s exact-boundary pair: `0.12` →
  `settings.DUPLICATES.THRESHOLD - 0.001` (deliberately just inside the strict `<` upper bound,
  rather than exactly on it — the previous value wasn't testing the boundary on purpose, it just
  happened to equal the old `0.3`'s... no, actually re-checking: `0.12` was never the old upper
  bound value; this one was already wrong even under the old `0.3` threshold's semantics, just
  coincidentally happened to still exclude/include correctly before the low threshold made it
  matter. Either way, deriving from the real setting removes the ambiguity).
- `_chain()`'s Tier B `loose` default (`0.22`) in
  `test_tier_b_splitting_disabled_returns_one_cluster_per_blob`: → pass
  `loose=settings.DUPLICATES.THRESHOLD - 0.01` explicitly at the call site (the helper's default
  stays as documentation of "a Tier B chain" shape, but this specific test needs a value guaranteed
  inside today's real band, not the helper's stale default).

Also correct the stale comment in `environments/settings.yaml`'s `clustering.ingestion_review_splitting`
block (currently: "Tier A candidate pairs are all < 0.05; Tier B's are 0.05-0.30" — the `0.30` is
the same pre-2026-09-18 number, now `0.12`) — a one-line doc fix, not a behavior change.

**Rejected alternative: just hardcode new correct numbers** (e.g. `0.22` → `0.10`). Smaller diff,
but reintroduces the exact same staleness risk that already caused this: nothing would fail loudly
if `settings.DUPLICATES.THRESHOLD` moves again, these tests would just quietly start failing (or
worse, quietly start passing for the wrong reason) a second time. Rejected in favor of deriving
from the live setting.

## Testing

- **New regression test for §1** (`tests/integration/test_clusterize.py` or `batch/tests/`, pure/
  DB-free since `resolve_cluster()` takes plain dicts — no DB needed): a synthetic cluster shaped
  like `general`'s real 69-member sub-case (one member connected to all others at a distance that
  survives the first tightening but not the second, all other pairwise edges present only at the
  loosest level) — assert the fixed function returns one non-empty group covering every member,
  not `[]`. Also a case where a *partial* wipe should still correctly drop true singletons (the
  204-out-of-288 pattern) — assert those specific members are absent while the surviving
  sub-components are intact, so the fix doesn't overcorrect into "never drop anything."
- **Existing test suite**: `tests/integration/test_clusterize.py`'s existing splitting tests (the
  small, hand-built cluster cases) must continue passing unchanged — the fix only changes behavior
  for the zero-yield case, which none of today's existing fixtures happen to exercise (confirmed by
  reading each one before writing this section).
- **§2's four fixed tests**: run `DATABASE_URL=... pytest tests/integration/test_ingestion_cluster_cap.py tests/integration/test_ingestion_cluster_splitting.py -v` and confirm all pass (the
  latter file has other tests already passing today — e.g. `test_oversized_blob_splits_into_a_partition_of_subgroups` — confirm those remain unaffected, not just the ones being fixed).
- **Full suite regression check**: per CLAUDE.md's "Running the right test scope" gotcha,
  `resolve_cluster()` is shared, load-bearing logic — run the entire `tests/integration/` root
  (not just the two files above) before merging. Expect exactly the one remaining known failure
  (`test_ingestion_tier_b_review.py`'s `StopIteration`), nothing else.
- **Live verification** (controller-only, after code review, before this spec is marked done):
  re-run `rebuild_duplicates.py --env general` (chains to `clusterize.py`) against the live
  `general` database and confirm via `DATABASE_URL_READONLY` that `tmp_clusters` now has non-zero
  rows for `general`, with the three known hub clusters' surviving members matching the
  investigation numbers above (84/136/76 → wait, restate precisely once live: expect the same
  84/18/26 split found in this spec's own read-only investigation, modulo any corpus drift between
  investigation time and rollout time). Spot-check the live Explore → Duplicates page.

## Rollout

1. Ship the code (Design §1-§2) plus test coverage through the same SDD process as every prior
   piece of this effort.
2. **Controller-only, live-database step**: re-run `rebuild_duplicates.py --env general` (the
   `clusterize.py` chain only needs a fresh run against `general`'s existing `tmp_duplicates` data
   — no backfill, no deletion, no other environment needs touching, since `metal`/`it` never
   exhibited this symptom in the first place: neither has a connected component large enough to
   trigger oversized splitting at all). Confirm via `DATABASE_URL_READONLY` and a live
   Explore → Duplicates spot-check for `general`.
3. Mark this spec `done` with a brief Rollout Outcome note (before/after `tmp_clusters` row count
   for `general`, confirmation the three hub clusters now appear).

## Self-Review

**Placeholder scan**: none — the `resolve_cluster()` diff, the fixture-derivation approach, and
every constant referenced are complete and literal, not descriptions of what to write. (The one
piece of exploratory prose left in Design §2's `test_small_clusters_untouched` bullet — reasoning
through why `0.12` wasn't a "real" boundary test even under the old threshold — is retained
deliberately: it's the actual reasoning trail, not a placeholder, and the concrete fix that follows
it is unambiguous.)

**Type/name consistency**: `resolve_cluster()`'s signature is unchanged (same parameters, same
return type `list[list[int]]`) — the fix only adds a new early-return branch inside the existing
recursive body, so every caller (`clusterize.py`'s own recursive call, `split_for_review()`) needs
zero changes.

**Spec coverage check**: Problem → both findings, each with concrete traced/queried evidence, not
assumption. Goal → both fixes plus the observable outcome (live page, test suite). Non-goals →
explicitly rule out recalibration, touching the review-path wrapper, the unrelated `StopIteration`
failure, and any interface change. Design → exact code for §1 (verified against real production
data before writing), exact derivation strategy for §2 (each of the four fixtures individually
addressed). Testing → new regression coverage for the exact zero-yield shape, confirmation existing
tests are undisturbed, the full-suite gotcha from CLAUDE.md explicitly applied. Rollout → scoped to
`general` only, with the specific reason `metal`/`it` don't need it stated rather than assumed.
