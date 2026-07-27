# Image Pending/Active Visibility — Design

Status: done
Originates from: docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md

**Date:** 2026-07-25. Prerequisite for `2026-07-24-ingestion-pipeline-design.md` — ingestion needs
newly-registered images to be invisible to normal browse/search until duplicate review clears
them, and to reuse the *same* `images`/`embeddings` tables so embeddings computed during review
aren't recomputed on promotion.

---

## Motivation

The ingestion design's chosen architecture (same DB, not a separate staging store) only works if
an image can be registered — with a real `Image` row and a real `Embedding` row — while still being
completely absent from every normal browse/search/stats surface. Today, nothing in the schema
distinguishes "fully reviewed, part of the library" from "just registered." `image_extras.flagged`
looks like a candidate but isn't one: it's a *visible* admin marker (flagged images still appear in
normal paginated listings — see `2026-06-29-rename-excluded-to-flagged-design.md`), not a hiding
mechanism.

This spec adds that hiding mechanism and, more importantly, enumerates the exhaustive list of
places that must respect it — because the actual risk here is not "can we add a column," it's "did
we forget one query and leak unreviewed content into someone's search results."

Confirmed with the user (2026-07-25): pending images are moved into `BASE_PATH` immediately after
stage-1 hash dedup, same as the pipeline moves files today — there is no separate "serve files from
`PATH_INGESTION_SOURCE`" mode to build. This removes an entire category of complexity the original
ingestion draft was carrying (a second file-serving path) — file-serving code (`image_store.py`,
`get_image_path()`) needs **no changes**. This spec is purely about DB-level visibility.

---

## Design

### Schema

```python
op.add_column('images', sa.Column('status', sa.String(20), nullable=True))
op.execute("UPDATE images SET status = 'active' WHERE status IS NULL")
op.alter_column('images', 'status', nullable=False, server_default='active')

op.add_column(
    'images',
    sa.Column('ingestion_batch_id', postgresql.UUID(as_uuid=True), nullable=True),
)
op.create_foreign_key(
    'images_ingestion_batch_id_fkey', 'images', 'batch_runs',
    ['ingestion_batch_id'], ['run_id'], ondelete='SET NULL',
)

# Targeted at the pending side deliberately, not `status` generally — see rationale below.
op.create_index(
    'ix_images_status_pending', 'images', ['id'],
    postgresql_where=sa.text("status = 'pending'"),
)
```

Depends on `2026-07-25-batch-run-tracking-design.md` landing first (`ingestion_batch_id` FKs to
`batch_runs.run_id`).

- `status`: plain `String(20)`, values `pending` / `active` / `rejected` — matches the existing
  `TrendsRun.status` / soon `BatchRun.status` convention of app-level string enums over native
  Postgres enum types (cheaper to extend later, no `ALTER TYPE`).

**Why a third value, `rejected`, and not a hard delete.** Added per user feedback (2026-07-25): the
existing agent-driven duplicate-review skill has produced false positives that permanently lost
images, which is exactly the failure mode a reject-by-status-flip avoids. When ingestion review
(stages 2/3 of `2026-07-24-ingestion-pipeline-design.md`) confirms a duplicate, the image's `status`
flips to `rejected` rather than the row being deleted — the file still physically moves to
`duplicates2/`/`duplicates3/` (the audit trail for *why*), but the DB row survives so an incorrect
rejection can be undone from the review UI itself (flip back to `pending`, move the file back)
without needing to reconstruct a deleted row from scratch. `rejected` needs no separate visibility
audit work — every query already defaults to `status="active"`, so `rejected` is excluded by the
exact same default as `pending`, for free.
- **Partial index on `pending` only, not a plain index on `status`.** With images overwhelmingly
  `active` (pending images are a small, transient minority — reviewed and promoted within a batch's
  lifetime), a full index on `status` gives the planner almost no selectivity for the common
  `WHERE status = 'active'` case; a partial index sized to the *small* pending set makes the
  "list pending images for review" queries fast without bloating index maintenance cost on every
  insert of an active-corpus-scale table. Same reasoning as the `tmp_duplicates` FK-index ADR's
  lesson about indexing to the query pattern, not the column in the abstract.

### Where `status` does *not* gate access

Byte-serving by known id (`GET /api/images/{id}` and equivalent) is **not** gated by status. The
ingestion review UI needs to render pending images by id, same as any other image — visibility
gating applies to *enumeration* (listing, search, stats, recommendations), not to direct lookup by
an id the caller already has. This mirrors how `flagged` images remain individually fetchable
today; pending images should behave the same way at the single-image level, differing only in
whether they show up when you *browse*.

### Repository convention

Every repository method that returns a list/page of images gains a `status` parameter, defaulting
to `"active"`:

```python
async def get_all_images(self, status: str = "active"): ...
```

Call sites needing pending images (the ingestion review endpoints) pass `status="pending"`
explicitly — so "forgot to filter" and "deliberately requesting pending" are both visible at the
call site, not buried in a default that's easy to omit. New methods added after this spec lands are
expected to follow the same convention; there's no framework-level enforcement of that beyond the
contract test below (see Verification).

---

## Full audit — every call site enumerated

### `repository/images.py` (global repo, used by most batch jobs)

No shared base query to patch centrally — each method is its own ad hoc `select(Image, ...)`.
Each of these needs the `status` parameter added:

- `get_all_images_with_hash()` — used by `detect_file_duplicates.py`
- `get_all_images()` — used by `build_tags_from_ocr.py`, `build_tags_from_descriptions.py`, others
- `iterate_images()`
- `get_images_and_ocr_texts()` — used by `build_ocr_lemmas.py` and others
- Any other `select(Image...)` in this file at implementation time — this list is from the current
  file as of this draft; grep for `select(` in `repository/images.py` at implementation time to
  confirm nothing new has landed since.

### `Backend/app/repositories/image_repository.py`

- Paginated browse query (returns `img.id, img.filename, img.created_at, extras.flagged`)
- Search/candidate query (embedding-distance based, joined with descriptions)
- `get_duplicates_clustered()` — also needs a *batch* filter per the ingestion design, on top of
  the status filter; the two are independent (active-library dedup review stays `status='active'`,
  ingestion-batch review uses `status='pending' AND ingestion_batch_id = :id`)
- `get_flagged()`
- Similarity/recommendation queries (three call sites identified in this file as of this draft)

### `Backend/app/repositories/diagnostics_repository.py`

- Corpus-size / stats counts (`/api/diagnostics/health` and friends) — pending images must not
  inflate counts shown to users as "current library size."

### `batch/extract_text_from_memes.py` — special case, no repository involved, and *not* a blanket exclusion

This script does **not** query the DB for its candidate list — it calls `os.listdir(BASE_PATH)`
directly (`io_producer`), then looks up or registers each file by filename. Since ingestion now
moves pending files into `BASE_PATH` early, this script *will* see pending images sitting on disk
before they clear review.

Unlike the repository methods above, this is **not** a simple "default to active" fix — ingestion
deliberately needs to run OCR on `pending` images partway through its own review flow (a lightweight
OCR pass ahead of loose-threshold duplicate review, so reviewers have text to compare — see
`2026-07-24-ingestion-pipeline-design.md`'s Tier B). So this script needs the same shape of fix as
`build_image_embeddings.py`: an explicit `--status {pending,active,all}` flag (default `active`,
matching today's de facto behavior once the column exists), checked in `io_producer` after
`find_image_by_filename` — process only if `image.status` matches the requested value(s), otherwise
skip (same `tracker.skip()` / `metrics_listener.increment(...)` path already used for other skip
reasons). Ingestion's Tier B pre-pass calls it with `--status pending`; routine/manual runs keep the
default `--status active`, so a human running the script by hand never accidentally OCRs
not-yet-reviewed images.

### `batch/build_image_embeddings.py` — deliberate exception, needs a mode, not a blanket filter

This is the one script that *must* be able to target pending images — that's the entire point of
ingestion stage 2 (compute embeddings before duplicate review, without waiting for promotion).
Rather than leaving it filter-less (today's behavior — it has no status awareness at all), give it
an explicit `--status {pending,active,all}` flag (default `active`, matching current de facto
behavior once the column exists) so ingestion's stage-2 call
(`build_image_embeddings.py --status pending --incremental`) is an intentional, visible choice
rather than an accidental gap in filtering.

### Other batch jobs (via `repository/images.py`, fixed once that file is fixed)

`build_tags_from_ocr.py`, `build_tags_from_descriptions.py`, `build_ocr_lemmas.py`,
`build_image_descriptions.py`, `build_concept_embeddings.py`, `build_bow.py` — all consume
`ImagesRepository` methods listed above; once those default to `status="active"`, these scripts
are correct with no changes of their own, *provided* none of them additionally bypass the
repository with a raw `select(Image...)` — worth a grep pass at implementation time to confirm.

### Android client

No client-side change expected — if the backend never returns pending images, there's nothing for
the Android client to filter. Documented here so it isn't independently "discovered" as a gap
during ingestion frontend work.

---

## Verification

A single new integration test, `tests/integration/test_image_visibility.py`, seeds one `active` and
one `pending` image (with an embedding, an OCR row, and a description, so every listed query path
has something to return) and asserts, for every audited method/endpoint above:

- The pending image's id never appears in a default-parameter call.
- The pending image's id *does* appear when `status="pending"` is passed explicitly.

This is a regression safety net, not a static guarantee — the audit list above is the actual
guarantee, kept current by updating this spec (and the test) whenever a new image-returning query
is added. Treat "did I add a row to this test" as part of the review checklist for any PR that adds
a new `select(Image...)`.

---

## Migration / rollout

1. Alembic revision for `images.status` + `ingestion_batch_id` + partial index, depending on the
   `batch_runs` migration.
2. Patch `repository/images.py` methods with the `status` parameter (default `"active"`).
3. Patch `Backend/app/repositories/image_repository.py` and `diagnostics_repository.py` the same
   way.
4. Add `--status {pending,active,all}` to `extract_text_from_memes.py` (default `active`).
5. Add `--status {pending,active,all}` to `build_image_embeddings.py` (default `active`).
6. Add `tests/integration/test_image_visibility.py`.
7. All existing rows backfill to `active` — zero behavior change for the current corpus; the new
   code paths are exercised only once ingestion starts registering `pending` rows.

## Out of scope

- Any UI for browsing pending images, or the actual reject/undo actions — that's the ingestion
  pipeline spec's concern, built on top of the `status` values this spec adds.
- Row-level security or DB-role-based enforcement of the pending/active/rejected split — the repository
  convention + contract test is the chosen mechanism; a DB-level guarantee was considered too heavy
  for a single-operator dev-workstation project and was rejected.
