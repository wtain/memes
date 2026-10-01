# [P3] Question: should description embeddings take part in duplicate detection?

- **Priority:** P3
- **Area:** Analysis
- **Source:** audit notes; the user assumed they already did

## Problem

They do not: duplicates use CLIP, OCR-text embeddings and OCR lemmas only. Analyse whether description embeddings add anything for
pure-image memes (no OCR text), where the CLIP probe alone decides, weighed against the cost (the bge-large embedding must exist for
every candidate, Ollama descriptions are slow, and a corroboration rule like the OCR-lemma one would be needed). A possible
outcome is closing this as won't-do.
