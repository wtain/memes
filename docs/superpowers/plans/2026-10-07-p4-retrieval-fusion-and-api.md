# P4 Retrieval, Fusion and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the meme-from-text retrieval stage: a pure `retrieval/` package (scoring, fusion, facets, duplicate collapse, cursor, pipeline with cache and parser circuit breaker), the SQL candidate generation, the `POST /api/meme-from-text/search` endpoint with shared schemas and generated types, and the evaluation additions (`fused` retriever, `tune-weights`, pool source) needed to tune the weights and prove the approved thresholds on the gold set.

**Architecture:** Pure modules carry all logic and are unit-tested without a database or Ollama. `repository/meme_retrieval.py` is the read-only SQL side. `retrieval/pipeline.py` composes parser, embedder, repository and scoring; the service hydrates `Meme` items and builds the generated response models. The P6 harness gets a `fused` retriever that runs the same pipeline restricted to the gold corpus.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async + pgvector (0.8.2 in the test database), FastAPI + the generated Pydantic models, the P3 `parse` package, the P6 `evaluation` package, `urllib` for the Ollama embedding call, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-07-p4-retrieval-fusion-and-api-design.md` (read it first). Inherited: ADR-003, P1 (`queryprofile` schema, `similarity.json`), P2 (profile tables), P3 (`parse_query`, `QueryParser`, handoff notes), P6 (gold set, harness, approved thresholds, re-based 2026-10-07).

## Global Constraints

- Python for every command: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe` (a worktree has no venv). Never `pip install` anything; the backend gets **no new dependency**. Run each pytest root as its own command: `tests/retrieval`, `tests/parse`, `tests/evaluation`, `tests/taxonomy`, `tests/ai`, `batch/tests`, `tests/integration`, `Backend` (from `Backend/`), `tests/docker`. Never combine roots (CLAUDE.md gotcha).
- `tests/integration/` needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line. **Subagents use only that URL**; they never read `environments/.env.*` and never get another database URL (CLAUDE.md). Task 9 (live) is controller-only. **Work only inside your own worktree**: earlier subagents wrote into the main checkout by mistake; use paths under your worktree only and check `git status` there before committing.
- **Pure rule:** every module in `retrieval/` may import only the standard library, `taxonomy.*`, `parse.*` and `retrieval.*` (an AST guard test enforces it): no `config.*`, `Storage.*`, `repository.*`, `sqlalchemy`, `evaluation.*`. `retrieval/__init__.py` stays empty. SQL lives only in `repository/meme_retrieval.py` (plain Python values out, never Pydantic models, never `commit()`; sessions come from the caller).
- **The user's text is never logged**, at any level, on any path (only the structured line and exception TYPE names). Query text goes only to the local Ollama and the local database (NFR-2).
- **The endpoint never fails because a model failed:** parse failure, embedding failure and both are degraded results (spec decision 7); only a malformed request is a 4xx (422). A database error is an ordinary 500.
- Axis scores are in 0..1. Fusion: `score = sum over present axes of (w_a / sum of w over present axes) x s_a`; an axis absent from the query is not scored; a meme with no value on a queried axis (or no profile, or no situation embedding) scores `neutral_score` (default 0.3) on it, flagged `neutral`; ties are ordered by image id; scores are rounded to 6 decimals.
- Settings keys (`environments/settings.yaml`, under `search.meme_from_text`; a missing key is a startup error): `weights.situation 0.40, weights.emotion 0.15, weights.stance 0.10, weights.topic 0.15, weights.tone 0.10, weights.lexical 0.10`, `candidates.situation_k 300`, `candidates.axis_k 300`, `candidates.lexical_k 100`, `candidates.total_cap 600`, `neutral_score 0.3`, `situation.floor 0.45`, `situation.ceil 0.85`, `similarity.use_neighbours true`, `lexical.enabled true`, `cache_ttl_seconds 120`, `embed_timeout_seconds 2.0`, `warm_interval_minutes 20`, `parser_breaker.failures 3`, `parser_breaker.open_seconds 30`, `page.default_limit 20`, `page.max_limit 50`. The embedding model comes from `taxonomy.embedding_model`, the Ollama URL from `parse.ollama_url` (`OLLAMA_HOST` wins).
- Hard filters (SQL, before ranking): axis filters are OR within an axis and AND across axes (`EXISTS` on `meme_profile_values`); tag filters keep the existing `/api/images` semantics (an image matches when any listed `(key, value)` pair matches). Eligible set for the axis path: `images.status = 'active'`, not flagged, a `meme_profiles` row exists, optional `restrict_ids`. Images without a profile are reachable only through the lexical source (never when an axis filter is present) and are labelled `axisPath: false`.
- Acceptance numbers (approved 2026-10-07, spec decision 10): overall nDCG@10 at least 0.50 and hit@10 at least 0.95; every language at least 0.85 of English and at least 0.30; no query class below the best baseline's score on that class (difficulty ambiguous 0.462, easy 0.530, hard 0.417; length short 0.591, medium 0.378, long 0.525); retrieval alone p95 under 300 ms.
- Commit messages end with these two trailer lines, exactly (Claude Sonnet 5.5, never another model name):
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01WUvgSm19vfxX68CBg4XvxY`.
- Windows: use the Edit/Write tools for repo files (several are CRLF; preserve each file's line endings); never round-trip repo text through PowerShell `Get/Set-Content`. Set `PYTHONIOENCODING=utf-8` for scripts that print non-ASCII.

## File Structure

| Path | Responsibility |
|---|---|
| `retrieval/__init__.py` (empty) | Package marker |
| `retrieval/config.py` | `RetrievalConfig`, `load_config(get, environ=None)`, `AXES`, `WEIGHT_KEYS` |
| `retrieval/model.py` | Dataclasses: `QueryView`, `Filters`, `Candidate`, `Match`, `AxisExplanation`, `ScoredCandidate` |
| `retrieval/similarity.py` | `SimilarityIndex`, `load_similarity` (reads `batch/data/taxonomy/similarity.json`) |
| `retrieval/scoring.py` | `situation_score`, `axis_score` |
| `retrieval/fusion.py` | `fuse`, `rank_candidates`, `explanation_dict` |
| `retrieval/facets.py` | `RankedItem`, `collapse_clusters`, `facet_counts` |
| `retrieval/candidates.py` | `merge_ranked` (round-robin union with a cap) |
| `retrieval/cursor.py` | `fingerprint`, `encode_cursor`, `decode_cursor`, `paginate` |
| `retrieval/embed.py` | `OllamaEmbedClient` over `urllib`, typed errors |
| `retrieval/pipeline.py` | `SearchRequest`, `SearchResult`, `Prepared`, `MemeFromTextPipeline`, `InvalidProfile` |
| `retrieval/tools.py` | Tool schemas and `search_memes_from_text`, `score_candidates`, `explain_results` |
| `repository/meme_retrieval.py` | `MemeRetrievalRepository` (SQL side) |
| `repository/ocr_lemmas.py` (modify) | `lexical_or_scores` extracted from the evaluation retriever |
| `Backend/app/services/meme_from_text_service.py`, `Backend/app/services/meme_from_text_pipeline.py`, `Backend/app/api/meme_from_text.py` | Service, process-wide pipeline factory, router |
| `shared/schemas/memefromtext*.schema.json`, `axisexplanation.schema.json`, `all.schema.json` + generated trees | Public schemas |
| `evaluation/retrievers/fused.py`, `evaluation/tuning.py`, `evaluation/__main__.py` (modify) | `fused` retriever, weight tuning, `tune-weights`, pool/run options |
| `tests/retrieval/`, `tests/integration/test_meme_retrieval_repository.py`, `tests/integration/test_lexical_or_scores.py`, `Backend/tests/test_meme_from_text_*.py`, `tests/load/synthetic_load_check.py` | Tests and the load check |

---

### Task 1: Pure retrieval core

**Files:**
- Create: `retrieval/__init__.py` (empty, `touch`), `retrieval/config.py`, `retrieval/model.py`, `retrieval/similarity.py`, `retrieval/scoring.py`, `retrieval/fusion.py`, `retrieval/facets.py`, `retrieval/candidates.py`, `retrieval/cursor.py`
- Create: `tests/retrieval/__init__.py` (empty), `tests/retrieval/helpers.py`, `test_config.py`, `test_similarity.py`, `test_scoring.py`, `test_fusion.py`, `test_facets.py`, `test_candidates.py`, `test_cursor.py`, `test_pure_imports.py`

**Interfaces:**
- Produces: `retrieval.config.RetrievalConfig` (frozen dataclass: `weights: Mapping[str, float]`, `situation_k`, `axis_k`, `lexical_k`, `total_cap`, `neutral_score`, `situation_floor`, `situation_ceil`, `use_neighbours`, `lexical_enabled`, `cache_ttl_seconds`, `embed_timeout_seconds`, `warm_interval_minutes`, `breaker_failures`, `breaker_open_seconds`, `default_limit`, `max_limit`, `embedding_model`, `ollama_url`; method `with_weights(weights)`), `load_config(get, environ=None) -> RetrievalConfig` (raises `ValueError` for missing or invalid keys), `AXES = ("emotion", "stance", "topic", "tone")`, `WEIGHT_KEYS`. `retrieval.model`: `AxisValues = tuple[tuple[str, float], ...]`; `QueryView(situation, axes, language, degraded)` with `from_profile(profile)` and `present_axes(*, lexical_active)`; `Filters(axes, tags)` with `to_dict()`, `is_empty()`, `has_axis_filters()`; `Candidate(image_id: str, situation_text, use_when, cosine: float | None, values: Mapping[str, AxisValues], lexical: float, axis_path: bool, cluster_id: int | None)`; `Match(query_key, meme_key, similarity, meme_confidence)`; `AxisExplanation(axis, score, weight, contribution, neutral, query_values, matched)`; `ScoredCandidate(candidate, score, axes)`. `retrieval.similarity.SimilarityIndex(axes)` with `neighbours(axis, key) -> Mapping[str, float]`, `similarity(axis, a, b) -> float`, `neighbours_for(axes) -> dict`; `load_similarity(path=None)`. `retrieval.scoring.situation_score(cosine, floor, ceil, neutral) -> (float, bool)`, `axis_score(axis, query_values, meme_values, similarity, neutral, use_neighbours) -> (float, bool, tuple[Match, ...])`. `retrieval.fusion.fuse(candidate, query, config, similarity, *, lexical_active) -> ScoredCandidate`, `rank_candidates(scored) -> list`, `explanation_dict(scored) -> dict`. `retrieval.facets.RankedItem(scored, cluster_size)`, `collapse_clusters(ranked) -> list[RankedItem]`, `facet_counts(items) -> dict[str, dict[str, int]]`. `retrieval.candidates.merge_ranked(sources, cap) -> list`. `retrieval.cursor.fingerprint(payload) -> str`, `encode_cursor(score, image_id, fp) -> str`, `decode_cursor(cursor, fp) -> tuple[float, str] | None`, `paginate(items, cursor, fp, limit) -> (page, next_cursor, has_next)`.

- [ ] **Step 1: Write the test helpers and the failing tests**

Create `tests/retrieval/__init__.py` (empty) and `tests/retrieval/helpers.py`:

```python
from retrieval.config import RetrievalConfig
from retrieval.model import Candidate
from retrieval.similarity import SimilarityIndex

