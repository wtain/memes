# Clusterize Oversized-Cluster Data Loss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `batch/clusterize.py`'s `resolve_cluster()` so it never silently discards an entire
oversized cluster, and fix four stale, unrelated ingestion-review test fixtures that have been
failing since a 2026-09-18 threshold change nobody updated them for.

**Architecture:** Two independent, unrelated fixes bundled in one plan because they were discovered
together and share a spec. Task 1 changes `resolve_cluster()`'s control flow (one new early-return
branch) plus regression tests. Task 2 changes four hardcoded test-fixture distances to derive from
the real, live threshold settings instead. Task 3 is a controller-only live-database verification
step against the `general` environment (the only one affected).

**Tech Stack:** Python 3.11, pytest (`batch/tests/` — pure unit tests, DB-free; `tests/integration/`
— real PostgreSQL via `ocrdb_test`), SQLAlchemy async ORM, Dynaconf (`config.settings`).

**Spec:** `docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md`

## Global Constraints

- No schema changes, no Backend API changes, no frontend changes anywhere in this plan (spec
  Non-goals).
- Task 1 does not touch `Backend/app/services/cluster_splitting.py` / `split_for_review()` — that
  path is confirmed unaffected (spec Problem section) and stays untouched.
- Task 1 does not touch `PROXIMITY_THRESHOLD`, `DECREMENT`, `FLOOR`, or `MAX_CLUSTER_SIZE` — the
  fix is a control-flow change inside `resolve_cluster()`, not a calibration change (spec's
  rejected alternatives).
