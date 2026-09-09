# Ingestion Review Cluster Splitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split oversized `IngestionService.list_clusters` review cards into tight subgroups on the fly, reusing `clusterize.resolve_cluster`, without changing decision semantics or the data model.

**Architecture:** A new pure module `Backend/app/services/cluster_splitting.py` wraps `resolve_cluster` (which drops implicit singletons) into a **lossless partitioner** `split_for_review` — its output groups are an exact partition of the input. `list_clusters` builds a `pairs_by_member` map alongside its existing union-find, then runs each oversized blob through `split_for_review` before assembling the response; everything downstream (OCR attach, sort, cursor, pagination) is unchanged. A `clustering.ingestion_review_splitting` config block (tier-aware ladders + `enabled` flag) drives it.

**Tech Stack:** Python 3.11, FastAPI service layer, SQLAlchemy async (integration tests), Dynaconf config, pytest; one Vitest case.

**Spec:** `docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md`

## Global Constraints

- Splitting is **presentational**: no change to `tmp_duplicates`, `resolve()`, `mark_reviewed`, `reject_image`, `get_tier_candidate_rows`, or any schema. No migration.
- **`batch/clusterize.py::resolve_cluster` must not be modified** — the `tmp_clusters` pipeline depends on it. Import and call it; wrap it.
- **Partition invariant:** for any blob, `set(∪ split_for_review outputs) == set(blob)` and the groups are pairwise disjoint. No member ever dropped (a dropped member is a latent unreviewable pair that blocks `ingest_promote`).
- **`enabled: false` == today:** with the flag off (or the config block absent), `list_clusters` returns exactly one cluster per union-find component, unchanged.
- Cursor encode/decode/slice, `has_next`, `next_cursor` logic is **untouched** — it just sees more items.
- Run `cd Backend && pytest` on its own — never combined with other pytest roots.
- Config: `settings.get("CLUSTERING.INGESTION_REVIEW_SPLITTING")` (dotted string), not attribute access, so a missing block disables cleanly instead of raising.
- Tier ladders (verbatim): `tier_a: {start: 0.05, decrement: 0.01, floor: 0.01}`, `tier_b: {start: 0.30, decrement: 0.05, floor: 0.05}`, `max_group_size: 12`, `enabled: true`.
- `member_info` in `list_clusters` is keyed by **UUID objects** (off the rows) — `member_info[m]` where `m` comes from `uf.get_cluster(root)`. No `str()` on that key.

---

## File Structure

**Create:**
- `Backend/app/services/cluster_splitting.py` — `split_for_review()` + the loose-attach / residual helpers. One responsibility: turn an oversized blob into a lossless partition of tight subgroups. Pure, no DB, no FastAPI.
- `Backend/tests/test_cluster_splitting.py` — unit tests for the above (pure, no DB).
- `tests/integration/test_ingestion_cluster_splitting.py` — `list_clusters` through the real schema: splits, `enabled:false` passthrough, cross-subgroup decision still settles.

**Modify:**
- `Backend/app/services/ingestion_service.py` — add `from collections import defaultdict`, `from Backend.app.services.cluster_splitting import split_for_review`, `resolve_cluster` is *not* imported here (only `cluster_splitting` imports it). Add `_split_params(tier)`. Build `pairs_by_member` in the row loop; run blobs through `split_for_review` in the cluster-assembly loop.
- `environments/settings.yaml` — add `clustering.ingestion_review_splitting`.
- `backend_api.md` — one sentence in "List Tier Clusters".
- `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` — one added case (many small clusters from one blob render + submit across them).
- `docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md` — status line → `done` at the end.

---

## Task 1: `cluster_splitting.py` — the lossless partitioner

**Files:**
- Create: `Backend/app/services/cluster_splitting.py`
- Create: `Backend/tests/test_cluster_splitting.py`

