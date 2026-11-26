# Ingestion Tier B text filter

status: approved
Originates from: `docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md` and
`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md` (this extends the lemma
corroboration idea from the OCR-text probe to the pairs the CLIP probes produce).

## Intent

Tier B review on `general` holds about 50,000 open candidate pairs across 3,000+ pending images
(measured 2026-11-26, read-only). Most are not duplicates: CLIP at 0.05-0.12 conflates "same
meme template" with "same meme" (see the 2026-09-18 note in `environments/settings.yaml`). OCR text
is already extracted at this stage and is the cheapest signal that two memes differ. The owner
wants the reviewer's queue cut to pairs that plausibly are duplicates, by settling the clearly
different ones automatically.

Measured on `general`: 33,450 pairs share no lemma at all, 45,500 of ~50,000 (90%) overlap below
0.2, 2,503 have fewer than 3 lemmas on a side; only ~2,000 pairs (about 2,100 images at most) have
overlap of 0.2 or more.

## Decision (owner, 2026-11-26)

Pairs whose text says "different" are **auto-kept**: settled as "not a duplicate", never shown.
Text embeddings exist for only ~12% of images (text-heavy only), so they are a veto, not the main
signal.

## Goals / non-goals / anti-goals

Goals
- Cut the Tier B queue by settling clearly different pairs, per pair.
- Fully reversible: every auto-kept pair is tagged with its run and can be reopened.
- A dry run with an eyeball-able sample comes before any apply.

Non-goals
- Tier A (its pairs are < 0.05, text already corroborates them in the OCR probe).
- Changing which pairs the probes insert; computing text embeddings for non-text-heavy images.
- Using text to *add* candidates or to reorder the remaining queue.

Anti-goals (failures even if it "works")
- A real duplicate silently kept (OCR-damaged, cropped caption, translated copy).
- An image promoted while it still has an unreviewed plausible duplicate.
- Reading or settling a pair when either side has too little text to judge.

## Rule (a pair is auto-kept only if ALL hold)

1. Tier B band pair, `tier_b_reviewed_at IS NULL`, the subject side is `pending` in the run, the
   other side is not `rejected`. Same pair set as `list_tier_b_review_page`.
2. Both images have at least `min_lemmas` (3) lemmas in `ocr_lemmas` (same floor as
   `MIN_LEMMA_COUNT_FLOOR`). Fewer = no text signal = pair stays.
3. Overlap coefficient `shared / min(n1, n2)` is below `max_overlap` (0.2, the same constant the
   OCR probe requires for *insertion*, so a pair that would not have been inserted by the text probe
   is the one settled here).
4. Embedding veto: if both images have an `ocr_text_embeddings` row and their cosine distance is
   below `veto_distance` (0.10, `TEXT_EMBEDDING_LOOSE_THRESHOLD`), the pair stays.
5. The pair's own `distance_source` is not `ocr_text` (an `ocr_text` pair already passed the lemma
   check on insertion; it is never auto-kept).

