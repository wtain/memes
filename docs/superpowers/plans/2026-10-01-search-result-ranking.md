# Search Result Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a text query `q` is present, order `GET /api/images` results by relevance (per-token best hit, source weight x tier weight, summed), with recency as the tie-breaker; browse and facet-only requests stay in recency order.

**Architecture:** `repository/ocr_lemmas.py` gains `scored_image_matches` (per-source queries, best weight per token, summed); `matching_image_ids` becomes a wrapper over it. `ImageRepository.search` takes a ranked path (score map -> one row fetch -> Python sort and keyset page) when `q` yields scores, and the existing SQL path otherwise. The cursor gains an optional `score`. Scoring in Python is recorded in an ADR.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async, FastAPI, Dynaconf settings, pytest (`pytest-asyncio`), PostgreSQL test DB `ocrdb_test`.

**Spec:** `docs/superpowers/specs/2026-10-01-search-result-ranking-design.md` (read it first). ADR: `docs/adr/adr-2026-10-01-search-ranking-in-python.md`.

## Global Constraints

- Python via `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`; set `PYTHONIOENCODING=utf-8`. In a worktree use that main-checkout venv path.
- Never combine test roots in one `pytest` invocation: `tests/integration/` (needs `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`), `Backend/tests` (`cd Backend` then `pytest`), `batch/tests/`, `tests/rules/` are four separate commands. `repository/ocr_lemmas.py` is shared search code, so the ENTIRE `tests/integration/` root must pass before the matching/search tasks are committed.
- Only the test database above. Never touch metal/general/IT databases, never run batch jobs against them, never bind ports 8081-8083 / 5173-5175.
- Repositories never call `session.commit()`.
- Matching semantics are unchanged: a token matches at exactly one tier (exact first; stem/fuzzy/phonetic fallbacks only when exact finds nothing in ANY source, and they are unioned); every token must match (AND); rejected Ollama descriptions stay excluded via `description_not_rejected`.
- Default weights: sources `note 1.0, ocr 0.9, tag 0.8, description 0.6`; tiers `exact 1.0, stem 0.8, fuzzy 0.5, phonetic 0.4`; `warn_match_count: 10000`. Tier-to-source map: exact -> ocr, tag, note, description; stem -> ocr, description; fuzzy -> ocr, tag, note, description; phonetic -> ocr only.
- Scores are rounded to 6 decimals everywhere (comparison, sorting, cursor).
- API contract change => update `backend_api.md` in the same change. No client change is needed (the cursor stays an opaque base64 string).
- Work in a git worktree (parallel sessions exist). Tasks run sequentially: Tasks 3 and 4 both edit shared search code.
- Commit messages end with the two attribution lines from the session's system reminder (`Co-Authored-By: ...` and `Claude-Session: ...`), separated from the body by a blank line.

## File Structure

| File | Responsibility |
|---|---|
| `environments/settings.yaml` (modify) | `search.ranking` weights and `warn_match_count` |
| `repository/search_ranking.py` (create) | `RankingWeights`, `get_weights()`, pure `score_images()` |
| `repository/ocr_lemmas.py` (modify) | per-source tier hit queries, `scored_image_matches`, `matching_image_ids` wrapper |
| `Backend/app/repositories/image_repository.py` (modify) | `RankedRow`, ranked search path, `cursor_score` |
| `Backend/app/services/image_service.py` (modify) | search cursor encode/decode with score, paginate cursor fix |
| `tests/integration/test_search_scoring.py` (create) | unit tests for `search_ranking` |
| `tests/integration/test_search_ranking_matches.py` (create) | token-level scoring through `scored_image_matches` |
| `tests/integration/test_search_ranking_repository.py` (create) | ordering and paging through `ImageRepository.search` |
| `Backend/tests/test_image_service.py` (modify) | service cursor tests |
| `backend_api.md`, `docs/data-flow.md`, `CLAUDE.md` (modify) | docs |

---

### Task 1: Ranking weights, config and the pure scorer

**Files:**
- Modify: `environments/settings.yaml` (the `search:` block, ~line 46)
- Create: `repository/search_ranking.py`
- Test: `tests/integration/test_search_scoring.py`

**Interfaces:**
- Produces:
  - `repository.search_ranking.SOURCES = ("note", "ocr", "tag", "description")`, `TIERS = ("exact", "stem", "fuzzy", "phonetic")`.
  - `RankingWeights(source: dict[str, float], tier: dict[str, float], warn_match_count: int)` frozen dataclass with `.hit(source: str, tier: str) -> float` returning `round(source_weight * tier_weight, 6)`.
  - `get_weights() -> RankingWeights` (cached; reads `settings.SEARCH.RANKING`; raises `ValueError` naming any missing key).
  - `score_images(token_hits: list[dict[uuid.UUID, float]]) -> dict[uuid.UUID, float]`: `token_hits[i]` maps image id to the best hit weight for token i. Returns only images present in EVERY dict, with score = sum of that image's weights, rounded to 6 decimals. An empty list returns `{}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_search_scoring.py`:

