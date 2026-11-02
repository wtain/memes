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
| C1 | Data model and registry | `comics` and ordered `comics_panels` tables, repository enforcing one comics per image and the draft rules, name rules, popularity source `comics_build`, entries in `docs/image-status.md` and `docs/image-derived-data.md` (no new image status: panels leave through the duplicates path) | C0 | draft (design awaiting owner's approval) | `2026-11-02-c1-data-model-and-status-registry-design.md` |
| C2 | Tiling composer | Pure function: ordered panels to one image (2 per row, 3rd centered, scaling and format rules), plus preview; no database | C0 | not started | |
| C3 | Comics management API and UI | Create, rename, search, list; add an image to a comics from the meme page (existing or new); reorder; remove a panel; `backend_api.md`, shared schemas and generated types | C1 | not started | |
| C4 | Build | Build action (compose with C2, register the new image, move sources to `comics-sources/<name>/`, status changes), derived data for the new image, errors and partial-failure handling | C1, C2, C3 | not started | |
| C5 | Operations | Batch inventory and registry entries if the build runs as a job, statistics, runbook, rollout | C4 | not started | |

## Spikes and deep dives

| ID | Question | Status | Outcome |
|---|---|---|---|
| S1 | Can candidate stories be detected from CLIP distance (0.05 to 0.3), identical dimensions and close filename timestamps? | done | No. On 12 random candidates from `general` (575 pairs matched the filter) the owner found no story, apart from one loose maybe. Too imprecise to drive review. The duplicate-flow integration was dropped. Scratch scripts were not kept. |
| S2 | Which signal could suggest candidate stories with few false positives (OCR text continuity, shared frame, numbering, same-author series)? | open, later | Not needed for C1 to C5 |
| D1 | How does the built image get its derived data (OCR, embeddings, tags, descriptions, profile): reuse the ingestion chain or the recut refresh path? | open | Decide in C4 design |
| D2 | What happens to popularity, ratings, history and saves that reference a panel (popularity absorption into the built image?) | open | Decide in C1 or C4 design |
| D3 | Does the build run inside the request (as recut box adjustment does) or as a run-tracked job? | open | Decide in C4 design |
| D4 | Layout details: panels of different sizes or aspect ratios, more than 3 panels, output format and quality, size cap | open | Decide in C2 design |
| D5 | Comics name rules: uniqueness, Cyrillic and Windows-forbidden characters in a directory name, collisions in `comics-sources/` | open | Decide in C1 or C4 design |
| D6 | Failure and atomicity of the build: files move, database rows change, a crash in between | open | Decide in C4 design |
| D7 | Recut approval flags and moves its source image; a recut starting on an image that is already a panel of a draft comics would leave a missing panel. Guard in recut code (skip images in a comics) or accept the missing-panel handling | open | Decide with C3 or C4; C1 only blocks the other direction |

## Order

```
C0 ──► C1 ──► C3 ──┐
  └──► C2 ─────────┴──► C4 ──► C5
```

C2 and C1 can run in parallel in separate worktrees. C3 needs C1 only; C4 needs all three.
