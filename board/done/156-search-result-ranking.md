# [P3] Search has no ranking: results are a membership filter ordered by created_at

- **Priority:** P3
- **Area:** Backend search
- **Source:** design for tasks 151/152 (docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md)
- **Tracker:** board/description-tagging-tracker.md

## Problem

`matching_image_ids` (`repository/ocr_lemmas.py`) returns a set of image ids: per query token the candidates are the union of sources
(OCR lemmas, tags, note lemmas, and description lemmas once 151/152 land), intersected across tokens. Results are then ordered by
`created_at desc`. There is no relevance score, so a hit in a human note, an OCR line, a tag or an LLM description is
indistinguishable, and a better match can sit far down the list.

The 151/152 design deliberately does not add ranking (no ranking exists to extend). This task is the follow-up for it.

## Open questions

- Is ranking wanted at all, or is recency fine for a meme corpus?
- Source weighting: notes are human-written and probably should outrank OCR, tags and LLM descriptions; exact should outrank stem,
  fuzzy and phonetic.
- Per-token score combination (sum, max), and how to keep the pagination cursor stable when order is no longer `created_at`.
- Cost: scoring needs per-source hit info that the current id-set union throws away.

Needs analysis first, then a design if it is wanted.