WEIGHTS = {"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10, "lexical": 0.10}


def make_config(**overrides) -> RetrievalConfig:
    values = dict(
        weights=dict(WEIGHTS), situation_k=300, axis_k=300, lexical_k=100, total_cap=600, neutral_score=0.3,
        situation_floor=0.45, situation_ceil=0.85, use_neighbours=True, lexical_enabled=True,
        cache_ttl_seconds=120.0, embed_timeout_seconds=2.0, warm_interval_minutes=20.0, breaker_failures=3,
        breaker_open_seconds=30.0, default_limit=20, max_limit=50, embedding_model="bge-large",
        ollama_url="http://127.0.0.1:11434")
    values.update(overrides)
    return RetrievalConfig(**values)


def make_similarity() -> SimilarityIndex:
    return SimilarityIndex({
        "emotion": {"anger": {"annoyance": 0.8}, "annoyance": {"anger": 0.8}},
        "stance": {"celebrate": {"congratulate": 0.8}, "congratulate": {"celebrate": 0.8}},
    })


def make_candidate(image_id="a", *, cosine=0.85, values=None, lexical=0.0, axis_path=True, cluster_id=None,
                   situation_text="a situation", use_when="Use when it fits.") -> Candidate:
    return Candidate(image_id=image_id, situation_text=situation_text, use_when=use_when, cosine=cosine,
                     values=values or {}, lexical=lexical, axis_path=axis_path, cluster_id=cluster_id)
```

Create `tests/retrieval/test_config.py`:

```python
import pytest

from retrieval.config import AXES, WEIGHT_KEYS, RetrievalConfig, load_config

VALUES = {
    "search.meme_from_text.weights.situation": 0.4, "search.meme_from_text.weights.emotion": 0.15,
    "search.meme_from_text.weights.stance": 0.1, "search.meme_from_text.weights.topic": 0.15,
    "search.meme_from_text.weights.tone": 0.1, "search.meme_from_text.weights.lexical": 0.1,
    "search.meme_from_text.candidates.situation_k": 300, "search.meme_from_text.candidates.axis_k": 300,
    "search.meme_from_text.candidates.lexical_k": 100, "search.meme_from_text.candidates.total_cap": 600,
    "search.meme_from_text.neutral_score": 0.3, "search.meme_from_text.situation.floor": 0.45,
    "search.meme_from_text.situation.ceil": 0.85, "search.meme_from_text.similarity.use_neighbours": True,
    "search.meme_from_text.lexical.enabled": True, "search.meme_from_text.cache_ttl_seconds": 120,
    "search.meme_from_text.embed_timeout_seconds": 2.0, "search.meme_from_text.warm_interval_minutes": 20,
    "search.meme_from_text.parser_breaker.failures": 3, "search.meme_from_text.parser_breaker.open_seconds": 30,
    "search.meme_from_text.page.default_limit": 20, "search.meme_from_text.page.max_limit": 50,
    "taxonomy.embedding_model": "bge-large", "parse.ollama_url": "http://127.0.0.1:11434",
}


def _get(values):
    return lambda key, default=None: values.get(key, default)


def test_load_config_reads_every_key():
    config = load_config(_get(VALUES), environ={})

    assert isinstance(config, RetrievalConfig)
    assert config.weights == {"situation": 0.4, "emotion": 0.15, "stance": 0.1, "topic": 0.15, "tone": 0.1,
                              "lexical": 0.1}
    assert (config.situation_k, config.axis_k, config.lexical_k, config.total_cap) == (300, 300, 100, 600)
    assert (config.neutral_score, config.situation_floor, config.situation_ceil) == (0.3, 0.45, 0.85)
    assert config.use_neighbours is True and config.lexical_enabled is True
    assert (config.cache_ttl_seconds, config.embed_timeout_seconds, config.warm_interval_minutes) == (120.0, 2.0, 20.0)
    assert (config.breaker_failures, config.breaker_open_seconds) == (3, 30.0)
    assert (config.default_limit, config.max_limit) == (20, 50)
    assert config.embedding_model == "bge-large" and config.ollama_url == "http://127.0.0.1:11434"


def test_constants():
    assert AXES == ("emotion", "stance", "topic", "tone")
    assert WEIGHT_KEYS == ("situation", "emotion", "stance", "topic", "tone", "lexical")


def test_ollama_host_environment_wins():
    config = load_config(_get(VALUES), environ={"OLLAMA_HOST": "gpu-box:9999"})
    assert config.ollama_url == "http://gpu-box:9999"


def test_a_missing_key_is_a_startup_error_naming_the_key():
    values = {k: v for k, v in VALUES.items() if k != "search.meme_from_text.weights.stance"}
    with pytest.raises(ValueError, match="weights.stance"):
        load_config(_get(values), environ={})


@pytest.mark.parametrize("key, bad, fragment", [
    ("search.meme_from_text.weights.tone", -0.1, "weights.tone"),
    ("search.meme_from_text.weights.tone", "x", "weights.tone"),
    ("search.meme_from_text.neutral_score", 1.5, "neutral_score"),
    ("search.meme_from_text.situation.ceil", 0.40, "floor"),
    ("search.meme_from_text.candidates.total_cap", 0, "total_cap"),
    ("search.meme_from_text.candidates.axis_k", 2.5, "axis_k"),
    ("search.meme_from_text.similarity.use_neighbours", "yes", "use_neighbours"),
    ("search.meme_from_text.page.default_limit", 100, "default_limit"),
    ("search.meme_from_text.embed_timeout_seconds", 0, "embed_timeout_seconds"),
    ("search.meme_from_text.parser_breaker.failures", 0, "failures"),
])
def test_invalid_values_are_rejected(key, bad, fragment):
    with pytest.raises(ValueError, match=fragment):
        load_config(_get({**VALUES, key: bad}), environ={})


def test_all_weights_zero_is_rejected():
    values = {**VALUES, **{f"search.meme_from_text.weights.{name}": 0 for name in WEIGHT_KEYS}}
    with pytest.raises(ValueError, match="weights"):
        load_config(_get(values), environ={})


def test_with_weights_replaces_only_the_weights():
    config = load_config(_get(VALUES), environ={})
    changed = config.with_weights({**config.weights, "situation": 0.9})
    assert changed.weights["situation"] == 0.9 and changed.neutral_score == config.neutral_score
    assert config.weights["situation"] == 0.4
```

Create `tests/retrieval/test_similarity.py`:

```python
import pytest

from retrieval.similarity import SimilarityIndex, load_similarity


def test_the_shipped_artifact_exposes_the_curated_pairs():
    index = load_similarity()

    assert index.neighbours("emotion", "anger")["annoyance"] == pytest.approx(0.8)
    assert index.similarity("stance", "celebrate", "congratulate") == pytest.approx(0.8)
    assert index.similarity("emotion", "joy", "joy") == 1.0
    assert index.similarity("emotion", "joy", "anger") == 0.0
    assert index.neighbours("emotion", "unknown_key") == {}
    assert index.neighbours("not_an_axis", "joy") == {}


def test_neighbours_for_returns_sorted_pairs_for_the_given_query_axes():
    index = SimilarityIndex({"emotion": {"anger": {"annoyance": 0.8, "disgust": 0.7}}})

    result = index.neighbours_for({"emotion": (("anger", 0.9),), "tone": (("sarcastic", 0.6),)})

    assert result == {"emotion": {"anger": (("annoyance", 0.8), ("disgust", 0.7))}, "tone": {"sarcastic": ()}}


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_similarity(tmp_path / "nope.json")


def test_a_custom_file_is_read(tmp_path):
    path = tmp_path / "similarity.json"
    path.write_text('{"axes": {"tone": {"dark": [{"key": "crude", "score": 0.6}]}}}', encoding="utf-8")
    assert load_similarity(path).similarity("tone", "dark", "crude") == pytest.approx(0.6)
```

Create `tests/retrieval/test_scoring.py`:

```python
import pytest

from retrieval.model import Match
from retrieval.scoring import axis_score, situation_score
from tests.retrieval.helpers import make_similarity

SIM = make_similarity()


def test_situation_score_rescales_between_floor_and_ceiling():
    assert situation_score(0.65, 0.45, 0.85, 0.3) == (pytest.approx(0.5), False)
    assert situation_score(0.30, 0.45, 0.85, 0.3) == (0.0, False)
    assert situation_score(0.95, 0.45, 0.85, 0.3) == (1.0, False)


def test_situation_score_is_neutral_without_an_embedding():
    assert situation_score(None, 0.45, 0.85, 0.3) == (0.3, True)


def test_exact_key_scores_the_meme_confidence():
    score, neutral, matched = axis_score("emotion", (("anger", 0.9),), (("anger", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx(0.6) and neutral is False
    assert matched == (Match("anger", "anger", 1.0, 0.6),)


def test_a_curated_neighbour_scores_similarity_times_confidence():
    score, neutral, matched = axis_score("emotion", (("anger", 0.9),), (("annoyance", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx(0.8 * 0.6) and neutral is False
    assert matched == (Match("anger", "annoyance", 0.8, 0.6),)


def test_neighbours_can_be_switched_off():
    score, _neutral, matched = axis_score("emotion", (("anger", 0.9),), (("annoyance", 0.6),), SIM, 0.3, False)
    assert score == 0.0 and matched == ()


def test_query_values_are_weighted_by_their_confidence():
    score, _n, matched = axis_score("emotion", (("anger", 0.9), ("joy", 0.3)), (("anger", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx((0.9 * 0.6 + 0.3 * 0.0) / 1.2)
    assert [m.query_key for m in matched] == ["anger"]


def test_the_best_meme_value_wins_per_query_value():
    score, _n, matched = axis_score(
        "emotion", (("anger", 0.9),), (("annoyance", 0.9), ("anger", 0.3)), SIM, 0.3, True)

    assert score == pytest.approx(max(0.8 * 0.9, 1.0 * 0.3)) and matched[0].meme_key == "annoyance"


def test_a_meme_without_values_on_the_axis_is_neutral_not_zero():
    assert axis_score("emotion", (("anger", 0.9),), (), SIM, 0.3, True) == (0.3, True, ())


def test_zero_total_query_confidence_falls_back_to_equal_weights():
    score, _n, _m = axis_score("emotion", (("anger", 0.0),), (("anger", 0.6),), SIM, 0.3, True)
    assert score == pytest.approx(0.6)
```

Create `tests/retrieval/test_fusion.py`:

```python
import pytest

from retrieval.fusion import explanation_dict, fuse, rank_candidates
from retrieval.model import QueryView
from tests.retrieval.helpers import make_candidate, make_config, make_similarity

SIM = make_similarity()
CONFIG = make_config()


def _view(situation="x", **axes):
    return QueryView(situation=situation, axes={a: tuple(v) for a, v in axes.items()}, language="en", degraded=False)


def test_fusion_renormalises_the_weights_over_the_present_axes():
    candidate = make_candidate("a", cosine=0.85, values={"emotion": (("anger", 0.9),)})

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    assert scored.score == pytest.approx((0.4 * 1.0 + 0.15 * 0.9) / 0.55, abs=1e-5)
    by_axis = {a.axis: a for a in scored.axes}
    assert set(by_axis) == {"situation", "emotion"}
    assert by_axis["situation"].weight == pytest.approx(0.4 / 0.55, abs=1e-5)
    assert by_axis["emotion"].contribution == pytest.approx(0.15 / 0.55 * 0.9, abs=1e-5)
    assert sum(a.contribution for a in scored.axes) == pytest.approx(scored.score, abs=1e-5)


def test_an_axis_absent_from_the_query_contributes_nothing():
    candidate = make_candidate("a", cosine=0.85, values={"tone": (("sarcastic", 0.9),)})

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    assert "tone" not in {a.axis for a in scored.axes}


def test_a_meme_with_no_value_on_a_queried_axis_scores_neutral():
    candidate = make_candidate("a", cosine=0.85, values={})

    scored = fuse(candidate, _view(stance=[("mock", 0.9)]), CONFIG, SIM, lexical_active=False)

    stance = next(a for a in scored.axes if a.axis == "stance")
    assert stance.neutral is True and stance.score == pytest.approx(0.3) and stance.matched == ()


def test_a_meme_without_a_profile_is_neutral_on_every_axis_but_keeps_its_lexical_score():
    candidate = make_candidate("a", cosine=None, values={}, lexical=0.8, axis_path=False)

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=True)

    by_axis = {a.axis: a for a in scored.axes}
    assert by_axis["situation"].neutral and by_axis["emotion"].neutral
    assert by_axis["lexical"].score == pytest.approx(0.8) and by_axis["lexical"].neutral is False


def test_lexical_is_an_axis_only_when_active():
    candidate = make_candidate("a", lexical=0.5)
    without = fuse(candidate, _view(), CONFIG, SIM, lexical_active=False)
    with_lexical = fuse(candidate, _view(), CONFIG, SIM, lexical_active=True)

    assert {a.axis for a in without.axes} == {"situation"}
    assert {a.axis for a in with_lexical.axes} == {"situation", "lexical"}


def test_a_query_with_nothing_to_score_gives_zero():
    scored = fuse(make_candidate("a"), _view(situation=None), CONFIG, SIM, lexical_active=False)
    assert scored.score == 0.0 and scored.axes == ()


def test_ranking_is_by_score_then_image_id():
    view = _view()
    scored = [fuse(make_candidate(i, cosine=c), view, CONFIG, SIM, lexical_active=False)
              for i, c in (("b", 0.85), ("a", 0.85), ("c", 0.65))]

    assert [s.candidate.image_id for s in rank_candidates(scored)] == ["a", "b", "c"]


def test_explanation_dict_has_the_public_shape():
    candidate = make_candidate("a", cosine=0.85, values={"emotion": (("annoyance", 0.6),)},
                               situation_text="Boss moved the deadline.", use_when="Use when plans change.")
    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    explanation = explanation_dict(scored)

    assert explanation["situationText"] == "Boss moved the deadline." and explanation["useWhen"] == "Use when plans change."
    emotion = next(a for a in explanation["axes"] if a["axis"] == "emotion")
    assert set(emotion) == {"axis", "score", "weight", "contribution", "neutral", "queryValues", "matched"}
    assert emotion["queryValues"] == ["anger"]
    assert emotion["matched"] == [{"queryKey": "anger", "memeKey": "annoyance", "similarity": 0.8, "memeConfidence": 0.6}]


def test_query_view_from_a_queryprofile_dict():
    profile = {"situation": " A friend got promoted. ", "language": "ru", "degraded": False,
               "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}], "stance": [], "topic": [], "tone": []}

    view = QueryView.from_profile(profile)

    assert view.situation == "A friend got promoted." and view.language == "ru"
    assert view.axes == {"emotion": (("joy", 0.9),)}
    assert view.present_axes(lexical_active=True) == ("situation", "emotion", "lexical")
    assert QueryView.from_profile({"situation": None, "emotion": []}).present_axes(lexical_active=False) == ()
```

Create `tests/retrieval/test_facets.py`:

```python
from retrieval.facets import collapse_clusters, facet_counts
from retrieval.fusion import rank_candidates
from retrieval.model import ScoredCandidate
from tests.retrieval.helpers import make_candidate


def _scored(image_id, score, cluster_id=None, values=None):
    return ScoredCandidate(make_candidate(image_id, cluster_id=cluster_id, values=values or {}), score, ())


def test_each_cluster_collapses_to_its_best_member_with_the_candidate_count():
    ranked = rank_candidates([_scored("a", 0.9, 1), _scored("b", 0.8, None), _scored("c", 0.7, 1), _scored("d", 0.6, 2)])

    items = collapse_clusters(ranked)

    assert [(i.scored.candidate.image_id, i.cluster_size) for i in items] == [("a", 2), ("b", None), ("d", 1)]


def test_clusters_with_equal_scores_keep_the_lowest_image_id():
    ranked = rank_candidates([_scored("z", 0.9, 5), _scored("m", 0.9, 5)])
    assert [i.scored.candidate.image_id for i in collapse_clusters(ranked)] == ["m"]


def test_facet_counts_count_each_item_once_per_value_over_the_collapsed_set():
    ranked = rank_candidates([
        _scored("a", 0.9, 1, {"emotion": (("anger", 0.9), ("annoyance", 0.6)), "tone": (("sarcastic", 0.9),)}),
        _scored("b", 0.8, 1, {"emotion": (("anger", 0.9),)}),                 # collapsed away
        _scored("c", 0.7, None, {"emotion": (("anger", 0.3),), "tone": (("dark", 0.6),)}),
    ])

    counts = facet_counts(collapse_clusters(ranked))

    assert counts["emotion"] == {"anger": 2, "annoyance": 1}
    assert counts["tone"] == {"sarcastic": 1, "dark": 1}
    assert counts["stance"] == {} and counts["topic"] == {}
```

Create `tests/retrieval/test_candidates.py`:

```python
from retrieval.candidates import merge_ranked


def test_sources_are_merged_round_robin_by_rank_without_duplicates():
    assert merge_ranked([["a", "b", "c"], ["b", "d"], ["e"]], 10) == ["a", "b", "e", "d", "c"]


def test_the_cap_applies_after_deduplication():
    assert merge_ranked([["a", "b", "c"], ["d", "e"]], 3) == ["a", "d", "b"]


def test_empty_sources_and_zero_cap():
    assert merge_ranked([], 5) == [] and merge_ranked([[], []], 5) == [] and merge_ranked([["a"]], 0) == []
```

Create `tests/retrieval/test_cursor.py`:

```python
from retrieval.cursor import decode_cursor, encode_cursor, fingerprint, paginate
from retrieval.facets import RankedItem
from retrieval.model import ScoredCandidate
from tests.retrieval.helpers import make_candidate


def _items(*pairs):
    return [RankedItem(ScoredCandidate(make_candidate(i), s, ()), None) for i, s in pairs]


def test_fingerprint_is_stable_and_order_independent():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_cursor_round_trip_and_fingerprint_check():
    cursor = encode_cursor(0.912345678, "img-1", "fp1")

    assert decode_cursor(cursor, "fp1") == (0.912346, "img-1")
    assert decode_cursor(cursor, "other") is None
    assert decode_cursor(None, "fp1") is None
    assert decode_cursor("not a cursor", "fp1") is None


def test_pages_continue_after_the_last_returned_item():
    items = _items(("a", 0.9), ("b", 0.8), ("c", 0.8), ("d", 0.5), ("e", 0.1))

    page1, next1, more1 = paginate(items, None, "fp", 2)
    page2, next2, more2 = paginate(items, next1, "fp", 2)
    page3, next3, more3 = paginate(items, next2, "fp", 2)

    assert [i.scored.candidate.image_id for i in page1] == ["a", "b"] and more1 is True
    assert [i.scored.candidate.image_id for i in page2] == ["c", "d"] and more2 is True
    assert [i.scored.candidate.image_id for i in page3] == ["e"] and more3 is False and next3 is None


def test_a_cursor_for_another_request_restarts_at_the_first_page():
    items = _items(("a", 0.9), ("b", 0.8))
    _page, cursor, _more = paginate(items, None, "fp", 1)

    page, _next, _more = paginate(items, cursor, "different", 1)

    assert [i.scored.candidate.image_id for i in page] == ["a"]


def test_an_empty_list_has_no_next_page():
    assert paginate([], None, "fp", 5) == ([], None, False)
```

Create `tests/retrieval/test_pure_imports.py`:

```python
import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "retrieval"
ALLOWED = set(sys.stdlib_module_names) | {"taxonomy", "parse", "retrieval"}


def _roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_every_retrieval_module_imports_only_stdlib_taxonomy_parse_and_retrieval():
    modules = sorted(PACKAGE.glob("*.py"))
    assert modules
    for path in modules:
        offenders = _roots(path) - ALLOWED
        assert not offenders, f"retrieval/{path.name} imports {sorted(offenders)}"


def test_the_package_init_is_empty():
    assert (PACKAGE / "__init__.py").read_text(encoding="utf-8").strip() == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `mkdir -p retrieval tests/retrieval && touch retrieval/__init__.py tests/retrieval/__init__.py` then `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`
Expected: collection ERRORS (`ModuleNotFoundError: No module named 'retrieval.config'` and the others).

- [ ] **Step 3: Write `retrieval/config.py`**

```python
"""Retrieval settings (P4 spec, decision 12). Pure: standard library, parse.config and nothing else."""
import dataclasses
import math
from dataclasses import dataclass
from typing import Mapping

from parse.config import resolve_ollama_url

AXES = ("emotion", "stance", "topic", "tone")
WEIGHT_KEYS = ("situation",) + AXES + ("lexical",)
PREFIX = "search.meme_from_text."

# attribute, key under PREFIX, kind
_SPEC = (
    ("situation_k", "candidates.situation_k", int),
    ("axis_k", "candidates.axis_k", int),
    ("lexical_k", "candidates.lexical_k", int),
    ("total_cap", "candidates.total_cap", int),
    ("neutral_score", "neutral_score", float),
    ("situation_floor", "situation.floor", float),
    ("situation_ceil", "situation.ceil", float),
    ("use_neighbours", "similarity.use_neighbours", bool),
    ("lexical_enabled", "lexical.enabled", bool),
    ("cache_ttl_seconds", "cache_ttl_seconds", float),
    ("embed_timeout_seconds", "embed_timeout_seconds", float),
    ("warm_interval_minutes", "warm_interval_minutes", float),
    ("breaker_failures", "parser_breaker.failures", int),
    ("breaker_open_seconds", "parser_breaker.open_seconds", float),
    ("default_limit", "page.default_limit", int),
    ("max_limit", "page.max_limit", int),
)


@dataclass(frozen=True)
class RetrievalConfig:
    weights: Mapping[str, float]
    situation_k: int
    axis_k: int
    lexical_k: int
    total_cap: int
    neutral_score: float
    situation_floor: float
    situation_ceil: float
    use_neighbours: bool
    lexical_enabled: bool
    cache_ttl_seconds: float
    embed_timeout_seconds: float
    warm_interval_minutes: float
    breaker_failures: int
    breaker_open_seconds: float
    default_limit: int
    max_limit: int
    embedding_model: str
    ollama_url: str

    def with_weights(self, weights: Mapping[str, float]) -> "RetrievalConfig":
        return dataclasses.replace(self, weights=dict(weights))


def _coerce(kind, raw):
    if kind is bool:
        if not isinstance(raw, bool):
            raise ValueError("not a boolean")
        return raw
    if isinstance(raw, bool):
        raise ValueError("a boolean is not a number")
    if kind is int and isinstance(raw, float) and not raw.is_integer():
        raise ValueError("not an integer")
    value = kind(raw)
    if kind is float and not math.isfinite(value):
        raise ValueError("not finite")
    return value


def _problems(values: dict, weights: dict) -> list[str]:
    problems = []
    for name, weight in weights.items():
        if weight < 0:
            problems.append(f"weights.{name} must be >= 0")
    if sum(weights.values()) <= 0:
        problems.append("weights must not all be zero")
    for attribute in ("situation_k", "axis_k", "lexical_k", "total_cap", "breaker_failures", "default_limit",
                      "max_limit"):
        if values[attribute] < 1:
            problems.append(f"{attribute} must be >= 1")
    if not 0.0 <= values["neutral_score"] <= 1.0:
        problems.append("neutral_score must be between 0 and 1")
    if not values["situation_floor"] < values["situation_ceil"]:
        problems.append("situation.floor must be below situation.ceil")
    if values["embed_timeout_seconds"] <= 0:
        problems.append("embed_timeout_seconds must be > 0")
    if values["warm_interval_minutes"] <= 0:
        problems.append("warm_interval_minutes must be > 0")
    if values["cache_ttl_seconds"] < 0 or values["breaker_open_seconds"] < 0:
        problems.append("cache_ttl_seconds and breaker_open_seconds must be >= 0")
    if values["default_limit"] > values["max_limit"]:
        problems.append("page.default_limit must not exceed page.max_limit")
    return problems


def load_config(get, environ=None) -> RetrievalConfig:
    """`get` is `settings.get` (dotted key, optional default). A missing or invalid key is a startup error."""
    missing, invalid, values, weights = [], [], {}, {}
    for attribute, key, kind in _SPEC:
        raw = get(PREFIX + key)
        if raw is None:
            missing.append(PREFIX + key)
            continue
        try:
            values[attribute] = _coerce(kind, raw)
        except (TypeError, ValueError):
            invalid.append(f"{PREFIX + key}={raw!r}")
    for name in WEIGHT_KEYS:
        key = f"{PREFIX}weights.{name}"
        raw = get(key)
        if raw is None:
            missing.append(key)
            continue
        try:
            weights[name] = _coerce(float, raw)
        except (TypeError, ValueError):
            invalid.append(f"{key}={raw!r}")
    model = get("taxonomy.embedding_model")
    url = get("parse.ollama_url")
    if not model:
        missing.append("taxonomy.embedding_model")
    if not url:
        missing.append("parse.ollama_url")
    if missing:
        raise ValueError("search.meme_from_text is missing keys: " + ", ".join(missing))
    if invalid:
        raise ValueError("search.meme_from_text has invalid values: " + ", ".join(invalid))
    problems = _problems(values, weights)
    if problems:
        raise ValueError("search.meme_from_text is invalid: " + "; ".join(problems))
    return RetrievalConfig(weights=weights, embedding_model=str(model),
                           ollama_url=resolve_ollama_url(str(url), environ), **values)
```

- [ ] **Step 4: Write `retrieval/model.py`, `retrieval/similarity.py`, `retrieval/scoring.py`**

`retrieval/model.py`:

```python
"""Plain data types of the retrieval stage (P4 spec, decisions 3 to 5). Pure."""
from dataclasses import dataclass, field
from typing import Mapping

from retrieval.config import AXES

AxisValues = tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class QueryView:
    """What the scorer needs from a query profile: the situation phrase and the values per axis that has any."""
    situation: str | None
    axes: Mapping[str, AxisValues]
    language: str
    degraded: bool

    @classmethod
    def from_profile(cls, profile: Mapping) -> "QueryView":
        axes = {}
        for axis in AXES:
            values = tuple((value["key"], float(value.get("confidence", 1.0))) for value in profile.get(axis) or [])
            if values:
                axes[axis] = values
        situation = (profile.get("situation") or "").strip() or None
        return cls(situation=situation, axes=axes, language=profile.get("language", "other"),
                   degraded=bool(profile.get("degraded")))

    def present_axes(self, *, lexical_active: bool) -> tuple[str, ...]:
        present = []
        if self.situation:
            present.append("situation")
        present.extend(axis for axis in AXES if axis in self.axes)
        if lexical_active:
            present.append("lexical")
        return tuple(present)


@dataclass(frozen=True)
class Filters:
    axes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(self.axes.values()) and not any(self.tags.values())

    def has_axis_filters(self) -> bool:
        return any(self.axes.values())

    def to_dict(self) -> dict:
        return {"axes": {axis: sorted(keys) for axis, keys in sorted(self.axes.items()) if keys},
                "tags": {key: sorted(values) for key, values in sorted(self.tags.items()) if values}}


@dataclass(frozen=True)
class Candidate:
    image_id: str
    situation_text: str | None
    use_when: str | None
    cosine: float | None                    # similarity of the situation vectors, None without an embedding
    values: Mapping[str, AxisValues]        # axis -> ((key, confidence), ...) of the meme profile
    lexical: float                          # 0..1, 0 when the lexical source did not return it
    axis_path: bool                         # False for a lexical-only image without a profile
    cluster_id: int | None


@dataclass(frozen=True)
class Match:
    query_key: str
    meme_key: str
    similarity: float
    meme_confidence: float


@dataclass(frozen=True)
class AxisExplanation:
    axis: str
    score: float
    weight: float
    contribution: float
    neutral: bool
    query_values: tuple[str, ...]
    matched: tuple[Match, ...]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    axes: tuple[AxisExplanation, ...]
```

`retrieval/similarity.py`:

```python
"""Vocabulary similarity for soft matching on the controlled axes (P1 spec, decision 8). Pure.

Reads the generated batch/data/taxonomy/similarity.json (25 curated near-synonym pairs)."""
import json
from pathlib import Path
from typing import Mapping

from taxonomy.loader import DEFAULT_DIR


class SimilarityIndex:
    def __init__(self, axes: Mapping[str, Mapping[str, Mapping[str, float]]]):
        self._axes = axes

    def neighbours(self, axis: str, key: str) -> Mapping[str, float]:
        return self._axes.get(axis, {}).get(key, {})

    def similarity(self, axis: str, a: str, b: str) -> float:
        if a == b:
            return 1.0
        return float(self.neighbours(axis, a).get(b, 0.0))

    def neighbours_for(self, query_axes: Mapping[str, tuple]) -> dict[str, dict[str, tuple[tuple[str, float], ...]]]:
        """For the repository's axis source: axis -> query key -> ((neighbour key, score), ...), best first."""
        result: dict[str, dict[str, tuple[tuple[str, float], ...]]] = {}
        for axis, values in query_axes.items():
            result[axis] = {
                key: tuple(sorted(self.neighbours(axis, key).items(), key=lambda pair: (-pair[1], pair[0])))
                for key, _confidence in values
            }
        return result


def load_similarity(path: Path | None = None) -> SimilarityIndex:
    target = Path(path) if path is not None else DEFAULT_DIR / "similarity.json"
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    axes = {
        axis: {key: {item["key"]: float(item["score"]) for item in neighbours} for key, neighbours in keys.items()}
        for axis, keys in data.get("axes", {}).items()
    }
    return SimilarityIndex(axes)
```

`retrieval/scoring.py`:

```python
"""Per-axis scores in 0..1 (P4 spec, decision 4). Pure."""
from typing import Sequence

from retrieval.model import AxisValues, Match
from retrieval.similarity import SimilarityIndex


def situation_score(cosine: float | None, floor: float, ceil: float, neutral: float) -> tuple[float, bool]:
    """Cosine similarity rescaled between a floor and a ceiling; neutral (flagged) without an embedding."""
    if cosine is None:
        return neutral, True
    return min(max((cosine - floor) / (ceil - floor), 0.0), 1.0), False


def axis_score(axis: str, query_values: AxisValues, meme_values: AxisValues, similarity: SimilarityIndex,
               neutral: float, use_neighbours: bool) -> tuple[float, bool, tuple[Match, ...]]:
    """Confidence-weighted match of the query values against the meme values on one controlled axis.

    match(k) = max over meme values m of sim(k, m) x meme confidence (1 for the same key, the curated neighbour
    score for neighbours); score = sum(query confidence x match) / sum(query confidence). A meme with no value
    on the axis scores `neutral` (flagged), not zero (FR-15)."""
    if not meme_values:
        return neutral, True, ()
    total = sum(confidence for _key, confidence in query_values)
    equal_weights = total <= 0
    if equal_weights:
        total = float(len(query_values))
    weighted = 0.0
    matched: list[Match] = []
    for query_key, query_confidence in query_values:
        best, best_match = 0.0, None
        for meme_key, meme_confidence in meme_values:
            sim = 1.0 if meme_key == query_key else (
                similarity.similarity(axis, query_key, meme_key) if use_neighbours else 0.0)
            value = sim * meme_confidence
            if value > best:
                best, best_match = value, Match(query_key, meme_key, round(sim, 6), meme_confidence)
        weighted += (1.0 if equal_weights else query_confidence) * best
        if best_match is not None:
            matched.append(best_match)
    return weighted / total, False, tuple(matched)
```

- [ ] **Step 5: Write `retrieval/fusion.py`, `retrieval/facets.py`, `retrieval/candidates.py`, `retrieval/cursor.py`**

`retrieval/fusion.py`:

```python
"""Weighted-sum fusion with explanations (P4 spec, decision 4). Pure."""
from typing import Mapping, Sequence

from retrieval.config import RetrievalConfig
from retrieval.model import AxisExplanation, Candidate, QueryView, ScoredCandidate
from retrieval.scoring import axis_score, situation_score
from retrieval.similarity import SimilarityIndex


def fuse(candidate: Candidate, query: QueryView, config: RetrievalConfig, similarity: SimilarityIndex, *,
         lexical_active: bool) -> ScoredCandidate:
    """score = sum over present axes of (w_a / sum of w over present axes) x s_a. Absent query axes are not
    scored (FR-15); a meme with no profile is neutral on every present axis but keeps its real lexical score."""
    present = query.present_axes(lexical_active=lexical_active)
    total_weight = sum(config.weights[axis] for axis in present)
    if not present or total_weight <= 0:
        return ScoredCandidate(candidate, 0.0, ())
    explanations, score = [], 0.0
    for axis in present:
        weight = config.weights[axis] / total_weight
        matched = ()
        if axis == "situation":
            if candidate.axis_path:
                value, neutral = situation_score(candidate.cosine, config.situation_floor, config.situation_ceil,
                                                 config.neutral_score)
            else:
                value, neutral = config.neutral_score, True
            query_values = (query.situation,)
        elif axis == "lexical":
            value, neutral, query_values = candidate.lexical, False, ()
        else:
            query_pairs = query.axes[axis]
            query_values = tuple(key for key, _confidence in query_pairs)
            if candidate.axis_path:
                value, neutral, matched = axis_score(axis, query_pairs, candidate.values.get(axis, ()), similarity,
                                                     config.neutral_score, config.use_neighbours)
            else:
                value, neutral = config.neutral_score, True
        contribution = weight * value
        score += contribution
        explanations.append(AxisExplanation(axis, round(value, 6), round(weight, 6), round(contribution, 6),
                                            neutral, query_values, matched))
    return ScoredCandidate(candidate, round(score, 6), tuple(explanations))


def rank_candidates(scored: Sequence[ScoredCandidate]) -> list[ScoredCandidate]:
    return sorted(scored, key=lambda item: (-item.score, item.candidate.image_id))


def explanation_dict(scored: ScoredCandidate) -> dict:
    """The public `explanation` object of a result (shared schema memefromtextresult)."""
    return {
        "axes": [
            {
                "axis": axis.axis, "score": axis.score, "weight": axis.weight, "contribution": axis.contribution,
                "neutral": axis.neutral, "queryValues": list(axis.query_values),
                "matched": [{"queryKey": m.query_key, "memeKey": m.meme_key, "similarity": m.similarity,
                             "memeConfidence": m.meme_confidence} for m in axis.matched],
            }
            for axis in scored.axes
        ],
        "situationText": scored.candidate.situation_text,
        "useWhen": scored.candidate.use_when,
    }
```

`retrieval/facets.py`:

```python
"""Duplicate-cluster collapse and facet counts (P4 spec, decision 5). Pure."""
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from retrieval.config import AXES
from retrieval.model import ScoredCandidate


@dataclass(frozen=True)
class RankedItem:
    scored: ScoredCandidate
    cluster_size: int | None     # candidates of its duplicate cluster collapsed into it (itself included)


def collapse_clusters(ranked: Sequence[ScoredCandidate]) -> list[RankedItem]:
    """`ranked` is already ordered best first; each cluster keeps its first (best) member."""
    sizes = Counter(item.candidate.cluster_id for item in ranked if item.candidate.cluster_id is not None)
    seen: set[int] = set()
    items: list[RankedItem] = []
    for item in ranked:
        cluster_id = item.candidate.cluster_id
        if cluster_id is None:
            items.append(RankedItem(item, None))
        elif cluster_id not in seen:
            seen.add(cluster_id)
            items.append(RankedItem(item, sizes[cluster_id]))
    return items


def facet_counts(items: Sequence[RankedItem]) -> dict[str, dict[str, int]]:
    """Per axis, how many collapsed results carry each value (an item counts once per value)."""
    counts: dict[str, Counter] = {axis: Counter() for axis in AXES}
    for item in items:
        for axis in AXES:
            for key in {key for key, _confidence in item.scored.candidate.values.get(axis, ())}:
                counts[axis][key] += 1
    return {axis: dict(counter) for axis, counter in counts.items()}
```

`retrieval/candidates.py`:

```python
"""Union of the candidate sources (P4 spec, decision 3). Pure."""
from typing import Hashable, Sequence


def merge_ranked(sources: Sequence[Sequence[Hashable]], cap: int) -> list:
    """Round-robin by rank over the ranked source lists, without duplicates, up to `cap` items."""
    if cap <= 0:
        return []
    merged: list = []
    seen: set = set()
    depth = max((len(source) for source in sources), default=0)
    for rank in range(depth):
        for source in sources:
            if rank < len(source) and source[rank] not in seen:
                seen.add(source[rank])
                merged.append(source[rank])
                if len(merged) >= cap:
                    return merged
    return merged
```
`retrieval/cursor.py`:

```python
"""Opaque relevance cursor with a request fingerprint (P4 spec, decision 6). Pure."""
import base64
import hashlib
import json
from typing import Sequence

from retrieval.facets import RankedItem

SCORE_DECIMALS = 6


def fingerprint(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def encode_cursor(score: float, image_id: str, fp: str) -> str:
    payload = {"s": round(score, SCORE_DECIMALS), "i": image_id, "f": fp}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str | None, fp: str) -> tuple[float, str] | None:
    """None when there is no cursor, it is not ours, or it belongs to another request (restart at page 1)."""
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if payload["f"] != fp:
            return None
        return float(payload["s"]), str(payload["i"])
    except (ValueError, KeyError, TypeError):
        return None


def paginate(items: Sequence[RankedItem], cursor: str | None, fp: str, limit: int):
    """Items are ordered by (-score, image id); the page starts after the cursor's (score, id)."""
    position = decode_cursor(cursor, fp)
    start = 0
    if position is not None:
        cursor_score, cursor_id = position
        start = len(items)
        for index, item in enumerate(items):
            score = round(item.scored.score, SCORE_DECIMALS)
            if score < cursor_score or (score == cursor_score and item.scored.candidate.image_id > cursor_id):
                start = index
                break
    page = list(items[start:start + limit])
    has_next = start + limit < len(items)
    next_cursor = encode_cursor(page[-1].scored.score, page[-1].scored.candidate.image_id, fp) if has_next else None
    return page, next_cursor, has_next
```

- [ ] **Step 6: Run the tests**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`
Expected: all PASS. If `test_cursor_round_trip_and_fingerprint_check` fails on the rounded score, the encoder must round to 6 decimals (it does). If the import guard fails because `retrieval/similarity.py` imports `taxonomy.loader`, that is allowed (`taxonomy.*`).

- [ ] **Step 7: Commit**

```bash
git add retrieval tests/retrieval
git commit -m "feat(retrieval): add the pure retrieval core (config, scoring, fusion, facets, cursor)"
```

---

### Task 2: Embedding client and the shared lexical-OR scorer

**Files:**
- Create: `retrieval/embed.py`, `tests/retrieval/test_embed_client.py`, `tests/integration/test_lexical_or_scores.py`
- Modify: `repository/ocr_lemmas.py`, `evaluation/retrievers/lexical.py`

**Interfaces:**
- Produces: `retrieval.embed.EmbedError(Exception)`, `EmbedUnavailable`, `EmbedTimeout`, `EmbedBadResponse`; `OllamaEmbedClient(base_url)` with blocking `embed(model, texts, timeout) -> list[list[float]]` (L2-normalised, one vector per text, raises the typed errors) and `async aembed(model, texts, timeout)`. `repository.ocr_lemmas.lexical_or_scores(session, text, restrict_ids=None) -> dict[uuid.UUID, float]` (per-token `scored_image_matches` scores summed per image; `{}` when no token matches; optional restriction to a collection of UUIDs). The evaluation `LexicalRetriever` "or" mode delegates to it.

- [ ] **Step 1: Write the failing tests**

Create `tests/retrieval/test_embed_client.py`:

```python
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from retrieval.embed import EmbedBadResponse, EmbedError, EmbedTimeout, EmbedUnavailable, OllamaEmbedClient


@pytest.fixture
def server():
    state = {"mode": "ok", "delay": 0.0, "requests": [], "embeddings": [[3.0, 4.0], [0.0, 2.0]]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes):
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state["requests"].append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            mode = state["mode"]
            if mode == "slow":
                time.sleep(state["delay"])
            if mode == "404":
                return self._send(404, b'{"error": "model not found"}')
            if mode == "500":
                return self._send(500, b'{"error": "boom"}')
            if mode == "garbage":
                return self._send(200, b"not json")
            if mode == "no_embeddings":
                return self._send(200, b'{"done": true}')
            if mode == "wrong_count":
                return self._send(200, json.dumps({"embeddings": [[1.0, 0.0]]}).encode())
            if mode == "incomplete_read":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b'{"embeddings": [')
                return
            self._send(200, json.dumps({"embeddings": state["embeddings"]}).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_success_returns_normalised_vectors_and_sends_the_expected_request(server):
    state, url = server

    vectors = OllamaEmbedClient(url + "/").embed("bge-large", ["a", "b"], timeout=2.0)

    assert vectors[0] == pytest.approx([0.6, 0.8]) and vectors[1] == pytest.approx([0.0, 1.0])
    path, body = state["requests"][0]
    assert path == "/api/embed" and body == {"model": "bge-large", "input": ["a", "b"]}


def test_a_zero_vector_stays_zero():
    from retrieval.embed import normalise
    assert normalise([0.0, 0.0]) == [0.0, 0.0]


def test_a_slow_answer_is_a_timeout(server):
    state, url = server
    state.update(mode="slow", delay=3.0)
    started = time.monotonic()
    with pytest.raises(EmbedTimeout):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=0.3)
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("mode, error", [("404", EmbedUnavailable), ("500", EmbedUnavailable),
                                         ("incomplete_read", EmbedUnavailable)])
def test_http_failures_are_unavailable(server, mode, error):
    state, url = server
    state["mode"] = mode
    with pytest.raises(error):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=2.0)


@pytest.mark.parametrize("mode", ["garbage", "no_embeddings", "wrong_count"])
def test_malformed_responses_are_bad_responses(server, mode):
    state, url = server
    state["mode"] = mode
    with pytest.raises(EmbedBadResponse):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=2.0)


def test_connection_refused_is_unavailable_or_a_timeout_on_windows():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    started = time.monotonic()
    # On Windows a closed loopback port may be reported as a timeout instead of an immediate refusal.
    with pytest.raises((EmbedUnavailable, EmbedTimeout)):
        OllamaEmbedClient(f"http://127.0.0.1:{port}").embed("m", ["a"], timeout=0.3)
    assert time.monotonic() - started < 1.3


def test_no_time_left_is_a_timeout_without_a_request(server):
    state, url = server
    with pytest.raises(EmbedTimeout):
        OllamaEmbedClient(url).embed("m", ["a"], timeout=0)
    assert state["requests"] == []


def test_an_empty_input_returns_no_vectors_without_a_request(server):
    state, url = server
    assert OllamaEmbedClient(url).embed("m", [], timeout=1.0) == []
    assert state["requests"] == []


def test_all_errors_share_a_base_class():
    assert all(issubclass(e, EmbedError) for e in (EmbedUnavailable, EmbedTimeout, EmbedBadResponse))


@pytest.mark.asyncio
async def test_aembed_runs_in_a_thread(server):
    _state, url = server
    vectors = await OllamaEmbedClient(url).aembed("m", ["a", "b"], timeout=2.0)
    assert len(vectors) == 2
```

Create `tests/integration/test_lexical_or_scores.py`:

```python
"""
Integration tests for repository.ocr_lemmas.lexical_or_scores. Requires a live PostgreSQL instance with pgvector --
see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import lexical_or_scores
from Storage.models import Image, OCRLemma


async def _image(session, *lemmas) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image.id, lemma=lemma))
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_any_token_scores_are_summed_and_more_matches_rank_higher(db_session):
    best = await _image(db_session, "boyfriend", "pizza")
    partial = await _image(db_session, "pizza")
    await _image(db_session, "weather")

    scores = await lexical_or_scores(db_session, "boyfriend pizza tonight")

    assert scores[best.id] > scores[partial.id] > 0
    assert len([i for i in scores if i in (best.id, partial.id)]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_the_restriction_is_applied(db_session):
    inside = await _image(db_session, "pizza")
    outside = await _image(db_session, "pizza")

    scores = await lexical_or_scores(db_session, "pizza", {inside.id})

    assert set(scores) == {inside.id} and outside.id not in scores


@pytest.mark.asyncio(loop_scope="session")
async def test_no_text_or_no_match_gives_an_empty_dict(db_session):
    assert await lexical_or_scores(db_session, "") == {}
    assert await lexical_or_scores(db_session, None) == {}
    assert await lexical_or_scores(db_session, "zzzqqqxx") == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval/test_embed_client.py -q` → collection ERROR (`retrieval.embed` missing).
Run (separate command): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_lexical_or_scores.py -q` → ImportError (`lexical_or_scores` missing).

- [ ] **Step 3: Write `retrieval/embed.py`**

```python
"""Ollama embedding client over the standard library (P4 spec, decision 2). Pure; mirrors parse/client.py.

The backend has no HTTP client dependency, so this uses urllib in a worker thread with socket timeouts. Error
messages never contain the user's text."""
import asyncio
import http.client
import json
import math
import urllib.error
import urllib.request


class EmbedError(Exception):
    pass


class EmbedUnavailable(EmbedError):
    """Ollama is not reachable, answered with an error status, or does not have the model."""


class EmbedTimeout(EmbedError):
    """Ollama did not answer within the time budget."""


class EmbedBadResponse(EmbedError):
    """Ollama answered but the response is not what /api/embed returns."""


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return list(vector) if norm == 0 else [value / norm for value in vector]


class OllamaEmbedClient:
    def __init__(self, base_url: str):
        self._url = base_url.rstrip("/") + "/api/embed"

    def embed(self, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        if not texts:
            return []
        if timeout <= 0:
            raise EmbedTimeout("no time left for the call")
        body = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise EmbedUnavailable("Ollama answered HTTP 404 (model not pulled or wrong endpoint)") from None
            raise EmbedUnavailable(f"Ollama answered HTTP {error.code}") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise EmbedTimeout("Ollama did not answer in time") from None
            raise EmbedUnavailable("Ollama is not reachable") from None
        except http.client.HTTPException:
            raise EmbedUnavailable("Ollama closed the connection or sent a malformed response") from None
        except TimeoutError:
            raise EmbedTimeout("Ollama did not answer in time") from None
        except OSError:
            raise EmbedUnavailable("Ollama is not reachable") from None
        try:
            embeddings = json.loads(payload)["embeddings"]
            vectors = [[float(value) for value in vector] for vector in embeddings]
        except (ValueError, KeyError, TypeError):
            raise EmbedBadResponse("the Ollama response has no embeddings") from None
        if len(vectors) != len(texts):
            raise EmbedBadResponse("the Ollama response has the wrong number of embeddings")
        return [normalise(vector) for vector in vectors]

    async def aembed(self, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        return await asyncio.to_thread(self.embed, model, texts, timeout)
```

- [ ] **Step 4: Extract `lexical_or_scores`**

In `repository/ocr_lemmas.py` (the file's line endings may be CRLF: use the Edit tool) add `import re` and `import uuid` if missing, a module constant, and, directly after `scored_image_matches`:

```python
_LEXICAL_TOKEN = re.compile(r"\w+", re.UNICODE)


async def lexical_or_scores(
    session: AsyncSession, text: Optional[str], restrict_ids=None,
) -> dict[uuid.UUID, float]:
    """Any-token variant of scored_image_matches for long messages (shared by the meme-from-text retrieval and
    the evaluation harness): each distinct lower-cased token is matched on its own with the same source and tier
    weights, and the per-token scores are summed per image. {} when nothing matches. `restrict_ids` (a
    collection of UUIDs) limits the result to those images."""
    allowed = None if restrict_ids is None else set(restrict_ids)
    scores: dict[uuid.UUID, float] = {}
    for token in dict.fromkeys(token.lower() for token in _LEXICAL_TOKEN.findall(text or "")):
        token_scores = await scored_image_matches(session, token)
        for image_id, score in (token_scores or {}).items():
            if allowed is not None and image_id not in allowed:
                continue
            scores[image_id] = scores.get(image_id, 0.0) + score
    return scores
```

Then change `evaluation/retrievers/lexical.py` so the "or" branch delegates (keep `and` as is, remove the now-unused `re` and `TOKEN`):

```python
        else:
            scores = await lexical_or_scores(self.session, query.text, corpus)
```
with `from repository.ocr_lemmas import lexical_or_scores, scored_image_matches`. The result ordering/limit lines below it stay unchanged.

- [ ] **Step 5: Run the tests**

Run each as its own command:
`/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q` (all PASS);
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/ -q` (the **whole** root, because shared code in `repository/ocr_lemmas.py` and the evaluation retriever changed; expect all PASS, 513 or more);
`/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/evaluation -q`.

- [ ] **Step 6: Commit**

```bash
git add retrieval/embed.py tests/retrieval/test_embed_client.py tests/integration/test_lexical_or_scores.py repository/ocr_lemmas.py evaluation/retrievers/lexical.py
git commit -m "feat(retrieval): add the Ollama embedding client and extract lexical_or_scores"
```

---

### Task 3: SQL candidate generation

**Files:**
- Create: `repository/meme_retrieval.py`, `tests/integration/test_meme_retrieval_repository.py`

**Interfaces:**
- Consumes: Task 1 (`Candidate`, `Filters`, `merge_ranked`), Task 2 (`lexical_or_scores`), the P2 tables (`MemeProfile`, `MemeProfileValue`, `MemeProfileSituationEmbedding`), `TmpImageClusters`, `ImageExtras`, `ImageTag`.
- Produces: `repository.meme_retrieval.MemeRetrievalRepository(session)` with `async generate_candidates(*, query_vector, query_axes, neighbours, lexical_text, filters, restrict_ids, model_name, situation_k, axis_k, lexical_k, total_cap) -> list[Candidate]` (`query_vector: list[float] | None`; `query_axes: Mapping[str, AxisValues]`; `neighbours: Mapping[str, Mapping[str, Sequence[tuple[str, float]]]] | None`; `lexical_text: str | None`; `filters: Filters`; `restrict_ids: Collection[uuid.UUID] | None`) and `async load_basics(image_ids: Collection[str]) -> dict[str, tuple[str, bool]]` (image id -> (filename, flagged)). Read-only; never commits.

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_meme_retrieval_repository.py`:

```python
"""
Integration tests for repository/meme_retrieval.py. Requires a live PostgreSQL instance with pgvector -- see
tests/integration/conftest.py. No Ollama: query vectors are built by hand.
"""
import inspect
import uuid

import pytest

from repository import meme_retrieval
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from repository.meme_retrieval import MemeRetrievalRepository
from retrieval.model import Filters
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageExtras, ImageTag, OCRLemma, TmpImageClusters,
)

MODEL = "bge-large"
KW = dict(model_name=MODEL, situation_k=50, axis_k=50, lexical_k=50, total_cap=100)


def _vec(*leading):
    vector = [0.0] * PROFILE_EMBEDDING_DIM
    for index, value in enumerate(leading):
        vector[index] = value
    return vector


async def _image(session, *, status="active", flagged=None, filename=None) -> Image:
    image = Image(filename=filename or f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    if flagged is not None:
        session.add(ImageExtras(image_id=image.id, flagged=flagged))
        await session.flush()
    return image


async def _profile(session, image, *, vector=None, model=MODEL, situation="a situation", use_when="Use when it fits.",
                   **axes):
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    repo = MemeProfileRepository(session)
    await repo.save_profile(image.id, ProfileData(situation, None, use_when, values),
                            ProfileMeta(2, "text-model", "p2-test", "f" * 64, False))
    if vector is not None:
        await repo.save_situation_embedding(image.id, vector, model, "digest-1")
    await session.flush()


async def _search(session, **overrides):
    params = dict(query_vector=_vec(1.0), query_axes={}, neighbours=None, lexical_text=None, filters=Filters(),
                  restrict_ids=None)
    params.update(overrides)
    params.update({key: value for key, value in KW.items() if key not in overrides})
    return await MemeRetrievalRepository(session).generate_candidates(**params)


def _ids(candidates):
    return [c.image_id for c in candidates]


@pytest.mark.asyncio(loop_scope="session")
async def test_the_eligible_set_excludes_inactive_flagged_and_profile_less_images(db_session):
    good = await _image(db_session)
    pending = await _image(db_session, status="pending")
    flagged = await _image(db_session, flagged=True)
    unflagged = await _image(db_session, flagged=False)
    no_profile = await _image(db_session)
    for image in (good, pending, flagged, unflagged):
        await _profile(db_session, image, vector=_vec(1.0, 0.1))

    result = _ids(await _search(db_session))

    assert str(good.id) in result and str(unflagged.id) in result
    for excluded in (pending, flagged, no_profile):
        assert str(excluded.id) not in result


@pytest.mark.asyncio(loop_scope="session")
async def test_the_restriction_limits_every_source(db_session):
    inside = await _image(db_session)
    outside = await _image(db_session)
    for image in (inside, outside):
        await _profile(db_session, image, vector=_vec(1.0), emotion=[("anger", 0.9)])

    result = _ids(await _search(db_session, query_axes={"emotion": (("anger", 0.9),)},
                                restrict_ids={inside.id}))

    assert result == [str(inside.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_source_orders_by_cosine_uses_the_configured_model_and_fills_cosine(db_session):
    near = await _image(db_session)
    far = await _image(db_session)
    other_model = await _image(db_session)
    await _profile(db_session, near, vector=_vec(1.0, 0.1))
    await _profile(db_session, far, vector=_vec(0.0, 1.0))
    await _profile(db_session, other_model, vector=_vec(1.0, 0.0), model="another-model")

    candidates = await _search(db_session, restrict_ids={near.id, far.id, other_model.id})

    assert _ids(candidates) == [str(near.id), str(far.id)]
    assert candidates[0].cosine == pytest.approx(1.0 / (1.01 ** 0.5), abs=1e-4)
    assert candidates[1].cosine == pytest.approx(0.0, abs=1e-4)
    assert all(c.axis_path for c in candidates)


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_source_ranks_exact_before_neighbour_matches_and_skips_unrelated(db_session):
    exact = await _image(db_session)
    neighbour = await _image(db_session)
    unrelated = await _image(db_session)
    await _profile(db_session, exact, emotion=[("anger", 0.9)])
    await _profile(db_session, neighbour, emotion=[("annoyance", 0.9)])
    await _profile(db_session, unrelated, emotion=[("joy", 0.9)])

    candidates = await _search(
        db_session, query_vector=None, query_axes={"emotion": (("anger", 0.9),)},
        neighbours={"emotion": {"anger": (("annoyance", 0.8),)}},
        restrict_ids={exact.id, neighbour.id, unrelated.id})

    assert _ids(candidates) == [str(exact.id), str(neighbour.id)]
    assert candidates[0].values["emotion"] == (("anger", 0.9),) and candidates[0].cosine is None


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_filters_are_or_within_an_axis_and_and_across_axes(db_session):
    a = await _image(db_session)
    b = await _image(db_session)
    c = await _image(db_session)
    await _profile(db_session, a, vector=_vec(1.0), emotion=[("anger", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, b, vector=_vec(1.0), emotion=[("joy", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, c, vector=_vec(1.0), emotion=[("anger", 0.9)])
    scope = {a.id, b.id, c.id}

    or_within = await _search(db_session, restrict_ids=scope,
                              filters=Filters(axes={"emotion": ("anger", "joy")}))
    and_across = await _search(db_session, restrict_ids=scope,
                               filters=Filters(axes={"emotion": ("anger",), "stance": ("mock",)}))

    assert set(_ids(or_within)) == {str(a.id), str(b.id), str(c.id)}
    assert _ids(and_across) == [str(a.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_filters_keep_the_existing_any_pair_semantics(db_session):
    tagged = await _image(db_session)
    other = await _image(db_session)
    for image in (tagged, other):
        await _profile(db_session, image, vector=_vec(1.0))
    db_session.add(ImageTag(image_id=tagged.id, key="category", value="work", source="rule"))
    await db_session.flush()

    result = _ids(await _search(db_session, restrict_ids={tagged.id, other.id},
                                filters=Filters(tags={"category": ("work",)})))

    assert result == [str(tagged.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_source_adds_unprofiled_images_flagged_as_not_axis_path(db_session):
    profiled = await _image(db_session)
    lexical_only = await _image(db_session)
    await _profile(db_session, profiled, vector=_vec(1.0))
    db_session.add(OCRLemma(image_id=lexical_only.id, lemma="deadline"))
    await db_session.flush()
    scope = {profiled.id, lexical_only.id}

    candidates = await _search(db_session, lexical_text="deadline", restrict_ids=scope)
    by_id = {c.image_id: c for c in candidates}

    assert by_id[str(lexical_only.id)].axis_path is False and by_id[str(lexical_only.id)].lexical == pytest.approx(1.0)
    assert by_id[str(lexical_only.id)].values == {} and by_id[str(lexical_only.id)].cosine is None
    assert by_id[str(profiled.id)].axis_path is True and by_id[str(profiled.id)].lexical == 0.0


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_filters_exclude_unprofiled_lexical_hits(db_session):
    lexical_only = await _image(db_session)
    db_session.add(OCRLemma(image_id=lexical_only.id, lemma="deadline"))
    await db_session.flush()

    candidates = await _search(db_session, lexical_text="deadline", restrict_ids={lexical_only.id},
                               filters=Filters(axes={"emotion": ("anger",)}))

    assert candidates == []


@pytest.mark.asyncio(loop_scope="session")
async def test_inactive_and_flagged_images_never_come_from_the_lexical_source(db_session):
    inactive = await _image(db_session, status="pending")
    flagged = await _image(db_session, flagged=True)
    for image in (inactive, flagged):
        db_session.add(OCRLemma(image_id=image.id, lemma="deadline"))
    await db_session.flush()

    assert await _search(db_session, lexical_text="deadline", restrict_ids={inactive.id, flagged.id}) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_clusters_are_attached(db_session):
    first = await _image(db_session)
    second = await _image(db_session)
    single = await _image(db_session)
    for image in (first, second, single):
        await _profile(db_session, image, vector=_vec(1.0))
    db_session.add_all([TmpImageClusters(cluster_id=870001, image_id=first.id),
                        TmpImageClusters(cluster_id=870001, image_id=second.id)])
    await db_session.flush()

    by_id = {c.image_id: c for c in await _search(db_session, restrict_ids={first.id, second.id, single.id})}

    assert by_id[str(first.id)].cluster_id == by_id[str(second.id)].cluster_id == 870001
    assert by_id[str(single.id)].cluster_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_total_cap_applies_and_sources_are_merged_round_robin(db_session):
    images = [await _image(db_session) for _ in range(4)]
    for index, image in enumerate(images):
        await _profile(db_session, image, vector=_vec(1.0, index * 0.1), emotion=[("anger", 0.9)])
    scope = {image.id for image in images}

    candidates = await _search(db_session, restrict_ids=scope, query_axes={"emotion": (("anger", 0.9),)}, total_cap=3)

    assert len(candidates) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_to_search_gives_nothing(db_session):
    assert await _search(db_session, query_vector=None) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_load_basics_returns_filename_and_flagged(db_session):
    plain = await _image(db_session, filename="plain.jpg")
    flagged = await _image(db_session, filename="flagged.jpg", flagged=True)

    basics = await MemeRetrievalRepository(db_session).load_basics({str(plain.id), str(flagged.id)})

    assert basics == {str(plain.id): ("plain.jpg", False), str(flagged.id): ("flagged.jpg", True)}


def test_the_repository_never_commits():
    assert "commit(" not in inspect.getsource(meme_retrieval)
```

- [ ] **Step 2: Run to verify failure**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_meme_retrieval_repository.py -q` → collection ERROR (`repository.meme_retrieval` missing).

- [ ] **Step 3: Write `repository/meme_retrieval.py`**

```python
"""SQL side of meme-from-text retrieval (P4 spec, decision 3). Read-only: plain Python values out, no commits.

Eligible set (axis path): active, not flagged, has a meme profile, inside the optional restriction, passing the
hard filters. The lexical source may additionally return images WITHOUT a profile (labelled axis_path False),
never when an axis filter is present."""
import uuid
from collections import defaultdict
from typing import Collection, Mapping, Sequence

from sqlalchemy import and_, case, exists, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from repository.ocr_lemmas import lexical_or_scores
from retrieval.candidates import merge_ranked
from retrieval.model import AxisValues, Candidate, Filters
from Storage.models import (
    Image, ImageExtras, ImageTag, MemeProfile, MemeProfileSituationEmbedding as Embedding,
    MemeProfileValue as Value, TmpImageClusters,
)

MAX_EF_SEARCH = 1000


class MemeRetrievalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ----------------------------------------------------------------- eligibility

    @staticmethod
    def _active_unflagged() -> list:
        return [Image.status == "active", or_(ImageExtras.flagged.is_(None), ImageExtras.flagged.is_(False))]

    @staticmethod
    def _tag_condition(tags: Mapping[str, Sequence[str]]):
        pairs = [and_(ImageTag.key == key, ImageTag.value.in_(list(values))) for key, values in tags.items() if values]
        if not pairs:
            return None
        return Image.id.in_(select(ImageTag.image_id).where(or_(*pairs)))

    def _eligible_ids(self, filters: Filters, restrict_ids: Collection[uuid.UUID] | None):
        """Select of image ids that have a profile and pass every condition."""
        statement = (
            select(MemeProfile.image_id)
            .join(Image, Image.id == MemeProfile.image_id)
            .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
            .where(*self._active_unflagged())
        )
        if restrict_ids is not None:
            statement = statement.where(Image.id.in_(list(restrict_ids)))
        tag_condition = self._tag_condition(filters.tags)
        if tag_condition is not None:
            statement = statement.where(tag_condition)
        for axis, keys in filters.axes.items():
            if keys:
                statement = statement.where(exists().where(and_(
                    Value.image_id == MemeProfile.image_id, Value.axis == axis, Value.value_key.in_(list(keys)))))
        return statement

    # ----------------------------------------------------------------- sources

    async def _prepare_vector_search(self, k: int) -> None:
        """pgvector 0.8: let a filtered HNSW scan keep going and return up to k rows. Ignored when unsupported."""
        ef_search = min(max(int(k), 40), MAX_EF_SEARCH)
        for statement in (f"SET LOCAL hnsw.ef_search = {ef_search}", "SET LOCAL hnsw.iterative_scan = relaxed_order"):
            try:
                async with self.session.begin_nested():
                    await self.session.execute(text(statement))
            except DBAPIError:
                pass

    async def _situation_ids(self, vector: list[float], model_name: str, k: int, eligible) -> list[uuid.UUID]:
        await self._prepare_vector_search(k)
        distance = Embedding.embedding.cosine_distance(vector)
        rows = await self.session.execute(
            select(Embedding.image_id)
            .where(Embedding.model_name == model_name, Embedding.image_id.in_(eligible))
            .order_by(distance, Embedding.image_id)
            .limit(k)
        )
        return [row[0] for row in rows.all()]

    async def _axis_ids(self, query_axes: Mapping[str, AxisValues],
                        neighbours: Mapping[str, Mapping[str, Sequence[tuple[str, float]]]] | None,
                        k: int, eligible) -> list[uuid.UUID]:
        weights: dict[tuple[str, str], float] = {}
        for axis, values in query_axes.items():
            for key, confidence in values:
                weights[(axis, key)] = max(weights.get((axis, key), 0.0), confidence)
                for neighbour_key, score in (neighbours or {}).get(axis, {}).get(key, ()):
                    weights[(axis, neighbour_key)] = max(weights.get((axis, neighbour_key), 0.0), confidence * score)
        if not weights:
            return []
        weight_expression = case(
            *[(and_(Value.axis == axis, Value.value_key == key), weight) for (axis, key), weight in weights.items()],
            else_=0.0)
        total = func.sum(Value.confidence * weight_expression)
        rows = await self.session.execute(
            select(Value.image_id)
            .where(or_(*[and_(Value.axis == axis, Value.value_key == key) for (axis, key) in weights]),
                   Value.image_id.in_(eligible))
            .group_by(Value.image_id)
            .order_by(total.desc(), Value.image_id)
            .limit(k)
        )
        return [row[0] for row in rows.all()]

    async def _lexical(self, text_value: str, filters: Filters, restrict_ids, eligible, k: int) -> dict[uuid.UUID, float]:
        scores = await lexical_or_scores(self.session, text_value, restrict_ids)
        if not scores:
            return {}
        if filters.has_axis_filters():
            allowed_rows = await self.session.execute(
                select(MemeProfile.image_id).where(MemeProfile.image_id.in_(list(scores)),
                                                   MemeProfile.image_id.in_(eligible)))
        else:
            statement = (
                select(Image.id)
                .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
                .where(*self._active_unflagged(), Image.id.in_(list(scores)))
            )
            tag_condition = self._tag_condition(filters.tags)
            if tag_condition is not None:
                statement = statement.where(tag_condition)
            allowed_rows = await self.session.execute(statement)
        allowed = {row[0] for row in allowed_rows.all()}
        top = sorted(((image_id, score) for image_id, score in scores.items() if image_id in allowed),
                     key=lambda pair: (-pair[1], str(pair[0])))[:k]
        if not top:
            return {}
        maximum = top[0][1]
        return {image_id: score / maximum for image_id, score in top}

    # ----------------------------------------------------------------- public

    async def generate_candidates(
        self, *, query_vector: list[float] | None, query_axes: Mapping[str, AxisValues],
        neighbours: Mapping[str, Mapping[str, Sequence[tuple[str, float]]]] | None, lexical_text: str | None,
        filters: Filters, restrict_ids: Collection[uuid.UUID] | None, model_name: str, situation_k: int,
        axis_k: int, lexical_k: int, total_cap: int,
    ) -> list[Candidate]:
        eligible = self._eligible_ids(filters, restrict_ids)
        sources: list[list[uuid.UUID]] = []
        if query_vector is not None:
            sources.append(await self._situation_ids(query_vector, model_name, situation_k, eligible))
        if query_axes:
            sources.append(await self._axis_ids(query_axes, neighbours, axis_k, eligible))
        lexical: dict[uuid.UUID, float] = {}
        if lexical_text:
            lexical = await self._lexical(lexical_text, filters, restrict_ids, eligible, lexical_k)
            sources.append(sorted(lexical, key=lambda image_id: (-lexical[image_id], str(image_id))))
        ids = merge_ranked(sources, total_cap)
        if not ids:
            return []
        return await self._load(ids, query_vector, model_name, lexical)

    async def _load(self, ids: list[uuid.UUID], vector: list[float] | None, model_name: str,
                    lexical: Mapping[uuid.UUID, float]) -> list[Candidate]:
        profiles = {
            row.image_id: row for row in (await self.session.execute(
                select(MemeProfile.image_id, MemeProfile.situation_text, MemeProfile.use_when)
                .where(MemeProfile.image_id.in_(ids)))).all()
        }
        values: dict[uuid.UUID, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
        for image_id, axis, key, confidence in (await self.session.execute(
                select(Value.image_id, Value.axis, Value.value_key, Value.confidence)
                .where(Value.image_id.in_(ids))
                .order_by(Value.image_id, Value.axis, Value.confidence.desc(), Value.value_key))).all():
            values[image_id][axis].append((key, float(confidence)))
        cosines: dict[uuid.UUID, float] = {}
        if vector is not None:
            distance = Embedding.embedding.cosine_distance(vector)
            for image_id, value in (await self.session.execute(
                    select(Embedding.image_id, distance)
                    .where(Embedding.image_id.in_(ids), Embedding.model_name == model_name))).all():
                cosines[image_id] = 1.0 - float(value)
        clusters = {
            image_id: cluster_id for image_id, cluster_id in (await self.session.execute(
                select(TmpImageClusters.image_id, TmpImageClusters.cluster_id)
                .where(TmpImageClusters.image_id.in_(ids)))).all()
        }
        candidates = []
        for image_id in ids:
            profile = profiles.get(image_id)
            candidates.append(Candidate(
                image_id=str(image_id),
                situation_text=profile.situation_text if profile else None,
                use_when=profile.use_when if profile else None,
                cosine=cosines.get(image_id),
                values={axis: tuple(pairs) for axis, pairs in values.get(image_id, {}).items()},
                lexical=float(lexical.get(image_id, 0.0)),
                axis_path=profile is not None,
                cluster_id=clusters.get(image_id),
            ))
        return candidates

    async def load_basics(self, image_ids: Collection[str]) -> dict[str, tuple[str, bool]]:
        """Filename and flagged state for hydrating result items."""
        ids = [uuid.UUID(image_id) for image_id in image_ids]
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Image.id, Image.filename, ImageExtras.flagged)
            .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
            .where(Image.id.in_(ids)))
        return {str(image_id): (filename, bool(flagged)) for image_id, filename, flagged in rows.all()}
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_meme_retrieval_repository.py -q` → all PASS. Then (separate command) the whole root: `DATABASE_URL=... python -m pytest tests/integration/ -q`.
Notes: `SET LOCAL` runs inside a savepoint (`begin_nested`) so an unsupported setting cannot abort the surrounding transaction. If `test_situation_source_orders...` fails on the cosine value, remember the stored vectors are NOT normalised in the test (`_vec(1.0, 0.1)`), pgvector's `cosine_distance` normalises internally, so the expected cosine is `1 / sqrt(1.01)`.

- [ ] **Step 5: Commit**

```bash
git add repository/meme_retrieval.py tests/integration/test_meme_retrieval_repository.py
git commit -m "feat(retrieval): add the SQL candidate generation (eligible set, sources, filters, clusters)"
```

---

### Task 4: Pipeline, tools and degraded paths

**Files:**
- Create: `retrieval/pipeline.py`, `retrieval/tools.py`, `tests/retrieval/test_pipeline.py`, `tests/retrieval/test_tools.py`

**Interfaces:**
- Consumes: Tasks 1 to 3, `parse.parser` (`QueryParser`-like object with `async parse(text, hint) -> ParseResult`, `model` property, `degraded_result`, `normalise_text`), `parse.prompt.PARSER_VERSION`, `parse.language.guess_language`, `taxonomy.validate_profile`.
- Produces: `retrieval.pipeline`: `InvalidProfile(ValueError)` with `.errors`; `SearchRequest(text, language_hint=None, profile=None, filters=Filters(), cursor=None, limit=None, restrict_ids=None)`; `ResultItem` = `RankedItem` (re-exported); `SearchResult(items: list[RankedItem], facets: dict[str, dict[str, int]], next_cursor, has_next, profile: dict, degraded: bool, fallback: str, meta: dict)`; `Prepared(profile, view, candidates, lexical_active, degraded, fallback, parse_ms, embed_ms, retrieval_ms, parser_version, model)`; `MemeFromTextPipeline(config, parser, embedder, similarity, tax=None, *, max_input_chars=600, clock=time.monotonic)` with `async search(request, repository) -> SearchResult`, `async prepare(request, repository) -> Prepared`, `rank(prepared, config=None) -> _Entry`-like object with `.items` and `.facets`, `async warm_up() -> bool`. `retrieval.tools`: `SEARCH_INPUT_SCHEMA`, `SEARCH_OUTPUT_SCHEMA`, `SCORE_INPUT_SCHEMA`, `async search_memes_from_text(arguments, pipeline, repository) -> dict`, `result_item_dict(item) -> dict`, `meta_dict(meta)`, `score_candidates(arguments, config, similarity) -> list[dict]`, `explain_results(arguments)`.
- Contract details the tests pin: the embedder is any object with `async aembed(model, texts, timeout) -> list[list[float]]`; the repository is any object with `async generate_candidates(**kwargs) -> list[Candidate]` called with exactly the keyword names of Task 3; the parser circuit breaker counts consecutive `unavailable`/`timeout` parse fallbacks; `fallback` in the result is `"none"` or the parser reason, with `+embed_unavailable` appended (or `embed_unavailable` alone) when the embedding failed; `degraded` is true whenever `fallback != "none"`.

- [ ] **Step 1: Write the failing pipeline tests**

Create `tests/retrieval/test_pipeline.py`:

```python
import dataclasses
import logging

import pytest

from parse.parser import ParseMeta, ParseResult
from retrieval.embed import EmbedUnavailable
from retrieval.model import Candidate, Filters
from retrieval.pipeline import InvalidProfile, MemeFromTextPipeline, SearchRequest
from taxonomy import load
from tests.retrieval.helpers import make_candidate, make_config, make_similarity

TAX = load()
GOOD_PROFILE = {
    "taxonomyVersion": TAX.version, "language": "en", "parserVersion": "p3-x", "degraded": False,
    "situation": "A friend got promoted.", "situationConfidence": None,
    "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}],
    "stance": [{"key": "congratulate", "confidence": 0.9, "source": "model"}], "topic": [], "tone": [],
}


class FakeParser:
    model = "m"

    def __init__(self, profile=None, fallback="none", degraded=False):
        self.profile = dict(profile or GOOD_PROFILE)
        self.fallback = fallback
        self.degraded = degraded
        self.calls = []

    async def parse(self, text, hint=None):
        self.calls.append((text, hint))
        profile = dict(self.profile)
        if self.degraded:
            profile = {**profile, "degraded": True, "situation": None, "emotion": [], "stance": []}
        return ParseResult(profile, ParseMeta("m", "p3-x", 5, False, 1, self.fallback))


class FakeEmbedder:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def aembed(self, model, texts, timeout):
        self.calls.append((model, list(texts), timeout))
        if self.fail:
            raise EmbedUnavailable("down")
        return [[1.0, 0.0] for _ in texts]


class FakeRepository:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def generate_candidates(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.candidates)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _pipeline(parser=None, embedder=None, clock=None, **config):
    return MemeFromTextPipeline(make_config(**config), parser or FakeParser(), embedder or FakeEmbedder(),
                                make_similarity(), TAX, clock=clock or Clock())


def _candidates():
    return [
        make_candidate("a", cosine=0.85, values={"emotion": (("joy", 0.9),), "stance": (("congratulate", 0.9),)}),
        make_candidate("b", cosine=0.65, values={"emotion": (("joy", 0.6),)}),
        make_candidate("c", cosine=0.50, values={}),
    ]


@pytest.mark.asyncio
async def test_a_normal_search_ranks_explains_and_reports_meta():
    repository = FakeRepository(_candidates())

    result = await _pipeline().search(SearchRequest(text="My friend got promoted!"), repository)

    assert [i.scored.candidate.image_id for i in result.items] == ["a", "b", "c"]
    assert result.degraded is False and result.fallback == "none" and result.has_next is False
    assert result.profile["situation"] == "A friend got promoted."
    assert result.meta["available"] is True and result.meta["candidates"] == 3 and result.meta["cacheHit"] is False
    assert result.meta["model"] == "m" and result.meta["parserVersion"]
    top = result.items[0].scored
    assert {a.axis for a in top.axes} >= {"situation", "emotion", "stance"}
    kwargs = repository.calls[0]
    assert kwargs["query_vector"] == [1.0, 0.0] and kwargs["model_name"] == "bge-large"
    assert kwargs["query_axes"] == {"emotion": (("joy", 0.9),), "stance": (("congratulate", 0.9),)}
    assert kwargs["lexical_text"] == "My friend got promoted!" and kwargs["restrict_ids"] is None
    assert (kwargs["situation_k"], kwargs["axis_k"], kwargs["lexical_k"], kwargs["total_cap"]) == (300, 300, 100, 600)
    assert kwargs["neighbours"]["emotion"]["joy"] == ()


@pytest.mark.asyncio
async def test_the_situation_phrase_is_what_gets_embedded():
    embedder = FakeEmbedder()
    await _pipeline(embedder=embedder).search(SearchRequest(text="hello"), FakeRepository(_candidates()))
    assert embedder.calls == [("bge-large", ["A friend got promoted."], 2.0)]


@pytest.mark.asyncio
async def test_an_edited_profile_skips_the_parser_and_is_validated():
    parser = FakeParser()
    profile = {**GOOD_PROFILE, "situation": "My own words."}

    result = await _pipeline(parser=parser).search(SearchRequest(text="hello", profile=profile),
                                                   FakeRepository(_candidates()))

    assert parser.calls == [] and result.profile["situation"] == "My own words."
    assert result.profile["parserVersion"] == "edited" and result.degraded is False


@pytest.mark.asyncio
async def test_an_invalid_edited_profile_raises_with_the_errors():
    bad = {**GOOD_PROFILE, "emotion": [{"key": "not_a_key", "confidence": 0.9, "source": "human"}]}
    with pytest.raises(InvalidProfile) as caught:
        await _pipeline().search(SearchRequest(text="hello", profile=bad), FakeRepository([]))
    assert any("unknown key" in error for error in caught.value.errors)


@pytest.mark.asyncio
async def test_a_degraded_parse_embeds_the_raw_text_and_flags_the_response():
    parser = FakeParser(fallback="unavailable", degraded=True)
    embedder = FakeEmbedder()
    repository = FakeRepository(_candidates())

    result = await _pipeline(parser=parser, embedder=embedder).search(
        SearchRequest(text="Привет, как дела?"), repository)

    assert result.degraded is True and result.fallback == "unavailable"
    assert embedder.calls[0][1] == ["Привет, как дела?"]
    assert repository.calls[0]["query_axes"] == {} and repository.calls[0]["query_vector"] == [1.0, 0.0]
    assert [a.axis for a in result.items[0].scored.axes] == ["situation"]
    assert result.profile["degraded"] is True


@pytest.mark.asyncio
async def test_an_embedding_failure_falls_back_to_the_axes_and_lexical():
    result = await _pipeline(embedder=FakeEmbedder(fail=True)).search(
        SearchRequest(text="hello"), FakeRepository(_candidates()))

    assert result.degraded is True and result.fallback == "embed_unavailable"
    assert result.items and "situation" not in {a.axis for a in result.items[0].scored.axes}


@pytest.mark.asyncio
async def test_a_degraded_parse_and_a_failed_embedding_combine_the_reasons():
    parser = FakeParser(fallback="timeout", degraded=True)
    lexical = make_candidate("l", cosine=None, lexical=1.0, axis_path=False)

    result = await _pipeline(parser=parser, embedder=FakeEmbedder(fail=True)).search(
        SearchRequest(text="hello"), FakeRepository([lexical]))

    assert result.fallback == "timeout+embed_unavailable" and result.degraded is True
    assert [i.scored.candidate.image_id for i in result.items] == ["l"]


@pytest.mark.asyncio
async def test_nothing_to_score_gives_an_empty_page():
    parser = FakeParser(fallback="unavailable", degraded=True)

    result = await _pipeline(parser=parser, embedder=FakeEmbedder(fail=True)).search(
        SearchRequest(text="hello"), FakeRepository([make_candidate("a", cosine=None, lexical=0.0)]))

    assert result.items == [] and result.has_next is False and result.next_cursor is None


@pytest.mark.asyncio
async def test_the_breaker_opens_after_consecutive_failures_and_closes_after_the_interval():
    clock = Clock()
    parser = FakeParser(fallback="unavailable", degraded=True)
    pipeline = _pipeline(parser=parser, clock=clock)
    repository = FakeRepository([])

    for index in range(3):
        await pipeline.search(SearchRequest(text=f"message {index}"), repository)
    assert len(parser.calls) == 3

    blocked = await pipeline.search(SearchRequest(text="message 3"), repository)
    assert len(parser.calls) == 3 and blocked.fallback == "breaker_open"

    clock.now += 31
    await pipeline.search(SearchRequest(text="message 4"), repository)
    assert len(parser.calls) == 4


@pytest.mark.asyncio
async def test_a_successful_parse_resets_the_failure_count():
    parser = FakeParser(fallback="unavailable", degraded=True)
    pipeline = _pipeline(parser=parser)
    repository = FakeRepository([])
    await pipeline.search(SearchRequest(text="one"), repository)
    await pipeline.search(SearchRequest(text="two"), repository)
    parser.fallback, parser.degraded = "none", False
    await pipeline.search(SearchRequest(text="three"), repository)
    parser.fallback, parser.degraded = "unavailable", True
    await pipeline.search(SearchRequest(text="four"), repository)
    await pipeline.search(SearchRequest(text="five"), repository)

    assert len(parser.calls) == 5                       # never reached three consecutive failures


@pytest.mark.asyncio
async def test_paging_uses_the_cache_and_a_cursor():
    repository = FakeRepository(_candidates())
    pipeline = _pipeline()

    first = await pipeline.search(SearchRequest(text="hello", limit=2), repository)
    second = await pipeline.search(SearchRequest(text="hello", limit=2, cursor=first.next_cursor), repository)

    assert [i.scored.candidate.image_id for i in first.items] == ["a", "b"] and first.has_next is True
    assert [i.scored.candidate.image_id for i in second.items] == ["c"] and second.has_next is False
    assert len(repository.calls) == 1 and second.meta["cacheHit"] is True


@pytest.mark.asyncio
async def test_the_cache_expires_and_depends_on_text_filters_and_restriction():
    clock = Clock()
    repository = FakeRepository(_candidates())
    pipeline = _pipeline(clock=clock)

    await pipeline.search(SearchRequest(text="hello"), repository)
    await pipeline.search(SearchRequest(text="hello"), repository)
    assert len(repository.calls) == 1
    await pipeline.search(SearchRequest(text="other"), repository)
    await pipeline.search(SearchRequest(text="hello", filters=Filters(axes={"emotion": ("joy",)})), repository)
    await pipeline.search(SearchRequest(text="hello", restrict_ids=frozenset()), repository)
    assert len(repository.calls) == 4
    clock.now += 121
    await pipeline.search(SearchRequest(text="hello"), repository)
    assert len(repository.calls) == 5


@pytest.mark.asyncio
async def test_a_zero_ttl_disables_the_cache():
    repository = FakeRepository(_candidates())
    pipeline = _pipeline(cache_ttl_seconds=0.0)
    await pipeline.search(SearchRequest(text="hello"), repository)
    await pipeline.search(SearchRequest(text="hello"), repository)
    assert len(repository.calls) == 2


@pytest.mark.asyncio
async def test_the_limit_is_clamped_to_the_configured_range():
    repository = FakeRepository([make_candidate(f"{i:02d}", cosine=0.85) for i in range(60)])
    pipeline = _pipeline(cache_ttl_seconds=0.0)

    big = await pipeline.search(SearchRequest(text="hello", limit=500), repository)
    zero = await pipeline.search(SearchRequest(text="hello", limit=0), repository)
    default = await pipeline.search(SearchRequest(text="hello"), repository)

    assert (len(big.items), len(zero.items), len(default.items)) == (50, 20, 20)


@pytest.mark.asyncio
async def test_duplicates_collapse_and_facets_count_the_collapsed_set():
    candidates = [
        make_candidate("a", cosine=0.85, values={"emotion": (("joy", 0.9),)}, cluster_id=7),
        make_candidate("b", cosine=0.80, values={"emotion": (("joy", 0.9),)}, cluster_id=7),
        make_candidate("c", cosine=0.60, values={"emotion": (("joy", 0.6),)}),
    ]

    result = await _pipeline().search(SearchRequest(text="hello"), FakeRepository(candidates))

    assert [(i.scored.candidate.image_id, i.cluster_size) for i in result.items] == [("a", 2), ("c", None)]
    assert result.facets["emotion"] == {"joy": 2}


@pytest.mark.asyncio
async def test_the_restriction_and_filters_reach_the_repository():
    repository = FakeRepository(_candidates())
    filters = Filters(axes={"tone": ("sarcastic",)}, tags={"category": ("work",)})

    await _pipeline().search(SearchRequest(text="hello", filters=filters, restrict_ids=frozenset()), repository)

    assert repository.calls[0]["filters"] == filters and repository.calls[0]["restrict_ids"] == frozenset()


@pytest.mark.asyncio
async def test_lexical_can_be_disabled_by_configuration():
    repository = FakeRepository(_candidates())
    await _pipeline(lexical_enabled=False).search(SearchRequest(text="hello"), repository)
    assert repository.calls[0]["lexical_text"] is None


@pytest.mark.asyncio
async def test_the_query_vector_is_cached_between_requests_for_the_same_situation():
    embedder = FakeEmbedder()
    pipeline = _pipeline(embedder=embedder, cache_ttl_seconds=0.0)
    repository = FakeRepository(_candidates())
    await pipeline.search(SearchRequest(text="one"), repository)
    await pipeline.search(SearchRequest(text="two"), repository)
    assert len(embedder.calls) == 1


@pytest.mark.asyncio
async def test_prepare_and_rank_can_be_used_separately_with_other_weights():
    pipeline = _pipeline()
    prepared = await pipeline.prepare(SearchRequest(text="hello"), FakeRepository(_candidates()))

    default_entry = pipeline.rank(prepared)
    situation_only = pipeline.rank(prepared, make_config(weights={"situation": 1.0, "emotion": 0.0, "stance": 0.0,
                                                                  "topic": 0.0, "tone": 0.0, "lexical": 0.0}))

    assert prepared.view.situation == "A friend got promoted." and len(prepared.candidates) == 3
    assert [i.scored.candidate.image_id for i in default_entry.items] == ["a", "b", "c"]
    assert [i.scored.candidate.image_id for i in situation_only.items] == ["a", "b", "c"]
    assert situation_only.items[0].scored.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_warm_up_never_raises_and_reports_success():
    assert await _pipeline().warm_up() is True
    assert await _pipeline(embedder=FakeEmbedder(fail=True)).warm_up() is False


@pytest.mark.asyncio
async def test_the_text_is_never_logged(caplog):
    marker = "SECRET-MARKER-98765"
    caplog.set_level(logging.DEBUG)
    for parser, embedder in ((FakeParser(), FakeEmbedder()), (FakeParser(fallback="unavailable", degraded=True),
                                                             FakeEmbedder(fail=True))):
        await _pipeline(parser=parser, embedder=embedder).search(
            SearchRequest(text=f"my private message {marker}"), FakeRepository(_candidates()))

    assert marker not in caplog.text
    assert any(record.name == "retrieval" for record in caplog.records)
```

Create `tests/retrieval/test_tools.py`:

```python
import pytest

from retrieval import tools
from retrieval.model import Filters
from retrieval.pipeline import MemeFromTextPipeline
from taxonomy import load
from tests.retrieval.helpers import make_candidate, make_config, make_similarity
from tests.retrieval.test_pipeline import FakeEmbedder, FakeParser, FakeRepository

TAX = load()


def _pipeline():
    return MemeFromTextPipeline(make_config(), FakeParser(), FakeEmbedder(), make_similarity(), TAX)


@pytest.mark.asyncio
async def test_search_tool_returns_the_output_shape():
    candidates = [make_candidate("a", cosine=0.85, values={"emotion": (("joy", 0.9),)}, cluster_id=3)]

    output = await tools.search_memes_from_text(
        {"text": "My friend got promoted!", "limit": 5, "filters": {"emotion": ["joy"], "tags": [{"key": "c", "value": "v"}]}},
        _pipeline(), FakeRepository(candidates))

    assert set(output) == {"items", "facets", "nextCursor", "hasNext", "profile", "degraded", "fallback", "meta"}
    item = output["items"][0]
    assert set(item) == {"imageId", "score", "axisPath", "explanation", "clusterId", "clusterSize"}
    assert item["imageId"] == "a" and item["axisPath"] is True and item["clusterId"] == 3 and item["clusterSize"] == 1
    assert {f["name"] for f in output["facets"]} == {"emotion", "stance", "topic", "tone"}
    assert output["facets"][0]["buckets"] == [{"value": "joy", "count": 1}]
    assert output["hasNext"] is False and output["nextCursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [None, "text", {}, {"text": 5}, {"text": "x", "limit": "many"},
                                       {"text": "x", "languageHint": "fr"}, {"text": "x", "filters": {"emotion": "joy"}}])
async def test_a_malformed_call_raises_value_error(arguments):
    with pytest.raises(ValueError):
        await tools.search_memes_from_text(arguments, _pipeline(), FakeRepository([]))


def test_schema_dictionaries_describe_the_calls():
    assert tools.SEARCH_INPUT_SCHEMA["required"] == ["text"]
    assert tools.SEARCH_OUTPUT_SCHEMA["required"] == ["items", "hasNext", "profile", "degraded", "fallback", "meta"]
    assert tools.SCORE_INPUT_SCHEMA["required"] == ["candidates", "query"]


def test_score_candidates_tool_scores_plain_dicts():
    config, similarity = make_config(), make_similarity()
    arguments = {
        "query": {"situation": "x", "language": "en", "degraded": False,
                  "emotion": [{"key": "anger", "confidence": 0.9}], "stance": [], "topic": [], "tone": []},
        "candidates": [{"imageId": "a", "cosine": 0.85, "values": {"emotion": [["annoyance", 0.6]]}, "lexical": 0.0,
                        "axisPath": True, "clusterId": None, "situationText": None, "useWhen": None}],
        "lexicalActive": False,
    }

    scored = tools.score_candidates(arguments, config, similarity)

    assert scored[0]["imageId"] == "a" and 0 < scored[0]["score"] <= 1
    emotion = next(a for a in scored[0]["explanation"]["axes"] if a["axis"] == "emotion")
    assert emotion["matched"][0]["memeKey"] == "annoyance"
    assert Filters().is_empty()
```

- [ ] **Step 2: Run to verify failure**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q` → collection ERRORS (`retrieval.pipeline`, `retrieval.tools` missing).

- [ ] **Step 3: Write `retrieval/pipeline.py`**

```python
"""The meme-from-text retrieval pipeline (P4 spec, decisions 2, 6 and 7). Pure: stdlib, parse, taxonomy.

Services are injected: a parser with `async parse(text, hint)`, an embedder with
`async aembed(model, texts, timeout)`, and, per call, a repository with `async generate_candidates(**kwargs)`.
The user's text is never logged; only the structured line below and exception type names are."""
import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Mapping

from parse.language import guess_language
from parse.parser import degraded_result, normalise_text
from parse.prompt import PARSER_VERSION
from retrieval.config import RetrievalConfig
from retrieval.cursor import fingerprint, paginate
from retrieval.facets import RankedItem, collapse_clusters, facet_counts
from retrieval.fusion import fuse, rank_candidates
from retrieval.model import Candidate, Filters, QueryView
from retrieval.similarity import SimilarityIndex
from taxonomy.loader import load
from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile

logger = logging.getLogger("retrieval")

CACHE_ENTRIES = 50
VECTOR_CACHE_ENTRIES = 200
WARM_TIMEOUT_SECONDS = 120.0
BREAKER_FAILURES_FALLBACKS = ("unavailable", "timeout")


class InvalidProfile(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class SearchRequest:
    text: str
    language_hint: str | None = None
    profile: Mapping | None = None
    filters: Filters = field(default_factory=Filters)
    cursor: str | None = None
    limit: int | None = None
    restrict_ids: frozenset | None = None       # evaluation only: restrict every source to these image ids


@dataclass(frozen=True)
class Prepared:
    profile: dict
    view: QueryView
    candidates: list[Candidate]
    lexical_active: bool
    degraded: bool
    fallback: str
    parse_ms: int
    embed_ms: int
    retrieval_ms: int
    parser_version: str
    model: str


@dataclass(frozen=True)
class _Entry:
    items: list[RankedItem]
    facets: dict[str, dict[str, int]]
    prepared: Prepared


@dataclass(frozen=True)
class SearchResult:
    items: list[RankedItem]
    facets: dict[str, dict[str, int]]
    next_cursor: str | None
    has_next: bool
    profile: dict
    degraded: bool
    fallback: str
    meta: dict


class MemeFromTextPipeline:
    def __init__(self, config: RetrievalConfig, parser, embedder, similarity: SimilarityIndex,
                 tax: Taxonomy | None = None, *, max_input_chars: int = 600, clock=time.monotonic):
        self._config = config
        self._parser = parser
        self._embedder = embedder
        self._similarity = similarity
        self._tax = tax or load()
        self._max_chars = max_input_chars
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, _Entry]] = OrderedDict()
        self._vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------------ public

    async def search(self, request: SearchRequest, repository) -> SearchResult:
        started = self._clock()
        limit = request.limit if request.limit else self._config.default_limit
        limit = max(1, min(limit, self._config.max_limit))
        text = normalise_text(request.text, self._max_chars)
        fp = self._fingerprint(request, text)
        entry = self._cache_get(fp)
        cache_hit = entry is not None
        if entry is None:
            prepared = await self.prepare(request, repository, text=text)
            entry = self.rank(prepared)
            self._cache_put(fp, entry)
        page, next_cursor, has_next = paginate(entry.items, request.cursor, fp, limit)
        prepared = entry.prepared
        meta = {
            "available": True,
            "parseMs": 0 if cache_hit else prepared.parse_ms,
            "embedMs": 0 if cache_hit else prepared.embed_ms,
            "retrievalMs": int((self._clock() - started) * 1000) if cache_hit else prepared.retrieval_ms,
            "candidates": len(prepared.candidates),
            "cacheHit": cache_hit,
            "parserVersion": prepared.parser_version,
            "model": prepared.model,
        }
        result = SearchResult(page, entry.facets, next_cursor, has_next, prepared.profile, prepared.degraded,
                              prepared.fallback, meta)
        logger.info("retrieval degraded=%s fallback=%s candidates=%s results=%s cache_hit=%s parse_ms=%s "
                    "embed_ms=%s retrieval_ms=%s", result.degraded, result.fallback, meta["candidates"],
                    len(page), cache_hit, meta["parseMs"], meta["embedMs"], meta["retrievalMs"])
        return result

    async def prepare(self, request: SearchRequest, repository, *, text: str | None = None) -> Prepared:
        """Resolve the profile, embed, generate candidates (everything before scoring)."""
        if text is None:
            text = normalise_text(request.text, self._max_chars)
        t0 = self._clock()
        profile, edited, parse_fallback = await self._resolve_profile(request, text)
        t1 = self._clock()
        view = QueryView.from_profile(profile)
        parse_degraded = bool(profile["degraded"]) and not edited
        embed_text = (text or None) if parse_degraded else view.situation
        vector, embed_failed = await self._embed(embed_text)
        t2 = self._clock()
        scoring_view = view
        if vector is None:
            # no query vector (nothing to embed, or the embedding failed): the situation axis cannot be scored
            scoring_view = QueryView(situation=None, axes=view.axes, language=view.language, degraded=view.degraded)
        elif parse_degraded:
            scoring_view = QueryView(situation=text, axes={}, language=view.language, degraded=True)
        lexical_text = text if (self._config.lexical_enabled and text) else None
        candidates = await repository.generate_candidates(
            query_vector=vector, query_axes=scoring_view.axes,
            neighbours=self._similarity.neighbours_for(scoring_view.axes) if self._config.use_neighbours else None,
            lexical_text=lexical_text, filters=request.filters, restrict_ids=request.restrict_ids,
            model_name=self._config.embedding_model, situation_k=self._config.situation_k,
            axis_k=self._config.axis_k, lexical_k=self._config.lexical_k, total_cap=self._config.total_cap)
        t3 = self._clock()
        lexical_active = lexical_text is not None and any(candidate.lexical > 0 for candidate in candidates)
        fallback = parse_fallback if parse_fallback not in ("none", "retry") else "none"
        if embed_failed:
            fallback = "embed_unavailable" if fallback == "none" else f"{fallback}+embed_unavailable"
        return Prepared(
            profile=profile, view=scoring_view, candidates=candidates, lexical_active=lexical_active,
            degraded=fallback != "none", fallback=fallback, parse_ms=int((t1 - t0) * 1000),
            embed_ms=int((t2 - t1) * 1000), retrieval_ms=int((t3 - t2) * 1000), parser_version=self._parser_version(),
            model=getattr(self._parser, "model", ""))

    def rank(self, prepared: Prepared, config: RetrievalConfig | None = None) -> _Entry:
        config = config or self._config
        if not prepared.view.present_axes(lexical_active=prepared.lexical_active):
            return _Entry([], {axis: {} for axis in ("emotion", "stance", "topic", "tone")}, prepared)
        scored = [fuse(candidate, prepared.view, config, self._similarity, lexical_active=prepared.lexical_active)
                  for candidate in prepared.candidates]
        items = collapse_clusters(rank_candidates(scored))
        return _Entry(items, facet_counts(items), prepared)

    async def warm_up(self) -> bool:
        """One embedding call so the model is loaded; never raises."""
        try:
            await self._embedder.aembed(self._config.embedding_model, ["warm up the embedding model"],
                                        WARM_TIMEOUT_SECONDS)
            return True
        except Exception as error:
            logger.info("embedding warm-up failed: %s", type(error).__name__)
            return False

    # ------------------------------------------------------------------ internals

    def _parser_version(self) -> str:
        return PARSER_VERSION

    async def _resolve_profile(self, request: SearchRequest, text: str):
        """(profile dict, edited?, parse fallback)."""
        if request.profile is not None:
            result = validate_profile(request.profile, self._tax)
            if result.errors:
                raise InvalidProfile(result.errors)
            language = request.profile.get("language") or request.language_hint or guess_language(text)
            profile = {"taxonomyVersion": self._tax.version, "language": language, "parserVersion": "edited",
                       "degraded": False, **result.profile}
            return profile, True, "none"
        if self._clock() < self._open_until:
            parsed = degraded_result(text, request.language_hint, self._tax, "breaker_open",
                                     model=getattr(self._parser, "model", ""), parser_version=PARSER_VERSION)
        else:
            parsed = await self._parser.parse(text, request.language_hint)
            self._record(parsed.meta.fallback)
        return dict(parsed.profile), False, parsed.meta.fallback

    def _record(self, fallback: str) -> None:
        if fallback in BREAKER_FAILURES_FALLBACKS:
            self._failures += 1
            if self._failures >= self._config.breaker_failures:
                self._open_until = self._clock() + self._config.breaker_open_seconds
        else:
            self._failures = 0

    async def _embed(self, text: str | None):
        """(vector or None, failed?). No text means no embedding and no failure."""
        if not text:
            return None, False
        key = (self._config.embedding_model, text)
        if key in self._vectors:
            self._vectors.move_to_end(key)
            return self._vectors[key], False
        try:
            vectors = await self._embedder.aembed(self._config.embedding_model, [text],
                                                  self._config.embed_timeout_seconds)
            vector = vectors[0]
        except Exception as error:
            logger.warning("embedding failed: %s", type(error).__name__)
            return None, True
        self._vectors[key] = vector
        while len(self._vectors) > VECTOR_CACHE_ENTRIES:
            self._vectors.popitem(last=False)
        return vector, False

    def _fingerprint(self, request: SearchRequest, text: str) -> str:
        restriction = None
        if request.restrict_ids is not None:
            restriction = hashlib.sha256(",".join(sorted(str(i) for i in request.restrict_ids)).encode()).hexdigest()
        config_hash = hashlib.sha256(repr(self._config).encode()).hexdigest()
        return fingerprint({
            "t": text, "p": request.profile, "f": request.filters.to_dict(), "r": restriction,
            "pv": PARSER_VERSION, "tv": self._tax.version, "c": config_hash,
        })

    def _cache_get(self, fp: str) -> _Entry | None:
        item = self._cache.get(fp)
        if item is None:
            return None
        expires, entry = item
        if self._clock() >= expires:
            del self._cache[fp]
            return None
        self._cache.move_to_end(fp)
        return entry

    def _cache_put(self, fp: str, entry: _Entry) -> None:
        if self._config.cache_ttl_seconds <= 0:
            return
        self._cache[fp] = (self._clock() + self._config.cache_ttl_seconds, entry)
        self._cache.move_to_end(fp)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
```

- [ ] **Step 4: Write `retrieval/tools.py`**

```python
"""Typed tool entry points of the retrieval stage (FR-22, P4 spec, decision 8). Pure.

Plain dictionaries in and out so an agent layer can call the same code the endpoint uses; the schemas below are
JSON-schema dictionaries like parse/tool.py's. The endpoint adds `meme` (hydrated from the database) to each item."""
from typing import Mapping

from parse.schema import LANGUAGES
from retrieval.config import AXES, RetrievalConfig
from retrieval.fusion import explanation_dict, fuse, rank_candidates
from retrieval.model import Candidate, Filters, QueryView
from retrieval.pipeline import MemeFromTextPipeline, SearchRequest, SearchResult
from retrieval.similarity import SimilarityIndex

SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "languageHint": {"type": "string", "enum": list(LANGUAGES)},
        "profile": {"$ref": "queryprofile.schema.json"},
        "filters": {"type": "object", "properties": {
            **{axis: {"type": "array", "items": {"type": "string"}} for axis in AXES},
            "tags": {"type": "array", "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}}},
        "cursor": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["text"],
}

SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object"}},
        "facets": {"type": "array", "items": {"$ref": "facet.schema.json"}},
        "nextCursor": {"type": "string"},
        "hasNext": {"type": "boolean"},
        "profile": {"$ref": "queryprofile.schema.json"},
        "degraded": {"type": "boolean"},
        "fallback": {"type": "string"},
        "meta": {"type": "object"},
    },
    "required": ["items", "hasNext", "profile", "degraded", "fallback", "meta"],
}

SCORE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"$ref": "queryprofile.schema.json"},
        "candidates": {"type": "array", "items": {"type": "object"}},
        "lexicalActive": {"type": "boolean"},
    },
    "required": ["candidates", "query"],
}


def _filters(raw) -> Filters:
    if raw is None:
        return Filters()
    if not isinstance(raw, Mapping):
        raise ValueError("filters must be an object")
    axes = {}
    for axis in AXES:
        keys = raw.get(axis)
        if keys is None:
            continue
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError(f"filters.{axis} must be a list of strings")
        axes[axis] = tuple(keys)
    tags: dict[str, list[str]] = {}
    for pair in raw.get("tags") or []:
        if not isinstance(pair, Mapping) or not isinstance(pair.get("key"), str) or not isinstance(pair.get("value"), str):
            raise ValueError("filters.tags must be a list of {key, value} objects")
        tags.setdefault(pair["key"], []).append(pair["value"])
    return Filters(axes=axes, tags={key: tuple(values) for key, values in tags.items()})


def build_request(arguments) -> SearchRequest:
    if not isinstance(arguments, Mapping):
        raise ValueError("arguments must be an object")
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("text is required and must be a string")
    hint = arguments.get("languageHint")
    if hint is not None and hint not in LANGUAGES:
        raise ValueError(f"languageHint must be one of {', '.join(LANGUAGES)}")
    limit = arguments.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise ValueError("limit must be an integer")
    profile = arguments.get("profile")
    if profile is not None and not isinstance(profile, Mapping):
        raise ValueError("profile must be an object")
    cursor = arguments.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError("cursor must be a string")
    return SearchRequest(text=text, language_hint=hint, profile=profile, filters=_filters(arguments.get("filters")),
                         cursor=cursor, limit=limit)


def result_item_dict(item) -> dict:
    scored = item.scored
    return {
        "imageId": scored.candidate.image_id, "score": scored.score, "axisPath": scored.candidate.axis_path,
        "explanation": explanation_dict(scored), "clusterId": scored.candidate.cluster_id,
        "clusterSize": item.cluster_size,
    }


def facets_list(facets: Mapping[str, Mapping[str, int]]) -> list[dict]:
    return [{"name": axis, "buckets": [{"value": value, "count": count} for value, count in sorted(counts.items())]}
            for axis, counts in facets.items()]


def result_dict(result: SearchResult) -> dict:
    return {
        "items": [result_item_dict(item) for item in result.items], "facets": facets_list(result.facets),
        "nextCursor": result.next_cursor, "hasNext": result.has_next, "profile": result.profile,
        "degraded": result.degraded, "fallback": result.fallback, "meta": result.meta,
    }


async def search_memes_from_text(arguments, pipeline: MemeFromTextPipeline, repository) -> dict:
    """Raises ValueError for a malformed call (and InvalidProfile for an invalid edited profile); model failures
    come back as a degraded result."""
    return result_dict(await pipeline.search(build_request(arguments), repository))


def _candidate_from_dict(raw: Mapping) -> Candidate:
    return Candidate(
        image_id=raw["imageId"], situation_text=raw.get("situationText"), use_when=raw.get("useWhen"),
        cosine=raw.get("cosine"),
        values={axis: tuple((key, float(confidence)) for key, confidence in pairs)
                for axis, pairs in (raw.get("values") or {}).items()},
        lexical=float(raw.get("lexical", 0.0)), axis_path=bool(raw.get("axisPath", True)),
        cluster_id=raw.get("clusterId"))


def score_candidates(arguments: Mapping, config: RetrievalConfig, similarity: SimilarityIndex) -> list[dict]:
    """Score and rank candidate dicts against a query profile (pure; no database, no model)."""
    query = QueryView.from_profile(arguments["query"])
    lexical_active = bool(arguments.get("lexicalActive", False))
    scored = [fuse(_candidate_from_dict(raw), query, config, similarity, lexical_active=lexical_active)
              for raw in arguments["candidates"]]
    return [{"imageId": item.candidate.image_id, "score": item.score, "explanation": explanation_dict(item)}
            for item in rank_candidates(scored)]


def explain_results(arguments: Mapping) -> list[dict]:
    """The explanation objects of already scored results (identity helper kept for the agent layer)."""
    return [item["explanation"] for item in arguments.get("items", [])]
```

- [ ] **Step 5: Run the tests**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q` → all PASS.
Notes: the import guard allows `retrieval/tools.py` importing `parse.schema`. In `test_the_breaker_opens...` each of the three first searches uses a different text so the result cache does not hide the parser calls. If `test_nothing_to_score_gives_an_empty_page` fails, check that `prepared.lexical_active` is false when no candidate has `lexical > 0` and `view.situation` is None (parse degraded plus failed embedding), so `present_axes` is empty and `rank` returns no items.

- [ ] **Step 6: Commit**

```bash
git add retrieval/pipeline.py retrieval/tools.py tests/retrieval/test_pipeline.py tests/retrieval/test_tools.py
git commit -m "feat(retrieval): add the pipeline (cache, breaker, degraded paths) and the typed tools"
```

---

### Task 5: Settings and shared schemas with generated types

**Files:**
- Modify: `environments/settings.yaml`, `shared/schemas/all.schema.json`
- Create: `shared/schemas/memefromtextrequest.schema.json`, `memefromtextresult.schema.json`, `memefromtextresponse.schema.json`, `axisexplanation.schema.json`; `tests/retrieval/test_settings_files.py`, `tests/retrieval/test_schemas.py`
- Regenerate (commit the results): `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`, `Backend/app/types/generated/*`

**Interfaces:**
- Produces: the `search.meme_from_text` settings block under the existing `search:` key; four shared schemas (below) with generated TS, Kotlin and Python types; Python modules `Backend.app.types.generated.memefromtextrequest|memefromtextresult|memefromtextresponse|axisexplanation` each exposing `Schema`.

- [ ] **Step 1: Write the failing tests**

Create `tests/retrieval/test_settings_files.py`:

```python
from pathlib import Path

import pytest
import yaml

from retrieval.config import WEIGHT_KEYS, load_config

SETTINGS = Path(__file__).resolve().parents[2] / "environments" / "settings.yaml"


def _dotted(data, prefix=""):
    flat = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and not value.get("__leaf__"):
            flat.update(_dotted(value, name + "."))
        flat[name] = value
    return flat


@pytest.fixture(scope="module")
def flat():
    return _dotted(yaml.safe_load(SETTINGS.read_text(encoding="utf-8")))


def test_the_shipped_settings_load_and_match_the_documented_defaults(flat):
    config = load_config(lambda key, default=None: flat.get(key, default), environ={})

    assert config.weights == {"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10,
                              "lexical": 0.10}
    assert set(config.weights) == set(WEIGHT_KEYS)
    assert (config.situation_k, config.axis_k, config.lexical_k, config.total_cap) == (300, 300, 100, 600)
    assert (config.neutral_score, config.situation_floor, config.situation_ceil) == (0.3, 0.45, 0.85)
    assert config.use_neighbours is True and config.lexical_enabled is True
    assert (config.cache_ttl_seconds, config.embed_timeout_seconds, config.warm_interval_minutes) == (120, 2.0, 20)
    assert (config.breaker_failures, config.breaker_open_seconds, config.default_limit, config.max_limit) == (3, 30, 20, 50)
    assert config.embedding_model == "bge-large"
```

Create `tests/retrieval/test_schemas.py`:

```python
import json
from pathlib import Path

import pytest

SCHEMAS = Path(__file__).resolve().parents[2] / "shared" / "schemas"
NEW = ("memefromtextrequest", "memefromtextresult", "memefromtextresponse", "axisexplanation")


def _load(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", NEW)
def test_each_new_schema_exists_is_valid_json_and_is_referenced_from_all(name):
    schema = _load(name)
    assert schema["$id"] == f"{name}.schema.json" and schema["type"] == "object"
    all_text = (SCHEMAS / "all.schema.json").read_text(encoding="utf-8")
    assert f"{name}.schema.json" in all_text


def test_the_request_schema_requires_text_and_bounds_the_limit():
    schema = _load("memefromtextrequest")
    assert schema["required"] == ["text"]
    assert schema["properties"]["limit"]["maximum"] == 50 and schema["properties"]["limit"]["minimum"] == 1
    assert set(schema["properties"]["filters"]["properties"]) == {"emotion", "stance", "topic", "tone", "tags"}


def test_the_response_schema_has_the_documented_fields():
    schema = _load("memefromtextresponse")
    assert set(schema["required"]) == {"items", "hasNext", "profile", "degraded", "fallback", "meta"}
    assert set(schema["properties"]["meta"]["properties"]) == {
        "available", "parseMs", "embedMs", "retrievalMs", "candidates", "cacheHit", "parserVersion", "model"}


def test_the_result_schema_carries_meme_score_explanation_and_cluster_info():
    schema = _load("memefromtextresult")
    assert set(schema["required"]) == {"meme", "score", "axisPath", "explanation"}
    assert {"clusterId", "clusterSize"} <= set(schema["properties"])
    assert set(_load("axisexplanation")["required"]) == {
        "axis", "score", "weight", "contribution", "neutral", "queryValues", "matched"}


def test_the_generated_python_models_exist_and_accept_a_minimal_response():
    from Backend.app.types.generated.memefromtextresponse import Schema

    payload = {"items": [], "hasNext": False, "degraded": False, "fallback": "none",
               "profile": {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False,
                           "emotion": [], "stance": [], "topic": [], "tone": []},
               "meta": {"available": True, "parseMs": 1, "embedMs": 1, "retrievalMs": 1, "candidates": 0,
                        "cacheHit": False, "parserVersion": "p3-x", "model": "m"}}
    model = Schema.model_validate(payload)
    assert model.hasNext is False and model.meta.available is True
```

- [ ] **Step 2: Run to verify failure**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval/test_settings_files.py tests/retrieval/test_schemas.py -q` → FAIL (no settings block, no schema files).

- [ ] **Step 3: Add the settings block**

In `environments/settings.yaml` (CRLF: use the Edit tool) add under the existing `search:` mapping, after the `ranking:` block and before the next top-level key (keep the file's indentation style):

```yaml
  # Meme from text retrieval and fusion (epic meme-from-text, P4; see
  # docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-07-p4-retrieval-fusion-and-api-design.md).
  # A missing key is a startup error. The weights are the documented defaults until the P4 tuning on the gold set.
  meme_from_text:
    weights: {situation: 0.40, emotion: 0.15, stance: 0.10, topic: 0.15, tone: 0.10, lexical: 0.10}
    candidates: {situation_k: 300, axis_k: 300, lexical_k: 100, total_cap: 600}
    neutral_score: 0.3            # score of an axis the meme has no value on (FR-15): neutral, not a mismatch
    situation: {floor: 0.45, ceil: 0.85}   # cosine range rescaled to 0..1 (unrelated pairs 0.44-0.58, related 0.66-0.84)
    similarity: {use_neighbours: true}
    lexical: {enabled: true}
    cache_ttl_seconds: 120
    embed_timeout_seconds: 2.0
    warm_interval_minutes: 20     # below parse.keep_alive (30m) so the models stay loaded
    parser_breaker: {failures: 3, open_seconds: 30}
    page: {default_limit: 20, max_limit: 50}
```

Verify with `APP_ENV=general /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -c "from config.settings import settings; print(settings.get('search.meme_from_text.weights.situation'), settings.get('search.meme_from_text.page.max_limit'))"` → `0.4 50`, and the same with `APP_ENV=metal` and `APP_ENV=it`.

- [ ] **Step 4: Write the four schemas and register them**

Look at how `queryprofile.schema.json` was added (`git log --oneline -S queryprofile.schema.json -- shared/schemas/all.schema.json | cat`, then `git show <sha> -- shared/schemas/all.schema.json`) and add the four new schemas to `shared/schemas/all.schema.json` the same way. Create:

`shared/schemas/axisexplanation.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "axisexplanation.schema.json",
  "title": "AxisExplanation",
  "description": "How one axis contributed to a result's score",
  "type": "object",
  "properties": {
    "axis": { "type": "string", "enum": ["situation", "emotion", "stance", "topic", "tone", "lexical"] },
    "score": { "type": "number", "minimum": 0, "maximum": 1 },
    "weight": { "type": "number", "description": "The renormalised share of this axis among the axes present in the query" },
    "contribution": { "type": "number", "description": "weight x score" },
    "neutral": { "type": "boolean", "description": "True when the meme has no value on this axis and the neutral score was used" },
    "queryValues": { "type": "array", "items": { "type": "string" } },
    "matched": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "queryKey": { "type": "string" },
          "memeKey": { "type": "string" },
          "similarity": { "type": "number" },
          "memeConfidence": { "type": "number" }
        },
        "required": ["queryKey", "memeKey", "similarity", "memeConfidence"]
      }
    }
  },
  "required": ["axis", "score", "weight", "contribution", "neutral", "queryValues", "matched"]
}
```

`shared/schemas/memefromtextresult.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "memefromtextresult.schema.json",
  "title": "MemeFromTextResult",
  "type": "object",
  "properties": {
    "meme": { "$ref": "./meme.schema.json" },
    "score": { "type": "number" },
    "axisPath": { "type": "boolean", "description": "False for a lexical-only hit on an image without a meme profile" },
    "explanation": {
      "type": "object",
      "properties": {
        "axes": { "type": "array", "items": { "$ref": "./axisexplanation.schema.json" } },
        "situationText": { "type": ["string", "null"] },
        "useWhen": { "type": ["string", "null"] }
      },
      "required": ["axes"]
    },
    "clusterId": { "type": "integer" },
    "clusterSize": { "type": "integer", "description": "Candidates of the duplicate cluster collapsed into this result, itself included" }
  },
  "required": ["meme", "score", "axisPath", "explanation"]
}
```

`shared/schemas/memefromtextrequest.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "memefromtextrequest.schema.json",
  "title": "MemeFromTextRequest",
  "type": "object",
  "properties": {
    "text": { "type": "string", "minLength": 1 },
    "languageHint": { "type": "string", "enum": ["en", "ru", "es", "other"] },
    "profile": { "$ref": "./queryprofile.schema.json", "description": "An edited query profile; when present the message is not parsed" },
    "filters": {
      "type": "object",
      "properties": {
        "emotion": { "type": "array", "items": { "type": "string" } },
        "stance": { "type": "array", "items": { "type": "string" } },
        "topic": { "type": "array", "items": { "type": "string" } },
        "tone": { "type": "array", "items": { "type": "string" } },
        "tags": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": { "key": { "type": "string" }, "value": { "type": "string" } },
            "required": ["key", "value"]
          }
        }
      }
    },
    "cursor": { "type": "string" },
    "limit": { "type": "integer", "minimum": 1, "maximum": 50 }
  },
  "required": ["text"]
}
```

`shared/schemas/memefromtextresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "memefromtextresponse.schema.json",
  "title": "MemeFromTextResponse",
  "type": "object",
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./memefromtextresult.schema.json" } },
    "facets": { "type": "array", "items": { "$ref": "./facet.schema.json" } },
    "nextCursor": { "type": "string" },
    "hasNext": { "type": "boolean" },
    "profile": { "$ref": "./queryprofile.schema.json" },
    "degraded": { "type": "boolean" },
    "fallback": { "type": "string", "description": "none, or the reason: unavailable, timeout, invalid_output, empty_input, breaker_open, embed_unavailable, or a combination joined with +" },
    "meta": {
      "type": "object",
      "properties": {
        "available": { "type": "boolean", "description": "False when this environment has no meme profiles or no parser (it)" },
        "parseMs": { "type": "integer" },
        "embedMs": { "type": "integer" },
        "retrievalMs": { "type": "integer" },
        "candidates": { "type": "integer" },
        "cacheHit": { "type": "boolean" },
        "parserVersion": { "type": "string" },
        "model": { "type": "string" }
      },
      "required": ["available", "parseMs", "embedMs", "retrievalMs", "candidates", "cacheHit", "parserVersion", "model"]
    }
  },
  "required": ["items", "hasNext", "profile", "degraded", "fallback", "meta"]
}
```

- [ ] **Step 5: Regenerate the three type trees and check for stale diffs**

From the repo root (CLAUDE.md procedure, `documents/generation.md`):
1. Python (from `Backend/`): `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/datamodel-codegen.exe --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel`.
2. TypeScript (from `Frontend/`): `bash generate-types.sh` (needs `npx`; installed). 
3. Kotlin: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe AndroidClient/scripts/generate_dtos.py`.
Then `git status --short` and `git diff --stat`: the changes must be additions for the four new schemas plus whatever the generator rewrites; **review any diff to existing generated types** (it must be empty or purely mechanical for unrelated schemas, or the generator version differs: report it instead of committing noise). If `npx` or the Kotlin script cannot run in the sandbox, say so in your report and leave that tree for the controller; the Python tree and the tests below are mandatory.

- [ ] **Step 6: Run the tests**

Run each as its own command: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`; `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q` (the whole Backend root; generated modules changed).
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add environments/settings.yaml shared/schemas Backend/app/types/generated Frontend/memes-frontend/src/types/generated AndroidClient tests/retrieval
git commit -m "feat(retrieval): add the search.meme_from_text settings and the shared request/response schemas"
```

---

### Task 6: Service, router, startup wiring, docs and packaging

**Files:**
- Create: `Backend/app/services/meme_from_text_pipeline.py`, `Backend/app/services/meme_from_text_service.py`, `Backend/app/api/meme_from_text.py`, `Backend/tests/test_meme_from_text_service.py`, `Backend/tests/test_meme_from_text_endpoint.py`, `Backend/tests/test_meme_from_text_wiring.py`
- Modify: `Backend/app/main.py`, `Backend/tests/test_main_lifespan.py`, `backend_api.md`, `Dockerfile.backend`, `tests/docker/test_backend_image_boots.py`, `tests/retrieval/test_pure_imports.py` (no change expected)

**Interfaces:**
- Consumes: Tasks 1 to 5, `Backend.app.services.query_parser.get_query_parser`, `Backend.app.repositories.image_repository.ImageRepository` (`get_texts`, `get_tags`), generated models.
- Produces: `Backend.app.services.meme_from_text_pipeline.get_pipeline() -> MemeFromTextPipeline | None` (`lru_cache`; None when `parse.model` is unset or the parser is missing; builds `RetrievalConfig` via `load_config(settings.get)`, `load_similarity()`, `OllamaEmbedClient(config.ollama_url)`, the P3 parser); `MemeFromTextService(pipeline, retrieval_repository, image_repository)` with `async search(request_model) -> MemeFromTextResponse` (the generated model); router `POST /api/meme-from-text/search` (`response_model=MemeFromTextResponse`; `InvalidProfile` -> HTTP 422 with `detail` = the error list; `it` or no pipeline -> HTTP 200 with the unavailable response); `lifespan` builds the pipeline and starts a background warm-up task.

- [ ] **Step 1: Write the failing tests**

Create `Backend/tests/test_meme_from_text_service.py` (mirror the import style of `Backend/tests/test_image_service.py`; the Backend root uses `asyncio_mode=auto`):

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from Backend.app.services.meme_from_text_service import MemeFromTextService
from Backend.app.types.generated.memefromtextrequest import Schema as Request
from retrieval.facets import RankedItem
from retrieval.model import Candidate, ScoredCandidate
from retrieval.pipeline import InvalidProfile, SearchResult

PROFILE = {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False, "situation": "s",
           "situationConfidence": None, "emotion": [], "stance": [], "topic": [], "tone": []}
META = {"available": True, "parseMs": 1, "embedMs": 2, "retrievalMs": 3, "candidates": 1, "cacheHit": False,
        "parserVersion": "p3-x", "model": "m"}


def _result(items, **overrides):
    values = dict(items=items, facets={"emotion": {"joy": 1}, "stance": {}, "topic": {}, "tone": {}},
                  next_cursor="cur", has_next=True, profile=PROFILE, degraded=False, fallback="none", meta=META)
    values.update(overrides)
    return SearchResult(**values)


def _item(image_id="11111111-1111-1111-1111-111111111111", axis_path=True, cluster_id=4, cluster_size=2):
    candidate = Candidate(image_id, "A situation.", "Use when it fits.", 0.8, {"emotion": (("joy", 0.9),)}, 0.0,
                          axis_path, cluster_id)
    return RankedItem(ScoredCandidate(candidate, 0.75, ()), cluster_size)


def _service(pipeline, basics=None, texts=None, tags=None):
    retrieval_repository = MagicMock()
    retrieval_repository.load_basics = AsyncMock(return_value=basics or {})
    image_repository = MagicMock()
    image_repository.get_texts = AsyncMock(return_value=texts or [])
    image_repository.get_tags = AsyncMock(return_value=tags or [])
    return MemeFromTextService(pipeline, retrieval_repository, image_repository), retrieval_repository


async def test_the_response_hydrates_memes_and_carries_the_pipeline_fields():
    image_id = "11111111-1111-1111-1111-111111111111"
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item(image_id)]))
    texts = [MagicMock(image_id=image_id, text="HELLO", confidence=0.9)]
    tags = [(image_id, "category", "work", "rule")]
    service, repository = _service(pipeline, {image_id: ("a.jpg", False)}, texts, tags)

    response = await service.search(Request(text="hello"))

    assert response.hasNext is True and response.nextCursor == "cur" and response.degraded is False
    result = response.items[0]
    assert result.meme.id == image_id and result.meme.imageUrl == f"/api/images/{image_id}"
    assert result.meme.originalFileName == "a.jpg" and result.meme.text == ["HELLO (0.9)"]
    assert result.meme.tags[0].category == "category" and result.meme.tags[0].name == "work"
    assert result.score == 0.75 and result.axisPath is True and result.clusterId == 4 and result.clusterSize == 2
    assert {f.name for f in response.facets} == {"emotion", "stance", "topic", "tone"}
    assert response.meta.model == "m" and response.profile.language == "en"
    repository.load_basics.assert_awaited_once()


async def test_the_request_is_translated_for_the_pipeline():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([], has_next=False, next_cursor=None))
    service, _ = _service(pipeline)
    request = Request.model_validate({
        "text": "hello", "languageHint": "ru", "cursor": "c", "limit": 7,
        "filters": {"emotion": ["anger"], "tags": [{"key": "category", "value": "work"}]}})

    await service.search(request)

    search_request = pipeline.search.await_args.args[0]
    assert search_request.text == "hello" and search_request.language_hint == "ru"
    assert search_request.cursor == "c" and search_request.limit == 7 and search_request.profile is None
    assert search_request.filters.axes == {"emotion": ("anger",)}
    assert search_request.filters.tags == {"category": ("work",)}


async def test_an_edited_profile_is_passed_as_a_plain_dict():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([], has_next=False, next_cursor=None))
    service, _ = _service(pipeline)

    await service.search(Request.model_validate({"text": "hello", "profile": PROFILE}))

    assert pipeline.search.await_args.args[0].profile["situation"] == "s"


