# C2: Tiling composer

Status: approved
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C2)
Follow-ups: none yet

## Intent

Turn an ordered list of image files into the one tiled image of a comics. C2 is pure pixel work, like
`batch/utils/recut_cut.py` for recut: no database, no knowledge of comics records, no moving of source files. C3 uses it for the
preview, C4 for the build.

## Decisions by the owner (brainstorming, 2026-11-03)

- **Layout.** Two panels per row, in the order given. With an odd count the last row has one panel, centered. Examples: 2 panels
  are one row; 3 are two then one centered; 4 are two rows of two; 5 are two, two, one centered.
- **Rows fitted to each other** (mockup A): inside a row the panels are scaled to the same height so they meet edge to edge. A
  panel is never cropped and never stretched.
- **Full resolution.** No downscaling cap for the result. A panel is never scaled up: the row height is the height of the
  smallest panel in the row, and a taller panel is scaled down to it.
- **Rows of different widths** (mockup X): the image is as wide as its widest row, and a narrower row is centered, with white on
  both sides, exactly like a lone last panel. Rows are not rescaled to a common width, so no panel loses resolution to line up
  with another row.
- **Thin white gutter, white background.** Gutter between panels and between rows; the same white as an outer margin of the
  same width around the whole image.
- A panel set of 2 is the minimum for a build, as in the comics rules (C1); the composer itself accepts 1 or more so the preview
  can show a draft early.

## Decisions I made (my calls, to check)

- **Row height of a single centered panel** is its own natural height, scaled down only when it would otherwise be taller than the
  previous row (so a lone last panel never dominates the image). It stays centered on the width of the widest row.
- **Gutter width** is 1% of the smallest row height, at least 4 px, where row heights are the scaled panel heights computed first
  (the gutter never changes a row height, so there is no circularity). The same value is used for gutter, row spacing and outer
  margin.
- **Safety limit** of 16,000 px on either side of the finished canvas, gutters and margins included. `plan_layout` raises
  `too_large` from the header sizes alone, before any pixel is loaded.
- **Output format.** JPEG when every panel is JPEG, quality from a new setting `comics.jpeg_quality` (default 95, like
  `recut.jpeg_quality`); PNG when any panel is PNG (lossless, so no quality loss from a PNG source); mixed JPEG and PNG panels
  give PNG. Output color mode is RGB; transparency is flattened onto the white background.
- **Panel intake.** EXIF orientation is applied before measuring; CMYK and palette images are converted to RGB. Animated images
  (GIF, animated WebP), unreadable files and files whose format is not JPEG, PNG or WebP are refused, with the position of the
  offending panel in the error. A WebP panel is read and, because the output is JPEG or PNG, becomes pixels like any other.
  ICC profile of the first panel is carried over when all panels share one; otherwise dropped.
- **Resampling** uses Pillow's Lanczos filter for the downscale.
- **Preview** uses the same layout computed at full size; every panel is then resampled once, directly from its file, at the
  preview scale (the full-size canvas is never built), so it matches the real layout exactly and stays cheap for large sets. The
  scale is `min(1, max_width / canvas width)`: a preview is never larger than the real result. It is always JPEG at quality 85,
  with transparency flattened on white, even for a PNG set, and is returned as bytes, never written to the library.
- **Errors.** `ComposeError.position` is the 0-based index of the offending panel (C3 and C4 display it 1-based). An empty list is
  `no_panels`; one panel gives that panel plus the margin. A panel without an ICC profile among panels that have one counts as a
  mismatch, so the profile is dropped. `compose_to_file` replaces an existing `dst` atomically (`os.replace`).
- **Setting.** `comics.jpeg_quality: 95` goes in the common `environments/settings.yaml`, read the way `recut.jpeg_quality` is, so
  every environment inherits it.
- **Docker.** C2 adds no Docker or `.dockerignore` change. If C4 decides (D3) that the build runs inside a backend request, C4
  adds the module to `Dockerfile.backend` and `.dockerignore`, as recut did for `recut_cut.py`. The composer uses only Pillow and
  the standard library, so it can move without surprises.

## Interface

A module in `batch/utils/`, next to `recut_cut.py`. One error class `ComposeError(reason, position, detail)` with reasons such as `unreadable`, `animated`,
`unsupported_format`, `too_large`, `no_panels`.

- `plan_layout(sizes: list[tuple[int, int]]) -> Layout`: pure arithmetic, no files. Takes the (width, height) of each panel after
  orientation and returns the rows, each panel's scaled size and its position, the gutter, and the canvas size. Unit-testable
  without pixels.
- `compose_to_file(paths, dst, jpeg_quality) -> ComposedFile`: writes `dst` atomically (`dst + '.tmp'`, then rename, as
  `crop_to_file` does) and returns width, height, content hash and format.
- `compose_preview(paths, max_width) -> bytes`: the same layout, scaled once, returned as JPEG bytes.

## Out of scope

Records, state, file moves, naming the output file, registering the image (C1, C4); endpoints and UI (C3).

## Tests

`plan_layout` is table-tested: 1, 2, 3, 4 and 5 panels; mixed aspect ratios; a lone last panel taller than the row above; the
gutter minimum; the 16,000 px refusal. Pixel tests use generated images in a temporary directory: size and position of each
panel in the result, white gutters, no upscaling, EXIF rotation, PNG transparency flattened, mixed JPEG and PNG giving PNG,
animated GIF refused with its position, atomic write (no `.tmp` left after a failure). No database, no network. The preview is
checked to have the same layout as the full result at a smaller scale. Plus the existing `tests/docs/` guards if a setting or
doc is added.

## Left to the builder

The module name, exact rounding rules for scaled sizes (the rule must keep every row within one pixel of the same height and
the result independent of file order beyond the given order), the shape of `Layout`, and whether to check the 16,000 px limit
before or after loading pixels (before is preferred: sizes come from file headers).
