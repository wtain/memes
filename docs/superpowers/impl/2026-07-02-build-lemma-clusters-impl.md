# build_lemma_clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `batch/build_lemma_clusters.py`, which reads BOW-unmatched lemmas, embeds them (sbert default, clip fallback), clusters them per-language with HDBSCAN, optionally names each cluster via Ollama and looks up the nearest DB concept (clip-only), and writes a human-editable YAML report.

**Architecture:** File-in/file-out batch job following the existing `build_bow.py` / `build_concept_embeddings.py` patterns: a reusable clustering primitive (`batch/utils/clustering.py`) wraps `hdbscan.HDBSCAN`; a new `ai/sbert.py` wrapper mirrors `ai/clip.py`'s `embed_text` interface; `ai/ollama.py` gains a small text-only naming client alongside its existing image-description classes; `repository/concepts.py` gains a method to fetch concept embeddings for the optional nearest-concept lookup. The main script composes these into pure, independently-testable stages (load → resolve language blocks → embed+cluster → name → lookup → write YAML) so the ML/DB-heavy parts are isolated behind thin seams that unit tests fake out.

**Tech Stack:** Python 3.11 (`.venv311`), `hdbscan==0.8.41` (already a dependency), `sentence-transformers==5.6.0` (already added to `requirements.txt` on this branch), `open_clip_torch==3.3.0`, `ollama==0.6.1`, `pyyaml`, `numpy`, SQLAlchemy async ORM.

## Global Constraints

- Per-language clustering only — never mix lemmas from different languages in one HDBSCAN run (spec Non-Goals).
- `sbert` (`paraphrase-multilingual-MiniLM-L12-v2`) is the default embed model; `clip` is a fallback, required (not optional) when `LOOKUP_CONCEPTS=true`.
- `LOOKUP_CONCEPTS=true` with `TEXT_EMBED_MODEL=sbert` must fail fast with a clear error — never silently produce garbage `nearest_concept` results.
- HDBSCAN must be called with `metric='euclidean'` over L2-normalised embeddings (`hdbscan` raises `Unrecognized metric 'cosine'` if given `metric='cosine'` directly).
- Ollama failures (timeout/unavailable) for a single cluster must not abort the run — set `ollama_concept: null` and continue.
- Missing input file → fail immediately. Missing requested language in input → warn and skip (not an error). Fewer than 2 lemmas for a language → skip clustering, log warning. More than 5000 lemmas → warn about O(N²) cost, proceed anyway.
- No automatic writes to the tags/rules DB — this batch only ever produces a YAML report for human review (spec Non-Goals).
- Output is one YAML file per run, covering all processed languages.
- Repositories never call `session.commit()` (project-wide convention, `CLAUDE.md`) — only relevant here for the optional DB read in the `LOOKUP_CONCEPTS` path, which only reads.

---

## File Structure

| File | Responsibility |
|---|---|
| `batch/utils/clustering.py` | *(new)* `build_clusters_from_embeddings` — thin `hdbscan.HDBSCAN` wrapper, reusable by other batches. |
| `ai/sbert.py` | *(new)* `SbertModel` — multilingual sentence-embedding wrapper, same `embed_text(text) -> np.ndarray` shape as `ai/clip.py`'s `ClipModel`. |
| `ai/ollama.py` | *(modified)* add `build_concept_naming_prompt` + `OllamaConceptNamer` alongside the existing `OllamaImageDescriber`/`OllamaAnimalDetector`. |
| `repository/concepts.py` | *(modified)* add `ConceptsRepository.get_all_with_embeddings()` for the `LOOKUP_CONCEPTS` path. |
| `batch/build_lemma_clusters.py` | *(new)* main entry point — loading/language-resolution, cluster-record building, YAML writing, nearest-concept lookup, and `run()`/`main()` orchestration. |
| `tests/batch/test_clustering.py` | *(new)* unit tests for the HDBSCAN wrapper. |
| `tests/ai/test_sbert.py` | *(new)* unit tests for `SbertModel` (monkeypatched, no real model download). |
| `tests/ai/test_ollama.py` | *(new)* unit tests for prompt building + `OllamaConceptNamer`. |
| `tests/integration/test_concepts_repository.py` | *(new)* integration test for `get_all_with_embeddings()` (real DB, pgvector — same pattern as `tests/integration/test_rebuild_duplicates.py`). |
| `tests/batch/test_build_lemma_clusters.py` | *(new)* unit tests for every pure helper plus end-to-end `run()`/`main()` tests using fakes (no real ML model, no real Ollama, no real DB). |

`tests/batch/__init__.py` and `tests/ai/__init__.py` are new empty packages (create with `Bash`, not `Write`, per the empty-files convention already used for `__init__.py` in this repo).

---

## Execution Order

**Phase 1 — parallel (independent files, no shared state):**
- Task 1: `batch/utils/clustering.py`
- Task 2: `ai/sbert.py`
- Task 3: `ai/ollama.py` (additive — existing classes untouched)
- Task 4: `repository/concepts.py` (additive)

