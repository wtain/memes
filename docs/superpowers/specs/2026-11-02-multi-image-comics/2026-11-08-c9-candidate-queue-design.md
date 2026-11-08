# C9: Candidate queue (suggested comics)

Status: done
Plan: docs/superpowers/plans/2026-11-08-c9-candidate-queue.md
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md (spike S3 idea c, spike S2)
Follow-ups: none yet

## Intent

The owner finds stories by luck today. S1 tried to detect them from CLIP distance, identical dimensions and close filename timestamps and found nothing usable. A real story the owner then found (two panels of `general`, 2026-11-08) showed why: the panels were **not** the same size (651x630 and 689x621), but they were each other's nearest CLIP neighbour (cosine distance 0.0998) and their filenames were two seconds apart (`IMG_20200923_110706_156.jpg`, `IMG_20200923_110708_392.jpg`). The owner confirmed the useful traits are the same visual style and, in other stories, text that continues; the known pair had text on only one panel, so a text signal is not used in this version.

C9 turns that into a **queue of suggested groups**: a batch job finds groups of images that were saved seconds apart and look alike, a page lists them, and the owner makes a draft comics from a group with one click or dismisses it for good. Dismissals and acceptances are stored, so the queue never repeats itself and the owner's decisions show how precise the detector is.

## Spike result (read-only, `general`, 2026-11-08)

- The known pair: CLIP distance 0.0998, filename timestamps 2 s apart, different sizes, one panel without OCR text.
- Filename timestamps: 20,028 of 32,328 active images have a `YYYYMMDD_HHMMSS` timestamp in the name; the next common shape is a 13-digit epoch in milliseconds (`FB_IMG_1746890432471`: 2,335 in `general`, 13,591 in `metal`, 978 in `it`).
- Pairs of images with timestamps at most 5 s apart: 3,373; of those with CLIP distance at most 0.15: 96, at most 0.12: 35, at most 0.10: 20. At most 10 s apart: 6,573 pairs, 166 within 0.15. So a distance window of roughly 0.06 to 0.15 on a 5 s gap gives a queue of about a hundred pairs in `general`, which one person can review. The precision of that list is unknown; the queue measures it.

## Decisions by the owner

- Build the review queue as **suggestions the owner confirms or dismisses** (2026-11-08).
- Same visual style and continuing text are the traits of the stories the owner cares about.

## Decisions I made (my calls)

- **Signals (v1).** Two images are linked when (a) their filename timestamps are at most `gap_seconds` apart and (b) their CLIP cosine distance is between `min_distance` and `max_distance`. Sizes are not compared. Text is not used. Defaults: `gap_seconds` 5, `min_distance` 0.06 (below that two images are duplicates and belong to the duplicates flow), `max_distance` 0.15, `max_group` 6 (a larger linked group is a batch save and is dropped), `max_candidates_per_run` 500. All five are settings under `comics_candidates.*`, required in every environment's YAML.
- **Timestamps.** Two filename shapes: `YYYYMMDD_HHMMSS` anywhere in the name (year 2000 to 2099, valid date and time), and a 13-digit epoch in milliseconds (between 2005 and 2035). Two images are compared only when their timestamps are of the same shape. Other names have no timestamp and never become candidates. More shapes can be added later in one module.
- **Eligible images.** Active, not flagged, not a panel of any comics, with an embedding and a parsable timestamp.
- **Groups.** Sort the eligible images per timestamp shape, link neighbours that meet both signals (an image is compared with every later image within `gap_seconds`), take connected components of 2 to `max_group` images, order each by timestamp then filename.
- **Storage (new migration, on top of `f3a8c1d6b952`).** `comics_candidates`: `id uuid` primary key, `key text` unique (md5 of the sorted image ids, so a re-run never recreates a group), `state text` (`open`, `accepted`, `dismissed`), `detector_version text`, `max_gap_seconds double precision`, `max_distance double precision` (the strongest signals found, for later precision analysis), `comics_id uuid` null (foreign key to `comics`, set null on delete), `created_at`, `decided_at` null. `comics_candidate_images`: `candidate_id` (cascade), `image_id` (cascade on image delete), `position int`, primary key (`candidate_id`, `image_id`). A candidate whose image is deleted or whose images are no longer all eligible is not listed (checked at read time; the row stays).
- **Batch job `detect_comics_candidates`.** Run-tracked (kind `detect_comics_candidates`, stats `images_considered`, `pairs_linked`, `groups_found`, `groups_inserted`, `groups_skipped_existing`, `groups_dropped_oversized`), admin-triggerable from `/admin/batches`, manual-trigger only, not scheduled, registered on all three environments. Incremental by construction (the unique `key`). It never changes images, comics or any other table.
- **API (new, under `/api/comics/candidates`, declared before `/{comics_id}`).** `GET` lists open, still-eligible candidates (offset/limit like the comics list, newest strongest first: smallest `max_distance`), each with its images (`id`, `filename`, `width`, `height`). `POST /{candidate_id}/accept` with an optional body `{imageIds: [...]}` (an ordered subset of the group, at least 2; default all) creates a draft comics named like C6 (`Comics <first file name>`, retry once with the id prefix on a name conflict), adds the images in order, marks the candidate `accepted` and returns the `ComicsDetail`, all in one transaction; if an image is no longer addable the answer is 409 with the reason and nothing changes. `POST /{candidate_id}/dismiss` marks it `dismissed` (idempotent). 404 for an unknown candidate; accepting or dismissing a decided one is 409 `candidate_decided`.
- **Page.** `/admin/comics/candidates` ("Suggestions"), linked from the comics list page. Each candidate is a row of thumbnails (with file name and size); each thumbnail can be switched off before making the comics; buttons "Make comics" (opens the editor of the new draft) and "Dismiss". A short "Show more" pager. Empty state explains that the job `detect_comics_candidates` fills the page.
- **Statistics.** Not changed in this version.
- **Rollout.** One migration; applied only with the owner's go-ahead per environment, like C1 to C4. The rollout document and the preflight's expected columns are extended (tables `comics_candidates` and `comics_candidate_images`).

