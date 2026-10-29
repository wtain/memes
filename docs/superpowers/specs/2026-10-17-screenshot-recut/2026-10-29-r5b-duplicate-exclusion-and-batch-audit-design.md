# R5b: Duplicate exclusion and batch audit - design

Status: done
Plan: docs/superpowers/plans/2026-10-29-r5b-duplicate-exclusion-and-batch-audit.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R5, split into R5a and R5b)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r5a-approve-reject-lifecycle-design.md
Follow-ups: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md

R5a approves a proposal by activating the cut and flagging the source; the source stays `active` until `move_flagged` and
`unregister_deleted_images` have run. In that window the duplicate jobs could pair the source with its own cut and put the pair
in front of the owner on `/duplicates`. R5b closes the window (second half of FR-11) and records the audit of the existing
batches that OQ-7 asked for, each answer pinned by a test.
Requirements covered: FR-11 (source/cut pairs), OQ-7.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | The exclusion lives in `find_duplicates()` (`batch/rebuild_duplicates.py`) | `rebuild_duplicates` and `ingest_find_duplicates` (which imports it) insert every candidate pair through that one function, in all three probes (general CLIP, safety net, OCR text). One clause covers both jobs and every probe |
| 2 | The clause sits inside the lateral subquery, before `LIMIT :k` | A source and its cut are near-identical, so the cut is the source's nearest neighbour. Filtering after the limit would spend one of the K slots on a pair that is then discarded |
| 3 | Every `image_cuts` pair that still has both ids is excluded, in any state | `proposed` and `rejected` cuts are `cut_proposed`, which no probed corpus contains, so a state filter would add nothing; `approved` is the case that matters. Once the source row is deleted its id is NULL and the clause stops matching, which is correct |
| 4 | No change to `ingest_hash_dedup` and no change to `move_flagged` | The audit found both correct for cuts (below); R5b pins that with tests rather than code |
| 5 | No clean-up of `tmp_duplicates` rows that exist already | None can: cuts only enter an `active` corpus on approve, and R5a has not run on a live database |

## The exclusion

Added to the `WHERE` of the lateral subquery in `find_duplicates()`, next to `AND ({corpus_filter_sql})`:

```sql
AND NOT EXISTS (
    SELECT 1 FROM image_cuts c
    WHERE c.cut_image_id IN (probe.id, e2.image_id)
      AND c.source_image_id IN (probe.id, e2.image_id)
)
```

- Symmetric: it does not matter which of the two is the probe.
- Only the source/cut pair is affected; a cut keeps matching its source's other copies, and the source keeps matching everything else.
- Cheap: `image_cuts` has a unique index on `cut_image_id` (`uq_image_cuts_cut_image_id`), so each candidate costs one index lookup.
- A module constant (`_EXCLUDE_RECUT_PAIR`, with a comment on why it sits inside the lateral) so the SQL is in one place.

## Audit of the existing batches (OQ-7)

| Batch | Question | Answer | Pinned by |
|---|---|---|---|
| `move_flagged` | Does it handle a flagged non-active image? | Yes. It selects every flagged image whatever its status and reads only `filename`, so a rejected cut (`cut_proposed`, flagged) is moved to `excluded/` like any other | Integration test: a flagged `cut_proposed` image's file is moved and reported as `moved` |
| `unregister_deleted_images` | Does it remove a rejected cut's row? | Not before R5a; now it deletes flagged `cut_proposed` images whose file is gone and never an unflagged one | R5a tests (`test_unregister_cut_proposed.py`) |
| `ingest_hash_dedup` | Must it skip files written by the cut step? | No. It scans `PATH_INGESTION_SOURCE`; the cut step writes into `BASE_PATH`. Its cross-corpus check compares only with `active` content hashes, so a `cut_proposed` row never causes a drop | Integration test: a dropped file whose hash equals a `cut_proposed` image's is not dropped as a duplicate |

## Docs

- `requirements.md`: OQ-7 gets its answer and a link to this spec.
- `docs/batches/use-cases/duplicates-and-clusters.md` and the `rebuild_duplicates` entry in `CLAUDE.md`: one sentence each, that a recut source/cut pair is never a candidate.
- `docs/image-derived-data.md`: the `tmp_duplicates` row says pairs are excluded (it still says "screenshot-recut R5").
- Tracker: R5b row linked, then `done`.

## Testing

Integration (test database; `tests/integration/test_recut_pair_exclusion.py` and additions next to the existing hash-dedup and move tests):

- A source with a near-identical cut and a control pair at the same distance: `find_duplicates()` inserts the control pair and not the source/cut pair. Run with the `rebuild_duplicates` active-library fragments and with the ingestion batch fragments.
- Both probe directions (the source as probe and the cut as probe).
- The excluded pair does not use a K slot: with `k = 1`, the source still finds its next-nearest real duplicate.
- The audit tests in the table above.
- The full `tests/integration/` root and `batch/tests/` (the change is in shared batch code, see the "Running the right test scope" gotcha in `CLAUDE.md`).

## Not covered (known)

- Re-dropping the original screenshot after its source was removed registers it as a new `pending` image, and Tier A/B review then compares it with the cut. That is existing ingestion behaviour and not part of FR-11.
- Transitive clustering: the exclusion removes only the direct source/cut pair. If a third active copy X exists, (source, X)
  and (cut, X) are still candidates, and `clusterize` (union-find) puts source, cut and X in one cluster that `/duplicates`
  shows, with the source flagged, until `move_flagged` and `unregister_deleted_images` have removed the source. Dropping
  flagged images (or recut-flagged sources) from `clusterize` would change how every flagged image is treated, so it is a
  separate decision, left to R7. Likewise the generic flag toggle can still unflag an approved source, which R5a accepts
  (no undo, no recut awareness in the flag path).
- Query plan: with the exclusion the planner keeps the HNSW ordered scan as long as `image_cuts` covers only part of the
  corpus (measured on synthetic data up to 2,000 cut rows in 20,000 images: the same cost as before). If nearly every
  active image were a source or cut, its row estimate collapses and the plan can fall back to a sequential scan per probe
  (about 70x slower in the reviewer's synthetic worst case). Not expected in practice; if it ever shows, compute each
  probe's partners once per probe instead of a correlated `NOT EXISTS`.
- Flagged images in general are still probed by the duplicate jobs; only the recut pair is excluded.
- The change was not measured against a live corpus; the added lookup is one unique-index probe per scanned candidate.
