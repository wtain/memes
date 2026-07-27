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
python -m batch.build_image_embeddings --env <env> --status pending --incremental
python -m batch.extract_text_from_memes --env <env> --status pending
python -m batch.ingest_find_duplicates --env <env> --tier tier_a
# → review Tier A at /ingestion, keep/reject, submit
python -m batch.ingest_find_duplicates --env <env> --tier tier_b
# → review Tier B at /ingestion, keep/reject, submit
python -m batch.ingest_promote --env <env>
```

Then, separately, run the normal enrichment pipeline (tags, lemmas, descriptions,
concepts) — see [Does NOT cover](#does-not-cover). Full detail on each step, plus
prerequisites and status-checking, is below.

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

- `build_tags_from_ocr` — rule-based tags from OCR text
- `build_ocr_lemmas` — per-image lemma index that powers smart search
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
yourself once you're done promoting.

One thing ingestion *does* already take care of, so you don't need to redo it: the active
library's duplicate index (`tmp_duplicates`) already has full coverage for newly-promoted
images, since Tier A/B search used the same `k`/corpus logic a normal incremental
`rebuild_duplicates` run would use. You only need to run `clusterize.py` afterward if you
want these images' duplicate relationships to show up in the Explore → Duplicates browse
page — that page reads from `tmp_clusters`, which is a separate index rebuilt from
`tmp_duplicates` each time `clusterize.py` runs.

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
   Refuses to start if an ingestion run is already active for this environment — finish or
   promote the existing one first.

3. **Embeddings for the new pending images:**
   ```powershell
   python -m batch.build_image_embeddings --env <env> --status pending --incremental
   ```

4. **OCR pre-pass** — run this *before* Tier A, not between the tiers (empirical finding:
   Tier A's "thumbnails alone are decisive" assumption doesn't hold for all memes, so both
   tiers need OCR text available for review — see Decision #10 in the design spec):
   ```powershell
   python -m batch.extract_text_from_memes --env <env> --status pending
   ```

5. **Tier A — tight-threshold candidates:**
   ```powershell
   python -m batch.ingest_find_duplicates --env <env> --tier tier_a
   ```

6. **Review Tier A** in the browser at `/ingestion` (use whichever origin is CORS-allowed
   for this environment's frontend — see `environments/Environments.md`; e.g. metal's LAN
   origin, not `127.0.0.1`, if that's what `.env.metal` declares). Keep/reject each pending
   member; submissions can be partial (not every cluster needs a decision in one pass).

7. **Tier B — loose-threshold candidates:**
   ```powershell
   python -m batch.ingest_find_duplicates --env <env> --tier tier_b
   ```

8. **Review Tier B** on the same `/ingestion` page — it switches queues automatically based
   on the run's current stage.

9. **Promote:**
   ```powershell
   python -m batch.ingest_promote --env <env>
   ```
   Promotes every pending image with no remaining unresolved duplicate candidate in either
   tier's band. Marks the whole run `completed` only once zero pending images remain in the
   batch — otherwise it's safe and expected to re-run this later, after more review, as a
   no-op for anything already resolved.

10. **(Optional)** if you want the promoted images' duplicate relationships to appear in
    Explore → Duplicates right away:
    ```powershell
    python -m batch.clusterize --env <env>
    ```

11. **Run the rest of the enrichment pipeline** (see "Does NOT cover" above and CLAUDE.md's
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
  is out of scope; there is no built-in path to demote a promoted image.

## Concurrency

Only one ingestion run can be active at a time per environment, enforced by
`ingest_hash_dedup.py`. Starting a second batch requires finishing (through promotion) or
otherwise resolving the current one first.
