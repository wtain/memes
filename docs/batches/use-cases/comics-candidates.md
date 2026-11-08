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
