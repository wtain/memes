# Ingestion Review — Cluster Splitting

status: approved
Originates from: `docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md` (the layout rework surfaced 200+-member review clusters); user request 2026-09-09.

## Problem

`IngestionService.list_clusters` groups a tier's candidate pairs into review cards with a
single flat union-find over `tmp_duplicates`: any two images linked by an in-band pair land in
the same card, transitively. On the live `general` queue this produces cards with **225+
members** — a loose chain `A~B~C~…~Z` where each link is close but the ends are not, merged
into one blob. A reviewer cannot meaningfully keep/reject 225 tiles as a unit; the real
structure is several tight groups (one per meme template) plus some loosely-similar stragglers.

The confirmed-duplicate pipeline already solves this: `batch/clusterize.py::resolve_cluster`
recursively re-clusters an oversized union-find blob at progressively tighter distance
thresholds. It is pure, DB-free, unit-tested, and works with any hashable id. It is **not**
wired into the ingestion review path.

## Goal

Split oversized ingestion-review cards into tight subgroups, on the fly, reusing
`resolve_cluster`, without changing decision semantics, the data model, or the
promote-blocking invariant.

## Non-goals

- No change to `tmp_duplicates`, `resolve()`, `mark_reviewed`, `reject_image`, or
  `get_tier_candidate_rows`. Splitting is **presentational** — see Invariants.
- No precomputed `review_group` column / batch step. Splitting is stateless, recomputed per
  `/clusters` request (the union-find already is).
- No change to `resolve_cluster` itself — `clusterize` / `tmp_clusters` behavior must be
  untouched. The ingestion adaptation wraps it.
- No frontend API-shape change. `IngestionCluster` is identical; the page just receives more,
  smaller cards.
- Not addressing Tier B's outer threshold source or the OCR ordering — those are settled.

## Key insight: splitting is purely presentational

`resolve()` applies decisions per `image_id`. `mark_reviewed` sets the tier's `reviewed_at` on
**every** `tmp_duplicates` row touching that image; `reject_image` makes every pair touching a
rejected image moot (`get_tier_candidate_rows` filters rejected sides out). So **which card an
image is shown in has zero effect** on:

- what a keep/reject settles (always all of that image's pairs, including cross-subgroup chain
  links),
- `get_blocked_pending_ids` / `ingest_promote` gating,
- whether the run can complete.

The only hard requirement the split must meet: **every candidate pair stays reviewable** —
i.e. every member of an original blob must appear in exactly one output group. `resolve_cluster`
as-is violates this (it drops members left with no surviving edge at a tighter threshold as
"implicit singletons"). For `clusterize` that's correct — a dropped member simply isn't a
confirmed duplicate. For ingestion review a dropped member is a *latent unreviewable pair*: it
still has `reviewed_at IS NULL` rows, so it silently blocks `ingest_promote` and the run never
finishes. Hence the wrapper below.

## Design

### 1. New module — `Backend/app/services/cluster_splitting.py`

```python
def split_for_review(
    members: list,
    pairs_by_member: dict,          # id -> list[(neighbor_id, distance)], symmetric
    *,
    start: float,
    decrement: float,
    floor: float,
    max_size: int,
) -> list[list]:
    """Partition an oversized ingestion-review blob into tight subgroups.

    Guarantee: the returned groups are a partition of `members` -- every input id appears in
    exactly one output group, none dropped, none duplicated. (This is the difference from
    clusterize.resolve_cluster, which drops implicit singletons.)

    Groups are <= max_size in the common case; a group can still exceed it when the tight
    core hit `floor` oversized, or a residual sub-component has no tight structure. The
    frontend collapses oversized cards.
    """
```

Algorithm:

1. `if len(members) <= max_size: return [list(members)]` — nothing to split.
2. **Tight cores:** `cores = resolve_cluster(members, pairs_by_member, start, decrement, floor, max_size)`
   (imported from `batch.clusterize`, called unchanged). `cores` is a list of member-id lists,
   possibly omitting some of `members`.
3. **Attach loose members.** `assigned = {id: core_index for each id in each core}`;
   `loose = set(members) - assigned.keys()`. Repeat until no change:
   - For each still-loose `L`, find its tightest edge (min distance in `pairs_by_member[L]`)
     to any currently-`assigned` neighbour.
   - Of all `(L, neighbour, distance)` candidates, take the globally tightest; add `L` to
     `assigned[neighbour]`'s core; mark `L` assigned.
   - (A newly-assigned member becomes an anchor for the next iteration, so chained-loose
     members drain in tightest-first order.)
   Simple iterative implementation; a Prim-style priority queue is a drop-in optimization if
   a profile ever shows it matters (loose sets are small in practice).
