# P6 Evaluation and Gold Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an `evaluation/` package (gold-data formats, metrics, pooling, retriever interface, five baseline retrievers, reports, baselines and compare, a blinded judging sheet and importer), the P2 `--ids-file` option, then produce a frozen 1,500-image corpus, 60 reviewed queries, about 700 human judgments and stored baselines for the meme-from-text epic.

**Architecture:** Pure standard-library modules (`gold`, `metrics`, `pooling`, `runner`, `report`) carry all logic and are unit-tested without a database. Retrievers, the corpus builder and the CLI are the only parts that touch PostgreSQL or Ollama, and they run with the read-only database role. Gold data is committed JSON under `evaluation/gold/`; sheets, pools and file names stay local under `evaluation/work/` (gitignored).

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async + pgvector, the P1 `taxonomy` package, the P2 profile tables and jobs, `ai.ollama_embed.OllamaEmbedder`, Pillow (thumbnails), pytest (`tests/evaluation`, `tests/integration`).

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p6-evaluation-and-gold-set-design.md` (read it first). Inherited: the P1 and P2 specs in the same folder, including the P2 as-built addendum.

## Global Constraints

- Run every command from the repo root with `.venv311` (`H:/workspace_sandbox/memes/.venv311/Scripts/python.exe`; from a worktree use that absolute path, the worktree has no venv). Never `pip install` anything. Run each pytest root as its own command: `tests/evaluation`, `tests/taxonomy`, `tests/ai`, `batch/tests`, `Backend` (from `Backend/`), `tests/integration`; never combine roots (CLAUDE.md gotcha).
- `tests/integration/` needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line (database `ocrdb_test` on the `ocr-db` container). The P2 `--ids-file` change touches `batch/` and `repository/`, so the whole `tests/integration/` root must pass before merge.
- **Live database rules (CLAUDE.md):** never give a subagent the main `DATABASE_URL`, and never any environment's `DATABASE_URL_READONLY`; subagents use only the `ocrdb_test` URL above. Every live step (drawing the corpus, profiling it, building pools, running baselines) is a labelled **controller** step. The evaluation CLI reads only through the read-only role and refuses to run unless `DATABASE_URL` contains `ocr_readonly`. The two P2 jobs that write profiles are run only by the controller with the user's consent.
- **The evaluation CLI must NOT call `config.settings.load_env()`:** that function loads `environments/.env.<env>` with `override=True` and would replace the read-only `DATABASE_URL` with the write-capable one. The CLI requires `APP_ENV` (so the module-level `settings` picks the right overlay yaml) and `DATABASE_URL` (read-only) already in the shell, and `BASE_PATH` via `--base-path`.
- The pure modules `evaluation/gold.py`, `metrics.py`, `pooling.py`, `runner.py`, `report.py` and `evaluation/retrievers/base.py` may import only the standard library, `taxonomy.*` and `evaluation.*` (an AST guard test enforces it). `evaluation/sheet.py` may additionally import Pillow lazily inside a function. `evaluation/retrievers/__init__.py` stays empty so importing `base` never pulls in SQLAlchemy.
- Gold data (all under `evaluation/gold/`, UTF-8, LF, deterministic key order, trailing newline): `corpus.json`, `queries.json`, `judgments.json`, `baselines/<label>.json`. File names, thumbnails and sheets are never committed; baselines never contain file names. Format `version` is 1.
- Grades: 0 wrong, 1 acceptable, 2 great. Unjudged pairs count as 0 in the metrics. Metrics at k = 10: nDCG@10 (gain = grade, discount `1/log2(rank+1)`), recall@10 (positives are grade at least 1, denominator = judged positives of the query), hit@10, judged@10. Queries with no judged positive are excluded from the nDCG, recall and hit means and counted as `noPositive`.
- Corpus: about 1,500 active `general` images, strata and shares `ocr+description` 60%, `ocr only` 20%, `description only` 10%, `no usable text` 10%; excluded: flagged images, images with an unsupported extension (`batch.utils.image_format_filter.has_unsupported_image_extension`), and all but the lowest image id of each duplicate cluster (`tmp_clusters`). At least 95% of the corpus must have a profile before any pool is built or baseline run.
- Queries: 60, 20 per language (`en`, `ru`, `es`), per language 5 short (at most 8 words) / 10 medium (9-20 words) / 5 long (at least 21 words) and 7 easy / 8 hard / 5 ambiguous; all topic keys appear; at least 8 distinct stance, 10 emotion and 6 tone keys across the expected profiles; at least 6 `pairId` groups present in all three languages. Unreviewed queries are excluded from official runs.
- Pools: about 12 candidates per query = 9 round-robin from `lexical_or`, `situation_embedding`, `axis_match` + 3 random, shuffled with a seed derived from the query id, no source labels in anything the user sees.
- Windows: set `PYTHONIOENCODING=utf-8` for scripts that print. Use the Edit tool for existing files (they may be CRLF); never round-trip repo text through PowerShell `Get/Set-Content`. Long Ollama runs go detached (`Start-Process`) or stay under the ~10-minute tool limit.
- Commit messages end with the attribution lines the session specifies.

## File Structure

| Path | Responsibility |
|---|---|
| `evaluation/__init__.py`, `evaluation/retrievers/__init__.py` (create) | Empty package markers |
| `evaluation/gold.py` (create) | Dataclasses, parsing and validation, query lint, deterministic IO, hashes, judgment merge and export import |
| `evaluation/metrics.py` (create) | `query_metrics`, `aggregate`, `mean`, `language_ratio` |
| `evaluation/pooling.py` (create) | `Pool`, `build_pool` |
| `evaluation/runner.py` (create) | `QueryResult`, `run_retriever`, `summarize` |
| `evaluation/report.py` (create) | Baseline file, Markdown report, `compare_baselines`, pooling-bias counts, `git_commit` |
| `evaluation/retrievers/base.py` (create) | `Retriever` protocol |
| `evaluation/retrievers/random_retriever.py`, `lexical.py`, `situation_embedding.py`, `axis_match.py` (create) | The baseline retrievers |
| `evaluation/corpus.py` (create) | Quotas, `select_corpus`, `load_candidates`, ids-file writer |
| `evaluation/state.py` (create) | `collect_profile_state` (database read) |
| `evaluation/sheet.py` (create) | Judging sheet renderer and thumbnails |
| `evaluation/__main__.py` (create) | CLI |
| `batch/utils/ids_file.py` (create) | `load_ids_file` |
| `repository/meme_profiles.py`, `repository/image_procesing_status.py` (modify) | Optional image-id filters |
| `batch/build_meme_profiles.py`, `batch/build_meme_profile_embeddings.py` (modify) | `--ids-file` |
| `.gitignore` (modify) | Ignore `evaluation/work/` |
| `evaluation/gold/README.md`, `corpus.json`, `queries.json`, `judgments.json`, `baselines/*.json` (create in controller tasks) | Gold data |
| tests (create) | `tests/evaluation/` (pure), `tests/integration/test_evaluation_retrievers.py`, `test_evaluation_corpus.py`, plus additions to the P2 job tests |

---

### Task 1: Gold formats and metrics

**Files:**
- Create: `evaluation/__init__.py`, `evaluation/retrievers/__init__.py` (both empty, create with `touch`), `evaluation/gold.py`, `evaluation/metrics.py`
- Create: `tests/evaluation/__init__.py` (empty), `tests/evaluation/test_gold.py`, `tests/evaluation/test_metrics.py`

**Interfaces:**
- Produces: `evaluation.gold`: `GoldError(ValueError)`, constants `LANGUAGES = ("en","ru","es")`, `LENGTHS`, `DIFFICULTIES`, `STRATA = ("ocr+description","ocr only","description only","no usable text")`, `GRADES = (0,1,2)`; frozen dataclasses `CorpusImage(image_id: str, content_hash: str | None, stratum: str)`, `Corpus(environment, seed, created, images: tuple[CorpusImage, ...], version=1)` with `image_ids()` and `strata()`, `ExpectedProfile(situation: str, emotion, stance, topic, tone: tuple[str, ...])` with `axis_values()`, `GoldQuery(id, language, text, length, difficulty, expected, reviewed=False, pair_id=None)`, `Judgment(query_id, image_id, grade, rater, date, round)`, `MergeResult(merged, added, overwritten, unchanged)`; functions `parse_corpus(data) -> Corpus`, `corpus_to_dict`, `parse_queries(data) -> list[GoldQuery]`, `queries_to_dict`, `parse_judgments(data) -> list[Judgment]`, `judgments_to_dict`, `load_corpus/save_corpus`, `load_queries/save_queries`, `load_judgments/save_judgments` (path-based), `file_sha256(path) -> str`, `check_queries(queries, tax) -> list[str]`, `lint_query_set(queries, tax, **quotas) -> list[str]`, `merge_judgments(existing, incoming) -> MergeResult`, `judgments_by_query(judgments) -> dict[str, dict[str, int]]`, `parse_export(data, *, valid_query_ids, valid_image_ids) -> list[Judgment]`. `evaluation.metrics`: `QueryMetrics(ndcg, recall, hit, judged_share, has_positive)`, `query_metrics(ranked, grades, k=10)`, `mean(values)`, `aggregate(items) -> dict`, `language_ratio(by_language) -> dict`.

- [ ] **Step 1: Write the failing metrics tests**

Create `tests/evaluation/__init__.py` (empty) and `tests/evaluation/test_metrics.py`:

```python
import math

import pytest

from evaluation.metrics import QueryMetrics, aggregate, language_ratio, mean, query_metrics


def test_query_metrics_hand_computed_example():
    # ranked a, b, c; grades a=2 b=0 c=1 and d=2 was never retrieved
    result = query_metrics(["a", "b", "c"], {"a": 2, "b": 0, "c": 1, "d": 2}, k=10)

    dcg = 2 / math.log2(2) + 0 + 1 / math.log2(4)                       # 2.5
    ideal = 2 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)      # grades 2, 2, 1
    assert result.ndcg == pytest.approx(dcg / ideal)
    assert result.recall == pytest.approx(2 / 3)                        # positives a, c, d; found a, c
    assert result.hit == 1.0
    assert result.judged_share == 1.0
    assert result.has_positive is True


def test_perfect_ranking_has_ndcg_one():
    result = query_metrics(["a", "c", "b"], {"a": 2, "b": 0, "c": 1}, k=10)
    assert result.ndcg == pytest.approx(1.0)


def test_unjudged_results_count_as_grade_zero_and_lower_judged_share():
    result = query_metrics(["x", "y", "a"], {"a": 2}, k=10)

    assert result.ndcg == pytest.approx((2 / math.log2(4)) / 2)
    assert result.judged_share == pytest.approx(1 / 3)
    assert result.hit == 1.0


def test_only_the_top_k_counts():
    ranked = ["n1", "n2", "a"]
    assert query_metrics(ranked, {"a": 2}, k=2).hit == 0.0
    assert query_metrics(ranked, {"a": 2}, k=3).hit == 1.0


def test_query_without_a_judged_positive_has_undefined_ranking_metrics():
    result = query_metrics(["a", "b"], {"a": 0, "b": 0}, k=10)

    assert (result.ndcg, result.recall, result.hit) == (None, None, None)
    assert result.has_positive is False
    assert result.judged_share == 1.0


def test_empty_result_list_scores_zero_with_undefined_judged_share():
    result = query_metrics([], {"a": 2}, k=10)

    assert result.ndcg == 0.0 and result.recall == 0.0 and result.hit == 0.0
    assert result.judged_share is None


def test_query_with_no_judgments_at_all_is_excluded_not_zero():
    result = query_metrics(["a"], {}, k=10)
    assert result.has_positive is False and result.ndcg is None


def test_mean_ignores_none_and_is_none_for_nothing():
    assert mean([1.0, None, 3.0]) == 2.0
    assert mean([None, None]) is None
    assert mean([]) is None


def test_aggregate_means_over_defined_values_and_counts_no_positive_queries():
    items = [
        QueryMetrics(1.0, 1.0, 1.0, 1.0, True),
        QueryMetrics(0.0, 0.0, 0.0, 0.5, True),
        QueryMetrics(None, None, None, 1.0, False),
    ]

    summary = aggregate(items)

    assert summary == {"queries": 3, "noPositive": 1, "ndcg": 0.5, "recall": 0.5, "hit": 0.5,
                       "judged": pytest.approx(2.5 / 3)}


def test_language_ratio_is_relative_to_english():
    by_language = {"en": {"ndcg": 0.5}, "ru": {"ndcg": 0.25}, "es": {"ndcg": None}}

    assert language_ratio(by_language) == {"en": 1.0, "ru": 0.5, "es": None}


def test_language_ratio_is_none_everywhere_without_an_english_baseline():
    assert language_ratio({"ru": {"ndcg": 0.5}}) == {"ru": None}
    assert language_ratio({"en": {"ndcg": 0.0}, "ru": {"ndcg": 0.5}}) == {"en": None, "ru": None}
```

- [ ] **Step 2: Write the failing gold tests**

Create `tests/evaluation/test_gold.py`:

```python
import json
from pathlib import Path

import pytest

from evaluation import gold
from evaluation.gold import (
    Corpus, CorpusImage, ExpectedProfile, GoldError, GoldQuery, Judgment, check_queries, file_sha256,
    judgments_by_query, lint_query_set, merge_judgments, parse_corpus, parse_export, parse_judgments,
    parse_queries,
)
from taxonomy import load as load_taxonomy

TAX = load_taxonomy()
UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"


def _expected(**overrides):
    values = dict(situation="someone cancels plans", emotion=("annoyance",), stance=("mock",),
                  topic=("friends",), tone=("sarcastic",))
    values.update(overrides)
    return ExpectedProfile(**values)


def _query(index=1, language="en", length="medium", difficulty="easy", text=None, expected=None, pair_id=None,
           reviewed=True):
    words = {"short": 4, "medium": 12, "long": 25}[length]
    return GoldQuery(id=f"q{index:03d}", language=language, text=text or " ".join(["word"] * words),
                     length=length, difficulty=difficulty, expected=expected or _expected(), reviewed=reviewed,
                     pair_id=pair_id)


class TestCorpus:
    def test_round_trip_preserves_everything(self, tmp_path):
        corpus = Corpus(environment="general", seed=7, created="2026-10-04",
                        images=(CorpusImage(UUID_A, "hash", "ocr only"), CorpusImage(UUID_B, None, "no usable text")))
        path = tmp_path / "corpus.json"

        gold.save_corpus(corpus, path)

        assert gold.load_corpus(path) == corpus
        assert corpus.image_ids() == (UUID_A, UUID_B)
        assert corpus.strata() == {UUID_A: "ocr only", UUID_B: "no usable text"}

    def test_saved_file_is_deterministic_lf_utf8_with_trailing_newline(self, tmp_path):
        corpus = Corpus("general", 7, "2026-10-04", (CorpusImage(UUID_A, "h", "ocr only"),))
        path = tmp_path / "corpus.json"

        gold.save_corpus(corpus, path)
        first = path.read_bytes()
        gold.save_corpus(corpus, path)

        assert path.read_bytes() == first
        assert b"\r" not in first and first.endswith(b"}\n")
        assert list(json.loads(first)) == ["version", "environment", "seed", "created", "images"]

    def test_unknown_stratum_is_rejected(self):
        data = {"version": 1, "environment": "general", "seed": 1, "created": "d",
                "images": [{"imageId": UUID_A, "contentHash": None, "stratum": "weird"}]}
        with pytest.raises(GoldError, match="stratum"):
            parse_corpus(data)

    def test_duplicate_image_ids_are_rejected(self):
        image = {"imageId": UUID_A, "contentHash": None, "stratum": "ocr only"}
        data = {"version": 1, "environment": "general", "seed": 1, "created": "d", "images": [image, image]}
        with pytest.raises(GoldError, match="duplicate"):
            parse_corpus(data)

    def test_wrong_version_is_rejected(self):
        with pytest.raises(GoldError, match="version"):
            parse_corpus({"version": 2, "environment": "general", "seed": 1, "created": "d", "images": []})


class TestQueries:
    def test_round_trip(self, tmp_path):
        queries = [_query(1, pair_id="p1"), _query(2, language="ru", reviewed=False)]
        path = tmp_path / "queries.json"

        gold.save_queries(queries, path)

        assert gold.load_queries(path) == queries

    def test_check_accepts_a_valid_query(self):
        assert check_queries([_query()], TAX) == []

    def test_check_reports_each_structural_problem(self):
        bad = [
            _query(1, text="  "),
            _query(2, expected=_expected(situation="")),
            _query(3, expected=_expected(emotion=("not_a_key",))),
            _query(4, expected=_expected(stance=("mock", "agree", "refuse"))),          # over max_values 2
            GoldQuery("q005", "fr", "hello", "short", "easy", _expected()),
            GoldQuery("q006", "en", "hello", "huge", "easy", _expected()),
            GoldQuery("q007", "en", "hello", "short", "impossible", _expected()),
            _query(1),                                                                 # duplicate id q001
        ]

        problems = " | ".join(check_queries(bad, TAX))

        for fragment in ("q001: text is empty", "q002: situation is empty", "q003: emotion: unknown key",
                         "q004: stance:", "q005: language", "q006: length", "q007: difficulty",
                         "duplicate query id q001"):
            assert fragment in problems

    def test_situation_longer_than_the_taxonomy_limit_is_reported(self):
        long_situation = "x" * (TAX.axes["situation"].max_chars + 1)
        problems = check_queries([_query(expected=_expected(situation=long_situation))], TAX)
        assert any("situation" in p and "longer" in p for p in problems)

    def test_parse_rejects_a_query_without_an_expected_profile(self):
        with pytest.raises(GoldError, match="expectedProfile"):
            parse_queries({"version": 1, "queries": [{"id": "q1", "language": "en", "text": "t",
                                                      "tags": {"length": "short", "difficulty": "easy"}}]})


def _valid_set():
    """A 60-query set that satisfies every drafting rule, built from the real taxonomy."""
    topics = list(TAX.values["topic"])
    stances = list(TAX.values["stance"])
    emotions = list(TAX.values["emotion"])
    tones = list(TAX.values["tone"])
    lengths = ["short"] * 5 + ["medium"] * 10 + ["long"] * 5
    difficulties = ["easy"] * 7 + ["hard"] * 8 + ["ambiguous"] * 5
    queries = []
    counter = 0
    for language in ("en", "ru", "es"):
        for i in range(20):
            counter += 1
            queries.append(_query(
                counter, language=language, length=lengths[i], difficulty=difficulties[i],
                expected=_expected(topic=(topics[counter % len(topics)],), stance=(stances[counter % len(stances)],),
                                   emotion=(emotions[counter % len(emotions)],), tone=(tones[counter % len(tones)],)),
                pair_id=f"p{i}" if i < 6 else None))
    return queries


class TestLintQuerySet:
    def test_a_valid_set_passes(self):
        assert lint_query_set(_valid_set(), TAX) == []

    def test_wrong_language_counts_are_reported(self):
        queries = [q for q in _valid_set() if not (q.language == "es" and q.id == "q041")]
        assert any("es" in p and "20" in p for p in lint_query_set(queries, TAX))

    def test_length_tag_must_match_the_word_count(self):
        queries = _valid_set()
        queries[0] = _query(1, language="en", length="short", text="this one has far too many words to be short")
        assert any("q001" in p and "short" in p for p in lint_query_set(queries, TAX))

    def test_missing_topic_coverage_is_reported(self):
        queries = [q for q in _valid_set()]
        only_work = ExpectedProfile("s", ("annoyance",), ("mock",), ("work",), ("sarcastic",))
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, only_work, True, q.pair_id)
                   for q in queries]
        assert any("topic" in p for p in lint_query_set(queries, TAX))

    def test_too_few_distinct_stances_are_reported(self):
        same = ExpectedProfile("s", tuple(TAX.values["emotion"])[:1], ("mock",), tuple(TAX.values["topic"])[:1],
                               tuple(TAX.values["tone"])[:1])
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, same, True, q.pair_id)
                   for q in _valid_set()]
        problems = " | ".join(lint_query_set(queries, TAX))
        assert "stance" in problems and "emotion" in problems and "tone" in problems

    def test_pair_ids_must_cover_all_three_languages(self):
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, q.expected, True,
                             None if q.language == "es" else q.pair_id) for q in _valid_set()]
        assert any("pair" in p for p in lint_query_set(queries, TAX))


class TestJudgments:
    def test_round_trip_and_grade_validation(self, tmp_path):
        judgments = [Judgment("q001", UUID_A, 2, "ramiz", "2026-10-04", 1)]
        path = tmp_path / "judgments.json"

        gold.save_judgments(judgments, path)

        assert gold.load_judgments(path) == judgments
        with pytest.raises(GoldError, match="grade"):
            parse_judgments({"version": 1, "judgments": [
                {"queryId": "q001", "imageId": UUID_A, "grade": 3, "rater": "r", "date": "d", "round": 1}]})

    def test_merge_adds_new_pairs_and_reports_overwrites_and_unchanged(self):
        existing = [Judgment("q1", "a", 1, "r", "d", 1), Judgment("q1", "b", 2, "r", "d", 1)]
        incoming = [Judgment("q1", "a", 2, "r", "d2", 2),        # overwrite
                    Judgment("q1", "b", 2, "r", "d2", 2),        # same grade: unchanged
                    Judgment("q2", "c", 0, "r", "d2", 2)]        # new

        result = merge_judgments(existing, incoming)

        assert (result.added, result.overwritten, result.unchanged) == (1, 1, 1)
        by_pair = {(j.query_id, j.image_id): j for j in result.merged}
        assert by_pair[("q1", "a")].grade == 2 and by_pair[("q1", "a")].round == 2
        assert set(by_pair) == {("q1", "a"), ("q1", "b"), ("q2", "c")}

    def test_judgments_by_query_groups_grades(self):
        judgments = [Judgment("q1", "a", 2, "r", "d", 1), Judgment("q1", "b", 0, "r", "d", 1),
                     Judgment("q2", "a", 1, "r", "d", 1)]
        assert judgments_by_query(judgments) == {"q1": {"a": 2, "b": 0}, "q2": {"a": 1}}


class TestExportImport:
    def _export(self, **overrides):
        data = {"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1,
                "judgments": [{"queryId": "q001", "imageId": UUID_A, "grade": 2}]}
        data.update(overrides)
        return data

    def test_valid_export_becomes_judgments(self):
        result = parse_export(self._export(), valid_query_ids={"q001"}, valid_image_ids={UUID_A})

        assert result == [Judgment("q001", UUID_A, 2, "ramiz", "2026-10-04", 1)]

    def test_unknown_query_unknown_image_bad_grade_and_duplicates_are_refused(self):
        valid = dict(valid_query_ids={"q001"}, valid_image_ids={UUID_A})
        with pytest.raises(GoldError, match="unknown query"):
            parse_export(self._export(judgments=[{"queryId": "q999", "imageId": UUID_A, "grade": 1}]), **valid)
        with pytest.raises(GoldError, match="unknown image"):
            parse_export(self._export(judgments=[{"queryId": "q001", "imageId": UUID_B, "grade": 1}]), **valid)
        with pytest.raises(GoldError, match="grade"):
            parse_export(self._export(judgments=[{"queryId": "q001", "imageId": UUID_A, "grade": 5}]), **valid)
        pair = {"queryId": "q001", "imageId": UUID_A, "grade": 1}
        with pytest.raises(GoldError, match="duplicate"):
            parse_export(self._export(judgments=[pair, pair]), **valid)

    def test_missing_rater_is_refused(self):
        data = self._export()
        del data["rater"]
        with pytest.raises(GoldError, match="rater"):
            parse_export(data, valid_query_ids={"q001"}, valid_image_ids={UUID_A})


def test_file_sha256_ignores_crlf(tmp_path):
    lf = tmp_path / "a.json"
    crlf = tmp_path / "b.json"
    lf.write_bytes(b'{"a": 1}\n')
    crlf.write_bytes(b'{"a": 1}\r\n')

    assert file_sha256(lf) == file_sha256(crlf)
    assert len(file_sha256(lf)) == 64
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `touch evaluation/__init__.py evaluation/retrievers/__init__.py tests/evaluation/__init__.py` then `python -m pytest tests/evaluation -q`
Expected: collection ERRORS (`ModuleNotFoundError: No module named 'evaluation.metrics'`).

- [ ] **Step 4: Write `evaluation/metrics.py`**

```python
"""Ranking metrics for the meme-from-text gold set (P6 spec, decision 8). Pure: standard library only."""
import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class QueryMetrics:
    ndcg: float | None
    recall: float | None
    hit: float | None
    judged_share: float | None
    has_positive: bool


def _dcg(grades: Sequence[int]) -> float:
    return sum(grade / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


def query_metrics(ranked: Sequence[str], grades: Mapping[str, int], k: int = 10) -> QueryMetrics:
    """Metrics of one ranked list against one query's judgments. Unjudged results count as grade 0.
    ndcg, recall and hit are None when the query has no judged positive (it cannot be scored)."""
    top = list(ranked[:k])
    gains = [grades.get(image_id, 0) for image_id in top]
    positives = {image_id for image_id, grade in grades.items() if grade >= 1}
    ideal_dcg = _dcg(sorted(grades.values(), reverse=True)[:k])

    ndcg = _dcg(gains) / ideal_dcg if ideal_dcg > 0 else None
    recall = len(positives.intersection(top)) / len(positives) if positives else None
    hit = (1.0 if any(grade >= 1 for grade in gains) else 0.0) if positives else None
    judged_share = sum(1 for image_id in top if image_id in grades) / len(top) if top else None
    return QueryMetrics(ndcg, recall, hit, judged_share, bool(positives))


def mean(values) -> float | None:
    defined = [value for value in values if value is not None]
    return sum(defined) / len(defined) if defined else None


def aggregate(items: Sequence[QueryMetrics]) -> dict:
    return {
        "queries": len(items),
        "noPositive": sum(1 for item in items if not item.has_positive),
        "ndcg": mean(item.ndcg for item in items),
        "recall": mean(item.recall for item in items),
        "hit": mean(item.hit for item in items),
        "judged": mean(item.judged_share for item in items),
    }


def language_ratio(by_language: Mapping[str, Mapping]) -> dict[str, float | None]:
    """nDCG of each language relative to English (user story US-7); None when English is missing or zero."""
    base = by_language.get("en", {}).get("ndcg")
    ratios: dict[str, float | None] = {}
    for language, stats in by_language.items():
        value = stats.get("ndcg")
        ratios[language] = value / base if base and value is not None else None
    return ratios
```

- [ ] **Step 5: Write `evaluation/gold.py`**

```python
"""Gold data formats for the meme-from-text evaluation (P6 spec, decision 2). Pure: standard library plus taxonomy.

All files are UTF-8 with LF newlines, a fixed key order and a trailing newline, so they diff cleanly in git
and hash identically on Windows and Linux checkouts.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile

FORMAT_VERSION = 1
LANGUAGES = ("en", "ru", "es")
LENGTHS = ("short", "medium", "long")
DIFFICULTIES = ("easy", "hard", "ambiguous")
STRATA = ("ocr+description", "ocr only", "description only", "no usable text")
GRADES = (0, 1, 2)
CONTROLLED_AXES = ("emotion", "stance", "topic", "tone")
MAX_QUERY_CHARS = 600
WORD_LIMITS = {"short": (1, 8), "medium": (9, 20), "long": (21, 10_000)}


class GoldError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusImage:
    image_id: str
    content_hash: str | None
    stratum: str


@dataclass(frozen=True)
class Corpus:
    environment: str
    seed: int
    created: str
    images: tuple[CorpusImage, ...]
    version: int = FORMAT_VERSION

    def image_ids(self) -> tuple[str, ...]:
        return tuple(image.image_id for image in self.images)

    def strata(self) -> dict[str, str]:
        return {image.image_id: image.stratum for image in self.images}


@dataclass(frozen=True)
class ExpectedProfile:
    situation: str
    emotion: tuple[str, ...]
    stance: tuple[str, ...]
    topic: tuple[str, ...]
    tone: tuple[str, ...]

    def axis_values(self) -> dict[str, tuple[str, ...]]:
        return {axis: getattr(self, axis) for axis in CONTROLLED_AXES}


@dataclass(frozen=True)
class GoldQuery:
    id: str
    language: str
    text: str
    length: str
    difficulty: str
    expected: ExpectedProfile
    reviewed: bool = False
    pair_id: str | None = None


@dataclass(frozen=True)
class Judgment:
    query_id: str
    image_id: str
    grade: int
    rater: str
    date: str
    round: int


@dataclass(frozen=True)
class MergeResult:
    merged: list[Judgment]
    added: int
    overwritten: int
    unchanged: int


# ---------------------------------------------------------------- serialisation helpers

def _check_version(data: Mapping, what: str) -> None:
    if data.get("version") != FORMAT_VERSION:
        raise GoldError(f"{what}: unsupported version {data.get('version')!r} (expected {FORMAT_VERSION})")


def dump_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump_json(data))


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def file_sha256(path: Path) -> str:
    """sha256 of the file content with CRLF normalised to LF (stable across Windows and Linux checkouts)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------- corpus

def corpus_to_dict(corpus: Corpus) -> dict:
    return {
        "version": corpus.version,
        "environment": corpus.environment,
        "seed": corpus.seed,
        "created": corpus.created,
        "images": [{"imageId": i.image_id, "contentHash": i.content_hash, "stratum": i.stratum}
                   for i in corpus.images],
    }


def parse_corpus(data: Mapping) -> Corpus:
    _check_version(data, "corpus")
    images, seen = [], set()
    for entry in data.get("images", []):
        image_id = entry.get("imageId")
        if not isinstance(image_id, str) or not image_id:
            raise GoldError("corpus: every image needs an imageId")
        if image_id in seen:
            raise GoldError(f"corpus: duplicate image id {image_id}")
        seen.add(image_id)
        if entry.get("stratum") not in STRATA:
            raise GoldError(f"corpus: image {image_id} has an unknown stratum {entry.get('stratum')!r}")
        images.append(CorpusImage(image_id, entry.get("contentHash"), entry["stratum"]))
    return Corpus(environment=data["environment"], seed=data["seed"], created=data["created"],
                  images=tuple(images), version=data["version"])


def load_corpus(path: Path) -> Corpus:
    return parse_corpus(_read(Path(path)))


def save_corpus(corpus: Corpus, path: Path) -> None:
    _write(Path(path), corpus_to_dict(corpus))


# ---------------------------------------------------------------- queries

def queries_to_dict(queries: Sequence[GoldQuery]) -> dict:
    items = []
    for q in queries:
        item = {
            "id": q.id, "language": q.language, "text": q.text,
            "tags": {"length": q.length, "difficulty": q.difficulty},
            "expectedProfile": {"situation": q.expected.situation, **{a: list(v) for a, v in q.expected.axis_values().items()}},
            "reviewed": q.reviewed,
        }
        if q.pair_id is not None:
            item["pairId"] = q.pair_id
        items.append(item)
    return {"version": FORMAT_VERSION, "queries": items}


def parse_queries(data: Mapping) -> list[GoldQuery]:
    _check_version(data, "queries")
    queries = []
    for entry in data.get("queries", []):
        profile = entry.get("expectedProfile")
        if not isinstance(profile, dict):
            raise GoldError(f"{entry.get('id')}: expectedProfile is required")
        tags = entry.get("tags") or {}
        queries.append(GoldQuery(
            id=entry["id"], language=entry["language"], text=entry["text"],
            length=tags.get("length", ""), difficulty=tags.get("difficulty", ""),
            expected=ExpectedProfile(
                situation=profile.get("situation", ""),
                **{axis: tuple(profile.get(axis, [])) for axis in CONTROLLED_AXES}),
            reviewed=bool(entry.get("reviewed", False)), pair_id=entry.get("pairId")))
    return queries


def load_queries(path: Path) -> list[GoldQuery]:
    return parse_queries(_read(Path(path)))


def save_queries(queries: Sequence[GoldQuery], path: Path) -> None:
    _write(Path(path), queries_to_dict(queries))


def check_queries(queries: Sequence[GoldQuery], tax: Taxonomy) -> list[str]:
    """Structural problems of every query (empty list = fine)."""
    problems: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if q.id in seen:
            problems.append(f"duplicate query id {q.id}")
        seen.add(q.id)
        if not q.text.strip():
            problems.append(f"{q.id}: text is empty")
        elif len(q.text) > MAX_QUERY_CHARS:
            problems.append(f"{q.id}: text is longer than {MAX_QUERY_CHARS} characters")
        if q.language not in LANGUAGES:
            problems.append(f"{q.id}: language {q.language!r} is not one of {LANGUAGES}")
        if q.length not in LENGTHS:
            problems.append(f"{q.id}: length {q.length!r} is not one of {LENGTHS}")
        if q.difficulty not in DIFFICULTIES:
            problems.append(f"{q.id}: difficulty {q.difficulty!r} is not one of {DIFFICULTIES}")
        if not q.expected.situation.strip():
            problems.append(f"{q.id}: situation is empty")
        profile = {"situation": q.expected.situation.strip() or None,
                   **{axis: [{"key": key} for key in values] for axis, values in q.expected.axis_values().items()}}
        result = validate_profile(profile, tax)
        problems.extend(f"{q.id}: {error}" for error in result.errors)
    return problems


def lint_query_set(queries: Sequence[GoldQuery], tax: Taxonomy, *, per_language: int = 20,
                   length_quota: Mapping[str, int] | None = None, difficulty_quota: Mapping[str, int] | None = None,
                   min_stances: int = 8, min_emotions: int = 10, min_tones: int = 6,
                   min_pairs: int = 6) -> list[str]:
    """The drafting rules of P6 spec decision 4 (empty list = the set is acceptable)."""
    length_quota = dict(length_quota or {"short": 5, "medium": 10, "long": 5})
    difficulty_quota = dict(difficulty_quota or {"easy": 7, "hard": 8, "ambiguous": 5})
    problems: list[str] = []

    for language in LANGUAGES:
        group = [q for q in queries if q.language == language]
        if len(group) != per_language:
            problems.append(f"{language}: {len(group)} queries, expected {per_language}")
        for tag, quota, getter in (("length", length_quota, lambda q: q.length),
                                   ("difficulty", difficulty_quota, lambda q: q.difficulty)):
            for value, expected in quota.items():
                have = sum(1 for q in group if getter(q) == value)
                if have != expected:
                    problems.append(f"{language}: {have} queries with {tag} {value}, expected {expected}")

    for q in queries:
        low, high = WORD_LIMITS.get(q.length, (0, 0))
        words = len(q.text.split())
        if not low <= words <= high:
            problems.append(f"{q.id}: length tag {q.length} but {words} words (allowed {low}-{high})")

    for axis, minimum in (("topic", len(tax.values["topic"])), ("stance", min_stances),
                          ("emotion", min_emotions), ("tone", min_tones)):
        distinct = {key for q in queries for key in getattr(q.expected, axis)}
        if len(distinct) < minimum:
            problems.append(f"only {len(distinct)} distinct {axis} keys across the expected profiles, need {minimum}")

    languages_by_pair: dict[str, set[str]] = {}
    for q in queries:
        if q.pair_id:
            languages_by_pair.setdefault(q.pair_id, set()).add(q.language)
    complete = sum(1 for languages in languages_by_pair.values() if languages == set(LANGUAGES))
    if complete < min_pairs:
        problems.append(f"only {complete} pair ids appear in all three languages, need {min_pairs}")
    return problems


# ---------------------------------------------------------------- judgments

def judgments_to_dict(judgments: Sequence[Judgment]) -> dict:
    ordered = sorted(judgments, key=lambda j: (j.query_id, j.image_id))
    return {"version": FORMAT_VERSION,
            "judgments": [{"queryId": j.query_id, "imageId": j.image_id, "grade": j.grade, "rater": j.rater,
                           "date": j.date, "round": j.round} for j in ordered]}


def parse_judgments(data: Mapping) -> list[Judgment]:
    _check_version(data, "judgments")
    judgments = []
    for entry in data.get("judgments", []):
        if entry.get("grade") not in GRADES:
            raise GoldError(f"judgment {entry.get('queryId')}/{entry.get('imageId')}: grade must be one of {GRADES}")
        judgments.append(Judgment(entry["queryId"], entry["imageId"], entry["grade"], entry["rater"],
                                  entry["date"], entry["round"]))
    return judgments


def load_judgments(path: Path) -> list[Judgment]:
    return parse_judgments(_read(Path(path)))


def save_judgments(judgments: Sequence[Judgment], path: Path) -> None:
    _write(Path(path), judgments_to_dict(judgments))


def merge_judgments(existing: Sequence[Judgment], incoming: Iterable[Judgment]) -> MergeResult:
    """Merge by (query, image). A later grade overwrites an earlier one; an identical grade is 'unchanged'."""
    by_pair = {(j.query_id, j.image_id): j for j in existing}
    added = overwritten = unchanged = 0
    for judgment in incoming:
        key = (judgment.query_id, judgment.image_id)
        previous = by_pair.get(key)
        if previous is None:
            added += 1
            by_pair[key] = judgment
        elif previous.grade == judgment.grade:
            unchanged += 1
        else:
            overwritten += 1
            by_pair[key] = judgment
    return MergeResult(list(by_pair.values()), added, overwritten, unchanged)


def judgments_by_query(judgments: Iterable[Judgment]) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        grouped.setdefault(judgment.query_id, {})[judgment.image_id] = judgment.grade
    return grouped


def parse_export(data: Mapping, *, valid_query_ids: set[str], valid_image_ids: set[str]) -> list[Judgment]:
    """Validate a judging-sheet export (`{rater, date, round, judgments: [{queryId, imageId, grade}]}`)."""
    for field in ("rater", "date", "round"):
        if field not in data:
            raise GoldError(f"export: missing {field}")
    judgments, seen = [], set()
    for entry in data.get("judgments", []):
        query_id, image_id, grade = entry.get("queryId"), entry.get("imageId"), entry.get("grade")
        if query_id not in valid_query_ids:
            raise GoldError(f"export: unknown query {query_id!r}")
        if image_id not in valid_image_ids:
            raise GoldError(f"export: unknown image {image_id!r}")
        if grade not in GRADES:
            raise GoldError(f"export: grade for {query_id}/{image_id} must be one of {GRADES}")
        if (query_id, image_id) in seen:
            raise GoldError(f"export: duplicate judgment for {query_id}/{image_id}")
        seen.add((query_id, image_id))
        judgments.append(Judgment(query_id, image_id, grade, data["rater"], data["date"], data["round"]))
    return judgments
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/evaluation -q`
Expected: all PASS. If a lint test fails because the real taxonomy has fewer than 10 emotion keys cycling in `_valid_set`, that is a test-helper bug, not a lint bug: check `len(TAX.values["emotion"])` (26) and the `counter % len(...)` cycling over 60 queries covers all keys of the smaller axes (topic 19, stance 15, tone 10, emotion 26) at least `min` distinct times.

- [ ] **Step 7: Commit**

```bash
git add evaluation tests/evaluation
git commit -m "feat(evaluation): add gold data formats, query lint and ranking metrics"
```

---

### Task 2: Pooling, runner and report

**Files:**
- Create: `evaluation/pooling.py`, `evaluation/runner.py`, `evaluation/report.py`, `evaluation/retrievers/base.py`
- Create: `tests/evaluation/test_pooling.py`, `tests/evaluation/test_runner.py`, `tests/evaluation/test_report.py`, `tests/evaluation/test_pure_imports.py`

**Interfaces:**
- Consumes: Task 1 (`GoldQuery`, `QueryMetrics`, `query_metrics`, `aggregate`, `language_ratio`, `mean`).
- Produces: `evaluation.pooling.Pool(query_id, candidates: tuple[str, ...], sources: dict[str, tuple[str, ...]])`, `build_pool(query_id, ranked: Mapping[str, Sequence[str]], random_candidates: Sequence[str], *, exclude=(), ranked_target=9, random_target=3, seed=0) -> Pool`; `evaluation.retrievers.base.Retriever` protocol (`name: str`, `config: dict`, `async retrieve(query, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]`, optional `async prepare(queries)`); `evaluation.runner.QueryResult(query_id, language, length, difficulty, ranked: tuple[str, ...], metrics: QueryMetrics)`, `run_retriever(retriever, queries, corpus_ids, judgments, *, k=10) -> list[QueryResult]`, `summarize(results, strata, judgments, k=10) -> dict` with keys `overall`, `byLanguage`, `byDifficulty`, `byLength`, `byStratum`, `languageRatio`; `evaluation.report`: `make_baseline(*, label, retriever_name, retriever_config, date, git_commit, gold_hashes, profile_state, results, summary) -> dict`, `save_baseline(path, baseline)`, `load_baseline(path)`, `format_report(baseline, bias=None) -> str`, `pooling_bias(results, pool_sources) -> dict`, `compare_baselines(a, b, *, force=False, drop_threshold=0.1, query_texts=None) -> str`, `CompareError`, `git_commit() -> str | None`.

- [ ] **Step 1: Write the failing pooling tests**

Create `tests/evaluation/test_pooling.py`:

```python
from evaluation.pooling import Pool, build_pool


def _pool(ranked, random_candidates=(), **kwargs):
    return build_pool("q001", ranked, list(random_candidates), **kwargs)


def test_ranked_sources_contribute_round_robin_from_rank_one():
    ranked = {"a": ["1", "2", "3", "4", "5"], "b": ["2", "6", "7"], "c": []}

    pool = _pool(ranked, ranked_target=5, random_target=0)

    # pass 1: a->1, b->2 ; pass 2: a->3 (2 already taken), b->6 ; pass 3: a->4
    assert sorted(pool.candidates) == ["1", "2", "3", "4", "6"]
    assert pool.sources["2"] == ("a", "b")
    assert pool.sources["1"] == ("a",)


def test_an_empty_source_is_filled_by_the_others():
    ranked = {"lexical": [], "embedding": [str(i) for i in range(20)], "axis": [str(i) for i in range(100, 120)]}

    pool = _pool(ranked, ranked_target=9, random_target=0)

    assert len(pool.candidates) == 9
    assert all("lexical" not in sources for sources in pool.sources.values())


def test_random_members_are_added_last_and_skip_images_already_taken():
    ranked = {"a": ["1", "2", "3"]}

    pool = _pool(ranked, random_candidates=["2", "x", "y", "z", "w"], ranked_target=3, random_target=3)

    assert len(pool.candidates) == 6
    assert set(pool.candidates) == {"1", "2", "3", "x", "y", "z"}
    assert pool.sources["x"] == ("random",)
    assert "random" not in pool.sources["2"]


def test_excluded_images_are_never_asked_again():
    ranked = {"a": ["1", "2", "3", "4"]}

    pool = _pool(ranked, random_candidates=["1", "r"], exclude={"1", "2"}, ranked_target=2, random_target=1)

    assert "1" not in pool.candidates and "2" not in pool.candidates
    assert set(pool.candidates) == {"3", "4", "r"}


def test_pool_is_smaller_when_candidates_run_out():
    pool = _pool({"a": ["1"]}, random_candidates=["2"], ranked_target=9, random_target=3)
    assert sorted(pool.candidates) == ["1", "2"]


def test_public_order_is_deterministic_per_query_and_seed_and_differs_between_queries():
    ranked = {"a": [str(i) for i in range(30)]}

    first = build_pool("q001", ranked, [], seed=5)
    again = build_pool("q001", ranked, [], seed=5)
    other_query = build_pool("q002", ranked, [], seed=5)
    other_seed = build_pool("q001", ranked, [], seed=6)

    assert first.candidates == again.candidates
    assert first.candidates != other_query.candidates
    assert first.candidates != other_seed.candidates
    assert sorted(first.candidates) == sorted(other_query.candidates)


def test_public_candidates_are_plain_image_ids_without_source_names():
    pool = _pool({"lexical_or": ["a1", "a2"]}, random_candidates=["r1"], ranked_target=2, random_target=1)

    assert isinstance(pool, Pool)
    assert all(isinstance(candidate, str) and "lexical" not in candidate for candidate in pool.candidates)
    assert pool.query_id == "q001"
```

- [ ] **Step 2: Write the failing runner tests**

Create `tests/evaluation/test_runner.py`:

```python
import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.runner import QueryResult, run_retriever, summarize

EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _query(query_id, language="en", reviewed=True, length="short", difficulty="easy"):
    return GoldQuery(query_id, language, "text", length, difficulty, EXPECTED, reviewed)


class FakeRetriever:
    name = "fake"
    config = {"k": 1}

    def __init__(self, rankings, with_prepare=False):
        self.rankings = rankings
        self.prepared = None
        if with_prepare:
            self.prepare = self._prepare

    async def _prepare(self, queries):
        self.prepared = [q.id for q in queries]

    async def retrieve(self, query, corpus_ids, k):
        return [(image_id, 1.0) for image_id in self.rankings.get(query.id, [])][:k]


@pytest.mark.asyncio
async def test_only_reviewed_queries_run_and_metrics_use_the_judgments():
    queries = [_query("q1"), _query("q2", reviewed=False), _query("q3", language="ru")]
    judgments = {"q1": {"a": 2, "b": 0}, "q3": {"c": 1}}
    retriever = FakeRetriever({"q1": ["a", "b"], "q2": ["a"], "q3": ["x"]})

    results = await run_retriever(retriever, queries, ["a", "b", "c", "x"], judgments, k=10)

    assert [r.query_id for r in results] == ["q1", "q3"]
    assert results[0].metrics.hit == 1.0 and results[0].ranked == ("a", "b")
    assert results[1].metrics.hit == 0.0
    assert (results[1].language, results[1].length, results[1].difficulty) == ("ru", "short", "easy")


@pytest.mark.asyncio
async def test_prepare_is_called_once_with_the_reviewed_queries_when_the_retriever_has_it():
    retriever = FakeRetriever({}, with_prepare=True)

    await run_retriever(retriever, [_query("q1"), _query("q2", reviewed=False)], ["a"], {}, k=10)

    assert retriever.prepared == ["q1"]


@pytest.mark.asyncio
async def test_results_are_truncated_to_k():
    retriever = FakeRetriever({"q1": ["a", "b", "c"]})

    results = await run_retriever(retriever, [_query("q1")], ["a", "b", "c"], {"q1": {"c": 2}}, k=2)

    assert results[0].ranked == ("a", "b") and results[0].metrics.hit == 0.0


def test_summarize_groups_by_language_difficulty_length_and_stratum():
    from evaluation.metrics import QueryMetrics

    def result(query_id, language, difficulty, ranked, ndcg):
        return QueryResult(query_id, language, "short", difficulty, tuple(ranked),
                           QueryMetrics(ndcg, ndcg, ndcg, 1.0, True))

    results = [result("q1", "en", "easy", ["a", "b"], 1.0), result("q2", "ru", "hard", ["c"], 0.5)]
    strata = {"a": "ocr only", "b": "ocr+description", "c": "ocr only"}
    judgments = {"q1": {"a": 2, "b": 0}, "q2": {"c": 1}}

    summary = summarize(results, strata, judgments, k=10)

    assert summary["overall"]["queries"] == 2 and summary["overall"]["ndcg"] == 0.75
    assert summary["byLanguage"]["en"]["ndcg"] == 1.0 and summary["byLanguage"]["ru"]["ndcg"] == 0.5
    assert summary["byDifficulty"]["hard"]["queries"] == 1
    assert summary["byLength"]["short"]["queries"] == 2
    assert summary["languageRatio"] == {"en": 1.0, "ru": 0.5}
    ocr_only = summary["byStratum"]["ocr only"]
    assert ocr_only["retrievedShare"] == pytest.approx((0.5 + 1.0) / 2)      # q1: 1 of 2, q2: 1 of 1
    assert ocr_only["meanGradeJudged"] == pytest.approx((2 + 1) / 2)
    assert ocr_only["judgedCount"] == 2
    assert summary["byStratum"]["ocr+description"]["meanGradeJudged"] == 0.0
```

- [ ] **Step 3: Write the failing report tests**

Create `tests/evaluation/test_report.py`:

```python
import json

import pytest

from evaluation.metrics import QueryMetrics
from evaluation.report import (
    CompareError, compare_baselines, format_report, load_baseline, make_baseline, pooling_bias, save_baseline,
)
from evaluation.runner import QueryResult, summarize

HASHES = {"corpusSha": "c1", "queriesSha": "q1", "judgmentsSha": "j1"}
STATE = {"profiled": 10, "taxonomyVersions": {"2": 10}}


def _result(query_id, language, ndcg):
    return QueryResult(query_id, language, "short", "easy", (f"img-{query_id}",),
                       QueryMetrics(ndcg, ndcg, 1.0 if ndcg else 0.0, 1.0, True))


def _baseline(label="b1", ndcgs=None, hashes=None):
    ndcgs = ndcgs or {"q1": 1.0, "q2": 0.5}
    results = [_result("q1", "en", ndcgs["q1"]), _result("q2", "ru", ndcgs["q2"])]
    summary = summarize(results, {"img-q1": "ocr only", "img-q2": "ocr only"}, {"q1": {"img-q1": 2}, "q2": {"img-q2": 1}})
    return make_baseline(label=label, retriever_name="fake", retriever_config={"x": 1}, date="2026-10-04",
                         git_commit="abc123", gold_hashes=hashes or HASHES, profile_state=STATE,
                         results=results, summary=summary)


def test_baseline_contains_identity_hashes_state_summary_and_per_query_metrics_but_no_file_names():
    baseline = _baseline()

    assert baseline["label"] == "b1" and baseline["retriever"] == {"name": "fake", "config": {"x": 1}}
    assert baseline["gitCommit"] == "abc123" and baseline["gold"] == HASHES and baseline["profileState"] == STATE
    assert baseline["summary"]["overall"]["queries"] == 2
    assert baseline["queries"]["q1"] == {"language": "en", "ndcg": 1.0, "recall": 1.0, "hit": 1.0, "judged": 1.0}
    assert "filename" not in json.dumps(baseline).lower()


def test_save_and_load_round_trip_is_deterministic(tmp_path):
    path = tmp_path / "b1.json"
    save_baseline(path, _baseline())
    first = path.read_bytes()

    assert load_baseline(path) == _baseline()
    save_baseline(path, _baseline())
    assert path.read_bytes() == first and first.endswith(b"}\n") and b"\r" not in first


def test_format_report_is_markdown_with_the_main_sections():
    report = format_report(_baseline(), bias={"lexical_or": 3, "random": 1, "unpooled": 2})

    assert report.startswith("# Evaluation: b1")
    for section in ("## Overall", "## By language", "## By difficulty", "## By length", "## By input stratum",
                    "## Pooling bias"):
        assert section in report
    assert "nDCG@10" in report and "recall@10" in report and "hit@10" in report and "judged@10" in report
    assert "unpooled" in report


def test_pooling_bias_counts_which_source_proposed_each_top_result():
    results = [_result("q1", "en", 1.0)]          # ranked = ("img-q1",)
    pool_sources = {"q1": {"img-q1": ["lexical_or", "axis_match"]}, "q2": {}}

    bias = pooling_bias(results, pool_sources)

    assert bias == {"lexical_or": 1, "axis_match": 1, "unpooled": 0, "topResults": 1}
    assert pooling_bias(results, {"q1": {}}) == {"unpooled": 1, "topResults": 1}


def test_compare_prints_deltas_and_the_queries_that_got_worse():
    before = _baseline("before", {"q1": 1.0, "q2": 0.5})
    after = _baseline("after", {"q1": 0.6, "q2": 0.5})

    text = compare_baselines(before, after, query_texts={"q1": "boss moved the deadline", "q2": "other"})

    assert "before -> after" in text
    assert "-0.200" in text                                   # overall nDCG mean 0.75 -> 0.55
    assert "q1" in text and "boss moved the deadline" in text and "-0.400" in text
    assert "q2" not in text.split("got worse")[1]


def test_compare_refuses_different_gold_data_unless_forced():
    before = _baseline("before")
    after = _baseline("after", hashes={**HASHES, "judgmentsSha": "j2"})

    with pytest.raises(CompareError, match="judgmentsSha"):
        compare_baselines(before, after)
    assert "before -> after" in compare_baselines(before, after, force=True)


def test_compare_with_no_regression_says_so():
    text = compare_baselines(_baseline("a"), _baseline("b"))
    assert "No query got worse" in text
```

- [ ] **Step 4: Write the AST import guard test**

Create `tests/evaluation/test_pure_imports.py`:

```python
import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "evaluation"
PURE = ["gold.py", "metrics.py", "pooling.py", "runner.py", "report.py", "retrievers/base.py"]
ALLOWED = set(sys.stdlib_module_names) | {"taxonomy", "evaluation"}


def _roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_pure_modules_import_only_stdlib_taxonomy_and_evaluation():
    for name in PURE:
        offenders = _roots(PACKAGE / name) - ALLOWED
        assert not offenders, f"evaluation/{name} imports {sorted(offenders)}"


def test_retrievers_package_init_is_empty_so_importing_base_pulls_in_no_database_code():
    init = PACKAGE / "retrievers" / "__init__.py"
    assert init.read_text(encoding="utf-8").strip() == ""
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `python -m pytest tests/evaluation -q`
Expected: collection ERRORS for `evaluation.pooling`, `evaluation.runner`, `evaluation.report` (modules missing); Task 1 tests still pass.

- [ ] **Step 6: Write `evaluation/pooling.py`, `evaluation/retrievers/base.py`, `evaluation/runner.py`**

`evaluation/pooling.py`:

```python
"""Blinded candidate pools for human judging (P6 spec, decision 5). Pure: standard library only."""
import random
from dataclasses import dataclass
from typing import Collection, Mapping, Sequence


@dataclass(frozen=True)
class Pool:
    query_id: str
    candidates: tuple[str, ...]              # the order the judge sees (shuffled)
    sources: dict[str, tuple[str, ...]]      # PRIVATE: image id -> the sources that proposed it


def build_pool(query_id: str, ranked: Mapping[str, Sequence[str]], random_candidates: Sequence[str], *,
               exclude: Collection[str] = (), ranked_target: int = 9, random_target: int = 3,
               seed: int | str = 0) -> Pool:
    """Round-robin over the ranked sources from rank 1 (skipping images already taken or excluded) until
    `ranked_target` distinct images are chosen; an exhausted or empty source is filled by the others. Random
    images are added last. The public order is a shuffle seeded by (seed, query id)."""
    names = list(ranked)
    seen = set(exclude)
    cursors = {name: 0 for name in names}
    taken: list[str] = []

    progressed = True
    while len(taken) < ranked_target and progressed:
        progressed = False
        for name in names:
            if len(taken) >= ranked_target:
                break
            ranking = ranked[name]
            while cursors[name] < len(ranking) and ranking[cursors[name]] in seen:
                cursors[name] += 1
            if cursors[name] < len(ranking):
                image_id = ranking[cursors[name]]
                cursors[name] += 1
                seen.add(image_id)
                taken.append(image_id)
                progressed = True

    random_taken: list[str] = []
    for image_id in random_candidates:
        if len(random_taken) >= random_target:
            break
        if image_id not in seen:
            seen.add(image_id)
            random_taken.append(image_id)

    candidates = taken + random_taken
    membership = {name: set(ranked[name]) for name in names}
    sources: dict[str, tuple[str, ...]] = {}
    for image_id in candidates:
        found = [name for name in names if image_id in membership[name]]
        if image_id in random_taken:
            found.append("random")
        sources[image_id] = tuple(sorted(found))

    order = list(candidates)
    random.Random(f"{seed}:{query_id}").shuffle(order)
    return Pool(query_id=query_id, candidates=tuple(order), sources=sources)
```

`evaluation/retrievers/base.py`:

```python
"""The retriever interface every evaluated system implements (P6 spec, decision 7). Typing only."""
from typing import Protocol, Sequence

from evaluation.gold import GoldQuery


class Retriever(Protocol):
    name: str
    config: dict

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        """Ranked (image id, score) pairs, best first, restricted to `corpus_ids`, at most `k`.
        Deterministic: ties are broken by image id. A retriever may also define
        `async def prepare(self, queries)` to batch work (for example one embedding call) before the run."""
        ...
```

`evaluation/runner.py`:

```python
"""Run a retriever over the gold queries and summarise the result (P6 spec, decision 8). Pure."""
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from evaluation.gold import GoldQuery
from evaluation.metrics import QueryMetrics, aggregate, language_ratio, mean, query_metrics


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    language: str
    length: str
    difficulty: str
    ranked: tuple[str, ...]
    metrics: QueryMetrics


async def run_retriever(retriever, queries: Sequence[GoldQuery], corpus_ids: Sequence[str],
                        judgments: Mapping[str, Mapping[str, int]], *, k: int = 10) -> list[QueryResult]:
    """Evaluate every reviewed query. Unreviewed queries never take part in an official run."""
    reviewed = [query for query in queries if query.reviewed]
    prepare = getattr(retriever, "prepare", None)
    if prepare is not None:
        await prepare(reviewed)
    results = []
    for query in reviewed:
        ranked = await retriever.retrieve(query, corpus_ids, k)
        ids = tuple(str(image_id) for image_id, _ in ranked[:k])
        results.append(QueryResult(
            query_id=query.id, language=query.language, length=query.length, difficulty=query.difficulty,
            ranked=ids, metrics=query_metrics(list(ids), judgments.get(query.id, {}), k=k)))
    return results


def summarize(results: Sequence[QueryResult], strata: Mapping[str, str],
              judgments: Mapping[str, Mapping[str, int]], k: int = 10) -> dict:
    def group(key) -> dict:
        groups: dict[str, list[QueryMetrics]] = defaultdict(list)
        for result in results:
            groups[key(result)].append(result.metrics)
        return {name: aggregate(items) for name, items in sorted(groups.items())}

    by_language = group(lambda r: r.language)
    by_stratum = {}
    for stratum in sorted(set(strata.values())):
        shares, grades = [], []
        for result in results:
            top = result.ranked[:k]
            if top:
                shares.append(sum(1 for image_id in top if strata.get(image_id) == stratum) / len(top))
            judged = judgments.get(result.query_id, {})
            grades.extend(judged[i] for i in top if strata.get(i) == stratum and i in judged)
        by_stratum[stratum] = {"retrievedShare": mean(shares), "meanGradeJudged": mean(grades),
                               "judgedCount": len(grades)}
    return {
        "overall": aggregate([r.metrics for r in results]),
        "byLanguage": by_language,
        "byDifficulty": group(lambda r: r.difficulty),
        "byLength": group(lambda r: r.length),
        "byStratum": by_stratum,
        "languageRatio": language_ratio(by_language),
    }
```

- [ ] **Step 7: Write `evaluation/report.py`**

```python
"""Baseline files, Markdown reports and compare (P6 spec, decision 8). Pure apart from the git lookup."""
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from evaluation.gold import _read, _write
from evaluation.runner import QueryResult

METRICS = (("ndcg", "nDCG@10"), ("recall", "recall@10"), ("hit", "hit@10"), ("judged", "judged@10"))


class CompareError(ValueError):
    pass


def git_commit() -> str | None:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def make_baseline(*, label: str, retriever_name: str, retriever_config: Mapping, date: str,
                  git_commit: str | None, gold_hashes: Mapping[str, str], profile_state: Mapping,
                  results: Sequence[QueryResult], summary: Mapping) -> dict:
    return {
        "label": label,
        "date": date,
        "retriever": {"name": retriever_name, "config": dict(retriever_config)},
        "gitCommit": git_commit,
        "gold": dict(gold_hashes),
        "profileState": dict(profile_state),
        "summary": dict(summary),
        "queries": {
            r.query_id: {"language": r.language, "ndcg": r.metrics.ndcg, "recall": r.metrics.recall,
                         "hit": r.metrics.hit, "judged": r.metrics.judged_share}
            for r in results
        },
    }


def save_baseline(path: Path, baseline: Mapping) -> None:
    _write(Path(path), baseline)


def load_baseline(path: Path) -> dict:
    return _read(Path(path))


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def _table(groups: Mapping[str, Mapping]) -> list[str]:
    lines = ["| group | queries | no positive | nDCG@10 | recall@10 | hit@10 | judged@10 |", "|---|---|---|---|---|---|---|"]
    for name, stats in groups.items():
        lines.append(f"| {name} | {stats['queries']} | {stats['noPositive']} | {_fmt(stats['ndcg'])} | "
                     f"{_fmt(stats['recall'])} | {_fmt(stats['hit'])} | {_fmt(stats['judged'])} |")
    return lines


def format_report(baseline: Mapping, bias: Mapping | None = None) -> str:
    summary = baseline["summary"]
    lines = [f"# Evaluation: {baseline['label']}", "",
             f"Retriever `{baseline['retriever']['name']}` {baseline['retriever']['config']}, {baseline['date']}, "
             f"commit {baseline.get('gitCommit') or 'unknown'}.",
             f"Profile state: {baseline.get('profileState')}", "", "## Overall", ""]
    lines += _table({"all": summary["overall"]})
    for title, key in (("By language", "byLanguage"), ("By difficulty", "byDifficulty"), ("By length", "byLength")):
        lines += ["", f"## {title}", ""] + _table(summary[key])
    ratios = ", ".join(f"{language} {_fmt(ratio)}" for language, ratio in summary["languageRatio"].items())
    lines += ["", f"nDCG@10 relative to English: {ratios}", "", "## By input stratum", "",
              "| stratum | retrieved share of top 10 | mean grade of judged | judged count |", "|---|---|---|---|"]
    for stratum, stats in summary["byStratum"].items():
        lines.append(f"| {stratum} | {_fmt(stats['retrievedShare'])} | {_fmt(stats['meanGradeJudged'])} | "
                     f"{stats['judgedCount']} |")
    if bias is not None:
        lines += ["", "## Pooling bias", "",
                  "Top-10 results by the source that proposed them to the judge (an image can have several "
                  "sources); `unpooled` results were never shown to the judge and count as grade 0:", ""]
        lines += [f"- {name}: {count}" for name, count in bias.items()]
    return "\n".join(lines) + "\n"


def pooling_bias(results: Sequence[QueryResult], pool_sources: Mapping[str, Mapping[str, Sequence[str]]]) -> dict:
    """How many top-10 results each pool source proposed, and how many were in no pool at all."""
    counts: dict[str, int] = {}
    unpooled = top_results = 0
    for result in results:
        sources_of = pool_sources.get(result.query_id, {})
        for image_id in result.ranked:
            top_results += 1
            sources = sources_of.get(image_id)
            if not sources:
                unpooled += 1
                continue
            for source in sources:
                counts[source] = counts.get(source, 0) + 1
    counts["unpooled"] = unpooled
    counts["topResults"] = top_results
    return counts


def compare_baselines(a: Mapping, b: Mapping, *, force: bool = False, drop_threshold: float = 0.1,
                      query_texts: Mapping[str, str] | None = None) -> str:
    if not force:
        for key in sorted(set(a["gold"]) | set(b["gold"])):
            if a["gold"].get(key) != b["gold"].get(key):
                raise CompareError(f"gold data differs ({key}): scores against different judgments are not "
                                   "comparable; re-run the older retriever or pass force=True")
    lines = [f"# Compare: {a['label']} -> {b['label']}", ""]
    lines += ["| group | metric | " + a["label"] + " | " + b["label"] + " | delta |", "|---|---|---|---|---|"]
    groups = {"all": (a["summary"]["overall"], b["summary"]["overall"])}
    for language in sorted(set(a["summary"]["byLanguage"]) | set(b["summary"]["byLanguage"])):
        groups[language] = (a["summary"]["byLanguage"].get(language, {}), b["summary"]["byLanguage"].get(language, {}))
    for name, (left, right) in groups.items():
        for key, title in METRICS[:3]:
            old, new = left.get(key), right.get(key)
            delta = None if old is None or new is None else new - old
            sign = "" if delta is None or delta < 0 else "+"
            lines.append(f"| {name} | {title} | {_fmt(old)} | {_fmt(new)} | "
                         f"{'-' if delta is None else sign + format(delta, '.3f')} |")
    worse = []
    for query_id, new in b["queries"].items():
        old = a["queries"].get(query_id)
        if old and old["ndcg"] is not None and new["ndcg"] is not None and new["ndcg"] - old["ndcg"] <= -drop_threshold:
            worse.append((new["ndcg"] - old["ndcg"], query_id))
    lines += ["", f"## Queries that got worse (nDCG@10 down by at least {drop_threshold})", ""]
    if not worse:
        lines.append("No query got worse by that much.")
    for delta, query_id in sorted(worse):
        text = (query_texts or {}).get(query_id, "")
        lines.append(f"- {query_id} ({delta:.3f}): {text}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 8: Run all evaluation tests**

Run: `python -m pytest tests/evaluation -q`
Expected: all PASS. Note on the compare test: the overall nDCG mean is (1.0 + 0.5)/2 = 0.75 before and (0.6 + 0.5)/2 = 0.55 after, so the delta line shows `-0.200`; query q1 dropped 0.4 and shows `-0.400`; `text.split("got worse")[1]` is the section after the heading and must not mention q2.

- [ ] **Step 9: Commit**

```bash
git add evaluation tests/evaluation
git commit -m "feat(evaluation): add pooling, runner, reports, baselines and compare"
```

---

### Task 3: Baseline retrievers

**Files:**
- Create: `evaluation/retrievers/random_retriever.py`, `lexical.py`, `situation_embedding.py`, `axis_match.py`
- Create: `tests/evaluation/test_random_retriever.py`, `tests/integration/test_evaluation_retrievers.py`

**Interfaces:**
- Consumes: Task 1 (`GoldQuery`, `ExpectedProfile`), Task 2 (`Retriever` protocol), `repository.ocr_lemmas.scored_image_matches(session, q)`, `Storage.models.{MemeProfileValue, MemeProfileSituationEmbedding, PROFILE_EMBEDDING_DIM}`, `ai.ollama_embed.OllamaEmbedder`-compatible embedders (`embed(list[str]) -> ndarray`).
- Produces: `RandomRetriever(seed: int = 0)` (`name = "random"`), `LexicalRetriever(session, mode: str)` (`name = f"lexical_{mode}"`, mode `"and"` or `"or"`), `SituationEmbeddingRetriever(session, embedder, model_name)` (`name = "situation_embedding"`, defines `prepare`), `AxisMatchRetriever(session)` (`name = "axis_match"`). Every retriever takes `corpus_ids` as strings, restricts results to them, and orders by descending score then ascending image id.

- [ ] **Step 1: Write the failing random-retriever unit test**

Create `tests/evaluation/test_random_retriever.py`:

```python
import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.random_retriever import RandomRetriever

EXPECTED = ExpectedProfile("s", (), (), (), ())


def _query(query_id):
    return GoldQuery(query_id, "en", "text", "short", "easy", EXPECTED, True)


@pytest.mark.asyncio
async def test_random_retriever_is_deterministic_per_query_and_seed_and_restricted_to_the_corpus():
    corpus = [f"img-{i:03d}" for i in range(50)]
    retriever = RandomRetriever(seed=3)

    first = await retriever.retrieve(_query("q1"), corpus, 10)
    again = await retriever.retrieve(_query("q1"), corpus, 10)
    other_query = await retriever.retrieve(_query("q2"), corpus, 10)
    other_seed = await RandomRetriever(seed=4).retrieve(_query("q1"), corpus, 10)

    assert first == again and len(first) == 10
    assert first != other_query and first != other_seed
    assert {image_id for image_id, _ in first} <= set(corpus)
    scores = [score for _, score in first]
    assert scores == sorted(scores, reverse=True)
    assert RandomRetriever.name == "random" and RandomRetriever(seed=3).config == {"seed": 3}


@pytest.mark.asyncio
async def test_random_retriever_returns_everything_when_the_corpus_is_smaller_than_k():
    result = await RandomRetriever().retrieve(_query("q1"), ["a", "b"], 10)
    assert sorted(image_id for image_id, _ in result) == ["a", "b"]
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/integration/test_evaluation_retrievers.py`:

```python
"""
Integration tests for the evaluation baseline retrievers. Requires a live PostgreSQL instance with
pgvector -- see tests/integration/conftest.py. No Ollama: the embedder is a fake.
"""
import uuid

import numpy as np
import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.axis_match import AxisMatchRetriever
from evaluation.retrievers.lexical import LexicalRetriever
from evaluation.retrievers.situation_embedding import SituationEmbeddingRetriever
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import PROFILE_EMBEDDING_DIM, Image, OCRLemma


def _query(text="hello", **expected):
    values = dict(situation="s", emotion=(), stance=(), topic=(), tone=())
    values.update(expected)
    return GoldQuery("q1", "en", text, "short", "easy", ExpectedProfile(**values), True)


async def _image(session, *lemmas) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image.id, lemma=lemma))
    await session.flush()
    return image


async def _profile(session, image, *, situation="a situation", **axes):
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    await MemeProfileRepository(session).save_profile(
        image.id, ProfileData(situation, None, None, values), ProfileMeta(2, "text-model", "p2-test", "f" * 64, False))
    await session.flush()


def _vector(*leading):
    vector = np.zeros(PROFILE_EMBEDDING_DIM, dtype=np.float32)
    vector[:len(leading)] = leading
    return vector.tolist()


class FakeEmbedder:
    def __init__(self, vectors_by_text):
        self.vectors_by_text = vectors_by_text
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return np.array([self.vectors_by_text[text] for text in texts], dtype=np.float32)


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_and_requires_every_word_and_is_empty_for_a_message_with_an_unmatched_word(db_session):
    both = await _image(db_session, "boyfriend", "pizza")
    one = await _image(db_session, "boyfriend")
    corpus = [str(both.id), str(one.id)]

    matching = await LexicalRetriever(db_session, "and").retrieve(_query("boyfriend pizza"), corpus, 10)
    unmatched = await LexicalRetriever(db_session, "and").retrieve(_query("boyfriend zzzqqq"), corpus, 10)

    assert [image_id for image_id, _ in matching] == [str(both.id)]
    assert unmatched == []
    assert LexicalRetriever(db_session, "and").name == "lexical_and"


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_or_scores_any_token_and_ranks_more_matches_higher(db_session):
    best = await _image(db_session, "boyfriend", "pizza")
    partial = await _image(db_session, "pizza")
    nothing = await _image(db_session, "weather")
    corpus = [str(best.id), str(partial.id), str(nothing.id)]

    result = await LexicalRetriever(db_session, "or").retrieve(_query("boyfriend pizza tonight"), corpus, 10)

    ids = [image_id for image_id, _ in result]
    assert ids == [str(best.id), str(partial.id)]
    assert result[0][1] > result[1][1] > 0


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_results_are_restricted_to_the_corpus(db_session):
    inside = await _image(db_session, "pizza")
    outside = await _image(db_session, "pizza")

    result = await LexicalRetriever(db_session, "or").retrieve(_query("pizza"), [str(inside.id)], 10)

    assert [image_id for image_id, _ in result] == [str(inside.id)]
    assert str(outside.id) not in [image_id for image_id, _ in result]


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_match_ranks_by_agreement_weighted_by_confidence_and_skips_non_matches(db_session):
    strong = await _image(db_session)
    weak = await _image(db_session)
    unrelated = await _image(db_session)
    await _profile(db_session, strong, emotion=[("anger", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, weak, emotion=[("anger", 0.3)], stance=[("refuse", 0.9)])
    await _profile(db_session, unrelated, emotion=[("joy", 0.9)], stance=[("agree", 0.9)])
    corpus = [str(strong.id), str(weak.id), str(unrelated.id)]

    result = await AxisMatchRetriever(db_session).retrieve(_query(emotion=("anger",), stance=("mock",)), corpus, 10)

    assert [image_id for image_id, _ in result] == [str(strong.id), str(weak.id)]
    assert result[0][1] == pytest.approx((0.9 + 0.9) / 2)
    assert result[1][1] == pytest.approx((0.3 + 0.0) / 2)


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_match_with_no_expected_values_returns_nothing(db_session):
    image = await _image(db_session)
    await _profile(db_session, image, emotion=[("anger", 0.9)])

    assert await AxisMatchRetriever(db_session).retrieve(_query(), [str(image.id)], 10) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_embedding_ranks_by_cosine_within_the_corpus_and_the_configured_model(db_session):
    near = await _image(db_session)
    far = await _image(db_session)
    outside = await _image(db_session)
    other_model = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    for image in (near, far, outside, other_model):
        await _profile(db_session, image)
    await repo.save_situation_embedding(near.id, _vector(1.0, 0.1), "bge-large", "d1")
    await repo.save_situation_embedding(far.id, _vector(0.0, 1.0), "bge-large", "d1")
    await repo.save_situation_embedding(outside.id, _vector(1.0, 0.0), "bge-large", "d1")
    await repo.save_situation_embedding(other_model.id, _vector(1.0, 0.0), "another-model", "d9")
    await db_session.flush()
    embedder = FakeEmbedder({"my boss moved the deadline": _vector(1.0, 0.0)})
    retriever = SituationEmbeddingRetriever(db_session, embedder, "bge-large")
    query = _query("my boss moved the deadline")

    await retriever.prepare([query])
    result = await retriever.retrieve(query, [str(near.id), str(far.id), str(other_model.id)], 10)

    assert [image_id for image_id, _ in result] == [str(near.id), str(far.id)]
    assert result[0][1] > result[1][1]
    assert embedder.calls == [["my boss moved the deadline"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_embedding_without_prepare_embeds_on_demand(db_session):
    image = await _image(db_session)
    await _profile(db_session, image)
    await MemeProfileRepository(db_session).save_situation_embedding(image.id, _vector(1.0), "bge-large", "d1")
    await db_session.flush()
    retriever = SituationEmbeddingRetriever(db_session, FakeEmbedder({"hello": _vector(1.0)}), "bge-large")

    result = await retriever.retrieve(_query("hello"), [str(image.id)], 10)

    assert [image_id for image_id, _ in result] == [str(image.id)]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/evaluation/test_random_retriever.py -q`
Expected: collection ERROR (`ModuleNotFoundError: No module named 'evaluation.retrievers.random_retriever'`).
Run (separate command): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/test_evaluation_retrievers.py -q`
Expected: collection ERROR (modules missing).

- [ ] **Step 4: Write the retrievers**

`evaluation/retrievers/random_retriever.py`:

```python
import random
from typing import Sequence

from evaluation.gold import GoldQuery


class RandomRetriever:
    """The floor: a seeded shuffle of the corpus, different per query."""

    name = "random"

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.config = {"seed": seed}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        order = sorted(corpus_ids)
        random.Random(f"{self.seed}:{query.id}").shuffle(order)
        top = order[:k]
        return [(image_id, 1.0 - index / max(len(top), 1)) for index, image_id in enumerate(top)]
```

`evaluation/retrievers/lexical.py`:

```python
import re
import uuid
from typing import Sequence

from evaluation.gold import GoldQuery
from repository.ocr_lemmas import scored_image_matches

TOKEN = re.compile(r"\w+", re.UNICODE)


class LexicalRetriever:
    """The production keyword search (mode "and": every query lemma must match, so a long message is usually
    empty) or its any-token variant (mode "or": per-token scores summed, same source and tier weights)."""

    def __init__(self, session, mode: str):
        if mode not in ("and", "or"):
            raise ValueError("mode must be 'and' or 'or'")
        self.session = session
        self.mode = mode
        self.name = f"lexical_{mode}"
        self.config = {"mode": mode}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        corpus = {uuid.UUID(image_id) for image_id in corpus_ids}
        if self.mode == "and":
            scores = await scored_image_matches(self.session, query.text) or {}
        else:
            scores = {}
            for token in dict.fromkeys(token.lower() for token in TOKEN.findall(query.text)):
                token_scores = await scored_image_matches(self.session, token)
                for image_id, score in (token_scores or {}).items():
                    scores[image_id] = scores.get(image_id, 0.0) + score
        ranked = sorted(((str(image_id), score) for image_id, score in scores.items() if image_id in corpus),
                        key=lambda pair: (-pair[1], pair[0]))
        return ranked[:k]
```

`evaluation/retrievers/situation_embedding.py`:

```python
import asyncio
import uuid
from typing import Sequence

from sqlalchemy import select

from evaluation.gold import GoldQuery
from Storage.models import MemeProfileSituationEmbedding as Embedding


class SituationEmbeddingRetriever:
    """The embedding-only path: embed the raw message and rank corpus profiles by cosine similarity to their
    stored situation vectors (no translation; the cross-language probe for the P1 embedding decision)."""

    name = "situation_embedding"

    def __init__(self, session, embedder, model_name: str):
        self.session = session
        self.embedder = embedder
        self.model_name = model_name
        self.config = {"embeddingModel": model_name}
        self._vectors: dict[str, list[float]] = {}

    async def prepare(self, queries: Sequence[GoldQuery]) -> None:
        texts = [query.text for query in queries]
        if not texts:
            return
        vectors = await asyncio.to_thread(self.embedder.embed, texts)
        self._vectors = {query.text: vector.tolist() for query, vector in zip(queries, vectors)}

    async def _vector(self, query: GoldQuery) -> list[float]:
        if query.text not in self._vectors:
            vectors = await asyncio.to_thread(self.embedder.embed, [query.text])
            self._vectors[query.text] = vectors[0].tolist()
        return self._vectors[query.text]

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        vector = await self._vector(query)
        distance = Embedding.embedding.cosine_distance(vector)
        rows = await self.session.execute(
            select(Embedding.image_id, distance)
            .where(Embedding.image_id.in_([uuid.UUID(image_id) for image_id in corpus_ids]),
                   Embedding.model_name == self.model_name)
            .order_by(distance, Embedding.image_id)
            .limit(k)
        )
        return [(str(image_id), 1.0 - float(value)) for image_id, value in rows.all()]
```

`evaluation/retrievers/axis_match.py`:

```python
import uuid
from collections import defaultdict
from typing import Sequence

from sqlalchemy import select

from evaluation.gold import GoldQuery
from Storage.models import MemeProfileValue


class AxisMatchRetriever:
    """Baseline using the query's GOLD expected profile (no parsing): per axis with expected values, the
    confidence-weighted share of expected values present on the image's profile; the score is the mean over
    those axes. Isolates the quality of P2's profiles from P3's parsing. Not the P4 fusion."""

    name = "axis_match"
    config: dict = {}

    def __init__(self, session):
        self.session = session

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        expected = {axis: values for axis, values in query.expected.axis_values().items() if values}
        if not expected:
            return []
        rows = await self.session.execute(
            select(MemeProfileValue.image_id, MemeProfileValue.axis, MemeProfileValue.value_key,
                   MemeProfileValue.confidence)
            .where(MemeProfileValue.image_id.in_([uuid.UUID(image_id) for image_id in corpus_ids]),
                   MemeProfileValue.axis.in_(list(expected)))
        )
        by_image: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for image_id, axis, key, confidence in rows.all():
            by_image[str(image_id)][axis][key] = confidence
        scored = []
        for image_id, axes in by_image.items():
            per_axis = [sum(axes.get(axis, {}).get(key, 0.0) for key in values) / len(values)
                        for axis, values in expected.items()]
            score = sum(per_axis) / len(per_axis)
            if score > 0:
                scored.append((image_id, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k]
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/evaluation -q`
Expected: all PASS.
Run (separate command): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/test_evaluation_retrievers.py -q`
Expected: all PASS. Notes: in the lexical tests the lemmas are inserted directly into `ocr_lemmas`; `scored_image_matches` normalises the query token with the real normaliser, and words used in the tests (`boyfriend`, `pizza`, `weather`) are already their own lemmas. The unmatched word `zzzqqq` has no exact hit and (length 6) triggers only the fuzzy fallback, which finds nothing similar in the seeded data.

- [ ] **Step 6: Commit**

```bash
git add evaluation tests/evaluation tests/integration/test_evaluation_retrievers.py
git commit -m "feat(evaluation): add random, lexical, situation-embedding and axis-match retrievers"
```

---

### Task 4: `--ids-file` for the P2 jobs

**Files:**
- Create: `batch/utils/ids_file.py`, `batch/tests/test_ids_file.py`
- Modify: `repository/meme_profiles.py`, `repository/image_procesing_status.py`
- Modify: `batch/build_meme_profiles.py`, `batch/build_meme_profile_embeddings.py`
- Modify: `batch/tests/test_build_meme_profiles_main.py`, `batch/tests/test_build_meme_profile_embeddings_main.py`
- Modify: `tests/integration/test_build_meme_profiles.py`, `tests/integration/test_build_meme_profile_embeddings.py`, `tests/integration/test_meme_profiles_repository.py`, `tests/integration/test_image_processing_status_repository.py`

**Interfaces:**
- Produces: `batch.utils.ids_file.load_ids_file(path) -> frozenset[uuid.UUID]` (JSON list or one UUID per line, `#` comments, at most `MAX_IDS = 20000`); `MemeProfileRepository.delete_model_generated(image_ids=None)`, `delete_all_embeddings(image_ids=None)`, `get_profiles_needing_embedding(model_name, model_digest, image_ids=None)`; `ImageProcessingStatusRepository.delete_for_images(image_ids) -> None`; `build_meme_profiles._collect_candidates(session, config, only_ids=None)`, `_process(reset, limit, retry_failed, only_ids=None)`, `main(..., only_ids=None)`; `build_meme_profile_embeddings._process(reset, limit, only_ids=None)`, `main(..., only_ids=None)`; both CLIs accept `--ids-file PATH`.

- [ ] **Step 1: Write the failing `ids_file` tests**

Create `batch/tests/test_ids_file.py`:

```python
import json
import uuid

import pytest

from batch.utils.ids_file import MAX_IDS, load_ids_file

A, B = uuid.uuid4(), uuid.uuid4()


def test_reads_a_json_list(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps([str(A), str(B), str(A)]), encoding="utf-8")

    assert load_ids_file(str(path)) == frozenset({A, B})


def test_reads_one_uuid_per_line_ignoring_blank_lines_and_comments(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text(f"# corpus ids\n{A}\n\n  {B}  \n", encoding="utf-8")

    assert load_ids_file(str(path)) == frozenset({A, B})


def test_invalid_uuid_empty_file_and_non_list_json_are_refused(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("not-a-uuid\n", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    obj = tmp_path / "obj.json"
    obj.write_text('{"a": 1}', encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("[1, 2", encoding="utf-8")

    with pytest.raises(ValueError, match="not a UUID"):
        load_ids_file(str(bad))
    with pytest.raises(ValueError, match="empty"):
        load_ids_file(str(empty))
    with pytest.raises(ValueError, match="list"):
        load_ids_file(str(obj))
    with pytest.raises(ValueError):
        load_ids_file(str(broken))


def test_more_than_the_maximum_number_of_ids_is_refused(tmp_path):
    path = tmp_path / "many.txt"
    path.write_text("\n".join(str(uuid.uuid4()) for _ in range(MAX_IDS + 1)), encoding="utf-8")

    with pytest.raises(ValueError, match="at most"):
        load_ids_file(str(path))
```

- [ ] **Step 2: Write the failing repository tests (append to existing files)**

Append to `tests/integration/test_meme_profiles_repository.py`:

```python


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_model_generated_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(first.id, _data(emotion=[("joy", 0.9)]), _meta())
    await repo.save_profile(second.id, _data(emotion=[("anger", 0.9)]), _meta())
    await db_session.flush()

    deleted = await repo.delete_model_generated({first.id})
    await db_session.flush()

    assert deleted == 1
    assert await db_session.get(MemeProfile, first.id) is None
    assert await db_session.get(MemeProfile, second.id) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_embeddings_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    for image in (first, second):
        await repo.save_profile(image.id, _data(), _meta())
        await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    assert await repo.delete_all_embeddings({first.id}) == 1
    await db_session.flush()

    assert [image_id for image_id, _ in await repo.get_profiles_needing_embedding("bge-large", "digest-1")] == [first.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_profiles_needing_embedding_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(first.id, _data(situation="one"), _meta())
    await repo.save_profile(second.id, _data(situation="two"), _meta())
    await db_session.flush()

    only_first = await repo.get_profiles_needing_embedding("bge-large", "d1", {first.id})
    everything = await repo.get_profiles_needing_embedding("bge-large", "d1")

    assert only_first == [(first.id, "one")]
    assert {image_id for image_id, _ in everything} == {first.id, second.id}
```

Append to `tests/integration/test_image_processing_status_repository.py` (use the file's existing imports and helper style; add `import uuid` and the model imports if missing):

```python


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_for_images_removes_only_the_given_images_rows_of_this_pipeline(db_session):
    from repository.image_procesing_status import ImageProcessingStatusRepository
    from Storage.models import Image

    first, second = Image(filename=f"{uuid.uuid4()}.jpg"), Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([first, second])
    await db_session.flush()
    repo = ImageProcessingStatusRepository(db_session, "test_pipeline")
    other = ImageProcessingStatusRepository(db_session, "other_pipeline")
    await repo.record_failure(first.id, "boom")
    await repo.record_failure(second.id, "boom")
    await other.record_failure(first.id, "kept")
    await db_session.flush()

    await repo.delete_for_images({first.id})
    await db_session.flush()

    assert await repo.get_image_ids_with_status("failed") == {second.id}
    assert await other.get_image_ids_with_status("failed") == {first.id}
```

- [ ] **Step 3: Update and extend the job tests**

In `batch/tests/test_build_meme_profiles_main.py`, change the two `_process` assertions in `TestMain` and add one test:
- `process_mock.assert_awaited_once_with(reset=False, limit=7, retry_failed=True)` becomes `process_mock.assert_awaited_once_with(reset=False, limit=7, retry_failed=True, only_ids=None)`;
- `process_mock.assert_awaited_once_with(reset=True, limit=None, retry_failed=False)` becomes `process_mock.assert_awaited_once_with(reset=True, limit=None, retry_failed=False, only_ids=None)`;
- add inside `TestMain`:

```python
    @pytest.mark.asyncio
    async def test_only_ids_is_passed_through_to_process(self):
        process_mock = AsyncMock(return_value={"images_selected": 0, "images_remaining": 0})
        ids = frozenset({"id-1"})

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", AsyncMock()):
            await module.main(trigger="manual", only_ids=ids)

        process_mock.assert_awaited_once_with(reset=False, limit=None, retry_failed=False, only_ids=ids)
```

In `batch/tests/test_build_meme_profile_embeddings_main.py`: `process_mock.assert_awaited_once_with(reset=False, limit=5)` becomes `(reset=False, limit=5, only_ids=None)`; `process_mock.assert_awaited_once_with(reset=True, limit=None)` becomes `(reset=True, limit=None, only_ids=None)`; add inside `TestMain`:

```python
    @pytest.mark.asyncio
    async def test_only_ids_is_passed_through_to_process(self):
        process_mock = AsyncMock(return_value={"images_selected": 0, "images_remaining": 0})
        ids = frozenset({"id-1"})

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", AsyncMock()):
            await module.main(trigger="manual", only_ids=ids)

        process_mock.assert_awaited_once_with(reset=False, limit=None, only_ids=ids)
```

Append to `tests/integration/test_build_meme_profiles.py` (it already has `_insert_image`, `_add_description`, `_run`, `CONFIG`, `LONG_TEXT`):

```python


@pytest.mark.asyncio(loop_scope="session")
async def test_only_ids_restricts_selection_to_the_listed_images(db_session):
    listed = await _insert_image(db_session)
    await _add_description(db_session, listed)
    unlisted = await _insert_image(db_session)
    await _add_description(db_session, unlisted)
    calls: list = []

    stats = await _run(db_session, calls, only_ids=frozenset({listed.id}))

    assert stats["saved"] == 1 and len(calls) == 1
    assert await db_session.get(MemeProfile, listed.id) is not None
    assert await db_session.get(MemeProfile, unlisted.id) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_with_only_ids_deletes_only_the_listed_images_profiles_and_failures(db_session):
    listed = await _insert_image(db_session)
    await _add_description(db_session, listed)
    other = await _insert_image(db_session)
    await _add_description(db_session, other)
    await _run(db_session, [])                                   # profiles for both
    status_repo = ImageProcessingStatusRepository(db_session, PIPELINE)
    await status_repo.record_failure(listed.id, "old failure")
    await status_repo.record_failure(other.id, "old failure")
    await db_session.flush()

    calls: list = []
    await _run(db_session, calls, reset=True, only_ids=frozenset({listed.id}))

    assert len(calls) == 1                                       # only the listed image was re-extracted
    assert await db_session.get(MemeProfile, other.id) is not None
    failed = await status_repo.get_image_ids_with_status("failed")
    assert other.id in failed and listed.id not in failed
```

Append to `tests/integration/test_build_meme_profile_embeddings.py` (it has `_profile`, `_run`, `_fake_embedder_class`, `_embeddings`):

```python


@pytest.mark.asyncio(loop_scope="session")
async def test_only_ids_restricts_embedding_to_the_listed_profiles(db_session):
    listed = await _profile(db_session, "listed situation")
    await _profile(db_session, "unlisted situation")
    embedded: list = []

    stats = await _run(db_session, _fake_embedder_class(embedded), only_ids=frozenset({listed.id}))

    assert embedded == ["listed situation"] and stats["embedded"] == 1
    assert [row.image_id for row in await _embeddings(db_session)] == [listed.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_with_only_ids_deletes_only_the_listed_embeddings(db_session):
    listed = await _profile(db_session, "listed situation")
    other = await _profile(db_session, "other situation")
    await _run(db_session, _fake_embedder_class([]))             # both embedded

    embedded: list = []
    await _run(db_session, _fake_embedder_class(embedded), reset=True, only_ids=frozenset({listed.id}))

    assert embedded == ["listed situation"]
    assert {row.image_id for row in await _embeddings(db_session)} == {listed.id, other.id}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest batch/tests/test_ids_file.py -q` → collection ERROR (`batch.utils.ids_file` missing).
Run (separate command): `python -m pytest batch/tests/test_build_meme_profiles_main.py batch/tests/test_build_meme_profile_embeddings_main.py -q` → FAIL (unexpected `only_ids`).
Run (separate command): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/test_meme_profiles_repository.py tests/integration/test_image_processing_status_repository.py tests/integration/test_build_meme_profiles.py tests/integration/test_build_meme_profile_embeddings.py -q` → FAIL on the new tests.

- [ ] **Step 5: Write `batch/utils/ids_file.py`**

```python
"""Reads an --ids-file for the meme profile jobs (P6 spec, decision 9)."""
import json
import uuid
from pathlib import Path

MAX_IDS = 20000


def load_ids_file(path: str) -> frozenset[uuid.UUID]:
    """A JSON list of UUID strings, or a text file with one UUID per line (blank lines and `#` comments ignored)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"ids file {path} is empty")
    if text.startswith("[") or text.startswith("{"):
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError(f"ids file {path} must contain a JSON list of UUIDs")
        items = [str(item) for item in raw]
    else:
        items = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    ids: set[uuid.UUID] = set()
    for item in items:
        try:
            ids.add(uuid.UUID(item))
        except ValueError:
            raise ValueError(f"not a UUID: {item!r}") from None
    if len(ids) > MAX_IDS:
        raise ValueError(f"an ids file may hold at most {MAX_IDS} ids, got {len(ids)}")
    return frozenset(ids)
```

- [ ] **Step 6: Modify the repositories**

In `repository/meme_profiles.py` change three methods (add `from typing import Collection` to the imports):

```python
    async def delete_model_generated(self, image_ids: Collection[uuid.UUID] | None = None) -> int:
        """`--reset`: delete every profile that is not human-verified (child rows cascade in the database),
        optionally only those of the given images."""
        statement = delete(MemeProfile).where(MemeProfile.human_verified.is_(False))
        if image_ids is not None:
            statement = statement.where(MemeProfile.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return result.rowcount

    async def delete_all_embeddings(self, image_ids: Collection[uuid.UUID] | None = None) -> int:
        statement = delete(MemeProfileSituationEmbedding)
        if image_ids is not None:
            statement = statement.where(MemeProfileSituationEmbedding.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return result.rowcount

    async def get_profiles_needing_embedding(self, model_name: str, model_digest: str,
                                             image_ids: Collection[uuid.UUID] | None = None
                                             ) -> list[tuple[uuid.UUID, str]]:
        """Profiles with a non-blank situation_text and no embedding row, or one from a different model or
        digest, optionally only among the given images."""
        statement = (
            select(MemeProfile.image_id, MemeProfile.situation_text)
            .outerjoin(MemeProfileSituationEmbedding, MemeProfileSituationEmbedding.image_id == MemeProfile.image_id)
            .where(
                MemeProfile.situation_text.is_not(None),
                func.length(func.trim(MemeProfile.situation_text)) > 0,
                or_(
                    MemeProfileSituationEmbedding.image_id.is_(None),
                    MemeProfileSituationEmbedding.model_name != model_name,
                    MemeProfileSituationEmbedding.model_digest != model_digest,
                ),
            )
            .order_by(MemeProfile.image_id)
        )
        if image_ids is not None:
            statement = statement.where(MemeProfile.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return [(image_id, text) for image_id, text in result.all()]
```

In `repository/image_procesing_status.py` add after `delete_all` (the file already imports `delete` and `ImageProcessingStatus`):

```python
    async def delete_for_images(self, image_ids) -> None:
        """No commit -- same convention as delete_all. Only this pipeline's rows for the given images."""
        await self.session.execute(
            delete(ImageProcessingStatus).where(
                ImageProcessingStatus.pipeline == self.pipeline,
                ImageProcessingStatus.image_id.in_(list(image_ids)),
            )
        )
```

- [ ] **Step 7: Modify the two jobs**

In `batch/build_meme_profiles.py`:
- import: `from batch.utils.ids_file import load_ids_file`;
- `_collect_candidates` signature `async def _collect_candidates(session, config, only_ids=None) -> list[Candidate]:` and right after `images = await inputs_repo.list_active_images()` add:
  ```python
      if only_ids is not None:
          images = [image for image in images if image[0] in only_ids]
  ```
- `_process` signature `async def _process(reset: bool, limit: int | None, retry_failed: bool, only_ids=None) -> dict:`; the reset block becomes:
  ```python
          if reset:
              deleted = await profiles.delete_model_generated(only_ids)
              if only_ids is None:
                  await status_repo.delete_all()
              else:
                  await status_repo.delete_for_images(only_ids)
              await session.commit()
              print(f"Deleted {deleted} model-generated profile(s) (human-verified profiles are kept)")
  ```
  and `candidates = await _collect_candidates(session, config, only_ids)`;
- `main` gains `only_ids=None` (last parameter) and passes `only_ids=only_ids` to both `_process(...)` calls;
- CLI: add `parser.add_argument("--ids-file", default=None, help="Only consider the images listed in this file (JSON list or one UUID per line); --reset then only resets those images")`, and replace the last line with `only_ids = load_ids_file(args.ids_file) if args.ids_file else None` followed by `asyncio.run(main(reset=args.reset, limit=args.limit, retry_failed=args.retry_failed, only_ids=only_ids))`.

In `batch/build_meme_profile_embeddings.py`:
- import `from batch.utils.ids_file import load_ids_file`;
- `_process(reset: bool, limit: int | None, only_ids=None)`; reset: `deleted = await repo.delete_all_embeddings(only_ids)`; selection: `pending = await repo.get_profiles_needing_embedding(model_name, model_digest, only_ids)`;
- `main(..., limit=None, only_ids=None)` passing `only_ids=only_ids`;
- CLI `--ids-file` as above and `asyncio.run(main(reset=args.reset, limit=args.limit, only_ids=only_ids))`.

- [ ] **Step 8: Run the tests**

Run each as its own command: `python -m pytest batch/tests -q`; `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/ -q` (the whole root: `repository/` and `batch/` changed); `python -m pytest tests/evaluation -q`.
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add batch/utils/ids_file.py batch/tests repository batch/build_meme_profiles.py batch/build_meme_profile_embeddings.py tests/integration
git commit -m "feat(meme-profiles): add --ids-file to the profile and embedding jobs"
```

---

### Task 5: Corpus builder and profile state

**Files:**
- Create: `evaluation/corpus.py`, `evaluation/state.py`
- Create: `tests/evaluation/test_corpus.py`, `tests/integration/test_evaluation_corpus.py`
- Modify: `.gitignore` (add `evaluation/work/`)

**Interfaces:**
- Consumes: Task 1 (`Corpus`, `CorpusImage`, `STRATA`), P2 (`MemeProfileInputsRepository`, `build_bundle`, `choose_path`, `PATH_VISION`, `MemeProfilesConfig`), `has_unsupported_image_extension`, `Storage.models.{ImageExtras, TmpImageClusters, MemeProfile, MemeProfileSituationEmbedding}`.
- Produces: `evaluation.corpus.quotas_for(size) -> dict[str, int]`, `stratum_of(bundle, min_text_chars) -> str`, `CorpusCandidate(image_id, content_hash, stratum, cluster_key)`, `select_corpus(candidates, quotas, seed) -> list[CorpusImage]`, `async load_candidates(session, config) -> list[CorpusCandidate]`, `write_ids_file(path, image_ids)`; `evaluation.state.collect_profile_state(session, corpus_ids) -> dict` with keys `corpusImages`, `profiled`, `taxonomyVersions`, `promptVersions`, `extractorModels`, `embedded`, `embeddingModels`.

- [ ] **Step 1: Write the failing pure corpus tests**

Create `tests/evaluation/test_corpus.py`:

```python
import pytest

from batch.utils.meme_profile_bundle import Bundle
from evaluation.corpus import CorpusCandidate, quotas_for, select_corpus, stratum_of, write_ids_file
from evaluation.gold import STRATA


def _candidate(index, stratum, cluster=None, content_hash="h"):
    image_id = f"{index:08d}-0000-0000-0000-000000000000"
    return CorpusCandidate(image_id, content_hash, stratum, cluster or f"s{image_id}")


def test_quotas_follow_the_shares_and_always_sum_to_the_size():
    assert quotas_for(1500) == {"ocr+description": 900, "ocr only": 300, "description only": 150, "no usable text": 150}
    for size in (10, 99, 1234):
        assert sum(quotas_for(size).values()) == size
    assert list(quotas_for(100)) == list(STRATA)


def test_stratum_of_a_bundle():
    long_text = "x" * 60
    assert stratum_of(Bundle(None, (("k", long_text),), long_text), 40) == "ocr+description"
    assert stratum_of(Bundle(None, (), long_text), 40) == "ocr only"
    assert stratum_of(Bundle(None, (("k", long_text),), None), 40) == "description only"
    assert stratum_of(Bundle(None, (), "short"), 40) == "no usable text"
    assert stratum_of(Bundle("a note " * 10, (), None), 40) == "description only"


def test_selection_takes_the_quota_per_stratum_deterministically():
    candidates = ([_candidate(i, "ocr+description") for i in range(100)]
                  + [_candidate(1000 + i, "ocr only") for i in range(50)]
                  + [_candidate(2000 + i, "description only") for i in range(20)]
                  + [_candidate(3000 + i, "no usable text") for i in range(20)])
    quotas = {"ocr+description": 12, "ocr only": 4, "description only": 2, "no usable text": 2}

    first = select_corpus(candidates, quotas, seed=7)
    again = select_corpus(candidates, quotas, seed=7)
    other = select_corpus(candidates, quotas, seed=8)

    assert first == again and first != other
    counts = {s: sum(1 for image in first if image.stratum == s) for s in STRATA}
    assert counts == quotas
    ids = [image.image_id for image in first]
    assert len(ids) == len(set(ids))


def test_shortfall_in_a_small_stratum_is_redistributed_to_ocr_and_description():
    candidates = ([_candidate(i, "ocr+description") for i in range(50)]
                  + [_candidate(1000 + i, "no usable text") for i in range(3)])
    quotas = {"ocr+description": 10, "ocr only": 0, "description only": 0, "no usable text": 6}

    selected = select_corpus(candidates, quotas, seed=1)

    counts = {s: sum(1 for image in selected if image.stratum == s) for s in STRATA}
    assert counts["no usable text"] == 3 and counts["ocr+description"] == 13 and len(selected) == 16


def test_only_the_lowest_image_id_of_a_duplicate_cluster_is_eligible():
    a, b, c = _candidate(1, "ocr only", cluster="c1"), _candidate(2, "ocr only", cluster="c1"), _candidate(3, "ocr only")
    quotas = {"ocr+description": 0, "ocr only": 10, "description only": 0, "no usable text": 0}

    selected = select_corpus([b, a, c], quotas, seed=1)

    assert sorted(image.image_id for image in selected) == sorted([a.image_id, c.image_id])


def test_content_hash_and_stratum_are_carried_into_the_corpus_images():
    selected = select_corpus([_candidate(1, "ocr only", content_hash="abc")],
                             {"ocr+description": 0, "ocr only": 5, "description only": 0, "no usable text": 0}, seed=1)

    assert selected[0].content_hash == "abc" and selected[0].stratum == "ocr only"


def test_write_ids_file_writes_one_uuid_per_line(tmp_path):
    path = tmp_path / "corpus.ids"

    write_ids_file(path, ["b", "a"])

    assert path.read_text(encoding="utf-8") == "a\nb\n"
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/integration/test_evaluation_corpus.py`:

```python
"""
Integration tests for the evaluation corpus builder and profile-state report. Requires a live PostgreSQL
instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from batch.utils.meme_profile_bundle import BundleLimits
from batch.utils.meme_profile_config import MemeProfilesConfig
from evaluation.corpus import load_candidates
from evaluation.state import collect_profile_state
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageDescription, ImageExtras, OCRText, TmpImageClusters,
)

CONFIG = MemeProfilesConfig(
    text_model="t", vision_model="v", num_ctx=1, batch_size=1, concurrent_requests=1, min_text_chars=20, seed=1,
    ocr_confidence_min=0.4, ocr_lang_score_min=0.3, limits=BundleLimits(200, 200, 200))
LONG = "a cat looking betrayed by a closed door"


async def _image(session, filename=None) -> Image:
    image = Image(filename=filename or f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


async def _with_text(session, image, *, description=True, ocr=True):
    if description:
        session.add(ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text=LONG))
    if ocr:
        session.add(OCRText(image_id=image.id, text=LONG, confidence=0.9, lang_score=0.9, language="en"))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_candidates_carry_strata_hashes_and_exclude_flagged_unsupported_and_inactive(db_session):
    both = await _image(db_session)
    await _with_text(db_session, both)
    ocr_only = await _image(db_session)
    await _with_text(db_session, ocr_only, description=False)
    description_only = await _image(db_session)
    await _with_text(db_session, description_only, ocr=False)
    no_text = await _image(db_session)
    flagged = await _image(db_session)
    await _with_text(db_session, flagged)
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    gif = await _image(db_session, filename=f"{uuid.uuid4()}.gif")
    await _with_text(db_session, gif)
    pending = Image(filename=f"{uuid.uuid4()}.jpg", status="pending")
    db_session.add(pending)
    await db_session.flush()
    both.content_hash = "hash-both"
    await db_session.flush()

    candidates = {c.image_id: c for c in await load_candidates(db_session, CONFIG)}

    assert candidates[str(both.id)].stratum == "ocr+description" and candidates[str(both.id)].content_hash == "hash-both"
    assert candidates[str(ocr_only.id)].stratum == "ocr only"
    assert candidates[str(description_only.id)].stratum == "description only"
    assert candidates[str(no_text.id)].stratum == "no usable text"
    for excluded in (flagged, gif, pending):
        assert str(excluded.id) not in candidates


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_cluster_members_share_a_cluster_key_and_singletons_get_their_own(db_session):
    first, second, single = await _image(db_session), await _image(db_session), await _image(db_session)
    db_session.add_all([TmpImageClusters(cluster_id=900001, image_id=first.id),
                        TmpImageClusters(cluster_id=900001, image_id=second.id)])
    await db_session.flush()

    candidates = {c.image_id: c for c in await load_candidates(db_session, CONFIG)}

    assert candidates[str(first.id)].cluster_key == candidates[str(second.id)].cluster_key == "c900001"
    assert candidates[str(single.id)].cluster_key == f"s{single.id}"


@pytest.mark.asyncio(loop_scope="session")
async def test_profile_state_counts_versions_models_and_embeddings_for_the_corpus_only(db_session):
    in_corpus, also_in, outside = await _image(db_session), await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    data = ProfileData("s", None, None, {})
    await repo.save_profile(in_corpus.id, data, ProfileMeta(2, "text-model", "p2-a", "f" * 64, False))
    await repo.save_profile(also_in.id, data, ProfileMeta(2, "vision-model", "p2-a", "f" * 64, False))
    await repo.save_profile(outside.id, data, ProfileMeta(1, "old-model", "p2-old", "f" * 64, False))
    await repo.save_situation_embedding(in_corpus.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    state = await collect_profile_state(db_session, [str(in_corpus.id), str(also_in.id), str(uuid.uuid4())])

    assert state == {
        "corpusImages": 3, "profiled": 2, "taxonomyVersions": {"2": 2}, "promptVersions": {"p2-a": 2},
        "extractorModels": {"text-model": 1, "vision-model": 1}, "embedded": 1,
        "embeddingModels": {"bge-large digest-1": 1},
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/evaluation/test_corpus.py -q` → collection ERROR.
Run (separate command): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/test_evaluation_corpus.py -q` → collection ERROR.

- [ ] **Step 4: Write `evaluation/corpus.py` and `evaluation/state.py`**

`evaluation/corpus.py`:

```python
"""The frozen evaluation corpus (P6 spec, decision 3)."""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import select

from batch.utils.image_format_filter import has_unsupported_image_extension
from batch.utils.meme_profile_bundle import PATH_VISION, build_bundle, choose_path
from evaluation.gold import STRATA, CorpusImage
from repository.meme_profile_inputs import MemeProfileInputsRepository
from Storage.models import ImageExtras, TmpImageClusters

SHARES = (("ocr+description", 0.6), ("ocr only", 0.2), ("description only", 0.1), ("no usable text", 0.1))
CHUNK = 1000


@dataclass(frozen=True)
class CorpusCandidate:
    image_id: str
    content_hash: str | None
    stratum: str
    cluster_key: str


def quotas_for(size: int) -> dict[str, int]:
    quotas = {stratum: int(size * share) for stratum, share in SHARES}
    quotas["ocr+description"] += size - sum(quotas.values())
    return quotas


def stratum_of(bundle, min_text_chars: int) -> str:
    if choose_path(bundle, min_text_chars) == PATH_VISION:
        return "no usable text"
    has_description = bool(bundle.note or bundle.descriptions)
    if has_description and bundle.ocr:
        return "ocr+description"
    return "description only" if has_description else "ocr only"


def select_corpus(candidates: Sequence[CorpusCandidate], quotas: Mapping[str, int], seed: int) -> list[CorpusImage]:
    """One image per duplicate cluster (the lowest image id), then a seeded draw per stratum; a stratum that
    cannot fill its quota hands the shortfall to ocr+description."""
    representative: dict[str, CorpusCandidate] = {}
    for candidate in candidates:
        current = representative.get(candidate.cluster_key)
        if current is None or candidate.image_id < current.image_id:
            representative[candidate.cluster_key] = candidate
    pools: dict[str, list[CorpusCandidate]] = {stratum: [] for stratum in STRATA}
    for candidate in representative.values():
        pools[candidate.stratum].append(candidate)
    for pool in pools.values():
        pool.sort(key=lambda c: c.image_id)

    rng = random.Random(seed)
    chosen: dict[str, list[CorpusCandidate]] = {}
    shortfall = 0
    for stratum in STRATA:
        quota = quotas.get(stratum, 0)
        pick = rng.sample(pools[stratum], min(quota, len(pools[stratum])))
        shortfall += quota - len(pick)
        chosen[stratum] = pick
    if shortfall:
        taken = {c.image_id for c in chosen["ocr+description"]}
        rest = [c for c in pools["ocr+description"] if c.image_id not in taken]
        chosen["ocr+description"] += rng.sample(rest, min(shortfall, len(rest)))

    return [CorpusImage(c.image_id, c.content_hash, c.stratum)
            for stratum in STRATA for c in sorted(chosen[stratum], key=lambda c: c.image_id)]


async def load_candidates(session, config) -> list[CorpusCandidate]:
    """Eligible active images with their stratum and duplicate-cluster key (read-only)."""
    inputs = MemeProfileInputsRepository(session)
    flagged = {image_id for (image_id,) in (await session.execute(
        select(ImageExtras.image_id).where(ImageExtras.flagged.is_(True)))).all()}
    clusters = {image_id: cluster_id for image_id, cluster_id in (await session.execute(
        select(TmpImageClusters.image_id, TmpImageClusters.cluster_id))).all()}
    images = [image for image in await inputs.list_active_images()
              if image[0] not in flagged and not has_unsupported_image_extension(image[1])]

    candidates: list[CorpusCandidate] = []
    for start in range(0, len(images), CHUNK):
        chunk = images[start:start + CHUNK]
        raw = await inputs.load_inputs([image_id for image_id, _, _ in chunk])
        for image_id, _, content_hash in chunk:
            item = raw[image_id]
            bundle = build_bundle(item.note, item.descriptions, item.ocr_rows,
                                  ocr_confidence_min=config.ocr_confidence_min,
                                  ocr_lang_score_min=config.ocr_lang_score_min, limits=config.limits)
            cluster = clusters.get(image_id)
            candidates.append(CorpusCandidate(
                str(image_id), content_hash, stratum_of(bundle, config.min_text_chars),
                f"c{cluster}" if cluster is not None else f"s{image_id}"))
    return candidates


def write_ids_file(path: Path, image_ids: Sequence[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{image_id}\n" for image_id in sorted(image_ids)))
```

`evaluation/state.py`:

```python
"""A snapshot of the profile state of the corpus, recorded in every baseline (P6 spec, decision 8)."""
import uuid
from typing import Sequence

from sqlalchemy import func, select

from Storage.models import MemeProfile, MemeProfileSituationEmbedding


async def collect_profile_state(session, corpus_ids: Sequence[str]) -> dict:
    ids = [uuid.UUID(image_id) for image_id in corpus_ids]

    async def counted(column, model, id_column) -> dict[str, int]:
        rows = await session.execute(
            select(column, func.count()).where(id_column.in_(ids)).group_by(column).order_by(column))
        return {str(key): count for key, count in rows.all()}

    profiled = (await session.execute(
        select(func.count()).select_from(MemeProfile).where(MemeProfile.image_id.in_(ids)))).scalar_one()
    embedded = (await session.execute(
        select(func.count()).select_from(MemeProfileSituationEmbedding)
        .where(MemeProfileSituationEmbedding.image_id.in_(ids)))).scalar_one()
    embedding_rows = await session.execute(
        select(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest, func.count())
        .where(MemeProfileSituationEmbedding.image_id.in_(ids))
        .group_by(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest))
    return {
        "corpusImages": len(ids),
        "profiled": profiled,
        "taxonomyVersions": await counted(MemeProfile.taxonomy_version, MemeProfile, MemeProfile.image_id),
        "promptVersions": await counted(MemeProfile.extractor_prompt_version, MemeProfile, MemeProfile.image_id),
        "extractorModels": await counted(MemeProfile.extractor_model, MemeProfile, MemeProfile.image_id),
        "embedded": embedded,
        "embeddingModels": {f"{name} {digest}": count for name, digest, count in embedding_rows.all()},
    }
```

Append to `.gitignore` (use the Edit tool; the file may have CRLF): the lines

```
# Local evaluation work files (pools, judging sheets, thumbnails): contain library file names
evaluation/work/
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/evaluation -q` then (separate command) `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/test_evaluation_corpus.py -q`
Expected: all PASS. Note: the `counted` helper keeps an unused `model` parameter only for readability of the call sites; if the implementer prefers, remove it from both signature and calls (no behavioural change).

- [ ] **Step 6: Commit**

```bash
git add evaluation tests/evaluation tests/integration/test_evaluation_corpus.py .gitignore
git commit -m "feat(evaluation): add the frozen corpus builder and the profile-state snapshot"
```

---

### Task 6: Judging sheet, pools file and judgment import

**Files:**
- Create: `evaluation/sheet.py`
- Create: `tests/evaluation/test_sheet.py`

**Interfaces:**
- Consumes: Task 1 (`GoldQuery`, `Judgment`, `parse_export`, `merge_judgments`), Task 2 (`Pool`).
- Produces: `evaluation.sheet.render_judging_sheet(*, language, queries, pools, thumbnails, rater, date, round_number) -> str` where `pools: Mapping[str, Sequence[str]]` maps query id to the public candidate order and `thumbnails: Mapping[str, str | None]` maps image id to a data URI; `make_thumbnail(path, size=400) -> str | None`; `pools_to_dict(round_number, per_query, pools, filenames) -> dict` and `pools_from_dict(data) -> tuple[dict[str, Pool], dict[str, str]]` for the local `pools.json` (`{"round", "perQuery", "pools": {qid: {"candidates": [...], "sources": {id: [...]}, "filenames": {id: name}}}}`).

- [ ] **Step 1: Write the failing sheet tests**

Create `tests/evaluation/test_sheet.py`:

```python
import json
import re

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.pooling import Pool
from evaluation.sheet import pools_from_dict, pools_to_dict, render_judging_sheet

EXPECTED = ExpectedProfile("hidden situation", ("annoyance",), ("mock",), ("work",), ("sarcastic",))
IMG_A, IMG_B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def _query(query_id, language="en", text="My boss moved the deadline <b>again</b>"):
    return GoldQuery(query_id, language, text, "short", "easy", EXPECTED, True)


def _sheet(**overrides):
    kwargs = dict(language="en", queries=[_query("q001"), _query("q002", language="ru", text="Привет")],
                  pools={"q001": [IMG_A, IMG_B], "q002": [IMG_B]},
                  thumbnails={IMG_A: "data:image/jpeg;base64,AAAA", IMG_B: None},
                  rater="ramiz", date="2026-10-04", round_number=1)
    kwargs.update(overrides)
    return render_judging_sheet(**kwargs)


def test_only_the_requested_languages_queries_are_shown_and_text_is_escaped():
    html = _sheet()

    assert "My boss moved the deadline &lt;b&gt;again&lt;/b&gt;" in html
    assert "Привет" not in html
    assert "<b>again</b>" not in html


def test_sheet_is_blinded_no_retriever_names_and_no_profile_values():
    html = _sheet().lower()

    for forbidden in ("lexical", "situation_embedding", "axis_match", "random", "hidden situation",
                      "annoyance", "sarcastic", "expectedprofile"):
        assert forbidden not in html


def test_each_candidate_has_three_grades_and_carries_ids_in_data_attributes():
    html = _sheet()

    assert html.count("data-q='q001'") == 2 * 3                       # two candidates x three grades
    assert f"data-i='{IMG_A}'" in html and f"data-i='{IMG_B}'" in html
    for label in ("2 great reply", "1 acceptable", "0 wrong"):
        assert label in html
    assert "data:image/jpeg;base64,AAAA" in html
    assert "(no image)" in html                                       # IMG_B has no thumbnail


def test_the_export_button_and_metadata_are_present_and_progress_is_stored_safely():
    html = _sheet()

    assert "Download judgments-en.json" in html or "judgments-en.json" in html
    meta = json.loads(re.search(r"<script type='application/json' id='meta'>(.*?)</script>", html, re.S).group(1))
    assert meta == {"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1}
    assert "localStorage" in html and "try" in html


def test_a_closing_script_tag_in_the_rater_name_cannot_break_out_of_the_metadata_block():
    html = _sheet(rater="</script><script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html


def test_pools_file_round_trip_keeps_candidates_sources_and_filenames():
    pools = {"q001": Pool("q001", (IMG_B, IMG_A), {IMG_A: ("lexical_or",), IMG_B: ("axis_match", "random")})}
    filenames = {IMG_A: "a.jpg", IMG_B: "b.jpg"}

    data = pools_to_dict(2, 12, pools, filenames)
    restored_pools, restored_names = pools_from_dict(data)

    assert data["round"] == 2 and data["perQuery"] == 12
    assert restored_pools == pools and restored_names == filenames
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/evaluation/test_sheet.py -q`
Expected: collection ERROR (`ModuleNotFoundError: No module named 'evaluation.sheet'`).

- [ ] **Step 3: Write `evaluation/sheet.py`**

```python
"""The blinded judging sheet and the local pools file (P6 spec, decision 6).

The sheet shows the incoming message and, for each candidate, only the meme image and three grades. It never
contains a retriever name, a profile value or an input text, so judging is not biased by the system's own labels.
"""
import base64
import html
import io
import json
from typing import Mapping, Sequence

from evaluation.gold import GoldQuery
from evaluation.pooling import Pool

GRADE_LABELS = ((2, "2 great reply"), (1, "1 acceptable"), (0, "0 wrong"))
RUBRIC = ("Judge only: is this a good meme to send back as a reply to the message? The grade is about fit, "
          "not about how funny or well made the meme is. 2 = great reply (you would send it), "
          "1 = acceptable (works, not ideal), 0 = wrong (does not fit, or you cannot tell what it means). "
          "Leave a meme ungraded to skip it. Ambiguous messages accept several kinds of reply.")

SCRIPT = """
const META = JSON.parse(document.getElementById('meta').textContent);
const KEY = 'eval-judging-' + META.language + '-r' + META.round;
function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
function save(state) { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* storage unavailable */ } }
const state = load();
function count() { document.getElementById('count').textContent = Object.keys(state).length; }
document.querySelectorAll('input[type=radio]').forEach(function (r) {
  const key = JSON.stringify([r.dataset.q, r.dataset.i]);
  if (state[key] === r.value) { r.checked = true; }
  r.addEventListener('change', function () { state[key] = r.value; save(state); count(); });
});
count();
function exportJudgments() {
  const judgments = Object.keys(state).map(function (key) {
    const pair = JSON.parse(key);
    return {queryId: pair[0], imageId: pair[1], grade: Number(state[key])};
  });
  const payload = {language: META.language, rater: META.rater, date: META.date, round: META.round, judgments: judgments};
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
  link.download = 'judgments-' + META.language + '.json';
  link.click();
}
"""


def render_judging_sheet(*, language: str, queries: Sequence[GoldQuery], pools: Mapping[str, Sequence[str]],
                         thumbnails: Mapping[str, str | None], rater: str, date: str, round_number: int) -> str:
    blocks = []
    shown = [query for query in queries if query.language == language and query.id in pools]
    for query in shown:
        candidates = []
        for image_id in pools[query.id]:
            thumbnail = thumbnails.get(image_id)
            picture = f"<img src='{html.escape(thumbnail, quote=True)}' alt=''>" if thumbnail else "<p>(no image)</p>"
            radios = "".join(
                f"<label><input type='radio' name='{html.escape(query.id + '__' + image_id, quote=True)}' "
                f"value='{grade}' data-q='{html.escape(query.id, quote=True)}' "
                f"data-i='{html.escape(image_id, quote=True)}'> {label}</label> "
                for grade, label in GRADE_LABELS)
            candidates.append(f"<div class='cand'>{picture}<p>{radios}</p></div>")
        blocks.append(f"<section><h3>{html.escape(query.id)}</h3>"
                      f"<blockquote>{html.escape(query.text)}</blockquote>"
                      f"<div class='row'>{''.join(candidates)}</div></section>")
    meta = json.dumps({"language": language, "rater": rater, "date": date, "round": round_number},
                      ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Judging sheet</title>"
        "<style>body{font-family:sans-serif;max-width:1300px;margin:1rem auto}section{border-bottom:1px solid #999;"
        "padding:1rem 0}blockquote{font-size:1.2rem;background:#f4f4f4;padding:.6rem;margin:.5rem 0}"
        ".row{display:flex;gap:1rem;flex-wrap:wrap}.cand{width:260px}img{max-width:260px;max-height:260px}</style>"
        f"</head><body><h1>Judging sheet ({html.escape(language)})</h1><p>{html.escape(RUBRIC)}</p>"
        f"<p>Graded so far: <b id='count'>0</b> of {sum(len(pools[q.id]) for q in shown)}. "
        "Your progress is saved in this browser; you can close the page and come back.</p>"
        + "".join(blocks)
        + f"<button onclick='exportJudgments()'>Download judgments-{html.escape(language)}.json</button>"
        f"<script type='application/json' id='meta'>{meta}</script><script>{SCRIPT}</script></body></html>"
    )


def make_thumbnail(path: str, size: int = 400) -> str | None:
    """A base64 JPEG data URI, or None when the file cannot be read (Pillow is imported lazily)."""
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.thumbnail((size, size))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def pools_to_dict(round_number: int, per_query: int, pools: Mapping[str, Pool], filenames: Mapping[str, str]) -> dict:
    return {
        "round": round_number,
        "perQuery": per_query,
        "pools": {
            query_id: {
                "candidates": list(pool.candidates),
                "sources": {image_id: list(sources) for image_id, sources in pool.sources.items()},
                "filenames": {image_id: filenames[image_id] for image_id in pool.candidates if image_id in filenames},
            }
            for query_id, pool in pools.items()
        },
    }


def pools_from_dict(data: Mapping) -> tuple[dict[str, Pool], dict[str, str]]:
    pools, filenames = {}, {}
    for query_id, entry in data["pools"].items():
        pools[query_id] = Pool(query_id, tuple(entry["candidates"]),
                               {image_id: tuple(sources) for image_id, sources in entry["sources"].items()})
        filenames.update(entry.get("filenames", {}))
    return pools, filenames
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/evaluation -q`
Expected: all PASS, including `test_pure_imports` (it does not list `sheet.py`, which lazily imports Pillow).

- [ ] **Step 5: Commit**

```bash
git add evaluation tests/evaluation
git commit -m "feat(evaluation): add the blinded judging sheet and the local pools file format"
```

---

### Task 7: CLI

**Files:**
- Create: `evaluation/__main__.py`
- Create: `tests/evaluation/test_cli.py`

**Interfaces:**
- Consumes: everything above; `Storage.db.AsyncSessionLocal` (imported lazily inside the database commands so non-database commands work without `DATABASE_URL`), `config.settings.settings`, `batch.utils.meme_profile_config.load_config`, `ai.ollama_embed.OllamaEmbedder`.
- Produces: `python -m evaluation <command>` with `build-corpus`, `build-pools`, `sheet`, `import-judgments`, `run`, `compare`, `list-baselines`; module functions `require_readonly()`, `main(argv=None) -> int`. Default gold directory `evaluation/gold`, default work directory `evaluation/work`.

Command contracts:
- `build-corpus --size 1500 --seed 7 --out evaluation/gold/corpus.json --ids-file evaluation/work/corpus.ids`: read-only; writes the corpus and the ids file; prints counts per stratum; refuses to overwrite an existing corpus unless `--force` (a frozen corpus must not change silently).
- `build-pools --round N [--per-query 12] [--pools evaluation/work/pools.json]`: read-only; for each reviewed query builds a pool from `lexical_or`, `situation_embedding`, `axis_match` (top 30 each) and a seeded random list, excluding images already judged for that query; stores `pools.json` (candidates, private sources, file names); refuses when fewer than 95% of the corpus is profiled.
- `sheet --pools ... --out-dir evaluation/work/sheets --base-path <BASE_PATH> --rater NAME [--date D]`: no database; one HTML per language present in the pools, named `judging-<language>-r<round>.html`.
- `import-judgments FILE...`: validates each export against `queries.json` and `corpus.json`, merges into `judgments.json`, prints added / overwritten / unchanged.
- `run --retriever NAME --label L [--k 10] [--seed 0]`: read-only; names `random`, `lexical_and`, `lexical_or`, `situation_embedding`, `axis_match`; refuses when fewer than 95% of the corpus is profiled; writes `evaluation/gold/baselines/<label>.json` (refuses to overwrite unless `--force`), prints the Markdown report including the pooling-bias note when `pools.json` exists.
- `compare A B [--force]`: labels or paths; prints the comparison.
- `list-baselines`: prints label, retriever, date and overall nDCG@10 per stored baseline.

- [ ] **Step 1: Write the failing CLI tests (non-database commands and guards)**

Create `tests/evaluation/test_cli.py`:

```python
import json

import pytest

from evaluation import __main__ as cli
from evaluation.gold import (
    Corpus, CorpusImage, ExpectedProfile, GoldQuery, Judgment, load_judgments, save_corpus, save_judgments,
    save_queries,
)
from evaluation.metrics import QueryMetrics
from evaluation.report import load_baseline, make_baseline, save_baseline
from evaluation.runner import QueryResult, summarize

IMG = "11111111-1111-1111-1111-111111111111"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _gold_dir(tmp_path):
    gold = tmp_path / "gold"
    (gold / "baselines").mkdir(parents=True)
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(IMG, "h", "ocr only"),)), gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", "boss moved the deadline", "short", "easy", EXPECTED, True)],
                 gold / "queries.json")
    save_judgments([], gold / "judgments.json")
    return gold


def _baseline(label, ndcg):
    results = [QueryResult("q001", "en", "short", "easy", (IMG,), QueryMetrics(ndcg, ndcg, 1.0, 1.0, True))]
    summary = summarize(results, {IMG: "ocr only"}, {"q001": {IMG: 2}})
    return make_baseline(label=label, retriever_name="fake", retriever_config={}, date="2026-10-04",
                         git_commit="abc", gold_hashes={"corpusSha": "c", "queriesSha": "q", "judgmentsSha": "j"},
                         profile_state={}, results=results, summary=summary)


def test_require_readonly_refuses_a_write_capable_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr:secret@localhost:5432/ocrdb")
    with pytest.raises(SystemExit, match="read-only"):
        cli.require_readonly()
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit, match="read-only"):
        cli.require_readonly()


def test_require_readonly_accepts_the_read_only_role(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr_readonly:x@localhost:5432/ocrdb")
    cli.require_readonly()


def test_import_judgments_merges_an_export_and_reports_counts(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    export = tmp_path / "judgments-en.json"
    export.write_text(json.dumps({"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1,
                                  "judgments": [{"queryId": "q001", "imageId": IMG, "grade": 2}]}), encoding="utf-8")

    code = cli.main(["--gold-dir", str(gold), "import-judgments", str(export)])

    assert code == 0
    assert load_judgments(gold / "judgments.json") == [Judgment("q001", IMG, 2, "ramiz", "2026-10-04", 1)]
    assert "added 1" in capsys.readouterr().out


def test_import_judgments_refuses_an_export_with_unknown_ids_and_changes_nothing(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    export = tmp_path / "bad.json"
    export.write_text(json.dumps({"language": "en", "rater": "r", "date": "d", "round": 1,
                                  "judgments": [{"queryId": "q999", "imageId": IMG, "grade": 1}]}), encoding="utf-8")

    code = cli.main(["--gold-dir", str(gold), "import-judgments", str(export)])

    assert code == 1 and "unknown query" in capsys.readouterr().err
    assert load_judgments(gold / "judgments.json") == []


def test_compare_and_list_baselines_work_on_stored_files(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    save_baseline(gold / "baselines" / "before.json", _baseline("before", 1.0))
    save_baseline(gold / "baselines" / "after.json", _baseline("after", 0.5))

    assert cli.main(["--gold-dir", str(gold), "list-baselines"]) == 0
    listing = capsys.readouterr().out
    assert "before" in listing and "after" in listing and "fake" in listing

    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after"]) == 0
    assert "before -> after" in capsys.readouterr().out


def test_compare_refuses_mismatched_gold_hashes_without_force(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    other = _baseline("after", 0.5)
    other["gold"]["judgmentsSha"] = "different"
    save_baseline(gold / "baselines" / "before.json", _baseline("before", 1.0))
    save_baseline(gold / "baselines" / "after.json", other)

    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after"]) == 1
    assert "judgmentsSha" in capsys.readouterr().err
    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after", "--force"]) == 0


def test_a_missing_baseline_is_a_clear_error(tmp_path, capsys):
    gold = _gold_dir(tmp_path)

    assert cli.main(["--gold-dir", str(gold), "compare", "nope", "nada"]) == 1
    assert "nope" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/evaluation/test_cli.py -q`
Expected: collection ERROR (`ModuleNotFoundError: No module named 'evaluation.__main__'`).

- [ ] **Step 3: Write `evaluation/__main__.py`**

```python
"""Evaluation CLI (P6 spec, decision 1).

    python -m evaluation <command> ...