**Interfaces:**
- Consumes: `batch.clusterize.resolve_cluster(members, pairs_by_member, threshold, decrement, floor, max_size) -> list[list]` (existing, unchanged); `graph.uf.UnionFind`.
- Produces: `split_for_review(members, pairs_by_member, *, start: float, decrement: float, floor: float, max_size: int) -> list[list]` — output groups partition `members` exactly. Ids are any hashable (UUIDs in production, ints in tests).

- [ ] **Step 1: Write the failing tests**

Create `Backend/tests/test_cluster_splitting.py`:

```python
"""Unit tests for split_for_review -- pure, no DB. Ids are ints here; production uses UUIDs."""
import pytest

from Backend.app.services.cluster_splitting import split_for_review


def _symmetric(pairs):
    """[(a, b, dist), ...] -> {id: [(neighbor, dist), ...]} symmetric."""
    out = {}
    for a, b, d in pairs:
        out.setdefault(a, []).append((b, d))
        out.setdefault(b, []).append((a, d))
    return out


def _assert_partition(groups, members):
    flat = [m for g in groups for m in g]
    assert sorted(flat) == sorted(members), "every member appears exactly once"
    assert len(flat) == len(set(flat)), "no member in two groups"


class TestSplitForReview:
    def test_at_or_below_max_size_returns_whole(self):
        members = [1, 2, 3]
        assert split_for_review(members, {}, start=0.05, decrement=0.01, floor=0.01, max_size=3) == [members]

    def test_splits_a_loose_chain_into_tight_halves(self):
        # 1-2-3  ~loose~  4-5-6 : the 3-4 link is 0.045, everything else 0.02
        members = [1, 2, 3, 4, 5, 6]
        pairs = _symmetric([(1, 2, 0.02), (2, 3, 0.02), (3, 4, 0.045),
                            (4, 5, 0.02), (5, 6, 0.02)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert sorted(sorted(g) for g in groups) == [[1, 2, 3], [4, 5, 6]]

    def test_loose_member_attaches_to_nearest_core(self):
        # 1-2-3-4 tight core; 5 hangs off 4 at 0.048 (dropped by resolve_cluster at 0.04)
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (3, 4, 0.01), (4, 5, 0.048)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=4)
        _assert_partition(groups, members)
        # 5 is not dropped -- it rides along with the {1,2,3,4} core
        the_group_with_5 = next(g for g in groups if 5 in g)
        assert set(the_group_with_5) == {1, 2, 3, 4, 5}

    def test_chained_loose_members_all_attach(self):
        # tight {1,2,3}; 4 hangs off 3 at 0.047, 5 hangs off 4 at 0.046
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (3, 4, 0.047), (4, 5, 0.046)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert {4, 5}.issubset(next(g for g in groups if 1 in g))

    def test_blob_looser_than_first_step_returns_whole(self):
        # every edge in [start-decrement, start) -> resolve_cluster finds no core, nothing
        # attaches, the residual union-find reconnects them -> one group, nothing dropped
        members = [1, 2, 3, 4]
        pairs = _symmetric([(1, 2, 0.045), (2, 3, 0.045), (3, 4, 0.045)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert sorted(groups[0]) == [1, 2, 3, 4] and len(groups) == 1

    def test_disjoint_residual_component_emitted_as_its_own_group(self):
        # {1,2,3} tight core; {4,5} connected only to each other at 0.048 (no edge to the core)
        members = [1, 2, 3, 4, 5]
        pairs = _symmetric([(1, 2, 0.01), (2, 3, 0.01), (4, 5, 0.048)])
        groups = split_for_review(members, pairs, start=0.05, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert {4, 5} in [set(g) for g in groups]

    def test_oversized_core_at_floor_is_returned_not_dropped(self):
        # 4 members all mutually 0.005 -- tighter than floor; resolve_cluster gives up oversized
        members = [1, 2, 3, 4]
        pairs = _symmetric([(1, 2, 0.005), (2, 3, 0.005), (3, 4, 0.005), (1, 3, 0.005),
                            (1, 4, 0.005), (2, 4, 0.005)])
        groups = split_for_review(members, pairs, start=0.02, decrement=0.01, floor=0.01, max_size=3)
        _assert_partition(groups, members)
        assert len(groups) == 1 and sorted(groups[0]) == [1, 2, 3, 4]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && pytest tests/test_cluster_splitting.py -v`
