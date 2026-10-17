# R1: Data model and status registry - design

Status: implementation
Plan: docs/superpowers/plans/2026-10-17-r1-data-model-and-status-registry.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R1)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md

R1 lays the foundation of the screenshot recut epic: the `image_cuts` relation, the `cut_proposed` image status, the `stale` marker
on the derived tables that need it, a shared helper for stale rows, the status registry, and the inventory of everything that hangs
off an image. R1 contains no detection, no cut logic, no review UI, no metrics and no change to the behaviour of any batch job.

## 1. Decisions carried into this design

| # | Decision | Source |
|---|---|---|
| 1 | The cut has its own status `cut_proposed`; `pending` stays ingestion's | ADR-001 |
| 2 | One status registry, `docs/image-status.md`, defines every image status; other documents link to it | epic D6 |
| 3 | Derived data is inherited in three groups: copy, recompute, copy then refresh | epic D3 |
| 4 | Staleness is a boolean column, added only where staleness cannot already be derived | ADR-001, refined here (section 5) |
| 5 | OCR rows are copied with polygons shifted by the crop origin; rows outside the crop are dropped | user, 2026-10-17 |
| 6 | The cut inherits `created_at` from the source | user, 2026-10-17 |
| 7 | The box is stored from day one so box adjustment (R6) needs no migration | epic D2 |

## 2. Audit results

Resolved by reading the code and, for data questions, by read-only queries (`DATABASE_URL_READONLY`) on all three environments on
2026-10-17.

| # | Question | Result |
|---|---|---|
| A1 | Which tables hang off `images`? | 18 tables with a direct foreign key: `image_metrics`, `ocr_texts`, `image_descriptions`, `embeddings`, `tmp_duplicates` (two FKs), `tmp_clusters`, `duplicate_decisions` (two FKs), `tags`, `ocr_lemmas`, `processing_errors`, `image_processing_status`, `image_extras`, `image_classifications`, `ocr_text_embeddings`, `description_notes`, `description_note_lemmas`, `meme_profiles`, `meme_text_ratings`. 6 more hang off those: `image_description_embeddings`, `image_description_feedback`, `description_lemmas` (via `image_descriptions`), `description_note_embeddings` (via `description_notes`), `meme_profile_values`, `meme_profile_situation_embeddings` (via `meme_profiles`). `images.ingestion_batch_id` points at `batch_runs`. `concept_*` tables do not reference `images`. Tables created by older migrations and since dropped are irrelevant. |
| A2 | Tag sources in use | `OCR`, `Ollama`, `YOLO`, `CONCEPT`. `Note` is in the code (`build_tags_from_notes`) but has no rows yet. No manual or human-sourced tags exist on any environment. |
| A3 | Shape of `ocr_texts.bbox` | Every row on all three environments (about 1.4 million) is a 4-point polygon `[[x,y],[x,y],[x,y],[x,y]]` of integers, in source pixel coordinates. In practice an axis-aligned rectangle. |
| A4 | Box partly inside the crop | Rule adopted: keep a row when the centre of its polygon lies inside the crop; clip its points to the crop; drop otherwise. |
| A5 | PostgreSQL version | 18.3 on metal, general and IT. Adding `NOT NULL DEFAULT false` is a metadata-only change, no table rewrite. |
| A6 | How the group 3 jobs select work | By absence, not by staleness: OCR by the `image_processing_status` marker `easyocr:en`; `build_ocr_lemmas` by images with no lemmas (and marker `ocr_lemmas`); `build_tags_from_ocr` and `build_tags_from_descriptions` by images with no tags of their source; `build_image_descriptions` by missing (image, prompt) pairs. So a copied row makes the cut invisible to every job, which is why an explicit mode is needed (R3). |
| A7 | Ratings | `meme_text_ratings` has 38 rows on general, 1 on metal. Copying them at cut time would give the cut a rating before anyone could have seen it, and `export_ratings` would offer the same judgement for a second image. Decision: ratings are not copied at cut time. On approve (R5), the source's ratings are moved to the cut (`UPDATE ... SET image_id`) before the source is removed. On reject they stay with the source. |
| A8 | Processing-status markers | `image_processing_status` holds one row per image and pipeline: `easyocr:en`, `ocr_lemmas`, `meme_profile` (its `done` and `failed` rows), and `image_description:<prompt key>` (failure records). The marker is what makes the OCR job skip an image. Decision: markers are not copied. A marker that says "done" for rows that are really inherited would be untrue, and the refresh mode (R3) selects by stale rows and bypasses the marker. R3 designs how the two interact. |
| A9 | Evaluation corpus | `evaluation/gold/corpus.json` freezes 1,500 `general` images, and `judgments.json` and the stored baselines are keyed by image id and `content_hash`. Approving a cut of a corpus image would remove the source and break that data. Decision: detection (R2) skips every image listed in `corpus.json` when running for the corpus environment (new requirement FR-17). |