Database commands read only. They require APP_ENV and the READ-ONLY DATABASE_URL (role ocr_readonly) to be set in
the shell BEFORE starting, and deliberately never call config.settings.load_env(): that function loads
environments/.env.<env> with override=True and would replace the read-only URL with the write-capable one.
"""
import argparse
import asyncio
import os
import sys
from datetime import date as date_class
from pathlib import Path

from evaluation.gold import (
    GoldError, Judgment, file_sha256, judgments_by_query, load_corpus, load_judgments, load_queries,
    merge_judgments, parse_export, save_corpus, save_judgments, Corpus,
)
from evaluation.report import (
    CompareError, compare_baselines, format_report, git_commit, load_baseline, make_baseline, pooling_bias,
    save_baseline,
)

DEFAULT_GOLD = Path("evaluation/gold")
DEFAULT_WORK = Path("evaluation/work")
MIN_PROFILED_SHARE = 0.95
RETRIEVER_NAMES = ("random", "lexical_and", "lexical_or", "situation_embedding", "axis_match")
POOL_SOURCES = ("lexical_or", "situation_embedding", "axis_match")
POOL_DEPTH = 30


def require_readonly() -> None:
    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        sys.exit("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly) and APP_ENV must be set.")


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _baseline_path(gold: Path, label_or_path: str) -> Path:
    candidate = Path(label_or_path)
    if candidate.suffix == ".json" and candidate.exists():
        return candidate
    return gold / "baselines" / f"{label_or_path}.json"


# ------------------------------------------------------------------ commands without a database

def cmd_import_judgments(args, gold: Path) -> int:
    queries = load_queries(gold / "queries.json")
    corpus = load_corpus(gold / "corpus.json")
    existing = load_judgments(gold / "judgments.json")
    incoming: list[Judgment] = []
    import json
    for name in args.files:
        with open(name, encoding="utf-8") as handle:
            data = json.load(handle)
        incoming.extend(parse_export(data, valid_query_ids={q.id for q in queries},
                                     valid_image_ids=set(corpus.image_ids())))
    result = merge_judgments(existing, incoming)
    save_judgments(result.merged, gold / "judgments.json")
    print(f"added {result.added}, overwritten {result.overwritten}, unchanged {result.unchanged}; "
          f"{len(result.merged)} judgments in total")
    return 0


def cmd_compare(args, gold: Path) -> int:
    paths = [_baseline_path(gold, args.a), _baseline_path(gold, args.b)]
    for name, path in zip((args.a, args.b), paths):
        if not path.exists():
            return _error(f"no baseline named {name!r} ({path})")
    queries = {q.id: q.text for q in load_queries(gold / "queries.json")} if (gold / "queries.json").exists() else {}
    print(compare_baselines(load_baseline(paths[0]), load_baseline(paths[1]), force=args.force, query_texts=queries))
    return 0


def cmd_list_baselines(args, gold: Path) -> int:
    for path in sorted((gold / "baselines").glob("*.json")):
        baseline = load_baseline(path)
        ndcg = baseline["summary"]["overall"]["ndcg"]
        shown = "-" if ndcg is None else f"{ndcg:.3f}"
        print(f"{baseline['label']}\t{baseline['retriever']['name']}\t{baseline['date']}\tnDCG@10 {shown}")
    return 0


def cmd_sheet(args, gold: Path) -> int:
    import json
    from evaluation.sheet import make_thumbnail, pools_from_dict, render_judging_sheet
    with open(args.pools, encoding="utf-8") as handle:
        data = json.load(handle)
    pools, filenames = pools_from_dict(data)
    queries = load_queries(gold / "queries.json")
    thumbnails = {image_id: make_thumbnail(os.path.join(args.base_path, name)) for image_id, name in filenames.items()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    languages = sorted({q.language for q in queries if q.id in pools})
    for language in languages:
        html = render_judging_sheet(
            language=language, queries=queries, pools={qid: pool.candidates for qid, pool in pools.items()},
            thumbnails=thumbnails, rater=args.rater, date=args.date or date_class.today().isoformat(),
            round_number=data["round"])
        target = out_dir / f"judging-{language}-r{data['round']}.html"
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html)
        print(f"wrote {target}")
    return 0


# ------------------------------------------------------------------ database commands

def _settings_config():
    from batch.utils.meme_profile_config import load_config
    from config.settings import settings
    config = load_config(settings.get)
    if config is None:
        sys.exit("meme_profiles is not configured for this environment (set APP_ENV to general or metal)")
    return config


def _embedder_and_model():
    from ai.ollama_embed import OllamaEmbedder
    from config.settings import settings
    model_name = settings.TAXONOMY.EMBEDDING_MODEL
    return OllamaEmbedder(model_name), model_name


def _make_retriever(name: str, session, seed: int):
    from evaluation.retrievers.axis_match import AxisMatchRetriever
    from evaluation.retrievers.lexical import LexicalRetriever
    from evaluation.retrievers.random_retriever import RandomRetriever
    from evaluation.retrievers.situation_embedding import SituationEmbeddingRetriever
    if name == "random":
        return RandomRetriever(seed)
    if name == "lexical_and":
        return LexicalRetriever(session, "and")
    if name == "lexical_or":
        return LexicalRetriever(session, "or")
    if name == "axis_match":
        return AxisMatchRetriever(session)
    if name == "situation_embedding":
        embedder, model_name = _embedder_and_model()
        return SituationEmbeddingRetriever(session, embedder, model_name)
    raise ValueError(f"unknown retriever {name!r}")


async def _require_profiled(session, corpus: Corpus) -> dict:
    from evaluation.state import collect_profile_state
    state = await collect_profile_state(session, list(corpus.image_ids()))
    if state["profiled"] < MIN_PROFILED_SHARE * len(corpus.images):
        sys.exit(f"Only {state['profiled']} of {len(corpus.images)} corpus images have a profile (need "
                 f"{MIN_PROFILED_SHARE:.0%}). Profile them with: python -m batch.build_meme_profiles --env general "
                 "--ids-file <corpus.ids>, then python -m batch.build_meme_profile_embeddings --ids-file <corpus.ids>")
    return state


async def cmd_build_corpus(args, gold: Path) -> int:
    require_readonly()
    from evaluation.corpus import load_candidates, quotas_for, select_corpus, write_ids_file
    from Storage.db import AsyncSessionLocal
    target = Path(args.out)
    if target.exists() and not args.force:
        return _error(f"{target} already exists; the corpus is frozen (use --force to replace it deliberately)")
    config = _settings_config()
    async with AsyncSessionLocal() as session:
        candidates = await load_candidates(session, config)
    images = select_corpus(candidates, quotas_for(args.size), args.seed)
    corpus = Corpus(environment=args.environment, seed=args.seed, created=date_class.today().isoformat(),
                    images=tuple(images))
    save_corpus(corpus, target)
    Path(args.ids_file).parent.mkdir(parents=True, exist_ok=True)
    write_ids_file(Path(args.ids_file), list(corpus.image_ids()))
    counts: dict[str, int] = {}
    for image in images:
        counts[image.stratum] = counts.get(image.stratum, 0) + 1
    print(f"{len(candidates)} eligible images; corpus of {len(images)}: {counts}; wrote {target} and {args.ids_file}")
    return 0


async def cmd_build_pools(args, gold: Path) -> int:
    require_readonly()
    import json
    from sqlalchemy import select
    from evaluation.pooling import build_pool
    from evaluation.retrievers.random_retriever import RandomRetriever
    from evaluation.sheet import pools_to_dict
    from Storage.db import AsyncSessionLocal
    from Storage.models import Image
    corpus = load_corpus(gold / "corpus.json")
    queries = [q for q in load_queries(gold / "queries.json") if q.reviewed]
    judged = judgments_by_query(load_judgments(gold / "judgments.json"))
    corpus_ids = list(corpus.image_ids())
    pools = {}
    async with AsyncSessionLocal() as session:
        await _require_profiled(session, corpus)
        retrievers = {name: _make_retriever(name, session, args.seed) for name in POOL_SOURCES}
        randomiser = RandomRetriever(args.seed)
        for retriever in retrievers.values():
            if hasattr(retriever, "prepare"):
                await retriever.prepare(queries)
        for query in queries:
            ranked = {name: [image_id for image_id, _ in await retriever.retrieve(query, corpus_ids, POOL_DEPTH)]
                      for name, retriever in retrievers.items()}
            random_ids = [image_id for image_id, _ in await randomiser.retrieve(query, corpus_ids, 50)]
            ranked_target = args.per_query - 3
            pools[query.id] = build_pool(query.id, ranked, random_ids, exclude=set(judged.get(query.id, {})),
                                         ranked_target=ranked_target, random_target=3, seed=args.seed)
        import uuid
        wanted = {uuid.UUID(image_id) for pool in pools.values() for image_id in pool.candidates}
        rows = await session.execute(select(Image.id, Image.filename).where(Image.id.in_(wanted)))
        filenames = {str(image_id): filename for image_id, filename in rows.all()}
    Path(args.pools).parent.mkdir(parents=True, exist_ok=True)
    with open(args.pools, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(pools_to_dict(args.round, args.per_query, pools, filenames), indent=2,
                                ensure_ascii=False) + "\n")
    print(f"wrote {args.pools}: {sum(len(p.candidates) for p in pools.values())} candidates for {len(pools)} queries")
    return 0


async def cmd_run(args, gold: Path) -> int:
    require_readonly()
    import json
    from evaluation.runner import run_retriever, summarize
    from Storage.db import AsyncSessionLocal
    corpus = load_corpus(gold / "corpus.json")
    queries = load_queries(gold / "queries.json")
    judgments = load_judgments(gold / "judgments.json")
    target = gold / "baselines" / f"{args.label}.json"
    if target.exists() and not args.force:
        return _error(f"{target} already exists (use --force to replace it)")
    async with AsyncSessionLocal() as session:
        state = await _require_profiled(session, corpus)
        retriever = _make_retriever(args.retriever, session, args.seed)
        results = await run_retriever(retriever, queries, list(corpus.image_ids()), judgments_by_query(judgments),
                                      k=args.k)
    summary = summarize(results, corpus.strata(), judgments_by_query(judgments), k=args.k)
    baseline = make_baseline(
        label=args.label, retriever_name=retriever.name, retriever_config=retriever.config,
        date=date_class.today().isoformat(), git_commit=git_commit(),
        gold_hashes={"corpusSha": file_sha256(gold / "corpus.json"), "queriesSha": file_sha256(gold / "queries.json"),
                     "judgmentsSha": file_sha256(gold / "judgments.json")},
        profile_state=state, results=results, summary=summary)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_baseline(target, baseline)
    bias = None
    pools_path = Path(args.pools)
    if pools_path.exists():
        from evaluation.sheet import pools_from_dict
        with open(pools_path, encoding="utf-8") as handle:
            pools, _ = pools_from_dict(json.load(handle))
        bias = pooling_bias(results, {qid: pool.sources for qid, pool in pools.items()})
    print(format_report(baseline, bias))
    print(f"wrote {target}")
    return 0


# ------------------------------------------------------------------ entry point

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    parser.add_argument("--gold-dir", default=str(DEFAULT_GOLD))
    commands = parser.add_subparsers(dest="command", required=True)

    corpus = commands.add_parser("build-corpus")
    corpus.add_argument("--size", type=int, default=1500)
    corpus.add_argument("--seed", type=int, default=7)
    corpus.add_argument("--environment", default="general")
    corpus.add_argument("--out", default=str(DEFAULT_GOLD / "corpus.json"))
    corpus.add_argument("--ids-file", default=str(DEFAULT_WORK / "corpus.ids"))
    corpus.add_argument("--force", action="store_true")

    pools = commands.add_parser("build-pools")
    pools.add_argument("--round", type=int, required=True)
    pools.add_argument("--per-query", type=int, default=12)
    pools.add_argument("--seed", type=int, default=7)
    pools.add_argument("--pools", default=str(DEFAULT_WORK / "pools.json"))

    sheet = commands.add_parser("sheet")
    sheet.add_argument("--pools", default=str(DEFAULT_WORK / "pools.json"))
    sheet.add_argument("--out-dir", default=str(DEFAULT_WORK / "sheets"))
    sheet.add_argument("--base-path", required=True)
    sheet.add_argument("--rater", required=True)
    sheet.add_argument("--date", default=None)

    imports = commands.add_parser("import-judgments")
    imports.add_argument("files", nargs="+")

    run = commands.add_parser("run")
    run.add_argument("--retriever", required=True, choices=RETRIEVER_NAMES)
    run.add_argument("--label", required=True)
    run.add_argument("--k", type=int, default=10)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--pools", default=str(DEFAULT_WORK / "pools.json"))
    run.add_argument("--force", action="store_true")

    compare = commands.add_parser("compare")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--force", action="store_true")

    commands.add_parser("list-baselines")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    gold = Path(args.gold_dir)
    handlers = {"import-judgments": cmd_import_judgments, "compare": cmd_compare, "list-baselines": cmd_list_baselines,
                "sheet": cmd_sheet}
    async_handlers = {"build-corpus": cmd_build_corpus, "build-pools": cmd_build_pools, "run": cmd_run}
    try:
        if args.command in handlers:
            return handlers[args.command](args, gold)
        return asyncio.run(async_handlers[args.command](args, gold))
    except (GoldError, CompareError) as error:
        return _error(str(error))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/evaluation -q`
Expected: all PASS. Then smoke-test the wiring without data: `python -m evaluation --help` (exit 0) and `python -m evaluation run --help` (lists the five retriever names).

- [ ] **Step 5: Run the whole set of affected roots**

Run each as its own command: `python -m pytest tests/evaluation -q`; `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/ -q`; `python -m pytest batch/tests -q`; `python -m pytest tests/ai -q`; `python -m pytest tests/taxonomy -q`.
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add evaluation/__main__.py tests/evaluation/test_cli.py
git commit -m "feat(evaluation): add the evaluation CLI (corpus, pools, sheets, import, run, compare)"
```

---

### Task 8: Draw the corpus and profile it (controller, live)

This and every later task are **controller + user** steps: they use the read-only role, write profiles into the live `general` database (with consent) and need the user's judgment. Do not delegate any of them.

**Files:**
- Create: `evaluation/gold/corpus.json`, `evaluation/gold/judgments.json` (empty, `{"version": 1, "judgments": []}`), `evaluation/gold/baselines/.gitkeep`
- Local only: `evaluation/work/corpus.ids`

- [ ] **Step 1: Draw the corpus with the read-only role**

The controller reads `DATABASE_URL_READONLY` and `BASE_PATH` for `general` from `environments/.env.general` (UTF-8: `Get-Content -Encoding UTF8`; the base path is Cyrillic), sets `DATABASE_URL` to the read-only value for this process, sets `APP_ENV=general` and `PYTHONIOENCODING=utf-8`, and runs from the repo root (or the worktree root):

`python -m evaluation build-corpus --size 1500 --seed 7`

Expected: `<N> eligible images; corpus of 1500: {...}` with the four strata near 900 / 300 / 150 / 150 (the small strata may be short; the shortfall goes to `ocr+description`), and `evaluation/gold/corpus.json` plus `evaluation/work/corpus.ids` written. Create `evaluation/gold/judgments.json` with `python -c "from pathlib import Path; from evaluation.gold import save_judgments; save_judgments([], Path('evaluation/gold/judgments.json'))"` and `evaluation/gold/baselines/.gitkeep`.

- [ ] **Step 2: Ask the user before writing 1,500 profiles to the live database**

Ask via AskUserQuestion: "Profiling the 1,500 corpus images writes about 1,500 profile rows and vectors into the live `general` database (about 20 minutes, `--ids-file` limits it to the corpus; `build_meme_profiles --reset --ids-file` can undo it). May I run it now?" Options: "Yes, profile the corpus (Recommended)", "Not now". Only after a yes continue.

- [ ] **Step 3: Profile the corpus (controller, detached)**

With the `general` main `DATABASE_URL` in the shell (parsed from `environments/.env.general`, never printed; the real env files may be copied into the worktree's `environments/` for `load_env` and must be deleted afterwards), run detached (`Start-Process`, output to files), then wait with a background `Wait-Process`:

`python -m batch.build_meme_profiles --env general --ids-file evaluation/work/corpus.ids`

Check the output: `saved` close to the number of corpus images not already profiled, `error.invalid_output` small (empty answers), no `error.unavailable`. Then:

`python -m batch.build_meme_profile_embeddings --env general --ids-file evaluation/work/corpus.ids`

Expected: `embedded` equals the number of profiles with a situation text; a second run reports 0 to embed.

- [ ] **Step 4: Verify coverage and commit the corpus**

With the read-only URL, run the corpus-state check: `python -c "import asyncio, os; from evaluation.gold import load_corpus; from evaluation.state import collect_profile_state; from Storage.db import AsyncSessionLocal; c = load_corpus('evaluation/gold/corpus.json'); ..."` is not needed: `python -m evaluation run --retriever random --label smoke` performs the 95% check and refuses with a clear message otherwise. Expected after profiling: it runs (with no judgments every query has no positive, so the metrics print as `-`; delete `evaluation/gold/baselines/smoke.json` afterwards). If it refuses, read the printed counts, re-run the profile job with `--retry-failed` once, and report the remainder to the user.

```bash
git add evaluation/gold/corpus.json evaluation/gold/judgments.json evaluation/gold/baselines/.gitkeep
git commit -m "feat(evaluation): add the frozen 1,500-image corpus and an empty judgments file"
```

---

### Task 9: Draft the 60 queries and get them reviewed (controller + user)

**Files:**
- Create: `evaluation/gold/queries.json`, `evaluation/gold/README.md`

- [ ] **Step 1: Draft the queries**

The controller writes `evaluation/gold/queries.json` (via a small script using `save_queries`, so the format is exact) with 60 queries, all `reviewed: false`, obeying the drafting rules in Global Constraints: 20 per language; per language 5 short (at most 8 words), 10 medium (9-20 words), 5 long (at least 21 words) and 7 easy / 8 hard / 5 ambiguous; written as an incoming message someone would actually receive; every topic key covered; at least 8 distinct stance, 10 emotion and 6 tone keys in the expected profiles; at least 6 `pairId` groups written in all three languages as near-translations; each query has an `expectedProfile` (English `situation`, taxonomy keys for emotion, stance, topic, tone, with the query-side semantics of P1 decision 1: what is happening in the message, and the emotion, stance and tone of the reply wanted). Ids `q001` to `q060`, grouped by language (`q001`-`q020` en, `q021`-`q040` ru, `q041`-`q060` es).

- [ ] **Step 2: Lint**

Run: `python -c "from pathlib import Path; from evaluation.gold import load_queries, check_queries, lint_query_set; from taxonomy import load; t = load(); q = load_queries(Path('evaluation/gold/queries.json')); print(check_queries(q, t)); print(lint_query_set(q, t))"`
Expected: two empty lists. Fix the queries until both are empty.

- [ ] **Step 3: User review gate**

Present the queries to the user as a table (id, language, length/difficulty, message, expected situation / emotion / stance / topic / tone) in chat, in batches by language, and ask them to edit, replace or drop any (via chat or by editing `queries.json`). Apply their changes (re-run the lint), set `"reviewed": true` on every approved query, and stop until they have approved all 60 (or a stated subset: the pools and metrics use only reviewed queries; the per-language counts then differ from the plan and the lint's count check is relaxed by passing `per_language=<n>` and the matching quotas, recorded in the README).

- [ ] **Step 4: Write `evaluation/gold/README.md` and commit**

The README states: what each file is; the rubric (verbatim from `evaluation/sheet.RUBRIC`); that the gold data is only valid for the `general` database it was drawn from (image ids are database-local, content hashes detect drift); the rules for extending (new queries are appended with new ids; new judging rounds use `--round N` and never re-ask judged pairs; baselines record the gold hashes and are compared only against the same hashes); what is committed and what stays local (`evaluation/work/`); and the read-only-database and no-`load_env` rules for the CLI.

```bash
git add evaluation/gold/queries.json evaluation/gold/README.md
git commit -m "feat(evaluation): add the 60 reviewed gold queries with expected profiles"
```

---

### Task 10: Pools, judging and import (controller + user)

**Files:**
- Local only: `evaluation/work/pools.json`, `evaluation/work/sheets/*.html`
- Modify: `evaluation/gold/judgments.json`

- [ ] **Step 1: Build the round-1 pools**

With the read-only `DATABASE_URL`, `APP_ENV=general`, Ollama running and `bge-large` present (the situation-embedding retriever embeds the 60 queries in one call):

`python -m evaluation build-pools --round 1 --per-query 12`

Expected: `wrote evaluation/work/pools.json: about 720 candidates for 60 queries`. If any query's pool has fewer than 12 candidates because the sources overlapped heavily, that is acceptable; report the smallest pool.

- [ ] **Step 2: Render the sheets and hand them to the user**

`python -m evaluation sheet --base-path "<BASE_PATH>" --rater <user's name> --date <today>`

Open one sheet per language for the user (`Start-Process`), explain the rubric in one sentence, and ask them to judge, then click "Download judgments-<language>.json" for each language and tell the controller where the files landed. The user can do one language per sitting; the sheet keeps progress in the browser. Before opening, verify blinding: grep the generated HTML for `lexical`, `random`, `axis_match`, `situation_embedding` and the expected-profile values; none may appear. **Stop and wait for the user.**

- [ ] **Step 3: Import the judgments**

`python -m evaluation import-judgments <path to judgments-en.json> <ru> <es>`

Expected: `added <n>, overwritten 0, unchanged 0; <total> judgments in total` with the total near 700 (fewer if the user skipped some). A refusal names the unknown query or image; fix the cause, never edit the user's grades.

- [ ] **Step 4: Commit the judgments**

```bash
git add evaluation/gold/judgments.json
git commit -m "feat(evaluation): add the first round of human judgments"
```

---

### Task 11: Baselines, experiments, addendum, docs and review (controller)

**Files:**
- Create: `evaluation/gold/baselines/*.json`, `docs/superpowers/reviews/2026-10-04-p6-evaluation-and-gold-set-review.md`
- Modify: the P6 spec (status and addendum), `docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md`, `CLAUDE.md`, `board/todo/158-run-meme-profile-backlog-on-general-and-metal.md`

- [ ] **Step 1: Run the five baselines**

With the read-only `DATABASE_URL` and `APP_ENV=general`, run each (each stores `evaluation/gold/baselines/<label>.json` and prints the report; `situation_embedding` needs Ollama):

```
python -m evaluation run --retriever random --label random
python -m evaluation run --retriever lexical_and --label lexical_and
python -m evaluation run --retriever lexical_or --label lexical_or
python -m evaluation run --retriever situation_embedding --label situation_embedding
python -m evaluation run --retriever axis_match --label axis_match
```

Then `python -m evaluation list-baselines` and `python -m evaluation compare random axis_match`. Expected: `lexical_and` near zero (most messages match nothing), `random` near the corpus base rate, and the three informative retrievers above `random`. Record any surprise (for example `axis_match` near `random`, which would point at profile quality).

- [ ] **Step 2: Answer the two decision gates with evidence**

Write down, from the baselines: (a) **prompt gate for board task 158:** do the profiles carry signal (does `axis_match` beat `random` by a clear margin, does `situation_embedding` beat `lexical_or`), and do the skewed values (stance `mock`) visibly hurt (read the per-stratum and per-difficulty tables)? Give a recommendation: run the backlog now, tune the prompt first, or both with the corpus experiment loop. (b) **embedding gap (OQ-4):** the per-language nDCG ratio of `situation_embedding` (English `bge-large`, raw RU/ES queries) against English. If the user wants the multilingual comparison, ask before pulling any model (for example `bge-m3`), then run the experiment loop of spec decision 10 (temporarily set `taxonomy.embedding_model`, `build_meme_profile_embeddings --ids-file evaluation/work/corpus.ids --reset`, re-run the retriever under a new label, compare, restore the setting).

- [ ] **Step 3: Propose acceptance thresholds**

From the baselines propose, for the user's approval, P4's acceptance thresholds: the P4 pipeline must beat the best baseline retriever on nDCG@10 and hit@10 overall; per-language nDCG@10 must stay above a stated fraction of English (derived from the measured gap, for example 0.8 if the gap is small); and no query class (difficulty, length) may drop below the best baseline's score on it. State them as a proposal, not a decision.

- [ ] **Step 4: Spec addendum, tracker, board task, CLAUDE.md**

Append to the P6 spec `## Addendum: result (<date>)` with: the corpus facts (final strata counts, how many were profiled), the number of reviewed queries and judgments, the baseline table (overall, per language), the two gate answers, the proposed thresholds, and any deviation from the spec. Set the spec `Status: done` and add the `Plan:` line. In `tracker.md` set P6 to `done` with the spec and plan paths and update the "Next step" paragraph. In `board/todo/158-...md` replace the "When to run it" decision gate paragraph with the evidence-based recommendation from step 2. In `CLAUDE.md` add a short entry in the batch/maintenance area describing `python -m evaluation` (what it does, the read-only requirement, the `--ids-file` option of the two P2 jobs, and the gold data location), following the style of the neighbouring entries.

- [ ] **Step 5: Final verification**

Run each as its own command: `python -m pytest tests/evaluation -q`; `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -m pytest tests/integration/ -q` (the whole root); `python -m pytest batch/tests -q`; `python -m pytest tests/ai -q`; `python -m pytest tests/taxonomy -q`; from `Backend/`: `python -m pytest -q`. Expected: all PASS. Remove any copies of `environments/.env.*` from the worktree.

- [ ] **Step 6: Review report and commit**

Create `docs/superpowers/reviews/2026-10-04-p6-evaluation-and-gold-set-review.md` against the spec's eight acceptance criteria (criterion, verdict met / met with deviation / not met, evidence), plus Deviations, What was fixed, What was not fixed (explained), What was intentionally ignored.

```bash
git add evaluation/gold docs CLAUDE.md board
git commit -m "docs: record P6 baselines, gate answers, proposed thresholds, review and tracker status"
```

---

## Self-review (plan against spec)

| Spec item | Task |
|---|---|
| Decision 1: package layout, CLI commands, read-only guard, no `load_env` | 1-3, 5-7 |
| Decision 2: gold file formats, deterministic IO, hashes | 1 |
| Decision 3: frozen corpus (quotas, exclusions, one per duplicate cluster, 95% profiled rule) | 5, 7 (`_require_profiled`), 8 |
| Decision 4: the 60 queries, drafting rules and lint, expected profiles | 1 (`check_queries`, `lint_query_set`), 9 |
| Decision 5: pools (round-robin, random fill, exclusion of judged, private sources, seeded shuffle) | 2 (`pooling`), 6 (pools file), 7 (`build-pools`), 10 |
| Decision 6: blinded sheet, rubric, resumable progress, export and importer | 6, 7 (`sheet`, `import-judgments`), 10 |
| Decision 7: `Retriever` interface and the five baseline retrievers | 2 (`base`), 3 |
| Decision 8: metrics, runner, baselines with gold hashes and profile state, `compare`, pooling-bias note, thresholds proposed not guessed | 1, 2, 7 (`run`, `compare`), 11 |
| Decision 9: `--ids-file` on both P2 jobs (incl. `--reset` scoping) | 4 |
| Decision 10: experiment workflow (prompt change, embedding model, profile quality without a parser) | 11 steps 1-2 |
| Testing section | tests in every task; whole integration root in 4 and 7 |
| Acceptance 1 (package, import guard) | 1-2 (`test_pure_imports`) |
| Acceptance 2 (corpus, queries, judgments) | 8, 9, 10 |
| Acceptance 3 (five baselines with reports) | 11 |
| Acceptance 4 (`compare`) | 2, 7, 11 |
| Acceptance 5 (`--ids-file`) | 4 |
| Acceptance 6 (addendum: numbers, two gates, thresholds) | 11 |
| Acceptance 7 (all test roots) | 7 step 5, 11 step 5 |
| Acceptance 8 (P3/P4 can start) | 11 step 4 |

Deviations from the spec text, stated here so reviewers do not rediscover them: the `Retriever` protocol takes `corpus_ids` as strings (retrievers convert to UUIDs) so the pure modules stay free of UUID handling; `lexical_or` tokenises with `\w+` and delegates normalisation to `scored_image_matches` per token (no private imports); `build-pools` runs the three ranked sources at depth 30 and takes 9 round-robin plus 3 random per query (the spec's "about 12").

Type and name consistency checks: `GoldQuery`, `Judgment`, `Corpus`, `QueryMetrics`, `QueryResult`, `Pool`, `CorpusCandidate` are each defined once and imported where used; `judgments_by_query` returns `dict[str, dict[str, int]]` and is what `run_retriever`, `summarize` and `build-pools` consume; `RETRIEVER_NAMES` and `POOL_SOURCES` in the CLI match the retriever `name` attributes (`lexical_and`, `lexical_or`, `situation_embedding`, `axis_match`, `random`); the baseline keys written by `make_baseline` (`gold`, `summary`, `queries`) are the keys read by `format_report`, `compare_baselines` and `cmd_list_baselines`; the profile-state keys produced by `collect_profile_state` are only displayed, never parsed, except `profiled` (used for the 95% check).
