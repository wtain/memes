"""Result-string and classifier-name constants for the text-heavy meme classifier (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md). Deliberately dependency-free (no
numpy/PIL/sentence-transformers) so Backend-reachable code can depend on these values without
pulling in batch/utils/text_heavy_classifier.py's heavy imports -- this module lives under
rules/ specifically because Dockerfile.backend already copies that directory wholesale."""

CLASSIFIER_NAME = "text_heavy_v1"
TEXT_HEAVY = "text_heavy"
NOT_TEXT_HEAVY = "not_text_heavy"