## 3. Schema

One Alembic revision. It applies to the test database first, then to each of the three live databases (metal, general, IT).

### 3.1 `image_cuts`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `source_image_id` | UUID null | FK `images.id`, `ON DELETE SET NULL` |
| `cut_image_id` | UUID null | FK `images.id`, `ON DELETE SET NULL`, unique |
| `source_content_hash` | String null | Snapshot for audit after the source is gone |
| `crop_x`, `crop_y`, `crop_w`, `crop_h` | Integer not null | Source pixels. Check constraint: `crop_x >= 0`, `crop_y >= 0`, `crop_w > 0`, `crop_h > 0` |
| `detector` | String(50) not null | |
| `detector_version` | String(50) not null | |
| `confidence` | Float null | |
| `detect_run_id` | UUID null | FK `batch_runs.run_id`, `ON DELETE SET NULL`, indexed |
| `state` | String(20) not null | Check constraint over the `CutState` values |
| `created_at` | DateTime tz not null, server default now | |
| `decided_at` | DateTime tz null | |

Indexes:
- partial unique index on `source_image_id` where `state IN ('detected','proposed','approved')`: at most one live proposal per source;
- unique on `cut_image_id` (nulls allowed, as PostgreSQL treats nulls as distinct);
- index on `detect_run_id` (metrics group by it);
- index on `(state)` for the review queue.

The "box inside the source image" rule (FR-2) needs the source dimensions, which live in another table, so it is validated in code by R2 and R3, not by a constraint.

### 3.2 `stale` columns

`stale boolean NOT NULL DEFAULT false` on `ocr_texts`, `ocr_lemmas`, `image_descriptions` and `tags`. Existing rows read `false`. No backfill and no index: stale rows are a tiny minority, and the helper (section 4) selects by `stale` joined to `images.status`. If R3 measures the select as slow on `tags` (the largest table), a partial index `WHERE stale` is added there; it is cheap because almost no row matches.

### 3.3 Rollout

Per environment: back up as usual, run the revision, then deploy code. Old code ignores the new columns and table, so the order is safe in both directions. The revision's `downgrade` drops the table and the four columns.

## 4. Code

- `Storage/models.py`: the `ImageCut` model; `StaleMixin` (the `stale` column) applied to `OCRText`, `OCRLemma`, `ImageDescription` and `ImageTag`; constants classes `ImageStatus` (`ACTIVE`, `PENDING`, `REJECTED`, `CUT_PROPOSED`) and `CutState` (`DETECTED`, `PROPOSED`, `APPROVED`, `REJECTED`, `FAILED`). `images.status` stays a free `String(20)`; no check constraint is added, since that would touch the largest live table for a small gain. Existing string literals in the code base are not rewritten in R1.
- `Storage/stale.py`, three functions, none of which commits (repositories never call `session.commit()`):
  - `images_with_stale_rows(session, model, status)`: ids of images in that status having at least one stale row in `model`;
  - `delete_stale_rows(session, model, image_ids)`: deletes the stale rows of those images, returning the count;
  - `stale_counts(session)`: stale row count per table, for statistics and tests.
- No `repository/image_cuts.py` in R1. Create and transition methods arrive with R2, R3 and R5, the projects that call them.
- No change to any file under `batch/` or `Backend/app/`.

## 5. Staleness: where the column is, and where it is not

A column is added only where staleness cannot already be derived.

