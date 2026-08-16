# Ingestion Review — Stale Decision Guard — Design

Status: draft
Originates from: docs/superpowers/specs/2026-08-15-ingestion-submit-all-decisions-design.md

**Date:** 2026-08-16.

Closes a gap where a decision (Keep/Reject) set on an image but never submitted can resurface
and get applied later, in a review context the user never actually saw or intended.

---

## Motivation

`IngestionReviewPage` (`Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`) keeps local
`decisions` state keyed by `image_id`. It is populated when a reviewer clicks Keep/Reject on a
`MemberTile`, and today is only cleared when that image's decision is successfully submitted
(`submitCluster`/`submitAll` delete submitted ids from `decisions` on success — added during the
submit-all-decisions review, see the originating spec above).

**Confirmed concrete failure:** an image can be a member of a tier_a cluster and, independently,
of a different tier_b cluster — union-find groups are rebuilt per tier from that tier's own
distance band, so the same still-`pending` image can appear in two unrelated clusters across the
two tiers. If a reviewer marks that image (say, Reject) while reviewing tier_a but never clicks
submit, and the run's stage later advances to tier_b review (possibly done by a different
reviewer, or the same one returning after a break), the page reloads showing tier_b clusters. The
abandoned tier_a decision is still sitting in `decisions`, keyed by that same `image_id`, and
nothing clears it. The next `submitCluster`/`submitAll` call that happens to touch a tier_b
cluster containing that image will silently include the stale Reject — rejecting the image (DB
status flip + physical file move) in the tier_b context, even though the user's only actual click
happened during an abandoned tier_a session and may no longer reflect their intent.

The backend has no independent check either: `IngestionRepository.reject_image()`
(`Backend/app/repositories/ingestion_repository.py:128`) unconditionally sets
`image.status = "rejected"` on whatever row matches `image_id`, regardless of the image's current
status. A stale decision reaching the API — whether through this exact scenario or a future
frontend bug, or a direct API call — is applied without question.

`MemberTile` only renders Keep/Reject buttons when `member.status === "pending"`
(`IngestionReviewPage.tsx:58,79`), so a decision can only ever be *set* against a pending image —
but nothing re-checks that the image is *still* pending at *submit* time, which is the actual gap.

## Design

Three independent layers, ordered from "closes the concrete scenario outright" to "defends
anything the first two miss":

### 1. Frontend: clear `decisions` on tier change

Add an effect keyed on `tier` (`Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`) that
resets `decisions` to `{}` whenever `tier` changes value (including the initial `null → tier_a`/
`tier_b` transition being a no-op, since `decisions` already starts empty). A tier change always
means the review queue has been rebuilt from scratch server-side, so starting the local decision
state over is the correct behavior, not a data-loss risk — nothing the user "submitted" is lost,
only never-submitted, already-abandoned clicks.

This alone closes the exact scenario above: once the stage advances and `tier` flips from
`tier_a` to `tier_b`, the stale Reject is gone before the tier_b clusters even render.

### 2. Frontend: filter collection to currently-`pending` members

`submitCluster`'s per-cluster collection loop and the page-level `allPendingDecisions`/
`clustersWithPendingCount` computation (`IngestionReviewPage.tsx:145-181`) both currently collect
any `decisions[member.image_id]` that is set, without checking `member.status`. Add a
`member.status === "pending"` condition to both collection sites — a decision surviving against a
member whose status has since changed (e.g. resolved by a concurrent reviewer in another browser
tab, within the *same* tier, so layer 1 doesn't catch it) is dropped rather than submitted.

This mirrors the existing rendering guard (`MemberTile` only shows the buttons when pending) at
the point where decisions are actually acted on, closing the "decision was legitimate when set,
stale by the time it's submitted" case that a same-tier concurrent reviewer can create.

### 3. Backend: `reject_image()` requires `status == "pending"`

`IngestionRepository.reject_image()` gains the same precondition `undo_reject()` already has in
the opposite direction — it becomes a no-op returning `None` (identical to "image doesn't exist")
when the current status isn't `"pending"`:

```python
async def reject_image(self, image_id) -> Optional[str]:
    """Flip status to rejected. Returns the filename (for the caller to move the file), or
    None if the image doesn't exist or isn't currently pending (already resolved elsewhere --
    see docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md)."""
    result = await self.session.execute(
        select(Image).where(Image.id == image_id, Image.status == "pending")
    )
    image = result.scalar_one_or_none()
    if image is None:
        return None
    image.status = "rejected"
    await self.session.flush()
    return image.filename
```

`IngestionService.resolve()` already treats a `None` return from `reject_image()` as "nothing to
do" (skips appending to `rejected`) — no service-layer change needed for this guard to take
effect.

`mark_reviewed()` (the Keep path) is deliberately left unguarded: it only stamps a
`tmp_duplicates` row's `tier_a_reviewed_at`/`tier_b_reviewed_at` column and never touches
`Image.status`. Running it against an image that's no longer pending is harmless — the row it
touches is already excluded from future `get_tier_candidate_rows()` queries by the existing
`img1.status != "rejected"` / `img2.status != "rejected"` filters, and marking a reviewed
timestamp on an active image's settled candidate pair has no observable effect. Adding a guard
here would be defense against a scenario that can't actually cause harm — skipped per YAGNI.

This layer is what protects against paths that don't go through the frontend at all (a future
caller, a direct API request, or any bug in layers 1-2) — the database is the last word on whether
a reject is still legitimate.

### Non-goals

- No change to `mark_reviewed()`/the Keep path (see above).
- No user-facing message when a stale decision is silently dropped (layers 1-2) or a reject is
  silently skipped by the backend (layer 3) — these are edge cases (abandoned reviews, race
  conditions), not routine user actions worth interrupting the flow to explain. If this proves
  confusing in practice, surfacing a count in the existing `error`/status area is a cheap
  follow-up, not part of this design.
- This spec does not address `IngestionService.resolve()`'s partial-failure behavior (a decision
  succeeding at the DB layer while its associated file move fails, or one failing decision
  aborting an entire batch) — that is a separate, unrelated failure mode covered by
  `docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md`.

## Testing

**Frontend (`IngestionReviewPage.test.tsx`):**
- Setting a decision, then changing the mocked run status so `tier` flips (tier_a → tier_b)
  between two `load()` calls, clears `decisions` — a subsequent `submitAll` on the new tier's
  clusters sends none of the pre-flip decisions.
- A decision set on a member, followed by a reload where that same member's `status` is no
  longer `"pending"` (simulating a concurrent reviewer), is excluded from both the
  `clustersWithPendingCount`/button-count display and the payload sent by `submitCluster`/
  `submitAll`.

**Backend (`Backend/tests/` — wherever `IngestionRepository`/`IngestionService` are currently
covered):**
- `reject_image()` on an image whose status is `"active"` (or `"rejected"`) returns `None` and
  does not modify `image.status`.
- `reject_image()` on a `"pending"` image still behaves exactly as before (returns the filename,
  sets status to `"rejected"`).
- `IngestionService.resolve()` with a reject decision targeting a non-pending image does not
  call `image_store.move_to_rejected()` and does not include that image in the response's
  `rejected` list.