## Rulings after the builder check (my calls)

- **Decided groups suppress overlapping new ones.** Exact-key uniqueness is not enough (a new image joining a dismissed pair, or leftovers of a partly accepted group, would form a new key). So the detector skips a new group that shares **two or more images** with any candidate in state `dismissed` or `accepted` (all rows of that candidate count, including images the owner switched off). A real triple that contains a dismissed pair is therefore suppressed; the owner can always add such an image by hand. Counter `groups_skipped_decided`.
- **Stored signals are the weakest link.** `max_gap_seconds` and `max_distance` are the largest gap and the largest CLIP distance among the group's linked edges; the list sorts by `max_distance` ascending, then `id`.
- **Chaining is intended.** Components are connected through edges (A-B and B-C make one group even if A-C is far apart); `max_group` applies to the component size.
- **Accept with a subset.** The candidate becomes `accepted`; images switched off are not dismissed. They can only reappear in a group sharing fewer than two images with the accepted one, because of the suppression rule above.
- **Paging.** Eligibility (every image active, not flagged, in no comics, still present) is checked in SQL, so pages are full and the list is stable; no total count is returned.
- **Already in a comics.** A candidate with an image now in a comics is not listed; accepting a candidate that became ineligible meanwhile answers 409 with the first offending image's reason (reuse the comics service's addability reasons).
- **Timestamps.** No comparison across the two shapes. The epoch shape matches a run of exactly 13 digits not adjacent to other digits.

## Goals

- A reviewable list of likely stories with one-click creation of the draft and one-click dismissal.
- Precision measurable from the decisions that accumulate.
- No effect on anything outside the new tables.

## Non-goals and anti-goals

- No text-continuity signal, no description or profile signal, no automatic building, no automatic creation of drafts, no scheduling.
- No tuning of thresholds beyond the settings; no machine learning.
- The job must never flag, move or modify an image; a dismissed group never returns.

## Constraints

- Follow the batch conventions in `docs/batches/` (registry, `inventory.md` row, CLAUDE.md batch list entry, use-case note, `tests/docs/` guards).
- `backend_api.md`, shared schemas and the three generated type trees stay in sync.
- Tests use only `ocrdb_test`; Backend, `tests/integration/` (whole root), `batch/tests/`, `tests/docs/` and the frontend run as separate commands.
- Any live-database query by an agent uses `DATABASE_URL_READONLY` only; no migration is applied by an agent without the owner's go-ahead.

## What is left to the builder

Module layout, the exact timestamp regexes within the two shapes, the migration revision id, the schema names, test layout, copy of the page's texts, and the order of the plan's tasks.

## Done when

The job finds the known pair in a test fixture with those two filename shapes and distances; oversized and out-of-window groups are skipped; a re-run inserts nothing new; accept and dismiss behave as above including the 409 cases; the page works; all suites are green; the rollout document and preflight cover the migration; the review report is saved; the tracker marks C9 done and the follow-up (measure precision from the decisions) is recorded.
