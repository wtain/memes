# Description / Tagging Audit and Pipeline Plan — Working Notes

Status: draft
Originates from: 2026-10-01 audit conversation (no spec yet; the pipeline part needs its own design)

## 1. Audit: what is actually true (verified in code 2026-10-01)

| Claim | Verdict | Evidence |
|---|---|---|
| Descriptions are built via Ollama | True | `batch/build_image_descriptions.py`; one row per (image, prompt_key) in `image_descriptions` |
| Embeddings are created for those descriptions | True | `batch/build_image_description_embeddings.py`, SBERT `BAAI/bge-large-en-v1.5` (1024-dim), table `image_description_embeddings` |
| Description embeddings are used only for similarity search and duplicates | **Half wrong** | Used for similarity only (`GET /api/images/{id}/similar?source=description`, `ImageRepository.get_similar_by_description`, min distance over same-prompt pairs). **Not used by duplicates**: `rebuild_duplicates`, `ingest_find_duplicates`, `clusterize` never reference descriptions. Duplicates use CLIP, the OCR-text embeddings and OCR lemmas. |
| Tagging is lemma-based, from OCR and descriptions | **Partly wrong** | OCR tagging: `build_tags_from_ocr` runs the new `ConceptTagger` (concept voting, language-aware, OCR confidence and language filters), source `"OCR"`. Description tagging: `build_tags_from_descriptions` runs the **old** regex/substring `RulesEngine` on raw description text, source `"Ollama"`. It does not use lemmas or ConceptTagger, so it is a different engine from OCR tagging. |
| Lemmas come from descriptions | **False** | `ocr_lemmas` is OCR only. `description_note_lemmas` is human notes only. Ollama descriptions have no lemma index, so they are invisible to smart search. The only lemma use of descriptions is `build_bow --text-source descriptions`, which is concept discovery, not search or tagging. |
| Users can write a description with its own embeddings, which can override the Ollama one | **Done, but it is not an override** | Spec `2026-08-20-description-notes-design.md` is `done`. Table `description_notes` (one per image) has its own SBERT embeddings (`description_note_embeddings`) and lemmas (`description_note_lemmas`). `PUT/DELETE /api/images/{id}/description-note`. Search includes note lemmas. Similarity has its own `source=description_note`. Nothing replaces or suppresses the Ollama description. They are parallel signals. Notes also do not feed tagging. |

### Additional cases the statement missed
- Description feedback (`image_description_feedback`, approve/reject per description). Nothing downstream consumes `approved` yet: tagging, embeddings and similarity ignore it.
- `build_image_descriptions` and `build_image_description_embeddings` are **not** in `environments/batch_registry.yaml` and do not use `tracked_run`, so they are not admin-triggerable and are not visible in `/admin/batches` history. The note jobs and `build_tags_from_descriptions` are in the registry.
- `build_image_description_embeddings` is **missing from CLAUDE.md's batch-pipeline list** (the note embeddings job is listed).
- `build_image_description_embeddings` and the description tagging are not status-aware (no `--status pending`), so ingestion cannot cover descriptions yet.
- Tagging sources: `OCR`, `Ollama`, `CONCEPT` (`tag_images_from_concepts`, CLIP concepts), `YOLO` (`detect_entities_and_tag`). Smart search matches OCR lemmas + tags + note lemmas.
- `build_tags_from_descriptions --incremental` skips any image that already has an `Ollama` tag. Re-describing an image therefore never retags it without a full run.
- Scheduler (`environments/settings.yaml`): only `trends_batch` and `build_statistics` are scheduled. All enrichment jobs are manual-only by design (spec `2026-08-14-batch-scheduling-rollout-design.md`).

## 2. Documentation gaps (to write so these questions are fast to answer next time)
1. **Data-flow map doc** (one page, e.g. `docs/data-flow.md`): for each signal (OCR text, OCR lemmas, CLIP, OCR-text embedding, description, description embedding, note, note lemmas/embedding, tags by source), give the producer job, the table, and which consumers use it (search, similarity, duplicates, tagging). A matrix is the right shape.
2. **Tagging doc**: the two engines (old `RulesEngine` vs `ConceptTagger`), which job uses which, tag `source` values, and the incremental-skip semantics.
3. **Description subsystem doc**: Ollama descriptions, feedback, embeddings, notes, similarity `source` modes, and "notes do not override".
4. Add `build_image_description_embeddings` to the CLAUDE.md batch list. Correct the stale ARCHITECTURE.md description-tagging flow.
5. Note in CLAUDE.md that description jobs are outside the registry/scheduler until the pipeline work lands.

## 3. Decisions to make
- Should the Ollama-description tagging move to `ConceptTagger` and lemmas, or stay on the old engine? Is a lemma index for descriptions wanted for smart search?
- Should notes override or suppress Ollama descriptions in search, similarity and tagging? Currently no.
- Should description feedback (`approved=false`) exclude descriptions from embeddings, similarity and tagging?
- Should description embeddings join duplicate detection? Currently no. The user's belief was wrong, but it may be worth considering.

## 4. Pipeline to design (needs brainstorming and a spec)
Goal: a chain of scheduled tasks covering the description and tagging data, with human-in-the-loop steps where needed (like ingestion).

Candidate chain, per environment:
1. `build_image_descriptions` (Ollama, incremental, `--limit` capped, GPU-heavy)
2. `build_image_description_embeddings` (incremental)
3. `build_tags_from_descriptions` (incremental)
4. `build_tags_from_ocr` (incremental)
5. `tag_images_from_concepts`, `detect_entities_and_tag` (heavier, maybe a lower cadence)
6. Note jobs: `build_description_note_lemmas` and `build_description_note_embeddings` (human edits trigger these, so a cheap incremental run after note edits)
7. `build_statistics` (already chained by the run wrapper)

Prerequisite changes:
- Register `build_image_descriptions` and `build_image_description_embeddings` with `tracked_run`/`finish_existing_run`, and add `batch_registry.yaml` entries.
- Add `--status pending` support to the description jobs so they can join `ingest_auto_prep`.
- Check that the Ollama steps can be preempted or capped: the Windows ~10-minute background kill, and the always-on dev GPU usage.
- Design the orchestration: a new `description_auto_prep`-style job like `ingest_auto_prep` (`kind` distinct from child jobs), manual-trigger vs scheduled, failure and resume semantics, and where the human gates sit (description feedback review, concept drafting via `/draft-lemma-concepts`, note editing).
- The incremental-skip gotcha above must be solved for retag-on-redescribe.

## 5. Suggested order
1. Docs (section 2). Cheap, and independent of the decisions.
2. Decisions in section 3, with the user.
3. Registry and tracking prerequisites (small, bounded).
4. Pipeline orchestration spec, then plan.
