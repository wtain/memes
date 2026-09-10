# Ingestion Review — Tier B Cluster Cap (Stopgap)

status: done
Plan: docs/superpowers/plans/2026-09-10-ingestion-tier-b-cluster-cap-stopgap.md
Originates from: debugging `/ingestion` tier B hanging on `general` (2026-09-10). Superseded by
`docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md` once that ships —
this is a short-lived bridge.

## Problem

Tier B on `general` is one union-find component of ~23,700 pending images (232k candidate
pairs, band 0.05–0.30). After the `split_for_review` O(E log E) fix, `list_clusters("tier_b")`
*returns*, but its output still contains one "cluster" of ~12,000 members — a ~tens-of-MB
response the browser can't render, ~20–40s to build. The page is unusable.

The real fix is per-image tier B review (separate spec). This stopgap just makes the endpoint
return a bounded payload so the small tier-B components (there are 62, all < 400 members) and
tier A stay reviewable while that's built.

## Goal

`list_clusters` never returns a cluster larger than a hard cap; the frontend shows what's
capped. No new endpoint, no schema restructure, ~15 lines.

## Non-goals

- Making the giant pseudo-cluster reviewable — that's the per-image spec.
- Preserving the promote-blocking invariant for the capped-off members (see Accepted cost).
- Any change to `resolve`, `split_for_review`, the cursor, or tier A behavior.

## Accepted cost

For a group that exceeds the cap, only the cap-many tightest members are ever returned. The
rest of that group's pending images keep their unresolved tier-B pairs, so `ingest_promote`
continues to block them and the run cannot complete. This is deliberate and short-lived: the
per-image review spec, landing right after, reviews those images and unblocks the run. On
`general` this affects the ~11,900 members of the one oversized group; every other tier-B
group and all of tier A are unaffected (none approaches the cap).

## Design

### 1. `IngestionService.list_clusters` — cap members and edges per group

`ingestion_service.py`, in the `for gi, group in enumerate(groups)` loop, after `group_edges`
/ `min_distance` / `_sort_key` are computed (so ordering and the cursor are unaffected — they
use the full group), cap what goes into the response:

```python
CLUSTER_MEMBER_CAP = 60  # module-level constant

# ... inside the group loop, after _sort_key is built from the full group_edges:
member_list = [member_info[m] for m in group]
total_members = len(member_list)
if total_members > CLUSTER_MEMBER_CAP:
    # keep the cap-many members with the tightest edge to another kept member.
    by_tightest = _members_by_tightest_edge(group, group_edges)  # list[str image_id], tightest first
    keep = set(by_tightest[:CLUSTER_MEMBER_CAP])
    member_list = [mi for mi in member_list if mi["image_id"] in keep]
    group_edges = [e for e in group_edges if e["image_id1"] in keep and e["image_id2"] in keep]

clusters.append({
    "members": member_list,
    "edges": group_edges,
    "total_members": total_members,
    "_sort_key": (min_distance, min(member_ids)),
})
```

`_members_by_tightest_edge(group, edges)` — a small module helper: for each member id (str),
its minimum incident edge distance among `edges`; members with no edge sort last; return the
member ids sorted `(min_incident_distance, image_id)` ascending. Pure, unit-testable.

`_sort_key` and `min_distance` are still computed from the **uncapped** `group_edges` above
this block, so the cursor's total order over clusters does not change.

Under `split_cfg` enabled (the norm), `split_for_review` already keeps most groups ≤ 12; only
a residual/floor-capped group can exceed 60. Under `split_cfg is None`, a whole blob is one
group and the cap does the work.

### 2. `Cluster` response model + schema — add `total_members`

- `Backend/app/api/ingestion.py`: `Cluster` gains `total_members: int`.
- `shared/schemas/ingestioncluster.schema.json`: add `"total_members": { "type": "integer" }`
  to `properties` and to `required`.
- Regenerate TS (`bash Frontend/generate-types.sh`) and Kotlin
  (`python AndroidClient/scripts/generate_dtos.py`); commit the regenerated files.
- `backend_api.md`: note `total_members` and that `members` is capped at 60.

### 3. Frontend — `ClusterRow` shows the cap

`ClusterRow.tsx`: it already collapses to `COLLAPSED_COUNT` (8) with a "Show N more" toggle.
When `cluster.total_members > cluster.members.length`, the expanded view can only show
`members.length`, so change the toggle / add a line:

- `hidden` is currently `cluster.members.length - COLLAPSED_COUNT`. Keep the collapse behavior.
- When `total_members > members.length`, render a muted line under the grid:
  `Showing {members.length} of {total_members} — this candidate group is too large to review
  as a cluster; per-image review is coming.`
- `edgeSummaryFor` is unchanged (operates on the capped `edges`, which is fine).

No other frontend change — `IngestionCluster` keeps its shape plus the one new field.

## Testing

- **Unit** (`Backend/tests/test_ingestion_service.py` or `test_cluster_splitting.py` sibling):
  `_members_by_tightest_edge` orders by min incident distance then id; edgeless members last.
- **Service integration** (`tests/integration/test_ingestion_cluster_splitting.py`, new case):
  a blob that `split_for_review` can't break below the cap (e.g. `force_split` with a fixture
  that stays one 80-member group) → `list_clusters` returns that cluster with
  `len(members) == 60`, `total_members == 80`, `edges` only among the 60; `_sort_key` /
  ordering identical to the uncapped run (assert the cursor/`next_cursor` unchanged).
- **Frontend** (`ClusterRow.test.tsx`): a cluster with `total_members: 500`, `members` length
  60 → the "Showing 60 of 500" line renders; a normal cluster (`total_members` == members
  length) → no such line.
- Regenerated-type drift check (`git diff --exit-code` on generated dirs).

## Rollout

Ships as its own small PR, merged before the per-image spec's plan starts. Once per-image
review lands and the frontend routes tier B to the new endpoint, `list_clusters` is only used
by tier A, where the cap never triggers — the code stays (harmless) or is removed in that PR's
cleanup, its call.