- `resolve_cluster()`'s signature and return type (`list[list[int]]`) do not change. Every caller
  (`clusterize.py`'s own recursive call, `split_for_review()`) needs zero changes.
- Task 2 derives fixture distances from `settings.DUPLICATES.THRESHOLD` /
  `batch.clusterize.PROXIMITY_THRESHOLD` at test-run time, not new hardcoded numbers — the whole
  point is these fixtures must not go stale the same way again.
- `test_ingestion_tier_b_review.py::test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards`
  (the `StopIteration` failure) is explicitly out of scope — already independently confirmed
  unrelated (pytest-asyncio harness issue) by a prior whole-branch review. Do not touch it, do not
  expect it to start passing.
- Task 1 and Task 2 touch completely disjoint files and have no interface dependency on each
  other — safe to dispatch in either order, but never in parallel (standard SDD rule: never
  dispatch multiple implementation subagents at once).
- Task 3 is CONTROLLER-ONLY: every step in it is a live-database write or a live-database read
  against the `general` environment. No subagent ever runs Task 3. Per CLAUDE.md's live-database
  rule, if a subagent needs read access for any reason it gets `DATABASE_URL_READONLY` only,
  explicitly labeled — but no task in this plan should need to hand that out at all.
- Task 3 only touches `general` — `metal`/`it` were confirmed in the spec's own investigation to
  have no connected component large enough to trigger oversized splitting at all, so they have
  nothing to re-run.

---

### Task 1: Fix `resolve_cluster()`'s zero-yield split defect

**Files:**
- Modify: `batch/clusterize.py:25-64` (the `resolve_cluster()` function)
- Modify: `batch/tests/test_clusterize.py` (add to the existing `TestResolveCluster` class)

**Interfaces:**
- Consumes: nothing new — `resolve_cluster(members, pairs_by_member, threshold, decrement, floor, max_size) -> list[list[int]]`, unchanged signature.
- Produces: the same function, same signature, same return type. `cluster_active_library()`
  (same file) and `Backend/app/services/cluster_splitting.py`'s `split_for_review()` both call it
  unmodified — this task does not touch either caller.

- [ ] **Step 1: Write the failing test**

Add this test method to the existing `TestResolveCluster` class in `batch/tests/test_clusterize.py`
(the class already exists with 5 tests — `test_under_limit_passes_through_unchanged`,
`test_singleton_input_is_dropped`, `test_splits_cleanly_after_one_decrement`,
`test_multi_level_split_drops_implicit_singletons`,
`test_gives_up_oversized_once_next_threshold_hits_floor` — add this as a 6th):

```python
    def test_zero_yield_split_is_accepted_oversized_not_dropped(self):
        # Chain 1-2-3-4-5, all four edges at distance 0.035. At the first tightening
        # (0.05 -> 0.04) every edge still qualifies (0.035 < 0.04), so the whole 5-member
        # chain survives as ONE still-oversized sub-component -- no fragmentation yet,
        # matching the real shape seen in general's 288-member hub cluster's first split
        # (see docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md).
        # Recursing into it retries at 0.04 -> 0.03: now 0.035 >= 0.03, so EVERY edge is
        # severed at once -- sub_uf finds zero sub-components. Before the fix, this
        # returned [] and silently dropped all 5 members, even though every edge was well
        # inside the original PROXIMITY_THRESHOLD (0.05). The fix must return the whole
        # group as one accepted-oversized group instead.
        pairs_by_member = _symmetric_pairs([
            (1, 2, 0.035),
            (2, 3, 0.035),
            (3, 4, 0.035),
            (4, 5, 0.035),
        ])
        result = resolve_cluster(
            [1, 2, 3, 4, 5], pairs_by_member, threshold=0.05, decrement=0.01, floor=0.01, max_size=2,
        )
        assert result == [[1, 2, 3, 4, 5]]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest batch/tests/test_clusterize.py::TestResolveCluster::test_zero_yield_split_is_accepted_oversized_not_dropped -v`
Expected: FAIL with `assert [] == [[1, 2, 3, 4, 5]]` (the current buggy code returns `[]`).

- [ ] **Step 3: Implement the fix**

In `batch/clusterize.py`, replace `resolve_cluster()`'s body from the `member_set = set(members)`
line through the end of the function with:

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

Also update the function's docstring (the `Returns a list of finalized member-id lists...` line)
to reflect the now-accurate contract:

```python
    """Recursively split an oversized cluster by progressively tightening the distance
    threshold, dropping any member left with no surviving edge (an implicit singleton)
    along the way. Pure / DB-free so it's independently unit-testable.

    members: the int ids in this cluster.
    pairs_by_member: id -> list of (neighbor id, distance) below the *original*
        PROXIMITY_THRESHOLD, symmetric (each pair present from both endpoints).
    Returns a list of finalized member-id lists -- each either within max_size, or still
    oversized because splitting hit `floor` or a tightening step found no surviving
    sub-structure at all (a "total wipe" -- both are treated as "give up, accept as-is").
    Clusters of size < 2 are dropped entirely; a >= 2-member group is never dropped.
    """
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest batch/tests/test_clusterize.py::TestResolveCluster::test_zero_yield_split_is_accepted_oversized_not_dropped -v`
Expected: PASS

- [ ] **Step 5: Run the full existing `TestResolveCluster` suite to confirm no regression**

Run: `pytest batch/tests/test_clusterize.py -v`
Expected: all tests PASS, including the 5 pre-existing ones — in particular
`test_multi_level_split_drops_implicit_singletons` must still pass unchanged (it already
demonstrates the fix does not overcorrect: `roots` is non-empty at every level in that test's
fixture, so the new branch never triggers there, and members 3 and 6 are still correctly dropped
as true implicit singletons while `{1,2}` and `{4,5}` survive — confirm this by reading the test's
assertion after running, don't just trust the pass/fail count).

- [ ] **Step 6: Run the full `batch/tests/` suite**

Run: `pytest batch/tests/ -q`
Expected: all tests pass (155 before this task; +1 for the new test = 156).

- [ ] **Step 7: Run the full `tests/integration/` suite**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: same known baseline as before this task (the 4 tests Task 2 will fix, plus the 1
already-out-of-scope `StopIteration` failure) — this task must not change that count, since it
doesn't touch anything `tests/integration/` exercises differently. If the count differs from the
5-failure baseline in either direction, stop and investigate before committing — either a fixture
Task 2 hasn't run yet still failing as expected (fine, 5 total unchanged) or something unexpected
changed (not fine).

- [ ] **Step 8: Commit**

```bash
git add batch/clusterize.py batch/tests/test_clusterize.py
git commit -m "fix: prevent resolve_cluster() from silently dropping a fully-shattered oversized cluster

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Fix four stale Tier B ingestion-review test fixtures

**Files:**
- Modify: `tests/integration/test_ingestion_cluster_cap.py` (3 tests)
- Modify: `tests/integration/test_ingestion_cluster_splitting.py` (1 test)
- Modify: `environments/settings.yaml` (1 stale comment, no behavior change)

**Interfaces:**
- Consumes: `settings.DUPLICATES.THRESHOLD` (from `config.settings`, already `0.12` in the
  currently-active tracked config — nothing to change there) and `batch.clusterize.PROXIMITY_THRESHOLD`
  (`0.05`, Tier A's own band top, already imported into `Backend/app/services/ingestion_service.py`
  as `TIER_A_THRESHOLD`).
- Produces: nothing new for other tasks — this task only touches test fixtures and a comment.

- [ ] **Step 1: Add the settings import to both test files**

In `tests/integration/test_ingestion_cluster_cap.py`, add after the existing imports (after line 10,
`from Storage.models import Image, TmpDuplicates`):

```python
from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from config.settings import settings
```

In `tests/integration/test_ingestion_cluster_splitting.py`, add the same two lines after its
existing imports (after line 15, `from Storage.models import Image, TmpDuplicates`).

- [ ] **Step 2: Fix `test_oversized_cluster_is_capped_and_reports_total`**

In `tests/integration/test_ingestion_cluster_cap.py`, change line 44 from:

```python
    await _make_pair(db_session, x, y, 0.20)
```

to:

```python
    await _make_pair(db_session, x, y, settings.DUPLICATES.THRESHOLD - 0.03)
```

(Stays comfortably inside Tier B's `[TIER_A_THRESHOLD, settings.DUPLICATES.THRESHOLD)` band. Using
`- 0.03` rather than `- 0.02` is deliberate: the star cluster's own spoke distances start at
`0.10 + i * 0.0001` (untouched by this step, i.e. `0.10` upward) — `settings.DUPLICATES.THRESHOLD
- 0.02` would land on exactly `0.10`, coincidentally identical to the star's first spoke distance.
Not necessarily harmful (they're different edges between different images, and `min_distance` for
sort purposes is computed from each group's own full edge set before capping either way — see
`Backend/app/services/ingestion_service.py:230` — so ordering stays consistent either way), but
`-0.03` (`0.09`) sits cleanly below the star's whole spoke range, removing any need to reason about
a coincidental tie.)

- [ ] **Step 3: Run the test to verify it now passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_cap.py::test_oversized_cluster_is_capped_and_reports_total -v`
Expected: PASS. (If it still fails, re-check `settings.DUPLICATES.THRESHOLD`'s actual live value
first — `python -c "from config.settings import settings; print(settings.DUPLICATES.THRESHOLD)"`
— the fix assumes it's `0.12`; if a future change moved it again, adjust the offset in Step 2
accordingly rather than assuming this step's code is wrong.)

- [ ] **Step 4: Fix `test_oversized_group_capped_with_splitting_enabled`**

In the same file, change line 87 from:

```python
    for i, s in enumerate(spokes):
        await _make_pair(db_session, hub, s, 0.10 + i * 0.001)  # all in Tier B band, < 0.30
```

to:

```python
    band_width = settings.DUPLICATES.THRESHOLD - TIER_A_THRESHOLD
    step = (band_width - 0.005) / len(spokes)
    for i, s in enumerate(spokes):
        await _make_pair(db_session, hub, s, TIER_A_THRESHOLD + 0.001 + i * step)
```

(`len(spokes)` is `CLUSTER_MEMBER_CAP + 15`; the `- 0.005` headroom guarantees the last spoke's
distance stays strictly below `settings.DUPLICATES.THRESHOLD`, even with floating-point rounding.
Every spoke still gets a distinct distance, preserving the test's own intent — "every spoke
individually inside Tier B's band, distinct distances so tightest-first ranking is well-defined" —
without hardcoding numbers that only happen to fit today's `0.12`.)

- [ ] **Step 5: Run the test to verify it now passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_cap.py::test_oversized_group_capped_with_splitting_enabled -v`
Expected: PASS.

- [ ] **Step 6: Fix `test_small_clusters_untouched`**

In the same file, change line 103 from:

```python
    await _make_pair(db_session, a, b, 0.12)
```

to:

```python
    await _make_pair(db_session, a, b, settings.DUPLICATES.THRESHOLD - 0.001)
```

(Deliberately just inside the strict `<` upper bound the real query uses
(`Backend/app/repositories/ingestion_repository.py:78`), rather than exactly on it — the boundary
itself is excluded by design, this test is about "a normal small cluster inside the band," not a
boundary test.)

- [ ] **Step 7: Run the test to verify it now passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_cap.py::test_small_clusters_untouched -v`
Expected: PASS.

- [ ] **Step 8: Run the full file to confirm no regression among the 3**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_cap.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 9: Fix `test_tier_b_splitting_disabled_returns_one_cluster_per_blob`**

In `tests/integration/test_ingestion_cluster_splitting.py`, change line 141 from:

```python
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5}, tight=0.08, loose=0.22)
```

to:

```python
    ids = await _chain(
        db_session, batch_id, n=8, loose_at={2, 5},
        tight=0.08, loose=settings.DUPLICATES.THRESHOLD - 0.01,
    )
```

(The `_chain()` helper's own `tight=0.08, loose=0.22` defaults, documented in its docstring as "a
Tier B chain," stay as-is — they're only defaults for callers that don't pass their own values, and
`test_tier_b_blob_splits_with_the_tier_b_ladder` at line 118, which already passes today, calls
`_chain(..., tight=0.08, loose=0.22)` explicitly too but is NOT in the failing set, since its own
assertions (`>= 2` items, `<= 3` members each) happen to still hold even with the chain fractured
by the query filter — leave that call site untouched; only this one test's assertions
(`== 1` item, `== 8` members) require the chain to actually stay whole, which requires the `loose`
value to land inside today's real Tier B band.)

- [ ] **Step 10: Run the test to verify it now passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_splitting.py::test_tier_b_splitting_disabled_returns_one_cluster_per_blob -v`
Expected: PASS.

- [ ] **Step 11: Run the full file to confirm no regression among its other tests**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_splitting.py -v`
Expected: all tests in the file PASS, including
`test_oversized_blob_splits_into_a_partition_of_subgroups`,
`test_decision_on_a_split_member_still_settles_its_cross_subgroup_pair`, and
`test_tier_b_blob_splits_with_the_tier_b_ladder` (already passing before this task — confirm they
still are, this step's file-level run isn't only about the one test just fixed).

- [ ] **Step 12: Fix the stale comment in `environments/settings.yaml`**

Find the `clustering.ingestion_review_splitting` block's comment (currently, around the `tier_a`/
`tier_b` ladder lines):

```yaml
    # Tier A candidate pairs are all < 0.05; Tier B's are 0.05-0.30. Each tier's ladder
```

Change `0.05-0.30` to `0.05-0.12` — a pure comment fix (the actual ladder values below it,
`tier_a: { decrement: 0.01, floor: 0.01 }` / `tier_b: { decrement: 0.05, floor: 0.05 }`, are
unrelated numbers already correct and untouched by this step). Since this line references a live
Dynaconf setting's value in prose, and that value could move again, consider whether to soften it
to "Tier A candidate pairs are all < TIER_A_THRESHOLD (0.05); Tier B's are TIER_A_THRESHOLD to
settings.DUPLICATES.THRESHOLD (currently 0.12)" — use judgement on which reads more naturally in
context; either communicates the current true state accurately, which is the actual requirement
here.

- [ ] **Step 13: Run the full `tests/integration/` suite**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: exactly one known failure remains —
`test_ingestion_tier_b_review.py::test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards`
(`StopIteration`, explicitly out of scope). If Task 1 ran first, `349 passed / 5 failed` (baseline
including this task's 4) should now read `353 passed / 1 failed`. If Task 1 hasn't run yet, this
task's 4 fixes still apply independently — confirm the count drops by exactly 4 from whatever
Task 1 left it at.

- [ ] **Step 14: Run the full `Backend/tests/` and `batch/tests/` suites for completeness**

Run: `cd Backend && pytest -q` and `cd .. && pytest batch/tests/ -q` (per CLAUDE.md's gotcha, never
combine these with `tests/integration/` in one invocation). Expected: no change from Task 1's
counts — this task doesn't touch anything either root exercises.

- [ ] **Step 15: Commit**

```bash
git add tests/integration/test_ingestion_cluster_cap.py tests/integration/test_ingestion_cluster_splitting.py environments/settings.yaml
git commit -m "fix: derive stale Tier B test fixture distances from the live threshold setting

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Live verification — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** It writes to (and reads from) the live `general`
database the developer's own running backend depends on continuously — the same category of
action as the two prior specs' own Task 7/rollout steps in this session. The controller runs every
step itself, never a subagent.

**Before Step 1 (the first live-environment write), stop and get the user's explicit go-ahead.**
Name what this does (re-running `rebuild_duplicates.py --env general`, which chains into
`clusterize.py`, against the live `general` database — no backup needed since this is a read-then-
recompute of an already-migration-managed table, not a destructive delete, but say so explicitly
and offer one anyway if the user prefers) before proceeding.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md` (status +
  a "Rollout Outcome" section).

- [ ] **Step 1: Get the go-ahead, then re-run `clusterize.py` against `general`**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.general" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.rebuild_duplicates --env general
```

This chains into `clusterize.py` automatically (default `--chain`), so a fresh `resolve_cluster()`
run happens against `general`'s existing `tmp_duplicates` data — no new candidate discovery is
strictly required for this task's own purpose (the fix doesn't depend on new pairs existing), but
`rebuild_duplicates.py` is the documented, already-tested entry point that chains correctly, so use
it rather than invoking `clusterize.main()` directly. Confirm backend health
(`http://127.0.0.1:8082/api/diagnostics/health`) afterward.

- [ ] **Step 2: Verify via `DATABASE_URL_READONLY` (read-only)**

```sql
SELECT count(DISTINCT cluster_id) AS clusters, count(*) AS rows FROM tmp_clusters;
```

Expect non-zero (0 before this task, per the spec's Problem section). Then confirm the three known
hub clusters specifically recovered:

```sql
SELECT cluster_id, count(*) AS members FROM tmp_clusters GROUP BY cluster_id ORDER BY members DESC LIMIT 10;
```

Expect group sizes in the neighborhood of the spec's own investigation numbers (84-member split as
2 groups of 69/15, an 18-member group, a 26-member group) — exact counts may drift slightly from
corpus changes between the spec's investigation and this rollout; the point is confirming
non-trivial groups exist where none did before, not matching the old numbers to the row.

- [ ] **Step 3: Spot-check the live Explore → Duplicates page for `general`**

Confirm real clusters now appear (via `/api/images/duplicates` or the frontend page) where the page
previously showed nothing.

- [ ] **Step 4: Mark this spec done**

`docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md`: `status: approved` →
`status: done`; add a `## Rollout Outcome` section with the before/after `tmp_clusters` counts from
Step 2 and the spot-check confirmation from Step 3.

```bash
git add docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md
git commit -m "docs: mark clusterize-oversized-cluster-data-loss spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** Design §1 (the `resolve_cluster()` fix) → Task 1, code verified against the
spec's own already-tested logic (the spec's investigation used this exact code shape before
writing the plan). Design §2 (all four stale fixtures + the settings.yaml comment) → Task 2, every
one of the four tests and the comment individually addressed with exact before/after code, not
described abstractly. Testing section → Task 1 Steps 5-7 and Task 2 Steps 3/5/7/8/10/11/13/14 cover
every test-suite requirement the spec named (new regression coverage, existing-test non-regression,
full `tests/integration/` root per CLAUDE.md's shared-code gotcha, `Backend/`/`batch/tests/`
completeness). Rollout section → Task 3 in full, explicitly CONTROLLER-ONLY with an explicit
go-ahead gate, matching this session's own established pattern for the two prior specs' live steps.

**Placeholder scan:** none — every code block in Task 1 and Task 2 is the literal, complete change,
not a description. Task 3's SQL/PowerShell commands are copy-pasteable, matching the pattern the
two prior specs' own Task 7 rollout steps used.

**Type/name consistency:** `resolve_cluster()`'s signature is unchanged across Task 1's before/
after code (confirmed by re-reading both blocks side by side). Task 2's new import lines
(`TIER_A_THRESHOLD`, `settings`) are used consistently by name in every step that references them
(Steps 2, 4, 6, 9) — no step introduces a differently-named variable for the same value.

**Cross-task interface check:** Task 1 and Task 2 touch disjoint files with no shared interface —
confirmed by listing every file each task modifies and finding zero overlap. Task 3 depends on
neither Task 1 nor Task 2's code changes for its own step 1 (the live re-run only needs Task 1's
fix, since Task 2's fixtures are test-only and never touch the live `general` database) — but
should still run last, after both are merged, so the fix being verified live is the actual reviewed
and tested code, not a mid-flight version.

**A note on task ordering:** Task 1 must land before Task 3 (Task 3 verifies Task 1's fix against
live data). Task 2 has no live-database component and could in principle run before, after, or
interleaved with Task 1 — but SDD's own "never dispatch multiple implementation subagents in
parallel" rule already forces strictly sequential dispatch regardless, so this is stated for
completeness, not because it changes execution order.
