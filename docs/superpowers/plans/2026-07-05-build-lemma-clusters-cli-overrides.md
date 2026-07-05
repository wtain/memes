# build_lemma_clusters CLI Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `argparse` CLI flags to `batch/build_lemma_clusters.py`'s `main()` — one per existing environment variable — that override the env var when passed, without removing env-var support, and keep the spec/command/env-file documentation in sync.

**Architecture:** `run()` (the typed orchestration function) is untouched. Only `main()` changes: it parses CLI flags (all defaulting to `None`), then for each of the 13 config values picks the CLI flag if given, else falls back to the existing env-var-driven expression, else the existing hardcoded default — exactly reproducing today's behavior when no CLI flags are passed.

**Tech Stack:** Python 3.11 stdlib `argparse` (including `argparse.BooleanOptionalAction` for the two boolean flags). No new dependencies.

## Global Constraints

- No new environment variables — every one of the 13 knobs already has an env var; this only adds a CLI layer on top.
- `run()`'s signature and behavior must not change.
- Precedence: CLI flag (if passed) wins > env var (if set) > existing hardcoded default. Running with zero CLI flags must reproduce today's pure-env-var behavior exactly.
- Boolean flags (`OLLAMA_ENABLED`, `LOOKUP_CONCEPTS`) use `argparse.BooleanOptionalAction` (`--foo`/`--no-foo`), not string-parsed `"true"/"false"` CLI values.
- `--cluster-output-file` must NOT be added to any `environments/.env.*` file (it varies per language run; a static value would cause different-language runs to clobber each other's output).
- `CLUSTER_SELECTION_METHOD=leaf` is added only to `environments/.env.general` (validated for that environment's data), not `.env.metal`/`.env.it`.

---

## File Structure

| File | Change |
|---|---|
| `batch/build_lemma_clusters.py` | Add `import argparse`; replace `main()`'s body with CLI-parsing + env-var-fallback logic. `run()` unchanged. |
| `tests/batch/test_build_lemma_clusters.py` | Update the 5 existing `test_main_*` tests (each needs `sys.argv` pinned to a no-flags state, since introducing `argparse` means `main()` now parses whatever `sys.argv` happens to be — pytest's own args, if not overridden). Add 2 new tests covering CLI-overrides-env and boolean-flag-fallback. |
| `docs/superpowers/specs/2026-07-01-build-lemma-clusters.md` | Add a note after the Configuration table listing each variable's CLI flag. |
| `.claude/commands/draft-lemma-concepts.md` | Simplify Step 1 to pass CLI flags directly instead of the env-var-prefixed invocation (still exports `DATABASE_URL`/`BASE_PATH` as env vars — those remain env-only). |
| `environments/.env.general` | Add `CLUSTER_SELECTION_METHOD=leaf`. |

---

## Task 1: `main()` CLI overrides + tests

**Files:**
- Modify: `batch/build_lemma_clusters.py:1` (add import), `batch/build_lemma_clusters.py:222-246` (replace `main()`)
- Modify: `tests/batch/test_build_lemma_clusters.py:332-420` (update 5 existing tests, add 2 new ones)

**Interfaces:**
- Consumes: `run()` at `batch/build_lemma_clusters.py:172` — signature unchanged (`input_file, output_file, language, min_cluster_size, min_samples, cluster_selection_epsilon, cluster_selection_method, embed_model, ollama_model, ollama_enabled, lookup_concepts`).
- Produces: `main()` with identical external behavior when invoked with no CLI flags; new CLI flags `--text-scope`, `--bow-output-file`, `--bow-unmatched-file`, `--cluster-output-file`, `--language`, `--min-cluster-size`, `--min-samples`, `--cluster-selection-epsilon`, `--cluster-selection-method`, `--text-embed-model`, `--ollama-model`, `--ollama-enabled`/`--no-ollama-enabled`, `--lookup-concepts`/`--no-lookup-concepts`.

- [ ] **Step 1: Write the failing tests**

Replace lines 332-420 of `tests/batch/test_build_lemma_clusters.py` (the five existing `test_main_*` functions) with the following — each existing test gets one added line (`monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])`, placed right after the `monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)` line) so `argparse` sees no CLI flags and every value falls through to the env var, exactly matching today's behavior. Two new tests are added at the end:

```python
@pytest.mark.asyncio
async def test_main_reads_env_vars_and_calls_run(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.setenv("TEXT_SCOPE", "all")
    monkeypatch.setenv("BOW_OUTPUT_FILE", "bow.json")
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")
    monkeypatch.setenv("LANGUAGE", "ru")
    monkeypatch.setenv("MIN_CLUSTER_SIZE", "3")
    monkeypatch.setenv("MIN_SAMPLES", "2")
    monkeypatch.setenv("CLUSTER_SELECTION_EPSILON", "0.15")
    monkeypatch.setenv("CLUSTER_SELECTION_METHOD", "leaf")
    monkeypatch.setenv("TEXT_EMBED_MODEL", "clip")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    monkeypatch.setenv("LOOKUP_CONCEPTS", "true")

    await main()

    assert captured["input_file"] == "bow.json"
    assert captured["output_file"] == "out.yaml"
    assert captured["language"] == "ru"
    assert captured["min_cluster_size"] == 3
    assert captured["min_samples"] == 2
    assert captured["cluster_selection_epsilon"] == 0.15
    assert captured["cluster_selection_method"] == "leaf"
    assert captured["embed_model"] == "clip"
    assert captured["ollama_model"] == "llama3"
    assert captured["ollama_enabled"] is False
    assert captured["lookup_concepts"] is True


@pytest.mark.asyncio
async def test_main_default_text_scope_uses_unmatched_file(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.delenv("TEXT_SCOPE", raising=False)
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")

    await main()

    assert captured["input_file"] == "unmatched.json"


@pytest.mark.asyncio
async def test_main_default_cluster_selection_method_is_eom(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.delenv("CLUSTER_SELECTION_METHOD", raising=False)
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")

    await main()

    assert captured["cluster_selection_method"] == "eom"


@pytest.mark.asyncio
async def test_main_missing_input_env_var_raises_clear_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.delenv("TEXT_SCOPE", raising=False)
    monkeypatch.delenv("BOW_UNMATCHED_FILE", raising=False)
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")

    with pytest.raises(SystemExit, match="BOW_UNMATCHED_FILE"):
        await main()


@pytest.mark.asyncio
async def test_main_missing_output_env_var_raises_clear_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.setenv("TEXT_SCOPE", "unmatched")
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.delenv("CLUSTER_OUTPUT_FILE", raising=False)

    with pytest.raises(SystemExit, match="CLUSTER_OUTPUT_FILE"):
        await main()


@pytest.mark.asyncio
async def test_main_cli_flags_override_env_vars(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setenv("TEXT_SCOPE", "unmatched")
    monkeypatch.setenv("BOW_OUTPUT_FILE", "env_bow.json")
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "env_unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "env_out.yaml")
    monkeypatch.setenv("LANGUAGE", "en")
    monkeypatch.setenv("MIN_CLUSTER_SIZE", "2")
    monkeypatch.setenv("MIN_SAMPLES", "1")
    monkeypatch.setenv("CLUSTER_SELECTION_EPSILON", "0.0")
    monkeypatch.setenv("CLUSTER_SELECTION_METHOD", "eom")
    monkeypatch.setenv("TEXT_EMBED_MODEL", "sbert")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2")
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    monkeypatch.setenv("LOOKUP_CONCEPTS", "false")

    monkeypatch.setattr("sys.argv", [
        "build_lemma_clusters",
        "--text-scope", "all",
        "--bow-output-file", "cli_bow.json",
        "--bow-unmatched-file", "cli_unmatched.json",
        "--cluster-output-file", "cli_out.yaml",
        "--language", "ru",
        "--min-cluster-size", "5",
        "--min-samples", "4",
        "--cluster-selection-epsilon", "0.2",
        "--cluster-selection-method", "leaf",
        "--text-embed-model", "clip",
        "--ollama-model", "llama3",
        "--no-ollama-enabled",
        "--lookup-concepts",
    ])

    await main()

    assert captured["input_file"] == "cli_bow.json"
    assert captured["output_file"] == "cli_out.yaml"
    assert captured["language"] == "ru"
    assert captured["min_cluster_size"] == 5
    assert captured["min_samples"] == 4
    assert captured["cluster_selection_epsilon"] == 0.2
    assert captured["cluster_selection_method"] == "leaf"
    assert captured["embed_model"] == "clip"
    assert captured["ollama_model"] == "llama3"
    assert captured["ollama_enabled"] is False
    assert captured["lookup_concepts"] is True


@pytest.mark.asyncio
async def test_main_boolean_flags_fall_back_to_env_when_omitted(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setattr("sys.argv", ["build_lemma_clusters"])
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    monkeypatch.setenv("LOOKUP_CONCEPTS", "true")

    await main()

    assert captured["ollama_enabled"] is False
    assert captured["lookup_concepts"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: the 5 pre-existing tests still PASS (the current `main()` ignores `sys.argv` entirely, so pinning it is a no-op until Step 3's implementation lands — these tests are just confirming env-var behavior hasn't regressed yet). The 2 new tests FAIL with `AssertionError`: `test_main_cli_flags_override_env_vars` fails because `captured["input_file"]` etc. reflect the env vars (`current main()` never reads the CLI flags at all), not the CLI-overridden values the test asserts; `test_main_boolean_flags_fall_back_to_env_when_omitted` should actually already pass by coincidence (it doesn't set any CLI flags), which is fine — it's there to guard the fallback path once CLI parsing exists, not to prove RED on its own.

- [ ] **Step 3: Write minimal implementation**

In `batch/build_lemma_clusters.py`, add to the top imports (alongside the existing `import asyncio`):

```python
import argparse
```

Replace the existing `main()` function (currently lines 222-246) with:

```python
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-scope", default=None)
    parser.add_argument("--bow-output-file", default=None)
    parser.add_argument("--bow-unmatched-file", default=None)
    parser.add_argument("--cluster-output-file", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--min-cluster-size", type=int, default=None)
    parser.add_argument("--min-samples", type=int, default=None)
    parser.add_argument("--cluster-selection-epsilon", type=float, default=None)
    parser.add_argument("--cluster-selection-method", default=None)
    parser.add_argument("--text-embed-model", default=None)
    parser.add_argument("--ollama-model", default=None)
    parser.add_argument("--ollama-enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--lookup-concepts", action=argparse.BooleanOptionalAction, default=None)
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    text_scope = args.text_scope if args.text_scope is not None else os.getenv("TEXT_SCOPE", "unmatched")

    if text_scope == "all":
        input_file = args.bow_output_file if args.bow_output_file is not None else os.getenv("BOW_OUTPUT_FILE")
    else:
        input_file = args.bow_unmatched_file if args.bow_unmatched_file is not None else os.getenv("BOW_UNMATCHED_FILE")

    output_file = args.cluster_output_file if args.cluster_output_file is not None else os.getenv("CLUSTER_OUTPUT_FILE")

    if not input_file:
        raise SystemExit(
            "BOW_OUTPUT_FILE must be set when TEXT_SCOPE=all, otherwise BOW_UNMATCHED_FILE must be set"
        )
    if not output_file:
        raise SystemExit("CLUSTER_OUTPUT_FILE must be set")

    if args.min_samples is not None:
        min_samples = args.min_samples
    else:
        min_samples = int(os.getenv("MIN_SAMPLES")) if os.getenv("MIN_SAMPLES") else None

    await run(
        input_file=input_file,
        output_file=output_file,
        language=args.language if args.language is not None else os.getenv("LANGUAGE", "all"),
        min_cluster_size=(
            args.min_cluster_size if args.min_cluster_size is not None
            else int(os.getenv("MIN_CLUSTER_SIZE", "2"))
        ),
        min_samples=min_samples,
        cluster_selection_epsilon=(
            args.cluster_selection_epsilon if args.cluster_selection_epsilon is not None
            else float(os.getenv("CLUSTER_SELECTION_EPSILON", "0.0"))
        ),
        cluster_selection_method=(
            args.cluster_selection_method if args.cluster_selection_method is not None
            else os.getenv("CLUSTER_SELECTION_METHOD", "eom")
        ),
        embed_model=args.text_embed_model if args.text_embed_model is not None else os.getenv("TEXT_EMBED_MODEL", "sbert"),
        ollama_model=args.ollama_model if args.ollama_model is not None else os.getenv("OLLAMA_MODEL", "qwen2"),
        ollama_enabled=(
            args.ollama_enabled if args.ollama_enabled is not None
            else os.getenv("OLLAMA_ENABLED", "true").lower() == "true"
        ),
        lookup_concepts=(
            args.lookup_concepts if args.lookup_concepts is not None
            else os.getenv("LOOKUP_CONCEPTS", "false").lower() == "true"
        ),
    )
```

Leave the `if __name__ == "__main__": asyncio.run(main())` guard at the end of the file unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: PASS (35 passed — 33 existing plus 2 new; the 5 updated tests still count individually, only their bodies changed)

- [ ] **Step 5: Run the full non-integration suite to check for regressions**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests -v`
Expected: all tests pass except the pre-existing `tests/integration/` errors (no live DB in this environment, unrelated to this change).

- [ ] **Step 6: Manually verify a real CLI invocation**

Run (from repo root, using the existing `bow.unmatched.general.json` as input — no live DB needed since `LOOKUP_CONCEPTS` stays off). `--ollama-enabled`/`--no-ollama-enabled` is a `BooleanOptionalAction` flag — pass one or the other bare, never `--ollama-enabled false`:

```bash
H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m batch.build_lemma_clusters \
  --bow-unmatched-file batch/output/bow.unmatched.general.json \
  --cluster-output-file batch/output/lemma_clusters.cli-smoke-test.yaml \
  --language ru \
  --min-cluster-size 5 \
  --cluster-selection-method leaf \
  --no-ollama-enabled
```

Expected: runs to completion, prints `Written to batch/output/lemma_clusters.cli-smoke-test.yaml`, and the output YAML's `parameters.min_cluster_size` is `5` and `parameters.cluster_selection_method` is `leaf` (confirming the CLI flags actually took effect, not just the tests). Delete the smoke-test output file afterward (`rm batch/output/lemma_clusters.cli-smoke-test.yaml`) — it's gitignored but no need to leave stray files around.

- [ ] **Step 7: Commit**

```bash
git add batch/build_lemma_clusters.py tests/batch/test_build_lemma_clusters.py
git commit -m "feat(batch): add CLI overrides for build_lemma_clusters env vars"
```

---

## Task 2: Documentation and environment file updates

**Depends on:** Task 1 (describes the CLI flags Task 1 implements).

**Files:**
- Modify: `docs/superpowers/specs/2026-07-01-build-lemma-clusters.md:147-149`
- Modify: `.claude/commands/draft-lemma-concepts.md` (Step 1 section)
- Modify: `environments/.env.general`

**Interfaces:**
- Consumes: the exact CLI flag names from Task 1 (`--bow-unmatched-file`, `--cluster-output-file`, `--language`, `--min-cluster-size`, `--cluster-selection-method`, `--ollama-model`, `--ollama-enabled`/`--no-ollama-enabled`, `--text-embed-model`).

- [ ] **Step 1: Update the spec's Configuration table**

In `docs/superpowers/specs/2026-07-01-build-lemma-clusters.md`, find this exact text:

```
| `LOOKUP_CONCEPTS` | `false` | Compare cluster centroid to DB concept embeddings; attach nearest match. Requires `TEXT_EMBED_MODEL=clip` (see Error Handling) |

---

## Output Format
```

Replace it with:

```
| `LOOKUP_CONCEPTS` | `false` | Compare cluster centroid to DB concept embeddings; attach nearest match. Requires `TEXT_EMBED_MODEL=clip` (see Error Handling) |

Every variable above also has a same-purpose CLI flag on `batch/build_lemma_clusters.py` that overrides it when passed (e.g. `--cluster-selection-method leaf` overrides `CLUSTER_SELECTION_METHOD`; `--ollama-enabled`/`--no-ollama-enabled` overrides `OLLAMA_ENABLED`). Running with no CLI flags reproduces the pure-env-var behavior described above exactly — the CLI is a convenience for ad hoc runs, not a replacement for the env-var-driven pipeline usage.

---

## Output Format
```

- [ ] **Step 2: Simplify the `/draft-lemma-concepts` command to use CLI flags**

In `.claude/commands/draft-lemma-concepts.md`, find the `## Step 1: Cluster the unmatched lemmas` section's final code block:

```
Run the clustering batch:

```bash
DATABASE_URL="$DATABASE_URL" \
BASE_PATH="$BASE_PATH" \
BOW_UNMATCHED_FILE=batch/output/bow.unmatched.<env>.json \
CLUSTER_OUTPUT_FILE=batch/output/lemma_clusters.<env>.<language>.yaml \
LANGUAGE=<language> \
TEXT_EMBED_MODEL=sbert \
OLLAMA_ENABLED=true \
OLLAMA_MODEL=<ollama-model> \
MIN_CLUSTER_SIZE=<min-cluster-size> \
CLUSTER_SELECTION_METHOD=<cluster-selection-method> \
.venv311/Scripts/python -m batch.build_lemma_clusters
```
```

Replace it with:

```
Run the clustering batch (`DATABASE_URL`/`BASE_PATH` stay as env vars — `Storage/db.py` only reads them from the environment; every other setting is passed as a CLI flag, which overrides the same-named env var if one happens to be set):

```bash
DATABASE_URL="$DATABASE_URL" \
BASE_PATH="$BASE_PATH" \
.venv311/Scripts/python -m batch.build_lemma_clusters \
  --bow-unmatched-file batch/output/bow.unmatched.<env>.json \
  --cluster-output-file batch/output/lemma_clusters.<env>.<language>.yaml \
  --language <language> \
  --text-embed-model sbert \
  --ollama-enabled \
  --ollama-model <ollama-model> \
  --min-cluster-size <min-cluster-size> \
  --cluster-selection-method <cluster-selection-method>
```
```

- [ ] **Step 3: Add `CLUSTER_SELECTION_METHOD` to `environments/.env.general`**

Append to the end of `environments/.env.general` (current last line is `TAGGING_PROFILE=general`):

```
# Empirically validated for this environment's OCR data (see CLAUDE.md's
# Concept discovery note) — leaf avoids one oversized catch-all cluster.
CLUSTER_SELECTION_METHOD=leaf
```

- [ ] **Step 4: Verify the doc edits render correctly and the env file is valid**

```bash
grep -n "CLUSTER_SELECTION_METHOD" H:/workspace_sandbox/memes/environments/.env.general
```
Expected: prints the new line, confirming it was added without corrupting the file.

```bash
grep -n "same-purpose CLI flag" H:/workspace_sandbox/memes/docs/superpowers/specs/2026-07-01-build-lemma-clusters.md
```
Expected: prints the new sentence, confirming the spec edit landed.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-01-build-lemma-clusters.md .claude/commands/draft-lemma-concepts.md environments/.env.general
git commit -m "docs: document build_lemma_clusters CLI overrides and add CLUSTER_SELECTION_METHOD to general env"
```

---

## Self-Review Notes

- **Spec coverage:** CLI flags for all 13 knobs (Task 1), precedence order (Task 1), `run()` unchanged (Task 1, no edits to it), boolean flags via `BooleanOptionalAction` (Task 1), no new env vars (Task 1 — confirmed no new `os.getenv` calls added, only `args.*` reads), `CLUSTER_SELECTION_METHOD` added only to `.env.general` (Task 2), `CLUSTER_OUTPUT_FILE` NOT added to any `.env.*` file (Task 2 — not touched), spec/command doc updates (Task 2).
- **Critical regression risk called out explicitly:** introducing `argparse` into `main()` changes what `sys.argv` needs to look like under test — every existing test needed `monkeypatch.setattr("sys.argv", ...)` added, or they'd silently start parsing pytest's own CLI arguments. Task 1 Step 1 addresses this for all 5 pre-existing tests, not just the 2 new ones.
- **Not implemented (explicitly out of scope per spec Non-Goals):** no change to `draft_concepts_from_clusters.py` (already pure CLI), no `CLUSTER_SELECTION_METHOD` in `.env.metal`/`.env.it`.