async def test_no_pipeline_returns_the_explicit_unavailable_answer():
    service, repository = _service(None)

    response = await service.search(Request(text="Привет"))

    assert response.items == [] and response.degraded is True and response.fallback == "unavailable"
    assert response.meta.available is False and response.profile.language == "ru"
    repository.load_basics.assert_not_called()


async def test_an_invalid_profile_error_propagates():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(side_effect=InvalidProfile(["emotion: unknown key 'x'"]))
    service, _ = _service(pipeline)
    with pytest.raises(InvalidProfile):
        await service.search(Request.model_validate({"text": "hello", "profile": PROFILE}))
```

Create `Backend/tests/test_meme_from_text_endpoint.py`:

```python
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.meme_from_text import get_meme_from_text_service, router
from Backend.app.types.generated.memefromtextresponse import Schema as Response
from retrieval.pipeline import InvalidProfile

PROFILE = {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False,
           "emotion": [], "stance": [], "topic": [], "tone": []}
RESPONSE = Response.model_validate({
    "items": [], "hasNext": False, "degraded": False, "fallback": "none", "profile": PROFILE,
    "meta": {"available": True, "parseMs": 0, "embedMs": 0, "retrievalMs": 0, "candidates": 0, "cacheHit": False,
             "parserVersion": "p3-x", "model": "m"}})


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.search = AsyncMock(return_value=RESPONSE)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_meme_from_text_service] = lambda: service
    return TestClient(app), service