| Table | Staleness | Why |
|---|---|---|
| `ocr_texts`, `ocr_lemmas`, `image_descriptions`, `tags` | `stale` column | The jobs select by absence of rows, so copied rows are indistinguishable from real ones (A6) |
| `meme_profiles` and its values and embedding | Derived, no column | `inputs_fingerprint`, taxonomy version, prompt version and model are compared on every run; a copied profile becomes stale as soon as the cut's inputs (refreshed OCR, descriptions) differ. Human-verified profiles are never rewritten, as today |
| `image_description_embeddings`, `image_description_feedback`, `description_lemmas` | Cascade | They hang off an `image_descriptions` row. Refresh deletes the stale description row, the children go with it, and the incremental jobs rebuild embeddings and lemmas for the new row. The human feedback judged the old text and is dropped with it, which is intended |
| `ocr_text_embeddings` | Recomputed (group 2) | Not copied |

## 6. Status registry: `docs/image-status.md`

| Status | Meaning | Set by | Read by |
|---|---|---|---|
| `active` | In the library and searchable | column default; `ingest_promote`; cut approval (R5) | all default queries |
| `pending` | Ingested and awaiting duplicate review | `ingest_hash_dedup` | ingestion review; jobs with `--status pending` |
| `rejected` | Rejected in ingestion review; file moved out of the library | ingestion review | ingestion tooling only |
| `cut_proposed` | A proposed cut awaiting human review | the cut step (R3) | recut review (R4); jobs with `--status cut_proposed` (R3) |

Per status the registry also lists the allowed transitions (`pending → active | rejected`, `rejected → pending` through the ingestion
undo, `cut_proposed → active` on approve, `cut_proposed → removed` on reject) and which jobs accept it through `--status`.

A closing section names things that read like statuses but are not image statuses, each with its own owner: `batch_runs.status`,
`image_processing_status.status`, `image_cuts.state`, the `image_extras.flagged` flag, and the ingestion run status.

Links added in the same change, each replacing any competing definition with a pointer to the registry: `CLAUDE.md` (key invariants
and the ingestion notes), `docs/batches/inventory.md` and `execution-model.md`, `2026-07-25-image-visibility-status-design.md`, and
the ingestion pipeline design. The older specs keep their history and gain a one-line pointer; they are not rewritten.

## 7. Inventory: `docs/image-derived-data.md`

The table below is the content of that document, with the audit results applied. First column: table. A test parses it (section 9).

The `images` row of the cut: new `id` and `filename`; `created_at` inherited from the source; `status = cut_proposed`;
`ingestion_batch_id` null; `width`, `height` and `content_hash` computed from the new file.

| Table | Group | At cut time | Refresh path |
|---|---|---|---|
| `description_notes` | 1 | Copied as is, including `lemmas_built_at` and `embedding_built_at` | none |
| `description_note_lemmas` | 1 | Copied | none |
| `description_note_embeddings` | 1 | Copied | none |
| `tags` where source is `Note` | 1 | Copied | none |
| `image_extras` | 1 | `remarks` copied; `flagged` and `flagged_reason` left empty | none |
| `meme_profiles` where `human_verified` | 1 | Copied | none, never rewritten |
| `meme_profile_values`, `meme_profile_situation_embeddings` of a human-verified profile | 1 | Copied | none |
| `meme_text_ratings` | 1, deferred | Not copied at cut time. Moved to the cut on approve (R5), stay with the source on reject | none |
| `embeddings` (CLIP) | 2 | Not copied | `build_image_embeddings --status cut_proposed` |
| `image_classifications` | 2 | Not copied | `classify_text_heavy --status cut_proposed` |
| `ocr_text_embeddings` | 2 | Not copied | `build_ocr_text_embeddings` after OCR and classification |
| `tags` where source is `YOLO` or `CONCEPT` | 2 | Not copied | `detect_entities_and_tag`, `tag_images_from_concepts` |
| `ocr_texts` | 3 | Copied with the polygon shifted by the crop origin; rows outside the crop dropped; `stale = true` | `extract_text_from_memes`, refresh mode (R3) |
| `ocr_lemmas` | 3 | Copied wholesale, `stale = true`. Lemmas are keyed by image and lemma, not by OCR row, so they cannot be filtered by the crop; lemmas of dropped frame text stay until the refresh | `build_ocr_lemmas`, refresh mode (R3), after the OCR refresh |
| `image_descriptions` | 3 | Copied, `stale = true` | `build_image_descriptions`, refresh mode (R3) |
| `image_description_embeddings`, `image_description_feedback`, `description_lemmas` | 3, via parent | Copied with the description | rebuilt for the new description row by their incremental jobs |
| `tags` where source is `OCR` or `Ollama` | 3 | Copied, `stale = true` | `build_tags_from_ocr`, `build_tags_from_descriptions`, refresh mode (R3) |
| `meme_profiles` (model-made), `meme_profile_values`, `meme_profile_situation_embeddings` | 3, derived | Copied exactly | `build_meme_profiles` and its embeddings job, through the existing fingerprint rule |
| `image_processing_status` | none | Not copied (A8) | the jobs create their own markers |
| `tmp_duplicates`, `tmp_clusters`, `duplicate_decisions` | none | Not copied | the duplicate jobs run as usual (R5 excludes source/cut pairs) |
| `processing_errors`, `image_metrics` | none | Not copied | produced by the jobs |

