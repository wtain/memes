# Duplicate-Cluster Partial Resolution (OCR-Assisted Splitting)

status: done
Plan: docs/superpowers/plans/2026-09-26-duplicate-cluster-partial-resolution.md
Originates from: `docs/superpowers/specs/drafts/2026-08-19-ocr-assisted-deduplication-draft.md` — Case 1 of
that draft (false positives: a big CLIP cluster that's really one visual template with many
different captions). Case 2 of the same draft (false negatives — genuine duplicates CLIP misses
but OCR text would catch) is **not** carried forward here; it was already implemented,
independently, by `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md` →
`docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md` →
`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md` before this brainstorm
happened to notice the overlap. Resolved via a 2026-09-26 brainstorming session; the
2026-09-04 notes already committed to the draft file are this session's starting point.

## Problem

`Backend/app/services/image_service.py`'s `dismiss_cluster` (the only lever the web `/duplicates`
page gives a reviewer) is all-or-nothing: it computes every pairwise combination across **all**
cluster members and bulk-writes them to `duplicate_decisions` as "not duplicate" in one call. For
a cluster that's genuinely one visual template with many different captions — the exact
false-positive class the CLIP-threshold tightening (2026-09-18) and the OCR-text-embedding work
were built around — dismissing the whole cluster is correct for most of it, but:

- If two members inside that cluster genuinely **are** the same caption posted twice, dismissing
  the whole cluster as "not duplicate" also permanently excludes that real pair from ever being
  re-confirmed (the anti-join in `clusterize.py` is unconditional once a pair is in
  `duplicate_decisions`).
- There is no way today to act on part of a cluster and leave the rest for later. A human either
  resolves all of it in one click or leaves the whole thing sitting in the queue indefinitely.

Separately, the CLI/agent tool (`/review-duplicates`, `tools/agent_duplicates.py`) already
surfaces per-member OCR text and pairwise distances for exactly this kind of judgment call, but
the web page shows neither — a reviewer using the web page has to open each image individually to
tell "same template, different joke" from "actual repost."

**Constraint carried in from this session's brainstorm:** `ImageExtras.flagged` (`Storage/models.py:426-434`)
is a bare boolean already used, unscoped, for unrelated purposes — a curator can flag an image
because it's a duplicate *or* because it doesn't belong in the collection at all, and there is no
way today to tell which. `MemeCard.tsx:75-84`'s existing per-card "Flagged" checkbox already
exercises this exact ambiguous path. Any new duplicate-review action that flags an image must not
make this worse.

## Goal

Let a reviewer select a **subset** of a cluster's members (not necessarily all of them) and apply
one of two actions to just that subset, leaving the rest of the cluster untouched for a later pass:

1. **"Not duplicates"** — the selected subset's pairwise combinations only, written to
   `duplicate_decisions` exactly as `dismiss_cluster` does today, just scoped down.
2. **"Duplicates — keep best"** — one selected member is marked the keeper; every other selected
   member is flagged (existing `mark_flagged` mechanism), tagged with a reason so this stays
   distinguishable from an unrelated manual flag.

OCR text (already fetched — `Meme.text`, already rendered via `MemeCard.tsx`'s existing
click-to-reveal "OCR" button) needs no change. What's added is a lightweight visual aid — each
card gets a similarity indicator, computed from the same lemma-overlap-coefficient metric the
OCR-text duplicate-matching corroboration gate already validated on real production data
(`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`) — so a reviewer can see at
a glance which currently-selected members' captions actually match each other, without opening
every card.

## Non-goals

- **No change to automatic detection.** `clusterize.py`, `rebuild_duplicates.py`,
  `ingest_find_duplicates.py` are untouched. This is presentation/review tooling only — decided
  in this session's brainstorm, reaffirming the pattern already established by
  `duplicate_decisions`, `agent_duplicates.py`, and ingestion Tier A/B review.
