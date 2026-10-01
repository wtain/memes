# [P3] Measure `source=description_all` on a real corpus (unindexed cross join)

- **Priority:** P3
- **Area:** Backend similarity
- **Source:** final review of tasks 151/152 (docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md section 2)
- **Tracker:** board/description-tagging-tracker.md

## Problem

`ImageRepository.get_similar_by_description_all` cross-joins the source image's vectors with every description and note vector
(minimum cosine distance per candidate image). No index can help that shape, so it is a full scan per request, roughly P times the
cost of the existing `source=description` mode (P = number of prompts), plus the NOT EXISTS anti-join for rejected descriptions.
The default similarity path is untouched and the frontend does not expose the mode yet, so this did not block the merge.

## To do

Measure with `EXPLAIN (ANALYZE)` on a real-size corpus (controller only, read-only DB role `DATABASE_URL_READONLY`; it is a SELECT).
If it is slow (spec guideline: over about 2 s on metal), switch to one HNSW-ordered lookup per source vector against each embedding
table (`ix_image_description_embeddings_*`, `ix_description_note_embeddings_embedding`), over-fetched and merged by minimum distance
in the service. API unchanged. Do this before the frontend exposes the mode.
