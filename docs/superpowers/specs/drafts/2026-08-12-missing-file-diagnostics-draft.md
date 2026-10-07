# Missing-File Diagnostics for Active Images — Placeholder

Status: draft / placeholder — not researched in depth, not ready for brainstorming or
implementation. Captured now so the idea isn't lost; revisit as a standalone spec later.
Originates from: investigation of two `general`-environment images
(`af272f78-7fbe-405e-aa07-ea153cc4ecb9`, `d24fecfa-17f0-4b87-b00b-da2308f7fa30`) whose DB
rows exist but whose files don't, 2026-08-12. That investigation turned up 896 such rows
in `general` alone (0 in `metal`/`it`) — see the conversation for the full write-up. This
draft is about the tooling gap the investigation exposed, not about that specific incident.

## The gap

`batch/tasks/SourceTasks.py`'s `UnregisterNonExisting` (driven by
`unregister_deleted_images.py`) is the only existing code that checks "does this active
image's file actually exist on disk" — and its only action is to immediately delete the
row (cascading to embeddings/OCR/tags) the moment it finds one missing. There is no
read-only / report-only way to ask "how many active images are currently missing their
file, and what do they have in common?" without either (a) writing a one-off script each
time (which is what this investigation had to do), or (b) running the destructive cleanup
and only learning the shape of the problem from stdout as rows are deleted.

This mattered for the actual investigation: distinguishing "two isolated missing files"
from "896 missing files, all sharing one registration path and a 15-hour window, isolated
to one of three environments" was the entire finding — and doing that required standing up
an ad hoc script that cross-referenced `images.filename` against the filesystem for the
whole active corpus, then correlated the missing set against `ingestion_batch_id`,
`created_at`, `image_extras.flagged`, and the min/max `created_at` across
`ocr_texts`/`embeddings`/`image_descriptions` (as a "last confirmed the file was still
present and readable" signal, since none of those tables record it directly).

## Idea

A read-only diagnostic script (e.g. `batch/diagnose_missing_files.py`, or a `--report-only`
/ `--dry-run` flag on `unregister_deleted_images.py` itself) that:

- Scans active (and maybe pending) images for a missing file, same check
  `UnregisterNonExisting` already does, but never deletes anything.
- For each missing row, reports: `id`, `filename`, `created_at`, `ingestion_batch_id`
  (NULL vs set distinguishes the legacy direct-registration path from the new ingestion
  pipeline), whether `image_extras.flagged`, and the latest `created_at` across
  `ocr_texts`/`embeddings`/`image_descriptions` as a "last known good" timestamp.
- Summarizes instead of just listing: counts by day, by `ingestion_batch_id` (NULL vs
  not), by flagged/not — so a pattern like "one 15-hour registration burst, 100% missing,
  isolated to one environment" is visible immediately instead of requiring a bespoke
  script each time this comes up.

Out of scope for the idea itself (needs its own thinking if pursued): whether this should
also cross-check `converted_originals/`/`excluded/`/`rejected/`/`possible_duplicates/` (the
investigation had to hand-roll that too), and whether "last known good" should be a real
column on `images` (updated by whichever job last successfully opened the file) rather than
recomputed from three other tables each time.

## Resolution of the incident this draft originates from

891 of the 896 missing rows turned out to be sitting, byte-identical (confirmed via
`content_hash`), in `Old - 1 - memes` — a sibling folder under `DCIM`, *outside*
`general`'s `BASE_PATH` tree entirely. The user had reorganized a batch of photos there
manually; nothing was deleted. This means a future version of the idea above should
consider searching sibling directories under the parent of `BASE_PATH` (not just
`BASE_PATH`'s own known subfolders) before concluding a file is actually gone — a "moved
out of the managed tree entirely" case is at least as likely as a true deletion, per this
incident's 99.4% hit rate. The remaining 5 (generic/downloaded-looking filenames, not
camera-roll style) were not found anywhere under all of `DCIM` and remain unresolved.

## Follow-on bug found while chasing this live: `ingest_validate_formats.py` never clears a stale `flagged` it set

Confirmed against `general`'s real, in-progress ingestion run (2026-08-14): 11 pending
images got `ImageExtras.flagged = True, remarks = "unreadable during format validation"`
set by `batch/utils/image_format_apply.py`'s `apply_format_fix` because their file wasn't
at `BASE_PATH` at the exact moment validation ran (same external sync race this whole
incident is about) — the images themselves were never actually corrupt, confirmed by
restoring the file from `Old - 1 - memes` and finding it byte-identical to what the DB
already expected. `apply_format_fix` only ever calls `set_flagged(image_id, True, ...)` on
the unreadable branch; the `no_op`/`renamed`/`converted` branches never call
`set_flagged(image_id, False)` to clear a previous flag if a later pass finds the same
image readable. So a transient failure (missing file, momentary lock, anything) leaves a
permanent false-positive flag unless someone manually clears it — which is what happened
here (cleared by hand via a one-off `UPDATE image_extras SET flagged = false, remarks =
NULL ...` after confirming all 11 were genuinely fine). A real fix belongs in
`batch/utils/image_format_apply.py`: either clear the flag on any non-unreadable outcome
for an image that was previously flagged by this same remarks string, or make the
maintenance batch (`fix_image_formats.py`) re-check and clear stale flags on its own
no-op/renamed/converted paths. Needs its own scoping — not done as part of this draft.

## Why not built now

The user asked to investigate two specific images and note tooling gaps for later, not to
build the tool. No design discussion has happened on shape/scope/naming.