- **No unification with the CLI/agent tool.** `/review-duplicates` keeps its own flow. It happens
  to already use the same `mark_flagged` primitive this spec extends, so the two stay conceptually
  aligned without sharing UI.
- **No new similarity algorithm.** Reuses the existing overlap-coefficient formula and
  `ocr_lemmas` table as-is. Computed on demand, presentation-only — never written to any table,
  never gates an action.
- **No resolution of a flagged loser's continued appearance in `/duplicates`.** A flagged image
  keeps showing up in future cluster listings until `move_flagged` (existing, manually-triggered
  maintenance script) next runs and physically removes it — identical to the CLI/agent flow's
  existing behavior today, not something this spec changes.
- **No server-side reason-gated undo.** The frontend already knows exactly which image ids it just
  flagged for this action (tracked client-side, same pattern `MemesDuplicatesList.tsx`'s existing
  `dismissedClusters` map already uses for the current full-cluster Undo) — its row-scoped Undo
  calls `unmark_flagged` on exactly those known ids, no server-side check needed.
  `flagged_reason` exists purely for audit/provenance, not as a runtime gate. Accepted, narrow
  edge case: if the same image is independently, manually re-flagged for an unrelated reason in the
  window between this action and an Undo click, Undo would still clear that flag too — the same
  class of race the existing pair-undo already doesn't guard against, not something this spec
  introduces.
- **No change to the existing manual per-card "Flagged" checkbox.** It keeps behaving exactly as
  today — general-purpose, no reason recorded (`reason=NULL`, same as every flag set before this
  spec). Only the new bulk "keep best" action passes a reason.

## Design

### §1. Schema

One nullable column, purely additive — existing rows/flags are unaffected:

```python
"""Add flagged_reason to image_extras

Revision ID: <generated>
Revises: <current head>
Create Date: 2026-09-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = '<generated>'
down_revision = '<current head>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('image_extras', sa.Column('flagged_reason', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('image_extras', 'flagged_reason')
```

`Storage/models.py`'s `ImageExtras` gains `flagged_reason = Column(String(50), nullable=True)`
next to the existing `flagged`/`remarks` columns. This feature is the only writer of the literal
value `"duplicate_review"`; the column stays a plain string (matching this codebase's existing
`ImageClassification.classifier`/`BatchRun.kind`-style "just a string" convention), not a DB enum
— nothing here needs the extra ceremony of a constrained type for one value today.

### §2. Repository

`Backend/app/repositories/image_repository.py`'s `set_flagged` gains an optional `reason` param,
threaded through the same upsert:

```python
async def set_flagged(self, image_id, is_flagged, reason: str | None = None):
    stmt = (
        insert(ImageExtras)
        .values(image_id=image_id, flagged=is_flagged, flagged_reason=reason)
        .on_conflict_do_update(
            index_elements=["image_id"],
            set_={"flagged": is_flagged, "flagged_reason": reason},
        )
    )
    await self.session.execute(stmt)
```

Unflagging (`is_flagged=False`) always passes `reason=None` too — once an image is unflagged,
whatever reason it was flagged for is stale and should clear with it, matching "the reason is only
meaningful while flagged" from the Goal section.

New method, `repository/ocr_lemmas.py` (the global repository, not `Backend/app/repositories/` —
matches this codebase's existing split: OCR-lemma logic already lives in the global layer,
consumed by both batch scripts and the Backend), for the similarity-badge computation:

