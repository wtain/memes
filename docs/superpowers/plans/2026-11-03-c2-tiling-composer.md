# C2 Tiling Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an ordered list of image files into one tiled image (2 per row, rows fitted, white gutter), plus a cheap preview.

**Architecture:** One pure-pixel module `batch/utils/comics_compose.py`, no database, in the style of `batch/utils/recut_cut.py`. A pure `plan_layout` does all arithmetic from sizes; `compose_to_file` and `compose_preview` load pixels and paste according to the layout.

**Tech Stack:** Python 3.11, Pillow, pytest (the `batch/tests` root).

**Spec:** `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-03-c2-tiling-composer-design.md`

## Global Constraints

- Two panels per row in the given order; an odd last row has one panel, centered on the widest row. 1 panel: that panel plus the margin.
- Inside a row all panels get the same height = the smallest panel height in that row; a taller panel is scaled down. Nothing is ever scaled up, cropped or stretched. A lone last panel keeps its natural height, scaled down only when taller than the previous row.
- Rows are not rescaled to a common width: canvas width = widest row plus margins; a narrower row is centered.
- Gutter `g = max(4, round(0.01 * smallest row height))` (row heights computed first); the same `g` is the gap between panels, between rows and the outer margin; background white.
- Limit 16,000 px per canvas side, gutters and margins included, checked in `plan_layout` from sizes alone (`too_large`).
- Output: PNG when any panel is PNG, else JPEG at the caller's quality; RGB; transparency flattened on white. Preview: always JPEG quality 85, scale `min(1, max_width / canvas width)`, each panel resampled once, no full-size canvas built.
- Panel intake: EXIF orientation applied before measuring; CMYK and palette converted to RGB; JPEG, PNG and WebP accepted, anything else, animated images and unreadable files refused with the 0-based position. ICC profile kept only when every panel has the same one.
- `ComposeError(reason, position, detail)`; reasons `unreadable`, `animated`, `unsupported_format`, `too_large`, `no_panels`. `compose_to_file` writes `dst + ".tmp"` then `os.replace`, never leaves a `.tmp` after a failure, and replaces an existing `dst`. Lanczos for downscaling.
- Setting `comics.jpeg_quality: 95` in the common `environments/settings.yaml`.
- No database and no network in any test.

## Review Focus

- A 1x1 or 1xN-pixel panel, and panels with extreme aspect ratios: the layout must still produce integer sizes of at least 1 px and never zero. Pinned in Task 1.
- A panel whose EXIF says rotated 90 degrees: measured and pasted in its displayed orientation. Pinned in Task 2.
- A PNG with transparency next to a JPEG: result is PNG and the transparent area is white. Pinned in Task 2.
- A failure midway (second panel unreadable): no destination file and no `.tmp` is left, and an existing destination is untouched. Pinned in Task 2.
- A preview request wider than the real canvas: never enlarged. Pinned in Task 2.

---

### Task 1: Layout arithmetic

**Files:**
- Create: `batch/utils/comics_compose.py` (layout part only)
- Test: `batch/tests/test_comics_layout.py`

**Interfaces:**
- Produces: `class ComposeError(Exception)` with attributes `reason: str`, `position: int | None`, `detail: str`; `@dataclass(frozen=True) class Placement: index: int; x: int; y: int; width: int; height: int`; `@dataclass(frozen=True) class Layout: canvas_width: int; canvas_height: int; gutter: int; placements: tuple[Placement, ...]` (placements in input order); `MAX_SIDE = 16000`; `plan_layout(sizes: list[tuple[int, int]]) -> Layout` where `sizes` are (width, height) in displayed orientation.

