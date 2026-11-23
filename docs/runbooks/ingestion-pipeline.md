# Runbook: Running the Ingestion Pipeline

Operational how-to for bringing a new batch of images from an inbox directory into the
active, searchable library. For the design rationale and decision log, see
`docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md` and
`docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md`. This
document only covers *how to run it*, not why it's built this way.

All commands below run from the repo root, in the batch venv (`.venv311`), with
`DATABASE_URL` already set in the shell (see CLAUDE.md's Configuration section) and
`--env` set to whichever of `metal` / `general` / `it` you're ingesting into.

## TL;DR

Drop new images into `<BASE_PATH>\inbox\` for the target environment (see
[Where do new images go?](#where-do-new-images-go) below), then run in order:

```powershell
python -m batch.ingest_hash_dedup --env <env>
python -m batch.ingest_validate_formats --env <env>
python -m batch.build_image_embeddings --env <env> --status pending --incremental
python -m batch.extract_text_from_memes --env <env> --status pending
python -m batch.build_ocr_lemmas --env <env> --status pending --incremental
python -m batch.classify_text_heavy --env <env> --status pending
python -m batch.build_ocr_text_embeddings --env <env> --status pending
python -m batch.ingest_find_duplicates --env <env> --tier tier_a
# → review Tier A at /ingestion, keep/reject, submit
python -m batch.ingest_find_duplicates --env <env> --tier tier_b
# → review Tier B at /ingestion, keep/reject, submit
python -m batch.ingest_promote --env <env>
```

Then, separately, run the normal enrichment pipeline (tags, lemmas, descriptions,
concepts) — see [Does NOT cover](#does-not-cover). Full detail on each step, plus
prerequisites and status-checking, is below.

The first eight commands (hash dedup through Tier A find-duplicates) are also available as
one job, `ingest_auto_prep`, and Tier B find-duplicates and promotion as
`ingest_find_duplicates_tier_b` and `ingest_promote`. All three are triggered from the Admin
page, so the whole flow can run without a terminal: see
[Running ingestion from the web UI](#running-ingestion-from-the-web-ui). None of the three is on
a schedule (`scheduler.jobs` deliberately excludes them): Tier B needs Tier A review finished,
and promotion needs Tier B review finished, so auto-running either risks acting on an unreviewed
queue. Only `ingest_abort` is shell-only.

## Running ingestion from the web UI

The same flow with no terminal. Menu names are those of the web app's top bar. The Admin and
Cleanup groups exist on the dev stage only (they are hidden on prod).

1. **Get the files into the inbox.** Either use **Library → Upload** (`/upload`: drag and drop up
   to 50 images of at most 20 MB each, then upload; it writes into `<BASE_PATH>\inbox\` and
   answers `duplicate` for content already in the library), or copy files into the inbox by
   hand (see [Where do new images go?](#where-do-new-images-go)). The Android album sync feeds
   the same inbox.

2. **Prepare: run `ingest_auto_prep`.** Open **Admin → Batches** (`/admin`), find the row
   `ingest_auto_prep`, click **Run**, then click **Confirm?** within a few seconds (a first click
   only arms the button). The row's badge shows the latest run's status and the page refreshes
   itself while a run is `running`; the table below lists every run with its error text, if any.
   This one job does what the first eight commands do, in order: hash dedup, format validation,
   CLIP embeddings, OCR, OCR lemmas, text-heavy classification, OCR-text embeddings and the
   Tier A duplicate search, always on the `pending` images of the current batch.
   - OCR and embeddings are slow for a big batch. Wait for `ingest_auto_prep` to show
     `completed` before moving on.
   - If it shows `failed`, read the error in the runs table, fix the cause and click **Run**
     again: every step is safe to re-run and the job joins the active ingestion run.
   - If one of its steps is already running (for example you started `build_ocr_lemmas` from the
     same page), the job stops quietly without running Tier A. Wait for the other run to finish,
     then trigger `ingest_auto_prep` again.
   - Do **not** run the individual rows `build_image_embeddings`, `extract_text_from_memes`,
     `build_ocr_lemmas`, `classify_text_heavy` or `build_ocr_text_embeddings` to prepare a batch:
     from the Admin page they work on `active` images, not `pending` ones. There is also no
     separate button for hash dedup or format validation; `ingest_auto_prep` is the only web
     route to them.

3. **Review Tier A.** Open **Cleanup → Ingestion** (`/ingestion`). The header shows the run's
   stage; at `tier_a_review` the page lists Tier A clusters (near-identical to something).
   Keep or reject every pending member, then use a cluster's own Submit, or the floating
   **Submit all decisions** bar (click once, then **Confirm?**). Partial decisions are fine;
   undecided images stay `pending`. A page that says "No ingestion run is currently in progress"
   means step 2 has not registered anything yet.

4. **Find Tier B candidates.** Back on **Admin → Batches**, run `ingest_find_duplicates_tier_b`
   (Run, then Confirm?). When it completes, the run moves to `tier_b_review`.

5. **Review Tier B.** Reload `/ingestion`: the same page now shows the Tier B queue, one card
   per image (OCR text is the main signal). Decide and submit as in step 3.

6. **Promote.** On **Admin → Batches**, run `ingest_promote`. Every pending image with no
   unresolved candidate becomes `active`. If undecided images remain, the run stays open; review
   them on `/ingestion` and run `ingest_promote` again (re-running is a no-op for what is
   already resolved). With no active run the job shows `failed`, on purpose.

7. **Follow-up jobs (all on Admin → Batches, all manual).** Ingestion stops at `active` with an
   embedding and OCR text. Then run:
   - `description_pipeline` (tags from OCR, descriptions, tags from descriptions, meme
     profiles and their embeddings); descriptions are capped per run, so re-trigger until the
     backlog is empty.
   - optional: `rebuild_duplicates` (chains into `clusterize`) so the new images' duplicate
     relationships show in Explore → Duplicates.

**What you cannot do from the UI:** abandon a run (`python -m batch.ingest_abort --env <env>`),
run hash dedup or format validation on their own, or start a new batch before the current run is
promoted or aborted (one active run per environment).

**More files mid-review.** Upload or copy them into the inbox and run `ingest_auto_prep` again;
it joins the active run. Do this before the run reaches `tier_b_review` where you can: Tier A
candidates found for new images after that point exist in the database but do not show in the
UI (the page picks its queue from the run's stage), so those images cannot be reviewed there
(see `docs/batches/use-cases/ingestion.md`, Gotchas).

## Scope

### Covers

- **Stage 1 — hash dedup**: exact byte-identical duplicates, both within the new batch and
  against the existing active library.
- **Stage 2 — embeddings + OCR, then Tier A review**: CLIP embeddings for the new images,
  OCR text, and human review of tight-threshold (thumbnail-level) near-duplicate candidates.
- **Stage 3 — Tier B review**: human review of loose-threshold near-duplicate candidates
  (same images, wider net) using OCR text as the primary signal.
- **Stage 4 — promotion**: flips images that cleared both tiers from `pending` to `active`.

After promotion, a promoted image has: a CLIP embedding, OCR text, and `status=active`.
That's the full extent of what ingestion guarantees.

### Does NOT cover

Ingestion does not run any of the downstream enrichment pipeline. These remain separate,
manually-invoked batch jobs that must be run afterward — same as for any other images —
to make newly-promoted images fully tagged/searchable:

- `description_pipeline` — the one admin-triggerable driver that runs the next five items
  and the meme-profile jobs (manual-trigger only, not scheduled; see CLAUDE.md)
- `build_tags_from_ocr` — rule-based tags from OCR text
- `build_image_descriptions` — LLM image descriptions (optional, expensive)
- `build_tags_from_descriptions` — rule-based tags from descriptions
- `build_concept_embeddings` — concept CLIP embeddings + mappings
- `build_bow`, `build_lemma_clusters`, `draft_concepts_from_clusters` — concept-discovery
  tooling
- `detect_entities_and_tag`, `tag_images_from_concepts`
- `trends_batch` — entirely separate subsystem, unrelated to image ingestion

None of these need ingestion-specific flags — they already default to (or explicitly
filter on) `status=active`, so a freshly-promoted image is picked up automatically the
next time you run the normal batch pipeline in its documented order (see CLAUDE.md's
"Batch pipeline" section). There is no single command that does this for you; run them
yourself once you're done promoting (from the web UI, `description_pipeline` on Admin → Batches
covers most of them in one click).

One thing ingestion *does* already take care of, so you don't need to redo it: the active
library's duplicate index (`tmp_duplicates`) already has full coverage for newly-promoted
images, since Tier A/B search used the same `k`/corpus logic a normal incremental
`rebuild_duplicates` run would use. You only need to run `clusterize.py` afterward if you
want these images' duplicate relationships to show up in the Explore → Duplicates browse
page — that page reads from `tmp_clusters`, which is a separate index rebuilt from
`tmp_duplicates` each time `clusterize.py` runs.

Likewise, `build_ocr_lemmas` (per-image lemma index that powers smart search, and, since
2026-09-19, the OCR-text duplicate-matching corroboration check — see
`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`) is already run for
these images as part of Stage 2 below (step 6, before Tier A review), since Tier A/B's own
OCR-text probe needs it — you don't need to rerun it for freshly-promoted images either.

## Where do new images go?

`PATH_INGESTION_SOURCE` (the inbox) is a **subdirectory of `BASE_PATH`**, not a separate
top-level directory — each environment's `.env.<env>` sets it to `<BASE_PATH>\inbox`:

| Environment | Drop new images into            |
| ----------- | -------------------------------- |
| `metal`     | `...\MetalMemes\inbox\`          |
| `general`   | `...\Важные переговоры 2\inbox\` |
| `it`        | `...\ITmemes\inbox\`             |

(Exact `BASE_PATH` values live in `environments/.env.<env>` — gitignored, not reproduced
here.) Stage 1 (`ingest_hash_dedup`) is what moves survivors *out* of `inbox\` and into
`BASE_PATH` itself (the root, alongside every other active image) — until that script
runs, files just sit in `inbox\` untouched.

The shell route below is the same flow as the web one: steps 2-9 are exactly what
`ingest_auto_prep` chains, and steps 11 and 13 are the `ingest_find_duplicates_tier_b` and
`ingest_promote` buttons.

## Prerequisites (one-time per environment)

- `PATH_INGESTION_SOURCE` set in `environments/.env.<env>` and the directory created on
  disk (see table above).
- `content_hash` is backfilled for the existing active corpus (run
  `detect_file_duplicates.py --env <env>` once if you've never run it) — otherwise Stage
  1's cross-corpus hash check has nothing to compare new files against.

## Running a batch

1. Drop new image files directly into `<BASE_PATH>\inbox\` (i.e. `PATH_INGESTION_SOURCE`;
   not a further subdirectory of it — Stage 1 only scans the top level).

2. **Stage 1 — hash dedup:**
   ```powershell
   python -m batch.ingest_hash_dedup --env <env>
   ```
   Exact-hash duplicates (in-batch and cross-corpus) move to
   `<BASE_PATH>\inbox\duplicates\`. Survivors are registered as `pending` `Image` rows and
   moved into `BASE_PATH` itself — this is the step that actually leaves the inbox.
   Safe to re-run at any point while a run is already active — joins the run instead of
   refusing (see Concurrency below).

3. **Validate/fix image formats:**
   ```powershell
   python -m batch.ingest_validate_formats --env <env>
   ```
   Fixes any file whose extension doesn't match its real content (renamed in place) and
   converts any WebP content to JPEG regardless of its current extension (original moved
   to `<BASE_PATH>\converted_originals\` for audit). An unreadable file is flagged via
   `ImageExtras` for human review instead of being touched. Safe to re-run.

4. **Embeddings for the new pending images:**
   ```powershell
   python -m batch.build_image_embeddings --env <env> --status pending --incremental
   ```

5. **OCR pre-pass** — run this *before* Tier A, not between the tiers (empirical finding:
   Tier A's "thumbnails alone are decisive" assumption doesn't hold for all memes, so both
   tiers need OCR text available for review — see Decision #10 in the design spec):
   ```powershell
   python -m batch.extract_text_from_memes --env <env> --status pending
   ```

6. **OCR-lemma index** — needed before either tier's find-duplicates step, so the OCR-text
   probe's lexical-overlap corroboration check (against `ocr_lemmas`) has coverage for these
   images; no ordering dependency on text-heavy classification, so it runs directly after OCR
   (see `docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`):
   ```powershell
   python -m batch.build_ocr_lemmas --env <env> --status pending --incremental
   ```
   `--status pending` requires `--incremental` — a full (non-incremental) reprocess
   unconditionally deletes the *entire* `ocr_lemmas` table, not just pending-status rows.

7. **Text-heavy classification** — needed before either tier reviews, so the review UI's
   `text_heavy` badge is populated for these images (purely additive metadata; doesn't
   affect matching itself):
   ```powershell
   python -m batch.classify_text_heavy --env <env> --status pending
   ```

8. **OCR-text embeddings** — needed before either tier's find-duplicates step, so
   text-heavy-vs-text-heavy pairs get OCR-text-based matching instead of losing coverage
   (see `docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md`):
   ```powershell
   python -m batch.build_ocr_text_embeddings --env <env> --status pending
   ```

9. **Tier A — tight-threshold candidates:**
   ```powershell
   python -m batch.ingest_find_duplicates --env <env> --tier tier_a
   ```

10. **Review Tier A** in the browser at `/ingestion` (use whichever origin is CORS-allowed
    for this environment's frontend — see `environments/Environments.md`; e.g. metal's LAN
    origin, not `127.0.0.1`, if that's what `.env.metal` declares). Keep/reject each pending
    member; submissions can be partial (not every cluster needs a decision in one pass). A
    "Submit all decisions" button submits every cluster with at least one decision in one
    request, instead of clicking each cluster's own Submit button individually.

11. **Tier B — loose-threshold candidates:**
    ```powershell
    python -m batch.ingest_find_duplicates --env <env> --tier tier_b
    ```

12. **Review Tier B** on the same `/ingestion` page — it switches queues automatically based
    on the run's current stage.

13. **Promote:**
    ```powershell
    python -m batch.ingest_promote --env <env>
    ```
    Promotes every pending image with no remaining unresolved duplicate candidate in either
    tier's band. Marks the whole run `completed` only once zero pending images remain in the
    batch — otherwise it's safe and expected to re-run this later, after more review, as a
    no-op for anything already resolved.

14. **(Optional)** if you want the promoted images' duplicate relationships to appear in
    Explore → Duplicates right away:
    ```powershell
    python -m batch.rebuild_duplicates --env <env>
    ```
    `rebuild_duplicates` chains straight into `clusterize` when it finishes (matching the
    `move_flagged`→`unregister_deleted_images` chaining pattern) — pass `--no-chain` to run
    only the duplicate-candidate rebuild. Both are also independently admin-triggerable from
    `/admin/batches` as `rebuild_duplicates` and `clusterize`.

15. **Run the rest of the enrichment pipeline** (see "Does NOT cover" above and CLAUDE.md's
    Batch pipeline section for the full order) to tag, index, and describe the
    newly-active images.

## Checking status

- `GET /api/ingestion/run` — current run's id/stage/stats (404 if none active).
- `GET /api/ingestion/pending` — still-pending images in the current batch.
- `/statistics` page — corpus-wide pending/rejected counts.

## Handling a mistaken decision

- `POST /api/ingestion/images/{image_id}/undo-reject` reverts a rejected image back to
  `pending` and moves its file back from `BASE_PATH/rejected/`. This only works while the
  image is still `rejected` — undoing a "Keep" decision that later got promoted to `active`
  is out of scope; there is no built-in path to demote a promoted image. Undo-reject also
  reverses the popularity edge that was recorded at reject.

- `python -m batch.ingest_abort --env <env>` abandons the entire currently active run
  instead of continuing it: it undoes every `pending`/`rejected` image the run registered
  (moves each file back to `PATH_INGESTION_SOURCE`, deletes its row — FK cascades clean up
  embeddings/OCR/tmp_duplicates automatically; popularity edges into surviving images are reversed) and marks the run `aborted`, freeing the
  one-active-run-per-kind lock. Never touches already-`active` (promoted) images in the
  batch. Can be run at any point before the run completes — right after Stage 1, mid-review,
  etc.

- **Abort doesn't undo a WebP conversion.** An image whose format step 3 fixed is restored
  under its *current* `filename` — for a converted WebP that's the new JPEG, not the
  original bytes. The original WebP stays in `<BASE_PATH>\converted_originals\` with no
  database record pointing at it, so an aborted, format-fixed image isn't returned to its
  exact original state; recover the original by hand from that directory if you need it.

## Concurrency

Only one ingestion run can be active at a time per environment, enforced by the database's
one-active-run-per-kind partial unique index (not by any script itself). Starting a second
batch requires finishing (through promotion) or otherwise resolving the current one first —
run `python -m batch.ingest_abort --env <env>` to abandon a stuck or unwanted run and free
the lock.

`ingest_hash_dedup.py` can now be re-run at any point while a batch is active to pick up
newly-dropped files, instead of being blocked — it joins the active run (same `batch_id`,
stats accumulate across invocations) rather than refusing. After doing so, re-run
`ingest_validate_formats.py --env <env>`, `build_image_embeddings --status pending
--incremental`, `extract_text_from_memes --status pending`, `build_ocr_lemmas --status
pending --incremental`, `classify_text_heavy --status pending`, `build_ocr_text_embeddings
--status pending`, and `ingest_find_duplicates.py`
(both tiers, as applicable) — steps 3-9 and 11 in "Running a batch" above — so the
newly-added images get review coverage; all seven are already safe to re-run against the
same batch.
**Don't skip the embeddings step**:
`ingest_find_duplicates.py`'s probe is an inner join against `embeddings`, so an image with
none is silently excluded from review entirely and could reach `ingest_promote.py`
unreviewed. Concurrent *invocations of the same* `ingest_hash_dedup.py` script are
serialized via a Postgres advisory lock, so a second operator's simultaneous re-run attempt
gets a clear "try again shortly" error rather than racing on `PATH_INGESTION_SOURCE`'s
filesystem state — but this lock doesn't cover `ingest_promote.py`/`ingest_abort.py`, so
avoid running either of those concurrently with an `ingest_hash_dedup.py` re-join.

When using the `ingest_auto_prep` job, also don't start `build_ocr_lemmas`, `classify_text_heavy` or
`build_ocr_text_embeddings` (each is separately admin-triggerable) while it is in flight: those three write their own
run rows, and if one of them is already running the driver fails with `BatchAlreadyRunningError` instead of skipping
it. See `docs/batches/execution-model.md`.