```python
async def get_pairwise_overlap_coefficients(self, image_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    """Overlap coefficient (shared_lemmas / min(lemma_count_1, lemma_count_2)) for every pair
    within image_ids -- same formula batch/rebuild_duplicates.py's corroboration gate uses
    (_OCR_LEMMA_OVERLAP_CHECK), computed directly rather than reused verbatim since that SQL
    fragment is correlated against a specific LATERAL-join probe/nn pair, not a free list of ids.
    Presentation-only: never written anywhere. Pairs where either side has zero ocr_lemmas rows
    are simply absent from the result (nothing to compare), not scored 0.0 -- the caller renders
    "no OCR text" for those, not a false "definitely different" signal."""
    if len(image_ids) < 2:
        return {}
    rows = await self.session.execute(
        select(OCRLemma.image_id, OCRLemma.lemma).where(OCRLemma.image_id.in_(image_ids))
    )
    lemmas_by_image: dict[uuid.UUID, set[str]] = {}
    for image_id, lemma in rows:
        lemmas_by_image.setdefault(image_id, set()).add(lemma)

    result: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    ids_with_lemmas = [i for i in image_ids if i in lemmas_by_image]
    for i, a in enumerate(ids_with_lemmas):
        for b in ids_with_lemmas[i + 1:]:
            shared = len(lemmas_by_image[a] & lemmas_by_image[b])
            denom = min(len(lemmas_by_image[a]), len(lemmas_by_image[b]))
            result[(min(a, b), max(a, b))] = shared / denom if denom else 0.0
    return result
```

In-process (Python `set` intersection) rather than a SQL self-join, deliberately: review-page
cluster sizes are small (`CLUSTER_MEMBER_CAP`-bounded, per `Backend/app/services/ingestion_service.py`'s
existing constant for the same "don't render an unbounded cluster" concern), so pulling every
member's lemma set in one query and diffing in memory is simpler than a correlated SQL query and
costs nothing measurable at that size.

### §3. Service

`Backend/app/services/image_service.py`:

```python
async def dismiss_cluster(self, cluster_id: int, member_ids: list[uuid.UUID] | None = None) -> list[tuple[uuid.UUID, uuid.UUID]]:
    all_member_ids = await self.repo.get_cluster_member_ids(cluster_id)
    if not all_member_ids:
        raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")

    if member_ids is None:
        target_ids = all_member_ids
    else:
        invalid = set(member_ids) - set(all_member_ids)
        if invalid:
            raise HTTPException(status_code=400, detail=f"Not members of cluster {cluster_id}: {invalid}")
        if len(member_ids) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 members to dismiss a subset")
        target_ids = member_ids

    pairs = [
        (target_ids[i], target_ids[j])
        for i in range(len(target_ids))
        for j in range(i + 1, len(target_ids))
    ]
    await self.decision_repo.record_decisions_bulk(pairs)
    return pairs


async def mark_flagged(self, image_id, reason: str | None = None):
    await self.repo.set_flagged(image_id, True, reason)


async def unmark_flagged(self, image_id):
    await self.repo.set_flagged(image_id, False)


async def get_cluster_overlap_coefficients(self, cluster_id: int) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    member_ids = await self.repo.get_cluster_member_ids(cluster_id)
    return await self.ocr_lemmas_repo.get_pairwise_overlap_coefficients(member_ids)
```

(`ImageService` gains an `ocr_lemmas_repo: OCRLemmasRepository` constructor dependency, wired
through `get_image_service`'s existing FastAPI dependency-injection function alongside its current
`ImageRepository`/`DuplicateDecisionsRepository` params.)

`member_ids=None` reproduces today's exact full-cluster behavior byte-for-byte — existing callers
(none known outside this router today, but this keeps the method's contract backward-compatible on
principle) are unaffected. `mark_flagged`'s `reason` defaults to `None`, so the existing per-card
manual "Flagged" checkbox (which will keep calling this with no reason argument) is unaffected.

No service-layer method for "keep best" specifically — the frontend calls `mark_flagged` once per
non-keeper selected member, passing `reason="duplicate_review"`. No new aggregate action is needed
service-side for this.

### §4. API

`Backend/app/api/images.py`:

- `PUT /meme/{image_id}/mark_flagged` gains an optional query param, `reason: str | None = Query(None, max_length=50)`,
  passed straight through to `service.mark_flagged(image_id, reason)`. `unmark_flagged` is
  unchanged (no reason to pass on unflag, per §2).
