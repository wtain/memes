# P1 Taxonomy and Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the shared meaning taxonomy for the meme-from-text epic: validated vocabularies, a loader/validator package, shared profile JSON schemas with generated types, an Ollama embedding client with a recorded model decision, and a generated vocabulary-similarity artifact, so P2 (extraction) and P3 (query parsing) can start from one contract.

**Architecture:** A pure-Python `taxonomy/` package (PyYAML only) loads YAML vocabularies from `batch/data/taxonomy/`, validates profiles, renders prompt choice lists and guards content with a version-plus-hash lock file. Anything needing numpy, Ollama or the DB (similarity, coverage probe, embedding client) lives outside the package's core modules, so the backend can import the core without ML libraries. Table design is fixed in the spec but the migration is written in P2.

**Tech Stack:** Python 3.11 (`.venv311`), PyYAML, numpy, `ollama` 0.6.2 client, pytest, JSON Schema draft-07 with `json2ts` / `datamodel-codegen` / `generate_dtos.py` type generation, SQLAlchemy models file (constant only).

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md` (read it first; also `adr/ADR-002-axis-taxonomy-and-representation.md`).

## Global Constraints

- Run every command from the repo root with `.venv311` active (PowerShell: `.\.venv311\Scripts\Activate.ps1`). Run each pytest root as its own command; never combine `Backend/tests`, `tests/integration`, `batch/tests`, `tests/rules`, `tests/ai` or `tests/taxonomy` roots in one invocation (CLAUDE.md gotcha). New tests here live in `tests/taxonomy/`, `tests/ai/` and `batch/tests/`.
- Never `pip install` anything into `.venv311` (venv-rot gotcha). Everything needed is already installed: `PyYAML`, `numpy`, `ollama==0.6.2`, `pytest`. `jsonschema` is NOT installed: do not use it. Validate schemas through the generated Pydantic models instead.
- The core modules `taxonomy/model.py`, `loader.py`, `validate.py`, `prompt.py`, `lint.py`, `lock.py` may import only the standard library, `yaml` and `taxonomy.*` (the backend imports them and `Backend/requirements-backend.txt` forbids ML libraries). `taxonomy/__init__.py` must not import `similarity.py` or `coverage.py`.
- Vocabulary keys: lowercase ASCII `snake_case`, at most 48 characters, unique within an axis, immutable once released. Every key has an English `definition` of at most 200 characters and labels for `en`, `ru`, `es`. Axis names at most 16 characters.
- Initial vocabulary sizes (spec): `emotion` 26, `stance` 15, `tone` 10, `topic` 16. Lint ranges: emotion 20-30, stance 10-18, tone 8-12, topic 12-20.
- Taxonomy `version` is an integer starting at 1. Any content change after the lock exists must bump it.
- Vectors are L2-normalised float32 and compared with cosine. New vectors never share a table or space with the existing `bge-large-en-v1.5` (sentence-transformers) vectors.
- A live database is only queried through `DATABASE_URL_READONLY` (role `ocr_readonly`), and only by the controller session, never by a subagent (CLAUDE.md, "Live database access for agents"). Never bind ports listed in `environments/Environments.md`.
- Windows: set `PYTHONIOENCODING=utf-8` before running batch-style scripts that print progress.
- Commit messages end with the attribution lines the session specifies. Write files with LF line endings where tooling hashes them (the taxonomy hash normalises CRLF to LF, see Task 2).
- Do not edit generated type trees by hand; regenerate them (Task 7). Do not commit unrelated generator drift: if regeneration changes pre-existing generated files, stop and report instead of committing it.

## File Structure

| Path | Responsibility |
|---|---|
| `ai/ollama_embed.py` (create) | `OllamaEmbedder`: embed texts via Ollama, normalised; look up model digest |
| `batch/tools/probe_embedding_model.py` (create) | One-off sanity probe for the embedding model (not CI) |
| `taxonomy/__init__.py` (create) | Public API re-exports (core only) |
| `taxonomy/model.py` (create) | Frozen dataclasses: `AxisSpec`, `VocabValue`, `Taxonomy` |
| `taxonomy/loader.py` (create) | YAML parsing, duplicate-key rejection, content hash, `load()` |
| `taxonomy/validate.py` (create) | `validate_profile()` |
| `taxonomy/prompt.py` (create) | `vocabulary_prompt()` |
| `taxonomy/lint.py` (create) | `lint()` rules for vocabulary content |
| `taxonomy/lock.py` (create) | Version-plus-hash lock: `check()`, `write_lock()`, `python -m taxonomy.lock` |
| `taxonomy/coverage.py` (create) | Batch-side: parse probe responses, summarise coverage, markdown report |
| `taxonomy/similarity.py` (create) | Batch-side (numpy): neighbours, overrides, artifact build and check |
| `batch/data/taxonomy/*.yaml`, `taxonomy.lock`, `similarity.json` (create) | The shipped taxonomy and generated files |
| `batch/tools/probe_vocabulary_coverage.py` (create) | One-off corpus sample probe (read-only DB + Ollama), not CI |
| `batch/build_taxonomy_similarity.py` (create) | CLI that generates `similarity.json` |
| `shared/schemas/profilevalue.schema.json`, `memeprofile.schema.json`, `queryprofile.schema.json` (create) | Shared profile contracts |
| `shared/schemas/all.schema.json` (modify) | Register the three schemas |
| `environments/settings.yaml` (modify) | `taxonomy.*` keys |
| `Storage/models.py` (modify) | `PROFILE_EMBEDDING_DIM` constant only |
| `tests/taxonomy/**`, `tests/ai/test_ollama_embed.py`, `batch/tests/test_probe_embedding_model.py` (create) | Tests |
| `docs/data-flow.md`, `CLAUDE.md`, spec, tracker (modify) | Documentation |

---

### Task 1: Ollama embedding client, model probe and decision record

**Files:**
- Create: `ai/ollama_embed.py`
- Create: `batch/tools/probe_embedding_model.py`
- Create: `tests/ai/test_ollama_embed.py`
- Create: `batch/tests/test_probe_embedding_model.py`
- Modify: `Storage/models.py` (constant after `TEXT_EMBEDDING_DIM`, around line 19)
- Modify: `environments/settings.yaml` (append `taxonomy:` group)
- Modify: the spec (append an "Addendum: embedding probe result" section)

**Interfaces:**
- Produces: `ai.ollama_embed.OllamaEmbedder(model: str)` with `embed(texts: list[str]) -> numpy.ndarray` (shape `(n, dim)`, float32, L2-normalised; `(0, 0)` for empty input) and `model_digest() -> str`; `batch.tools.probe_embedding_model.evaluate(embedder) -> list[tuple[str, float, float, bool]]`; `Storage.models.PROFILE_EMBEDDING_DIM: int`; `settings.TAXONOMY.EMBEDDING_MODEL: str`.

- [ ] **Step 1: Write the failing tests for the embedder**

Create `tests/ai/test_ollama_embed.py`:

```python
import numpy as np
import ollama
import pytest

from ai.ollama_embed import OllamaEmbedder


def test_embed_returns_l2_normalised_float32(monkeypatch):
    def fake_embed(model, input):
        assert model == "fake-embed"
        assert input == ["a", "b"]
        return {"embeddings": [[3.0, 4.0], [0.0, 5.0]]}

    monkeypatch.setattr(ollama, "embed", fake_embed)
    result = OllamaEmbedder("fake-embed").embed(["a", "b"])

    assert result.dtype == np.float32
    assert result.shape == (2, 2)
    np.testing.assert_allclose(result, [[0.6, 0.8], [0.0, 1.0]], atol=1e-6)


def test_embed_empty_input_does_not_call_ollama(monkeypatch):
    def boom(**kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(ollama, "embed", boom)
    assert OllamaEmbedder("fake-embed").embed([]).shape == (0, 0)


def test_embed_zero_vector_stays_zero(monkeypatch):
    monkeypatch.setattr(ollama, "embed", lambda model, input: {"embeddings": [[0.0, 0.0]]})
    np.testing.assert_array_equal(OllamaEmbedder("m").embed(["x"]), [[0.0, 0.0]])


def test_model_digest_matches_with_or_without_latest_tag(monkeypatch):
    listing = {"models": [{"model": "other:latest", "digest": "zzz"},
                          {"model": "bge-large:latest", "digest": "abc123"}]}
    monkeypatch.setattr(ollama, "list", lambda: listing)
    assert OllamaEmbedder("bge-large").model_digest() == "abc123"
    assert OllamaEmbedder("bge-large:latest").model_digest() == "abc123"


def test_model_digest_missing_model_names_the_pull_command(monkeypatch):
    monkeypatch.setattr(ollama, "list", lambda: {"models": []})
    with pytest.raises(RuntimeError, match="ollama pull bge-large"):
        OllamaEmbedder("bge-large").model_digest()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/ai/test_ollama_embed.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'ai.ollama_embed'`)

- [ ] **Step 3: Write the embedder**

Create `ai/ollama_embed.py`:

```python
import numpy as np
import ollama


class OllamaEmbedder:
    """Embeds text with an Ollama-served embedding model (P1 spec, decision 6).

    Vectors are L2-normalised so cosine similarity is a plain dot product.
    """

    def __init__(self, model: str):
        self.model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        response = ollama.embed(model=self.model, input=texts)
        vectors = np.asarray(response["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    def model_digest(self) -> str:
        wanted = {self.model, f"{self.model}:latest"}
        for entry in ollama.list()["models"]:
            if entry["model"] in wanted:
                return entry["digest"]
        raise RuntimeError(f"Ollama model {self.model!r} not found; run `ollama pull {self.model}`")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/ai/test_ollama_embed.py -v`
Expected: 5 PASS

- [ ] **Step 5: Write the failing test for the probe's evaluation logic**

Create `batch/tests/test_probe_embedding_model.py`:

```python
import numpy as np

from batch.tools import probe_embedding_model as probe


class _FakeEmbedder:
    """Maps every text to a one-hot direction chosen by a lookup, so cosines are exact."""

    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        vectors = np.zeros((len(texts), 4), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, self.mapping.get(text, 3)] = 1.0
        return vectors


def test_evaluate_passes_when_pairs_are_closer_than_unrelated(monkeypatch):
    monkeypatch.setattr(probe, "SIMILAR_PAIRS", [("a", "b")])
    monkeypatch.setattr(probe, "UNRELATED", ["u"])
    rows = probe.evaluate(_FakeEmbedder({"a": 0, "b": 0, "u": 1}))
    assert rows == [("a", 1.0, 0.0, True)]


def test_evaluate_fails_when_unrelated_is_as_close(monkeypatch):
    monkeypatch.setattr(probe, "SIMILAR_PAIRS", [("a", "b")])
    monkeypatch.setattr(probe, "UNRELATED", ["u"])
    rows = probe.evaluate(_FakeEmbedder({"a": 0, "b": 0, "u": 0}))
    assert rows[0][3] is False
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest batch/tests/test_probe_embedding_model.py -v`
Expected: FAIL (`ImportError: cannot import name 'probe_embedding_model'`)

- [ ] **Step 7: Write the probe**

Create `batch/tools/probe_embedding_model.py`:

```python
"""One-off sanity probe for the Ollama embedding model (P1 spec, decision 6).

Not a CI test (needs a running Ollama with the model pulled):
    python -m batch.tools.probe_embedding_model --model bge-large

Prints the model digest and output dimension, then checks that each similar phrase pair is closer
than the closest unrelated phrase by MARGIN. Exit code 1 if any pair fails.
"""
import argparse
import sys

import numpy as np

from ai.ollama_embed import OllamaEmbedder

SIMILAR_PAIRS = [
    ("my boss moved the deadline to tomorrow", "sudden deadline pressure at work"),
    ("plans got cancelled at the last minute", "unexpected cancellation of an evening out"),
    ("I finally passed the exam", "relief after getting a good grade"),
    ("someone ate my lunch from the office fridge", "food stolen at work"),
]
UNRELATED = [
    "a cat sitting on a windowsill in the sun",
    "a recipe for tomato soup",
    "the football match ended in a draw",
    "the train timetable changed for summer",
]
MARGIN = 0.05


def evaluate(embedder) -> list[tuple[str, float, float, bool]]:
    firsts = embedder.embed([a for a, _ in SIMILAR_PAIRS])
    seconds = embedder.embed([b for _, b in SIMILAR_PAIRS])
    unrelated = embedder.embed(UNRELATED)
    rows = []
    for i, (phrase, _) in enumerate(SIMILAR_PAIRS):
        positive = float(firsts[i] @ seconds[i])
        negative = float(np.max(unrelated @ firsts[i]))
        rows.append((phrase, positive, negative, positive > negative + MARGIN))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="bge-large")
    args = parser.parse_args(argv)

    embedder = OllamaEmbedder(args.model)
    print(f"model={args.model} digest={embedder.model_digest()}")
    print(f"dimension={embedder.embed(['dimension probe']).shape[1]}")
    rows = evaluate(embedder)
    for phrase, positive, negative, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  pair={positive:.3f}  closest_unrelated={negative:.3f}  {phrase}")
    return 0 if all(ok for *_, ok in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `pytest batch/tests/test_probe_embedding_model.py -v`
Expected: 2 PASS

- [ ] **Step 9: Commit the code**

```bash
git add ai/ollama_embed.py batch/tools/probe_embedding_model.py tests/ai/test_ollama_embed.py batch/tests/test_probe_embedding_model.py
git commit -m "feat(ai): add Ollama embedding client and embedding-model probe"
```

- [ ] **Step 10: Pull the candidate model and run the probe (needs the user's OK: ~670 MB download)**

Run: `ollama pull bge-large` then `PYTHONIOENCODING=utf-8 python -m batch.tools.probe_embedding_model --model bge-large`
Expected: prints `digest=...`, `dimension=<N>` and four `PASS` lines, exit code 0.
If `bge-large` is not in the Ollama library or any pair prints `FAIL`: run `ollama pull mxbai-embed-large` and re-run with `--model mxbai-embed-large`. If both fail, stop and report to the user: do not pick a model that fails the probe.
Record the chosen model name, the digest and the dimension `<N>` (used in the next steps).

- [ ] **Step 11: Record the decision in config, constants and spec**

Append to `environments/settings.yaml` (use the chosen model name):

```yaml

taxonomy:
  embedding_model: bge-large   # chosen in P1 (spec 2026-10-02-p1-taxonomy-and-data-model-design.md, decision 6)
```

In `Storage/models.py`, directly after the `TEXT_EMBEDDING_DIM = 1024` line, add (replace `1024` with the measured `<N>` if different):

```python
# Output size of the Ollama-served model behind meme profile vectors (P1 spec, decision 6).
# Measured with batch/tools/probe_embedding_model.py. Independent of TEXT_EMBEDDING_DIM: the
# vectors come from a different runtime and must never share a table or index with it.
PROFILE_EMBEDDING_DIM = 1024
```

Append to the spec (`docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md`):

```markdown

## Addendum: embedding probe result

Recorded <date the probe was run, YYYY-MM-DD>. Model `<name>`, digest `<digest>`, dimension `<N>`, pairs passed `4/4`
(`python -m batch.tools.probe_embedding_model`). `PROFILE_EMBEDDING_DIM = <N>` in `Storage/models.py`,
`taxonomy.embedding_model` in `environments/settings.yaml`.
```

Fill in the bracketed values from the real probe output. Do not leave angle brackets in the committed text.

- [ ] **Step 12: Verify config loads and commit**

Run: `python -c "from config.settings import settings; print(settings.TAXONOMY.EMBEDDING_MODEL)"`
Expected: prints the chosen model name.
Run: `python -c "from Storage.models import PROFILE_EMBEDDING_DIM; print(PROFILE_EMBEDDING_DIM)"` (set `DATABASE_URL` to any placeholder first if the import guard complains)
Expected: prints `<N>`.

```bash
git add environments/settings.yaml Storage/models.py docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md
git commit -m "feat(taxonomy): record embedding model decision (settings, dimension constant, spec addendum)"
```

---

### Task 2: Taxonomy data model and loader

**Files:**
- Create: `taxonomy/__init__.py`, `taxonomy/model.py`, `taxonomy/loader.py`
- Create: `tests/taxonomy/__init__.py` (empty; create with `touch`), `tests/taxonomy/fixtures/mini/taxonomy.yaml`, `emotion.yaml`, `stance.yaml`, `tests/taxonomy/test_loader.py`

**Interfaces:**
- Produces:
  - `taxonomy.model`: constants `FREE_TEXT = "free_text"`, `VOCABULARY = "vocabulary"`, `LANGUAGES = ("en", "ru", "es")`; frozen dataclasses `AxisSpec(name, type, max_chars=None, max_values=None)`, `VocabValue(key, definition, labels)`, `Taxonomy(version, axes, values, content_hash, similarity_overrides={})` with methods `controlled_axes() -> tuple[str, ...]` and `has_key(axis, key) -> bool`.
  - `taxonomy.loader`: `TaxonomyError(ValueError)`, `DEFAULT_DIR: Path`, `load(directory: Path | None = None) -> Taxonomy` (the default directory is cached, explicit directories are never cached).

- [ ] **Step 1: Create the fixture taxonomy**

Run: `mkdir -p tests/taxonomy/fixtures/mini && touch tests/taxonomy/__init__.py`

`tests/taxonomy/fixtures/mini/taxonomy.yaml`:
```yaml
version: 7
axes:
  situation: {type: free_text, max_chars: 40}
  emotion:   {type: vocabulary, max_values: 2}
  stance:    {type: vocabulary, max_values: 1}
similarity_overrides:
  emotion:
    add: [[joy, anger, 0.5]]
    remove: []
```

`tests/taxonomy/fixtures/mini/emotion.yaml`:
```yaml
values:
  joy:
    definition: "Plain happiness."
    labels: {en: Joy, ru: Радость, es: Alegría}
  anger:
    definition: "Strong hostility."
    labels: {en: Anger, ru: Гнев, es: Enfado}
```

`tests/taxonomy/fixtures/mini/stance.yaml`:
```yaml
values:
  agree:
    definition: "Confirms what was said."
    labels: {en: Agree, ru: Согласиться, es: Estar de acuerdo}
  refuse:
    definition: "Declines a request."
    labels: {en: Refuse, ru: Отказать, es: Rechazar}
```

- [ ] **Step 2: Write the failing loader tests**

`tests/taxonomy/test_loader.py`:

```python
import shutil
from pathlib import Path

import pytest

from taxonomy.loader import TaxonomyError, load
from taxonomy.model import FREE_TEXT, VOCABULARY

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def _copy(tmp_path) -> Path:
    target = tmp_path / "tax"
    shutil.copytree(FIXTURE, target)
    return target


def test_load_reads_version_axes_and_values():
    tax = load(FIXTURE)
    assert tax.version == 7
    assert tax.axes["situation"].type == FREE_TEXT
    assert tax.axes["situation"].max_chars == 40
    assert tax.axes["emotion"].type == VOCABULARY
    assert tax.axes["emotion"].max_values == 2
    assert list(tax.values["emotion"]) == ["joy", "anger"]  # file order preserved
    assert tax.values["emotion"]["joy"].definition == "Plain happiness."
    assert tax.values["emotion"]["joy"].labels["ru"] == "Радость"


def test_free_text_axis_has_no_values_and_controlled_axes_listed():
    tax = load(FIXTURE)
    assert "situation" not in tax.values
    assert tax.controlled_axes() == ("emotion", "stance")


def test_has_key():
    tax = load(FIXTURE)
    assert tax.has_key("emotion", "joy")
    assert not tax.has_key("emotion", "agree")
    assert not tax.has_key("nope", "joy")


def test_similarity_overrides_are_parsed():
    tax = load(FIXTURE)
    assert tax.similarity_overrides == {"emotion": {"add": [["joy", "anger", 0.5]], "remove": []}}


def test_content_hash_is_stable_across_crlf(tmp_path):
    original = load(FIXTURE).content_hash
    target = _copy(tmp_path)
    for path in target.glob("*.yaml"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert load(target).content_hash == original


def test_content_hash_changes_when_a_definition_changes(tmp_path):
    target = _copy(tmp_path)
    before = load(target).content_hash
    emotion = target / "emotion.yaml"
    emotion.write_text(emotion.read_text(encoding="utf-8").replace("Plain happiness.", "Pure delight."),
                       encoding="utf-8")
    assert load(target).content_hash != before


def test_duplicate_key_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "emotion.yaml").write_text(
        "values:\n  joy:\n    definition: a\n    labels: {en: a, ru: a, es: a}\n"
        "  joy:\n    definition: b\n    labels: {en: b, ru: b, es: b}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="duplicate key"):
        load(target)


def test_missing_axis_file_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "stance.yaml").unlink()
    with pytest.raises(TaxonomyError, match="missing file: stance.yaml"):
        load(target)


def test_unknown_axis_type_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: 1\naxes:\n  emotion: {type: weird, max_values: 1}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="unknown type"):
        load(target)


def test_vocabulary_axis_without_max_values_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: 1\naxes:\n  emotion: {type: vocabulary}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="max_values"):
        load(target)


def test_non_integer_version_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: one\naxes:\n  emotion: {type: vocabulary, max_values: 1}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="version"):
        load(target)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_loader.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'taxonomy'`)

- [ ] **Step 4: Write the model**

`taxonomy/model.py`:

```python
from dataclasses import dataclass, field
from typing import Mapping

FREE_TEXT = "free_text"
VOCABULARY = "vocabulary"
LANGUAGES = ("en", "ru", "es")


@dataclass(frozen=True)
class AxisSpec:
    name: str
    type: str
    max_chars: int | None = None
    max_values: int | None = None


@dataclass(frozen=True)
class VocabValue:
    key: str
    definition: str
    labels: Mapping[str, str]


@dataclass(frozen=True)
class Taxonomy:
    version: int
    axes: Mapping[str, AxisSpec]
    values: Mapping[str, Mapping[str, VocabValue]]
    content_hash: str
    similarity_overrides: Mapping[str, Mapping] = field(default_factory=dict)

    def controlled_axes(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.axes.items() if spec.type == VOCABULARY)

    def has_key(self, axis: str, key: str) -> bool:
        return key in self.values.get(axis, {})
```

- [ ] **Step 5: Write the loader**

`taxonomy/loader.py`:

```python
import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from taxonomy.model import FREE_TEXT, VOCABULARY, AxisSpec, Taxonomy, VocabValue

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "batch" / "data" / "taxonomy"


class TaxonomyError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (PyYAML silently keeps the last one)."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise TaxonomyError(f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _normalised_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise TaxonomyError(f"missing file: {path.name}")
    return path.read_bytes().replace(b"\r\n", b"\n")


def _read_yaml(path: Path) -> dict:
    data = yaml.load(_normalised_bytes(path).decode("utf-8"), Loader=_UniqueKeyLoader) or {}
    if not isinstance(data, dict):
        raise TaxonomyError(f"{path.name}: top level must be a mapping")
    return data


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_axes(raw: dict) -> dict[str, AxisSpec]:
    if not isinstance(raw, dict) or not raw:
        raise TaxonomyError("taxonomy.yaml: 'axes' must be a non-empty mapping")
    axes = {}
    for name, spec in raw.items():
        axis_type = spec.get("type") if isinstance(spec, dict) else None
        if axis_type == FREE_TEXT:
            if not _positive_int(spec.get("max_chars")):
                raise TaxonomyError(f"axis {name!r}: free_text needs a positive integer max_chars")
            axes[name] = AxisSpec(name, FREE_TEXT, max_chars=spec["max_chars"])
        elif axis_type == VOCABULARY:
            if not _positive_int(spec.get("max_values")):
                raise TaxonomyError(f"axis {name!r}: vocabulary needs a positive integer max_values")
            axes[name] = AxisSpec(name, VOCABULARY, max_values=spec["max_values"])
        else:
            raise TaxonomyError(f"axis {name!r}: unknown type {axis_type!r}")
    return axes


def _parse_values(axis: str, raw: dict) -> dict[str, VocabValue]:
    entries = raw.get("values")
    if not isinstance(entries, dict) or not entries:
        raise TaxonomyError(f"{axis}.yaml: 'values' must be a non-empty mapping")
    values = {}
    for key, entry in entries.items():
        if not isinstance(key, str):
            raise TaxonomyError(f"{axis}.yaml: key {key!r} must be a string")
        if not isinstance(entry, dict) or not isinstance(entry.get("definition"), str):
            raise TaxonomyError(f"{axis}.{key}: needs a string 'definition'")
        labels = entry.get("labels")
        if not isinstance(labels, dict) or not all(isinstance(v, str) for v in labels.values()):
            raise TaxonomyError(f"{axis}.{key}: 'labels' must map language to string")
        values[key] = VocabValue(key, entry["definition"], dict(labels))
    return values


def _content_hash(directory: Path, vocabulary_axes: list[str]) -> str:
    digest = hashlib.sha256()
    for name in ["taxonomy.yaml", *sorted(f"{axis}.yaml" for axis in vocabulary_axes)]:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(_normalised_bytes(directory / name) + b"\0")
    return digest.hexdigest()


def _parse(directory: Path) -> Taxonomy:
    root = _read_yaml(directory / "taxonomy.yaml")
    version = root.get("version")
    if not _positive_int(version):
        raise TaxonomyError("taxonomy.yaml: 'version' must be a positive integer")
    axes = _parse_axes(root.get("axes"))
    vocabulary_axes = [name for name, spec in axes.items() if spec.type == VOCABULARY]
    values = {axis: _parse_values(axis, _read_yaml(directory / f"{axis}.yaml")) for axis in vocabulary_axes}
    overrides = root.get("similarity_overrides") or {}
    if not isinstance(overrides, dict):
        raise TaxonomyError("taxonomy.yaml: 'similarity_overrides' must be a mapping")
    return Taxonomy(
        version=version,
        axes=axes,
        values=values,
        content_hash=_content_hash(directory, vocabulary_axes),
        similarity_overrides=overrides,
    )


@lru_cache(maxsize=1)
def _load_default() -> Taxonomy:
    return _parse(DEFAULT_DIR)


def load(directory: Path | None = None) -> Taxonomy:
    """Load the shipped taxonomy (cached) or one from `directory` (never cached)."""
    if directory is None:
        return _load_default()
    return _parse(Path(directory))
```

`taxonomy/__init__.py`:

```python
from taxonomy.loader import TaxonomyError, load
from taxonomy.model import AxisSpec, Taxonomy, VocabValue

__all__ = ["AxisSpec", "Taxonomy", "TaxonomyError", "VocabValue", "load"]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/taxonomy/test_loader.py -v`
Expected: 11 PASS

- [ ] **Step 7: Commit**

```bash
git add taxonomy tests/taxonomy
git commit -m "feat(taxonomy): add taxonomy model and YAML loader with content hash"
```

---

### Task 3: Profile validation and prompt rendering

**Files:**
- Create: `taxonomy/validate.py`, `taxonomy/prompt.py`
- Modify: `taxonomy/__init__.py` (add exports)
- Create: `tests/taxonomy/test_validate.py`, `tests/taxonomy/test_prompt.py`

**Interfaces:**
- Consumes: `taxonomy.loader.load`, `taxonomy.model.{Taxonomy, FREE_TEXT, VOCABULARY}`.
- Produces: `taxonomy.validate.validate_profile(profile: Mapping, tax: Taxonomy | None = None) -> ValidationResult` where `ValidationResult(profile: dict, errors: list[str])` has a boolean property `ok`; `taxonomy.prompt.vocabulary_prompt(axis: str, tax: Taxonomy | None = None) -> str`. Profile shape (camelCase, matches the shared schema): free-text axis `situation` plus `situationConfidence`; controlled axes as lists of `{"key": str, "confidence": float, "source": "model" | "human"}`. Missing `confidence` defaults to `1.0`, missing `source` to `"model"`. Normalised output sorts each axis by confidence descending then key, and merges duplicate keys keeping the highest confidence.

- [ ] **Step 1: Write the failing tests**

`tests/taxonomy/test_validate.py`:

```python
from pathlib import Path

import pytest

from taxonomy.loader import load
from taxonomy.validate import validate_profile

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture(scope="module")
def tax():
    return load(FIXTURE)


def test_valid_profile_is_normalised(tax):
    result = validate_profile({
        "situation": "  plans cancelled  ",
        "situationConfidence": 0.8,
        "emotion": [{"key": "anger", "confidence": 0.4}, {"key": "joy", "confidence": 0.9, "source": "human"}],
        "stance": [{"key": "agree"}],
    }, tax)
    assert result.ok, result.errors
    assert result.profile["situation"] == "plans cancelled"
    assert result.profile["situationConfidence"] == 0.8
    assert result.profile["emotion"] == [
        {"key": "joy", "confidence": 0.9, "source": "human"},
        {"key": "anger", "confidence": 0.4, "source": "model"},
    ]
    assert result.profile["stance"] == [{"key": "agree", "confidence": 1.0, "source": "model"}]


def test_empty_profile_is_valid_and_has_every_axis(tax):
    result = validate_profile({}, tax)
    assert result.ok
    assert result.profile == {"situation": None, "situationConfidence": None, "emotion": [], "stance": []}


def test_blank_situation_becomes_none(tax):
    assert validate_profile({"situation": "   "}, tax).profile["situation"] is None


def test_unknown_key_is_an_error(tax):
    result = validate_profile({"emotion": [{"key": "euphoria"}]}, tax)
    assert not result.ok
    assert result.errors == ["emotion: unknown key 'euphoria'"]
    assert result.profile["emotion"] == []


def test_key_from_another_axis_is_unknown(tax):
    assert not validate_profile({"emotion": [{"key": "agree"}]}, tax).ok


def test_too_many_values_is_an_error(tax):
    result = validate_profile({"stance": [{"key": "agree"}, {"key": "refuse"}]}, tax)
    assert result.errors == ["stance: 2 values exceed the limit of 1"]


def test_duplicate_keys_merge_keeping_the_highest_confidence(tax):
    result = validate_profile({"emotion": [{"key": "joy", "confidence": 0.2}, {"key": "joy", "confidence": 0.7}]}, tax)
    assert result.ok
    assert result.profile["emotion"] == [{"key": "joy", "confidence": 0.7, "source": "model"}]


@pytest.mark.parametrize("confidence", [-0.1, 1.5, "high", True, None])
def test_bad_confidence_is_an_error(tax, confidence):
    result = validate_profile({"emotion": [{"key": "joy", "confidence": confidence}]}, tax)
    assert not result.ok


def test_bad_source_is_an_error(tax):
    assert not validate_profile({"emotion": [{"key": "joy", "source": "robot"}]}, tax).ok


def test_overlong_situation_is_an_error(tax):
    result = validate_profile({"situation": "x" * 41}, tax)
    assert result.errors == ["situation: longer than 40 characters"]


def test_non_string_situation_is_an_error(tax):
    assert not validate_profile({"situation": 5}, tax).ok


def test_situation_confidence_out_of_range_is_an_error(tax):
    assert not validate_profile({"situationConfidence": 2}, tax).ok


def test_non_list_axis_is_an_error(tax):
    assert not validate_profile({"emotion": "joy"}, tax).ok


def test_non_object_value_is_an_error(tax):
    assert not validate_profile({"emotion": ["joy"]}, tax).ok
```

`tests/taxonomy/test_prompt.py`:

```python
from pathlib import Path

import pytest

from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_vocabulary_prompt_lists_key_and_definition_in_file_order():
    assert vocabulary_prompt("emotion", load(FIXTURE)) == "- joy: Plain happiness.\n- anger: Strong hostility."


def test_vocabulary_prompt_rejects_free_text_axis():
    with pytest.raises(ValueError, match="situation"):
        vocabulary_prompt("situation", load(FIXTURE))


def test_vocabulary_prompt_rejects_unknown_axis():
    with pytest.raises(ValueError, match="nope"):
        vocabulary_prompt("nope", load(FIXTURE))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_validate.py tests/taxonomy/test_prompt.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'taxonomy.validate'`)

- [ ] **Step 3: Write the validator**

`taxonomy/validate.py`:

```python
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Mapping

from taxonomy.loader import load
from taxonomy.model import FREE_TEXT, AxisSpec, Taxonomy

SOURCES = ("model", "human")


@dataclass
class ValidationResult:
    profile: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_unit_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def _validate_free_text(name: str, spec: AxisSpec, profile: Mapping, out: dict, errors: list[str]) -> None:
    text = profile.get(name)
    if text is not None and not isinstance(text, str):
        errors.append(f"{name}: must be a string or null")
        text = None
    text = text.strip() if text else None
    if text and len(text) > spec.max_chars:
        errors.append(f"{name}: longer than {spec.max_chars} characters")
    out[name] = text or None

    confidence = profile.get(f"{name}Confidence")
    if confidence is not None and not _is_unit_number(confidence):
        errors.append(f"{name}Confidence: must be a number in 0..1")
        confidence = None
    out[f"{name}Confidence"] = float(confidence) if confidence is not None else None


def _validate_values(axis: str, spec: AxisSpec, items, tax: Taxonomy, errors: list[str]) -> list[dict]:
    if not isinstance(items, list):
        errors.append(f"{axis}: must be a list")
        return []
    best: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, Mapping):
            errors.append(f"{axis}: each value must be an object")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not tax.has_key(axis, key):
            errors.append(f"{axis}: unknown key {key!r}")
            continue
        confidence = item.get("confidence", 1.0)
        if not _is_unit_number(confidence):
            errors.append(f"{axis}: confidence for {key!r} must be a number in 0..1")
            continue
        source = item.get("source", "model")
        if source not in SOURCES:
            errors.append(f"{axis}: source for {key!r} must be one of {SOURCES}")
            continue
        entry = {"key": key, "confidence": float(confidence), "source": source}
        if key not in best or entry["confidence"] > best[key]["confidence"]:
            best[key] = entry
    values = sorted(best.values(), key=lambda v: (-v["confidence"], v["key"]))
    if spec.max_values is not None and len(values) > spec.max_values:
        errors.append(f"{axis}: {len(values)} values exceed the limit of {spec.max_values}")
    return values


def validate_profile(profile: Mapping, tax: Taxonomy | None = None) -> ValidationResult:
    """Validate and normalise a meme or query profile against the taxonomy.

    Only taxonomy-related fields are checked; other keys are ignored (the JSON schema covers them).
    """
    tax = tax or load()
    errors: list[str] = []
    out: dict[str, Any] = {}
    for name, spec in tax.axes.items():
        if spec.type == FREE_TEXT:
            _validate_free_text(name, spec, profile, out, errors)
        else:
            out[name] = _validate_values(name, spec, profile.get(name) or [], tax, errors)
    return ValidationResult(out, errors)
```

- [ ] **Step 4: Write the prompt renderer**

`taxonomy/prompt.py`:

```python
from taxonomy.loader import load
from taxonomy.model import VOCABULARY, Taxonomy


def vocabulary_prompt(axis: str, tax: Taxonomy | None = None) -> str:
    """Render an axis's choice list (key plus definition) for LLM prompts, in file order."""
    tax = tax or load()
    spec = tax.axes.get(axis)
    if spec is None or spec.type != VOCABULARY:
        raise ValueError(f"{axis!r} is not a controlled-vocabulary axis")
    return "\n".join(f"- {key}: {value.definition}" for key, value in tax.values[axis].items())
```

Update `taxonomy/__init__.py` to:

```python
from taxonomy.loader import TaxonomyError, load
from taxonomy.model import AxisSpec, Taxonomy, VocabValue
from taxonomy.prompt import vocabulary_prompt
from taxonomy.validate import ValidationResult, validate_profile

__all__ = [
    "AxisSpec", "Taxonomy", "TaxonomyError", "ValidationResult", "VocabValue",
    "load", "validate_profile", "vocabulary_prompt",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/taxonomy -v`
Expected: all PASS (Task 2 tests included). The parametrised `None` case is an explicit `"confidence": null`, which must be rejected (only an absent confidence defaults to 1.0).

- [ ] **Step 6: Commit**

```bash
git add taxonomy tests/taxonomy
git commit -m "feat(taxonomy): add profile validation and vocabulary prompt rendering"
```

---

### Task 4: Shipped vocabularies and lint

**Files:**
- Create: `taxonomy/lint.py`
- Create: `batch/data/taxonomy/taxonomy.yaml`, `emotion.yaml`, `stance.yaml`, `tone.yaml`, `topic.yaml`
- Create: `tests/taxonomy/test_lint.py`, `tests/taxonomy/test_shipped_taxonomy.py`

**Interfaces:**
- Consumes: `taxonomy.model.*`, `taxonomy.loader.load`.
- Produces: `taxonomy.lint.lint(tax: Taxonomy, *, check_sizes: bool = True) -> list[str]` (empty list means clean), constants `SIZE_RANGES`, `REQUIRED_AXES`, `MAX_DEFINITION_CHARS = 200`.

- [ ] **Step 1: Write the failing lint tests**

`tests/taxonomy/test_lint.py`:

```python
from taxonomy.lint import lint
from taxonomy.model import FREE_TEXT, VOCABULARY, AxisSpec, Taxonomy, VocabValue


def _value(key="joy", definition="Plain happiness.", labels=None):
    return VocabValue(key, definition, labels or {"en": "Joy", "ru": "Радость", "es": "Alegría"})


def _tax(emotion_values, axes=None):
    axes = axes or {
        "situation": AxisSpec("situation", FREE_TEXT, max_chars=160),
        "emotion": AxisSpec("emotion", VOCABULARY, max_values=3),
        "stance": AxisSpec("stance", VOCABULARY, max_values=2),
        "topic": AxisSpec("topic", VOCABULARY, max_values=3),
        "tone": AxisSpec("tone", VOCABULARY, max_values=2),
    }
    values = {"emotion": emotion_values, "stance": {"agree": _value("agree")},
              "topic": {"work": _value("work")}, "tone": {"wholesome": _value("wholesome")}}
    return Taxonomy(1, axes, values, "hash")


def test_clean_taxonomy_has_no_problems_when_sizes_are_not_checked():
    assert lint(_tax({"joy": _value()}), check_sizes=False) == []


def test_bad_key_formats_are_reported():
    for bad in ("Joy", "joy-ful", "1joy", "joy joy", "j" * 49):
        problems = lint(_tax({bad: _value(bad)}), check_sizes=False)
        assert any("key" in p and "emotion" in p for p in problems), bad


def test_empty_and_overlong_definitions_are_reported():
    assert any("definition" in p for p in lint(_tax({"joy": _value(definition="  ")}), check_sizes=False))
    assert any("200" in p for p in lint(_tax({"joy": _value(definition="x" * 201)}), check_sizes=False))


def test_missing_label_is_reported():
    problems = lint(_tax({"joy": _value(labels={"en": "Joy", "ru": "Радость"})}), check_sizes=False)
    assert "emotion.joy: missing es label" in problems


def test_size_ranges_are_enforced_when_requested():
    problems = lint(_tax({"joy": _value()}))
    assert any(p.startswith("emotion: 1 values outside 20-30") for p in problems)


def test_required_axes_must_exist():
    axes = {"emotion": AxisSpec("emotion", VOCABULARY, max_values=3)}
    tax = Taxonomy(1, axes, {"emotion": {"joy": _value()}}, "hash")
    assert "missing required axis 'situation'" in lint(tax, check_sizes=False)


def test_situation_must_be_free_text():
    axes = {
        "situation": AxisSpec("situation", VOCABULARY, max_values=1),
        "emotion": AxisSpec("emotion", VOCABULARY, max_values=3),
        "stance": AxisSpec("stance", VOCABULARY, max_values=2),
        "topic": AxisSpec("topic", VOCABULARY, max_values=3),
        "tone": AxisSpec("tone", VOCABULARY, max_values=2),
    }
    tax = _tax({"joy": _value()}, axes=axes)
    assert "situation: must be a free_text axis" in lint(tax, check_sizes=False)


def test_axis_name_length_is_limited():
    axes = {"a_very_long_axis_name": AxisSpec("a_very_long_axis_name", FREE_TEXT, max_chars=10)}
    tax = Taxonomy(1, axes, {}, "hash")
    assert any("longer than 16" in p for p in lint(tax, check_sizes=False))
```

`tests/taxonomy/test_shipped_taxonomy.py`:

```python
from taxonomy.lint import lint
from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt


def test_shipped_taxonomy_loads_and_lints_clean():
    tax = load()
    assert tax.version >= 1
    assert lint(tax) == []


def test_shipped_axes_are_the_confirmed_five():
    assert list(load().axes) == ["situation", "emotion", "stance", "topic", "tone"]


def test_every_controlled_axis_renders_a_prompt():
    tax = load()
    for axis in tax.controlled_axes():
        prompt = vocabulary_prompt(axis, tax)
        assert prompt.count("\n") + 1 == len(tax.values[axis])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_lint.py tests/taxonomy/test_shipped_taxonomy.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'taxonomy.lint'`)

- [ ] **Step 3: Write the lint**

`taxonomy/lint.py`:

```python
import re

from taxonomy.model import FREE_TEXT, LANGUAGES, VOCABULARY, Taxonomy

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_DEFINITION_CHARS = 200
MAX_AXIS_NAME_CHARS = 16
REQUIRED_AXES = ("situation", "emotion", "stance", "topic", "tone")
SIZE_RANGES = {"emotion": (20, 30), "stance": (10, 18), "tone": (8, 12), "topic": (12, 20)}


def lint(tax: Taxonomy, *, check_sizes: bool = True) -> list[str]:
    """Return human-readable problems with the vocabulary content; an empty list means clean."""
    problems: list[str] = []
    for axis in REQUIRED_AXES:
        if axis not in tax.axes:
            problems.append(f"missing required axis {axis!r}")
    if "situation" in tax.axes and tax.axes["situation"].type != FREE_TEXT:
        problems.append("situation: must be a free_text axis")

    for name, spec in tax.axes.items():
        if len(name) > MAX_AXIS_NAME_CHARS:
            problems.append(f"axis {name!r}: name longer than {MAX_AXIS_NAME_CHARS} characters")
        if spec.type != VOCABULARY:
            continue
        values = tax.values.get(name, {})
        if check_sizes and name in SIZE_RANGES:
            low, high = SIZE_RANGES[name]
            if not low <= len(values) <= high:
                problems.append(f"{name}: {len(values)} values outside {low}-{high}")
        for key, value in values.items():
            if not KEY_RE.match(key):
                problems.append(f"{name}: key {key!r} must be lowercase snake_case, at most 48 characters")
            if not value.definition.strip():
                problems.append(f"{name}.{key}: empty definition")
            elif len(value.definition) > MAX_DEFINITION_CHARS:
                problems.append(f"{name}.{key}: definition longer than {MAX_DEFINITION_CHARS} characters")
            for language in LANGUAGES:
                if not value.labels.get(language, "").strip():
                    problems.append(f"{name}.{key}: missing {language} label")
    return problems
```

- [ ] **Step 4: Write `taxonomy.yaml`**

`batch/data/taxonomy/taxonomy.yaml`:

```yaml
# Meme-from-text taxonomy (epic docs/superpowers/specs/2026-10-02-meme-from-text/).
# Bump `version` on ANY change to an axis, a key, a definition or a label, then run
# `python -m taxonomy.lock` to refresh taxonomy.lock. Keys are immutable once released.
version: 1
axes:
  situation: {type: free_text, max_chars: 160}
  emotion:   {type: vocabulary, max_values: 3}
  stance:    {type: vocabulary, max_values: 2}
  topic:     {type: vocabulary, max_values: 3}
  tone:      {type: vocabulary, max_values: 2}
```

- [ ] **Step 5: Write `emotion.yaml` (26 values)**

`batch/data/taxonomy/emotion.yaml`:

```yaml
values:
  joy:
    definition: "Plain happiness or delight."
    labels: {en: Joy, ru: Радость, es: Alegría}
  amusement:
    definition: "Finds something funny; laughing at it."
    labels: {en: Amusement, ru: Веселье, es: Diversión}
  pride:
    definition: "Satisfaction in an achievement or in oneself."
    labels: {en: Pride, ru: Гордость, es: Orgullo}
  relief:
    definition: "Tension released after a worry or ordeal ends."
    labels: {en: Relief, ru: Облегчение, es: Alivio}
  gratitude:
    definition: "Feeling thankful toward someone or something."
    labels: {en: Gratitude, ru: Благодарность, es: Gratitud}
  love:
    definition: "Affection, adoration or devotion."
    labels: {en: Love, ru: Любовь, es: Amor}
  excitement:
    definition: "Eager, energised anticipation or enthusiasm."
    labels: {en: Excitement, ru: Восторг, es: Emoción}
  smugness:
    definition: "Self-satisfied pleasure at being right or ahead."
    labels: {en: Smugness, ru: Самодовольство, es: Suficiencia}
  hope:
    definition: "Optimistic expectation that things will turn out well."
    labels: {en: Hope, ru: Надежда, es: Esperanza}
  anger:
    definition: "Strong hostility or rage at something."
    labels: {en: Anger, ru: Гнев, es: Enfado}
  frustration:
    definition: "Feeling stuck because effort keeps failing or being blocked."
    labels: {en: Frustration, ru: Фрустрация, es: Frustración}
  annoyance:
    definition: "Mild irritation at something petty or repeated."
    labels: {en: Annoyance, ru: Досада, es: Fastidio}
  disgust:
    definition: "Revulsion at something repellent or distasteful."
    labels: {en: Disgust, ru: Отвращение, es: Asco}
  contempt:
    definition: "Looking down on someone or something as unworthy."
    labels: {en: Contempt, ru: Презрение, es: Desprecio}
  sadness:
    definition: "Sorrow, grief or low mood."
    labels: {en: Sadness, ru: Грусть, es: Tristeza}
  disappointment:
    definition: "Let down because expectations were not met."
    labels: {en: Disappointment, ru: Разочарование, es: Decepción}
  resignation:
    definition: "Accepting an unwelcome situation without fighting it."
    labels: {en: Resignation, ru: Смирение, es: Resignación}
  exhaustion:
    definition: "Drained of energy, physically or mentally tired."
    labels: {en: Exhaustion, ru: Усталость, es: Agotamiento}
  anxiety:
    definition: "Nervous worry about what might go wrong."
    labels: {en: Anxiety, ru: Тревога, es: Ansiedad}
  fear:
    definition: "Alarm at a threat or danger."
    labels: {en: Fear, ru: Страх, es: Miedo}
  embarrassment:
    definition: "Self-conscious discomfort after a slip or exposure."
    labels: {en: Embarrassment, ru: Смущение, es: Vergüenza}
  confusion:
    definition: "Not understanding what is going on."
    labels: {en: Confusion, ru: Недоумение, es: Confusión}
  shock:
    definition: "Stunned disbelief at something unexpected."
    labels: {en: Shock, ru: Шок, es: Conmoción}
  boredom:
    definition: "Lack of interest; time dragging."
    labels: {en: Boredom, ru: Скука, es: Aburrimiento}
  jealousy:
    definition: "Resentful envy of what someone else has."
    labels: {en: Jealousy, ru: Зависть, es: Celos}
  indifference:
    definition: "Not caring either way."
    labels: {en: Indifference, ru: Безразличие, es: Indiferencia}
```

- [ ] **Step 6: Write `stance.yaml` (15 values)**

`batch/data/taxonomy/stance.yaml`:

```yaml
values:
  agree:
    definition: "Confirms or endorses what was said."
    labels: {en: Agree, ru: Согласиться, es: Estar de acuerdo}
  disagree:
    definition: "Pushes back or says the other person is wrong."
    labels: {en: Disagree, ru: Не согласиться, es: Discrepar}
  refuse:
    definition: "Declines a request, invitation or demand."
    labels: {en: Refuse, ru: Отказать, es: Rechazar}
  mock:
    definition: "Ridicules or teases the situation or the person in it."
    labels: {en: Mock, ru: Подколоть, es: Burlarse}
  console:
    definition: "Comforts someone who is upset."
    labels: {en: Console, ru: Утешить, es: Consolar}
  celebrate:
    definition: "Marks a win or a good moment with joy."
    labels: {en: Celebrate, ru: Отпраздновать, es: Celebrar}
  congratulate:
    definition: "Praises someone for an achievement."
    labels: {en: Congratulate, ru: Поздравить, es: Felicitar}
  deflect:
    definition: "Dodges the topic or changes the subject."
    labels: {en: Deflect, ru: Уйти от темы, es: Esquivar}
  warn:
    definition: "Cautions that trouble is coming."
    labels: {en: Warn, ru: Предупредить, es: Advertir}
  complain:
    definition: "Voices dissatisfaction or grumbles."
    labels: {en: Complain, ru: Пожаловаться, es: Quejarse}
  apologize:
    definition: "Says sorry or admits fault."
    labels: {en: Apologize, ru: Извиниться, es: Disculparse}
  thank:
    definition: "Expresses thanks."
    labels: {en: Thank, ru: Поблагодарить, es: Agradecer}
  brag:
    definition: "Shows off or boasts."
    labels: {en: Brag, ru: Похвастаться, es: Presumir}
  relate:
    definition: "Says 'same here' and shares the experience."
    labels: {en: Relate, ru: Мне это знакомо, es: Identificarse}
  demand:
    definition: "Insists or presses someone to act."
    labels: {en: Demand, ru: Потребовать, es: Exigir}
```

- [ ] **Step 7: Write `tone.yaml` (10 values) and `topic.yaml` (16 values)**

`batch/data/taxonomy/tone.yaml`:

```yaml
values:
  wholesome:
    definition: "Warm, kind and sincere."
    labels: {en: Wholesome, ru: Тёплый, es: Entrañable}
  sarcastic:
    definition: "Says the opposite of what is meant, mockingly."
    labels: {en: Sarcastic, ru: Саркастичный, es: Sarcástico}
  absurd:
    definition: "Deliberately illogical or surreal."
    labels: {en: Absurd, ru: Абсурдный, es: Absurdo}
  deadpan:
    definition: "Flat delivery with no visible emotion."
    labels: {en: Deadpan, ru: Невозмутимый, es: Inexpresivo}
  dramatic:
    definition: "Exaggerated, theatrical intensity."
    labels: {en: Dramatic, ru: Драматичный, es: Dramático}
  cynical:
    definition: "Distrustful, assumes the worst motives."
    labels: {en: Cynical, ru: Циничный, es: Cínico}
  self_deprecating:
    definition: "Makes fun of oneself."
    labels: {en: Self-deprecating, ru: Самоирония, es: Autocrítico}
  dark:
    definition: "Grim or morbid humour about death, loss or misfortune."
    labels: {en: Dark, ru: Чёрный юмор, es: Humor negro}
  crude:
    definition: "Vulgar, profane or toilet humour."
    labels: {en: Crude, ru: Грубый, es: Grosero}
  explicit:
    definition: "Sexual or graphic content."
    labels: {en: Explicit, ru: Откровенный, es: Explícito}
```

`batch/data/taxonomy/topic.yaml`:

```yaml
values:
  work:
    definition: "Jobs, bosses, colleagues, meetings and deadlines."
    labels: {en: Work, ru: Работа, es: Trabajo}
  study:
    definition: "School, university, exams and homework."
    labels: {en: Study, ru: Учёба, es: Estudios}
  family:
    definition: "Parents, children, relatives and home life."
    labels: {en: Family, ru: Семья, es: Familia}
  romance:
    definition: "Dating, love, relationships and breakups."
    labels: {en: Romance, ru: Романтика, es: Romance}
  friends:
    definition: "Friendships and social plans."
    labels: {en: Friends, ru: Друзья, es: Amigos}
  money:
    definition: "Income, prices, debt and spending."
    labels: {en: Money, ru: Деньги, es: Dinero}
  health:
    definition: "Illness, doctors, fitness and mental health."
    labels: {en: Health, ru: Здоровье, es: Salud}
  technology:
    definition: "Computers, phones, software and the internet."
    labels: {en: Technology, ru: Технологии, es: Tecnología}
  gaming:
    definition: "Video games and tabletop games."
    labels: {en: Gaming, ru: Игры, es: Videojuegos}
  food:
    definition: "Eating, cooking, drinks and diets."
    labels: {en: Food, ru: Еда, es: Comida}
  sleep:
    definition: "Sleep, tiredness and early mornings."
    labels: {en: Sleep, ru: Сон, es: Sueño}
  politics:
    definition: "Governments, elections and public affairs."
    labels: {en: Politics, ru: Политика, es: Política}
  animals:
    definition: "Pets and wildlife as the subject."
    labels: {en: Animals, ru: Животные, es: Animales}
  travel:
    definition: "Trips, transport and commuting."
    labels: {en: Travel, ru: Путешествия, es: Viajes}
  holidays:
    definition: "Holidays, birthdays and celebrations."
    labels: {en: Holidays, ru: Праздники, es: Fiestas}
  weather:
    definition: "Heat, cold, rain and seasons."
    labels: {en: Weather, ru: Погода, es: Clima}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/taxonomy -v`
Expected: all PASS. If a shipped-taxonomy assertion fails, fix the YAML (not the lint) unless the lint rule contradicts the spec.

- [ ] **Step 9: Commit**

```bash
git add taxonomy/lint.py batch/data/taxonomy tests/taxonomy
git commit -m "feat(taxonomy): add draft vocabularies (emotion, stance, tone, topic) and lint"
```

---

### Task 5: Corpus coverage check and one vocabulary adjustment

This task has a user review gate. The controller session runs the live-data steps itself (no subagent gets any database URL).

**Files:**
- Create: `taxonomy/coverage.py`
- Create: `tests/taxonomy/test_coverage.py`
- Create: `batch/tools/probe_vocabulary_coverage.py` (throwaway probe, not CI)
- Create: `docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md`
- Modify: `batch/data/taxonomy/*.yaml` (once, from the review)

**Interfaces:**
- Consumes: `taxonomy.loader.load`, `taxonomy.prompt.vocabulary_prompt`, `ai` / `ollama` chat, `Storage.models.{Image, OCRText, ImageDescription}`.
- Produces: `taxonomy.coverage.assign_from_response(raw: str, tax) -> dict | None` returning `{"assigned": {axis: [key]}, "suggested": {axis: [str]}}`; `summarize(results: list[dict], tax) -> dict`; `format_report(summary: dict) -> str`; `python -m taxonomy.coverage <results.jsonl>` prints the markdown report. Constant `OVERUSE_SHARE = 0.4`.

- [ ] **Step 1: Write the failing tests**

`tests/taxonomy/test_coverage.py`:

```python
import json
from pathlib import Path

from taxonomy.coverage import assign_from_response, format_report, summarize
from taxonomy.loader import load

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
TAX = load(FIXTURE)


def test_assign_splits_valid_keys_from_unknown_ones():
    raw = json.dumps({"emotion": ["joy", "euphoria"], "stance": ["agree"],
                      "suggested": {"emotion": ["bliss"], "stance": []}})
    result = assign_from_response(raw, TAX)
    assert result["assigned"] == {"emotion": ["joy"], "stance": ["agree"]}
    assert result["suggested"] == {"emotion": ["euphoria", "bliss"], "stance": []}


def test_assign_accepts_a_bare_string_and_deduplicates():
    result = assign_from_response(json.dumps({"emotion": "joy", "stance": ["agree", "agree"]}), TAX)
    assert result["assigned"] == {"emotion": ["joy"], "stance": ["agree"]}


def test_assign_returns_none_for_invalid_json_or_non_object():
    assert assign_from_response("not json", TAX) is None
    assert assign_from_response("[1, 2]", TAX) is None
    assert assign_from_response(None, TAX) is None


def _result(emotion=(), stance=(), suggested_emotion=()):
    return {"assigned": {"emotion": list(emotion), "stance": list(stance)},
            "suggested": {"emotion": list(suggested_emotion), "stance": []}}


def test_summarize_counts_coverage_unused_overused_and_suggestions():
    results = [
        _result(["joy"], ["agree"], ["bliss"]),
        _result(["joy"], [], ["bliss"]),
        _result([], ["agree"], ["glee"]),
    ]
    summary = summarize(results, TAX)
    emotion = summary["axes"]["emotion"]
    assert summary["images"] == 3
    assert emotion["covered_share"] == 2 / 3
    assert emotion["counts"] == {"joy": 2, "anger": 0}
    assert emotion["unused"] == ["anger"]
    assert emotion["overused"] == ["joy"]  # 2/3 > 0.4
    assert emotion["suggestions"] == [("bliss", 2), ("glee", 1)]
    assert summary["axes"]["stance"]["overused"] == ["agree"]


def test_summarize_empty_input_does_not_divide_by_zero():
    summary = summarize([], TAX)
    assert summary["axes"]["emotion"]["covered_share"] == 0.0
    assert summary["axes"]["emotion"]["overused"] == []


def test_format_report_is_markdown_with_a_section_per_axis():
    report = format_report(summarize([_result(["joy"], ["agree"])], TAX))
    assert report.startswith("# Vocabulary coverage")
    assert "## emotion" in report and "## stance" in report
    assert "Unused values: anger" in report
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_coverage.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'taxonomy.coverage'`)

- [ ] **Step 3: Write the coverage module**

`taxonomy/coverage.py`:

```python
"""Batch-side helpers for the vocabulary corpus check (P1 spec, section 9). Not imported by the backend."""
import json
import sys
from collections import Counter

from taxonomy.loader import load
from taxonomy.model import Taxonomy

OVERUSE_SHARE = 0.4


def assign_from_response(raw, tax: Taxonomy) -> dict | None:
    """Parse a probe model's JSON answer into valid keys and free-text suggestions.

    Returns None when the answer is not a JSON object. Keys not in the vocabulary become suggestions.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    assigned = {axis: [] for axis in tax.controlled_axes()}
    suggested = {axis: [] for axis in tax.controlled_axes()}
    for axis in tax.controlled_axes():
        values = data.get(axis) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
            if tax.has_key(axis, value):
                if value not in assigned[axis]:
                    assigned[axis].append(value)
            else:
                suggested[axis].append(value)
    extra = data.get("suggested")
    if isinstance(extra, dict):
        for axis in tax.controlled_axes():
            items = extra.get(axis) or []
            if isinstance(items, list):
                suggested[axis].extend(s.strip() for s in items if isinstance(s, str) and s.strip())
    return {"assigned": assigned, "suggested": suggested}


def summarize(results: list[dict], tax: Taxonomy) -> dict:
    total = len(results)
    summary = {"images": total, "axes": {}}
    for axis in tax.controlled_axes():
        counts = Counter({key: 0 for key in tax.values[axis]})
        suggestions: Counter = Counter()
        covered = 0
        for result in results:
            keys = result["assigned"].get(axis, [])
            if keys:
                covered += 1
            counts.update(keys)
            suggestions.update(result["suggested"].get(axis, []))
        summary["axes"][axis] = {
            "covered_share": covered / total if total else 0.0,
            "counts": dict(counts),
            "unused": sorted(key for key, count in counts.items() if count == 0),
            "overused": sorted(key for key, count in counts.items() if total and count / total > OVERUSE_SHARE),
            "suggestions": suggestions.most_common(10),
        }
    return summary


def format_report(summary: dict) -> str:
    lines = ["# Vocabulary coverage", "", f"Images probed: {summary['images']}", ""]
    for axis, data in summary["axes"].items():
        lines += [
            f"## {axis}",
            "",
            f"- Images with at least one value: {data['covered_share']:.0%}",
            f"- Unused values: {', '.join(data['unused']) or 'none'}",
            f"- Overused values (over {OVERUSE_SHARE:.0%} of images): {', '.join(data['overused']) or 'none'}",
            "- Suggested values the model wanted but the vocabulary lacks: "
            + (", ".join(f"{text} ({count})" for text, count in data["suggestions"]) or "none"),
            "",
            "| Value | Images |",
            "|---|---|",
        ]
        lines += [f"| {key} | {count} |" for key, count in sorted(data["counts"].items(), key=lambda kv: -kv[1])]
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    results = []
    failures = 0
    with open(argv[0], encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if "assigned" in entry:
                results.append(entry)
            else:
                failures += 1
    print(format_report(summarize(results, load())))
    print(f"Probe failures (excluded above): {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/taxonomy -v`
Expected: all PASS

- [ ] **Step 5: Write the throwaway probe script**

Create `batch/tools/probe_vocabulary_coverage.py`. It is run by hand once and is not covered by tests.

```python
"""Throwaway corpus probe for the P1 vocabulary check (spec section 9). NOT the P2 extractor.

Reads a stratified sample of active images with a READ-ONLY database role, asks a local vision model to
pick vocabulary values, and writes one JSON line per image. Run by the controller session only:

    DATABASE_URL=<DATABASE_URL_READONLY of the environment> PYTHONIOENCODING=utf-8 \
        python -m batch.tools.probe_vocabulary_coverage --base-path <BASE_PATH> --out results.jsonl

then:  python -m taxonomy.coverage results.jsonl
"""
import argparse
import asyncio
import json
import os
import sys

import ollama
from sqlalchemy import exists, func, select

from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageDescription, OCRText
from taxonomy.coverage import assign_from_response
from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt

STRATA = [  # (name, has_ocr, has_description, share of --n)
    ("ocr+description", True, True, 0.4),
    ("ocr only", True, False, 0.2),
    ("description only", False, True, 0.2),
    ("neither", False, False, 0.2),
]
OCR_CHAR_LIMIT = 600


def build_prompt(tax, ocr_text: str, description: str) -> str:
    sections = [
        "You label memes for a search engine. Look at the image and the extra text below, then say how this meme",
        "would be used as a REPLY to a message. Pick values ONLY from these lists (use the keys exactly).",
        "",
    ]
    for axis in tax.controlled_axes():
        sections += [f"{axis} (up to {tax.axes[axis].max_values}):", vocabulary_prompt(axis, tax), ""]
    sections += [
        f"Text in the image (OCR): {ocr_text[:OCR_CHAR_LIMIT] or '(none)'}",
        f"Existing description: {description or '(none)'}",
        "",
        'Answer with JSON only: {"emotion": [...], "stance": [...], "topic": [...], "tone": [...], '
        '"suggested": {"emotion": [], "stance": [], "topic": [], "tone": []}}. '
        'Put a short free-text word in "suggested" for an axis only when no listed value fits.',
    ]
    return "\n".join(sections)


async def sample(session, has_ocr: bool, has_description: bool, limit: int):
    ocr = exists().where(OCRText.image_id == Image.id)
    desc = exists().where(ImageDescription.image_id == Image.id)
    stmt = (
        select(Image.id, Image.filename)
        .where(Image.status == "active", ocr if has_ocr else ~ocr, desc if has_description else ~desc)
        .order_by(func.random())
        .limit(limit)
    )
    return (await session.execute(stmt)).all()


async def texts_for(session, image_id):
    ocr = (await session.execute(
        select(OCRText.text).where(OCRText.image_id == image_id).order_by(OCRText.id))).scalars().all()
    desc = (await session.execute(
        select(ImageDescription.text).where(ImageDescription.image_id == image_id).limit(1))).scalars().first()
    return " ".join(ocr), desc or ""


async def run(args) -> None:
    tax = load()
    with open(args.out, "w", encoding="utf-8") as out:
        async with AsyncSessionLocal() as session:
            for name, has_ocr, has_description, share in STRATA:
                rows = await sample(session, has_ocr, has_description, round(args.n * share))
                for image_id, filename in rows:
                    ocr_text, description = await texts_for(session, image_id)
                    path = os.path.join(args.base_path, filename)
                    entry = {"image_id": str(image_id), "stratum": name}
                    try:
                        response = ollama.chat(
                            model=args.model, format="json",
                            messages=[{"role": "user", "images": [path],
                                       "content": build_prompt(tax, ocr_text, description)}],
                            options={"temperature": 0, "num_ctx": 8192},
                        )
                        parsed = assign_from_response(response["message"]["content"], tax)
                        entry.update(parsed if parsed else {"error": "unparseable answer"})
                    except Exception as exc:  # one bad image (e.g. WebP) must not stop the probe
                        entry["error"] = str(exc)[:200]
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{name}: {filename} {'ok' if 'assigned' in entry else 'FAILED'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--model", default="qwen2.5vl:7b")
    args = parser.parse_args(argv)
    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        print("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly).", file=sys.stderr)
        return 2
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `python -c "import batch.tools.probe_vocabulary_coverage"` with `DATABASE_URL=postgresql+asyncpg://ocr_readonly:x@localhost:5432/x` set.
Expected: no output, no error (import check only; it makes no connection).

- [ ] **Step 6: Run the probe on the `general` corpus (controller only)**

The controller reads `DATABASE_URL_READONLY` and `BASE_PATH` from `environments/.env.general`, never passes either to a subagent, and runs (PowerShell example, values filled from that file):

```powershell
$env:DATABASE_URL = "<DATABASE_URL_READONLY value>"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.tools.probe_vocabulary_coverage --base-path "<BASE_PATH value>" --out "$env:TEMP\p1-coverage.jsonl" --n 100
```

Expected: about 100 lines of `<stratum>: <file> ok|FAILED`. The run may take a while on `qwen2.5vl:7b`; the process is bounded by the ~10-minute tool limit, so run it with `--n 25` four times into four files if needed and `cat` them together, or launch it as a detached `Start-Process`, per the CLAUDE.md gotcha. Failures from unreadable images are expected and excluded from the report.

- [ ] **Step 7: Produce the report and commit it**

Run: `python -m taxonomy.coverage "$env:TEMP\p1-coverage.jsonl"` and save the output to `docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md`, adding a short header with the date, the environment (`general`), the model, the sample size, the number of probe failures and the strata. Add a "Proposed vocabulary changes" section listing, per axis, values to drop (unused), split (overused) and add (frequent suggestions) with your reasoning.

```bash
git add taxonomy/coverage.py tests/taxonomy/test_coverage.py batch/tools/probe_vocabulary_coverage.py docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md
git commit -m "feat(taxonomy): add vocabulary coverage probe and corpus review"
```

- [ ] **Step 8: User review gate**

Stop and ask the user to review the coverage report and the proposed vocabulary changes, and to edit the YAML freely. Do not apply changes before they answer. This is the single adjustment pass; further tuning belongs to P6.

- [ ] **Step 9: Apply the agreed adjustments**

Edit `batch/data/taxonomy/*.yaml` per the user's decisions (keep `version: 1`, since no lock exists yet and nothing has been released). Re-run `pytest tests/taxonomy -v`; the lint must still pass with the size ranges. Append a "Decisions" section to the review file naming each change made and each suggestion not taken, with the user's reason.

```bash
git add batch/data/taxonomy docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md
git commit -m "feat(taxonomy): adjust vocabularies after corpus coverage review"
```

---

### Task 6: Version-and-hash lock

**Files:**
- Create: `taxonomy/lock.py`
- Create: `tests/taxonomy/test_lock.py`
- Create: `batch/data/taxonomy/taxonomy.lock` (generated)

**Interfaces:**
- Consumes: `taxonomy.loader.{load, DEFAULT_DIR}`.
- Produces: `taxonomy.lock.LOCK_NAME = "taxonomy.lock"`, `check(tax: Taxonomy, directory: Path) -> str | None` (None means consistent, otherwise the failure message), `write_lock(directory: Path) -> str` (returns a status line, raises `TaxonomyError` when the content changed without a version bump), `main(argv=None) -> int` behind `python -m taxonomy.lock`.

- [ ] **Step 1: Write the failing tests**

`tests/taxonomy/test_lock.py`:

```python
import json
import shutil
from pathlib import Path

import pytest

from taxonomy.loader import DEFAULT_DIR, TaxonomyError, load
from taxonomy.lock import LOCK_NAME, check, write_lock

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture
def directory(tmp_path) -> Path:
    target = tmp_path / "tax"
    shutil.copytree(FIXTURE, target)
    return target


def _edit_definition(directory: Path) -> None:
    emotion = directory / "emotion.yaml"
    emotion.write_text(emotion.read_text(encoding="utf-8").replace("Plain happiness.", "Pure delight."),
                       encoding="utf-8")


def _bump_version(directory: Path) -> None:
    path = directory / "taxonomy.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("version: 7", "version: 8"), encoding="utf-8")


def test_missing_lock_is_reported_with_the_command_to_create_it(directory):
    message = check(load(directory), directory)
    assert "python -m taxonomy.lock" in message


def test_write_then_check_is_clean(directory):
    write_lock(directory)
    assert check(load(directory), directory) is None
    stored = json.loads((directory / LOCK_NAME).read_text(encoding="utf-8"))
    assert stored["version"] == 7
    assert stored["content_hash"] == load(directory).content_hash


def test_content_change_without_version_bump_is_reported(directory):
    write_lock(directory)
    _edit_definition(directory)
    assert "bump" in check(load(directory), directory)


def test_write_lock_refuses_content_change_without_version_bump(directory):
    write_lock(directory)
    _edit_definition(directory)
    with pytest.raises(TaxonomyError, match="bump"):
        write_lock(directory)


def test_version_bump_with_stale_lock_asks_for_a_refresh(directory):
    write_lock(directory)
    _edit_definition(directory)
    _bump_version(directory)
    assert "python -m taxonomy.lock" in check(load(directory), directory)
    write_lock(directory)
    assert check(load(directory), directory) is None


def test_write_lock_is_idempotent(directory):
    write_lock(directory)
    first = (directory / LOCK_NAME).read_bytes()
    write_lock(directory)
    assert (directory / LOCK_NAME).read_bytes() == first


def test_shipped_lock_is_current():
    message = check(load(), DEFAULT_DIR)
    assert message is None, message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_lock.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'taxonomy.lock'`)

- [ ] **Step 3: Write the lock module**

`taxonomy/lock.py`:

```python
import json
import sys
from pathlib import Path

from taxonomy.loader import DEFAULT_DIR, TaxonomyError, load
from taxonomy.model import Taxonomy

LOCK_NAME = "taxonomy.lock"
REFRESH_HINT = "run `python -m taxonomy.lock` to refresh it"


def _read_lock(directory: Path) -> dict | None:
    path = directory / LOCK_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check(tax: Taxonomy, directory: Path) -> str | None:
    """Return None when the lock matches the vocabulary files, otherwise a message saying what to do."""
    lock = _read_lock(directory)
    if lock is None:
        return f"{LOCK_NAME} is missing; {REFRESH_HINT}"
    if lock.get("content_hash") == tax.content_hash and lock.get("version") == tax.version:
        return None
    if lock.get("content_hash") != tax.content_hash and lock.get("version") == tax.version:
        return (f"taxonomy content changed without bumping `version` (still {tax.version}); "
                f"bump it in taxonomy.yaml, then {REFRESH_HINT}")
    return f"{LOCK_NAME} is stale for version {tax.version}; {REFRESH_HINT}"


def write_lock(directory: Path) -> str:
    """Write the lock for the taxonomy in `directory`; refuse a content change with an unchanged version."""
    tax = load(directory)
    existing = _read_lock(directory)
    if existing and existing.get("version") == tax.version and existing.get("content_hash") != tax.content_hash:
        raise TaxonomyError(
            f"content changed but `version` is still {tax.version}: bump it in taxonomy.yaml first")
    payload = {"version": tax.version, "content_hash": tax.content_hash}
    (directory / LOCK_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return f"wrote {LOCK_NAME}: version {tax.version}, hash {tax.content_hash[:12]}"


def main(argv=None) -> int:
    try:
        print(write_lock(DEFAULT_DIR))
    except TaxonomyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Generate the shipped lock**

Run: `python -m taxonomy.lock`
Expected: `wrote taxonomy.lock: version 1, hash <12 hex chars>`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/taxonomy -v`
Expected: all PASS, including `test_shipped_lock_is_current`.

- [ ] **Step 6: Commit**

```bash
git add taxonomy/lock.py tests/taxonomy/test_lock.py batch/data/taxonomy/taxonomy.lock
git commit -m "feat(taxonomy): add version-and-hash lock for vocabulary changes"
```

---

### Task 7: Profile JSON schemas and generated types

**Files:**
- Create: `shared/schemas/profilevalue.schema.json`, `shared/schemas/memeprofile.schema.json`, `shared/schemas/queryprofile.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`, `Backend/app/types/generated/*` (new files only)
- Create: `tests/taxonomy/test_profile_schemas.py`

**Interfaces:**
- Consumes: `taxonomy.loader.load` (to cross-check axis names).
- Produces: schemas `ProfileValue {key, confidence, source}`, `MemeProfile {taxonomyVersion, situation, situationConfidence, emotion[], stance[], topic[], tone[], useWhen, humanVerified, stale}`, `QueryProfile {taxonomyVersion, language, parserVersion, degraded, situation, situationConfidence, emotion[], stance[], topic[], tone[]}`; generated Pydantic classes importable as `Backend.app.types.generated.memeprofile.MemeProfile` etc. (confirm the exact module names after generation).

- [ ] **Step 1: Write the schema files**

`shared/schemas/profilevalue.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "profilevalue.schema.json",
  "title": "ProfileValue",
  "type": "object",
  "properties": {
    "key": { "type": "string", "description": "Vocabulary key from the taxonomy (language-neutral)" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "source": { "type": "string", "enum": ["model", "human"] }
  },
  "required": ["key", "confidence", "source"]
}
```

`shared/schemas/memeprofile.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "memeprofile.schema.json",
  "title": "MemeProfile",
  "description": "What a meme conveys and when it is usable as a reply, along the taxonomy axes",
  "type": "object",
  "properties": {
    "taxonomyVersion": { "type": "integer" },
    "situation": { "type": ["string", "null"], "description": "Short English phrase: the situation in which the meme is a fitting reply" },
    "situationConfidence": { "type": ["number", "null"], "minimum": 0, "maximum": 1 },
    "emotion": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "stance": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "topic": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "tone": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "useWhen": { "type": ["string", "null"], "description": "One English sentence, human-readable" },
    "humanVerified": { "type": "boolean" },
    "stale": { "type": "boolean", "description": "Derived: taxonomy version, prompt version or inputs changed since extraction" }
  },
  "required": ["taxonomyVersion", "emotion", "stance", "topic", "tone", "humanVerified", "stale"]
}
```

`shared/schemas/queryprofile.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "queryprofile.schema.json",
  "title": "QueryProfile",
  "description": "How an incoming message was understood: the situation and the reply wanted, along the taxonomy axes",
  "type": "object",
  "properties": {
    "taxonomyVersion": { "type": "integer" },
    "language": { "type": "string", "enum": ["en", "ru", "es", "other"] },
    "parserVersion": { "type": "string" },
    "degraded": { "type": "boolean", "description": "True when the structured parse failed or was skipped" },
    "situation": { "type": ["string", "null"], "description": "Short English phrase describing what is happening in the message" },
    "situationConfidence": { "type": ["number", "null"], "minimum": 0, "maximum": 1 },
    "emotion": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "stance": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "topic": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } },
    "tone": { "type": "array", "items": { "$ref": "profilevalue.schema.json" } }
  },
  "required": ["taxonomyVersion", "language", "parserVersion", "degraded", "emotion", "stance", "topic", "tone"]
}
```

In `shared/schemas/all.schema.json`, add these three lines inside `"definitions"`, directly after the `"ImageDescription"` line:

```json
    "ProfileValue":        { "$ref": "profilevalue.schema.json" },
    "MemeProfile":         { "$ref": "memeprofile.schema.json" },
    "QueryProfile":        { "$ref": "queryprofile.schema.json" },
```

- [ ] **Step 2: Regenerate the three type trees**

Run (frontend, from repo root): `cd Frontend && bash generate-types.sh && cd ..`
Run (Kotlin, from repo root): `python AndroidClient/scripts/generate_dtos.py`
Run (Python, from `Backend/`, command from `documents/generation.md`):

```bash
cd Backend && datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel && cd ..
```

Run: `git status --short Frontend/memes-frontend/src/types/generated AndroidClient Backend/app/types/generated`
Expected: `all.d.ts` modified, `Models.kt` modified, new files under `Backend/app/types/generated/` for the three schemas, and NO modified pre-existing Python files other than `__init__`-style index files (if any). If pre-existing generated files changed, stop, inspect `git diff`, and report; do not commit generator drift. If the Kotlin generator fails on a construct (for example the nullable `["string", "null"]` union), look at how `shared/schemas/imagedescription.schema.json` or other existing schemas express optional fields, mirror that in these schemas, and regenerate all three trees.
Run: `grep -n "MemeProfile\|QueryProfile\|ProfileValue" Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt | head`
Expected: all three names appear in both files.

- [ ] **Step 3: Write the failing schema tests**

First confirm the generated module names: `ls Backend/app/types/generated | grep -i "profile"`. Expected `memeprofile.py`, `profilevalue.py`, `queryprofile.py`. If they differ, use the real names in the imports below.

`tests/taxonomy/test_profile_schemas.py`:

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from Backend.app.types.generated.memeprofile import MemeProfile
from Backend.app.types.generated.queryprofile import QueryProfile
from taxonomy.loader import load

SCHEMAS = Path(__file__).resolve().parents[2] / "shared" / "schemas"
VALUE = {"key": "joy", "confidence": 0.9, "source": "model"}


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["profilevalue", "memeprofile", "queryprofile"])
def test_schema_id_matches_filename(name):
    assert _schema(f"{name}.schema.json")["$id"] == f"{name}.schema.json"


def test_all_schema_registers_the_profile_schemas():
    definitions = _schema("all.schema.json")["definitions"]
    assert definitions["ProfileValue"] == {"$ref": "profilevalue.schema.json"}
    assert definitions["MemeProfile"] == {"$ref": "memeprofile.schema.json"}
    assert definitions["QueryProfile"] == {"$ref": "queryprofile.schema.json"}


@pytest.mark.parametrize("name", ["memeprofile", "queryprofile"])
def test_schema_properties_cover_every_taxonomy_axis(name):
    properties = _schema(f"{name}.schema.json")["properties"]
    for axis in load().axes:
        assert axis in properties, f"{name} schema lacks axis {axis!r}"


def test_meme_profile_accepts_a_valid_example():
    profile = MemeProfile.model_validate({
        "taxonomyVersion": 1, "situation": "plans cancelled", "situationConfidence": 0.7,
        "emotion": [VALUE], "stance": [], "topic": [], "tone": [],
        "useWhen": "Use when plans fall through.", "humanVerified": False, "stale": False,
    })
    assert profile.emotion[0].key == "joy"


def test_meme_profile_allows_null_situation_and_use_when():
    MemeProfile.model_validate({
        "taxonomyVersion": 1, "situation": None, "emotion": [], "stance": [], "topic": [], "tone": [],
        "useWhen": None, "humanVerified": True, "stale": True,
    })


def test_meme_profile_requires_its_required_fields():
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({"taxonomyVersion": 1})


def test_profile_value_rejects_unknown_source_and_out_of_range_confidence():
    base = {"taxonomyVersion": 1, "emotion": [], "stance": [], "topic": [], "tone": [],
            "humanVerified": False, "stale": False}
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({**base, "emotion": [{**VALUE, "source": "robot"}]})
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({**base, "emotion": [{**VALUE, "confidence": 1.5}]})


def test_query_profile_accepts_a_valid_example_and_rejects_bad_language():
    base = {"taxonomyVersion": 1, "language": "ru", "parserVersion": "p1", "degraded": False,
            "emotion": [], "stance": [VALUE], "topic": [], "tone": []}
    assert QueryProfile.model_validate(base).language.value == "ru"
    with pytest.raises(ValidationError):
        QueryProfile.model_validate({**base, "language": "fr"})
```

If `language.value` fails because the generated field is a plain string rather than an Enum, change that one assertion to compare `== "ru"` (check which the generator produced).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/taxonomy/test_profile_schemas.py -v`
Expected: all PASS. If the `Backend.app...` import fails, check how another test under `tests/` imports generated models or run `python -c "import Backend.app.types.generated.memeprofile"` for the real error; fix the import path, not the schema.

- [ ] **Step 5: Verify the schemas against the other repo checks**

Run: `python -c "import json,pathlib; [json.loads(p.read_text(encoding='utf-8')) for p in pathlib.Path('shared/schemas').glob('*.json')]; print('all schema files parse')"`
Expected: `all schema files parse`.
Run (separate command, frontend root): `cd Frontend/memes-frontend && tsc -b && cd ../..`
Expected: no errors (generated types compile).

- [ ] **Step 6: Commit**

```bash
git add shared/schemas Frontend/memes-frontend/src/types/generated AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt Backend/app/types/generated tests/taxonomy/test_profile_schemas.py
git commit -m "feat(schemas): add MemeProfile, QueryProfile and ProfileValue shared schemas with generated types"
```

---

### Task 8: Vocabulary similarity artifact

**Files:**
- Create: `taxonomy/similarity.py`
- Create: `batch/build_taxonomy_similarity.py`
- Modify: `environments/settings.yaml` (add `similarity_threshold` under `taxonomy:`)
- Create: `tests/taxonomy/test_similarity.py`, `tests/taxonomy/test_shipped_similarity.py`, `tests/taxonomy/test_core_imports.py`
- Create: `batch/data/taxonomy/similarity.json` (generated)

**Interfaces:**
- Consumes: `taxonomy.loader.load`, `ai.ollama_embed.OllamaEmbedder`, `taxonomy.model.Taxonomy.similarity_overrides` (shape per axis: `{"add": [[key_a, key_b, score], ...], "remove": [[key_a, key_b], ...]}`).
- Produces: `taxonomy.similarity.compute_neighbours(keys, vectors, threshold) -> dict[str, list[dict]]` (each neighbour `{"key", "score"}`, sorted by score desc then key, self excluded); `apply_overrides(neighbours: dict, overrides: dict, valid_keys: set[str]) -> dict` (symmetric add/remove, raises `ValueError` on an unknown key); `build_artifact(tax, embedder, *, threshold, model_name, model_digest) -> dict`; `check_artifact(artifact: dict, tax) -> list[str]`; `neighbour_stats(artifact) -> dict` with `median`, `max`, `isolated`; artifact top-level keys `taxonomy_version`, `content_hash`, `model_name`, `model_digest`, `threshold`, `axes` (`axis -> key -> [{key, score}]`).

- [ ] **Step 1: Write the failing tests**

`tests/taxonomy/test_similarity.py`:

```python
from pathlib import Path

import numpy as np
import pytest

from taxonomy.loader import load
from taxonomy.similarity import (apply_overrides, build_artifact, check_artifact, compute_neighbours,
                                 neighbour_stats)

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
KEYS = ["a", "b", "c"]
VECTORS = np.array([[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]], dtype=np.float32)  # a.b=0.8, b.c=0.6, a.c=0.0


def test_compute_neighbours_filters_by_threshold_sorts_and_excludes_self():
    result = compute_neighbours(KEYS, VECTORS, threshold=0.55)
    assert result["a"] == [{"key": "b", "score": 0.8}]
    assert result["b"] == [{"key": "a", "score": 0.8}, {"key": "c", "score": 0.6}]
    assert result["c"] == [{"key": "b", "score": 0.6}]


def test_compute_neighbours_keeps_every_key_even_when_isolated():
    assert compute_neighbours(KEYS, VECTORS, threshold=0.99) == {"a": [], "b": [], "c": []}


def test_apply_overrides_adds_and_removes_symmetrically():
    base = compute_neighbours(KEYS, VECTORS, threshold=0.55)
    result = apply_overrides(base, {"add": [["a", "c", 0.5]], "remove": [["a", "b"]]}, set(KEYS))
    assert result["a"] == [{"key": "c", "score": 0.5}]
    assert result["c"] == [{"key": "b", "score": 0.6}, {"key": "a", "score": 0.5}]
    assert result["b"] == [{"key": "c", "score": 0.6}]


def test_apply_overrides_rejects_unknown_keys():
    with pytest.raises(ValueError, match="zzz"):
        apply_overrides({"a": []}, {"add": [["a", "zzz", 0.5]]}, {"a"})


class _FakeEmbedder:
    def embed(self, texts):
        vectors = np.zeros((len(texts), 2), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i] = [1.0, 0.0] if text in {"Plain happiness.", "Confirms what was said."} else [0.0, 1.0]
        return vectors


def test_build_artifact_embeds_definitions_and_applies_taxonomy_overrides():
    tax = load(FIXTURE)
    artifact = build_artifact(tax, _FakeEmbedder(), threshold=0.9, model_name="fake", model_digest="d1")
    assert artifact["taxonomy_version"] == 7
    assert artifact["content_hash"] == tax.content_hash
    assert artifact["model_name"] == "fake" and artifact["model_digest"] == "d1"
    assert artifact["threshold"] == 0.9
    # joy and anger are orthogonal under the fake embedder; the fixture's override adds joy<->anger at 0.5
    assert artifact["axes"]["emotion"]["joy"] == [{"key": "anger", "score": 0.5}]
    assert artifact["axes"]["emotion"]["anger"] == [{"key": "joy", "score": 0.5}]
    assert artifact["axes"]["stance"] == {"agree": [], "refuse": []}


def test_check_artifact_accepts_a_fresh_artifact_and_flags_drift():
    tax = load(FIXTURE)
    artifact = build_artifact(tax, _FakeEmbedder(), threshold=0.9, model_name="fake", model_digest="d1")
    assert check_artifact(artifact, tax) == []

    stale = {**artifact, "taxonomy_version": 6}
    assert any("version" in p for p in check_artifact(stale, tax))
    changed = {**artifact, "content_hash": "other"}
    assert any("content_hash" in p for p in check_artifact(changed, tax))
    unknown = {**artifact, "axes": {**artifact["axes"], "emotion": {"joy": [{"key": "zzz", "score": 0.9}],
                                                                       "anger": []}}}
    assert any("zzz" in p for p in check_artifact(unknown, tax))
    missing = {**artifact, "axes": {**artifact["axes"], "emotion": {"joy": []}}}
    assert any("anger" in p for p in check_artifact(missing, tax))


def test_neighbour_stats_reports_median_max_and_isolated_keys():
    artifact = {"axes": {"emotion": {"a": [{"key": "b", "score": 0.9}], "b": [{"key": "a", "score": 0.9}], "c": []}}}
    stats = neighbour_stats(artifact)
    assert stats["median"] == 1
    assert stats["max"] == 1
    assert stats["isolated"] == ["emotion.c"]
```

`tests/taxonomy/test_shipped_similarity.py`:

```python
import json

from taxonomy.loader import DEFAULT_DIR, load
from taxonomy.similarity import check_artifact


def test_shipped_similarity_artifact_matches_the_shipped_taxonomy():
    artifact = json.loads((DEFAULT_DIR / "similarity.json").read_text(encoding="utf-8"))
    problems = check_artifact(artifact, load())
    assert problems == [], "regenerate with `python -m batch.build_taxonomy_similarity`: " + "; ".join(problems)
```

`tests/taxonomy/test_core_imports.py`:

```python
import ast
import sys
from pathlib import Path

CORE_MODULES = ["__init__", "model", "loader", "validate", "prompt", "lint", "lock"]
ALLOWED_THIRD_PARTY = {"yaml"}
PACKAGE = Path(__file__).resolve().parents[2] / "taxonomy"


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_modules_import_only_stdlib_yaml_and_taxonomy():
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | {"taxonomy"}
    for module in CORE_MODULES:
        offenders = _imported_roots(PACKAGE / f"{module}.py") - allowed
        assert not offenders, f"taxonomy/{module}.py imports {sorted(offenders)}; the backend cannot install them"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/taxonomy/test_similarity.py tests/taxonomy/test_core_imports.py -v`
Expected: `test_similarity.py` FAILs (`ModuleNotFoundError: No module named 'taxonomy.similarity'`); `test_core_imports.py` PASSes already (it guards Tasks 2-6). `test_shipped_similarity.py` is run later, after generation.

- [ ] **Step 3: Write the similarity module**

`taxonomy/similarity.py`:

```python
"""Batch-side (numpy) vocabulary similarity for soft matching on controlled axes (P1 spec, decision 8).

Not a core module: the backend only reads the generated similarity.json.
"""
from statistics import median

import numpy as np

from taxonomy.model import Taxonomy


def compute_neighbours(keys: list[str], vectors: np.ndarray, threshold: float) -> dict[str, list[dict]]:
    """Within-axis cosine neighbours at or above `threshold` (vectors must be L2-normalised)."""
    similarities = vectors @ vectors.T
    neighbours: dict[str, list[dict]] = {}
    for i, key in enumerate(keys):
        row = [
            {"key": keys[j], "score": round(float(similarities[i, j]), 4)}
            for j in range(len(keys))
            if j != i and similarities[i, j] >= threshold
        ]
        row.sort(key=lambda item: (-item["score"], item["key"]))
        neighbours[key] = row
    return neighbours


def apply_overrides(neighbours: dict[str, list[dict]], overrides: dict, valid_keys: set[str]) -> dict[str, list[dict]]:
    """Apply a symmetric `add: [[a, b, score]]` / `remove: [[a, b]]` override to an axis's neighbours."""
    for entry in [*(overrides.get("add") or []), *(overrides.get("remove") or [])]:
        for key in entry[:2]:
            if key not in valid_keys:
                raise ValueError(f"similarity override names unknown key {key!r}")

    result = {key: list(row) for key, row in neighbours.items()}
    for a, b in (pair[:2] for pair in overrides.get("remove") or []):
        result[a] = [n for n in result[a] if n["key"] != b]
        result[b] = [n for n in result[b] if n["key"] != a]
    for a, b, score in overrides.get("add") or []:
        for source, target in ((a, b), (b, a)):
            result[source] = [n for n in result[source] if n["key"] != target]
            result[source].append({"key": target, "score": round(float(score), 4)})
    for row in result.values():
        row.sort(key=lambda item: (-item["score"], item["key"]))
    return result


def build_artifact(tax: Taxonomy, embedder, *, threshold: float, model_name: str, model_digest: str) -> dict:
    axes = {}
    for axis in tax.controlled_axes():
        keys = list(tax.values[axis])
        vectors = embedder.embed([tax.values[axis][key].definition for key in keys])
        neighbours = compute_neighbours(keys, vectors, threshold)
        overrides = tax.similarity_overrides.get(axis) or {}
        axes[axis] = apply_overrides(neighbours, overrides, set(keys)) if overrides else neighbours
    return {
        "taxonomy_version": tax.version,
        "content_hash": tax.content_hash,
        "model_name": model_name,
        "model_digest": model_digest,
        "threshold": threshold,
        "axes": axes,
    }


def check_artifact(artifact: dict, tax: Taxonomy) -> list[str]:
    """Problems that make a stored artifact unusable for the current taxonomy; empty means consistent."""
    problems = []
    if artifact.get("taxonomy_version") != tax.version:
        problems.append(f"taxonomy_version {artifact.get('taxonomy_version')} != {tax.version}")
    if artifact.get("content_hash") != tax.content_hash:
        problems.append("content_hash does not match the vocabulary files")
    stored_axes = artifact.get("axes", {})
    for axis in tax.controlled_axes():
        stored = stored_axes.get(axis)
        if stored is None:
            problems.append(f"axis {axis!r} missing")
            continue
        valid = set(tax.values[axis])
        for key in sorted(valid - set(stored)):
            problems.append(f"{axis}.{key}: missing from artifact")
        for key, row in stored.items():
            if key not in valid:
                problems.append(f"{axis}.{key}: unknown key")
            for neighbour in row:
                if neighbour["key"] not in valid:
                    problems.append(f"{axis}.{key}: unknown neighbour {neighbour['key']!r}")
    return problems


def neighbour_stats(artifact: dict) -> dict:
    counts, isolated = [], []
    for axis, rows in artifact["axes"].items():
        for key, row in rows.items():
            counts.append(len(row))
            if not row:
                isolated.append(f"{axis}.{key}")
    return {"median": median(counts) if counts else 0, "max": max(counts, default=0), "isolated": isolated}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/taxonomy/test_similarity.py tests/taxonomy/test_core_imports.py -v`
Expected: all PASS

- [ ] **Step 5: Write the generator CLI**

`batch/build_taxonomy_similarity.py`:

```python
"""Generates batch/data/taxonomy/similarity.json: within-axis neighbours of the vocabulary keys, from the
Ollama embeddings of their definitions (P1 spec, decision 8).

    PYTHONIOENCODING=utf-8 python -m batch.build_taxonomy_similarity --env general [--threshold 0.75]

The file is committed so a bad neighbour pair shows up in a diff. Re-run after any vocabulary change or
embedding-model change; tests/taxonomy/test_shipped_similarity.py fails when it is stale.
"""
import argparse
import json

from ai.ollama_embed import OllamaEmbedder
from config.settings import load_env, settings
from taxonomy.loader import DEFAULT_DIR, load
from taxonomy.similarity import build_artifact, neighbour_stats

OUTPUT = DEFAULT_DIR / "similarity.json"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--threshold", type=float, default=None,
                        help="override settings.TAXONOMY.SIMILARITY_THRESHOLD")
    parser.add_argument("--model", default=None, help="override settings.TAXONOMY.EMBEDDING_MODEL")
    parser.add_argument("--dry-run", action="store_true", help="print stats, do not write the file")
    args = parser.parse_args(argv)
    load_env(args.env)

    model = args.model or settings.TAXONOMY.EMBEDDING_MODEL
    threshold = args.threshold if args.threshold is not None else settings.TAXONOMY.SIMILARITY_THRESHOLD
    embedder = OllamaEmbedder(model)
    artifact = build_artifact(load(), embedder, threshold=threshold, model_name=model,
                              model_digest=embedder.model_digest())

    stats = neighbour_stats(artifact)
    print(f"threshold={threshold} median_neighbours={stats['median']} max={stats['max']} "
          f"isolated={len(stats['isolated'])} {stats['isolated']}")
    if args.dry_run:
        return
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Choose the threshold from the real score distribution**

Add the initial value to `environments/settings.yaml` under the existing `taxonomy:` group:

```yaml
taxonomy:
  embedding_model: bge-large   # keep the value chosen in Task 1
  similarity_threshold: 0.6    # initial; tuned in Task 8 from the measured neighbour counts
```

Run: `PYTHONIOENCODING=utf-8 python -m batch.build_taxonomy_similarity --env general --dry-run`
Expected: a line like `threshold=0.6 median_neighbours=<m> max=<x> isolated=<n> [...]`.
Tune: BGE-family models score unrelated short texts high, so 0.6 may connect nearly everything. Re-run with `--threshold 0.7`, `0.75`, `0.8`, `0.85` until the median is between 2 and 5 and few keys are isolated. If no threshold gives that, report the distribution to the user rather than forcing it. Set `similarity_threshold` in `settings.yaml` to the chosen value.

- [ ] **Step 7: Generate and review the artifact**

Run: `PYTHONIOENCODING=utf-8 python -m batch.build_taxonomy_similarity --env general`
Expected: `wrote ...similarity.json`.
Review the file for obviously wrong neighbour pairs (for example `joy` next to `anger`, `agree` next to `disagree` is acceptable only if the user wants antonyms treated as related; flag it). For each wrong pair add a `remove` entry, and for each obviously missing pair an `add` entry, under `similarity_overrides` in `batch/data/taxonomy/taxonomy.yaml`, using this exact shape:

```yaml
similarity_overrides:
  emotion:
    remove: [[joy, anger]]
    add: [[resignation, disappointment, 0.7]]
```

Because that edits `taxonomy.yaml`, bump `version` to 2 and run `python -m taxonomy.lock`, then regenerate `similarity.json` (the artifact embeds the content hash). If no overrides are needed, leave `taxonomy.yaml` untouched. Show the user the final neighbour lists for sign-off before committing.

- [ ] **Step 8: Run all taxonomy tests**

Run: `pytest tests/taxonomy -v`
Expected: all PASS, including `test_shipped_similarity.py` and `test_shipped_lock_is_current`.

- [ ] **Step 9: Commit**

```bash
git add taxonomy/similarity.py batch/build_taxonomy_similarity.py batch/data/taxonomy environments/settings.yaml tests/taxonomy
git commit -m "feat(taxonomy): add vocabulary similarity artifact and generator"
```

---

### Task 9: Documentation, tracker and final verification

**Files:**
- Modify: `docs/data-flow.md` (new section), `CLAUDE.md` (batch list entry)
- Modify: the P1 spec (status, plan link, addendum), `docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md`
- Create: `docs/superpowers/reviews/2026-10-02-p1-taxonomy-and-data-model-review.md`

- [ ] **Step 1: Document the planned signals in `docs/data-flow.md`**

Insert a new section before `## 5. Known gaps`, renumbering nothing else:

```markdown
## 4b. Planned: meme profile signals (epic meme-from-text)

Defined by P1 (`docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md`),
**not populated yet** (the producer job is P2). Listed here so "does X use Y?" stays answerable.

| Signal | Table (planned) | Producer (planned) | Used by (planned) |
|---|---|---|---|
| Meme profile (situation, use-when, versions, fingerprint, human_verified) | `meme_profiles` | P2 extraction job | Meme-from-text retrieval, UI |
| Controlled axis values (emotion, stance, topic, tone) | `meme_profile_values` | P2 | Meme-from-text filters, facets, fusion |
| Situation embedding (Ollama-served English model) | `meme_profile_situation_embeddings` | P2 | Meme-from-text retrieval only. Not used by duplicates or `/similar` |
| Vocabulary similarity | `batch/data/taxonomy/similarity.json` (committed, generated by `build_taxonomy_similarity`) | P1 | Soft matching on controlled axes (P4) |

The taxonomy itself (`batch/data/taxonomy/`, loaded by the `taxonomy` package) is shared by all three environments. Rejected Ollama
descriptions never feed profiles. A rejection changes the inputs fingerprint, so the profile is re-extracted by the next P2 run
and stays searchable until then (unlike tags, which are deleted immediately).
```

- [ ] **Step 2: Add the batch-list entry to `CLAUDE.md`**

In the "Maintenance (run as needed)" block of the batch pipeline section, after the `build_ocr_text_embeddings` entry, add:

```
build_taxonomy_similarity   → regenerates batch/data/taxonomy/similarity.json (within-axis neighbours of
                               the meme-from-text vocabulary keys, from Ollama embeddings of their
                               definitions, model/threshold from settings.TAXONOMY.*). Manual-run only,
                               no DB, not run-tracked, not admin-triggerable. Re-run after any change under
                               batch/data/taxonomy/ (after bumping its version and running
                               `python -m taxonomy.lock`) or after changing taxonomy.embedding_model --
                               tests/taxonomy/test_shipped_similarity.py fails when it is stale. See
                               docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md.
```

- [ ] **Step 3: Run the final verification (each root as its own command)**

Run: `pytest tests/taxonomy -v` — Expected: all PASS.
Run: `pytest tests/ai -v` — Expected: all PASS (the new `test_ollama_embed.py` included).
Run: `pytest batch/tests -v` — Expected: all PASS (the new `test_probe_embedding_model.py` included).
Run: `cd Backend && pytest && cd ..` — Expected: all PASS (the new generated Pydantic files must not break app import).
Run: `cd Frontend/memes-frontend && tsc -b && eslint src/ --max-warnings 0 && vitest run && cd ../..` — Expected: all PASS.
Run: `git status --short` — Expected: only intended files; no changes under generated trees beyond Task 7's additions.
`tests/integration/` and `tests/rules/` are intentionally not run: this plan changes no shared normalization or matching code (`rules/normalize.py`, `repository/ocr_lemmas.py`, `rules/concept_tagger.py`, `batch/build_bow.py`) and adds no table (the CLAUDE.md "run the whole integration root" rule is triggered by those shared modules).

- [ ] **Step 4: Write the review report (one iteration)**

Create `docs/superpowers/reviews/2026-10-02-p1-taxonomy-and-data-model-review.md` covering, against the spec's eight acceptance criteria: for each, the evidence (test names, files, commands run and their outputs) and a verdict (met, met with deviation, not met). Include a "Fixed", "Accepted deviations (explained)" and "Intentionally ignored" list as CLAUDE.md requires, and note the two items that depend on live systems (embedding probe result and the corpus coverage check) with their dates.

- [ ] **Step 5: Update the spec and tracker, commit**

In the P1 spec: set `Status: done` (replace `draft`) and add `Plan: docs/superpowers/plans/2026-10-02-p1-taxonomy-and-data-model.md` directly under the status line. In `tracker.md`: set the P1 row `Status` to `done`, and its `Spec / plan` cell to the spec path plus the plan path. Update the tracker "Next step" paragraph to say P1 is done and P2, P3 and P6 can start, and that board tasks for them have not been created.

```bash
git add docs CLAUDE.md
git commit -m "docs: document P1 taxonomy signals, batch entry, review and tracker status"
```

---

## Self-review (plan against spec)

| Spec item | Task |
|---|---|
| Decision 1 axes confirmed, sensitivity as `tone` values | 4 (`tone.yaml` `dark`/`crude`/`explicit`; lint requires the five axes) |
| Decision 2 shared taxonomy, not per environment | 4 (single `batch/data/taxonomy/`), 8 (settings keys in the common file) |
| Decision 3 vocabulary files, rules, sizes, change lock | 4 (files, lint), 6 (lock) |
| Decision 4 loader package, validate, prompt, cwd-independent path | 2, 3 (`DEFAULT_DIR` is package-relative); PyYAML already in backend closure; import guard test in 8 |
| Decision 5 profile and query schemas, generated types | 7 |
| Decision 6 embedding provisional via Ollama, probe, dimension, settings, digest per row | 1 (client, probe, constant, settings, addendum); per-row digest is table design for P2 |
| Decision 7 table design, staleness derivation | Fixed in the spec for P2; `PROFILE_EMBEDDING_DIM` defined in 1. No migration here by design (spec "Out of scope") |
| Decision 8 similarity artifact, overrides, committed, consistency test | 8 |
| Section 9 corpus validation | 5 |
| Acceptance 1-8 | 4+5 (1), 8 import-guard test (2), 7 (3), 1 (4), 8 (5), 5 (6), spec content (7), 9 (8) |

Type consistency check: `Taxonomy`, `AxisSpec`, `VocabValue`, `load`, `TaxonomyError`, `validate_profile`, `ValidationResult`, `vocabulary_prompt`, `lint`, `check`, `write_lock`, `compute_neighbours`, `apply_overrides`, `build_artifact`, `check_artifact`, `neighbour_stats`, `assign_from_response`, `summarize`, `format_report`, `OllamaEmbedder.embed/model_digest` keep the same names and signatures wherever they are used across tasks. The profile JSON uses camelCase (`situationConfidence`, `taxonomyVersion`) in both the validator output and the schemas.