Expected: `ModuleNotFoundError: No module named 'Backend.app.services.cluster_splitting'`.

- [ ] **Step 3: Implement `cluster_splitting.py`**

```python
"""
Presentational splitting of oversized ingestion-review clusters.

IngestionService.list_clusters groups a tier's candidate pairs with a flat union-find, which
merges loose A~B~C~...~Z chains into one 200+ member review card. This splits such a card into
tight subgroups, reusing batch.clusterize.resolve_cluster for the tight-core detection.

Unlike resolve_cluster -- which drops members left isolated at a tighter threshold as implicit
singletons (fine for confirmed-duplicate shaping) -- split_for_review is LOSSLESS: its output
groups are an exact partition of the input. A member dropped from ingestion review is a latent
unreviewable candidate pair that silently blocks ingest_promote.

See docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md.
"""
from batch.clusterize import resolve_cluster
from graph.uf import UnionFind


def split_for_review(members, pairs_by_member, *, start, decrement, floor, max_size):
    """Partition an oversized ingestion-review blob into tight subgroups.

    members: ids in one union-find component (any hashable; UUIDs in practice).
    pairs_by_member: id -> list[(neighbor_id, distance)], symmetric, covering every in-band
        pair among `members`. Entries for ids outside `members` are ignored. A real blob
        always has at least one edge, so this is never effectively empty in production.
    start / decrement / floor: the tier's threshold ladder (see the spec's config block).
    max_size: groups at or below this are left whole.

    Returns id-lists that partition `members` exactly -- every input id in exactly one group,
    none dropped, none duplicated. A group may exceed max_size when the tight core hit `floor`
    still oversized or a residual sub-component has no tight structure; the frontend collapses
    oversized cards.
    """
    members = list(members)
    if len(members) <= max_size:
        return [members]

    member_set = set(members)

    # 1. Tight cores via the existing recursive splitter. May omit members (implicit singletons).
    cores = [list(c) for c in resolve_cluster(
        members, pairs_by_member, start, decrement, floor, max_size,
    )]

    # 2. Re-attach every omitted member to the core holding its single tightest edge, globally
    #    tightest first, so a chain of loose members drains toward the core it hangs off.
    assigned = {m: i for i, core in enumerate(cores) for m in core}
    loose = member_set - assigned.keys()
    while loose:
        best = None  # (distance, loose_id, core_index)
        for m in loose:
            for neighbor, distance in pairs_by_member.get(m, ()):
                if neighbor in assigned and (best is None or distance < best[0]):
                    best = (distance, m, assigned[neighbor])
        if best is None:
            break  # the remaining loose ids reach no core
        _, m, core_index = best
        cores[core_index].append(m)
        assigned[m] = core_index
        loose.discard(m)

    # 3. Residual: loose ids that reach no core. They were all in one blob, so they're
    #    connected among themselves at the tier's outer threshold -- union-find their mutual
    #    edges and emit each component. Also the "whole blob is looser than start-decrement"
    #    fallback: `cores` is empty, nothing attaches, everyone lands here as one component.
    if loose:
        uf = UnionFind()
        for m in loose:
            uf.get(m)  # register even an id with no surviving mutual edge
            for neighbor, _ in pairs_by_member.get(m, ()):
                if neighbor in loose:
                    uf.connect(m, neighbor)
        for root in uf.list_clusters():
            cores.append(list(uf.get_cluster(root)))

    return [c for c in cores if c]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && pytest tests/test_cluster_splitting.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Confirm `resolve_cluster` is untouched**

Run: `cd Backend && pytest ../batch/tests/test_clusterize.py -q` (or from repo root `pytest batch/tests/test_clusterize.py -q`).
Expected: PASS — this task added no changes to `clusterize.py`, this is a guard.

- [ ] **Step 6: Commit**

```bash
git add Backend/app/services/cluster_splitting.py Backend/tests/test_cluster_splitting.py
git commit -m "feat: lossless cluster splitter for ingestion review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 2: wire `split_for_review` into `list_clusters` + config + service integration tests