def test_post_search_returns_the_generated_response_shape(client_and_service):
    client, service = client_and_service

    response = client.post("/api/meme-from-text/search", json={"text": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"items", "hasNext", "profile", "degraded", "fallback", "meta"}
    assert body["meta"]["available"] is True and body["fallback"] == "none"
    assert service.search.await_args.args[0].text == "hello"
    assert response.headers["cache-control"].startswith("no-")


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "x", "limit": 0}, {"text": "x", "limit": 51},
                                     {"text": "x", "languageHint": "fr"}, {"text": "x", "filters": {"emotion": "joy"}}])
def test_a_malformed_request_is_a_422(client_and_service, payload):
    client, _service = client_and_service
    assert client.post("/api/meme-from-text/search", json=payload).status_code == 422


def test_an_invalid_edited_profile_is_a_422_with_the_errors(client_and_service):
    client, service = client_and_service
    service.search.side_effect = InvalidProfile(["emotion: unknown key 'x'"])

    response = client.post("/api/meme-from-text/search", json={"text": "hello", "profile": PROFILE})

    assert response.status_code == 422 and "unknown key" in str(response.json()["detail"])


def test_the_route_is_post_only(client_and_service):
    client, _service = client_and_service
    assert client.get("/api/meme-from-text/search").status_code == 405
