# [P2] Rejected description feedback must exclude the description from search, similarity and tagging

- **Priority:** P2
- **Area:** Backend / Batch
- **Source:** user decision 2026-10-01
- **Depends on:** 150, 151 (the consumers must exist first for search and tagging)

## Problem

`image_description_feedback.approved` is display-only today. A description marked rejected still feeds embeddings,
`/similar?source=description`, and `Ollama` tags.

## Decision

Rejected descriptions are excluded from search, similarity and tagging. Open design points (resolve in analysis/design):
unreviewed (no feedback) stays included; an approve after a reject re-includes it; already-built embeddings, lemmas and tags need
cleanup or query-time filtering; what happens to tags already derived from a description when it gets rejected.

## Pointers

`Backend/app/repositories/image_repository.py` (`get_similar_by_description`, `set_description_feedback`),
`repository/image_description_embeddings.py`, `repository/images.py` (`get_images_and_descriptions*`).
