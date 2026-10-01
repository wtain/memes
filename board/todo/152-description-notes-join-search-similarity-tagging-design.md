# [P1] Design: human notes join (not override) Ollama descriptions in search, similarity and tagging

- **Priority:** P1
- **Area:** Design / Backend / Batch
- **Source:** user decision 2026-10-01
- **Tracker:** board/description-tagging-tracker.md

## Decision

A note must not override the Ollama description; both participate. This needs a proper design. Status today: notes already join
search (note lemmas) and have their own similarity mode (`source=description_note`); notes do not feed tagging.

## Open questions

- Search: ranking of note hits vs OCR/tag/description hits. Notes are human-written and probably should rank higher.
- Similarity: keep separate modes (`description`, `description_note`) or add a combined mode merging both embeddings (same bge-large
  space, so merging is feasible: min distance across all of an image's description and note vectors).
- Tagging: notes run through `ConceptTagger` (source `Note`?) and the tag-refresh trigger when a note is edited or cleared.
- Notes have no language tag, so lemmas are unstemmed (see `batch/build_description_note_lemmas.py`); is that acceptable for tagging?
- Interaction with feedback exclusion (149): notes have no feedback, so they are always included.
- Edit-triggered refresh: today note embeddings and lemmas are rebuilt by manual batches only.

Output: a design spec in docs/superpowers/specs/ (architectural path), then implementation tasks.
