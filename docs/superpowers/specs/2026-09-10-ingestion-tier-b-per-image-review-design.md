# Ingestion Review — Per-Image Tier B Review

status: done
Plan: docs/superpowers/plans/2026-09-10-ingestion-tier-b-per-image-review.md
Follow-ups: docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md,
docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md
Originates from: debugging `/ingestion` tier B hanging on `general` (2026-09-10); the
cluster model degenerates on real tier-B data.
Supersedes (operationally): the tier-B path of
`docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md` and the
stopgap `2026-09-10-ingestion-tier-b-cluster-cap-stopgap-design.md`.

## Problem

Tier B review is modelled as clusters: `IngestionService.list_clusters` runs a flat
union-find over the tier's candidate pairs (band 0.05–0.30) and returns each connected
component as a review card. On `general`'s live batch this produces **one component of
23,671 pending images** (232k candidate pairs) plus 62 tiny ones. Investigation
(2026-09-10) showed:

- Tightening the band does not fragment the mega-component — even `[0.05, 0.08)` still has a
  1,525-member component. These are dense template-neighbourhoods (a meme reposted hundreds
  of times); any threshold catching the real dups also chains the neighbourhood.
- `split_for_review` (the cluster-splitting feature) can't help — it carves out ~2,500
  members into tight ≤12 cores and re-attaches the other ~21,000 into one ~12,000-member
  group.

Clustering is the wrong unit for tier B. What a reviewer actually decides is per-image:
*"is this newly-ingested image a duplicate of something?"*

## Goal

A tier-B review queue whose unit is **one pending image + its unreviewed candidate
neighbours**, bounded by construction, paginated over pending images rather than over
candidate pairs.

## Non-goals

- Tier A. It works — small components, clusters are the right unit. Tier A keeps
  `list_clusters` / `ClusterRow` unchanged.
- Any change to `resolve`, `mark_reviewed`, `reject_image`, `get_blocked_pending_ids`,
  `ingest_promote`, the `tmp_duplicates` schema, or the tier bands.
- Reviewing candidates that are `active` (already-in-corpus) images — they are read-only
  context; a decision on the pending subject settles those pairs.
- A separate "resolve" endpoint — decisions still `POST /api/ingestion/clusters/tier_b/resolve`.

## Key facts this rests on

- `resolve(tier, [{image_id, decision}])` is image-centric and cluster-agnostic. `keep` →
  `mark_reviewed(image_id, "tier_b")` sets `tier_b_reviewed_at` on **every** `tmp_duplicates`
  row touching that image (settles its pairs to every neighbour, decided or not). `reject` →
  `reject_image` flips status, and `get_tier_candidate_rows` then excludes any pair with a
  rejected side.
- So each pending image is decided **exactly once**, and that decision resolves all of its
  tier-B candidate pairs regardless of which card the click happened on.
- `get_blocked_pending_ids` / `ingest_promote` gate on unresolved rows — as long as every
  pending image with an open pair appears in the queue, run completion is unaffected.

## Design

### 1. Repository — `list_tier_b_review_page`

New method on `IngestionRepository`. Two queries (avoids fetching all 232k pair-rows into
Python just to rank and slice):

