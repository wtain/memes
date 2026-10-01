# [P2] Ollama descriptions have no lemma index, so smart search cannot match them

- **Priority:** P2
- **Area:** Batch / Backend search
- **Source:** audit notes (see tracker)

## Problem

Smart search unions `ocr_lemmas`, `image_tags` and `description_note_lemmas` (`repository/ocr_lemmas.py`). Ollama descriptions are
only reachable indirectly, through tags. Descriptions would help images with little or no OCR text.

## Scope

Analysis first: a `description_lemmas` index (mirroring `description_note_lemmas`, per (image, prompt)) vs reusing one table. It must
honor feedback exclusion (149), fuzzy/phonetic matching (see the smart-search specs), and ranking (should a description hit outrank a
tag hit?). Related to the notes-join design (152), so design them together.
