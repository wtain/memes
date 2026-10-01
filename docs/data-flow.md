# Data flow: signals, producers, consumers

Where each enrichment signal comes from and who uses it. Verified against the code on 2026-10-01.
Start here when asked "does X use Y?"; update it whenever a producer or consumer changes.

## 1. Signal matrix

| Signal | Table | Producer job | Used by |
|---|---|---|---|
| OCR text | `ocr_texts` | `extract_text_from_memes` | OCR tagging, OCR lemmas, OCR-text embeddings, review UI |
| OCR lemmas | `ocr_lemmas` | `build_ocr_lemmas` | Smart search; OCR-text duplicate corroboration (`rebuild_duplicates`, `ingest_find_duplicates`) |
| CLIP embedding | `embeddings` | `build_image_embeddings` | Visual search, `/similar?source=image`, duplicates (general + safety-net probes), concept tagging |
| OCR-text embedding (384-dim) | `ocr_text_embeddings` | `build_ocr_text_embeddings` | Duplicates (OCR-text probe, text-heavy pairs only) |
| Ollama description | `image_descriptions` (one row per image + prompt) | `build_image_descriptions` | Description embeddings, description tagging, `build_bow --text-source descriptions` (concept discovery only), UI display |
| Description feedback | `image_description_feedback` (approved / rejected) | UI (`PUT .../feedback`) | UI display only. **Not yet consumed by search, similarity or tagging** (see board task) |
| Description embedding (bge-large, 1024-dim) | `image_description_embeddings` | `build_image_description_embeddings` | `/similar?source=description` only. **Not used by duplicates** |
| Description note (human, one per image) | `description_notes` | UI (`PUT/DELETE /api/images/{id}/description-note`) | Note lemmas, note embeddings |
| Note lemmas | `description_note_lemmas` | `build_description_note_lemmas` | Smart search |
| Note embedding (bge-large, 1024-dim) | `description_note_embeddings` | `build_description_note_embeddings` | `/similar?source=description_note` |
| Tags | `image_tags` (column `source`) | Tagging jobs below | Smart search (as a lemma source), tag filters, UI |

Smart search matches an image if the query lemma is in `ocr_lemmas`, in `image_tags`, or in `description_note_lemmas`
(`repository/ocr_lemmas.py`). **Ollama descriptions are not in that set.**

## 2. Tagging

Tags are rows in `image_tags` with a `source` string. Each job owns one source, and a full run deletes only its own source.

| Job | Engine | Input | `source` | Incremental behavior |
|---|---|---|---|---|
| `build_tags_from_ocr` | `ConceptTagger` (`rules/concept_tagger.py`, YAML in `batch/data/tagging/`, profile `GENERAL.TAGGING_PROFILE`) | OCR text, per language, after the OCR confidence and language-plausibility filters | `OCR` | Skips images that already have an `OCR` tag |
| `build_tags_from_descriptions` | `ConceptTagger`, same vocabulary and profile as OCR tagging | Every Ollama description (all prompts), each tagged on its own as `en` with no confidence or language filters, then the tags are unioned per image | `Ollama` | Selects images with no `Ollama` tag, or whose newest description is newer than their newest `Ollama` tag (re-described). Their `Ollama` tags are deleted and rewritten from all their descriptions. An image whose descriptions yield no tags is re-evaluated on every run (harmless) |
| `tag_images_from_concepts` | CLIP concept similarity (`CONCEPTS.*`, built by `build_concept_embeddings`) | CLIP embeddings | `CONCEPT` | Full rebuild |
| `detect_entities_and_tag` | YOLOv8 animal detector | Image files | `YOLO` | Full rebuild |

Notes:
- OCR and description tagging share one engine and vocabulary, but the vocabulary was written for meme text, so descriptions
  (visual, English prose) may match it differently. Tags are kept apart by `source`. Description votes are not accumulated across
  prompts: a tag must reach its threshold within a single description.
- `ConceptTagger` needs `batch/data/tagging/{concepts,tags}.<profile>.yaml`; only `metal` and `general` have them, so neither tagging job runs on `it`.
- The old `RulesEngine` (`rules/engine.py`) is no longer used by any batch job, only by dev tools under `batch/tools/`.
- `rules/normalize.py` is shared by `ConceptTagger` and `build_bow`.
- Concept discovery (`build_bow`, `build_lemma_clusters`, `draft_concepts_from_clusters`) drafts new concept/tag YAML entries
  for human review. It is a human-in-the-loop step, not an automatic tagger.

## 3. Descriptions

- **Ollama descriptions** (`image_descriptions`): one row per (image, prompt_key), prompts configured per environment. Failures are tracked
  per pair and retried only with `--retry-failed`. See specs 2026-07-13 and 2026-07-15.
- **Feedback** (`image_description_feedback`): per description, approve or reject, toggled via the API. Today it is display-only.
- **Description embeddings**: SBERT `BAAI/bge-large-en-v1.5`, per description. `/similar?source=description` takes the minimum cosine
  distance over pairs that share the same `prompt_key`. See spec 2026-07-16.
- **Description notes**: human-written, one per image, editable in place, no history. Notes **do not replace** Ollama descriptions. They
  are a separate, parallel signal: they join search through their lemmas and have their own similarity mode (`source=description_note`).
  They do not feed tagging. See spec 2026-08-20.
- **Duplicates never use descriptions or notes.** They use CLIP, OCR-text embeddings and OCR lemmas (see CLAUDE.md, batch pipeline).

## 4. Scheduling and admin triggering

- Scheduled (`environments/settings.yaml`, `scheduler.jobs`): only `trends_batch` and `build_statistics`.
- Every other enrichment job is manual. Most are triggerable from `/admin/batches` through `environments/batch_registry.yaml`.
- `build_image_descriptions` and `build_image_description_embeddings` are run-tracked and in the registry (admin-triggerable, manual only). Triggered on their own they have no `--limit`, so one trigger describes the whole backlog.
- `description_pipeline` (admin-triggerable, manual only) runs, in order: `build_tags_from_ocr`, the two note jobs, `build_image_descriptions` (capped by `image_descriptions.max_per_run`, unset means unlimited), then description embeddings and `build_tags_from_descriptions`. Independent steps continue past a failure; the two steps that need descriptions are skipped if `build_image_descriptions` fails. Each step self-tracks under its own kind. Active images only, so new images join after `ingest_promote`. Not in the chain: `tag_images_from_concepts`, `detect_entities_and_tag`, concept drafting.
- Ingestion (`ingest_auto_prep`) does not cover descriptions: neither description job supports `--status pending`.

## 5. Known gaps (tracked in `board/todo`)

See tasks 147-154 (index: `board/description-tagging-tracker.md`). Summary: description feedback is not consumed anywhere, descriptions are not searchable, notes do not join tagging
or description similarity, description jobs are outside ingestion (deliberately: new images are described after promotion).