**Files:**
- Modify: `Backend/app/services/ingestion_service.py`
- Modify: `environments/settings.yaml`
- Modify: `backend_api.md`
- Create: `tests/integration/test_ingestion_cluster_splitting.py`

**Interfaces:**
- Consumes: `split_for_review(members, pairs_by_member, *, start, decrement, floor, max_size)` from Task 1.
- Produces: `_split_params(tier: str) -> dict | None` (module-level in `ingestion_service.py`) — `{"start", "decrement", "floor", "max_size"}` or `None` when disabled/absent. `list_clusters` unchanged signature; its response shape unchanged.

- [ ] **Step 1: Add the config block**

`environments/settings.yaml`, insert under the existing `clustering:` key, as a sibling of `splitting:` (after the `splitting` block's `floor: 0.01` line, before the blank line preceding `scheduler:`):

```yaml
  ingestion_review_splitting:
    # Splits oversized IngestionService.list_clusters review cards into tight subgroups on
    # the fly -- presentational only, see
    # docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md.
    # Reuses clusterize.resolve_cluster for the tight cores; a wrapper re-attaches loose
    # members so no candidate pair ever leaves the review queue.
    enabled: true
    max_group_size: 12
    # Tier A candidate pairs are all < 0.05; Tier B's are 0.05-0.30. Each tier's ladder
    # starts at its band top and tightens by `decrement` until a group is small enough or
    # the next step would fall below `floor`.
    tier_a: { start: 0.05, decrement: 0.01, floor: 0.01 }
    tier_b: { start: 0.30, decrement: 0.05, floor: 0.05 }
```

- [ ] **Step 2: Verify Dynaconf reads the nested block**

Run:
```bash
./.venv311/Scripts/python.exe -c "from config.settings import settings; c=settings.get('CLUSTERING.INGESTION_REVIEW_SPLITTING'); print(bool(c), c.get('enabled'), c['tier_a']['start'], c['tier_b']['decrement'], c['max_group_size'])"
```
Expected: `True True 0.05 0.05 12`. If dict-style `c['tier_a']['start']` raises, fall back to attribute access (`c.tier_a.start`) in `_split_params` and note it in the report.

- [ ] **Step 2b: Write the failing service integration tests**

Create `tests/integration/test_ingestion_cluster_splitting.py`:

```python
"""
Integration tests for IngestionService.list_clusters cluster splitting against the real
schema. Splitting is presentational -- these prove the queue is reshaped without changing
decision semantics or promote-blocking.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _make_image(session, status, batch_id) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _make_pair(session, id1, id2, distance) -> None:
    session.add(TmpDuplicates(
        image_id1=min(id1, id2), image_id2=max(id1, id2), distance=distance, match_source="in_batch",
    ))
    await session.flush()


async def _chain(session, batch_id, n, loose_at):
    """n pending images in a chain; links are 0.02 except indices in `loose_at` (0-based edge
    index) which are 0.045 -> one union-find blob, splits at the loose links."""
    ids = [await _make_image(session, "pending", batch_id) for _ in range(n)]
    for i in range(n - 1):
        await _make_pair(session, ids[i], ids[i + 1], 0.045 if i in loose_at else 0.02)
    return ids


@pytest.fixture
def force_split(monkeypatch):
    """Force a small max_size so a modest fixture splits, without touching real config."""
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params",
        lambda tier: {"start": 0.05, "decrement": 0.01, "floor": 0.01, "max_size": 3},
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_blob_splits_into_a_partition_of_subgroups(db_session, force_split):
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})  # splits {0,1,2} {3,4,5} {6,7}
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_a", batch_id=batch_id)

    assert page["has_next"] is False
    assert len(page["items"]) >= 2
    returned = [str(m["image_id"]) for c in page["items"] for m in c["members"]]
    assert sorted(returned) == sorted(str(i) for i in ids)   # partition: nothing dropped/dupd
    assert len(returned) == len(set(returned))
    assert all(len(c["members"]) <= 3 for c in page["items"])


@pytest.mark.asyncio(loop_scope="session")
async def test_splitting_disabled_returns_one_cluster_per_blob(db_session, monkeypatch):
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params", lambda tier: None,
    )
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_a", batch_id=batch_id)

    assert len(page["items"]) == 1
    assert len(page["items"][0]["members"]) == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_on_a_split_member_still_settles_its_cross_subgroup_pair(db_session, force_split):
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})
    service = IngestionService(IngestionRepository(db_session))

    # ids[2] and ids[3] are in different subgroups but share the loose 0.045 pair.
    from unittest.mock import patch
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await service.resolve("tier_a", [{"image_id": ids[2], "decision": "reject"}])

    page = await service.list_clusters("tier_a", batch_id=batch_id)
    remaining_pairs = [
        (e["image_id1"], e["image_id2"]) for c in page["items"] for e in c["edges"]
    ]
    assert (str(min(ids[2], ids[3])), str(max(ids[2], ids[3]))) not in remaining_pairs
    blocked = await service.repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)
    assert ids[2] not in blocked
```

- [ ] **Step 3: Run to verify failure**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_splitting.py -v`
Expected: FAIL — `_split_params` doesn't exist yet (AttributeError on the monkeypatch target), and `list_clusters` still returns one blob.

- [ ] **Step 4: Implement `_split_params` and the `list_clusters` wiring**

In `Backend/app/services/ingestion_service.py`:

Imports — add:
```python
from collections import defaultdict
from Backend.app.services.cluster_splitting import split_for_review
```
(`resolve_cluster` is NOT imported here — only `cluster_splitting` imports it. The existing `from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD` line stays.)

Add module-level, near `_tier_band`:
```python
def _split_params(tier: str):
    """Threshold ladder + max size for `tier`, or None when splitting is disabled or the
    config block is absent (a stale/omitted overlay disables cleanly rather than raising)."""
    cfg = settings.get("CLUSTERING.INGESTION_REVIEW_SPLITTING")
    if not cfg or not cfg.get("enabled"):
        return None
    t = cfg[tier]
    return {
        "start": t["start"], "decrement": t["decrement"], "floor": t["floor"],
        "max_size": cfg["max_group_size"],
    }
```
(If Step 2 showed dict access fails, use `t = getattr(cfg, tier)` and `t.start` etc.)

In `list_clusters`, the row loop — add the `pairs_by_member` build:
```python
        uf = UnionFind()
        member_info: dict = {}
        edges: list[dict] = []
        member_uuids: set = set()
        pairs_by_member: dict = defaultdict(list)

        for id1, filename1, status1, id2, filename2, status2, distance, match_source in rows:
            uf.connect(id1, id2)
            member_info[id1] = {"image_id": str(id1), "filename": filename1, "status": status1}
            member_info[id2] = {"image_id": str(id2), "filename": filename2, "status": status2}
            member_uuids.update((id1, id2))
            pairs_by_member[id1].append((id2, distance))
            pairs_by_member[id2].append((id1, distance))
            edges.append({
                "image_id1": str(id1), "image_id2": str(id2),
                "distance": distance, "match_source": match_source,
            })
```

The cluster-assembly loop — replace:
```python
        clusters = []
        for root in uf.list_clusters():
            cluster_members = uf.get_cluster(root)
            member_ids = {str(m) for m in cluster_members}
            cluster_edges = [
                e for e in edges
                if e["image_id1"] in member_ids and e["image_id2"] in member_ids
            ]
            min_distance = min((e["distance"] for e in cluster_edges), default=1.0)
            min_image_id = min(member_ids)
            clusters.append({
                "members": [member_info[m] for m in cluster_members],
                "edges": cluster_edges,
                "_sort_key": (min_distance, min_image_id),
            })
```
with:
```python
        split_cfg = _split_params(tier)
        clusters = []
        for root in uf.list_clusters():
            blob = uf.get_cluster(root)
            groups = (
                split_for_review(blob, pairs_by_member, **split_cfg)
                if split_cfg is not None
                else [blob]
            )
            for group in groups:
                member_ids = {str(m) for m in group}
                group_edges = [
                    e for e in edges
                    if e["image_id1"] in member_ids and e["image_id2"] in member_ids
                ]
                min_distance = min((e["distance"] for e in group_edges), default=1.0)
                clusters.append({
                    "members": [member_info[m] for m in group],
                    "edges": group_edges,
                    "_sort_key": (min_distance, min(member_ids)),
                })
```

Nothing else in `list_clusters` changes — the OCR attach above this loop, and `clusters.sort` / cursor / slice / `has_next` / `next_cursor` / `_sort_key` pop below it, are all untouched.

- [ ] **Step 5: Run the integration tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_cluster_splitting.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Run the existing ingestion + pagination tests — behavior guard**

Run:
```bash
cd Backend && pytest -q -k "ingestion or cluster"
```
Expected: PASS (endpoint tests mock the service, so unaffected; `test_ingestion_service.py::TestListClustersPagination` still green — the cursor path is untouched).

Run:
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k "ingestion or clusterize"
```
Expected: PASS — including `test_ingestion_partial_resolve.py` and `test_ingestion_resolve_atomicity.py` (with real config, `enabled: true`, their small fixtures are all ≤ `max_group_size` so `split_for_review` returns them whole — no behavior change).

- [ ] **Step 7: Update `backend_api.md`**

In the "List Tier Clusters" section, add one sentence after the response description:

> A large union-find component is split into tight subgroups for review — each subgroup is a
> separate item in `items`. Decisions are per-image and settle every candidate pair regardless
> of which subgroup an image is shown in.

- [ ] **Step 8: Boot check + commit**

Run: `cd Backend && python -c "import Backend.app.main"` — no import errors (guards the new `cluster_splitting` import chain: `batch.clusterize` is already imported transitively by the existing `PROXIMITY_THRESHOLD` import, so no new `Storage.db`-guard exposure).

```bash
git add Backend/app/services/ingestion_service.py environments/settings.yaml backend_api.md tests/integration/test_ingestion_cluster_splitting.py
git commit -m "feat: split oversized ingestion review clusters into tight subgroups

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 3: frontend guard test + full verification + spec status

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`
- Modify: `docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md`

- [ ] **Step 1: Add the frontend guard test**

The page test's `react-virtuoso` mock renders whatever clusters the API returns, so server-side splitting is transparent — this case just guards that nothing in the page assumes "one card per blob". Add near the other submit tests in `IngestionReviewPage.test.tsx`:

```tsx
  it('renders and submits across several small clusters split from one blob', async () => {
    // three subgroups the server split out of one union-find component
    const subgroups: IngestionCluster[] = [1, 2, 3].map((n) => ({
      members: [{ image_id: `s${n}-1`, filename: `s${n}-1.jpg`, status: 'pending', ocr_text: null }],
      edges: [],
    }))
    const getIngestionClusters = vi.fn().mockResolvedValue(page(subgroups))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({
      rejected: ['s1-1', 's2-1', 's3-1'], kept: [], failed: [], move_failed: [],
    })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1-1.jpg')
    expect(screen.getByText('s2-1.jpg')).toBeInTheDocument()
    expect(screen.getByText('s3-1.jpg')).toBeInTheDocument()

    for (const btn of screen.getAllByRole('button', { name: /^reject$/i })) {
      await userEvent.click(btn)
    }
    await userEvent.click(screen.getByRole('button', { name: /submit all decisions/i }))
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() => {
      const [, payload] = resolveIngestionCluster.mock.calls[0]
      expect(payload).toHaveLength(3)
    })
  })
```

- [ ] **Step 2: Run the frontend test file**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: PASS (existing + 1 new).

- [ ] **Step 3: Full frontend gate**

Run:
```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
```
Expected: all clean.

- [ ] **Step 4: Full backend sweeps**

Run:
```bash
cd Backend && pytest -q
```
Expected: PASS.

Run from repo root:
```bash
pytest batch/tests/test_clusterize.py -q
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k "ingestion or clusterize"
```
Expected: PASS — `clusterize` behavior unchanged; all ingestion integration green.

- [ ] **Step 5: Manual smoke (if a `general` ingestion run is active)**

Use the running dev servers (`:5174` / `:8082`, never bind the occupied env ports). At `/ingestion`:
- The former 200+-tile card is now a series of small cards (a dozen-ish tiles each).
- Union of what you see still covers everything; a giant card that hit the floor still collapses via "show N more".
- Reject one image in one small card, submit — it resolves; the run's blocked-count drops as expected.

If no run is active, note it and rely on the integration tests.

- [ ] **Step 6: Update spec status + commit**

In `docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md`, change `status: approved` → `status: done` and add `Plan: docs/superpowers/plans/2026-09-09-ingestion-review-cluster-splitting.md` under it.

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md
git commit -m "test: frontend guard for split clusters; mark spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

- [ ] **Step 7: Whole-branch review**

Invoke `superpowers:requesting-code-review` for the branch against the spec. One iteration; fix action points; post the final summary.

---

## Self-Review (completed during planning)

**Spec coverage:**
- Spec §1 (`cluster_splitting.py` / `split_for_review`) → Task 1 (module + 7 unit tests incl. the partition assertion helper).
- Spec §2 (`list_clusters` integration, `pairs_by_member`, unchanged downstream) → Task 2 Step 4.
- Spec §3 (config block + `_split_params`, `settings.get` dotted, tier ladders) → Task 2 Steps 1, 2, 4.
- Spec §4 (`backend_api.md` sentence) → Task 2 Step 7.
- Spec §5 (frontend: no code change, guard test, collapse stays) → Task 3 Step 1.
- Spec Invariants 1–5 → partition helper (unit, every case) + Task 2 integration tests: #2 cross-subgroup decision, #3 `get_blocked_pending_ids`, #4 `enabled:false` one-cluster test, #5 `TestListClustersPagination` regression run (Task 2 Step 6).
- Spec §6 testing → Tasks 1/2/3 test steps map 1:1.
- Non-goals respected: no schema/migration, `resolve_cluster` unmodified (Task 1 Step 5 + Task 3 Step 4 guard), no API shape change (frontend test uses the existing `IngestionCluster`), no precompute.

**Placeholder scan:** No TBD/TODO. Every code step has literal code; every test step names the command and expected result. The one conditional ("if dict access fails, use attribute access") is a verified fork with both branches spelled out, gated by Step 2's check.

**Type consistency:** `split_for_review(members, pairs_by_member, *, start, decrement, floor, max_size)` identical in Task 1 (definition), Task 1 tests, Task 2 call (`**split_cfg`). `_split_params -> dict | None` with keys `start/decrement/floor/max_size` matches `split_for_review`'s kwargs exactly. `pairs_by_member` shape `{id: [(neighbor, distance)]}` identical in the unit-test `_symmetric` helper, the `list_clusters` row-loop build, and `resolve_cluster`'s existing contract. `member_info` keyed by UUID (`member_info[m]`, no `str()`) — matches the current code.