Because the OCR refresh rewrites `ocr_texts`, the lemma refresh must run after it, and the same holds down the chain. A lemma row
rebuilt before the OCR refresh would be fresh in name only, which is why the order below is part of the contract, not a hint.

Refresh order implied by the table: OCR, then OCR lemmas and OCR tags, then descriptions, then description embeddings, lemmas and
tags, then profile and profile embedding. `description_pipeline` already orders the last part; R3 decides whether a dedicated driver
chains the whole sequence for cut images.

## 8. Cross-cutting rules recorded here for later projects

- The cut step (R3) runs in one transaction: file written, `images` row, `image_cuts` row, group 1 and 3 copies. A failure leaves no partial cut. A file written before a failed commit is removed.
- Approve (R5) moves the source's ratings to the cut in the same transaction as the status change and the source flag.
- Detection (R2) never proposes an image that is itself a cut result, one with a live proposal, one rejected under the same detector version, or one listed in the evaluation corpus (A9).

## 9. Testing

- Migration up and down on `ocrdb_test`; the four `stale` columns exist with default `false`, and existing rows read `false`.
- `image_cuts`: a second live row for one source fails; a rejected row plus a live row passes; a duplicate `cut_image_id` fails; the crop check constraints reject zero or negative sizes and negative origins; deleting the source or the cut image sets the column to null and keeps the row.
- `Storage/stale.py`: selects only stale rows of images in the requested status; deletes only those rows; never commits (the session is checked for pending state, as in the existing repository tests); `stale_counts` matches.
- Registry sync: every `ImageStatus` value appears in `docs/image-status.md`, and every status in the registry is an `ImageStatus` value.
- Inventory completeness: every table in `Base.metadata` with a foreign key to `images.id`, and every table with a foreign key to one of those, appears in `docs/image-derived-data.md` with a group. A new table fails the test until it is classified.
- `tests/integration/` is run as a whole root with `DATABASE_URL` pointing at `ocrdb_test` (CLAUDE.md: shared model code), and the backend tests separately.
- Live environments: `alembic upgrade head` on each; `SELECT count(*)` of rows with `stale` true is zero afterwards.

## 10. Acceptance criteria

1. The migration applies and reverts on `ocrdb_test`; the rollout of the three live databases is documented in this spec's plan.
2. `image_cuts` enforces one live row per source and a unique `cut_image_id`, and keeps its rows when either image is deleted.
3. The four tables have `stale`, default `false`, with no change to existing rows.
4. `ImageStatus` and `CutState` exist, and `docs/image-status.md` documents every status; the sync test passes.
5. `docs/image-derived-data.md` classifies every table off `images`; the completeness test passes.
6. `Storage/stale.py` has the three functions with tests, and commits nothing.
7. `CLAUDE.md`, the batch docs and the two older specs point at the registry, and none of them defines a status differently.
8. The full integration root and the backend tests pass. No batch job changes behaviour.

## 11. Out of scope

Detection, the cut step, repositories for `image_cuts`, the review API and UI, business metrics, the `--status cut_proposed` options, the
"refresh stale" mode in any job, and a check constraint on `images.status`.

## 12. Risks

- **Migration on the live databases.** Metadata-only per A5, but the revision is still applied to the test database first and reviewed before the three live ones.
- **The inventory goes out of date.** The completeness test is the guard; it only catches new tables, not a changed meaning of an existing one, so changing a derived table's role also means editing the inventory.
- **Literal `"active"` strings stay in many queries.** Acceptable: the constants exist for new code, and a bulk rewrite is out of scope and risky.