```python
"""
Unit tests for repository/search_ranking.py (pure scoring arithmetic and weight loading). No DB rows are
used; the file lives in tests/integration/ only because it imports repository code and that root sets
DATABASE_URL.
"""
import uuid

import pytest

from repository.search_ranking import RankingWeights, SOURCES, TIERS, get_weights, score_images

_W = RankingWeights(
    source={"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6},
    tier={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4},
    warn_match_count=10000,
)


def test_hit_weight_is_source_times_tier_rounded():
    assert _W.hit("note", "exact") == 1.0
    assert _W.hit("description", "fuzzy") == 0.3
    assert _W.hit("ocr", "stem") == 0.72


def test_score_images_sums_tokens_and_requires_every_token():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token_hits = [
        {a: 1.0, b: 0.6, c: 0.9},
        {a: 0.5, b: 0.9},          # c has no hit for token 2 -> dropped
    ]

    scores = score_images(token_hits)

    assert scores == {a: 1.5, b: 1.5}


def test_score_images_empty_input_and_empty_token():
    assert score_images([]) == {}
    assert score_images([{uuid.uuid4(): 1.0}, {}]) == {}


def test_score_images_rounds_to_six_decimals():
    a = uuid.uuid4()

    assert score_images([{a: 0.1}, {a: 0.2}, {a: 0.3}])[a] == 0.6   # 0.6000000000000001 unrounded


def test_get_weights_reads_the_configured_defaults():
    weights = get_weights()

    assert weights.source == {"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6}
    assert weights.tier == {"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4}
    assert weights.warn_match_count == 10000
    assert set(weights.source) == set(SOURCES) and set(weights.tier) == set(TIERS)
```

- [ ] **Step 2: Run to verify they fail**

Run: `$env:DATABASE_URL='postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test'; H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_search_scoring.py -q`
Expected: FAIL (`ModuleNotFoundError: repository.search_ranking`).

- [ ] **Step 3: Implement**

In `environments/settings.yaml`, extend the `search:` block (keep the three existing keys):

```yaml
search:
  fuzzy_min_lemma_length: 5
  fuzzy_similarity_threshold: 0.35
  phonetic_min_lemma_length: 5
  # Relevance ranking for text queries (docs/superpowers/specs/2026-10-01-search-result-ranking-design.md).
  # A hit's weight is source_weight * tier_weight; a token's score is its best hit, an image's score the sum over tokens.
  ranking:
    source_weights: {note: 1.0, ocr: 0.9, tag: 0.8, description: 0.6}
    tier_weights: {exact: 1.0, stem: 0.8, fuzzy: 0.5, phonetic: 0.4}
    # Log a warning when one query matches more than this many images: the trigger to revisit
    # Python-side scoring (docs/adr/adr-2026-10-01-search-ranking-in-python.md).
    warn_match_count: 10000
```

Create `repository/search_ranking.py`:

```python
"""Relevance scoring primitives for smart search: weights from config and the pure scorer.

See docs/superpowers/specs/2026-10-01-search-result-ranking-design.md and
docs/adr/adr-2026-10-01-search-ranking-in-python.md.
"""
import uuid
from dataclasses import dataclass
from functools import lru_cache

from config.settings import settings

SOURCES = ("note", "ocr", "tag", "description")
TIERS = ("exact", "stem", "fuzzy", "phonetic")

_SCORE_DECIMALS = 6


@dataclass(frozen=True)
class RankingWeights:
    source: dict
    tier: dict
    warn_match_count: int

    def hit(self, source: str, tier: str) -> float:
        """Weight of one hit: the source's weight times the match tier's weight."""
        return round(self.source[source] * self.tier[tier], _SCORE_DECIMALS)


@lru_cache(maxsize=1)
def get_weights() -> RankingWeights:
    ranking = settings.SEARCH.RANKING
    source_cfg = ranking.SOURCE_WEIGHTS
    tier_cfg = ranking.TIER_WEIGHTS
    missing = [f"source_weights.{k}" for k in SOURCES if k not in source_cfg]
    missing += [f"tier_weights.{k}" for k in TIERS if k not in tier_cfg]
    if missing:
        raise ValueError(f"search.ranking is missing keys: {', '.join(missing)}")
    return RankingWeights(
        source={k: float(source_cfg[k]) for k in SOURCES},
        tier={k: float(tier_cfg[k]) for k in TIERS},
        warn_match_count=int(ranking.WARN_MATCH_COUNT),
    )


def score_images(token_hits: list[dict[uuid.UUID, float]]) -> dict[uuid.UUID, float]:
    """token_hits[i] maps image id -> that image's best hit weight for query token i. Returns the images
    present for EVERY token with score = sum of their per-token weights (AND semantics)."""
    if not token_hits:
        return {}
    candidates = set(token_hits[0])
    for hits in token_hits[1:]:
        candidates &= set(hits)
    return {
        image_id: round(sum(hits[image_id] for hits in token_hits), _SCORE_DECIMALS)
        for image_id in candidates
    }
```

If Dynaconf returns keys with different casing for nested dicts (e.g. `ranking.SOURCE_WEIGHTS` is a box whose keys are the lowercase YAML keys), adjust the lookup so the test passes; do not change the YAML keys.

- [ ] **Step 4: Run to verify they pass**

Run the same pytest command. Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add environments/settings.yaml repository/search_ranking.py tests/integration/test_search_scoring.py
git commit -m "feat: search ranking weights, config and pure scorer"
```

---

### Task 2: Fix the pagination cursor skipping a row per page

**Files:**
- Modify: `Backend/app/services/image_service.py` (`_paginate_response`, ~line 476)
- Test: `Backend/tests/test_image_service.py` (add a test class)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ImageService._paginate_response(rows, items, limit, facets=None)` builds `nextCursor` from the LAST RETURNED row (`rows[limit - 1]` when `len(rows) > limit`, else `rows[-1]` as before). Ranked paging (Task 4) depends on this.

