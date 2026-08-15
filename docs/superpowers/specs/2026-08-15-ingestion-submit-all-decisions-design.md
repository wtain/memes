# Ingestion Review — Submit All Decisions — Design

Status: draft

**Date:** 2026-08-15.

Adds a "Submit all decisions" button to the ingestion review page, so a reviewer working through
many clusters doesn't have to click each cluster's own "Submit decisions" button individually.

---

## Motivation

`IngestionReviewPage` (`Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`) requires a
separate click on each cluster's "Submit decisions" button, even after marking Keep/Reject on
many clusters in one sitting. With dozens of clusters in a tier, this is a lot of repeated
clicking for no real benefit — there's no reason each cluster's decisions need to be sent in a
separate request.

## Design

### Backend

No changes. `POST /api/ingestion/clusters/{tier}/resolve` (`Backend/app/api/ingestion.py`,
`IngestionService.resolve`) already accepts an arbitrary list of `{image_id, decision}` pairs with
no per-cluster scoping or validation — "submit all" is just a matter of collecting more pairs into
one call to the same endpoint the per-cluster button already uses.

### Frontend

In `IngestionReviewPage.tsx`:

- **Collection scope**: for each currently-loaded cluster, include only the members that have an
  actual decision in local `decisions` state (`Keep`/`Reject` clicked) — a cluster with zero
  decided members contributes nothing to the batch; a cluster with some decided and some undecided
  members contributes only the decided ones. This exactly mirrors what clicking that cluster's own
  "Submit decisions" button does today, just batched across every cluster at once. A cluster is
  only counted toward the button's cluster-count display if it has at least one decided member —
  i.e., exactly when its own per-cluster submit button would currently be enabled.
- **Button placement**: top of the page, directly after `StatusBanner`, before the cluster list.
  Only rendered when `tier` is set and at least one cluster has a decided member.
- **Button label**: shows a live count, e.g. `Submit all decisions (3 clusters, 7 images)`, so the
  reviewer knows the scope before committing.
- **Confirmation**: two-click confirm, matching `AdminBatchesPage`'s Run button exactly — first
  click relabels the button "Confirm?" and starts a 3-second timeout reverting it; a second click
  within that window submits. Reuses the same `CONFIRM_TIMEOUT_MS = 3000` convention.
- **Submission**: one call to `memesApi.resolveIngestionCluster(tier, allDecisions)`, followed by
  the existing `load()` refresh (same as per-cluster submit) — clusters that end up fully resolved
  naturally drop out of the reloaded list; no separate decision-state clearing needed, matching
  today's per-cluster behavior.
- **Concurrency guard**: while "submit all" is in flight, every per-cluster "Submit decisions"
  button is also disabled (not just the one matching the in-flight index), and vice versa — a
  per-cluster submit firing while "submit all" is in flight for the same images would race two
  overlapping requests against the same `image_id`s. `submitting` state is generalized from
  `number | null` to `number | "all" | null`; per-cluster buttons disable on
  `submitting === i || submitting === "all"`, the all-button disables on `submitting !== null`.
  Distinct per-cluster submissions are still allowed to overlap each other, matching existing
  behavior (this guard is specifically about "all" vs. any individual cluster, not individual
  clusters vs. each other).
- **Errors**: on failure, set the existing page-level `error` state (matching per-cluster submit's
  own error handling) rather than a separate error surface — a failed "submit all" is exactly as
  disruptive as a failed page load today, and the existing error UI is a full-page replacement.

### Testing

New test cases in `IngestionReviewPage.test.tsx`:
- Button is absent when no cluster has a decision.
- Button shows the correct cluster/image counts across multiple clusters with a mix of decided and
  undecided members.
- Clicking once shows "Confirm?"; clicking again calls `resolveIngestionCluster` with exactly the
  decided members' `{image_id, decision}` pairs, excluding undecided members and clusters with no
  decisions at all.
- Per-cluster submit buttons are disabled while "submit all" is in flight.
