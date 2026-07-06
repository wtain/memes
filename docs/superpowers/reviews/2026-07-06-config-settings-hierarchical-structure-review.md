# Review: Config settings hierarchical YAML structure

Spec: `docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md`

## Logic correctness against the spec

- All 32 tracked keys restructured exactly per the spec's mapping table, into 7 groups (`ocr`, `rules`, `bow`, `lemma_clustering`, `concepts`, `ollama`, `general`).
- `config/settings.py`: `merge_enabled=True` added to `Dynaconf(...)`, verified via a throwaway reproduction before implementation that it deep-merges nested dicts across the common + per-environment files without data loss, and does not reinstate the earlier `environments=True` leakage bug (orthogonal mechanisms — confirmed by direct test).
- Access convention (bare `settings.GROUP.KEY` for guaranteed keys, `settings.get("GROUP.KEY")` dotted-path for undefaulted ones) applied consistently across all migrated call sites.
- One real gap found and fixed mid-implementation: `TAGGING_DATA_DIR` (read by `build_tags_from_ocr.py` for `ConceptTagger.load(...)`) was missing from the original mapping table entirely — caught by the batch-group-A subagent, not by upfront analysis. Fixed by adding `rules.tagging_data_dir` to the spec and updating the one call site. This is the process working as intended (subagents don't guess when the spec is silent — they surface the gap), but it means the upfront table wasn't as exhaustive as claimed before implementation. No other gaps surfaced across three independent implementers (Backend/Storage, batch A, batch B) and a full grep sweep for stray old flat-key references after all three landed (zero hits).
- Every requirement in the spec's Testing section is met: both integration test suites now assert all 32 keys per environment (previously 2-3 keys), run green both before (flat baseline) and after (nested) the restructure with identical expected values, and the full existing suites (Backend 126, `tests/rules/` 52, `batch/tests/` 6) stay green throughout.

## Code quality

- Duplication: the `_COMMON`/`_EXPECTED` dicts are duplicated between `Backend/tests/test_config_integration.py` and `batch/tests/test_env_loading.py` rather than extracted to a shared fixture. This was a deliberate call (documented inline in both files) — the two suites independently exercise the same config module from different application entry points, and each should stand alone as a complete regression net; a shared module would blur that. Acceptable given ~32 lines duplicated across 2 files, not growing.
- One code-quality issue found and fixed during this review: a `merge_enabled=True` rationale comment was initially placed as a floating module-level comment between `_build()` and `_SettingsProxy`, disconnected from the code it explains. Moved into `_build()`'s own docstring, next to the parameter it justifies.
- Naming: `lemma_clustering` (not `clustering`) and `concepts.text_concepts_file` (not shortened to `concepts.text_file`) were deliberate disambiguation choices, documented in the spec's grouping rationale, to avoid colliding in meaning with the unrelated image-duplicate clustering pipeline and image-set concepts respectively.
- No new abstractions introduced beyond what the restructuring required — this is a rename/re-nest of existing, working infrastructure, not new architecture.

## Test coverage

- `cd Backend && pytest`: 126 passed.
- `pytest tests/rules/`: 52 passed.
- `pytest batch/tests/`: 6 passed.
- `py_compile` clean on all 19 migrated batch files (verified by the two batch-group subagents) plus `Backend/app/main.py`.
- `--help` smoke-tested on every batch script's CLI with `DATABASE_URL` pre-set, confirming no `AttributeError`/`KeyError` at import time.
- Full grep sweep post-migration for any of the 32 old flat key names still appearing as `settings.KEY` / `settings.get("KEY")` anywhere in `batch/`, `Backend/`, `Storage/`: zero hits.

## What was fixed

- `TAGGING_DATA_DIR` mapping gap (added to spec + `build_tags_from_ocr.py`).
- Floating comment moved into the docstring it belongs to.

## What was not fixed but explained (acceptable deviation)

- `RULES_FUZZY_THRESHOLD`, mentioned in `Readme.md`'s rules-engine job description, is not read by any code (confirmed via grep during the original flat-structure migration and again here) — stale documentation predating both migrations. Left as-is with a note flagging it as stale, since fixing it would mean either implementing a feature or removing a doc line, neither of which is in scope for a config-structure refactor.
- `PROFILE` vs `TAGGING_PROFILE` naming inconsistency (`batch/tools/spot_check_losses.py`): moved to `general.profile` unchanged, per the spec's explicit non-goal — this refactor preserves resolved values and existing quirks, it doesn't fix them.

## What was intentionally ignored and why

- No shared test-fixture module for the duplicated `_EXPECTED` dicts (see Code quality above) — YAGNI; two independent ~32-line dicts are simpler to reason about than a shared module both suites would need to import and keep in sync with a third source of truth.