- [ ] **Step 1: Write the failing tests** in `batch/tests/test_comics_layout.py`, with these exact expectations (each is `(sizes) -> canvas, gutter, placements as (x, y, w, h)`):
  - `[(100, 100)]` -> canvas `(108, 108)`, gutter 4, `[(4, 4, 100, 100)]`.
  - `[(1000, 1000), (1000, 1000)]` -> canvas `(2030, 1020)`, gutter 10, `[(10, 10, 1000, 1000), (1020, 10, 1000, 1000)]`.
  - `[(1000, 1000), (960, 1280)]` -> second panel scaled to `(750, 1000)`, canvas `(1780, 1020)`.
  - `[(1000, 1000), (750, 1000), (1000, 1000)]` -> canvas `(1780, 2030)`, third placement `(390, 1020, 1000, 1000)`.
  - `[(500, 500), (500, 500), (1000, 1000)]` -> gutter 5, third panel scaled to `(500, 500)` and centered.
  - no upscaling: `[(400, 300), (800, 900)]` -> first `(400, 300)`, second `(267, 300)`.
  - different row widths: `[(100, 100), (75, 100), (133, 100), (178, 100)]` -> gutter 4, canvas width `4 + 315 + 4`, first row placements start at `x = 72`, second row at `x = 4`.
  - 4 panels give two rows of two; 5 panels give 2, 2, 1 with the last centered on the widest row.
  - extreme ratios `[(1, 1000), (1000, 1)]` -> every placement has `width >= 1` and `height >= 1`.
  - `plan_layout([])` raises `ComposeError` with `reason == "no_panels"`; `[(9000, 100), (9000, 100)]` raises `too_large` with `position is None`.
  - property check over random size lists (`random.Random(1)`, 200 cases of 1 to 6 panels, sizes 1 to 3000): placements stay inside the canvas, no two overlap, all panels of a row share one height, and no placement is larger than its source in either dimension.

- [ ] **Step 2: Run** `pytest batch/tests/test_comics_layout.py -v` (from the repo root). Expected: FAIL, module missing.