```

Create `Backend/tests/test_meme_from_text_wiring.py`:

```python
from unittest.mock import MagicMock

import Backend.app.services.meme_from_text_pipeline as factory
from retrieval.pipeline import MemeFromTextPipeline


def setup_function():
    factory.get_pipeline.cache_clear()


def teardown_function():
    factory.get_pipeline.cache_clear()


def test_no_parser_means_no_pipeline(monkeypatch):
    monkeypatch.setattr(factory, "get_query_parser", lambda: None)
    assert factory.get_pipeline() is None


def test_the_pipeline_is_built_once_from_the_settings(monkeypatch):
    parser = MagicMock()
    parser.model = "m"
    monkeypatch.setattr(factory, "get_query_parser", lambda: parser)

    pipeline = factory.get_pipeline()

    assert isinstance(pipeline, MemeFromTextPipeline)
    assert factory.get_pipeline() is pipeline
```

Update `Backend/tests/test_main_lifespan.py`: read the file; its tests patch `_configure_logging`, `start_scheduler`, `stop_scheduler` and `get_query_parser` in `Backend.app.main`. The new lifespan also builds the pipeline and starts a warm-up task, so those tests must additionally patch `Backend.app.main.get_pipeline` (return `None`) and keep passing; add two tests: (a) `lifespan` calls `get_pipeline` once before yielding and starts the warm-up task when a pipeline and a parser exist (patch `get_pipeline` to return a MagicMock whose `warm_up` is an `AsyncMock`, patch `get_query_parser` to return a MagicMock with `warm_up = AsyncMock()`, patch `asyncio.sleep` inside the loop to raise `asyncio.CancelledError` after the first iteration, and assert both `warm_up` mocks were awaited at least once), (b) an exception in `get_pipeline` does not stop startup.

- [ ] **Step 2: Run to verify failure**

Run (from `Backend/`, own command): `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/test_meme_from_text_service.py tests/test_meme_from_text_endpoint.py tests/test_meme_from_text_wiring.py -q` → ERRORS (modules missing).

- [ ] **Step 3: Write the pipeline factory**

`Backend/app/services/meme_from_text_pipeline.py`:

```python
"""Process-wide meme-from-text pipeline for the backend (P4 spec, decisions 2 and 7). Built lazily from the
tracked settings. None when the environment has no parser (`parse.model` unset, the `it` environment): the
endpoint then answers with the explicit unavailable response."""
from functools import lru_cache