4. **Residual sub-components.** Any members still unassigned have edges only to each other.
   Union-find them using their mutual edges (any distance — they were all in one blob, so all
   `< tier high`), and emit each connected component as its own group. If step 2 returned
   nothing and nothing attached (every edge in the blob sits in `[start-decrement, start)`),
   the whole blob is one residual component → returned whole, which is the correct
   "can't split meaningfully" fallback.

`resolve_cluster` is imported, not modified. If a shared home feels cleaner later it can move,
but this spec keeps it in `clusterize.py`.

### 2. `IngestionService.list_clusters` integration

`ingestion_service.py`, after the union-find loop builds `uf` / `edges` / `member_info`
(unchanged) and before the `clusters = []` assembly:

```python
split_cfg = _split_params(tier)           # None when disabled
pairs_by_member: dict = defaultdict(list)
if split_cfg is not None:
    for e in edges:
        a, b, d = UUID(e["image_id1"]), UUID(e["image_id2"]), e["distance"]
        pairs_by_member[a].append((b, d))
        pairs_by_member[b].append((a, d))

clusters = []
for root in uf.list_clusters():
    blob = uf.get_cluster(root)                       # list[UUID]
    groups = (
        split_for_review(blob, pairs_by_member, **split_cfg)
        if split_cfg is not None
        else [blob]
    )
    for group in groups:                                  # group: list[UUID]
        member_ids = {str(m) for m in group}
        group_edges = [e for e in edges
                       if e["image_id1"] in member_ids and e["image_id2"] in member_ids]
        min_distance = min((e["distance"] for e in group_edges), default=1.0)
        clusters.append({
            "members": [member_info[m] for m in group],   # member_info is keyed by UUID today
            "edges": group_edges,
            "_sort_key": (min_distance, min(member_ids)),  # string min, as today
        })
```

`member_info` is keyed by the UUID objects that come off the rows
(`member_info[id1] = ...`), and `uf.get_cluster(root)` returns those same UUIDs, so
`member_info[m]` is a direct lookup — no `str()` on the key. Everything after this block — the
OCR-text attach (already done on `member_info`), `clusters.sort`, the cursor decode/slice,
`has_next` / `next_cursor`, the `_sort_key` pop — is **unchanged**, just over more items.

### 3. Config

`environments/settings.yaml`, under the existing `clustering:` group (sibling of `splitting`):

```yaml
clustering:
  splitting:          # existing -- clusterize.py / tmp_clusters, unchanged
    ...
  ingestion_review_splitting:
    # Splits oversized IngestionService.list_clusters cards into tight subgroups on the fly
    # (presentational only -- see 2026-09-09-ingestion-review-cluster-splitting-design.md).
    # Reuses clusterize.resolve_cluster for the tight cores; a wrapper re-attaches loose
    # members so no candidate pair leaves the review queue.
    enabled: true
    max_group_size: 12
    # Tier A candidate pairs are all < 0.05; Tier B's are 0.05-0.30. Each tier's ladder
    # starts at its own band top and tightens by `decrement` until a group is small enough
    # or the next step would fall below `floor`.
    tier_a: { start: 0.05, decrement: 0.01, floor: 0.01 }
    tier_b: { start: 0.30, decrement: 0.05, floor: 0.05 }
```

`_split_params(tier)` in `ingestion_service.py`:

```python
def _split_params(tier: str) -> dict | None:
    cfg = settings.get("CLUSTERING.INGESTION_REVIEW_SPLITTING")
    if not cfg or not cfg.get("enabled"):
        return None
    t = cfg[tier]                                   # tier is "tier_a" | "tier_b"
    return {
        "start": t["start"], "decrement": t["decrement"], "floor": t["floor"],
        "max_size": cfg["max_group_size"],
    }
```

Use `settings.get("CLUSTERING.INGESTION_REVIEW_SPLITTING")` (dotted string) rather than
attribute access, so an environment that omits the block entirely disables splitting cleanly
rather than raising. `clustering` currently lives only in the common `settings.yaml`, so all
three environments inherit it; per-env overlays can set `enabled: false` if wanted.

### 4. `backend_api.md`

The "List Tier Clusters" response shape is unchanged. Add one sentence: *"A large union-find
component is split into tight subgroups for review; each subgroup is a separate item.
Decisions are per-image and settle every candidate pair regardless of which subgroup an image
is shown in."*

### 5. Frontend

