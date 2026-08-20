# Persisted "Not Duplicate" Decisions — Design

Status: approved

## Motivation

The `/duplicates` review page (and the `/review-duplicates` agent tool) surfaces near-duplicate
clusters computed by `clusterize.py`'s union-find over `tmp_duplicates`. Some of these are false
positives — most commonly meme template reposts with different text, which are visually near-
identical (low CLIP embedding distance) but semantically distinct. Today there is no way to record
"these two images are not duplicates": `clusterize.py` fully rebuilds `tmp_clusters` from scratch
on every run using nothing but embedding distance, so a false positive a reviewer already looked at
and dismissed keeps coming back on every subsequent `rebuild_duplicates`/`clusterize` run, forever.

This follows directly from two pieces of recent work in this repo: `clusterize.py` now splits
oversized clusters and drops implicit singletons (recursive-threshold splitting), and separately
now scopes its union-find to `status = 'active'` images only (see the two most recent commits on
`batch/clusterize.py`). Neither of those addresses recurring false positives — they fix cluster
*shape*, not cluster *correctness*.

## Goals

- A durable record that a specific pair of images is confirmed **not** a duplicate.
- `clusterize.py` must never re-cluster a decided pair again, including across a
  `rebuild_duplicates --full` wipe-and-reprobe of `tmp_duplicates` (the same embeddings will
  rediscover the same candidate pair; the decision must still suppress it).
- A "Not duplicates" action on the review page, scoped to a whole cluster (the common case).
- Decisions must be reversible (undo).

## Non-goals (explicitly deferred)

- **Read-time filtering** of already-dismissed clusters in the `/duplicates` review query.
  `tmp_clusters` only stores final membership, not the edges that produced it, so correctly hiding
  a fully-dismissed cluster at read time requires comparing decided-pair count against `C(N, 2)`
  for that cluster's current members — a correlated-subquery addition to a query that's currently a
  flat join. Bounded (clusters are capped at 12 members by the existing splitting feature) but real
  added complexity, in a third place (batch filter + read-time filter + frontend) for a same-session
  polish problem, not the actual complaint (false positives recurring *across* review sessions,
  which the batch-time filter alone fully solves). Revisit if same-session staleness proves
  annoying in practice.
- **Per-pair (sub-cluster) dismissal UI.** Only whole-cluster dismissal is exposed in the UI for v1
  (see Data model — storage is still pair-based underneath, so this can be added later without a
  schema change).
- **Immediate/triggered re-clustering** on decision. No `clusterize.py` rerun is triggered by a
  dismiss action; the effect is visible the next time `clusterize` runs (already a manual-trigger
  operation via `/admin/batches`, unchanged by this spec).
- **Wiring the `/review-duplicates` agent tool's "variant — keep both" outcome** into this store.
  Natural follow-up once the store exists; not designed here.
- **Mixed clusters** (some members are true duplicates of each other, one is an unrelated false
  positive caught in the same cluster). Whole-cluster dismiss on a mixed cluster will also record
  the true-duplicate sub-pair(s) as "not duplicate," which is wrong for those pairs. Accepted risk:
  empirically (`general`, current data) only a handful of clusters ever exceed 3–4 members, and a
  reviewer sees all members before clicking. Not solved here — solving it precisely is exactly what
  reintroduces per-pair UI complexity this spec deliberately avoids.
- **Transitive reunification via a bridge node.** Confirmed empirically (see
  `tests/integration/test_clusterize.py::test_bridge_node_transitively_reunites_a_decided_pair`):
  a `duplicate_decisions` row only excludes the *specific* decided edge from
  `clusterize.py`'s union-find — it does not mean "these two images may never share a cluster."
  If image C later arrives and is a near-duplicate of *both* A and B (an already-decided pair),
  the undecided A–C and B–C edges transitively reunite A and B into one cluster on the next
  `clusterize` run, silently undoing the original decision. Accepted limitation, not fixed here:
  truly preventing this requires propagating a must-not-link constraint through the whole
  clustering pass (constrained clustering) — a materially larger feature than the plain per-edge
  filter this spec ships. Flagged for awareness; revisit only if it proves disruptive in practice.

## Data model

New table `duplicate_decisions` — **not** `tmp_`-prefixed, because it must survive
`rebuild_duplicates --full`'s `DELETE FROM tmp_duplicates` (the `tmp_` tables are explicitly
designed to be safely droppable derived caches; a human decision is durable source data and must
not live there).