**Background:** the repository returns `limit + 1` rows to compute `hasNext`; `items` is trimmed to `limit` but `rows` is not, so `rows[-1]` is the extra, un-returned row. The next page's strict `<` cursor predicate then excludes that row, silently skipping one result per page. This step must first PROVE the defect with a failing test; if the test passes before any fix (no defect), stop, report that finding, and do not change the code.

- [ ] **Step 1: Write the failing test**

Append to `Backend/tests/test_image_service.py` (uses the module's existing imports: `datetime`, `uuid`; add `from types import SimpleNamespace` if absent):

```python
class TestPaginateResponseCursor:
    def _rows(self, n):
        base = datetime(2026, 1, 1, 12, 0, 0)
        return [
            SimpleNamespace(id=uuid.UUID(int=i + 1), created_at=base.replace(minute=59 - i))
            for i in range(n)
        ]

    def test_cursor_points_at_last_returned_row_not_the_extra_row(self):
        rows = self._rows(4)            # limit 3 -> the repo returned limit + 1 rows
        items = [object()] * 4

        response = ImageService._paginate_response(rows, items, limit=3)

        assert response.hasNext is True
        cursor_created_at, cursor_id = ImageService._decode_cursor(response.nextCursor)
        assert cursor_id == rows[2].id          # the 3rd (last returned) row, not rows[3]
        assert cursor_created_at == rows[2].created_at

    def test_cursor_is_last_row_when_there_is_no_next_page(self):
        rows = self._rows(2)

        response = ImageService._paginate_response(rows, [object()] * 2, limit=3)

        assert response.hasNext is False
        _, cursor_id = ImageService._decode_cursor(response.nextCursor)
        assert cursor_id == rows[-1].id
```

- [ ] **Step 2: Run to verify it fails**

Run (from `Backend/`): `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_image_service.py::TestPaginateResponseCursor -q`
Expected: `test_cursor_points_at_last_returned_row_not_the_extra_row` FAILS (cursor is `rows[3]`). If it passes, STOP and report.

- [ ] **Step 3: Implement**

In `Backend/app/services/image_service.py` replace `_paginate_response`:

```python
    @staticmethod
    def _paginate_response(rows, items: list, limit: int, facets: list | None = None) -> MemeSearchResponse:
        has_next = len(items) > limit
        if has_next:
            items = items[:limit]
        # `rows` holds up to limit + 1 rows (the extra one only proves hasNext). The cursor must point at the
        # last row actually returned: pointing at the extra row would make the next page's strict "after cursor"
        # predicate skip it.
        next_cursor = ImageService._encode_cursor(rows[min(len(rows), limit) - 1]) if rows else None
        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=facets or [])
```

- [ ] **Step 4: Run tests**

Run (from `Backend/`): the new class, then the full `pytest` for `Backend/tests` as its own command. Expected: all PASS. If an existing test asserted the old (extra-row) cursor, update it to the corrected expectation and say so in the report.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/image_service.py Backend/tests/test_image_service.py
git commit -m "fix: pagination cursor pointed at the extra row and skipped one result per page"
```

---

### Task 3: Scored matching in `repository/ocr_lemmas.py`

**Files:**
- Modify: `repository/ocr_lemmas.py` (helpers `_exact_lemma_ids`, `_fuzzy_lemma_ids`, `_phonetic_lemma_ids`, `_stem_lemma_ids`, `matching_image_ids`, imports)
- Test: `tests/integration/test_search_ranking_matches.py`; existing `tests/integration/test_ocr_lemmas_repository.py` and friends must keep passing

**Interfaces:**
- Consumes: `RankingWeights`, `get_weights`, `score_images` (Task 1).
- Produces:
  - `scored_image_matches(session: AsyncSession, q: Optional[str], weights: Optional[RankingWeights] = None) -> Optional[dict[uuid.UUID, float]]`: `None` = "apply no filter" (same conditions as today's `matching_image_ids`), `{}` = no image matches, otherwise `{image_id: score}`.
  - `matching_image_ids(session, q) -> Optional[set]` keeps its signature and meaning: `None` if scores is `None` else `set(scores)`.
  - Private tier helpers become `_exact_hits`, `_stem_hits`, `_fuzzy_hits`, `_phonetic_hits`: each `(session, lemma, weights) -> dict[uuid.UUID, float]` (image id -> best hit weight in that tier).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_search_ranking_matches.py`:

```python
"""
Integration tests for repository.ocr_lemmas.scored_image_matches: per-token best hit (source x tier),
summed across tokens. Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids, scored_image_matches
from repository.search_ranking import get_weights
from Storage.models import (
    DescriptionLemma, DescriptionNoteLemma, Image, ImageDescription, ImageDescriptionFeedback,
    ImageTag, OCRLemma,
)

W = get_weights()


async def _image(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    return image


async def _description_lemma(db_session, image, lemma, rejected=False):
    description = ImageDescription(image_id=image.id, prompt_key=str(uuid.uuid4()), model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma=lemma))
    if rejected:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=False))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_each_source_scores_its_own_weight_at_exact_tier(db_session):
    note_img, ocr_img, tag_img, desc_img = [await _image(db_session) for _ in range(4)]
    db_session.add_all([
        DescriptionNoteLemma(image_id=note_img.id, lemma="zebracorn"),
        OCRLemma(image_id=ocr_img.id, lemma="zebracorn"),
        ImageTag(image_id=tag_img.id, key="k", value="zebracorn", source="OCR"),
    ])
    await _description_lemma(db_session, desc_img, "zebracorn")
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn")

    assert scores[note_img.id] == W.hit("note", "exact")
    assert scores[ocr_img.id] == W.hit("ocr", "exact")
    assert scores[tag_img.id] == W.hit("tag", "exact")
    assert scores[desc_img.id] == W.hit("description", "exact")
    assert scores[note_img.id] > scores[ocr_img.id] > scores[tag_img.id] > scores[desc_img.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_token_scores_its_best_hit_not_the_sum_of_sources(db_session):
    both = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="zebracorn"),
        DescriptionNoteLemma(image_id=both.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn")

    assert scores[both.id] == W.hit("note", "exact")      # best of the two, not note + ocr


@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_tokens_sum_and_all_tokens_are_required(db_session):
    both = await _image(db_session)
    one = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="zebracorn"),
        DescriptionNoteLemma(image_id=both.id, lemma="quokkaberry"),
        OCRLemma(image_id=one.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn quokkaberry")

    assert set(scores) == {both.id}
    assert scores[both.id] == round(W.hit("ocr", "exact") + W.hit("note", "exact"), 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_exact_outranks_fuzzy_for_the_same_source(db_session):
    # The fallback tiers only run when NO source has an exact hit for the token, so exact and fuzzy are
    # exercised with two different query words.
    exact_img = await _image(db_session)
    fuzzy_img = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=exact_img.id, lemma="hedgehogz"),
        OCRLemma(image_id=fuzzy_img.id, lemma="pineapples"),
    ])
    await db_session.flush()

    exact_scores = await scored_image_matches(db_session, "hedgehogz")
    fuzzy_scores = await scored_image_matches(db_session, "pineappel")   # no exact "pineappel" anywhere

    assert exact_scores[exact_img.id] == W.hit("ocr", "exact")
    assert fuzzy_scores[fuzzy_img.id] == W.hit("ocr", "fuzzy")
    assert W.hit("ocr", "fuzzy") < W.hit("ocr", "exact")


@pytest.mark.asyncio(loop_scope="session")
async def test_english_stem_fallback_scores_stem_tier_for_ocr_and_description(db_session):
    ocr_img = await _image(db_session)
    desc_img = await _image(db_session)
    db_session.add(OCRLemma(image_id=ocr_img.id, lemma="sofa"))
    await _description_lemma(db_session, desc_img, "sofa")
    await db_session.flush()

    scores = await scored_image_matches(db_session, "sofas")

    assert scores[ocr_img.id] == W.hit("ocr", "stem")
    assert scores[desc_img.id] == W.hit("description", "stem")


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_contributes_no_score(db_session):
    rejected = await _image(db_session)
    await _description_lemma(db_session, rejected, "zebracorn", rejected=True)

    assert await scored_image_matches(db_session, "zebracorn") == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_filter_and_wrapper_semantics_are_preserved(db_session):
    assert await scored_image_matches(db_session, None) is None
    assert await scored_image_matches(db_session, "   ") is None
    assert await matching_image_ids(db_session, None) is None

    img = await _image(db_session)
    db_session.add(OCRLemma(image_id=img.id, lemma="zebracorn"))
    await db_session.flush()

    assert await matching_image_ids(db_session, "zebracorn") == {img.id}
    assert await scored_image_matches(db_session, "nonexistentqwxz") == {}
```

If `"pineappel"` does not reach `"pineapples"` under `settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD` (0.35), pick a closer pair of words rather than weakening the assertion.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/integration/test_search_ranking_matches.py -q` (with `DATABASE_URL`).
Expected: FAIL (`ImportError: cannot import name 'scored_image_matches'`).

- [ ] **Step 3: Implement**

In `repository/ocr_lemmas.py`:

Imports: add `from repository.search_ranking import RankingWeights, get_weights, score_images`.

Add helpers after `_description_forms` and replace the four tier helpers (`_exact_lemma_ids`, `_fuzzy_lemma_ids`, `_phonetic_lemma_ids`, `_stem_lemma_ids`) with hit-weight versions (keep each function's existing docstring text; only the body/return type changes):

```python
async def _best_weights(
    session: AsyncSession, tier: str, source_queries: dict, weights: RankingWeights,
) -> dict:
    """Runs one query per source and returns image id -> best hit weight (source weight x tier weight).
    `source_queries` maps a source name to a list of queries that each select image ids."""
    best: dict = {}
    for source, queries in source_queries.items():
        weight = weights.hit(source, tier)
        for query in queries:
            for (image_id,) in (await session.execute(query)).all():
                if weight > best.get(image_id, 0.0):
                    best[image_id] = weight
    return best


def _merge_max(*hit_maps: dict) -> dict:
    merged: dict = {}
    for hit_map in hit_maps:
        for image_id, weight in hit_map.items():
            if weight > merged.get(image_id, 0.0):
                merged[image_id] = weight
    return merged


async def _exact_hits(session: AsyncSession, lemma: str, weights: RankingWeights) -> dict:
    # (keep the existing comment about description lemmas being stems and matching both forms)
    return await _best_weights(session, "exact", {
        "ocr": [select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)],
        "tag": [select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())],
        "note": [select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma == lemma)],
        "description": [_description_image_ids(DescriptionLemma.lemma.in_(_description_forms(lemma)))],
    }, weights)