**Phase 2 — sequential (all three tasks touch `batch/build_lemma_clusters.py` and its test file; must run one at a time, in order, after Phase 1 completes):**
- Task 5: loading + language-block resolution
- Task 6: cluster-record building + YAML writer + nearest-concept math
- Task 7: `run()`/`main()` orchestration (depends on Tasks 1–6)

---

## Task 1: `batch/utils/clustering.py`

**Files:**
- Create: `batch/utils/clustering.py`
- Create: `tests/batch/__init__.py` (empty)
- Test: `tests/batch/test_clustering.py`

**Interfaces:**
- Produces: `build_clusters_from_embeddings(keys: list[str], embeddings: np.ndarray, min_cluster_size: int, min_samples: int | None = None, cluster_selection_epsilon: float = 0.0) -> dict[int, list[str]]`. Contract: caller guarantees `len(keys) >= 2` and `embeddings.shape == (len(keys), D)`. Label `-1` is HDBSCAN's noise bucket.

- [ ] **Step 1: Create empty test package**

Run: `mkdir -p tests/batch && touch tests/batch/__init__.py`

- [ ] **Step 2: Write the failing test**

```python
# tests/batch/test_clustering.py
import numpy as np

from batch.utils.clustering import build_clusters_from_embeddings


def test_build_clusters_from_embeddings_groups_close_points_separately():
    keys = ["a", "b", "c", "d", "outlier"]
    embeddings = np.array([
        [1.0, 0.0],
        [0.9, 0.1],
        [0.0, 1.0],
        [0.1, 0.9],
        [5.0, 5.0],
    ])

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size=2)

    non_noise = {frozenset(members) for label, members in groups.items() if label != -1}
    assert frozenset({"a", "b"}) in non_noise
    assert frozenset({"c", "d"}) in non_noise
    assert groups[-1] == ["outlier"]


def test_build_clusters_from_embeddings_respects_min_cluster_size():
    keys = ["a", "b", "c", "d"]
    embeddings = np.array([
        [1.0, 0.0],
        [0.99, 0.01],
        [0.0, 1.0],
        [10.0, 10.0],
    ])

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size=3)

    # No group of size >= 3 exists among 4 well-separated points -> everything is noise.
    assert set(groups.keys()) == {-1}
    assert sorted(groups[-1]) == ["a", "b", "c", "d"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_clustering.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.utils.clustering'`

- [ ] **Step 4: Write minimal implementation**

```python
# batch/utils/clustering.py
import numpy as np
from hdbscan import HDBSCAN


def build_clusters_from_embeddings(
    keys: list[str],
    embeddings: np.ndarray,
    min_cluster_size: int,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
) -> dict[int, list[str]]:
    """
    Run HDBSCAN over L2-normalised embeddings (metric='euclidean' — for
    normalised vectors this is a monotonic function of cosine similarity,
    since ||a-b||^2 = 2 - 2*cos_sim(a,b)). Returns {label: [keys]}, where
    label -1 is HDBSCAN's noise bucket (maps directly to "singletons").

    Caller must ensure len(keys) >= 2 and embeddings are already L2-normalised.
    """
    clusterer = HDBSCAN(
        metric="euclidean",
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=cluster_selection_epsilon,
    )
    labels = clusterer.fit_predict(embeddings)

    groups: dict[int, list[str]] = {}
    for key, label in zip(keys, labels):
        groups.setdefault(int(label), []).append(key)
    return groups
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_clustering.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add batch/utils/clustering.py tests/batch/__init__.py tests/batch/test_clustering.py
git commit -m "feat(batch): add HDBSCAN clustering utility for embedding-based grouping"
```

---

## Task 2: `ai/sbert.py`

**Files:**
- Create: `ai/sbert.py`
- Create: `tests/ai/__init__.py` (empty)
- Test: `tests/ai/test_sbert.py`

