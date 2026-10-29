# Derived data of an image

What hangs off an `images` row and what happens to each part when a recut proposal creates a cut image (epic
screenshot-recut, `docs/superpowers/specs/2026-10-17-screenshot-recut/`). Authoritative for the question "what is all the data
of an image". `tests/docs/test_image_derived_data_inventory.py` fails when a table with a foreign key to `images` (or to one of
those tables) is missing here. Changing the role of a table also means editing this file.

Groups: **1** copied as is (human-authored or not pixel-dependent). **2** not copied; recomputed by the existing pipeline
(pixel-dependent). **3** copied, flagged or derived as stale, refreshed later. **relation** is bookkeeping, not derived data.
**none** is not copied and not recomputed for the cut by this feature.

The cut's `images` row: new `id` and `filename`; `created_at` inherited from the source; `status = cut_proposed`;
`ingestion_batch_id` empty; `width`, `height` and `content_hash` computed from the new file.

## Inventory

| Table | Group | At cut time | Refresh path |
|---|---|---|---|
| `image_cuts` | relation | The proposal itself | not applicable |
| `description_notes` | 1 | Copied as is, including `lemmas_built_at` and `embedding_built_at` | none |
| `description_note_lemmas` | 1 | Copied | none |
| `description_note_embeddings` | 1 | Copied | none |
| `image_extras` | 1 | `remarks` copied; `flagged` and `flagged_reason` left empty | none |
| `meme_text_ratings` | 1, deferred | Not copied at cut time. Moved to the cut on approve, stay with the source on reject (implemented in R5a) | none |
| `embeddings` | 2 | Not copied (CLIP vector) | `build_image_embeddings --status cut_proposed` |
| `image_classifications` | 2 | Not copied (text-heavy classifier) | `classify_text_heavy --status cut_proposed`; the OCR refresh deletes a cut's row so it is recomputed from the new text |
| `ocr_text_embeddings` | 2 | Not copied | `build_ocr_text_embeddings --status cut_proposed`, after the OCR refresh and `classify_text_heavy`; the OCR refresh deletes a cut's row |
| `ocr_texts` | 3 | Copied with each polygon shifted by the crop origin; a row is kept when its polygon centre lies inside the crop (points clipped to the crop), otherwise dropped; `stale = true` | `extract_text_from_memes --refresh-stale` (reads the cut files of images with stale OCR from the database; implemented in R3c) |
| `ocr_lemmas` | 3 | Copied wholesale, `stale = true`. Keyed by image and lemma, not by OCR row, so they cannot be filtered by the crop | `build_ocr_lemmas --refresh-stale`, after the OCR refresh (implemented in R3c) |
| `image_descriptions` | 3 | Copied, `stale = true` | `build_image_descriptions --refresh-stale`: every configured prompt is described again; a prompt that fails keeps its stale row (implemented in R3c) |
| `image_description_embeddings` | 3, via parent | Copied with the description | rebuilt for the new description row by `build_image_description_embeddings` |
| `image_description_feedback` | 3, via parent | Copied with the description; lost when the stale description is replaced (the feedback judged the old text) | none |
| `description_lemmas` | 3, via parent | Copied with the description | rebuilt for the new description row by `build_description_lemmas` |
| `tags` | 1, 2 or 3 by source | See "Tags by source" below | see below |
| `meme_profiles` | 1 or 3 | Copied exactly. A human-verified profile is group 1 and never rewritten; a model-made one is group 3 with derived staleness | `build_meme_profiles`, through the existing fingerprint rule |
| `meme_profile_values` | 1 or 3 | Copied with its profile | with the profile |
| `meme_profile_situation_embeddings` | 1 or 3 | Copied with its profile | `build_meme_profile_embeddings`, with the profile |
| `image_processing_status` | none | Not copied: a copied `done` would claim work that was not done. The refresh mode selects by stale rows and bypasses the marker | the jobs create their own markers |
| `tmp_duplicates` | none | Not copied | the duplicate jobs run as usual (source/cut pairs are excluded, screenshot-recut R5) |
| `tmp_clusters` | none | Not copied | `clusterize` |
| `duplicate_decisions` | none | Not copied | not applicable |
| `processing_errors` | none | Not copied | produced by the jobs |
| `image_metrics` | none | Not copied | produced by the jobs |
| `popularity_absorptions` | none | Not copied; it has no foreign key to `images` (plain ids, so history survives deletion). A cut's `images.popularity` is set by the recut approval edge (popularity epic P2), never copied | not applicable |

`images.popularity` is a column of `images`, not a table: a new image starts at 1, and a cut gets its popularity from the recut approval edge (popularity epic P2), never by copying the source's value.

## Tags by source

`tags.source` takes these values in the code today (verified 2026-10-17: no manual or human-sourced tags exist on any environment):

| Source | Group | At cut time | Refresh path |
|---|---|---|---|
| `Note` | 1 | Copied (derived from the copied note, not from pixels) | none |
| `YOLO` | 2 | Not copied; not derived at proposal time | `detect_entities_and_tag`: the next full run covers the cut once it is `active` |
| `CONCEPT` | 2 | Not copied; not derived at proposal time | `tag_images_from_concepts`: the next full run covers the cut once it is `active` |
| `OCR` | 3 | Copied, `stale = true` | `build_tags_from_ocr --refresh-stale` (implemented in R3c) |
| `Ollama` | 3 | Copied, `stale = true` | `build_tags_from_descriptions --refresh-stale` (implemented in R3c) |

## Refresh order

The OCR refresh rewrites `ocr_texts`, so everything derived from OCR runs after it: OCR, then OCR lemmas and OCR tags, then
descriptions, then description embeddings, lemmas and tags, then the profile and its embedding. The driver `refresh_recut_cuts`
runs them in this order (a job skips an image whose upstream rows are still stale). A cut whose source had no row in a table has
nothing stale to replace there; the normal run of that job fills it once the cut is `active`. The order is part of the contract: a lemma row rebuilt before the OCR refresh would be fresh in name only.