async def _fuzzy_hits(session: AsyncSession, lemma: str, weights: RankingWeights) -> dict:
    # (keep the existing long docstring and the SET LOCAL pg_trgm.similarity_threshold statement unchanged)
    threshold = float(settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD)
    assert 0 < threshold <= 1, f"invalid fuzzy similarity threshold: {threshold}"
    await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {threshold}"))
    return await _best_weights(session, "fuzzy", {
        "ocr": [select(OCRLemma.image_id).where(OCRLemma.lemma.op("%")(lemma))],
        "tag": [select(distinct(ImageTag.image_id)).where(ImageTag.value.op("%")(lemma))],
        "note": [select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma.op("%")(lemma))],
        "description": [
            _description_image_ids(DescriptionLemma.lemma.op("%")(form)) for form in _description_forms(lemma)
        ],
    }, weights)


async def _phonetic_hits(session: AsyncSession, lemma: str, weights: RankingWeights) -> dict:
    code = russian_metaphone(lemma)
    return await _best_weights(session, "phonetic", {
        "ocr": [select(OCRLemma.image_id).where(OCRLemma.phonetic_code == code)],
    }, weights)


async def _stem_hits(session: AsyncSession, lemma: str, weights: RankingWeights) -> dict:
    stem = stem_english_word(lemma)
    return await _best_weights(session, "stem", {
        "ocr": [select(OCRLemma.image_id).where(OCRLemma.lemma == stem)],
        "description": [_description_image_ids(DescriptionLemma.lemma == stem)],
    }, weights)