from Backend.app.services.query_parser import get_query_parser
from config.settings import settings
from retrieval.config import load_config
from retrieval.embed import OllamaEmbedClient
from retrieval.pipeline import MemeFromTextPipeline
from retrieval.similarity import load_similarity


@lru_cache(maxsize=1)
def get_pipeline() -> MemeFromTextPipeline | None:
    parser = get_query_parser()
    if parser is None:
        return None
    config = load_config(settings.get)
    return MemeFromTextPipeline(
        config, parser, OllamaEmbedClient(config.ollama_url), load_similarity(),
        max_input_chars=int(settings.get("parse.max_input_chars")))
```

- [ ] **Step 4: Write the service and the router**

`Backend/app/services/meme_from_text_service.py`:

```python
"""Meme-from-text service: runs the pipeline and hydrates the response (P4 spec, decisions 2 and 9)."""
from typing import Optional

from Backend.app.types.generated.memefromtextrequest import Schema as MemeFromTextRequest
from Backend.app.types.generated.memefromtextresponse import Schema as MemeFromTextResponse
from parse.language import guess_language
from parse.parser import normalise_text
from parse.prompt import PARSER_VERSION
from retrieval.model import Filters
from retrieval.pipeline import MemeFromTextPipeline, SearchRequest
from retrieval.tools import facets_list, result_item_dict
from taxonomy.loader import load

