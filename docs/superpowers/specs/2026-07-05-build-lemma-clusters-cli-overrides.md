# build_lemma_clusters: CLI Overrides on Top of Environment Variables

**Date:** 2026-07-05
**Status:** Proposed
**Scope:** `batch/build_lemma_clusters.py` (`main()` only), `environments/.env.general`, `docs/superpowers/specs/2026-07-01-build-lemma-clusters.md`, `.claude/commands/draft-lemma-concepts.md`

---

## Summary

`batch/build_lemma_clusters.py`'s `main()` currently reads all 13 config knobs exclusively from environment variables (no CLI arguments at all), while its sibling `draft_concepts_from_clusters.py` is pure `argparse` (no env vars). This adds `argparse` CLI flags to `build_lemma_clusters` — one per existing env var — that **override** the env var when given, without removing env-var support. `run()` (the typed orchestration function both `main()` and tests call) is unchanged; only the env-var/CLI merge happens in `main()`.

---

## Design Decisions

| Question | Decision |
|---|---|
| New env vars needed? | **None.** Every one of the 13 knobs already has an env var per the original spec's Configuration table. This change only adds a CLI layer on top. |
| Input-file CLI shape | Mirror each of `TEXT_SCOPE`/`BOW_OUTPUT_FILE`/`BOW_UNMATCHED_FILE` as its own flag (`--text-scope`, `--bow-output-file`, `--bow-unmatched-file`), rather than a single `--input-file` shortcut — keeps override behavior symmetric across every knob, no special-cased flags. |
| Precedence | CLI flag (if passed) wins; otherwise fall back to the env var; otherwise fall back to the existing hardcoded default. Every `argparse` default is `None` so `main()` can distinguish "not passed" from "passed with a falsy-looking value." |
| Boolean flags (`OLLAMA_ENABLED`, `LOOKUP_CONCEPTS`) | Use `argparse.BooleanOptionalAction` (`--ollama-enabled`/`--no-ollama-enabled`) rather than parsing `"true"/"false"` strings from the CLI — idiomatic argparse, and `None` default when neither is passed. |
| `run()` signature | Unchanged. `main()` is the only thing that changes; existing tests that call `run()` directly need no changes. |
| `.env.general` additions | Add **only** `CLUSTER_SELECTION_METHOD=leaf` — a genuine environment-specific empirical finding (dense generic-vocabulary clusters in `general`'s OCR data, already documented in `CLAUDE.md`), matching the existing precedent that `.env.*` files hold required/stable per-environment settings (`RULES_FILE`, `BOW_OUTPUT_FILE`) but not generic tunables left at their code defaults (`BOW_MIN_WORD_LENGTH`, `OCR_CONFIDENCE_MIN`). Not added to `.env.metal`/`.env.it` — the `leaf` finding hasn't been validated against their data. |
| `CLUSTER_OUTPUT_FILE` in `.env.general`? | No. Unlike `BOW_OUTPUT_FILE` (one stable artifact per environment, always overwritten in place), `CLUSTER_OUTPUT_FILE` necessarily varies per language run (`lemma_clusters.general.ru.yaml` vs `...en.yaml`) — a single static path would cause different-language runs to clobber each other. Stays CLI/invocation-driven, as it already is via `/draft-lemma-concepts`. |

---

## CLI Flags

| Flag | Type | Overrides env var |
|---|---|---|
| `--text-scope` | str | `TEXT_SCOPE` |
| `--bow-output-file` | str | `BOW_OUTPUT_FILE` |
| `--bow-unmatched-file` | str | `BOW_UNMATCHED_FILE` |
| `--cluster-output-file` | str | `CLUSTER_OUTPUT_FILE` |
| `--language` | str | `LANGUAGE` |
| `--min-cluster-size` | int | `MIN_CLUSTER_SIZE` |
| `--min-samples` | int | `MIN_SAMPLES` |
| `--cluster-selection-epsilon` | float | `CLUSTER_SELECTION_EPSILON` |
| `--cluster-selection-method` | str | `CLUSTER_SELECTION_METHOD` |
| `--text-embed-model` | str | `TEXT_EMBED_MODEL` |
| `--ollama-model` | str | `OLLAMA_MODEL` |
| `--ollama-enabled` / `--no-ollama-enabled` | bool (`BooleanOptionalAction`) | `OLLAMA_ENABLED` |
| `--lookup-concepts` / `--no-lookup-concepts` | bool (`BooleanOptionalAction`) | `LOOKUP_CONCEPTS` |

All flags are optional; running with no CLI flags at all reproduces today's pure-env-var behavior exactly.

---

## Fail-Fast Checks

Unchanged in spirit, just checked against the merged (CLI-or-env) value instead of the raw env var:
- Missing input file (both `--bow-output-file`/`BOW_OUTPUT_FILE` when scope is `all`, or `--bow-unmatched-file`/`BOW_UNMATCHED_FILE` otherwise) → `SystemExit` with the existing message.
- Missing `--cluster-output-file`/`CLUSTER_OUTPUT_FILE` → `SystemExit` with the existing message.

---

## Documentation Updates

- **`docs/superpowers/specs/2026-07-01-build-lemma-clusters.md`** — Configuration table gets a one-line note per row (or a single note above the table) that every variable now also has a same-named CLI flag that overrides it.
- **`.claude/commands/draft-lemma-concepts.md`** — Step 1's manual `grep`-and-export dance for `BOW_UNMATCHED_FILE`/`CLUSTER_OUTPUT_FILE`/etc. simplifies to passing CLI flags directly (still reads `DATABASE_URL`/`BASE_PATH` from the env file, since those remain env-var-only via `Storage/db.py`, unrelated to this change).
- **`environments/.env.general`** — add `CLUSTER_SELECTION_METHOD=leaf`.
- **`CLAUDE.md`** — no change needed; its one-line pipeline mention stays accurate either way.

---

## Testing

Extend `tests/batch/test_build_lemma_clusters.py`'s existing `main()` tests (which already monkeypatch `run` and inspect the captured kwargs) to cover:
- CLI flag overrides env var when both are set.
- Env var is used when the CLI flag is omitted (today's behavior, must not regress).
- Hardcoded default is used when neither is set (today's behavior, must not regress).
- Boolean flags: `--ollama-enabled`/`--no-ollama-enabled` correctly override `OLLAMA_ENABLED`; omitting both falls back to the env var.

No new test file — this extends the existing `test_main_*` tests in place.

---

## Non-Goals

- No change to `run()`'s signature or behavior.
- No change to `draft_concepts_from_clusters.py` (already pure CLI, no env vars — out of scope here).
- No `CLUSTER_SELECTION_METHOD` addition to `.env.metal`/`.env.it` (unvalidated for those environments).
