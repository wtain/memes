# Skip Already-Reviewed Duplicate Clusters — Design

Status: done
Plan: docs/superpowers/plans/2026-08-01-duplicate-cluster-skip-if-flagged.md

**Date:** 2026-08-01.

`batch/detect_file_duplicates.py` should not flag anything in a duplicate cluster if any member
of that cluster is already flagged — treat that as a signal a human already reviewed the cluster.
Requires a new bulk flag-lookup method on `ImageExtrasRepository`, which is currently write-only
(`set_flagged` exists; nothing reads flags at all, bulk or single).

---

## Motivation

`detect_file_duplicates.py` currently flags every non-keeper in every duplicate cluster,
unconditionally, on every run. If an operator has already reviewed a cluster — e.g. manually
un-flagged one of its members via the UI because it wasn't actually a duplicate they wanted
removed, or flagged a different member for a different reason — a later run of this script
re-flags the rest of the cluster anyway, ignoring that prior human decision.

### Known Limitation: Incomplete Cluster Reviews

This feature protects only against clusters where some member remains flagged after human
review. If an operator un-flags a cluster member (setting it back to `flagged=False` from
`True`), or un-flags every member of a larger cluster, the next run of `detect_file_duplicates`
will re-flag them, since the check is `any(currently flagged=True)` rather than "any member's
flag was ever modified by a human." In the common 2-member cluster case, un-flagging the
previously-flagged member means both members are now `False`, triggering a re-flag on the next
run. This is a known, accepted trade-off: a "row exists at all" signal was considered as an
alternative (treating any `image_extras` row, regardless of `flagged` value, as a sign of human
attention), but rejected because `detect_file_duplicates.py` is itself the only other writer of
`image_extras` rows in the application (besides the UI). Using "row exists" would make the skip
signal near-permanent for any cluster this script has ever auto-flagged, not just genuinely
human-reviewed ones — a bigger design problem than the incompleteness this feature is addressing
today. A future refinement could track "last modified by" per row and check that condition
separately, but that's a separate decision.

## Scope

**In scope:** `ImageExtrasRepository.get_flags_bulk()`, and `detect_file_duplicates.py`'s
per-cluster decision to skip flagging entirely when any cluster member is already flagged.

**Out of scope:** any other behavior change to `detect_file_duplicates.py` (hashing, clustering,
content verification, the "keep oldest" rule all stay as-is); no CLI flag/opt-out — this becomes
the unconditional default, matching how the behavior was described; no broader test coverage for
parts of `detect_file_duplicates.py` this change doesn't touch (the script currently has zero test
coverage of any kind — this plan adds targeted coverage for the new logic only, not a full-script
test suite).

## Design

### `ImageExtrasRepository.get_flags_bulk()`

```python
async def get_flags_bulk(self, image_ids: list) -> dict:
    """Bulk-fetch flagged status for a set of image_ids in one query. Every id in
    image_ids is guaranteed a key in the result -- an id with no image_extras row at
    all (never flagged/unflagged) maps to False, matching get_is_flagged's existing
    single-row convention of treating "no row" as "not flagged"."""
    result = await self.session.execute(
        select(ImageExtras.image_id, ImageExtras.flagged)
        .where(ImageExtras.image_id.in_(image_ids))
    )
    flags = {image_id: bool(flagged) for image_id, flagged in result.all()}
    return {image_id: flags.get(image_id, False) for image_id in image_ids}
```

One query regardless of how many ids are passed — no per-image or per-cluster round trip. Not
chunked for extremely large id lists; the caller's usage (all members across all duplicate
clusters found in one run) is expected to be small relative to Postgres's `IN`-clause practical
limits, and this is an internal batch tool, not a hot path — chunking would be premature for the
actual usage pattern.

### `detect_file_duplicates.py`

A new pure helper, easy to unit test without a DB or filesystem:

```python
def cluster_already_handled(cluster: list, flags: dict) -> bool:
    """True if any member of this cluster is already flagged -- treat the whole
    cluster as already reviewed by a human, and skip flagging anything else in it."""
    return any(flags.get(mid, False) for mid in cluster)
```

Using a general-purpose flag (`ImageExtras.flagged`) rather than a duplicate-specific marker is
appropriate here because `flagged` is transient and self-clearing: `move_flagged.py` chains into
`unregister_deleted_images`, which physically removes flagged images and cascade-deletes their
`image_extras` row shortly after, so a stale skip signal doesn't persist indefinitely — it's
bounded by the operator's `move_flagged` cadence rather than permanent.

Wiring into `main()`'s Phase 4: before the per-cluster loop, collect every image id across every
cluster with 2+ members (the same clusters the loop already iterates), call `get_flags_bulk` once
for that combined set. Inside the loop, check `cluster_already_handled(cluster, flags)` **before**
the `files_are_identical` byte-comparison (cheaper, and skips that work too for a
already-handled cluster) — if true, print a skip message, increment a new
`clusters.skipped_already_flagged` metric, and `continue` without touching `files_are_identical`,
the keeper/duplicate split, or `set_flagged` at all for that cluster.

The check considers the **whole cluster** (including the would-be "keeper", not just the
would-be-flagged duplicates) — matching "if anything is flagged within cluster" literally, since a
flagged keeper is just as much a signal of prior human attention as a flagged duplicate.

### Testing

`tests/integration/test_image_extras_repository.py` (new — first test file for this repository;
needs a real DB, matching this project's convention for repository-layer code):

- A mix of flagged, explicitly-unflagged, and never-touched (no row) image ids in one
  `get_flags_bulk` call returns the correct `True`/`False`/`False` mapping respectively, with every
  requested id present as a key.
- An empty `image_ids` list returns an empty dict without erroring.

`batch/tests/test_detect_file_duplicates.py` (new — first test file for this script; matches this
package's convention of testing extracted pure functions without touching the DB/filesystem
`main()` orchestrates):

- A cluster with no flagged members: `cluster_already_handled` returns `False`.
- A cluster where a non-keeper member is flagged: returns `True`.
- A cluster where the (would-be) keeper itself is flagged: returns `True` — confirms the check
  isn't scoped to only the duplicates.
- An id present in `cluster` but absent from `flags` (defensive — shouldn't happen given
  `get_flags_bulk`'s guarantee that every requested id gets a key, but the helper shouldn't crash
  if it does): treated as not-flagged via `.get(mid, False)`.

## Rollout

1. Add `ImageExtrasRepository.get_flags_bulk()` + its integration test.
2. Add `cluster_already_handled()` to `detect_file_duplicates.py`, wire it into `main()`'s Phase 4
   loop (bulk-fetch flags once, skip-check before `files_are_identical`), add its unit tests.