UNAVAILABLE_META = {"available": False, "parseMs": 0, "embedMs": 0, "retrievalMs": 0, "candidates": 0,
                    "cacheHit": False, "parserVersion": PARSER_VERSION, "model": ""}


def _plain(value):
    """The generated models use str-subclass enums for enum fields; tolerate a plain string too."""
    return getattr(value, "value", value)


def _filters(request: MemeFromTextRequest) -> Filters:
    raw = request.filters
    if raw is None:
        return Filters()
    axes = {axis: tuple(getattr(raw, axis) or ()) for axis in ("emotion", "stance", "topic", "tone")
            if getattr(raw, axis)}
    tags: dict[str, list[str]] = {}
    for pair in raw.tags or []:
        tags.setdefault(pair.key, []).append(pair.value)
    return Filters(axes=axes, tags={key: tuple(values) for key, values in tags.items()})


class MemeFromTextService:
    def __init__(self, pipeline: Optional[MemeFromTextPipeline], retrieval_repository, image_repository):
        self.pipeline = pipeline
        self.retrieval_repository = retrieval_repository
        self.image_repository = image_repository

    async def search(self, request: MemeFromTextRequest) -> MemeFromTextResponse:
        if self.pipeline is None:
            return self._unavailable(request)
        search_request = SearchRequest(
            text=request.text,
            language_hint=_plain(request.languageHint),
            profile=request.profile.model_dump(mode="json", exclude_none=True) if request.profile is not None else None,
            filters=_filters(request), cursor=request.cursor, limit=request.limit)
        result = await self.pipeline.search(search_request, self.retrieval_repository)
        items = [result_item_dict(item) for item in result.items]
        await self._hydrate(items)
        payload = {
            "items": items, "facets": facets_list(result.facets), "hasNext": result.has_next,
            "profile": result.profile, "degraded": result.degraded, "fallback": result.fallback, "meta": result.meta,
        }
        if result.next_cursor:
            payload["nextCursor"] = result.next_cursor
        return MemeFromTextResponse.model_validate(payload)

    async def _hydrate(self, items: list[dict]) -> None:
        """Replace each item's `imageId` by the `meme` object (filename, flagged, OCR text, tags)."""
        ids = {item["imageId"] for item in items}
        basics = await self.retrieval_repository.load_basics(ids) if ids else {}
        texts: dict[str, list[str]] = {}
        tags: dict[str, list[dict]] = {}
        if ids:
            for row in await self.image_repository.get_texts(ids):
                texts.setdefault(str(row.image_id), []).append(f"{row.text} ({row.confidence})")
            for image_id, key, value, source in await self.image_repository.get_tags(ids):
                tags.setdefault(str(image_id), []).append(
                    {"name": value, "category": key, "score": 1, "source": source})
        for item in items:
            image_id = item.pop("imageId")
            filename, flagged = basics.get(image_id, (None, False))
            meme = {"id": image_id, "imageUrl": f"/api/images/{image_id}", "text": texts.get(image_id, []),
                    "tags": tags.get(image_id, []), "flagged": flagged}
            if filename is not None:
                meme["originalFileName"] = filename
            if item.get("clusterId") is not None:
                meme["clusterId"] = item["clusterId"]
            item["meme"] = meme
            for key in ("clusterId", "clusterSize"):
                if item.get(key) is None:
                    item.pop(key, None)

    def _unavailable(self, request: MemeFromTextRequest) -> MemeFromTextResponse:
        tax = load()
        language = _plain(request.languageHint) or guess_language(normalise_text(request.text, 600))
        profile = {"taxonomyVersion": tax.version, "language": language, "parserVersion": PARSER_VERSION,
                   "degraded": True, "emotion": [], "stance": [], "topic": [], "tone": []}
        return MemeFromTextResponse.model_validate({
            "items": [], "facets": [], "hasNext": False, "profile": profile, "degraded": True,
            "fallback": "unavailable", "meta": dict(UNAVAILABLE_META)})
```

`Backend/app/api/meme_from_text.py`:

```python
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.cache import no_cache_headers
from Backend.app.services.meme_from_text_pipeline import get_pipeline
from Backend.app.services.meme_from_text_service import MemeFromTextService
from Backend.app.types.generated.memefromtextrequest import Schema as MemeFromTextRequest
from Backend.app.types.generated.memefromtextresponse import Schema as MemeFromTextResponse
from repository.meme_retrieval import MemeRetrievalRepository
from retrieval.pipeline import InvalidProfile
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meme-from-text", tags=["meme-from-text"])


async def get_meme_from_text_service(
    db: AsyncSession = Depends(get_async_db),
) -> AsyncGenerator[MemeFromTextService, None]:
    yield MemeFromTextService(get_pipeline(), MemeRetrievalRepository(db), ImageRepository(db))


@router.post("/search", response_model=MemeFromTextResponse, response_model_exclude_none=True)
async def search_memes_from_text(
    request: MemeFromTextRequest,
    response: Response,
    service: MemeFromTextService = Depends(get_meme_from_text_service),
):
    response.headers.update(no_cache_headers())
    try:
        return await service.search(request)
    except InvalidProfile as error:
        raise HTTPException(status_code=422, detail=error.errors) from None
```

(If `no_cache_headers` returns headers whose `Cache-Control` value does not start with `no-`, adjust the endpoint test's assertion to the real value; read `Backend/app/services/cache.py`.)

- [ ] **Step 5: Wire the router and the lifespan**

In `Backend/app/main.py`: import the router and the helpers next to the others (`from Backend.app.api.meme_from_text import router as meme_from_text_router`, `from Backend.app.services.meme_from_text_pipeline import get_pipeline`), register it with `app.include_router(meme_from_text_router, prefix="/api")` after the images router, and extend `lifespan`:

```python
async def _warm_models(pipeline, parser, interval_minutes: float) -> None:
    """Keep the parse and embedding models loaded (Ollama keep_alive is 30 min); never raises."""
    while True:
        try:
            if parser is not None:
                await parser.warm_up()
            await pipeline.warm_up()
        except Exception as error:
            logging.getLogger(__name__).info("model warm-up failed: %s", type(error).__name__)
        await asyncio.sleep(interval_minutes * 60)
```

and inside `lifespan` after the existing `get_query_parser()` call (add `import asyncio` if missing):

```python
    warm_task = None
    try:
        pipeline = get_pipeline()
        if pipeline is not None:
            warm_task = asyncio.create_task(_warm_models(
                pipeline, get_query_parser(), float(settings.get("search.meme_from_text.warm_interval_minutes"))))
    except Exception as error:
        logging.getLogger(__name__).warning("meme-from-text pipeline not started: %s", type(error).__name__)
    yield
    if warm_task is not None:
        warm_task.cancel()
```
(keep the scheduler start and stop calls exactly as they are around the new code).

- [ ] **Step 6: Document the endpoint, copy the package into the image, extend the docker check**

`backend_api.md` (match the existing heading and table style of its other sections): add a section "POST /api/meme-from-text/search" documenting: purpose; the request body (every field of `memefromtextrequest.schema.json`, limits 1 to 50, default 20, `profile` skips parsing, filter semantics: OR within an axis, AND across axes, tag filters match any listed pair); the response (every field of `memefromtextresponse.schema.json`, `items[].meme` is the existing `Meme`, `items[].explanation.axes[]`, `facets` one per axis counted over the collapsed filtered candidate set, `clusterId`/`clusterSize`, `axisPath`); behaviours (cursor is opaque and tied to the request, `nextCursor` addresses the last returned item, results are cached for 120 s per request; flagged, inactive and profile-less images never appear on the axis path; a lexical-only hit has `axisPath: false`); the degraded contract (`degraded` and `fallback` values, the endpoint never fails because a model failed, `meta.available: false` for an environment without profiles or parser, 422 for a malformed request or an invalid edited profile with the error list in `detail`); `meta` timing fields; and a note that the message text is not stored or logged.

`Dockerfile.backend`: after the `COPY parse ./parse` line add `COPY retrieval ./retrieval`.

`tests/docker/test_backend_image_boots.py`: extend the import check test from P3 (`test_image_can_import_the_parser_and_load_the_taxonomy`) or add a sibling test running `python -c "from retrieval.pipeline import MemeFromTextPipeline; from retrieval.similarity import load_similarity; print(len(load_similarity().neighbours('emotion', 'anger')))"` inside the image and asserting return code 0 and a digit on stdout.

- [ ] **Step 7: Run the tests**

Each as its own command: `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q` (the whole Backend root; includes the updated lifespan tests); `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`; `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/docker -q` (builds the real image; use a tool timeout of 600000 ms or run it detached). Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add Backend/app Backend/tests backend_api.md Dockerfile.backend tests/docker
git commit -m "feat(retrieval): add the meme-from-text service, endpoint, startup wiring and docs"
```

---

### Task 7: Evaluation additions (`fused` retriever, `tune-weights`, pool source)

**Files:**
- Create: `evaluation/retrievers/fused.py`, `evaluation/tuning.py`, `tests/evaluation/test_tuning.py`, `tests/evaluation/test_fused_retriever.py`
- Modify: `evaluation/__main__.py`, `tests/evaluation/test_cli.py`, `evaluation/gold/README.md`

**Interfaces:**
- Consumes: Tasks 1 to 4, the P6 harness (`evaluation.metrics.query_metrics`, `evaluation.gold`), `repository.meme_retrieval.MemeRetrievalRepository`.
- Produces: `evaluation.retrievers.fused.FusedRetriever(session, pipeline)` (`name = "fused"`; `retrieve(query, corpus_ids, k)` runs `pipeline.search` restricted to the corpus, returns `[(image_id, score)]`; `config` records the model and weights); `evaluation.tuning`: `PreparedQuery(query_id, language, view, candidates, lexical_active, grades)`, `evaluate_weights(prepared, config, similarity, weights, k=10) -> float | None`, `coordinate_ascent(prepared, config, similarity, start, *, grid=GRID, passes=3, k=10) -> (weights, score)`, `stratified_folds(items, folds, seed) -> list[list]`, `cross_validate(prepared, config, similarity, *, folds=5, seed=7, k=10) -> dict`, `tune(prepared, config, similarity, *, folds=5, seed=7, k=10) -> dict` (keys `defaultWeights`, `defaultScore`, `recommendedWeights`, `inSampleScore`, `cvScore`, `foldScores`, `foldSpread`), `GRID`; CLI: `python -m evaluation tune-weights --parse-model M --label L [--folds 5] [--seed 7] [--force]` (needs the read-only database role and Ollama; writes `<gold-dir>/tuning/<label>.json` and prints the YAML snippet), `run --retriever fused --parse-model M`, `build-pools --sources fused --parse-model M`; module function `_build_pipeline(parse_model) -> MemeFromTextPipeline`.

- [ ] **Step 1: Write the failing tests**

Create `tests/evaluation/test_tuning.py`:

```python
import pytest

from evaluation.tuning import (
    GRID, PreparedQuery, coordinate_ascent, cross_validate, evaluate_weights, stratified_folds, tune,
)
from retrieval.model import QueryView
from tests.retrieval.helpers import WEIGHTS, make_candidate, make_config, make_similarity

SIM = make_similarity()
CONFIG = make_config()


def _query(index, language="en"):
    """Situation similarity prefers the WRONG meme, the emotion axis prefers the RIGHT one."""
    view = QueryView(situation="x", axes={"emotion": (("anger", 0.9),)}, language=language, degraded=False)
    right = make_candidate(f"r{index}", cosine=0.55, values={"emotion": (("anger", 0.9),)})
    wrong = make_candidate(f"w{index}", cosine=0.85, values={"emotion": (("joy", 0.9),)})
    return PreparedQuery(f"q{index}", language, view, (wrong, right), False, {f"r{index}": 2, f"w{index}": 0})


def _prepared(count=10):
    languages = ["en", "ru", "es"]
    return [_query(i, languages[i % 3]) for i in range(count)]


def test_evaluate_weights_scores_the_ranking_with_the_given_weights():
    prepared = _prepared(3)
    situation_heavy = {**WEIGHTS, "situation": 1.0, "emotion": 0.0}
    emotion_heavy = {**WEIGHTS, "situation": 0.0, "emotion": 1.0}

    assert evaluate_weights(prepared, CONFIG, SIM, emotion_heavy) == pytest.approx(1.0)
    assert evaluate_weights(prepared, CONFIG, SIM, situation_heavy) < 1.0


def test_queries_without_a_judged_positive_are_ignored():
    view = QueryView("x", {}, "en", False)
    empty = PreparedQuery("q", "en", view, (make_candidate("a"),), False, {"a": 0})
    assert evaluate_weights([empty], CONFIG, SIM, dict(WEIGHTS)) is None


def test_coordinate_ascent_improves_a_bad_start_and_is_deterministic():
    prepared = _prepared(6)
    bad = {**WEIGHTS, "situation": 0.6, "emotion": 0.05}

    first = coordinate_ascent(prepared, CONFIG, SIM, bad)
    again = coordinate_ascent(prepared, CONFIG, SIM, bad)

    assert first == again
    assert first[1] > evaluate_weights(prepared, CONFIG, SIM, bad)
    assert first[1] == pytest.approx(1.0)
    assert set(first[0]) == set(WEIGHTS) and sum(first[0].values()) > 0 and all(w in GRID for w in first[0].values())


def test_stratified_folds_partition_every_item_once_and_balance_languages():
    items = _prepared(15)

    folds = stratified_folds(items, 5, seed=7)

    assert len(folds) == 5 and sorted(i.query_id for f in folds for i in f) == sorted(i.query_id for i in items)
    for fold in folds:
        assert {i.language for i in fold} == {"en", "ru", "es"}
    assert [[i.query_id for i in f] for f in stratified_folds(items, 5, seed=7)] == [[i.query_id for i in f] for f in folds]
    assert [[i.query_id for i in f] for f in stratified_folds(items, 5, seed=8)] != [[i.query_id for i in f] for f in folds]


def test_cross_validation_reports_held_out_scores_per_fold():
    report = cross_validate(_prepared(10), CONFIG, SIM, folds=5, seed=7)

    assert len(report["foldScores"]) == 5 and report["cvScore"] == pytest.approx(sum(report["foldScores"]) / 5)
    assert report["foldSpread"] == pytest.approx(max(report["foldScores"]) - min(report["foldScores"]))


def test_tune_returns_recommended_weights_with_in_sample_and_cross_validated_scores():
    report = tune(_prepared(10), CONFIG, SIM, folds=5, seed=7)

    assert set(report) == {"defaultWeights", "defaultScore", "recommendedWeights", "inSampleScore", "cvScore",
                           "foldScores", "foldSpread"}
    assert report["inSampleScore"] >= report["defaultScore"] and report["cvScore"] > 0.5
    assert report["defaultWeights"] == WEIGHTS
```

Create `tests/evaluation/test_fused_retriever.py`:

```python
import uuid

import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.fused import FusedRetriever
from retrieval.facets import RankedItem
from retrieval.model import ScoredCandidate
from retrieval.pipeline import SearchResult
from tests.retrieval.helpers import make_candidate, make_config

IMG_A, IMG_B = str(uuid.uuid4()), str(uuid.uuid4())


class FakePipeline:
    def __init__(self):
        self.requests = []
        self._config = make_config()

    async def search(self, request, repository):
        self.requests.append((request, repository))
        items = [RankedItem(ScoredCandidate(make_candidate(IMG_A), 0.9, ()), None),
                 RankedItem(ScoredCandidate(make_candidate(IMG_B), 0.5, ()), None)]
        return SearchResult(items[:request.limit], {}, None, False, {}, False, "none", {})


@pytest.mark.asyncio
async def test_retrieve_runs_the_pipeline_restricted_to_the_corpus_and_returns_scored_ids():
    pipeline = FakePipeline()
    retriever = FusedRetriever(object(), pipeline, repository_factory=lambda session: "repo")
    query = GoldQuery("q1", "ru", "привет", "short", "easy", ExpectedProfile("s", (), (), (), ()), True)

    result = await retriever.retrieve(query, [IMG_A, IMG_B], 1)

    assert result == [(IMG_A, 0.9)]
    request, repository = pipeline.requests[0]
    assert request.text == "привет" and request.language_hint == "ru" and request.limit == 1
    assert request.restrict_ids == frozenset({uuid.UUID(IMG_A), uuid.UUID(IMG_B)}) and repository == "repo"
    assert retriever.name == "fused" and "weights" in retriever.config
```

Append to `tests/evaluation/test_cli.py`:

```python
def test_fused_is_a_retriever_and_a_pool_source_that_need_a_parse_model(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gold = _gold_dir(tmp_path)

    assert cli.main(["--gold-dir", str(gold), "run", "--retriever", "fused", "--label", "x"]) == 1
    assert "--parse-model" in capsys.readouterr().err
    args = cli.build_parser().parse_args(["build-pools", "--round", "3", "--sources", "fused", "--parse-model", "m"])
    assert cli.validate_pool_args(args) is None
    args = cli.build_parser().parse_args(["build-pools", "--round", "3", "--sources", "fused"])
    assert "--parse-model" in cli.validate_pool_args(args)


def test_tune_weights_options_and_refusal_to_overwrite(tmp_path, capsys):
    args = cli.build_parser().parse_args(["tune-weights", "--parse-model", "m", "--label", "l", "--folds", "3",
                                          "--seed", "9", "--force"])
    assert (args.parse_model, args.label, args.folds, args.seed, args.force) == ("m", "l", 3, 9, True)

    gold = _gold_dir(tmp_path)
    (gold / "tuning").mkdir()
    (gold / "tuning" / "l.json").write_text("{}", encoding="utf-8")
    code = cli.main(["--gold-dir", str(gold), "tune-weights", "--parse-model", "m", "--label", "l"])
    assert code == 1 and "already exists" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify failure**

Run: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/evaluation -q` → the new tests FAIL / collection errors (`evaluation.tuning`, `evaluation.retrievers.fused` missing).

- [ ] **Step 3: Write `evaluation/retrievers/fused.py` and `evaluation/tuning.py`**

`evaluation/retrievers/fused.py`:

```python
import uuid
from typing import Callable, Sequence

from evaluation.gold import GoldQuery


def _default_repository(session):
    from repository.meme_retrieval import MemeRetrievalRepository
    return MemeRetrievalRepository(session)


class FusedRetriever:
    """The P4 pipeline (parse, embed, candidates, fusion) restricted to the gold corpus: the system under test."""

    name = "fused"

    def __init__(self, session, pipeline, repository_factory: Callable = _default_repository):
        self._session = session
        self._pipeline = pipeline
        self._repository_factory = repository_factory
        config = getattr(pipeline, "_config", None)
        self.config = {"weights": dict(config.weights) if config is not None else {},
                       "embeddingModel": getattr(config, "embedding_model", "")}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        from retrieval.pipeline import SearchRequest
        request = SearchRequest(text=query.text, language_hint=query.language, limit=k,
                                restrict_ids=frozenset(uuid.UUID(image_id) for image_id in corpus_ids))
        result = await self._pipeline.search(request, self._repository_factory(self._session))
        return [(item.scored.candidate.image_id, item.scored.score) for item in result.items][:k]
```

`evaluation/tuning.py`:

