# Use case: suggested comics (candidate queue)

Status: draft (written 2026-11-08). Design: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-08-c9-candidate-queue-design.md`.

`detect_comics_candidates` suggests groups of images that may be one story: images saved seconds apart that look alike. The
owner makes a draft comics from a group or dismisses it on the suggestions page; the job only stores the suggestions.

```
active, unflagged images in no comics, with an embedding and a file name timestamp
   │  pairs with timestamps at most gap_seconds apart (same timestamp shape only)
   ▼
CLIP cosine distance per pair (embeddings, pgvector <=>, only for those pairs)
   │  link when min_distance <= distance <= max_distance
   ▼
connected components of 2..max_group images  ──►  comics_candidates (open) + comics_candidate_images
   (larger components are batch saves: dropped, counted)
```

- **Timestamps.** `YYYYMMDD_HHMMSS` anywhere in the name (year 2000 to 2099, valid date and time), or a run of exactly 13 digits between
  2005 and 2035 (epoch milliseconds). The two shapes are never compared with each other; a name with neither never becomes a
  candidate. Sizes and OCR text are not used.
- **Settings** (`comics_candidates.*`, required): `gap_seconds` 5, `min_distance` 0.06 (below it two images are duplicates and belong to
  the duplicates flow), `max_distance` 0.15, `max_group` 6, `max_candidates_per_run` 500 (strongest groups first, so the cap keeps the best).
- **Re-runs.** The group key is the md5 of its sorted image ids, so an existing group is never recreated (`groups_skipped_existing`), and a
  new group sharing two or more images with a dismissed or accepted candidate is skipped (`groups_skipped_decided`).
- **Stats.** `images_considered`, `pairs_linked`, `groups_found`, `groups_inserted`, `groups_skipped_existing`, `groups_skipped_decided`,
  `groups_dropped_oversized`.
- **Safety.** The job writes only the two candidate tables; it never flags, moves or modifies an image or a comics. Admin-triggerable
  from `/admin/batches`, manual-trigger only, not scheduled; `python -m batch.detect_comics_candidates --env {metal,general,it}`.

## Review and known behaviour

- **Review.** The page `/admin/comics/candidates` ("Suggestions", linked from the comics list) shows each open group as a row of
  thumbnails. A thumbnail can be switched off before "Make comics" (at least two stay on); "Dismiss" hides the group for good.
  Accepting creates a draft comics named `Comics <first file name>` in one transaction and opens its editor. Groups with an image that
  was since flagged, deactivated, deleted or put in a comics are not listed. Accepting with no image list is refused with 409
  `candidate_incomplete` when a deleted image left fewer rows than the group was stored with.
- **Live recut cuts.** An image that is the source or the cut of a live recut row (detected, proposed or approved) is skipped by the
  detector, and a group containing one is not listed (the comics service would refuse it as "has a recut cut").
- **Overlapping open groups.** If A-B is open and a later run finds A-B-C, both are listed; accepting one makes the other answer 409
  (`image_in_comics`). Deciding a group hides every open group that shares two or more images with it (so dismissing A-B-C hides A-B,
  in the list as well as in later runs); only decided groups suppress new ones.
- **Measuring precision.** `comics_candidates.state` counts (accepted against dismissed) and `max_distance` / `max_gap_seconds` of each
  decided candidate are the data for tuning `gap_seconds`, `min_distance` and `max_distance` later. A read-only query over those two
  columns is enough.