- [ ] **Step 3: Implement** `ComposeError`, `Placement`, `Layout`, `MAX_SIDE` and `plan_layout(sizes: list[tuple[int, int]]) -> Layout` in `batch/utils/comics_compose.py`. Row heights first (min of the row's heights; lone last panel: `min(its height, previous row height)`), scaled widths `max(1, round(w * row_h / h))` (a panel not scaled keeps its size), then the gutter, then positions with a row's left offset `g + (widest_row_width - row_width) // 2`.

- [ ] **Step 4: Run** `pytest batch/tests/test_comics_layout.py -v`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(comics): tiling layout arithmetic`.

---

### Task 2: Pixel composition and preview

**Files:**
- Modify: `batch/utils/comics_compose.py`
- Test: `batch/tests/test_comics_compose.py`

**Interfaces:**
- Consumes: `plan_layout`, `Layout`, `Placement`, `ComposeError` from Task 1.
- Produces: `@dataclass(frozen=True) class ComposedFile: width: int; height: int; content_hash: str; format: str`; `compose_to_file(paths: list[str], dst: str, jpeg_quality: int) -> ComposedFile`; `compose_preview(paths: list[str], max_width: int) -> bytes`. Internally a header-only `read_panel_sizes(paths) -> list[tuple[int, int]]` (validates format and animation, applies the EXIF orientation swap to sizes) and a panel loader used by both entry points. `content_hash` uses `batch.utils.file_hash.sha256_file`.

- [ ] **Step 1: Write the failing tests** in `batch/tests/test_comics_compose.py`; generate panels in `tmp_path` as solid colors (`PILImage.new`), and read results back with Pillow:
  - `test_two_jpegs_are_tiled_with_white_gutter`: red 200x100 and blue 100x100 JPEG panels -> result is JPEG, size equals `plan_layout` canvas, pixel at the center of each placement is near the panel color (tolerance 12), a pixel inside the gutter is white (>= 250 each channel), `ComposedFile.content_hash == sha256_file(dst)`.
  - `test_png_input_gives_png`; `test_mixed_jpeg_and_png_gives_png`; `test_webp_with_jpeg_gives_jpeg`.
  - `test_transparency_is_flattened_on_white`: RGBA PNG with a fully transparent half -> that half reads white.
  - `test_exif_rotation_is_applied`: JPEG 300x100 with EXIF orientation 6 (`exif = im.getexif(); exif[0x0112] = 6`, saved with `exif=exif`) is placed as 100x300; compare against `plan_layout` for sizes `[(100, 300), ...]`.
  - `test_jpeg_quality_is_used`: two outputs at quality 95 and 40 from a noise image differ in size, 95 larger.
  - Parametrized refusals, each asserting `reason` and `position`: animated GIF at index 1 -> `animated`; a BMP at index 0 -> `unsupported_format`; a text file named `x.jpg` at index 2 -> `unreadable`; `compose_to_file([], ...)` -> `no_panels`.
  - `test_a_failure_leaves_nothing_behind`: second panel unreadable with an existing `dst` containing bytes `b"old"`: `dst` still `b"old"`, no `dst + ".tmp"`; and with no existing `dst`, no `dst` and no `.tmp`.
  - `test_an_existing_destination_is_replaced`.
  - `test_preview_matches_the_layout_at_a_smaller_scale`: `max_width = canvas_width // 2` -> JPEG bytes whose width is within 1 px of `canvas_width / 2` and height within 1 px of `canvas_height / 2`; a PNG set also previews as JPEG; `max_width` above the canvas width returns the canvas size.
  - `test_preview_of_a_large_set_does_not_build_the_full_canvas`: patch `PILImage.new` to record the sizes requested and assert none exceeds the preview size.

- [ ] **Step 2: Run** `pytest batch/tests/test_comics_compose.py -v`. Expected: FAIL, functions missing.

- [ ] **Step 3: Implement** `read_panel_sizes`, the panel loader (`ImageOps.exif_transpose`, alpha composited on white, other modes `convert("RGB")`), `compose_to_file` (header sizes, `plan_layout`, white `PILImage.new("RGB", canvas, "white")`, resize with `Image.Resampling.LANCZOS` only when the target size differs, ICC rule, `.tmp` then `os.replace`, cleanup in `except`) and `compose_preview` (same layout, scale `min(1, max_width / canvas_width)`, positions and sizes `round`ed and clamped inside the preview canvas, JPEG quality 85 into `io.BytesIO`). Wrap Pillow errors (`OSError`, `ValueError`, `SyntaxError`, `PILImage.DecompressionBombError`) as `unreadable` with the panel position.

- [ ] **Step 4: Run** `pytest batch/tests/test_comics_layout.py batch/tests/test_comics_compose.py -v`, then the whole `batch/tests` root as its own command (`cd` to the repo root; `pytest batch/tests/ -v`). Expected: PASS.

- [ ] **Step 5: Commit** `feat(comics): tiling composer`.

---

### Task 3: Setting and close-out

**Files:**
- Modify: `environments/settings.yaml` (new `comics:` group after `recut:` with `jpeg_quality: 95` and a comment pointing at the design)
- Create: `batch/tests/test_comics_settings.py` (same shape as `batch/tests/test_recut_settings.py`: the group has exactly the key `jpeg_quality` in the common file and the value is in 1 to 100)
- Modify: `docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md` (C2 `done` with plan link), `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-03-c2-tiling-composer-design.md` (Status `done`, `Plan:` line)
- Create: `docs/superpowers/reviews/2026-11-03-c2-tiling-composer-review.md`

- [ ] **Step 1: Write the failing settings test**, run `pytest batch/tests/test_comics_settings.py -v` (FAIL: no `comics` group), add the group, run again (PASS). Confirm the key loads: `python -c "from config.settings import settings; print(settings.COMICS.JPEG_QUALITY)"` run from the repo root with `APP_ENV=general` prints 95 (no `DATABASE_URL` is needed for this import; if the import demands it, use the YAML-only test and say so).
- [ ] **Step 2:** One review pass of the branch diff against the spec, saved as the review file with the final summary (fixed, not fixed with reason, ignored with reason). Fix findings with one agent, one iteration.
- [ ] **Step 3:** Update status lines and tracker. Commit `docs(comics): C2 done`.
