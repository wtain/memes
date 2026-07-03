# draft_concepts_from_clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `batch/draft_concepts_from_clusters.py`, a CLI script that takes the top N clusters from a `build_lemma_clusters` output YAML (for one language) and appends draft concept entries to `batch/data/tagging/concepts.<env>.yaml` plus matching tag declarations to `batch/data/tagging/tags.<env>.yaml`, for human review via `git diff`.

**Architecture:** A single self-contained module composed of small pure functions (slugify a name, extract a cluster's top lemma, collect existing words/keys/tags from the two target files, select+backfill the top N non-colliding clusters, format the two kinds of appended YAML text) plus a thin `run()`/`main()` orchestration layer. All file I/O is either a full-file `yaml.safe_load` (read-only, used only for collision checks) or a raw text append (write) — the script never re-dumps or reformats existing YAML content.

**Tech Stack:** Python 3.11 (`.venv311`), `pyyaml` (already a dependency), `argparse` (stdlib). No new dependencies.

## Global Constraints

- Target files are only the new-design pair: `batch/data/tagging/concepts.<env>.yaml` and `batch/data/tagging/tags.<env>.yaml`. The legacy `batch/data/rules.<env>.json` is never touched.
- Both files must already exist for the given `--env` — this script only appends, it never scaffolds a new profile. Missing either file is a fail-fast error.
- Writes are plain text appends only. Existing file content, comments, and ordering must never be reformatted or reparsed-and-rewritten.
- A cluster is skipped (not counted toward N) if its top lemma (highest-frequency member — first key in its `members` mapping) already appears in any existing concept's `words:` list **or** any `fuzzy:` entry's `word` field, anywhere in `concepts.<env>.yaml`.
- Skipped clusters are backfilled from the next-ranked cluster — the run keeps going until N clusters are accepted or the cluster list is exhausted (shortfall is reported, not an error).
- Concept key = slugified `ollama_concept`, falling back to the slugified top lemma if `ollama_concept` is null/empty. If the resulting key collides with an existing or already-generated-this-run key, disambiguate with a numeric suffix (`_2`, `_3`, ...) — never skip a cluster purely for a key-name collision.
- Every new concept gets exactly one vote: `тема:<top_lemma>: 1.0`. No other tag category or multiple votes are invented.
- If `тема:<top_lemma>` is not already declared in `tags.<env>.yaml`, append it (`  тема:<top_lemma>: {}`); if already declared, don't duplicate it.
- `--top <= 0` → nothing added, no error, summary reports "0 concepts added".
- Missing `--cluster-file`, or `--language` absent from the cluster file's `languages` mapping, are fail-fast errors with a clear message.
- No new dependencies — reuse `yaml.safe_load` (already used throughout `batch/`).

---

## File Structure

| File | Responsibility |
|---|---|
| `batch/draft_concepts_from_clusters.py` | *(new)* Everything: slugify/collision/selection/formatting helpers, `run()` orchestration, `main()` CLI entry point. Single file — this is a small, self-contained script per the spec's own "Reuse and Refactoring" section (no existing utilities extracted or reused beyond `yaml`). |
| `tests/batch/test_draft_concepts_from_clusters.py` | *(new)* Unit tests for every helper plus end-to-end `run()`/`main()` tests using `tmp_path` fixtures (no real `batch/data/tagging/` files touched). |

Both files are built up across the 4 tasks below (each task appends to both, never modifying an earlier task's functions/tests).

**Note on testability:** the spec describes concept/tag file paths as resolving to `batch/data/tagging/{concepts,tags}.<env>.yaml`. To keep `run()` testable against temporary fixture files instead of the repo's real tagging data, `run()` takes an explicit `data_dir` parameter (default `"batch/data/tagging"`, matching `rules/concept_tagger.py`'s own `ConceptTagger.load(data_dir, profile)` signature — this mirrors an already-established pattern in this codebase, not a new one). `main()` exposes this as an optional `--data-dir` flag; end users never need to pass it since the default matches the spec exactly.

---

## Task 1: Pure helpers — slugify, top lemma, existing-state collectors, key resolution

**Files:**
- Create: `batch/draft_concepts_from_clusters.py`
- Create: `tests/batch/test_draft_concepts_from_clusters.py`

**Interfaces:**
- Produces:
  - `slugify(name: str | None) -> str`
  - `top_lemma(cluster: dict) -> str`
  - `collect_existing_words(concepts_data: dict | None) -> set[str]`
  - `collect_existing_keys(concepts_data: dict | None) -> set[str]`
  - `collect_declared_tags(tags_data: dict) -> set[str]`
  - `resolve_key(ollama_concept: str | None, lemma: str, existing_keys: set[str]) -> str`

  All consumed by Task 2 (`select_top_clusters`, via `top_lemma`) and Task 4 (`run()`, via all of them).

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch/test_draft_concepts_from_clusters.py
from batch.draft_concepts_from_clusters import (
    collect_declared_tags,
    collect_existing_keys,
    collect_existing_words,
    resolve_key,
    slugify,
    top_lemma,
)


def test_slugify_quoted_multiword_name():
    assert slugify('"Local Dialect Words"') == "local_dialect_words"


def test_slugify_single_word():
    assert slugify("Memes") == "memes"


def test_slugify_cyrillic_name():
    assert slugify("Сон") == "сон"


def test_slugify_none_returns_empty():
    assert slugify(None) == ""


def test_slugify_empty_string_returns_empty():
    assert slugify("") == ""


def test_slugify_collapses_punctuation():
    assert slugify("Purchase / Activities!!") == "purchase_activities"


def test_top_lemma_returns_first_member():
    cluster = {"members": {"спать": 640, "сон": 100}}
    assert top_lemma(cluster) == "спать"


def test_collect_existing_words_includes_words_and_fuzzy():
    concepts_data = {
        "sleep": {"words": ["спать", "сон"]},
        "salary": {"words": ["зарплата"], "fuzzy": [{"word": "зорплата", "threshold": 85}]},
    }
    result = collect_existing_words(concepts_data)
    assert result == {"спать", "сон", "зарплата", "зорплата"}


def test_collect_existing_words_handles_missing_words_key():
    assert collect_existing_words({"empty": {}}) == set()


def test_collect_existing_words_handles_none():
    assert collect_existing_words(None) == set()


def test_collect_existing_keys():
    assert collect_existing_keys({"sleep": {}, "family": {}}) == {"sleep", "family"}


def test_collect_existing_keys_handles_none():
    assert collect_existing_keys(None) == set()


def test_collect_declared_tags():
    tags_data = {"defaults": {"threshold": 1.0}, "tags": {"тема:сон": {}, "тема:семья": {}}}
    assert collect_declared_tags(tags_data) == {"тема:сон", "тема:семья"}


def test_resolve_key_uses_slugified_ollama_name():
    assert resolve_key("Sleeping", "спать", existing_keys=set()) == "sleeping"


def test_resolve_key_falls_back_to_lemma_when_no_ollama_name():
    assert resolve_key(None, "спать", existing_keys=set()) == "спать"


def test_resolve_key_disambiguates_collision():
    assert resolve_key("Sleeping", "спать", existing_keys={"sleeping"}) == "sleeping_2"


def test_resolve_key_disambiguates_multiple_collisions():
    key = resolve_key("Sleeping", "спать", existing_keys={"sleeping", "sleeping_2"})
    assert key == "sleeping_3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.draft_concepts_from_clusters'`

- [ ] **Step 3: Write minimal implementation**

```python
# batch/draft_concepts_from_clusters.py
import re


def slugify(name: str | None) -> str:
    if not name:
        return ""
    text = name.strip().strip('"').strip("'").lower()
    text = re.sub(r'[^a-z0-9Ѐ-ӿ]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text


def top_lemma(cluster: dict) -> str:
    return next(iter(cluster["members"]))


def collect_existing_words(concepts_data: dict | None) -> set[str]:
    words: set[str] = set()
    for concept in (concepts_data or {}).values():
        for w in (concept.get("words") or []):
            words.add(w)
        for fe in (concept.get("fuzzy") or []):
            words.add(fe["word"])
    return words


def collect_existing_keys(concepts_data: dict | None) -> set[str]:
    return set((concepts_data or {}).keys())


def collect_declared_tags(tags_data: dict) -> set[str]:
    return set((tags_data.get("tags") or {}).keys())


def resolve_key(ollama_concept: str | None, lemma: str, existing_keys: set[str]) -> str:
    base = slugify(ollama_concept) or slugify(lemma)
    key = base
    suffix = 2
    while key in existing_keys:
        key = f"{base}_{suffix}"
        suffix += 1
    return key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/draft_concepts_from_clusters.py tests/batch/test_draft_concepts_from_clusters.py
git commit -m "feat(batch): add slugify/collision-detection helpers for draft_concepts_from_clusters"
```

---

## Task 2: Top-N selection with skip-and-backfill

**Depends on:** Task 1 (`top_lemma`, same file — appends to it).

**Files:**
- Modify: `batch/draft_concepts_from_clusters.py`
- Modify: `tests/batch/test_draft_concepts_from_clusters.py`

**Interfaces:**
- Consumes: `top_lemma(cluster: dict) -> str` (Task 1)
- Produces: `select_top_clusters(clusters: list[dict], existing_words: set[str], top: int) -> tuple[list[dict], list[dict]]` — returns `(accepted, skipped)`. `accepted` has at most `top` clusters (in input order) whose top lemma is not in `existing_words`; `skipped` holds every cluster that was visited and passed over because its top lemma was already covered. Clusters beyond whatever was needed to fill `top` accepted slots are never visited (neither accepted nor skipped). Consumed by Task 4's `run()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_draft_concepts_from_clusters.py`:

```python
from batch.draft_concepts_from_clusters import select_top_clusters


def _cluster(word: str, freq: int = 10) -> dict:
    return {
        "members": {word: freq},
        "total_frequency": freq,
        "size": 1,
        "ollama_concept": word.title(),
    }


def test_select_top_clusters_accepts_up_to_top_n():
    clusters = [_cluster("a"), _cluster("b"), _cluster("c")]

    accepted, skipped = select_top_clusters(clusters, existing_words=set(), top=2)

    assert [top_lemma(c) for c in accepted] == ["a", "b"]
    assert skipped == []


def test_select_top_clusters_skips_already_covered_and_backfills():
    clusters = [_cluster("a"), _cluster("b"), _cluster("c")]

    accepted, skipped = select_top_clusters(clusters, existing_words={"a"}, top=2)

    assert [top_lemma(c) for c in accepted] == ["b", "c"]
    assert [top_lemma(c) for c in skipped] == ["a"]


def test_select_top_clusters_reports_shortfall_when_not_enough_available():
    clusters = [_cluster("a"), _cluster("b")]

    accepted, skipped = select_top_clusters(clusters, existing_words={"a"}, top=5)

    assert [top_lemma(c) for c in accepted] == ["b"]
    assert [top_lemma(c) for c in skipped] == ["a"]


def test_select_top_clusters_zero_top_adds_nothing():
    clusters = [_cluster("a")]

    accepted, skipped = select_top_clusters(clusters, existing_words=set(), top=0)

    assert accepted == []
    assert skipped == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_top_clusters' from 'batch.draft_concepts_from_clusters'`

- [ ] **Step 3: Write minimal implementation**

Append to `batch/draft_concepts_from_clusters.py`:

```python
def select_top_clusters(
    clusters: list[dict],
    existing_words: set[str],
    top: int,
) -> tuple[list[dict], list[dict]]:
    """
    Walk clusters in order (already frequency-descending, per
    build_lemma_clusters). Returns (accepted, skipped): accepted has at
    most `top` clusters whose top lemma is not in existing_words; skipped
    holds every cluster visited and passed over because it was already
    covered. Clusters beyond what's needed to fill `top` slots are never
    visited.
    """
    accepted: list[dict] = []
    skipped: list[dict] = []
    for cluster in clusters:
        if len(accepted) >= top:
            break
        lemma = top_lemma(cluster)
        if lemma in existing_words:
            skipped.append(cluster)
            continue
        accepted.append(cluster)
    return accepted, skipped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: PASS (21 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/draft_concepts_from_clusters.py tests/batch/test_draft_concepts_from_clusters.py
git commit -m "feat(batch): add top-N cluster selection with skip-and-backfill"
```

---

## Task 3: YAML formatting and file-append mechanics

**Depends on:** Task 1 (same file — appends to it; no direct call dependency on Task 1/2 functions, but shares the module).

**Files:**
- Modify: `batch/draft_concepts_from_clusters.py`
- Modify: `tests/batch/test_draft_concepts_from_clusters.py`

**Interfaces:**
- Produces:
  - `format_concept_block(key: str, cluster: dict, lemma: str) -> str` — the comment + concept YAML block to append to `concepts.<env>.yaml`, ending with `\n`.
  - `format_tag_declaration(lemma: str) -> str` — the single `  тема:<lemma>: {}\n` line to append to `tags.<env>.yaml`.
  - `append_to_file(path: str, text: str) -> None` — appends `text` to the file at `path`, inserting a leading `\n` first only if the file doesn't already end with one.

  All consumed by Task 4's `run()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_draft_concepts_from_clusters.py`:

```python
from batch.draft_concepts_from_clusters import append_to_file, format_concept_block, format_tag_declaration


def test_format_concept_block_with_ollama_name():
    cluster = {
        "ollama_concept": "Sleeping",
        "total_frequency": 640,
        "size": 3,
        "members": {"спать": 500, "сон": 100, "уснуть": 40},
    }

    text = format_concept_block("sleeping", cluster, "спать")

    assert text == (
        '# Ollama suggested: "Sleeping" (freq=640, size=3, from build_lemma_clusters)\n'
        "sleeping:\n"
        "  words:\n"
        "  - спать\n"
        "  - сон\n"
        "  - уснуть\n"
        "  votes:\n"
        "    тема:спать: 1.0\n"
    )


def test_format_concept_block_without_ollama_name():
    cluster = {
        "ollama_concept": None,
        "total_frequency": 10,
        "size": 2,
        "members": {"a": 5, "b": 5},
    }

    text = format_concept_block("a", cluster, "a")

    assert text == (
        "# from build_lemma_clusters (no Ollama name)\n"
        "a:\n"
        "  words:\n"
        "  - a\n"
        "  - b\n"
        "  votes:\n"
        "    тема:a: 1.0\n"
    )


def test_format_tag_declaration():
    assert format_tag_declaration("спать") == "  тема:спать: {}\n"


def test_append_to_file_adds_leading_newline_when_missing(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("existing: true", encoding="utf-8")

    append_to_file(str(path), "new: 1\n")

    assert path.read_text(encoding="utf-8") == "existing: true\nnew: 1\n"


def test_append_to_file_no_extra_newline_when_already_present(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("existing: true\n", encoding="utf-8")

    append_to_file(str(path), "new: 1\n")

    assert path.read_text(encoding="utf-8") == "existing: true\nnew: 1\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_concept_block' from 'batch.draft_concepts_from_clusters'`

- [ ] **Step 3: Write minimal implementation**

Append to `batch/draft_concepts_from_clusters.py`:

```python
def format_concept_block(key: str, cluster: dict, lemma: str) -> str:
    ollama_concept = cluster.get("ollama_concept")
    if ollama_concept:
        comment = (
            f'# Ollama suggested: "{ollama_concept}" '
            f'(freq={cluster["total_frequency"]}, size={cluster["size"]}, from build_lemma_clusters)'
        )
    else:
        comment = "# from build_lemma_clusters (no Ollama name)"

    lines = [comment, f"{key}:", "  words:"]
    for member in cluster["members"]:
        lines.append(f"  - {member}")
    lines.append("  votes:")
    lines.append(f"    тема:{lemma}: 1.0")
    return "\n".join(lines) + "\n"


def format_tag_declaration(lemma: str) -> str:
    return f"  тема:{lemma}: {{}}\n"


def append_to_file(path: str, text: str) -> None:
    with open(path, encoding="utf-8") as f:
        existing = f.read()
    prefix = "" if existing.endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: PASS (26 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/draft_concepts_from_clusters.py tests/batch/test_draft_concepts_from_clusters.py
git commit -m "feat(batch): add YAML formatting and append-only file writing for draft concepts"
```

---

## Task 4: `run()`/`main()` orchestration

**Depends on:** Tasks 1-3 (same file — appends to it, calling everything defined so far).

**Files:**
- Modify: `batch/draft_concepts_from_clusters.py`
- Modify: `tests/batch/test_draft_concepts_from_clusters.py`

**Interfaces:**
- Consumes: `slugify`, `top_lemma`, `collect_existing_words`, `collect_existing_keys`, `collect_declared_tags`, `resolve_key` (Task 1); `select_top_clusters` (Task 2); `format_concept_block`, `format_tag_declaration`, `append_to_file` (Task 3).
- Produces:
  - `load_yaml(path: str) -> dict` — thin `yaml.safe_load` wrapper.
  - `run(cluster_file: str, env: str, language: str, top: int, data_dir: str = "batch/data/tagging") -> None`
  - `main() -> None` — parses `argparse` CLI (`--cluster-file`, `--env`, `--language`, `--top` default `10`, `--data-dir` default `"batch/data/tagging"`) and calls `run(...)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_draft_concepts_from_clusters.py`:

```python
import textwrap

import pytest
import yaml

from batch.draft_concepts_from_clusters import main, run


def _write(path, content):
    path.write_text(content, encoding="utf-8")


def test_run_end_to_end_appends_new_concept_and_tag(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    concepts_path = data_dir / "concepts.general.yaml"
    tags_path = data_dir / "tags.general.yaml"
    _write(concepts_path, "existing_concept:\n  words:\n  - existing_word\n  votes:\n    тема:existing: 1.0")
    _write(tags_path, "defaults:\n  threshold: 1.0\ntags:\n  тема:existing: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))

    concepts_data = yaml.safe_load(concepts_path.read_text(encoding="utf-8"))
    assert concepts_data["sleeping"]["words"] == ["спать", "сон"]
    assert concepts_data["sleeping"]["votes"] == {"тема:спать": 1.0}
    assert concepts_data["existing_concept"]["words"] == ["existing_word"]

    tags_data = yaml.safe_load(tags_path.read_text(encoding="utf-8"))
    assert "тема:спать" in tags_data["tags"]
    assert "тема:existing" in tags_data["tags"]


def test_run_skips_cluster_already_covered_and_backfills(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    concepts_path = data_dir / "concepts.general.yaml"
    tags_path = data_dir / "tags.general.yaml"
    _write(concepts_path, "sleep:\n  words:\n  - спать\n  votes:\n    тема:спать: 1.0")
    _write(tags_path, "defaults:\n  threshold: 1.0\ntags:\n  тема:спать: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
              - id: 2
                ollama_concept: "Purchases"
                total_frequency: 300
                size: 2
                members:
                  купить: 200
                  покупать: 100
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))

    concepts_data = yaml.safe_load(concepts_path.read_text(encoding="utf-8"))
    assert "purchases" in concepts_data
    assert "sleeping" not in concepts_data


def test_run_missing_language_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    _write(data_dir / "tags.general.yaml", "tags: {}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(ValueError, match="es"):
        run(str(cluster_file), env="general", language="es", top=1, data_dir=str(data_dir))


def test_run_missing_concepts_file_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "tags.general.yaml", "tags: {}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(FileNotFoundError):
        run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))


def test_run_missing_tags_file_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(FileNotFoundError):
        run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))


def test_run_prints_shortfall_warning_when_not_enough_clusters(tmp_path, capsys):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    _write(data_dir / "tags.general.yaml", "tags: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=5, data_dir=str(data_dir))

    out = capsys.readouterr().out
    assert "Added 1 concept" in out
    assert "only 1" in out.lower()


def test_main_parses_args_and_calls_run(monkeypatch):
    captured = {}

    def fake_run(cluster_file, env, language, top, data_dir):
        captured.update(cluster_file=cluster_file, env=env, language=language, top=top, data_dir=data_dir)

    monkeypatch.setattr("batch.draft_concepts_from_clusters.run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["draft_concepts_from_clusters", "--cluster-file", "c.yaml", "--env", "general", "--language", "ru", "--top", "5"],
    )

    main()

    assert captured == {
        "cluster_file": "c.yaml",
        "env": "general",
        "language": "ru",
        "top": 5,
        "data_dir": "batch/data/tagging",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: FAIL with `ImportError: cannot import name 'run' from 'batch.draft_concepts_from_clusters'`

- [ ] **Step 3: Write minimal implementation**

Add these imports to the top of `batch/draft_concepts_from_clusters.py` (alongside the existing `import re`):

```python
import argparse
import os

import yaml
```

Append the orchestration code:

```python
def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(
    cluster_file: str,
    env: str,
    language: str,
    top: int,
    data_dir: str = "batch/data/tagging",
) -> None:
    cluster_data = load_yaml(cluster_file)
    languages = cluster_data.get("languages") or {}
    if language not in languages:
        raise ValueError(f"Language {language!r} not found in {cluster_file}")
    clusters = languages[language].get("clusters") or []

    concepts_path = os.path.join(data_dir, f"concepts.{env}.yaml")
    tags_path = os.path.join(data_dir, f"tags.{env}.yaml")

    if not os.path.exists(concepts_path):
        raise FileNotFoundError(f"{concepts_path} does not exist")
    if not os.path.exists(tags_path):
        raise FileNotFoundError(f"{tags_path} does not exist")

    concepts_data = load_yaml(concepts_path) or {}
    tags_data = load_yaml(tags_path) or {}

    existing_words = collect_existing_words(concepts_data)
    existing_keys = collect_existing_keys(concepts_data)
    declared_tags = collect_declared_tags(tags_data)

    accepted, skipped = select_top_clusters(clusters, existing_words, top)

    added = []
    for cluster in accepted:
        lemma = top_lemma(cluster)
        key = resolve_key(cluster.get("ollama_concept"), lemma, existing_keys)
        existing_keys.add(key)

        append_to_file(concepts_path, format_concept_block(key, cluster, lemma))

        tag = f"тема:{lemma}"
        if tag not in declared_tags:
            append_to_file(tags_path, format_tag_declaration(lemma))
            declared_tags.add(tag)

        added.append((key, lemma, cluster))

    _print_summary(added, skipped, top, concepts_path, tags_path)


def _print_summary(added, skipped, top, concepts_path, tags_path):
    for key, lemma, cluster in added:
        print(f"  + {key} (тема:{lemma}, {cluster['size']} members, ollama: {cluster.get('ollama_concept')!r})")
    for cluster in skipped:
        print(f"  - skipped cluster with top lemma {top_lemma(cluster)!r} (already covered)")
    print(f"Added {len(added)} concept(s) (requested {top}) to {concepts_path} and {tags_path}")
    if len(added) < top:
        print(f"WARNING: only {len(added)} non-colliding cluster(s) were available (requested {top})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-file", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--data-dir", default="batch/data/tagging")
    args = parser.parse_args()

    run(args.cluster_file, args.env, args.language, args.top, args.data_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/batch/test_draft_concepts_from_clusters.py -v`
Expected: PASS (33 passed)

- [ ] **Step 5: Run the full non-integration suite to check for regressions**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests -v`
Expected: all tests pass except the pre-existing `tests/integration/` errors (no live DB in this environment — unrelated to this change).

- [ ] **Step 6: Commit**

```bash
git add batch/draft_concepts_from_clusters.py tests/batch/test_draft_concepts_from_clusters.py
git commit -m "feat(batch): wire up draft_concepts_from_clusters run()/main() CLI"
```

---

## Self-Review Notes

- **Spec coverage:** CLI interface (Task 4), selection/ranking/skip-and-backfill (Task 2), key derivation + slugification + collision disambiguation (Task 1), collision detection via `words:`/`fuzzy:` (Task 1 + spec self-review fix), default single `тема` vote + tag registration (Task 4), append-only writes with correct newline handling (Task 3), all Error Handling table rows (missing cluster file — natural `FileNotFoundError` from `load_yaml`; missing `--language`; missing concepts/tags file; shortfall; null `ollama_concept`; key collision; already-declared tag; already-covered cluster; `--top <= 0`) covered across Tasks 1-4.
- **Not implemented (explicitly out of scope per spec Non-Goals):** legacy `rules.<env>.json` output, choosing tag categories beyond `тема`, the `decision:`-field annotation workflow from the parent `build_lemma_clusters` spec, cluster-membership curation, cross-language merging.
- **Deviation from spec (minor, disclosed):** `run()` takes an explicit `data_dir` parameter (default `"batch/data/tagging"`, exposed as optional `--data-dir` on the CLI) instead of hardcoding the path, so tests can point it at `tmp_path` fixtures. The default reproduces the spec's stated path exactly; end users never need to pass it. This mirrors `rules/concept_tagger.py`'s own `ConceptTagger.load(data_dir, profile)` signature, an already-established pattern in this codebase.