Settling sets `tier_b_reviewed_at = now()` on **that pair only** (not every pair on the image as
the reviewer's keep does) and `tier_b_auto_kept_run = <run id>`. No image is rejected, no file moves,
no popularity edge, no metrics `kept` event for the image (the image's other pairs are untouched).

## Approaches considered

- **A. Lemma overlap only.** Simplest. Same OCR-noise risk, no second opinion.
- **B. Lemma overlap + embedding veto (chosen).** The veto covers only ~7% of low-overlap pairs but
  those are exactly the text-heavy ones where lemma overlap is least trustworthy.
- **C. Embed all text, use embedding as the main signal.** Bigger batch run, and text embeddings are
  weak on text-light memes. Rejected for now; the dry run report will show whether it is needed.
- **Where it runs.** At probe-insertion time would change `ingest_find_duplicates` for every
  environment and cannot be reversed per run; at review time would cost a query per page. A separate
  job over the open pairs is reversible and inspectable. Chosen.

## Design

- **Migration**: `tmp_duplicates.tier_b_auto_kept_run UUID NULL` plus a partial index
  `WHERE tier_b_auto_kept_run IS NOT NULL`. `tmp_duplicates` is already class X in
  `sync/registry.py` (rebuildable), so only the registry page needs `python -m sync.render --write`
  if it lists columns; `tests/sync/` and `tests/docs/` must stay green.
- **Job `ingest_text_filter`** (`batch/ingest_text_filter.py`, kind `ingest_text_filter`), registered
  in `environments/batch_registry.yaml`, `docs/batches/inventory.md`, and the CLAUDE.md batch list.
  - Admin-triggerable from `/admin/batches` (manual only, not scheduled) and **always a dry run
    there**: the admin contract passes no parameters, and an irreversible-feeling action must not be
    one click.
  - CLI: `python -m batch.ingest_text_filter --env general` (dry run), `--apply --plan-hash H`
    (applies exactly what the matching dry run reported; refuses if the open pair set changed so the
    hash no longer matches), `--undo --run-id R` (clears `tier_b_reviewed_at` and
    `tier_b_auto_kept_run` for pairs of run R that are still auto-kept).
  - Dry run writes a report under `Storage/ingestion-text-filter/<run id>/` (git-ignored): counts by
    bucket, the plan hash, and a random sample (default 100) of would-be-kept pairs with both OCR
    texts, filenames, CLIP distance, overlap and embedding distance, plus a second sample from the
    0.2-0.3 overlap band that stays, to show where the line is. OCR text stays local to this file;
    it never goes into logs or metrics.
  - Run-tracked with stats `pairs_open`, `pairs_eligible`, `pairs_vetoed`, `pairs_no_text`,
    `pairs_applied`; progress via `report_progress` per the batch-progress convention.
- **Settings** `ingestion_text_filter.*` in `environments/settings.yaml`: `min_lemmas: 3`,
  `max_overlap: 0.2`, `veto_distance: 0.10`, `sample_size: 100`. Available on all three
  environments (owner decision, 2026-11-26): the job only reads `ocr_lemmas`, and rule 2 skips any
  pair without enough lemmas, so on an environment with none it settles nothing.
- **Queue effects**: the review queue, progress counts and `ingest_promote` already treat a pair with
  `tier_b_reviewed_at` set as settled, so no query change is needed. `get_review_progress` and the
  Tier B page need no edit; a test pins that an auto-kept pair disappears from both.
- **Docs**: `docs/batches/use-cases/ingestion.md` (a step between Tier B discovery and review),
  `docs/runbooks/ingestion-pipeline.md` (dry run, read the report, apply, undo), `backend_api.md`
  unchanged (no endpoint change).

## Open numbers, to be set from the first dry run, not now

`max_overlap` 0.2 and `veto_distance` 0.10 are the existing constants; if the sample shows real
duplicates among the would-be-kept pairs, tighten before applying. The first apply on `general` is
a manual step by the owner after reading the report.

## Pipeline placement

Manual and optional; not part of `ingest_auto_prep` and not scheduled. Order: Tier B find-duplicates,
`ingest_text_filter` dry run, read the report, `--apply`, review the remaining Tier B queue,
`ingest_promote`. Skipping it leaves Tier B exactly as today. Also rerunnable: pairs inserted later
(new Tier B discovery on a joined run) are picked up by a new dry run.

## Left to the builder

Report file format (JSON + a readable sheet), the SQL shape (a single set-based statement is
required: a per-pair Python loop over 50,000 pairs is not acceptable, and the measurement query
using correlated subqueries took over two minutes, so use a join on a per-image lemma-count CTE),
the plan-hash definition, and test layout. Tests: unit for the rule (each of the five conditions),
integration against `ocrdb_test` for apply, undo and queue disappearance, and the full
`tests/integration/` root since `ocr_lemmas` consumers are shared.