**Query A — rank the subjects, apply the cursor, take the page.**
Expand each unreviewed in-band `tmp_duplicates` row into `(subject_id, distance)` for every
side that is a `pending` image of this batch whose *other* side is not `rejected` (a
`UNION ALL` of the two directions over a CTE / subquery, then join to `images` for the
subject's filename+status); then `GROUP BY subject_id`, `min(distance) AS min_distance`,
`count(*) AS total_candidates`, `ORDER BY min_distance, subject_id`, `LIMIT limit + 1`
(the +1 gives `has_next`). The cursor filter goes in `HAVING`, as a Postgres row-value
comparison over the aggregate:
`HAVING (min(distance), subject_id) > (:cursor_distance, :cursor_subject_id)` — omitted
entirely when there is no cursor. `:cursor_distance` is the exact float from the previous
page's `_encode_cursor` (`repr()` round-trips a double exactly through `float()`), passed as
an asyncpg parameter. Returns `[(subject_id, subject_filename, subject_status, min_distance,
total_candidates)]` for the page.

**Query B — candidates for the page's subjects only.**
For `subject_id IN (page subject ids)`, the unreviewed in-band pairs, joined to the other
image: `(subject_id, cand_id, cand_filename, cand_status, distance, match_source)`, ordered
`subject_id, distance, cand_id`. In the service, keep the tightest `CANDIDATE_CAP` (30) per
subject (capping later moved to the repository — see the candidate-query-bound follow-up spec).

`reviewed_col` / band / `not rejected` / `pending-in-batch` predicates mirror
`get_tier_candidate_rows`. The `image_id1 = s OR image_id2 = s` expansion uses
`UNION ALL` of the two directions (planner-friendlier than `OR` on the join), each direction
filtered to its `tmp_duplicates` side.

Perf note: Query A aggregates ~232k rows → ~7,200 groups; measured ~3–5s on the live DB
today (the current hang is `split_for_review`, not the fetch). Acceptable for v1 — this
replaces an endpoint that never returns. A partial index on
`tmp_duplicates(tier_b_reviewed_at) WHERE tier_b_reviewed_at IS NULL` is a fast follow if
needed, out of scope here.

### 2. Service — `IngestionService.list_tier_b_review`

```
async def list_tier_b_review(self, batch_id=None, cursor=None, limit=40) -> dict
```

1. `_resolve_batch_id`.
2. Query A → page subjects.
3. Query B → candidates; group by subject, sort by distance, cap at `CANDIDATE_CAP` (capping later moved to the repository — see the candidate-query-bound follow-up spec).
4. `get_ocr_texts` over `{subject ids} ∪ {shown candidate ids}` with `settings.OCR.*`
   thresholds (reuse the existing method).
5. Build items:
   ```
   {
     "image": {"image_id", "filename", "status", "ocr_text"},   # the pending subject
     "candidates": [
       {"member": {"image_id", "filename", "status", "ocr_text"},
        "distance": float, "match_source": str | None}
     ],
     "total_candidates": int,        # before the CANDIDATE_CAP
   }
   ```
6. `next_cursor` = `_encode_cursor(min_distance, str(subject_id))` of the last page item when
   `has_next`, else `None`. Reuse `_encode_cursor` / `_decode_cursor` verbatim.
7. Return `{"items": [...], "next_cursor": ..., "has_next": ...}`.

### 3. API — new endpoint + models

`Backend/app/api/ingestion.py`:

```python
class TierBCandidate(BaseModel):
    member: ClusterMember
    distance: float
    match_source: Optional[str]

class TierBReviewItem(BaseModel):
    image: ClusterMember
    candidates: list[TierBCandidate]
    total_candidates: int

class TierBReviewPage(BaseModel):
    items: list[TierBReviewItem]
    next_cursor: Optional[str]
    has_next: bool

@router.get("/review/tier_b", response_model=TierBReviewPage)
async def tier_b_review(
    cursor: Optional[str] = None,
    limit: int = Query(40, ge=1, le=200),
    service: IngestionService = Depends(get_ingestion_service),
):
    return await service.list_tier_b_review(cursor=cursor, limit=limit)
```

- Reuses `ClusterMember`.
- New `shared/schemas/`: `ingestiontierbcandidate.schema.json`,
  `ingestiontierbreviewitem.schema.json`, `ingestiontierbreviewpage.schema.json`; register in
  `all.schema.json`. Regenerate TS + Kotlin, commit.
- `backend_api.md`: new "Tier B Review" section under "Ingestion".

`/api/ingestion/clusters/tier_b` (the cluster endpoint) stays — still used for a brief
`tier_b_review` stage window if the run has no per-image work, and harmless. The frontend
just stops calling it for tier B.

### 4. Frontend — API client + types

- `MemesApi.getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage>`;
  `HttpMemesApi` → `GET /api/ingestion/review/tier_b?limit=40&cursor=`.
- Generated types: `IngestionTierBReviewPage`, `IngestionTierBReviewItem`,
  `IngestionTierBCandidate` (from the schemas).
- `test/mockApi.ts`: default `{ items: [], next_cursor: null, has_next: false }`.

### 5. Frontend — `IngestionReviewPage` branches by tier

`IngestionReviewPage.tsx`:

- `tier === "tier_a"` → the existing `ClusterRow` list + `getIngestionClusters` (unchanged).
- `tier === "tier_b"` → a new `TierBReviewList` fed by `getIngestionTierBReview`.

Shared, unchanged: `tierForStage`, `StatusBanner`, the fixed "Submit all" action bar, the
2-step confirm, `decisions` state (keyed by `image_id` globally), the peek `Modal`, the
optimistic-submit machinery, `formatResolveSummary`, reload-when-queue-empties (Ruling 3).

`decisions`, `submitting`, `runSubmit`, `submitAll`, the sticky bar and its counts already
operate on `image_id`s and cluster-or-item objects — they need only a small generalisation:
the "unit" is now `IngestionCluster | IngestionTierBReviewItem`. Extract the
"pending image ids in this unit" and "is this unit fully resolved" as two helpers that switch
on shape (a cluster's pending members vs. an item's subject + in-batch candidates).

### 6. Frontend — `TierBReviewList` + `TierBReviewCard`

`src/components/ingestion/TierBReviewList.tsx` — `<Virtuoso useWindowScroll>` over the loaded
items; `endReached` + "Load more" append via cursor; identical shape to the tier-A list's
virtualization (reuse the `vi.mock('react-virtuoso')` test pattern).

`src/components/ingestion/TierBReviewCard.tsx` — one review item:

- **Subject** pinned at the top: `MemberTile` (large), always with Keep/Reject (it's a
  pending image by construction).
- **Candidates** below in the responsive grid (`repeat(auto-fill, minmax(300px, 1fr))`, as
  `ClusterRow`), first `COLLAPSED_COUNT` shown + "Show N more" (`total_candidates` may exceed
  the returned `candidates.length` — show "Showing K of `total_candidates`").
  - A candidate with `member.status === "pending"` → `MemberTile` with Keep/Reject
    (decidable — it's an in-batch pending image; **decision #3**).
  - A candidate with `member.status === "active"` → `MemberTile` with **no** Keep/Reject
    (read-only corpus context).
  - Each candidate tile shows its `distance` + `match_source` as the edge summary line
    (`0.087 · cross_corpus`).
- Per-card "Submit decisions" button (as `ClusterRow`), enabled when any decidable image in
  the card has a decision.

**Cross-card consistency (decision #3):** an in-batch candidate image X can appear both as a
candidate on subject Y's card *and* as its own subject card. `decisions` is keyed by
`image_id`, so a decision on X renders identically everywhere (`MemberTile` reads
`decisions[member.image_id]`). No dedupe layer needed. On submit, X's card is
optimistically removed once `decisions[X]` is set (same rule as a fully-resolved cluster);
if X was only shown as Y's candidate, Y's card stays and X's tile there goes read-only via
the same `resolvedStatus` flip already built for tier A.

### 7. Optimistic submit / reload — generalised, not rewritten

`runSubmit` already: computes decided-pending ids for the units being submitted, optimistically
removes fully-resolved units, calls `resolve`, prunes decisions payload-scoped, flips resolved
members to read-only, re-inserts on `failed`, rolls back on throw, reloads when the visible
list empties. All of that is `image_id`-based and unit-shape-agnostic **except** two helpers:

- `decidedPendingIn(unit)` → for a cluster: pending members with a decision; for a tier-B
  item: the subject (if decided) + in-batch candidates with a decision.
- `isFullyResolved(unit)` → for a cluster: every pending member decided; for a tier-B item:
  the **subject** is decided (its in-batch candidates each own their own card; a card is
  "done" when its subject is decided, even if some candidate tiles are still undecided —
  those get decided on their own cards).

The `resolvedStatus` flip (rejected→"rejected", kept→"active") applies to candidate tiles
that are still shown on a surviving card.

## Invariants

1. **Every pending image with an open tier-B pair is in the queue.** Query A's subject set is
   exactly "pending-in-batch images with ≥1 unreviewed in-band pair to a non-rejected other".
2. **Each pending image decided once.** Its decision (from any card) settles all its pairs via
   `mark_reviewed` / `reject_image`. Duplicate decisions are backend no-ops (pending guard /
   `is_(None)` filter).
3. **Run completion unaffected.** `get_blocked_pending_ids` / `ingest_promote` unchanged; the
   queue covers every blocking image.
4. **Tier A unchanged** — byte-identical `list_clusters` path and `ClusterRow`.
5. **Cursor stable** — `(min_distance, subject_id)`, `repr(float)|uuid`, strict `>` slice;
   same skip-then-reload tolerance the cluster cursor documents.

## Testing

### Backend unit — `test_ingestion_service.py` (`TestListTierBReview`, mocked repo)
- Item shape: `image` + `candidates` (each `{member, distance, match_source}`) +
  `total_candidates`.
- Candidates capped at `CANDIDATE_CAP`, tightest kept, `total_candidates` reports the uncapped
  count.
- `next_cursor` round-trips; `has_next` on the boundary; blank/malformed cursor → from start.
- OCR thresholds passed from `settings.OCR.*`.

### Backend integration — `tests/integration/test_ingestion_tier_b_review.py` (new)
- A batch: 5 pending images, a mix of pending-pending and pending-active tier-B pairs at
  varied distances. `list_tier_b_review(batch_id=…)` returns one item per pending image with
  an open pair, ordered by min candidate distance, each with the right candidates.
- Reject the subject of one item via `service.resolve("tier_b", …)`; re-fetch → that item is
  gone, and any *other* item that had it as a candidate no longer lists it (its pair is
  excluded — rejected side), and if that was its only candidate the other item is gone too.
- `keep` a subject → same drop, via `mark_reviewed` settling the pairs.
- `get_blocked_pending_ids` after: matches the still-open set.
- An `active` candidate is never itself a subject (not pending).

### Frontend
- `TierBReviewCard.test.tsx`: subject always has Keep/Reject; pending candidate has them;
  active candidate doesn't; "Show N more" with `total_candidates` > shown; distance/source
  line; `onDecide(image_id, d)` for subject and for a pending candidate.
- `IngestionReviewPage.test.tsx`: `stage: tier_b_review` → calls
  `getIngestionTierBReview`, renders `TierBReviewCard`s, not `ClusterRow`s; a decision on a
  subject + submit removes that card; a decision on an in-batch candidate shown on two cards
  reflects on both; "Submit all" aggregates across cards; reload when the list empties.
- `tsc -b`, `eslint src/` (0 warnings), `vitest run`; regenerated-type `git diff` check.

## Rollout / risk

- Additive: new endpoint, new components, a tier branch in one page. Tier A untouched.
- The cluster tier-B endpoint stays callable (no removal) — lowest-risk.
- Query A's ~3–5s cost is the known number; if it regresses under load, the partial index is
  the fast follow. The endpoint it replaces currently never returns, so this is strictly
  better.
- Once merged, the stopgap's `CLUSTER_MEMBER_CAP` code is dead for tier B (page no longer
  calls that endpoint for tier B) — leave it (harmless for tier A) or remove in this PR;
  either is fine, note the choice in the plan.