**Interfaces:**
- Produces: `SbertModel(model_name: str = "paraphrase-multilingual-MiniLM-L12-v2")` with `.embed_text(text: str) -> np.ndarray` (L2-normalised, same shape contract as `ai/clip.py`'s `ClipModel.embed_text`).

- [ ] **Step 1: Create empty test package**

Run: `mkdir -p tests/ai && touch tests/ai/__init__.py`

- [ ] **Step 2: Write the failing test**

```python
# tests/ai/test_sbert.py
import numpy as np

from ai.sbert import SbertModel


class _FakeSentenceTransformer:
    def __init__(self, model_name):
        self.model_name = model_name

    def encode(self, text, normalize_embeddings=False):
        assert normalize_embeddings is True
        return np.array([1.0, 2.0, 3.0])


def test_embed_text_returns_numpy_array(monkeypatch):
    monkeypatch.setattr("ai.sbert.SentenceTransformer", _FakeSentenceTransformer)
    model = SbertModel(model_name="fake-model")

    result = model.embed_text("hello")

    assert isinstance(result, np.ndarray)
    np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0]))


def test_default_model_name_is_multilingual_minilm(monkeypatch):
    captured = {}

    class _Capturing(_FakeSentenceTransformer):
        def __init__(self, model_name):
            captured["model_name"] = model_name
            super().__init__(model_name)

    monkeypatch.setattr("ai.sbert.SentenceTransformer", _Capturing)
    SbertModel()

    assert captured["model_name"] == "paraphrase-multilingual-MiniLM-L12-v2"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/ai/test_sbert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai.sbert'`

- [ ] **Step 4: Write minimal implementation**

```python
# ai/sbert.py
import numpy as np
from sentence_transformers import SentenceTransformer


class SbertModel:

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self.model = SentenceTransformer(model_name)

    def embed_text(self, text: str) -> np.ndarray:
        embedding = self.model.encode(text, normalize_embeddings=True)
        return np.asarray(embedding)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/ai/test_sbert.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add ai/sbert.py tests/ai/__init__.py tests/ai/test_sbert.py
git commit -m "feat(ai): add multilingual sbert text-embedding wrapper"
```

---

## Task 3: `ai/ollama.py` — cluster naming

**Files:**
- Modify: `ai/ollama.py` (append below existing `OllamaAnimalDetector`; do not touch existing classes)
- Test: `tests/ai/test_ollama.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `build_concept_naming_prompt(language: str, word_freqs: list[tuple[str, int]]) -> tuple[str, str]` (system, user prompt strings) and `OllamaConceptNamer(model: str = "qwen2")` with `.name_cluster(language: str, word_freqs: list[tuple[str, int]]) -> str | None` (returns `None` on any Ollama failure, never raises).

- [ ] **Step 1: Write the failing test**

```python
# tests/ai/test_ollama.py
import ollama

from ai.ollama import OllamaConceptNamer, build_concept_naming_prompt


def test_build_concept_naming_prompt_formats_words_and_frequencies():
    system, user = build_concept_naming_prompt("ru", [("мем", 232), ("мемасик", 45)])

    assert "concept name" in system.lower()
    assert "Language: Russian" in user
    assert "мем (232)" in user
    assert "мемасик (45)" in user


def test_build_concept_naming_prompt_unknown_language_falls_back_to_code():
    _, user = build_concept_naming_prompt("fr", [("chat", 10)])

    assert "Language: fr" in user


def test_name_cluster_returns_stripped_content(monkeypatch):
    def fake_chat(model, messages):
        assert model == "qwen2"
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        return {"message": {"content": " meme \n"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    namer = OllamaConceptNamer(model="qwen2")

    result = namer.name_cluster("ru", [("мем", 232)])

    assert result == "meme"


def test_name_cluster_returns_none_on_failure(monkeypatch):
    def fake_chat(model, messages):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ollama, "chat", fake_chat)
    namer = OllamaConceptNamer(model="qwen2")

    result = namer.name_cluster("ru", [("мем", 232)])

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/ai/test_ollama.py -v`
Expected: FAIL with `ImportError: cannot import name 'OllamaConceptNamer' from 'ai.ollama'`

- [ ] **Step 3: Write minimal implementation**

Append to the end of `ai/ollama.py` (existing `import ollama` at the top of the file already covers this; existing classes are untouched):

```python
_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "es": "Spanish",
}


def build_concept_naming_prompt(language: str, word_freqs: list[tuple[str, int]]) -> tuple[str, str]:
    system = (
        "You are a concise tagger for a meme image database. Given a list of word forms "
        "and their frequency of occurrence in image texts, propose a single short concept "
        "name (1-3 English words) that best describes what they have in common. "
        "Respond with the concept name only, no explanation."
    )
    language_name = _LANGUAGE_NAMES.get(language, language)
    word_lines = "\n".join(f"- {word} ({freq})" for word, freq in word_freqs)
    user = f"Language: {language_name}\nWords and frequencies:\n{word_lines}"
    return system, user


class OllamaConceptNamer:

    def __init__(self, model: str = "qwen2"):
        self.model = model

    def name_cluster(self, language: str, word_freqs: list[tuple[str, int]]) -> str | None:
        system, user = build_concept_naming_prompt(language, word_freqs)
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response["message"]["content"].strip()
        except Exception as e:
            print(f"Ollama naming failed for cluster ({language}): {e}")
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/ai/test_ollama.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add ai/ollama.py tests/ai/test_ollama.py
git commit -m "feat(ai): add Ollama-based cluster concept naming"
```

---

## Task 4: `repository/concepts.py` — concept embeddings for lookup

**Files:**
- Modify: `repository/concepts.py`
- Test: `tests/integration/test_concepts_repository.py` (requires live Postgres+pgvector — same as `tests/integration/test_rebuild_duplicates.py`; not run by the default `pytest` invocation)

**Interfaces:**
- Consumes: `Storage.models.Concept` (existing).
- Produces: `ConceptsRepository.get_all_with_embeddings() -> Sequence[Row]` where each row has `.id: int`, `.name: str`, `.embedding: list[float]`, restricted to concepts with a non-null embedding.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_concepts_repository.py
"""
Integration tests for repository/concepts.py's get_all_with_embeddings().

These tests require a live PostgreSQL instance with pgvector — same setup as
tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest

from repository.concepts import ConceptsRepository
from Storage.models import Concept


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_with_embeddings_returns_concepts_with_vectors(db_session):
    dim = 512
    vec = [0.0] * dim
    vec[0] = 1.0
    concept = Concept(name=f"test-{uuid.uuid4()}", embedding=vec)
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptsRepository(db_session)
    rows = await repo.get_all_with_embeddings()

    matching = [r for r in rows if r.id == concept.id]
    assert len(matching) == 1
    assert matching[0].name == concept.name
    assert list(matching[0].embedding) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_with_embeddings_excludes_null_embeddings(db_session):
    concept = Concept(name=f"test-null-{uuid.uuid4()}", embedding=None)
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptsRepository(db_session)
    rows = await repo.get_all_with_embeddings()

    assert concept.id not in [r.id for r in rows]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_concepts_repository.py -v`
(Requires `DATABASE_URL` pointed at a live pgvector instance, per `tests/integration/conftest.py`.)
Expected: FAIL with `AttributeError: 'ConceptsRepository' object has no attribute 'get_all_with_embeddings'`

- [ ] **Step 3: Write minimal implementation**

In `repository/concepts.py`, add a method to the existing `ConceptsRepository` class (below `add`):

```python
    async def get_all_with_embeddings(self):
        result = await self.session.execute(
            select(Concept.id, Concept.name, Concept.embedding)
            .where(Concept.embedding.isnot(None))
        )
        return result.all()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_concepts_repository.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add repository/concepts.py tests/integration/test_concepts_repository.py
git commit -m "feat(repository): add concept-embeddings lookup for lemma-cluster nearest-concept matching"
```

---

## Task 5: `batch/build_lemma_clusters.py` — loading + language resolution

**Depends on:** Phase 1 complete (no direct imports yet, but must run after to keep the sequential file-touch order clean).

**Files:**
- Create: `batch/build_lemma_clusters.py`
- Create: `tests/batch/test_build_lemma_clusters.py`

**Interfaces:**
- Produces: `load_lemma_source(path: str) -> dict` and `resolve_language_blocks(data: dict, language: str) -> dict[str, dict[str, int]]`. `resolve_language_blocks` is consumed by Task 7's `run()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/batch/test_build_lemma_clusters.py
import json

import pytest

from batch.build_lemma_clusters import load_lemma_source, resolve_language_blocks


def test_load_lemma_source_reads_json(tmp_path):
    path = tmp_path / "unmatched.json"
    path.write_text(json.dumps({"ru": {"мем": 5}}), encoding="utf-8")

    data = load_lemma_source(str(path))

    assert data == {"ru": {"мем": 5}}


def test_load_lemma_source_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_lemma_source("does_not_exist.json")


def test_resolve_language_blocks_all_returns_every_block():
    data = {"ru": {"мем": 5}, "en": {"lol": 10}}

    result = resolve_language_blocks(data, "all")

    assert result == data


def test_resolve_language_blocks_specific_language():
    data = {"ru": {"мем": 5}, "en": {"lol": 10}}

    result = resolve_language_blocks(data, "en")

    assert result == {"en": {"lol": 10}}


def test_resolve_language_blocks_missing_language_warns_and_skips(capsys):
    data = {"ru": {"мем": 5}}

    result = resolve_language_blocks(data, "fr")

    assert result == {}
    assert "fr" in capsys.readouterr().out


def test_resolve_language_blocks_flat_dict_treated_as_en():
    flat_data = {"lol": 120, "lmao": 45}

    result = resolve_language_blocks(flat_data, "all")

    assert result == {"en": {"lol": 120, "lmao": 45}}


def test_resolve_language_blocks_empty_dict_treated_as_empty_en():
    result = resolve_language_blocks({}, "all")

    assert result == {"en": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.build_lemma_clusters'`

- [ ] **Step 3: Write minimal implementation**

```python
# batch/build_lemma_clusters.py
import json


def load_lemma_source(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_language_blocks(data: dict, language: str) -> dict[str, dict[str, int]]:
    """
    Normalize raw BOW JSON into {lang: {lemma: freq}}.

    Flat dicts (descriptions-sourced BOW files have no per-language keys) are
    treated as a single implicit "en" block. `language="all"` returns every
    block found; a specific language returns only that block (empty dict +
    warning if the language is absent from the input).
    """
    is_flat = not data or not isinstance(next(iter(data.values())), dict)
    normalized = {"en": data} if is_flat else data

    if language == "all":
        return normalized

    if language not in normalized:
        print(f"WARNING: language {language!r} not found in input file, skipping")
        return {}

    return {language: normalized[language]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/build_lemma_clusters.py tests/batch/test_build_lemma_clusters.py
git commit -m "feat(batch): add lemma-cluster input loading and language-block resolution"
```

---

## Task 6: `batch/build_lemma_clusters.py` — cluster records, YAML output, nearest-concept math

**Depends on:** Task 5 (same file, appends to it).

**Files:**
- Modify: `batch/build_lemma_clusters.py`
- Modify: `tests/batch/test_build_lemma_clusters.py` (append)

**Interfaces:**
- Consumes: nothing new from earlier tasks directly (pure functions), but shares the module with Task 5's functions.
- Produces:
  - `build_cluster_records(groups: dict[int, list[str]], frequencies: dict[str, int]) -> tuple[list[dict], list[dict]]` — clusters sorted by `total_frequency` descending, `id` numbered from 1; each cluster dict has keys `id`, `ollama_concept` (`None`), `total_frequency`, `size`, `members` (dict, frequency-descending order preserved from input). Singletons preserve input order.
  - `write_yaml_output(output_file: str, parameters: dict, languages: dict) -> None`
  - `nearest_concept(centroid: np.ndarray, concept_rows: list[tuple[int, str, np.ndarray]]) -> dict | None` — returns `{"name", "concept_id", "cosine_distance"}` for the closest row by cosine distance, or `None` if `concept_rows` is empty.

All three are consumed by Task 7's `run()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/batch/test_build_lemma_clusters.py`:

```python
import numpy as np
import yaml

from batch.build_lemma_clusters import build_cluster_records, nearest_concept, write_yaml_output


def test_build_cluster_records_groups_and_sorts_by_total_frequency():
    frequencies = {
        "мем": 232, "мемасик": 45, "мемный": 18,
        "металхед": 89, "metalhead": 45, "стонкс": 12,
    }
    groups = {
        0: ["мем", "мемасик", "мемный"],
        1: ["металхед", "metalhead"],
        -1: ["стонкс"],
    }

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert [c["id"] for c in clusters] == [1, 2]
    assert clusters[0]["total_frequency"] == 295
    assert clusters[0]["size"] == 3
    assert clusters[0]["members"] == {"мем": 232, "мемасик": 45, "мемный": 18}
    assert clusters[0]["ollama_concept"] is None
    assert clusters[1]["total_frequency"] == 134
    assert singletons == [{"lemma": "стонкс", "frequency": 12}]


def test_build_cluster_records_no_singletons():
    frequencies = {"a": 5, "b": 3}
    groups = {0: ["a", "b"]}

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert len(clusters) == 1
    assert singletons == []


def test_build_cluster_records_all_noise():
    frequencies = {"a": 5, "b": 3}
    groups = {-1: ["a", "b"]}

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert clusters == []
    assert singletons == [{"lemma": "a", "frequency": 5}, {"lemma": "b", "frequency": 3}]


def test_write_yaml_output_writes_expected_structure(tmp_path):
    output_file = tmp_path / "out" / "clusters.yaml"
    parameters = {"min_cluster_size": 2, "embed_model": "sbert"}
    languages = {
        "ru": {
            "clusters": [{
                "id": 1, "ollama_concept": "meme", "total_frequency": 295, "size": 3,
                "members": {"мем": 232, "мемасик": 45, "мемный": 18},
            }],
            "singletons": [{"lemma": "стонкс", "frequency": 12}],
        }
    }

    write_yaml_output(str(output_file), parameters, languages)

    loaded = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert loaded["parameters"] == parameters
    assert loaded["languages"] == languages
    assert "generated_at" in loaded


def test_nearest_concept_picks_closest_by_cosine_distance():
    centroid = np.array([1.0, 0.0])
    rows = [
        (1, "Metal", np.array([0.0, 1.0])),
        (2, "Memes", np.array([0.99, 0.14])),
    ]

    result = nearest_concept(centroid, rows)

    assert result["concept_id"] == 2
    assert result["name"] == "Memes"
    assert result["cosine_distance"] < 0.02


def test_nearest_concept_no_rows_returns_none():
    assert nearest_concept(np.array([1.0, 0.0]), []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_cluster_records' from 'batch.build_lemma_clusters'`

- [ ] **Step 3: Write minimal implementation**

Append to `batch/build_lemma_clusters.py` (add these imports to the top of the file alongside the existing `import json`):

```python
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml
```

Then add the functions:

```python
def build_cluster_records(
    groups: dict[int, list[str]],
    frequencies: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """
    Turn HDBSCAN's {label: [lemma, ...]} into (clusters, singletons).
    Clusters are sorted by total_frequency descending and numbered from 1.
    Singleton (label -1) lemmas and cluster members keep their relative
    order from `frequencies` (build_bow writes frequency-descending order).
    """
    order = {lemma: i for i, lemma in enumerate(frequencies)}

    cluster_groups = [members for label, members in groups.items() if label != -1]
    cluster_groups.sort(key=lambda members: sum(frequencies[m] for m in members), reverse=True)

    clusters = []
    for idx, members in enumerate(cluster_groups, start=1):
        ordered_members = sorted(members, key=lambda m: order[m])
        member_freqs = {m: frequencies[m] for m in ordered_members}
        clusters.append({
            "id": idx,
            "ollama_concept": None,
            "total_frequency": sum(member_freqs.values()),
            "size": len(member_freqs),
            "members": member_freqs,
        })

    singleton_lemmas = sorted(groups.get(-1, []), key=lambda m: order[m])
    singletons = [{"lemma": lemma, "frequency": frequencies[lemma]} for lemma in singleton_lemmas]

    return clusters, singletons


def write_yaml_output(output_file: str, parameters: dict, languages: dict) -> None:
    os.makedirs(Path(output_file).parent, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": parameters,
        "languages": languages,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def nearest_concept(centroid: np.ndarray, concept_rows: list[tuple[int, str, np.ndarray]]) -> dict | None:
    if not concept_rows:
        return None

    def distance(row):
        _, _, embedding = row
        return 1.0 - float(np.dot(centroid, np.asarray(embedding)))

    best = min(concept_rows, key=distance)
    concept_id, name, _ = best
    return {"name": name, "concept_id": concept_id, "cosine_distance": round(distance(best), 4)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/build_lemma_clusters.py tests/batch/test_build_lemma_clusters.py
git commit -m "feat(batch): add cluster-record building, YAML writer, and nearest-concept lookup"
```

---

## Task 7: `batch/build_lemma_clusters.py` — `run()` / `main()` orchestration

**Depends on:** Task 6 (same file), Task 1 (`build_clusters_from_embeddings`), Task 2 (`SbertModel`), Task 3 (`OllamaConceptNamer`), Task 4 (`ConceptsRepository.get_all_with_embeddings`).

**Files:**
- Modify: `batch/build_lemma_clusters.py`
- Modify: `tests/batch/test_build_lemma_clusters.py` (append)

**Interfaces:**
- Consumes:
  - `batch.utils.clustering.build_clusters_from_embeddings(keys, embeddings, min_cluster_size, min_samples=None, cluster_selection_epsilon=0.0) -> dict[int, list[str]]`
  - `ai.sbert.SbertModel().embed_text(text: str) -> np.ndarray`
  - `ai.clip.ClipModel().embed_text(text: str) -> np.ndarray`
  - `ai.ollama.OllamaConceptNamer(model).name_cluster(language, word_freqs) -> str | None`
  - `repository.concepts.ConceptsRepository(session).get_all_with_embeddings() -> Sequence[Row]` (rows with `.id`, `.name`, `.embedding`)
  - `Storage.db.AsyncSessionLocal`
  - This module's own `load_lemma_source`, `resolve_language_blocks`, `build_cluster_records`, `write_yaml_output`, `nearest_concept` (Tasks 5–6).
- Produces: `async def run(input_file, output_file, language="all", min_cluster_size=2, min_samples=None, cluster_selection_epsilon=0.0, embed_model="sbert", ollama_model="qwen2", ollama_enabled=True, lookup_concepts=False) -> None` and `async def main() -> None`. Also exposes `_get_embedder(name: str)` and `_get_namer(model: str)` and `_load_concept_rows()` as monkeypatch seams for tests.

- [ ] **Step 1: Write the failing test**

Append to `tests/batch/test_build_lemma_clusters.py`:

```python
import pytest

from batch.build_lemma_clusters import main, run


class _FakeEmbedder:
    def __init__(self, vectors: dict):
        self.vectors = vectors

    def embed_text(self, text: str) -> np.ndarray:
        return np.array(self.vectors[text], dtype=float)


class _FakeNamer:
    def __init__(self, name: str = "meme"):
        self.name = name

    def name_cluster(self, language, word_freqs):
        return self.name


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_run_end_to_end_writes_expected_yaml(tmp_path, monkeypatch):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"ru": {"мем": 232, "мемасик": 45, "стонкс": 12}}', encoding="utf-8")
    output_file = tmp_path / "clusters.yaml"

    vectors = {"мем": [1.0, 0.0], "мемасик": [0.95, 0.05], "стонкс": [0.0, 1.0]}
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr("batch.build_lemma_clusters._get_namer", lambda model: _FakeNamer("meme"))

    await run(
        input_file=str(input_file), output_file=str(output_file),
        min_cluster_size=2, ollama_enabled=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    ru = result["languages"]["ru"]
    assert ru["clusters"][0]["ollama_concept"] == "meme"
    assert ru["clusters"][0]["members"] == {"мем": 232, "мемасик": 45}
    assert ru["singletons"] == [{"lemma": "стонкс", "frequency": 12}]


@pytest.mark.asyncio
async def test_run_ollama_disabled_leaves_concept_null(tmp_path, monkeypatch):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"en": {"lol": 10, "lmao": 8}}', encoding="utf-8")
    output_file = tmp_path / "out.yaml"

    vectors = {"lol": [1.0, 0.0], "lmao": [0.99, 0.01]}
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))

    await run(
        input_file=str(input_file), output_file=str(output_file),
        min_cluster_size=2, ollama_enabled=False,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert result["languages"]["en"]["clusters"][0]["ollama_concept"] is None


@pytest.mark.asyncio
async def test_run_skips_clustering_for_fewer_than_two_lemmas(tmp_path, monkeypatch, capsys):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"es": {"gato": 3}}', encoding="utf-8")
    output_file = tmp_path / "out.yaml"

    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder({}))

    await run(input_file=str(input_file), output_file=str(output_file), ollama_enabled=False)

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert result["languages"]["es"]["clusters"] == []
    assert result["languages"]["es"]["singletons"] == [{"lemma": "gato", "frequency": 3}]
    assert "fewer than 2 lemmas" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_run_lookup_concepts_with_sbert_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="TEXT_EMBED_MODEL=clip"):
        await run(
            input_file=str(tmp_path / "missing.json"), output_file=str(tmp_path / "out.yaml"),
            embed_model="sbert", lookup_concepts=True,
        )


@pytest.mark.asyncio
async def test_run_lookup_concepts_attaches_nearest_concept(tmp_path, monkeypatch):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"en": {"lol": 10, "lmao": 8}}', encoding="utf-8")
    output_file = tmp_path / "out.yaml"

    vectors = {"lol": [1.0, 0.0], "lmao": [0.9, 0.1]}
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr(
        "batch.build_lemma_clusters._load_concept_rows",
        lambda: _async_return([(1, "Internet Memes", np.array([1.0, 0.0]))]),
    )

    await run(
        input_file=str(input_file), output_file=str(output_file), min_cluster_size=2,
        embed_model="clip", ollama_enabled=False, lookup_concepts=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    nearest = result["languages"]["en"]["clusters"][0]["nearest_concept"]
    assert nearest["concept_id"] == 1
    assert nearest["name"] == "Internet Memes"


@pytest.mark.asyncio
async def test_run_lookup_concepts_no_concepts_in_db_sets_null(tmp_path, monkeypatch, capsys):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"en": {"lol": 10, "lmao": 8}}', encoding="utf-8")
    output_file = tmp_path / "out.yaml"

    vectors = {"lol": [1.0, 0.0], "lmao": [0.9, 0.1]}
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr("batch.build_lemma_clusters._load_concept_rows", lambda: _async_return([]))

    await run(
        input_file=str(input_file), output_file=str(output_file), min_cluster_size=2,
        embed_model="clip", ollama_enabled=False, lookup_concepts=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert result["languages"]["en"]["clusters"][0]["nearest_concept"] is None
    assert "no concept embeddings" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_main_reads_env_vars_and_calls_run(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setenv("TEXT_SCOPE", "all")
    monkeypatch.setenv("BOW_OUTPUT_FILE", "bow.json")
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")
    monkeypatch.setenv("LANGUAGE", "ru")
    monkeypatch.setenv("MIN_CLUSTER_SIZE", "3")
    monkeypatch.setenv("MIN_SAMPLES", "2")
    monkeypatch.setenv("CLUSTER_SELECTION_EPSILON", "0.15")
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
    monkeypatch.delenv("TEXT_SCOPE", raising=False)
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")

    await main()

    assert captured["input_file"] == "unmatched.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: FAIL with `ImportError: cannot import name 'run' from 'batch.build_lemma_clusters'`

- [ ] **Step 3: Write minimal implementation**

Add to the top of `batch/build_lemma_clusters.py` (alongside the existing imports):

```python
import asyncio

from Storage.db import AsyncSessionLocal
from repository.concepts import ConceptsRepository
```

Append the orchestration code:

```python
def _get_embedder(name: str):
    if name == "sbert":
        from ai.sbert import SbertModel
        return SbertModel()
    if name == "clip":
        from ai.clip import ClipModel
        return ClipModel()
    raise ValueError(f"Unknown TEXT_EMBED_MODEL: {name!r}")


def _get_namer(model: str):
    from ai.ollama import OllamaConceptNamer
    return OllamaConceptNamer(model=model)


async def _load_concept_rows() -> list[tuple[int, str, np.ndarray]]:
    async with AsyncSessionLocal() as session:
        repo = ConceptsRepository(session)
        rows = await repo.get_all_with_embeddings()
        return [(r.id, r.name, np.asarray(r.embedding)) for r in rows]


def _cluster_centroid(members: dict, embeddings_by_lemma: dict) -> np.ndarray:
    vectors = np.stack([embeddings_by_lemma[lemma] for lemma in members])
    centroid = vectors.mean(axis=0)
    return centroid / np.linalg.norm(centroid)


def _process_language(
    lang: str,
    lemma_freqs: dict[str, int],
    embedder,
    namer,
    min_cluster_size: int,
    min_samples: int | None,
    cluster_selection_epsilon: float,
    lookup_concepts: bool,
    concept_rows: list[tuple[int, str, np.ndarray]],
) -> dict:
    if len(lemma_freqs) < 2:
        print(f"WARNING: language {lang!r} has fewer than 2 lemmas, skipping clustering")
        singletons = [{"lemma": lemma, "frequency": freq} for lemma, freq in lemma_freqs.items()]
        return {"clusters": [], "singletons": singletons}

    if len(lemma_freqs) > 5000:
        print(f"WARNING: language {lang!r} has {len(lemma_freqs)} lemmas (>5000) - O(N^2) clustering cost is high")

    keys = list(lemma_freqs)
    embeddings_by_lemma = {lemma: embedder.embed_text(lemma) for lemma in keys}
    embeddings = np.stack([embeddings_by_lemma[lemma] for lemma in keys])
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size, min_samples, cluster_selection_epsilon)
    clusters, singletons = build_cluster_records(groups, lemma_freqs)

    if namer is not None:
        for cluster in clusters:
            cluster["ollama_concept"] = namer.name_cluster(lang, list(cluster["members"].items()))

    if lookup_concepts:
        for cluster in clusters:
            if concept_rows:
                centroid = _cluster_centroid(cluster["members"], embeddings_by_lemma)
                cluster["nearest_concept"] = nearest_concept(centroid, concept_rows)
            else:
                cluster["nearest_concept"] = None

    return {"clusters": clusters, "singletons": singletons}


async def run(
    input_file: str,
    output_file: str,
    language: str = "all",
    min_cluster_size: int = 2,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
    embed_model: str = "sbert",
    ollama_model: str = "qwen2",
    ollama_enabled: bool = True,
    lookup_concepts: bool = False,
) -> None:
    if lookup_concepts and embed_model != "clip":
        raise ValueError(
            "LOOKUP_CONCEPTS=true requires TEXT_EMBED_MODEL=clip "
            "(DB concept embeddings are CLIP-based, not comparable to sbert centroids)"
        )

    data = load_lemma_source(input_file)
    blocks = resolve_language_blocks(data, language)

    embedder = _get_embedder(embed_model)
    namer = _get_namer(ollama_model) if ollama_enabled else None

    concept_rows: list[tuple[int, str, np.ndarray]] = []
    if lookup_concepts:
        concept_rows = await _load_concept_rows()
        if not concept_rows:
            print("WARNING: LOOKUP_CONCEPTS=true but no concept embeddings found in DB")

    languages_output = {}
    for lang, lemma_freqs in blocks.items():
        languages_output[lang] = _process_language(
            lang, lemma_freqs, embedder, namer, min_cluster_size, min_samples,
            cluster_selection_epsilon, lookup_concepts, concept_rows,
        )

    parameters = {
        "min_cluster_size": min_cluster_size,
        "min_samples": min_samples,
        "cluster_selection_epsilon": cluster_selection_epsilon,
        "embed_model": embed_model,
        "ollama_model": ollama_model,
    }
    write_yaml_output(output_file, parameters, languages_output)
    print(f"Written to {output_file}")


async def main() -> None:
    text_scope = os.getenv("TEXT_SCOPE", "unmatched")
    input_file = os.getenv("BOW_OUTPUT_FILE") if text_scope == "all" else os.getenv("BOW_UNMATCHED_FILE")

    await run(
        input_file=input_file,
        output_file=os.getenv("CLUSTER_OUTPUT_FILE"),
        language=os.getenv("LANGUAGE", "all"),
        min_cluster_size=int(os.getenv("MIN_CLUSTER_SIZE", "2")),
        min_samples=int(os.getenv("MIN_SAMPLES")) if os.getenv("MIN_SAMPLES") else None,
        cluster_selection_epsilon=float(os.getenv("CLUSTER_SELECTION_EPSILON", "0.0")),
        embed_model=os.getenv("TEXT_EMBED_MODEL", "sbert"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2"),
        ollama_enabled=os.getenv("OLLAMA_ENABLED", "true").lower() == "true",
        lookup_concepts=os.getenv("LOOKUP_CONCEPTS", "false").lower() == "true",
    )


if __name__ == "__main__":
    asyncio.run(main())
```

Also add this import at the top of the file, next to `build_clusters_from_embeddings`'s usage:

```python
from batch.utils.clustering import build_clusters_from_embeddings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_build_lemma_clusters.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Run the full non-integration suite to check for regressions**

Run: `.venv311/Scripts/python -m pytest tests -v`
Expected: all tests pass (integration tests under `tests/integration/` are excluded from plain `pytest` per the project's existing convention — confirm they're not collected, or that they fail only due to no live DB being configured, not due to this change).

- [ ] **Step 6: Commit**

```bash
git add batch/build_lemma_clusters.py tests/batch/test_build_lemma_clusters.py
git commit -m "feat(batch): wire up build_lemma_clusters run()/main() orchestration"
```

---

## Self-Review Notes

- **Spec coverage:** input loading (Task 5), per-language HDBSCAN clustering with L2-normalisation (Task 1 + `_process_language` in Task 7), Ollama naming with failure-tolerant fallback (Task 3 + Task 7), optional CLIP-only `LOOKUP_CONCEPTS` with fail-fast on `sbert` (Task 4, Task 6, Task 7), YAML output format matching the spec's example shape (Task 6), all Configuration env vars wired in `main()` (Task 7), all Error Handling table rows covered (missing file, missing language, <2 lemmas, >5000 lemmas, Ollama failure, unknown embed model, flat-dict-as-`en`, no DB concepts, `LOOKUP_CONCEPTS`+`sbert`).
- **Not implemented (explicitly out of scope, per spec Non-Goals):** writing decisions/tags back to the DB or rule files — the YAML `decision:` field workflow described in "Human Review Workflow" is a manual/future step, not part of this batch.
- **Deviation from the spec's `main()` sketch:** the spec's sketch reads `BOW_UNMATCHED_FILE` unconditionally, which contradicts its own Configuration table (`TEXT_SCOPE=all` should read `BOW_OUTPUT_FILE`). Task 7 implements the Configuration table's `TEXT_SCOPE` branching, since that table is the authoritative source and the sketch is explicitly labeled as a sketch.