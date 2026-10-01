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
| Description lemmas | `description_lemmas` | `build_description_lemmas` | Smart search (Ollama description as a search source; indexed as English; rows of rejected descriptions are excluded at query time) |
| Description feedback | `image_description_feedback` (approved / rejected) | UI (`PUT .../feedback`) | Smart search and `/similar?source=description_all` (rejected descriptions excluded), UI display. **Not consumed by description tagging, description embedding generation or `source=description`** (board task 149) |
| Description embedding (bge-large, 1024-dim) | `image_description_embeddings` | `build_image_description_embeddings` | `/similar?source=description` only. **Not used by duplicates** |
| Description note (human, one per image) | `description_notes` | UI (`PUT/DELETE /api/images/{id}/description-note`) | Note lemmas, note embeddings, note tagging |
| Note lemmas | `description_note_lemmas` | `build_description_note_lemmas` (also refreshed inline by `PUT .../description-note`) | Smart search |
| Note embedding (bge-large, 1024-dim) | `description_note_embeddings` | `build_description_note_embeddings` | `/similar?source=description_note`, `/similar?source=description_all` |
| Tags | `image_tags` (column `source`) | Tagging jobs below | Smart search (as a lemma source), tag filters, UI |

Smart search matches an image if the query lemma is in `ocr_lemmas`, in `image_tags`, in `description_note_lemmas`, or in
`description_lemmas` (`repository/ocr_lemmas.py`). Ollama description lemmas of descriptions the user rejected are excluded at query time.

Similarity modes of `GET /api/images/{id}/similar`: `image` (CLIP), `description` (minimum cosine distance over description vectors sharing
a `prompt_key`), `description_note` (note vector) and `description_all` (minimum cosine distance over all of the image's non-rejected
description vectors plus its note vector, no `prompt_key` restriction; 404 when the image has no non-rejected description embedding and no note embedding, so an image whose only descriptions are rejected also 404s).

Latency after a note edit: lemmas are immediate (refreshed inline in `PUT .../description-note`); tags appear after `build_tags_from_notes`;
the similarity vector updates after `build_description_note_embeddings`.

## 2. Tagging

Tags are rows in `image_tags` with a `source` string. Each job owns one source, and a full run deletes only its own source.

| Job | Engine | Input | `source` | Incremental behavior |
|---|---|---|---|---|
| `build_tags_from_ocr` | `ConceptTagger` (`rules/concept_tagger.py`, YAML in `batch/data/tagging/`, profile `GENERAL.TAGGING_PROFILE`) | OCR text, per language, after the OCR confidence and language-plausibility filters | `OCR` | Skips images that already have an `OCR` tag |
| `build_tags_from_descriptions` | `ConceptTagger`, same vocabulary and profile as OCR tagging | Every Ollama description (all prompts), each tagged on its own as `en` with no confidence or language filters, then the tags are unioned per image | `Ollama` | Selects images with no `Ollama` tag, or whose newest description is newer than their newest `Ollama` tag (re-described). Their `Ollama` tags are deleted and rewritten from all their descriptions. An image whose descriptions yield no tags is re-evaluated on every run (harmless) |
| `build_tags_from_notes` | `ConceptTagger`, same vocabulary and profile as OCR tagging | The human description note, tagged with `language=None` (script-based fallback) | `Note` | Selects notes with no `Note` tag or edited after their newest `Note` tag; their `Note` tags are deleted and rewritten. Full run (no `--incremental`) deletes every `Note` tag first. Clearing a note (`DELETE`, or `PUT` with empty text) deletes that image's `Note` tags immediately |
| `tag_images_from_concepts` | CLIP concept similarity (`CONCEPTS.*`, built by `build_concept_embeddings`) | CLIP embeddings | `CONCEPT` | Full rebuild |
| `detect_entities_and_tag` | YOLOv8 animal detector | Image files | `YOLO` | Full rebuild |

Notes:
- `description_pipeline` runs both `build_tags_from_notes` and `build_description_lemmas` (see section 4).
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
- **Description lemmas** (`description_lemmas`): each Ollama description indexed as English by `build_description_lemmas`; incremental by construction.
- **Feedback** (`image_description_feedback`): per description, approve or reject, toggled via the API. Rejected descriptions are excluded from
  smart search and from `source=description_all`. Excluding them from description TAGGING, description EMBEDDING generation and
  `source=description` is NOT part of this change (board task 149).
- **Description embeddings**: SBERT `BAAI/bge-large-en-v1.5`, per description. `/similar?source=description` takes the minimum cosine
  distance over pairs that share the same `prompt_key`. See spec 2026-07-16.
- **Description notes**: human-written, one per image, editable in place, no history. Notes **do not replace** Ollama descriptions. They
  are a separate, parallel signal: they join search through their lemmas and have their own similarity mode (`source=description_note`).
  They feed tagging through `build_tags_from_notes` (source `Note`), and they also feed `source=description_all` similarity. See specs 2026-08-20 and 2026-10-01.
- **Duplicates never use descriptions or notes.** They use CLIP, OCR-text embeddings and OCR lemmas (see CLAUDE.md, batch pipeline).

## 4. Scheduling and admin triggering

- Scheduled (`environments/settings.yaml`, `scheduler.jobs`): only `trends_batch` and `build_statistics`.
- Every other enrichment job is manual. Most are triggerable from `/admin/batches` through `environments/batch_registry.yaml`.
- `build_image_descriptions` and `build_image_description_embeddings` are run-tracked and in the registry (admin-triggerable, manual only). Triggered on their own they have no `--limit`, so one trigger describes the whole backlog.
- `build_description_lemmas` and `build_tags_from_notes` are in the registry (admin-triggerable, manual only, not scheduled).
- `description_pipeline` (admin-triggerable, manual only) runs, in order: `build_tags_from_ocr`, the two note jobs, `build_tags_from_notes`, `build_image_descriptions` (capped by `image_descriptions.max_per_run`, unset means unlimited), then description embeddings, `build_description_lemmas` and `build_tags_from_descriptions`. Independent steps continue past a failure; the steps that need descriptions are skipped if `build_image_descriptions` fails. Each step self-tracks under its own kind. Active images only, so new images join after `ingest_promote`. Not in the chain: `tag_images_from_concepts`, `detect_entities_and_tag`, concept drafting.
- Ingestion (`ingest_auto_prep`) does not cover descriptions: none of the description jobs, including `build_description_lemmas`, supports `--status pending` (board task 148).

## 5. Known gaps (tracked in `board/todo`)

See tasks 147-156 (index: `board/description-tagging-tracker.md`). Summary: description feedback is not consumed by description tagging, embedding generation or `source=description` (task 149),
description jobs are outside ingestion (deliberately: new images are described after promotion), and search has no ranking (task 156).