```sql
CREATE TABLE duplicate_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_id1 UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    image_id2 UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (image_id1, image_id2)
);
CREATE INDEX ix_duplicate_decisions_image_id1 ON duplicate_decisions (image_id1);
CREATE INDEX ix_duplicate_decisions_image_id2 ON duplicate_decisions (image_id2);
```

- `image_id1`/`image_id2` are always stored `LEAST`/`GREATEST`-normalized, mirroring
  `tmp_duplicates`' own convention — a pair is represented once, and lookups/inserts never need to
  check both orderings.
- `ON DELETE CASCADE` matches every other per-image table in this schema — if an image is later
  hard-deleted, its decision rows disappear with it automatically, no separate cleanup needed.
- The durable unit is **the pair**, not "a cluster." A whole-cluster dismiss is a convenience that,
  at click time, looks up the cluster's current members and inserts all `C(N, 2)` pairs — there is
  no separate "cluster decision" record to keep in sync with clusters that get reshaped or split by
  a later `clusterize` run.
- Alembic migration adds the table; `Storage/models.py` gets a `DuplicateDecision` ORM model (single
  source of truth for models, per this repo's convention).

## `clusterize.py` change

`get_duplicate_pairs` gets one additional filter: exclude any `tmp_duplicates` row whose
`(image_id1, image_id2)` exists in `duplicate_decisions` — a `NOT EXISTS`/anti-join. Both tables
already store pairs `LEAST`/`GREATEST`-normalized, so this is a direct equality join, no
re-normalization needed at query time.

This is the single enforcement point. Once a pair is decided, no future `clusterize` run — however
many times `rebuild_duplicates` has repopulated `tmp_duplicates` in between, `--full` or
incremental — will ever reunite it, since the same embeddings will simply rediscover the same
candidate pair and it will be filtered again.

## Repository / service layer

New repository (home — backend-specific `Backend/app/repositories/` vs. global `repository/` —
decided at plan-writing time; likely backend-specific since it's driven entirely by the review API,
not by any batch script) with:

- `record_decision(image_id1, image_id2)` — normalizes to `LEAST`/`GREATEST`, `ON CONFLICT DO
  NOTHING` (idempotent, matches `tmp_duplicates`' own insert pattern).
- `record_decisions_bulk(pairs)` — one bulk insert, for the whole-cluster case.
- `delete_decisions(pairs)` — undo; deletes each given pair's row if present, idempotent (no error
  if already gone).
- `list_recent(limit, offset)` — for the audit-listing endpoint.

Service layer (`Backend/app/services/`, extending `ImageService` or a new
`DuplicateDecisionService`):

- `dismiss_cluster(cluster_id)` — looks up the cluster's **current** members from `tmp_clusters`
  (always re-read server-side at request time — a stale client-supplied member list could record
  decisions for images that already left the cluster in an intervening rebuild), generates all
  `C(N, 2)` pairs, calls `record_decisions_bulk`. Returns the recorded pairs — the frontend needs
  them verbatim to build the undo action.
- `undo_decisions(pairs)` — thin passthrough to `delete_decisions`, used by the undo toast.
- `list_decisions(limit, offset)` — thin passthrough, for the audit listing.

## API

New endpoints in `Backend/app/api/images.py`, alongside the existing `GET /api/images/duplicates`:

- `POST /api/images/duplicates/clusters/{cluster_id}/dismiss`
  - Response: `{"pairs": [{"image_id1": ..., "image_id2": ...}, ...]}` — the exact pairs recorded,
    so the frontend can hold them for undo without a second lookup.
  - 404 if `cluster_id` doesn't currently exist in `tmp_clusters` (stale UI state — e.g. an
    intervening `clusterize` run already cleared it).
- `POST /api/images/duplicates/pairs/undo-dismiss`
  - Body: `{"pairs": [{"image_id1": ..., "image_id2": ...}, ...]}`
  - Deletes exactly those decision rows if present.
- `GET /api/images/duplicates/decisions?limit=&offset=`
  - Minimal audit listing: recent decisions, newest first, with image ids/filenames/`decided_at` —
    enough to render two thumbnails and an Undo button. Undo here calls the same
    `pairs/undo-dismiss` endpoint with a single-pair list.

`backend_api.md` updated for all three, per this repo's "Adding a new endpoint" contract.

## Frontend