- `POST /duplicates/clusters/{cluster_id}/dismiss` gains an optional request body:

```python
class DuplicateDismissRequestModel(BaseModel):
    member_ids: list[str] | None = None


@router.post("/duplicates/clusters/{cluster_id}/dismiss", response_model=DuplicateDismissResponseModel)
async def dismiss_duplicate_cluster(
    cluster_id: int,
    response: Response,
    body: DuplicateDismissRequestModel = DuplicateDismissRequestModel(),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    member_ids = [uuid.UUID(i) for i in body.member_ids] if body.member_ids else None
    pairs = await service.dismiss_cluster(cluster_id, member_ids)
    return DuplicateDismissResponseModel(
        pairs=[DuplicatePairModel(image_id1=str(a), image_id2=str(b)) for a, b in pairs]
    )
```

An empty/omitted body (`{}` or no body at all — FastAPI treats a `BaseModel`-typed body param with
a default instance as optional) reproduces today's full-cluster dismiss exactly, so this is
backward-compatible for any existing caller.

New endpoint for the similarity badge, read-only, no side effects:

```python
@router.get("/duplicates/clusters/{cluster_id}/similarity", response_model=ClusterSimilarityResponseModel)
async def get_cluster_similarity(
    cluster_id: int,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = await service.get_cluster_overlap_coefficients(cluster_id)
    return ClusterSimilarityResponseModel(
        pairs=[
            ClusterSimilarityPairModel(image_id1=str(a), image_id2=str(b), overlap=coeff)
            for (a, b), coeff in pairs.items()
        ]
    )
```

Deliberately a **separate**, lazily-fetched endpoint rather than a field added to every `Meme` in
the main paginated `/duplicates` response: that response is virtualized and windowed
(`MemesDuplicatesList.tsx`'s extensive scroll-position-stability machinery, see its own comments
on `rowFirstItemIndex`/`increaseViewportBy`) and has already needed careful performance tuning —
adding an O(members²) computed field to every row of that response, most of which a reviewer never
even scrolls to, is the wrong place for this. The frontend fetches this endpoint once per row,
lazily, the first time a row's cards render (or the first time a reviewer selects a second member
in that row — either trigger point is an implementation-plan-level call, not a design-level one).

`shared/schemas/` additions: `duplicatedismissrequest.schema.json` (`member_ids: string[] | null`),
`clustersimilarityresponse.schema.json` + `clustersimilaritypair.schema.json` (mirrors
`duplicatepair.schema.json`'s existing two-id shape, plus `overlap: number`). Regenerate all three
type trees per CLAUDE.md's Type generation section, as with every prior schema change this
session.

### §5. Frontend

`MemesDuplicatesList.tsx` gains, per row (keyed by `clusterId`, same pattern as the existing
`dismissedClusters` map):

- `selectedMembers: Map<number, Set<string>>` — which member ids are currently checked in that
  row.
- `keeperByCluster: Map<number, string | undefined>` — which selected member (if any) is marked
  the keeper.
- `similarityByCluster: Map<number, Map<string, number>>` — fetched lazily (via the new endpoint)
  the first time a row needs it; keyed by a stable pair-key for O(1) lookup when rendering each
  card's badge.

Two buttons replace today's single "Not duplicates": both disabled until ≥2 members are selected;
"Duplicates — keep best" additionally requires exactly one keeper marked among the selection. A
**"Select all"** toggle stays available per row so today's common case — the whole cluster is
obviously not duplicates — remains a single click, same cost as today; partial selection is
additive, not a regression for that case.

After either action, only the *acted-on* members are removed from that row's visible grid (the
row's `dismissedClusters`-style per-row Undo strip only replaces the whole row once every member
has been resolved — a partially-resolved row keeps rendering its remaining, not-yet-decided
members normally, still selectable for a follow-up action).