```

Replace `matching_image_ids` with the scored implementation plus a wrapper (keep the existing docstring on the wrapper, and move the tier-explanation docstring to `scored_image_matches`; add one sentence: "Returns image id -> relevance score (sum over tokens of the best hit's source x tier weight)"):

```python
async def scored_image_matches(
    session: AsyncSession, q: Optional[str], weights: Optional[RankingWeights] = None,
) -> Optional[dict]:
    """... (move the existing matching_image_ids docstring here, plus the scoring sentence above) ..."""
    if not q:
        return None

    # (keep the existing language=None comment block verbatim)
    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
    if not lemmas:
        return None

    weights = weights or get_weights()
    token_hits = []
    for lemma in lemmas:
        hits = await _exact_hits(session, lemma, weights)
        if not hits:
            if is_latin_word(lemma):
                hits = _merge_max(hits, await _stem_hits(session, lemma, weights))
            if len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
                hits = _merge_max(hits, await _fuzzy_hits(session, lemma, weights))
                if (
                    is_cyrillic_word(lemma)
                    and len(lemma) >= settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH
                    and not _is_known_word(lemma)
                ):
                    hits = _merge_max(hits, await _phonetic_hits(session, lemma, weights))
        if not hits:
            return {}
        token_hits.append(hits)

    return score_images(token_hits)


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """Set of image ids matching q (None = no filter), same semantics as before ranking existed; a thin
    wrapper over scored_image_matches for callers that do not need the scores."""
    scores = await scored_image_matches(session, q)
    return None if scores is None else set(scores)
```

Then `grep -rn "_exact_lemma_ids\|_fuzzy_lemma_ids\|_phonetic_lemma_ids\|_stem_lemma_ids" --include=*.py .` (excluding `.venv*`): any test or module that imported or patched the removed private helpers must be updated to the new names/signatures with the same intent; report each change.

- [ ] **Step 4: Run tests**

Run `pytest tests/integration/test_search_ranking_matches.py -q`, then the ENTIRE `pytest tests/integration/ -q` (separate commands, with `DATABASE_URL`). Expected: all PASS; the existing matching/equivalence/description-lemma tests prove semantics are unchanged.

- [ ] **Step 5: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_search_ranking_matches.py
git commit -m "feat: scored_image_matches with per-source tier hits; matching_image_ids wraps it"
```
(Include any updated existing test files in the `git add`.)

---

### Task 4: Ranked search path, cursor score, scale guard

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (imports, `_build_filtered_ids_query`, `search`, new `RankedRow`, `_ranked_page`)
- Modify: `Backend/app/services/image_service.py` (`search`, `_decode_search_cursor`, `_encode_cursor`, `_encode_cursor1`)
- Test: `tests/integration/test_search_ranking_repository.py`, `Backend/tests/test_image_service.py`