- `MemesDuplicatesList.tsx`: each cluster row gets a "Not duplicates" button.
  - On click: `POST .../clusters/{cluster_id}/dismiss`.
  - On success: remove the row from the component's own in-memory list (no refetch) and show a
    toast — "Marked N images as not duplicates · Undo" — holding the pairs the response returned.
  - Toast's Undo: `POST .../pairs/undo-dismiss` with the held pairs, then re-surface the row (exact
    mechanics — re-inserting into `Virtuoso`'s windowed state vs. just letting the next natural
    fetch pick it back up — decided at implementation time; either is acceptable).
  - No persistence of the optimistic removal across refresh/remount (no `localStorage`, etc.) — a
    fresh load reflects actual server truth, i.e. the row reappears, since read-time filtering is
    explicitly out of scope. This is intentional, not an oversight.
- A minimal decisions-audit view (a small section of an existing admin page, or a new lightweight
  route — left as an implementation-time call, not load-bearing for the core feature) rendering
  `GET .../decisions` with a per-row Undo button, for undoing decisions from past sessions where the
  original toast is long gone.

## Behavior / edge cases

- **New images joining a partially-dismissed group later**: unaffected. Decisions are pair-specific
  — a new image C forms fresh, undecided pairs with existing images and can freely cluster with
  them even if some *other* pair in that group was previously dismissed.
- **A dismissed pair whose image later gets flagged/removed** (`move_flagged` →
  `unregister_deleted_images`): `duplicate_decisions` rows cascade-delete automatically via the FK,
  same as every other per-image table — no separate cleanup job needed.
- **Concurrent dismiss + a manually-triggered `clusterize` run**: no special handling needed.
  `record_decisions_bulk` only writes to `duplicate_decisions`, never touches `tmp_clusters`, so
  there's no write conflict with `clusterize.py`'s delete+rebuild of `tmp_clusters`. Worst case, a
  decision recorded mid-run simply takes effect on the *next* `clusterize` run instead of the
  in-flight one.
- **Mixed clusters**: see Non-goals — accepted limitation.

## Testing

- Repository/service unit tests (mocked DB) for record/bulk-record/delete/list, following existing
  `Backend/tests` patterns.
- `clusterize.py`'s decision filter is DB-touching (a query change, not a pure function like
  `resolve_cluster`), so — consistent with how the rest of `_process()`'s DB glue already has no
  dedicated unit coverage — verify it via `tests/integration/`: a case (new file or added to
  whichever test already covers `rebuild_duplicates`/`clusterize`) that inserts a decided pair and
  asserts `clusterize.py` excludes it from the resulting `tmp_clusters`. Confirm exact scope at
  plan-writing time; this change doesn't touch shared normalization code so the "run the whole
  `tests/integration/` root" gotcha likely doesn't apply, but double-check.
- Frontend: `MemesDuplicatesList.test.tsx` gets a case for dismiss → toast → undo, alongside its
  existing windowing/scroll-jump coverage.

## Migration / rollout

1. Alembic migration: create `duplicate_decisions` + indexes.
2. Add `DuplicateDecision` to `Storage/models.py`.
3. `clusterize.py`: add the decision filter to `get_duplicate_pairs`.
4. Backend: repository, service, three new endpoints, `backend_api.md` update.
5. Frontend: dismiss button, optimistic removal, toast+undo, minimal decisions-audit view.
6. No data backfill needed — this only affects `clusterize` runs going forward.

## Open questions

- Exact home for the audit-listing UI (dedicated page vs. a section of an existing admin page) —
  left for implementation planning.
- Whether `record_decisions_bulk` needs a cap on `N`. Cluster size is already capped at
  `settings.CLUSTERING.SPLITTING.MAX_CLUSTER_SIZE` (12) by the existing splitting feature, so
  `C(12, 2) = 66` pairs is today's real-world ceiling — not large enough to need a separate cap, but
  noted here so it isn't silently forgotten if `MAX_CLUSTER_SIZE` is ever raised substantially.

## Out of scope (deferred follow-ups, not designed here)

- Read-time filtering of fully-dismissed clusters in the review query.
- Per-pair (sub-cluster) dismissal UI.
- Immediate/triggered re-clustering on decision.
- Wiring `/review-duplicates`'s agent-tool "variant" outcome into `duplicate_decisions`.
- Precise handling of mixed (true-duplicate + false-positive) clusters.
- Incorporating OCR text similarity into duplicate detection — see
  `docs/superpowers/specs/drafts/2026-08-19-ocr-assisted-deduplication-draft.md` (separate,
  unrelated mechanism: proactively improving detection quality, vs. this spec's reactive
  human-in-the-loop correction).
