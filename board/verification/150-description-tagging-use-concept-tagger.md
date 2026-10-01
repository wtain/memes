# [P1] Description tagging should use ConceptTagger, and retag when a description changes

- **Priority:** P1
- **Area:** Batch / Rules
- **Source:** user decision 2026-10-01
- **Tracker:** board/description-tagging-tracker.md

## Problem

`build_tags_from_descriptions` uses the old regex `RulesEngine` on raw text with source `Ollama`, while OCR tagging uses
`ConceptTagger` with lemmas and language-aware voting. Also, `--incremental` skips any image that already has an `Ollama` tag, so a
re-described image is never retagged without a full run.

## Decision

Move description tagging to `ConceptTagger`. Descriptions are English LLM output (`language="en"`), so the OCR confidence and
language filters probably do not apply as-is; confirm. Decide whether the tag source stays `Ollama` and how to migrate existing
tags (full rebuild after the switch). Fix the incremental semantics (retag when a description is newer than the image's tags, or
key per description).

## Pointers

`batch/build_tags_from_descriptions.py`, `batch/build_tags_from_ocr.py`, `rules/concept_tagger.py`, `rules/engine.py`,
`repository/images.py`, `batch/data/tagging/`, `docs/data-flow.md` section 2.
Changes to shared tagging code need the whole `tests/integration/` root run (see CLAUDE.md gotchas).
