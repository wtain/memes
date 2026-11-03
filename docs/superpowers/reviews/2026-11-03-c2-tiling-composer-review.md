# C2 tiling composer: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-03-c2-tiling-composer-design.md`
Plan: `docs/superpowers/plans/2026-11-03-c2-tiling-composer.md`
Range reviewed: `8ec19cc..4de8edc` (layout, composition, setting, two fix rounds).

Process: a per-task review after tasks 1 and 2, one fix round on task 2, a final whole-branch review on the most capable model, one
fix wave and one scoped re-review. Task 3 (the setting, two files) had no separate task review; the final review covered it
explicitly.

## What was fixed

- Multi-frame JPEG photos (MPO, common from phones) were refused as animated; they are now treated as JPEG using the first frame
  and the animation check applies only to GIF, PNG and WebP.
- A CMYK ICC profile could be embedded in RGB output; then a Gray or Lab profile could still be embedded through grayscale
  panels. The rule is now: a profile is kept only when every panel carries the same one and its header colour space is `RGB `.
- A 16-bit panel (modes I, F, I;16) was silently clipped to white; it is now scaled to 8 bits before conversion.
- The module docstring described only the layout arithmetic; it now describes the whole module and links the spec.

## Not fixed, accepted (with reason)

- `write_error` is a reason the spec did not list. It is a contained addition (raised only around the output write, position
  `None`, tested); the spec's reason list now includes it.
- WebP counts as neither JPEG nor PNG for the output-format rule, so a WebP-only set gives JPEG. Ruling made at pre-flight; one
  line to change if wanted.
- Concurrent writes to the same destination share `dst + ".tmp"`. Same pattern as `recut_cut`; output naming and locking belong to C4.

## Deferred minors (no merge impact)

- The layout property test does not count skipped `too_large` cases and there is no boundary test at 16000 and 16001.
- A test checks EXIF rotation by canvas size only; the preview memory test uses opaque panels only.
- `read_panel_sizes([])` returns an empty list instead of raising `no_panels` (unused internally).
- `compose_preview` does not validate `max_width <= 0` (worth a one-line error when C3 wires the endpoint).
- `sha256_file` runs after `os.replace`, outside the error handling.
- A panel large enough to trip Pillow's decompression-bomb guard is reported as `unreadable`, not `too_large`.
- `read_header` may decode a PNG to read EXIF (performance only).
- Cosmetic spacing in one line.
- Possible optimisation for C3: JPEG `draft()` decoding for cheaper previews of large phone-photo sets.