```python
"""Weight tuning on the gold set (P4 spec, decision 10). Pure: stdlib, evaluation.metrics and retrieval.

Candidates are collected once per query; every objective evaluation re-fuses them in memory."""
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from evaluation.metrics import mean, query_metrics
from retrieval.config import RetrievalConfig, WEIGHT_KEYS
from retrieval.facets import collapse_clusters
from retrieval.fusion import fuse, rank_candidates
from retrieval.model import Candidate, QueryView
from retrieval.similarity import SimilarityIndex

GRID = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6)


@dataclass(frozen=True)
class PreparedQuery:
    query_id: str
    language: str
    view: QueryView
    candidates: Sequence[Candidate]
    lexical_active: bool
    grades: Mapping[str, int]


def evaluate_weights(prepared: Sequence[PreparedQuery], config: RetrievalConfig, similarity: SimilarityIndex,
                     weights: Mapping[str, float], k: int = 10) -> float | None:
    """Mean nDCG@k over the queries that have a judged positive; None when there is none."""
    tuned = config.with_weights(weights)
    scores = []
    for item in prepared:
        scored = [fuse(candidate, item.view, tuned, similarity, lexical_active=item.lexical_active)
                  for candidate in item.candidates]
        ids = [entry.scored.candidate.image_id for entry in collapse_clusters(rank_candidates(scored))][:k]
        metrics = query_metrics(ids, item.grades, k=k)
        if metrics.ndcg is not None:
            scores.append(metrics.ndcg)
    return mean(scores)


def coordinate_ascent(prepared, config, similarity, start: Mapping[str, float], *, grid=GRID, passes: int = 3,
                      k: int = 10) -> tuple[dict, float]:
    """One weight at a time over the grid, keeping the best (ties keep the current value); a few passes."""
    weights = dict(start)
    best = evaluate_weights(prepared, config, similarity, weights, k) or 0.0
    for _pass in range(passes):
        improved = False
        for name in WEIGHT_KEYS:
            for value in grid:
                if value == weights[name]:
                    continue
                trial = {**weights, name: value}
                if sum(trial.values()) <= 0:
                    continue
                score = evaluate_weights(prepared, config, similarity, trial, k) or 0.0
                if score > best + 1e-9:
                    weights, best, improved = trial, score, True
        if not improved:
            break
    return weights, best


def stratified_folds(items: Sequence, folds: int, seed: int) -> list[list]:
    """Shuffle (seeded) within each language and deal round-robin so every fold has every language."""
    rng = random.Random(seed)
    by_language: dict[str, list] = {}
    for item in items:
        by_language.setdefault(item.language, []).append(item)
    result: list[list] = [[] for _ in range(folds)]
    position = 0
    for language in sorted(by_language):
        group = sorted(by_language[language], key=lambda item: item.query_id)
        rng.shuffle(group)
        for item in group:
            result[position % folds].append(item)
            position += 1
    return result


def cross_validate(prepared, config, similarity, *, folds: int = 5, seed: int = 7, k: int = 10) -> dict:
    parts = stratified_folds(prepared, folds, seed)
    scores = []
    for index, held_out in enumerate(parts):
        training = [item for other, part in enumerate(parts) if other != index for item in part]
        weights, _score = coordinate_ascent(training, config, similarity, config.weights, k=k)
        scores.append(evaluate_weights(held_out, config, similarity, weights, k) or 0.0)
    return {"foldScores": scores, "cvScore": sum(scores) / len(scores), "foldSpread": max(scores) - min(scores)}


def tune(prepared, config, similarity, *, folds: int = 5, seed: int = 7, k: int = 10) -> dict:
    default_score = evaluate_weights(prepared, config, similarity, config.weights, k) or 0.0
    weights, in_sample = coordinate_ascent(prepared, config, similarity, config.weights, k=k)
    cv = cross_validate(prepared, config, similarity, folds=folds, seed=seed, k=k)
    return {"defaultWeights": dict(config.weights), "defaultScore": default_score, "recommendedWeights": weights,
            "inSampleScore": in_sample, **cv}
```

- [ ] **Step 4: Extend `evaluation/__main__.py`**

(a) Add `"fused"` to `RETRIEVER_NAMES` and `POOL_SOURCE_CHOICES` (`POOL_SOURCE_CHOICES = POOL_SOURCES + ("parsed_axis_match", "fused")`); update `validate_pool_args` so that **either** `parsed_axis_match` or `fused` in the sources requires `--parse-model` (message `"--parse-model is required when parsed_axis_match or fused is a pool source"`, and keep the existing test's expectation of the substring `--parse-model`); make `cmd_run` require `--parse-model` for both (`if args.retriever in ("parsed_axis_match", "fused") and not args.parse_model:` with message `"--parse-model is required for the parsed_axis_match and fused retrievers"`).

(b) Add a lazily importing builder next to `_build_parser`:

```python
def _build_pipeline(parse_model: str):
    """The production pipeline for evaluation: the tracked parse.* and search.meme_from_text.* settings with the
    parser model taken from the command line."""
    from config.settings import settings
    from parse.config import load_config as load_parse_config
    from parse.parser import QueryParser
    from retrieval.config import load_config
    from retrieval.embed import OllamaEmbedClient
    from retrieval.pipeline import MemeFromTextPipeline
    from retrieval.similarity import load_similarity

    def get(key, default=None):
        return parse_model if key == "parse.model" else settings.get(key, default)

    parser = QueryParser(load_parse_config(get))
    config = load_config(settings.get)
    return MemeFromTextPipeline(config, parser, OllamaEmbedClient(config.ollama_url), load_similarity(),
                                max_input_chars=int(settings.get("parse.max_input_chars")))
```

(c) In `_make_retriever` add before the final `raise`:

```python
    if name == "fused":
        from evaluation.retrievers.fused import FusedRetriever
        return FusedRetriever(session, _build_pipeline(parse_model))
```

(d) `cmd_tune_weights` (async, before the entry point section):

```python
async def cmd_tune_weights(args, gold: Path) -> int:
    """Collect the candidates of every reviewed query once (live Ollama and the read-only database), then tune
    the six weights with cross-validation. Prints the recommended settings; committing them is a human step."""
    target = gold / "tuning" / f"{args.label}.json"
    if target.exists() and not args.force:
        return _error(f"{target} already exists (use --force to replace it)")
    require_readonly()
    import json
    from evaluation.tuning import PreparedQuery, tune
    from repository.meme_retrieval import MemeRetrievalRepository
    from retrieval.pipeline import SearchRequest
    from retrieval.similarity import load_similarity
    from Storage.db import AsyncSessionLocal
    corpus = load_corpus(gold / "corpus.json")
    queries = [q for q in load_queries(gold / "queries.json") if q.reviewed]
    judged = judgments_by_query(load_judgments(gold / "judgments.json"))
    corpus_ids = frozenset(uuid.UUID(image_id) for image_id in corpus.image_ids())
    pipeline = _build_pipeline(args.parse_model)
    prepared = []
    async with AsyncSessionLocal() as session:
        await _check_corpus(session, corpus, check_digest=True)
        repository = MemeRetrievalRepository(session)
        await pipeline.warm_up()
        for query in queries:
            item = await pipeline.prepare(SearchRequest(text=query.text, language_hint=query.language,
                                                        restrict_ids=corpus_ids), repository)
            prepared.append(PreparedQuery(query.id, query.language, item.view, tuple(item.candidates),
                                          item.lexical_active, judged.get(query.id, {})))
    report = tune(prepared, pipeline._config, load_similarity(), folds=args.folds, seed=args.seed)
    report.update({"label": args.label, "date": date_class.today().isoformat(), "parseModel": args.parse_model,
                   "queries": len(prepared), "queriesSha": file_sha256(gold / "queries.json"),
                   "judgmentsSha": file_sha256(gold / "judgments.json")})
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    weights = report["recommendedWeights"]
    print(f"default weights nDCG@10 {report['defaultScore']:.3f}; tuned in-sample {report['inSampleScore']:.3f}; "
          f"cross-validated {report['cvScore']:.3f} (folds {', '.join(f'{s:.3f}' for s in report['foldScores'])}, "
          f"spread {report['foldSpread']:.3f})")
    print("settings.yaml snippet:\n  weights: {" + ", ".join(f"{name}: {weights[name]}" for name in weights) + "}")
    print(f"wrote {target}")
    return 0
```
(add `import uuid` at the top of `__main__.py` if it is not already imported at module level).

(e) `build_parser`: add the subcommand

```python
    tune_cmd = commands.add_parser("tune-weights")
    tune_cmd.add_argument("--parse-model", required=True)
    tune_cmd.add_argument("--label", required=True)
    tune_cmd.add_argument("--folds", type=int, default=5)
    tune_cmd.add_argument("--seed", type=int, default=7)
    tune_cmd.add_argument("--force", action="store_true")
```
and register `"tune-weights": cmd_tune_weights` in `async_handlers`. Add one README sentence to `evaluation/gold/README.md`: "`tune-weights` collects the fused pipeline's candidates once and searches the fusion weights with cross-validation (`tuning/<label>.json`); `run --retriever fused --parse-model M` evaluates the production pipeline restricted to the corpus."

- [ ] **Step 5: Run the tests**

Each as its own command: `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/evaluation -q`; `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`; `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m evaluation tune-weights --help` (exit 0). Do not run `tune-weights`, `run` or `build-pools` for real (live Ollama and the read-only database are Task 9).

- [ ] **Step 6: Commit**

```bash
git add evaluation tests/evaluation
git commit -m "feat(evaluation): add the fused retriever, weight tuning and the fused pool source"
```

---

### Task 8: Synthetic load check (test database only)

**Files:**
- Create: `tests/load/__init__.py` (empty), `tests/load/synthetic_load_check.py`, `tests/load/README.md`

**Interfaces:**
- Produces: `python tests/load/synthetic_load_check.py [--images 25000] [--queries 50] [--keep]` from the repo root with `DATABASE_URL` pointing at `ocrdb_test`: inserts N synthetic images with profiles, values, normalised random situation vectors and a duplicate-cluster sample, runs the candidate SQL plus scoring, fusion, collapse and facets for the queries, prints p50 and p95 of the retrieval stage and PASS or FAIL against 300 ms p95, and deletes its rows (`filename LIKE 'synthetic-load-%'`; cascades remove profiles) unless `--keep`. It refuses to run unless `DATABASE_URL` contains `ocrdb_test`. Not collected by pytest (the file is not named `test_*.py`).

- [ ] **Step 1: Write the script**

```python
"""Synthetic load check for the P4 retrieval stage (spec decision 11). TEST DATABASE ONLY.

    DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" \
        python tests/load/synthetic_load_check.py --images 25000 --queries 50

Fills ocrdb_test with synthetic images, profiles and normalised random situation vectors, measures the retrieval
stage (candidate SQL, scoring, fusion, collapse, facets; no parsing, no embedding call) and removes its rows.
It never runs against anything but a database whose URL contains `ocrdb_test`."""
import argparse
import asyncio
import os
import random
import sys
import time
import uuid

import numpy as np
from sqlalchemy import delete, func, insert, select

if "ocrdb_test" not in os.environ.get("DATABASE_URL", ""):
    sys.exit("Refusing to run: DATABASE_URL must point at the test database (ocrdb_test).")

from repository.meme_retrieval import MemeRetrievalRepository                      # noqa: E402
from retrieval.config import AXES                                                    # noqa: E402
from retrieval.facets import collapse_clusters, facet_counts                         # noqa: E402
from retrieval.fusion import fuse, rank_candidates                                   # noqa: E402
from retrieval.model import Filters, QueryView                                       # noqa: E402
from retrieval.similarity import load_similarity                                     # noqa: E402
from Storage.db import AsyncSessionLocal                                             # noqa: E402
from Storage.models import (                                                         # noqa: E402
    PROFILE_EMBEDDING_DIM, Image, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue, TmpImageClusters,
)
from taxonomy import load                                                            # noqa: E402

PREFIX = "synthetic-load-"
MODEL = "bge-large"
BATCH = 1000


def _config():
    sys.path.insert(0, ".")
    from tests.retrieval.helpers import make_config
    return make_config()


async def _populate(session, count: int, rng: random.Random, np_rng, tax) -> None:
    keys = {axis: list(tax.values[axis]) for axis in AXES}
    for start in range(0, count, BATCH):
        size = min(BATCH, count - start)
        ids = [uuid.uuid4() for _ in range(size)]
        await session.execute(insert(Image), [
            {"id": image_id, "filename": f"{PREFIX}{image_id}.jpg", "status": "active"} for image_id in ids])
        await session.execute(insert(MemeProfile), [
            {"image_id": image_id, "taxonomy_version": tax.version, "extractor_model": "synthetic",
             "extractor_prompt_version": "synthetic", "inputs_fingerprint": "0" * 64,
             "situation_text": "synthetic situation", "use_when": "Use when synthetic."} for image_id in ids])
        values = []
        for image_id in ids:
            for axis in AXES:
                for key in rng.sample(keys[axis], rng.randint(1, 2)):
                    values.append({"image_id": image_id, "axis": axis, "value_key": key,
                                   "confidence": rng.choice([0.3, 0.6, 0.9]), "source": "model"})
        await session.execute(insert(MemeProfileValue), values)
        vectors = np_rng.standard_normal((size, PROFILE_EMBEDDING_DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        await session.execute(insert(MemeProfileSituationEmbedding), [
            {"image_id": image_id, "embedding": vector.tolist(), "model_name": MODEL, "model_digest": "synthetic"}
            for image_id, vector in zip(ids, vectors)])
        await session.execute(insert(TmpImageClusters), [
            {"cluster_id": 990000 + (start + index) // 3, "image_id": image_id}
            for index, image_id in enumerate(ids) if index % 10 == 0 or index % 10 == 1])
        await session.commit()
        print(f"  inserted {start + size} / {count}", flush=True)


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        ids = select(Image.id).where(Image.filename.like(f"{PREFIX}%"))
        await session.execute(delete(TmpImageClusters).where(TmpImageClusters.image_id.in_(ids)))
        await session.execute(delete(Image).where(Image.filename.like(f"{PREFIX}%")))
        await session.commit()


async def main(images: int, queries: int, keep: bool) -> int:
    tax, similarity, config = load(), load_similarity(), _config()
    rng, np_rng = random.Random(7), np.random.default_rng(7)
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(func.count()).select_from(Image).where(Image.filename.like(f"{PREFIX}%")))).scalar_one()
    if existing:
        print(f"removing {existing} leftover synthetic rows"); await _cleanup()
    try:
        async with AsyncSessionLocal() as session:
            print(f"populating {images} synthetic images"); await _populate(session, images, rng, np_rng, tax)
            await session.execute(select(1))
        timings = []
        keys = {axis: list(tax.values[axis]) for axis in AXES}
        async with AsyncSessionLocal() as session:
            repository = MemeRetrievalRepository(session)
            for _ in range(queries):
                vector = np_rng.standard_normal(PROFILE_EMBEDDING_DIM).astype(np.float32)
                vector = (vector / np.linalg.norm(vector)).tolist()
                axes = {axis: tuple((key, rng.choice([0.3, 0.6, 0.9])) for key in rng.sample(keys[axis], 2))
                        for axis in AXES}
                view = QueryView("synthetic query", axes, "en", False)
                started = time.perf_counter()
                candidates = await repository.generate_candidates(
                    query_vector=vector, query_axes=axes, neighbours=similarity.neighbours_for(axes),
                    lexical_text=None, filters=Filters(), restrict_ids=None, model_name=MODEL,
                    situation_k=config.situation_k, axis_k=config.axis_k, lexical_k=config.lexical_k,
                    total_cap=config.total_cap)
                scored = [fuse(c, view, config, similarity, lexical_active=False) for c in candidates]
                items = collapse_clusters(rank_candidates(scored))
                facet_counts(items)
                timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        p50 = timings[len(timings) // 2]
        p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
        verdict = "PASS" if p95 < 300 else "FAIL"
        print(f"{images} images, {queries} queries: retrieval p50 {p50:.0f} ms, p95 {p95:.0f} ms "
              f"(candidates {len(candidates)}) -> {verdict} (budget: p95 under 300 ms)")
        return 0 if verdict == "PASS" else 1
    finally:
        if not keep:
            print("removing synthetic rows"); await _cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=25000)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--keep", action="store_true")
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.images, arguments.queries, arguments.keep)))
```

`tests/load/README.md`: three lines saying what the script does, that it needs `DATABASE_URL` for `ocrdb_test` only, and the command above.

- [ ] **Step 2: Smoke-run it small, then at full size**

Run from the repo root: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONPATH=. /h/workspace_sandbox/memes/.venv311/Scripts/python.exe tests/load/synthetic_load_check.py --images 2000 --queries 10` (expect a PASS line and `removing synthetic rows`), then the same with `--images 25000 --queries 50` (expect it to finish within a few minutes; the HNSW index makes the insert the slow part). Report the printed p50 and p95 in your report. If a column name differs from the model (for example `Image` requires another non-null column), adjust the inserts and say so. Afterwards confirm cleanup: `select count(*) from images where filename like 'synthetic-load-%'` is 0 (run it with a short Python snippet against the test database).

- [ ] **Step 3: Make sure the normal test roots are unaffected and commit**

Run `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q` and `DATABASE_URL=... python -m pytest tests/integration/ -q` (a leftover synthetic row would show up there; there must be none).

```bash
git add tests/load
git commit -m "test(retrieval): add the synthetic 25,000-profile load check (test database only)"
```

---

### Task 9: Tuning, round 3, thresholds, latency and documentation (controller and user)

Live steps (Ollama, the read-only database role, the user's judging time). **Do not delegate any of it.**

**Files:**
- Create (committed): `evaluation/gold/tuning/<label>.json`, `evaluation/gold/baselines/fused*.json`, `docs/superpowers/reviews/2026-10-07-p4-retrieval-fusion-and-api-review.md`
- Modify: `environments/settings.yaml` (tuned weights), `evaluation/gold/judgments.json` (round 3), the P4 spec (addendum, status), `tracker.md`, `CLAUDE.md`, `evaluation/gold/README.md`

- [ ] **Step 1: Default-weights baseline**

With the read-only URL (`DATABASE_URL_READONLY` of `environments/.env.general`, read with `Get-Content -Encoding UTF8`, never printed; `APP_ENV=general`; Ollama running with `qwen2.5:7b-instruct` and `bge-large`): `python -m evaluation run --retriever fused --parse-model qwen2.5:7b-instruct --label fused-defaults`, then `compare parsed_qwen25-7b fused-defaults`. Record nDCG@10, hit@10, per language, difficulty, length and `judged@10`.

- [ ] **Step 2: Tune the weights**

`python -m evaluation tune-weights --parse-model qwen2.5:7b-instruct --label tune-1`. Review the printed in-sample score, the cross-validated score and the fold spread (a large gap between in-sample and cross-validated means overfitting: prefer the default or a rounded compromise). The weights go into `environments/settings.yaml` (`search.meme_from_text.weights`) only after review; round them to two decimals and re-run `run --retriever fused --label fused-tuned` to confirm the stored score.

- [ ] **Step 3: Round-3 pools and judging**

`python -m evaluation build-pools --round 3 --sources fused --parse-model qwen2.5:7b-instruct --random 0 --depth 10 --per-query 10 --pools-dir H:\workspace_sandbox\memes\evaluation\work` (writes `pools-r3.json`: the unjudged members of the tuned fused top ten), `python -m evaluation sheet --round 3 --pools-dir ... --out-dir ...\sheets --base-path <BASE_PATH> --rater <name> --date <today>`, verify blinding (no retriever or profile words outside the base64 data), open the three sheets for the user and **stop and wait**. Tell the user that the exports are named `judgments-<language>.json` and that the browser appends `(1)`, `(2)` when earlier files exist: import only the files whose payload says `"round": 3` (read each file's `round`, `language`, `judgments` count first), then `import-judgments` (the report must show `overwritten 0`).

- [ ] **Step 3b: Re-run all baselines under the new judgments**

Re-run `random`, `lexical_and`, `lexical_or`, `situation_embedding`, `axis_match`, `parsed_qwen25-7b` (parsed_axis_match with `--parse-model qwen2.5:7b-instruct`) and `fused` (`--force`, label `fused`) so all baselines share the same gold hashes, then `list-baselines`.

- [ ] **Step 4: Check the approved thresholds**

On the final judgments: overall nDCG@10 at least 0.50 and hit@10 at least 0.95; every language at least 0.85 of English and at least 0.30; no query class below the best baseline's value for that class (take the best among the other baselines per class from the re-run). Any miss is reported with the numbers and options (tune again, adjust the candidate caps or the neutral score, re-weigh) before declaring anything; never edit a threshold.

- [ ] **Step 5: Latency**

Retrieval stage on the gold corpus: the `meta.retrievalMs` values of 60 `fused` searches (add them to the tuning or run output) and the end-to-end time with the warm parser; the synthetic result from Task 8 (25,000 profiles). Record p50 and p95.

- [ ] **Step 6: Documentation, review report and final verification**

Append `## Addendum: result (<date>)` to the P4 spec (tuned weights with the cross-validated and in-sample scores, the baseline table before and after, the threshold check per language and class, latency numbers, deviations, handoff notes for P5: endpoint contract, degraded contract, `meta.available`, cursor behaviour, facet semantics, filters, edited profile flow, explanation fields), set `Status: done`, update `tracker.md` (P4 row `done`, Next step), `CLAUDE.md` (an entry for `retrieval/`, the endpoint, `search.meme_from_text.*`, `tune-weights`, `run --retriever fused`, the load check), `evaluation/gold/README.md` (round 3), and create the review report against the spec's eight acceptance criteria. Run each root as its own command (`tests/retrieval`, `tests/evaluation`, `tests/parse`, `tests/taxonomy`, `tests/ai`, `batch/tests`, the whole `tests/integration/`, `Backend`, `tests/docker`); all must pass. Commit with the standard trailers.

---

## Self-review (plan against spec)

| Spec item | Task |
|---|---|
| Decision 1 modules, pure import guard, Docker copy | 1 to 4, 6 |
| Decision 2 request/flow/caching, edited profile, breaker, embed client, result cache | 2, 4 |
| Decision 3 candidate generation, eligible set, filters, lexical-only unprofiled images | 2 (`lexical_or_scores`), 3 |
| Decision 4 scoring and fusion, neutral rule, explanations | 1 |
| Decision 5 filters, facets, diversity | 1 (facets), 3 (SQL filters), 4 |
| Decision 6 cursor | 1, 4 |
| Decision 7 degraded paths, `it`, startup warm-up | 4, 6 |
| Decision 8 tools | 4 |
| Decision 9 schemas, generated types, `backend_api.md`, router | 5, 6 |
| Decision 10 `fused`, `tune-weights`, round 3, thresholds | 7, 9 |
| Decision 11 synthetic load check | 8 |
| Decision 12 settings | 1 (loader), 5 (YAML) |
| Testing section | tests in every task; whole integration root in 2 and 3; docker in 6 |
| Acceptance 1 to 3 | 1 to 6 |
| Acceptance 4 to 7 | 9 |
| Acceptance 8 | 6, 9 |

Deviations from the spec text, so reviewers do not rediscover them: the vector top-K query sets `hnsw.ef_search` as well as `hnsw.iterative_scan` (HNSW returns at most `ef_search` rows without it); candidate-source ordering is round-robin by rank (`merge_ranked`) rather than "best source rank" with ties unspecified; `clusterSize` counts the candidates of the cluster inside the candidate set (not the whole library); the process-wide pipeline factory lives in `Backend/app/services/meme_from_text_pipeline.py` because the pure package cannot import `config.settings`; the endpoint answers 422 for an invalid edited profile with the validator's messages in `detail`.

Type and name consistency checks: `Candidate`, `QueryView`, `Filters`, `ScoredCandidate`, `RankedItem`, `SearchRequest`, `SearchResult`, `Prepared` are each defined once and imported where used; the repository's `generate_candidates` keyword names are exactly those the pipeline passes and the tests' fakes accept; `RetrievalConfig` attributes match `load_config`, the YAML keys (spec decision 12) and `tests/retrieval/helpers.make_config`; `explanation_dict`'s camelCase keys match `axisexplanation.schema.json` and `memefromtextresult.schema.json`; `result_item_dict` produces `imageId` which the service replaces by `meme`; the pool source name `fused` and the retriever name `fused` are the same.