No code change required — `IngestionCluster` is identical and the page already handles many
clusters. The `ClusterRow` "show 8 / Show N more" collapse stays as the display safety net for
a subgroup that came back oversized. One test is added (§6).

Deferred nice-to-have (not in this spec): a muted "linked to a larger candidate group" hint on
a card whose members have `tmp_duplicates` edges to images outside the card. Needs the API to
expose cross-group edge counts; skip for now.

## Invariants (must hold; assert in tests)

1. **Partition.** For any blob, `set(union of split_for_review's output groups) == set(blob)`
   and the groups are pairwise disjoint.
2. **Decision semantics unchanged.** Keeping/rejecting a member that was split into group X
   still settles that member's pairs to members now shown in group Y (verified through
   `resolve` + `list_clusters` in an integration test).
3. **Nothing leaves the queue.** No pending member with an unresolved in-band pair is ever
   absent from `list_clusters` output because of splitting → `get_blocked_pending_ids` and run
   completion behave exactly as with `enabled: false`.
4. **`enabled: false` == today.** With the flag off, `list_clusters` returns one cluster per
   union-find component, byte-for-byte as before this change.
5. **Pagination unaffected.** Cursor encode/decode/slice, `has_next`, `next_cursor` logic is
   untouched; it just sees more items.

## Testing

### Unit — `Backend/tests/test_cluster_splitting.py` (new, pure, no DB)

- `under_max_size` → returns `[members]` unchanged.
- Chain `1~2~…~8` with a loose middle link → splits into the two tight halves; **union of
  outputs == input** (partition assertion helper used in every case).
- A loose member whose only edge is to a tight core → attached to that core.
- Two chained-loose members → both attach, tightest-first.
- A blob whose every edge is in `[start-decrement, start)` → returned whole (one group),
  nothing dropped.
- Residual sub-component with no tight core and no edge to any core → emitted as its own
  group.
- Group can exceed `max_size` when the core hit `floor` — assert it's still returned, not
  dropped.

### Service integration — `tests/integration/test_ingestion_cluster_splitting.py` (new)

Uses the `IngestionService` + real-schema pattern from
`test_ingestion_partial_resolve.py` / `test_ingestion_resolve_atomicity.py`.

- Build a Tier A chain blob of ~10 pending images (`_make_pair` at mixed 0.02 / 0.045
  distances) so it's one union-find component but two tight cores. `list_clusters("tier_a",
  batch_id=…)` returns **≥ 2** clusters; the union of all returned members == the 10 images;
  every returned cluster is ≤ `max_group_size` (given the fixture is built to split cleanly).
- With `settings` patched to `enabled: false` (or the block removed), the same fixture returns
  **exactly one** cluster of 10 — invariant #4.
- Decide a member that landed in one subgroup (`resolve` reject) whose blob had a cross-core
  pair; re-fetch `list_clusters` → that pair is gone from every cluster, the other subgroup is
  reshaped, and `get_blocked_pending_ids` no longer lists the rejected image — invariant #2/#3.
- Tier B fixture (pairs at 0.08 / 0.22) → splits with the `tier_b` ladder; `enabled:false`
  returns one blob.

### Frontend — `IngestionReviewPage.test.tsx` (one added case)

The page test mock already supplies pre-shaped clusters, so splitting is server-side and
mostly transparent. Add: `getIngestionClusters` returns 3 small clusters that (per the mock)
came from one blob; assert all three render, a decision in each, and "Submit all" sends every
decision. (Guards that nothing in the page assumes "one card per blob".)

### Regression

- `cd Backend && pytest -k "ingestion or cluster_splitting"` green.
- `pytest batch/tests/test_clusterize.py` green — `resolve_cluster` untouched.
- `DATABASE_URL=… pytest tests/integration/ -k "ingestion or clusterize"` green.
- Frontend `tsc -b`, `eslint src/`, `vitest run` green.

## Rollout / risk

- Pure add + one flag. `enabled: false` is a complete kill switch that provably reproduces
  today's output (invariant #4 test).
- CPU: `split_for_review` runs only for blobs `> max_size`. `resolve_cluster` is already
  benchmarked for `clusterize`'s corpus-wide use; per-request blobs are far smaller. The loose
  re-attach loop is O(loose² · degree) worst case with small loose sets — acceptable; PQ
  optimization noted if a profile disagrees.
- The one behavioral change a reviewer sees: a former 225-tile card becomes ~15-30 small
  cards. That's the point. If the subgroup count is overwhelming, tune `max_group_size` up or
  `decrement` coarser via config — no code change.