`MemeCard.tsx` gains: a `selectable` prop (only true on the duplicates page, default `false`
elsewhere — this card is reused across several pages), a `selected`/`onToggleSelect` pair
(a new checkbox, distinct from the existing "Flagged" checkbox — selecting a card for a bulk
action never flags it by itself), an `isKeeper`/`onSetKeeper` pair (only meaningful while
`selected`), and an optional `similarity?: number` prop rendered as a small badge/border-color hint
when present. The existing OCR click-to-reveal button and the existing manual "Flagged" checkbox
are untouched.

## Testing

- **Repository** (`tests/integration/test_backend_image_repository.py`, real DB — `ImageRepository`
  is already tested this way, matching `DiagnosticsRepository`'s own convention confirmed earlier
  this session): `set_flagged` with a `reason` persists it; without one, `flagged_reason`
  stays/becomes `NULL`; unflagging always clears `flagged_reason` regardless of what it was.
- **Repository** (existing `tests/integration/` coverage for `repository/ocr_lemmas.py`, or a new
  file alongside it — whichever this repo's current layout already uses for that module):
  `get_pairwise_overlap_coefficients` — known lemma sets produce the expected coefficient; an
  image with zero `ocr_lemmas` rows is absent from the result, not scored 0.0; a single-image or
  empty input returns `{}`.
- **Service** (`Backend/tests/`, mocked): `dismiss_cluster` — `member_ids=None` reproduces the
  existing full-cluster pairs exactly (regression test for the pre-existing behavior); a valid
  subset produces only that subset's pairwise combinations; a `member_ids` value containing an id
  not in the cluster raises 400; a single-element `member_ids` raises 400 (need ≥2 to form a
  pair); a 404 for an unknown `cluster_id` is unchanged from today.
- **API** (`Backend/tests/test_images_endpoints.py` or wherever `/duplicates` is currently tested):
  `POST .../dismiss` with no body / empty body matches today's existing test cases exactly (backward-compat
  regression); with a `member_ids` body, only those pairs land in the response; `PUT
  .../mark_flagged?reason=duplicate_review` persists the reason, omitting the param leaves it
  `NULL`; `GET .../similarity` returns the expected pairs for a fixture cluster.
- **Frontend** (`vitest`, extending `MemesDuplicatesList.test.tsx`'s existing suite): selecting
  members enables the two buttons only once the right preconditions are met (≥2 selected;
  exactly one keeper for "keep best"); "Select all" checks every member in a row; a partial action
  removes only the acted-on members from the row's grid, not the whole row; the row only shows the
  "Marked as not duplicates"-style resolved strip once every member is gone.

## Rollout

No live-database write beyond the one additive migration (nullable column, no backfill, no
existing-row risk — the same low-risk profile `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`'s
brand-new-empty-table migration had). Ship migration + code together; no phased rollout needed since
nothing here changes automatic detection or existing decision data. Manual smoke-test on one
environment's `/duplicates` page (select a subset, dismiss, refresh, confirm only that subset is
gone from the cluster) before calling this done, per this repo's "Before committing backend
changes" convention.

## Rollout outcome

Migration `29a039fa4457` (add `flagged_reason` to `image_extras`) applied to all three live
environments — `metal`, `general`, `it` — each verified at the new revision via
`DATABASE_URL_READONLY`, with `/api/diagnostics/health` confirmed healthy on each afterward.
Read-only smoke test against `metal`'s live data (a real cluster and the new
`GET .../similarity` endpoint) confirmed correct before the migration; not re-run after, since the
migration is additive and orthogonal to what that endpoint reads. The write-side manual
smoke-test this section calls for (actually dismissing/flagging a real cluster through the UI)
has not been done — those are permanent decisions against real production data, deferred pending
an explicit go-ahead from whoever is at the keyboard for that environment.
