# Epic: Multi-image comics

Status: draft
Originates from: user request, 2026-11-02 (brainstorming session)
Follow-ups: project design specs are listed in `tracker.md` and written when each project is picked up.

One story is often spread over several images (a comic, a setup and its punchline, a comment thread). Only the whole matters, so
the library should hold one combined image instead of its parts. This epic lets the owner assemble a named "comics" from 2 or more
existing images, put them in order, and build a single tiled image from them. The source images move out of the library into
`comics-sources/<comics name>/`.

This folder is the epic: charter and project tracker. Design specs for individual projects are written one per project and linked
from `tracker.md`.

## Decisions made (by the owner, in brainstorming)

- **Scale.** A couple of dozen cases in about 30k images; usually 2 or 3 panels, seldom more. Human involvement is acceptable.
- **Result.** The panels are tiled into one image, 2 per row. With 3 panels the first two share the first row and the third is
  centered on the second row.
- **Not part of duplicate review.** It is a separate flow with its own page. The earlier idea of marking a subset of a duplicate
  cluster as "a story" was dropped: see spike S1 (the similarity signal has almost no precision).
- **Entry points.** Start from an empty "create comics", or from any meme (details page): "add to comics" with the choice of an
  existing comics or a new one.
- **Comics are persistent, named and searchable.** A comics with at least 2 images offers "build".
- **Build.** Composes the tiled image, registers it as a new library image, and moves each source image to
  `comics-sources/<comics name>/` (same family as the `duplicates` directory and `move_flagged`).
- **Irreversible.** As with duplicates, there is no undo of a build.
- **Order** is set by a human before building.

## Decisions I made (guesses, to be confirmed in the project designs)

- A comics is a database record with an ordered list of images, so an unfinished one survives restarts.
- The built image is a new `images` row that runs through the normal derived-data pipeline, as a recut cut does; the source images get a
  status of their own (a new row in `docs/image-status.md`, which `tests/docs/` enforces).
- Automatic suggestion of candidate stories is not part of the first projects (spike S2).

## Non-goals

- Reversing a build, or editing a built comics.
- Automatic combination without a human decision.
- Layouts beyond the simple tiling (panel borders, captions, free arrangement).

## Anti-goals

- A detector that floods the owner with false candidates (spike S1 showed this is what similarity alone does).
- Losing a source file: nothing is deleted, sources are moved.

## Glossary

- **Comics**: a named, ordered list of images intended to be one story.
- **Panel**: one source image of a comics.
- **Build**: composing the tiled image and retiring the panels.