**Interfaces:**
- Consumes: `scored_image_matches`, `get_weights`, `RankingWeights` (Tasks 1, 3); the corrected `_paginate_response` (Task 2).
- Produces:
  - `ImageRepository.search(q, tags, cursor_created_at, cursor_id, limit, cursor_score: Optional[float] = None) -> (rows, facets)`. With `q` yielding scores: rows are `RankedRow(id, filename, created_at, flagged, score)` ordered `(score desc, created_at desc, id desc)`, at most `limit + 1`, strictly after the cursor `(cursor_score, cursor_created_at, cursor_id)` when all three are given. Without scores: unchanged rows/order; a non-`None` `cursor_score` is ignored together with the cursor (restart at page 1).
  - `ImageRepository._build_filtered_ids_query(matching_ids: Optional[set], tags: dict)` (its only caller is `search`).
  - `ImageService._decode_search_cursor(cursor) -> tuple[Optional[float], Optional[datetime], Optional[uuid.UUID]]`; `_encode_cursor1(created_at, id, score=None)`; `_encode_cursor(last_row)` includes `score` when the row has a non-None `score` attribute. Existing `_decode_cursor` (used by other endpoints) is unchanged.
  - A `WARNING` log from logger `Backend.app.repositories.image_repository` when a ranked query matches more than `weights.warn_match_count` images.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_search_ranking_repository.py`:

```python
"""
Integration tests: ImageRepository.search ranks text queries by relevance (recency as tie-breaker),
pages a ranked listing with a score cursor, leaves no-q browsing untouched and logs the scale guard.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import logging
import uuid
from datetime import datetime, timedelta

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from repository.search_ranking import RankingWeights
from Storage.models import DescriptionNoteLemma, Image, ImageTag, OCRLemma

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image(db_session, minutes=0):
    image = Image(filename=f"{uuid.uuid4()}.jpg", created_at=_T0 + timedelta(minutes=minutes))
    db_session.add(image)
    await db_session.flush()
    return image


async def _search(repo, q, **kw):
    return await repo.search(
        q=q, tags=kw.pop("tags", {}), cursor_created_at=kw.pop("cursor_created_at", None),
        cursor_id=kw.pop("cursor_id", None), limit=kw.pop("limit", 50), **kw,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_note_hit_outranks_ocr_hit_outranks_older_equal_scores_by_recency(db_session):
    older_note = await _image(db_session, minutes=0)
    newer_ocr = await _image(db_session, minutes=10)
    newest_ocr = await _image(db_session, minutes=20)
    db_session.add_all([
        DescriptionNoteLemma(image_id=older_note.id, lemma="zebracorn"),
        OCRLemma(image_id=newer_ocr.id, lemma="zebracorn"),
        OCRLemma(image_id=newest_ocr.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(ImageRepository(db_session), "zebracorn")

    ordered = [r.id for r in rows]
    # note (1.0) first even though it is the oldest; the two equal ocr hits (0.9) fall back to recency
    assert ordered == [older_note.id, newest_ocr.id, newer_ocr.id]
    assert rows[0].score > rows[1].score == rows[2].score


@pytest.mark.asyncio(loop_scope="session")
async def test_no_q_keeps_recency_order_and_plain_rows(db_session):
    older = await _image(db_session, minutes=0)
    newer = await _image(db_session, minutes=5)

    rows, _ = await _search(ImageRepository(db_session), None)

    ids = [r.id for r in rows]
    assert ids.index(newer.id) < ids.index(older.id)
    assert not hasattr(rows[0], "score")


@pytest.mark.asyncio(loop_scope="session")
async def test_ranked_listing_pages_every_match_once_in_non_increasing_score_order(db_session):
    images = []
    for i in range(7):
        image = await _image(db_session, minutes=i)
        images.append(image)
        # alternate sources so scores differ: note (1.0) vs ocr (0.9)
        if i % 2 == 0:
            db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="zebracorn"))
        else:
            db_session.add(OCRLemma(image_id=image.id, lemma="zebracorn"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    limit = 3
    seen, scores = [], []
    cursor = {}
    for _ in range(10):
        rows, _ = await _search(repo, "zebracorn", limit=limit, **cursor)
        page = rows[:limit]
        seen += [r.id for r in page]
        scores += [r.score for r in page]
        if len(rows) <= limit:
            break
        last = page[-1]
        cursor = {"cursor_score": last.score, "cursor_created_at": last.created_at, "cursor_id": last.id}

    assert sorted(seen) == sorted(i.id for i in images)         # every match exactly once
    assert len(seen) == len(set(seen)) == 7
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_ranked_query_ignores_a_recency_format_cursor_and_restarts(db_session):
    first = await _image(db_session, minutes=1)
    second = await _image(db_session, minutes=2)
    db_session.add_all([
        OCRLemma(image_id=first.id, lemma="zebracorn"),
        OCRLemma(image_id=second.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    rows, _ = await _search(
        ImageRepository(db_session), "zebracorn", cursor_created_at=second.created_at, cursor_id=second.id,
    )

    assert {r.id for r in rows} == {first.id, second.id}   # cursor without a score: restart at page 1


@pytest.mark.asyncio(loop_scope="session")
async def test_unranked_query_ignores_a_ranked_cursor(db_session):
    image = await _image(db_session, minutes=3)

    rows, _ = await _search(
        ImageRepository(db_session), None,
        cursor_score=0.9, cursor_created_at=image.created_at - timedelta(days=3650), cursor_id=uuid.uuid4(),
    )

    assert image.id in {r.id for r in rows}                # would be filtered out if the cursor were applied


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_facet_filter_intersects_with_scored_matches_and_facets_unchanged(db_session):
    tagged = await _image(db_session, minutes=1)
    untagged = await _image(db_session, minutes=2)
    db_session.add_all([
        OCRLemma(image_id=tagged.id, lemma="zebracorn"),
        OCRLemma(image_id=untagged.id, lemma="zebracorn"),
        ImageTag(image_id=tagged.id, key="animal", value="cat", source="rules"),
    ])
    await db_session.flush()

    rows, facets = await _search(ImageRepository(db_session), "zebracorn", tags={"animal": {"cat"}})

    assert {r.id for r in rows} == {tagged.id}
    assert facets["animal"]["cat"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_scale_guard_logs_a_warning_above_warn_match_count(db_session, monkeypatch, caplog):
    for i in range(3):
        image = await _image(db_session, minutes=i)
        db_session.add(OCRLemma(image_id=image.id, lemma="zebracorn"))
    await db_session.flush()
    tiny = RankingWeights(
        source={"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6},
        tier={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4},
        warn_match_count=2,
    )
    monkeypatch.setattr("Backend.app.repositories.image_repository.get_weights", lambda: tiny)
    monkeypatch.setattr("repository.ocr_lemmas.get_weights", lambda: tiny)

    with caplog.at_level(logging.WARNING, logger="Backend.app.repositories.image_repository"):
        await _search(ImageRepository(db_session), "zebracorn")

    assert any("ranked search matched" in r.getMessage() for r in caplog.records)
```

Append to `Backend/tests/test_image_service.py` (reuse its `service` / `mock_repo` fixtures and the imports already there; add `from types import SimpleNamespace` if missing):

```python
class TestSearchCursor:
    def test_ranked_cursor_round_trips_with_score(self):
        created_at = datetime(2026, 1, 1, 12, 0, 0)
        image_id = uuid.uuid4()
        row = SimpleNamespace(id=image_id, created_at=created_at, score=1.9)

        cursor = ImageService._encode_cursor(row)
        score, decoded_created_at, decoded_id = ImageService._decode_search_cursor(cursor)

        assert (score, decoded_created_at, decoded_id) == (1.9, created_at, image_id)

    def test_recency_cursor_has_no_score(self):
        created_at = datetime(2026, 1, 1, 12, 0, 0)
        image_id = uuid.uuid4()
        cursor = ImageService._encode_cursor(SimpleNamespace(id=image_id, created_at=created_at))

        assert ImageService._decode_search_cursor(cursor) == (None, created_at, image_id)
        assert ImageService._decode_search_cursor(None) == (None, None, None)

    async def test_search_passes_the_decoded_score_to_the_repository(self, service, mock_repo, monkeypatch):
        created_at = datetime(2026, 1, 1, 12, 0, 0)
        image_id = uuid.uuid4()
        cursor = ImageService._encode_cursor(SimpleNamespace(id=image_id, created_at=created_at, score=0.9))
        mock_repo.search.return_value = ([], {})
        monkeypatch.setattr(ImageService, "_record_history", AsyncMock())
        monkeypatch.setattr(ImageService, "_fill_texts_and_tags", AsyncMock())

        await service.search(q="cat", raw_facets=None, cursor=cursor, limit=10)

        kwargs = mock_repo.search.call_args.kwargs
        assert kwargs["cursor_score"] == 0.9
        assert kwargs["cursor_created_at"] == created_at and kwargs["cursor_id"] == image_id
```

- [ ] **Step 2: Run to verify they fail**

Run `pytest tests/integration/test_search_ranking_repository.py -q` (with `DATABASE_URL`) and, from `Backend/`, `pytest tests/test_image_service.py::TestSearchCursor -q`.
Expected: FAIL (`TypeError: search() got an unexpected keyword argument 'cursor_score'`, missing `_decode_search_cursor`).

- [ ] **Step 3: Implement**

`Backend/app/repositories/image_repository.py`:

Imports: add `import logging`, `import time`, `from typing import NamedTuple` (merge with the existing `typing` import), `from repository.ocr_lemmas import scored_image_matches` (replace the `matching_image_ids` import if it becomes unused), `from repository.search_ranking import get_weights`; add `logger = logging.getLogger(__name__)` below the imports and:

```python
class RankedRow(NamedTuple):
    """One page row of a relevance-ranked search: the usual image columns plus the relevance score."""
    id: uuid.UUID
    filename: str
    created_at: datetime
    flagged: Optional[bool]
    score: float
```

Change `_build_filtered_ids_query` to take the already-computed matching ids (its only caller is `search`; confirm with grep):

```python
    async def _build_filtered_ids_query(self, matching_ids: Optional[set], tags: dict[str, set]):
        """Returns a scalar-subquery of image IDs matching the text-match set and tags, unpaginated."""
        img = aliased(Image)
        image_tag = aliased(ImageTag)

        query = select(img.id).where(img.status == "active")

        if matching_ids is not None:
            query = query.where(img.id.in_(matching_ids))
        # (tags block unchanged)
        return query
```

Replace `search` (keep the facet block and the no-q branch byte-for-byte, only restructured):

```python
    async def search(
        self,
        q: Optional[str],
        tags: dict[str, set],
        cursor_created_at: Optional[datetime],
        cursor_id: Optional[uuid.UUID],
        limit: int,
        cursor_score: Optional[float] = None,
    ):
        img = aliased(Image)
        image_tag = aliased(ImageTag)

        scores = await scored_image_matches(self.session, q)
        filtered_ids = await self._build_filtered_ids_query(None if scores is None else set(scores), tags)
        filtered_ids_subquery = filtered_ids.subquery()

        # Facet counts over the full filtered set -- no pagination applied here (unchanged)
        # ... existing facets_query / raw_facets code ...

        if scores is not None:
            rows = await self._ranked_page(
                scores, filtered_ids_subquery, cursor_score, cursor_created_at, cursor_id, limit
            )
            return rows, dict(raw_facets)

        if cursor_score is not None:
            # A relevance cursor presented without a ranked query: restart at page 1.
            cursor_created_at = cursor_id = None

        # ... existing paginated recency query unchanged ...
        return results.all(), dict(raw_facets)

    async def _ranked_page(self, scores, filtered_ids_subquery, cursor_score, cursor_created_at, cursor_id, limit):
        """Relevance-ordered page: score map from matching, rows fetched for the surviving ids, sorted and
        paged in Python (docs/adr/adr-2026-10-01-search-ranking-in-python.md)."""
        started = time.perf_counter()
        img = aliased(Image)
        extras = aliased(ImageExtras)
        result = await self.session.execute(
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(img.id.in_(select(filtered_ids_subquery.c.id)))
        )
        rows = [RankedRow(r.id, r.filename, r.created_at, r.flagged, scores[r.id]) for r in result.all()]
        rows.sort(key=lambda r: (r.score, r.created_at, r.id), reverse=True)
        if cursor_score is not None and cursor_created_at is not None and cursor_id is not None:
            cursor_key = (cursor_score, cursor_created_at, cursor_id)
            rows = [r for r in rows if (r.score, r.created_at, r.id) < cursor_key]

        warn_at = get_weights().warn_match_count
        if len(scores) > warn_at:
            logger.warning(
                "ranked search matched %d images (> %d); scoring+paging took %.0f ms",
                len(scores), warn_at, (time.perf_counter() - started) * 1000,
            )
        return rows[: limit + 1]
```

`Backend/app/services/image_service.py`:

In `search`, replace the cursor decode and repo call:

```python
        cursor_score, cursor_created_at, cursor_id = self._decode_search_cursor(cursor)

        rows, raw_facet_map = await self.repo.search(
            q=q,
            tags=tags,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
            cursor_score=cursor_score,
        )
```

Add next to the other cursor helpers, and extend the encoders:

```python
    @staticmethod
    def _decode_search_cursor(cursor: Optional[str]):
        """(score, created_at, id) for the main search endpoint: score is None for a recency-format cursor."""
        if not cursor:
            return None, None, None
        obj = json.loads(base64.urlsafe_b64decode(cursor).decode())
        score = obj.get("score")
        return (
            float(score) if score is not None else None,
            datetime.fromisoformat(obj["created_at"]),
            uuid.UUID(obj["id"]),
        )

    @staticmethod
    def _encode_cursor(last_row) -> str:
        return ImageService._encode_cursor1(last_row.created_at, last_row.id, getattr(last_row, "score", None))

    @staticmethod
    def _encode_cursor1(created_at, id, score=None):
        payload = {"id": str(id), "created_at": created_at.isoformat()}
        if score is not None:
            payload["score"] = score
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
```
(Replace the existing `_encode_cursor` / `_encode_cursor1` bodies; existing callers keep working because `score` defaults to `None` and the payload key order `id, created_at` is unchanged.)

- [ ] **Step 4: Run tests**

Run, as separate commands: `pytest tests/integration/test_search_ranking_repository.py -q`; then the ENTIRE `pytest tests/integration/ -q` (with `DATABASE_URL`; existing `repo.search` tests and the equivalence test must still pass unmodified); then from `Backend/`: the full `pytest -q`; then `pytest batch/tests/ -q`.
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py tests/integration/test_search_ranking_repository.py Backend/tests/test_image_service.py
git commit -m "feat: relevance-ordered search with score cursor and scale-guard warning"
```

---

### Task 5: Docs and full verification

**Files:**
- Modify: `backend_api.md`, `docs/data-flow.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-01-search-result-ranking-design.md` (Status line)

- [ ] **Step 1: Update docs**

- `backend_api.md`: in the `GET /api/images` section state: with a non-empty `q`, results are ordered by relevance (per-token best hit, source weight x match-tier weight, summed across tokens) then recency; without `q` they are ordered by recency (unchanged); `nextCursor` stays an opaque string (relevance pages embed the score); a cursor from the other format is ignored and the listing restarts at the first page. Also note the fix: no result is skipped between pages.
- `docs/data-flow.md`: in the search consumer description add the ranking sentence and name the config location (`search.ranking.*` in `environments/settings.yaml`); remove "search has no ranking (task 156)" from the known-gaps summary if present.
- `CLAUDE.md`: in the Configuration paragraph mention the `search.ranking.*` keys (weights and `warn_match_count`) and link `docs/adr/adr-2026-10-01-search-ranking-in-python.md` as the place to look when revisiting Python-side scoring.
- Spec: set `Status:` to `done`; set the `Plan:` line to `docs/superpowers/plans/2026-10-01-search-result-ranking.md`.

- [ ] **Step 2: Full verification (four separate commands)**

```
$env:DATABASE_URL='postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test'; H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -q
cd Backend; H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest -q
H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -q
H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/rules/ -q
```
Also from the repo root with `DATABASE_URL` and `BASE_PATH` set to the test DB and a temp dir: `python -c "import Backend.app.main"` (expect no import error). Report each pass count.

- [ ] **Step 3: Commit**

```bash
git add backend_api.md docs/data-flow.md CLAUDE.md docs/superpowers/specs/2026-10-01-search-result-ranking-design.md
git commit -m "docs: search result ranking (api, data flow, config)"
```

- [ ] **Step 4: Controller-only follow-up (not for subagents)**

After merge, the controller smoke-tests the three running backends with plain GETs (`/api/images?limit=5&q=<word>` returns 200; page 2 via `nextCursor` returns no duplicates of page 1) and records the observed ordering. No live-DB writes.
