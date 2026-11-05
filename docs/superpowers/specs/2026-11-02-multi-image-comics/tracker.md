# Multi-image comics: project tracker

Status: draft
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md

Created 2026-11-02. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists. Spikes and
deep dives use `open`, `in progress`, `done` or `dropped`. Work happens in a worktree and each project is merged to `main` when done.

## Projects

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| C0 | Epic charter and tracker | This folder | none | done (split approved and merged 2026-11-02) | `README.md`, `tracker.md` |
| C1 | Data model and registry | `comics` and ordered `comics_panels` tables, repository enforcing one comics per image and the draft rules, name rules, popularity source `comics_build`, entries in `docs/image-status.md` and `docs/image-derived-data.md` (no new image status: panels leave through the duplicates path) | C0 | done (2026-11-04) | `2026-11-02-c1-data-model-and-status-registry-design.md`, plan `docs/superpowers/plans/2026-11-03-c1-comics-data-model.md`, review `docs/superpowers/reviews/2026-11-03-c1-comics-data-model-review.md` |
| C2 | Tiling composer | Pure function: ordered panels to one image (2 per row, 3rd centered, scaling and format rules), plus preview; no database | C0 | done (2026-11-03) | `2026-11-03-c2-tiling-composer-design.md`, plan `docs/superpowers/plans/2026-11-03-c2-tiling-composer.md`, review `docs/superpowers/reviews/2026-11-03-c2-tiling-composer-review.md` |
| C3 | Comics management API and UI | Create, rename, search, list; add an image to a comics from the meme page (existing or new); reorder; remove a panel; `backend_api.md`, shared schemas and generated types | C1 | done (2026-11-05) | `2026-11-04-c3-comics-api-and-ui-design.md`, plan `docs/superpowers/plans/2026-11-05-c3-comics-api-and-ui.md`, review `docs/superpowers/reviews/2026-11-05-c3-comics-api-and-ui-review.md` |
| C4 | Build | Build action (compose with C2, register the new image, move sources to `comics-sources/<name>/`, status changes), derived data for the new image, errors and partial-failure handling | C1, C2, C3 | planned (2026-11-05) | `2026-11-05-c4-build-design.md`, plan `docs/superpowers/plans/2026-11-05-c4-build.md` |
| C5 | Operations | Batch inventory and registry entries if the build runs as a job, statistics, runbook, rollout | C4 | not started | |

## Spikes and deep dives

| ID | Question | Status | Outcome |
|---|---|---|---|
| S1 | Can candidate stories be detected from CLIP distance (0.05 to 0.3), identical dimensions and close filename timestamps? | done | No. On 12 random candidates from `general` (575 pairs matched the filter) the owner found no story, apart from one loose maybe. Too imprecise to drive review. The duplicate-flow integration was dropped. Scratch scripts were not kept. |
| S2 | Which signal could suggest candidate stories with few false positives (OCR text continuity, shared frame, numbering, same-author series)? | open, later | Not needed for C1 to C5 |
| D1 | How does the built image get its derived data (OCR, embeddings, tags, descriptions, profile): reuse the ingestion chain or the recut refresh path? | done | Neither: the build registers the image as active; the existing incremental jobs fill its data afterwards. A `comics_pipeline` driver is C5 (C4 design) |
| D2 | What happens to popularity, ratings, history and saves that reference a panel (popularity absorption into the built image?) | done | Popularity inherits like duplicates (source `comics_build`); notes, saves, ratings and history of retired panels are not carried over (C4 design) |
| D3 | Does the build run inside the request (as recut box adjustment does) or as a run-tracked job? | done | Inside the request; the source files move later with move flagged (C4 design) |
| D4 | Layout details: panels of different sizes or aspect ratios, more than 3 panels, output format and quality, size cap | done | Answered in the C2 design |
| D5 | Comics name rules: uniqueness, Cyrillic and Windows-forbidden characters in a directory name, collisions in `comics-sources/` | done | Answered in the C1 design (name rules, unique name key) |
| D6 | Failure and atomicity of the build: files move, database rows change, a crash in between | done | One transaction; the only file written is the built image, removed on rollback; source files are untouched until move flagged (C4 design) |
| D11 | C3 follow-ups that become reachable with the build (found in the C3 final review): (a) the editor does not refresh after a `built` or `not_found` error, so draft controls stay live on a stale view; (b) "Move from X" reads as if the target were built when the source X is built; (c) the built view cannot say whether a retired panel's source image still exists (`ComicsPanel` has no `sourceImageId`; ties to D10); (d) a failed preview has no retry button; (e) extract named schemas for `ComicsAddability.items` and `ComicsByImage.comics` before Android gets comics screens (Kotlin types them as `JsonObject`) | open | C4 or C5 |
| D9 | C1 follow-ups found in review, none blocking: (a) no test pins the row locks in `rename`/`delete`/`mark_built` or `get(lock=True)` (needs a two-session `FOR UPDATE NOWAIT` test; write it with C4, which relies on the lock); (b) an automated migration shape and downgrade-guard test for `d7a3b9c5e214`, like `test_popularity_migration.py`; (c) add `source_image_id` to the `comics_panels` columns in `tools/rollout_preflight.py` `EXPECTED`; (d) `mark_built`'s `image_already_built` pre-check is check-then-act, translate the unique violation too if C4 can race; (e) tests for the add_image re-raise branches | open | C4 or a small cleanup |
| D8 | `repository/discovery_report.py` `DEFAULT_SOURCES` is all popularity sources except `adjustment`, so `comics_build` edges would count as "memes that keep coming back" in the discovery report. A comics build absorbing its panels is not a re-drop. Exclude the source there or document it | decided | Exclude `comics_build` from `DEFAULT_SOURCES`; implemented in C4 |
| D7 | Recut approval flags and moves its source image; a recut starting on an image that is already a panel of a draft comics would leave a missing panel. Guard in recut code (skip images in a comics) or accept the missing-panel handling | open | decided | No guard in the recut code: the build re-checks every panel (not active, flagged, live cut) and refuses with the panel position (C4 design) |
| D10 | Optional keeping of source images at build (owner request, 2026-11-05): kept panels stay active and are released from the comics (provenance row kept, `image_id` NULL). A built comics then cannot tell a kept panel from a retired one by `image_id`; record it (a `kept` flag on the panel row, a new migration) or derive it from `source_image_id` still existing as an image. Also: kept panels pass no popularity | decided | A `kept` boolean column on `comics_panels`, added by a second migration after `d7a3b9c5e214`; implemented in C4 |

## Order

```
C0 ──► C1 ──► C3 ──┐
  └──► C2 ─────────┴──► C4 ──► C5
```

C2 and C1 can run in parallel in separate worktrees. C3 needs C1 only; C4 needs all three.
