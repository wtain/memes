# OCR Language Plausibility Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce cross-language OCR garbage by scoring every `ocr_texts` row for how plausible its recognized text is in its assigned reader-language (`wordfreq`-based lexical scoring), storing that score non-destructively, and filtering on it downstream in `build_bow.py` and `build_tags_from_ocr.py` (the latter has no language filtering today — a real bug this plan also fixes). Ends with a golden-set eval script that measures filtering quality, headlined by false-suppression rate.

**Architecture:** A single pure-function module, `rules/lang_plausibility.py`, computes `score(text, language) -> float | None` and the shared predicate `passes_language_filter(...)`. It lives under `rules/` (not `batch/`) because `repository/ocr_text.py` needs to call it, and this codebase's layering never has `repository/` import from `batch/` — only `batch/` imports from `rules/` (confirmed: `grep` found zero `repository/*.py` files importing `batch`, and five `batch/*.py` files already importing `rules`). Placing it under `rules/` alongside `rules/normalize.py` (which it reuses for tokenization) keeps that layering intact. The score is computed once, at write time, by `repository/ocr_text.py::overwrite_texts`, and stored in a new nullable `ocr_texts.lang_score` column — never used to delete data. A backfill script scores pre-existing rows. Two existing consumers (`build_bow.py`, `build_tags_from_ocr.py`) gain an `OCR_LANG_SCORE_MIN` threshold check, mirroring the existing `OCR_CONFIDENCE_MIN` pattern. A golden-set eval script (mirroring `batch/eval_rules.py`) measures precision/recall/F1 and false-suppression rate.

**Tech Stack:** Python 3.11 (`.venv311`), `wordfreq==3.1.1` (new dependency — pure-Python, ships static frequency data for `en`/`es`/`ru`, no GPU/network at runtime), SQLAlchemy 2.0 async ORM, Alembic, pytest / pytest-asyncio.

## Global Constraints

- Never delete or skip persisting `ocr_texts` rows because of `lang_score` — filtering happens downstream at read time only (spec: "Score is destructive or additive?" → Additive).
- `lang_score = NULL` means "not scored" (fewer than 2 alphabetic tokens) and must pass through every downstream filter unfiltered — never treated as garbage (spec: "Short / non-alphabetic rows").
- Scoring constants: `_MIN_ALPHA_TOKENS = 2`, `_ZIPF_KNOWN_THRESHOLD = 1.0` (spec: Scoring Function).
- `OCR_LANG_SCORE_MIN` env var, default `0.3`, read the same way `OCR_CONFIDENCE_MIN` (default `0.4`) already is in `build_bow.py` / `build_tags_from_ocr.py`.
- Reuse `rules.normalize.tokenize` for tokenization — do not reimplement (spec: Side Notes).
- Repositories must not call `session.commit()` (project-wide convention, `CLAUDE.md`) — applies to every new/modified repository method in this plan. (Pre-existing violations in `TagsRepository`/`TagsSaver` are out of scope.)
- All commands run through `.venv311` (Python 3.11), per project convention.
- `wordfreq` is the only new dependency this plan introduces (plus its small transitive deps `msgpack`, `locate`, `langcodes` — `ftfy` and `regex`, its other dependencies, are already pinned in `requirements.txt`).

---

## File Structure

| File | Responsibility |
|---|---|
| `rules/lang_plausibility.py` | *(new)* `score(text, language) -> float \| None` and `passes_language_filter(confidence, lang_score, confidence_min, lang_score_min) -> bool` — pure, no I/O. |
| `requirements.txt` | *(modified)* add `wordfreq==3.1.1`, `msgpack==1.2.1`, `locate==1.1.1`, `langcodes==3.5.1`. |
| `Storage/models.py` | *(modified)* `OCRText` gains `lang_score = Column(Float, nullable=True)`. |
| `Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py` | *(new)* migration adding the column. |
| `repository/ocr_text.py` | *(modified)* `overwrite_texts` computes `lang_score` per row; `get_all_texts_with_language` returns it; new `get_rows_for_scoring` / `update_lang_score` for the backfill script. |
| `repository/images.py` | *(modified)* `get_images_and_ocr_texts` / `get_images_and_ocr_texts_without_tags` select `lang_score` too. |
| `batch/build_bow.py` | *(modified)* `_build_ocr_bow` gains an `OCR_LANG_SCORE_MIN` check via `passes_language_filter`. |
| `batch/build_tags_from_ocr.py` | *(modified)* same filter added — this file currently has **no** language filtering at all (bug fix). |
| `batch/score_ocr_language.py` | *(new)* backfill script — scores existing rows without re-running OCR. |
| `batch/data/tagging/golden_ocr_language.yaml` | *(new)* hand-labeled starter golden set (24 rows) for the eval metric. |
| `batch/eval_ocr_language_filter.py` | *(new)* golden-set eval — precision/recall/F1, false-suppression rate, threshold sweep. |
| `tests/rules/test_lang_plausibility.py` | *(new)* unit tests for `score()` / `passes_language_filter()`. |
| `tests/integration/test_ocr_text_repository.py` | *(new)* integration tests for the `repository/ocr_text.py` changes. |
| `tests/integration/test_images_repository.py` | *(new)* integration tests for the `repository/images.py` changes. |
| `tests/integration/test_build_ocr_bow_lang_filter.py` | *(new)* integration test proving `build_bow.py`'s new filter excludes garbage rows. |
| `tests/batch/test_eval_ocr_language_filter.py` | *(new)* unit tests for the eval script's pure evaluation math. |

`batch/build_tags_from_ocr.py`'s wiring change gets **no** new test file — see Task 6's Testing Note for why.

---

## Execution Order

**Phase 1 — parallel (independent files):**
- Task 1: `rules/lang_plausibility.py` + `wordfreq` dependency
- Task 2: `Storage/models.py` + migration (`lang_score` column)
- Task 8: `batch/data/tagging/golden_ocr_language.yaml` (pure content, no code dependency)

**Phase 2 — parallel (each depends only on Phase 1 tasks, and touches files no other Phase-2 task touches):**
- Task 3: `repository/ocr_text.py` (needs Task 1 + Task 2)
- Task 4: `repository/images.py` (needs Task 2)
- Task 9: `batch/eval_ocr_language_filter.py` (needs Task 1; pairs with Task 8's golden file for a real run, but its unit tests use synthetic fixtures so has no hard file dependency on Task 8)

**Phase 3 — parallel (each depends on one Phase-2 task, and touches files no other Phase-3 task touches):**
- Task 5: `batch/build_bow.py` (needs Task 1 + Task 3)
- Task 6: `batch/build_tags_from_ocr.py` (needs Task 1 + Task 4)
- Task 7: `batch/score_ocr_language.py` (needs Task 1 + Task 3)

**Phase 4 — sequential, after everything above:**
- Task 10: run the eval script against the golden set, confirm `OCR_LANG_SCORE_MIN=0.3` is reasonable (or adjust), close the loop.

---

## Task 1: `rules/lang_plausibility.py`

**Files:**
- Create: `rules/lang_plausibility.py`
- Modify: `requirements.txt`
- Test: `tests/rules/test_lang_plausibility.py`

**Interfaces:**
- Produces: `score(text: str, language: str) -> float | None` — fraction of alphabetic tokens recognized as real words in `language` (via `wordfreq.zipf_frequency`); `None` if fewer than 2 alphabetic tokens.
- Produces: `passes_language_filter(confidence: float | None, lang_score: float | None, confidence_min: float, lang_score_min: float) -> bool` — `False` if `confidence` is not `None` and below `confidence_min`, or `lang_score` is not `None` and below `lang_score_min`; `True` otherwise.
- Consumes: `rules.normalize.tokenize(text: str) -> list[str]` (existing).

- [ ] **Step 1: Add the `wordfreq` dependency and install it**

Edit `requirements.txt`. Three separate insertions to keep the file's existing alphabetical (case-insensitive) order:

```diff
 kiwisolver==1.4.9
+langcodes==3.5.1
 lazy_loader==0.4
+locate==1.1.1
 Mako==1.3.10
```

```diff
 mpmath==1.3.0
+msgpack==1.2.1
 multidict==6.7.1
```

```diff
 watchfiles==1.1.1
 wcwidth==0.2.14
 websocket-client==1.9.0
+wordfreq==3.1.1
 wsproto==1.3.2
```

Run: `.venv311/Scripts/python -m pip install wordfreq==3.1.1`
Expected: `Successfully installed langcodes-3.5.1 locate-1.1.1 msgpack-1.2.1 wordfreq-3.1.1` (versions may already be satisfied if a prior step installed them).

- [ ] **Step 2: Write the failing tests**

```python
# tests/rules/test_lang_plausibility.py
from rules.lang_plausibility import passes_language_filter, score


def test_score_none_for_text_with_fewer_than_two_alpha_tokens():
    assert score("42", "en") is None
    assert score("lol", "en") is None


def test_score_high_for_genuine_english_sentence():
    result = score("when your friends finally get the joke", "en")
    assert result is not None
    assert result > 0.8


def test_score_high_for_genuine_russian_sentence():
    result = score("когда друзья наконец поняли шутку", "ru")
    assert result is not None
    assert result > 0.8


def test_score_high_for_genuine_spanish_sentence():
    result = score("cuando tus amigos por fin entienden el chiste", "es")
    assert result is not None
    assert result > 0.8


def test_score_low_for_garbled_latin_misread_of_cyrillic():
    # Simulates an `en` EasyOCR reader's best-effort Latin-glyph guess at a
    # Cyrillic-only image: valid Latin script, but not real English words.
    result = score("ctapt 3gect xdbl qwzk", "en")
    assert result is not None
    assert result < 0.3


def test_score_is_case_insensitive():
    assert score("WHEN YOUR FRIENDS FINALLY GET THE JOKE", "en") == score(
        "when your friends finally get the joke", "en"
    )


def test_passes_language_filter_rejects_low_confidence():
    assert passes_language_filter(0.2, 0.9, confidence_min=0.4, lang_score_min=0.3) is False


def test_passes_language_filter_rejects_low_lang_score():
    assert passes_language_filter(0.9, 0.1, confidence_min=0.4, lang_score_min=0.3) is False


def test_passes_language_filter_none_lang_score_passes_through():
    assert passes_language_filter(0.9, None, confidence_min=0.4, lang_score_min=0.3) is True


def test_passes_language_filter_none_confidence_passes_through():
    assert passes_language_filter(None, 0.9, confidence_min=0.4, lang_score_min=0.3) is True


def test_passes_language_filter_both_ok():
    assert passes_language_filter(0.9, 0.9, confidence_min=0.4, lang_score_min=0.3) is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv311/Scripts/python -m pytest tests/rules/test_lang_plausibility.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rules.lang_plausibility'`

- [ ] **Step 4: Write the implementation**

```python
# rules/lang_plausibility.py
from wordfreq import zipf_frequency

from rules.normalize import tokenize

_MIN_ALPHA_TOKENS = 2
_ZIPF_KNOWN_THRESHOLD = 1.0  # below this, wordfreq effectively hasn't seen the word


def score(text: str, language: str) -> float | None:
    """
    Fraction of alphabetic tokens in `text` that are recognized words in
    `language`, per wordfreq's frequency data. Returns None if there are
    fewer than _MIN_ALPHA_TOKENS alphabetic tokens to judge from (too
    short/noisy to score reliably) rather than guessing.
    """
    tokens = [t for t in tokenize(text) if not t.isdigit()]
    if len(tokens) < _MIN_ALPHA_TOKENS:
        return None

    known = sum(1 for t in tokens if zipf_frequency(t.lower(), language) >= _ZIPF_KNOWN_THRESHOLD)
    return known / len(tokens)


def passes_language_filter(
    confidence: float | None,
    lang_score: float | None,
    confidence_min: float,
    lang_score_min: float,
) -> bool:
    """True if the row should be kept — used by build_bow.py and build_tags_from_ocr.py."""
    if confidence is not None and confidence < confidence_min:
        return False
    if lang_score is not None and lang_score < lang_score_min:
        return False
    return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv311/Scripts/python -m pytest tests/rules/test_lang_plausibility.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Commit**

```bash
git add rules/lang_plausibility.py requirements.txt tests/rules/test_lang_plausibility.py
git commit -m "feat(rules): add wordfreq-based OCR language plausibility scoring"
```

---

## Task 2: `Storage/models.py` + migration

**Files:**
- Modify: `Storage/models.py:61-75` (the `OCRText` class)
- Create: `Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py`

**Interfaces:**
- Produces: `OCRText.lang_score` (`Float`, nullable) — consumed by Task 3 (`repository/ocr_text.py`) and Task 4 (`repository/images.py`).

- [ ] **Step 1: Add the column to the model**

In `Storage/models.py`, modify the `OCRText` class:

```diff
 class OCRText(Base):
     __tablename__ = "ocr_texts"

     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
     image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

     text = Column(Text, nullable=False)
     confidence = Column(Float)
     bbox = Column(JSON)            # polygon or x,y,w,h
     language = Column(String(8), default="en")
+    lang_score = Column(Float, nullable=True)  # None = not scored (too short); else 0.0-1.0

     created_at = Column(DateTime, server_default=func.now())

     image = relationship("Image", back_populates="texts")
```

- [ ] **Step 2: Confirm the model change is syntactically valid**

Run: `.venv311/Scripts/python -c "from Storage.models import OCRText; print(OCRText.lang_score)"`
Expected: prints `OCRText.lang_score` (or equivalent `Column` repr) with no traceback. (This only needs `DATABASE_URL` set to *some* value — `tests/conftest.py`'s `os.environ.setdefault` already does this for any command invoked from the repo root when `PYTHONPATH` picks up `tests/conftest.py`'s side effect isn't automatic outside pytest, so instead run: `DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test_db .venv311/Scripts/python -c "from Storage.models import OCRText; print(OCRText.lang_score)"` on bash, or set the env var first on PowerShell.)

- [ ] **Step 3: Write the migration**

Current Alembic head is `a1b2c3d4e5f6` (confirmed via `alembic heads`). Create:

```python
# Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py
"""add lang_score to ocr_texts

Revision ID: b7f3c9a2d4e1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7f3c9a2d4e1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ocr_texts', sa.Column('lang_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('ocr_texts', 'lang_score')
```

- [ ] **Step 4: Verify the migration is recognized as the new head**

Run (from `Storage/`): `../.venv311/Scripts/python -m alembic heads`
Expected: `b7f3c9a2d4e1 (head)`

If you have a live dev database configured (per `CLAUDE.md`'s migration workflow — load `environments/.env.*` then `alembic upgrade head`), run it there too and confirm no errors. This is not required for the test suite: `tests/integration/conftest.py` creates tables directly from `Storage/models.py` via `Base.metadata.create_all`, independent of Alembic.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py
git commit -m "feat(storage): add lang_score column to ocr_texts"
```

---

## Task 3: `repository/ocr_text.py`

**Files:**
- Modify: `repository/ocr_text.py`
- Test: `tests/integration/test_ocr_text_repository.py`

**Interfaces:**
- Consumes: `rules.lang_plausibility.score(text: str, language: str) -> float | None` (Task 1); `OCRText.lang_score` (Task 2).
- Produces: `OCRTextRepository.get_all_texts_with_language()` now returns rows of `(text, confidence, language, lang_score)` — a breaking shape change consumed by Task 5 (`build_bow.py`).
- Produces: `OCRTextRepository.get_rows_for_scoring(rescore_all: bool = False)` → rows of `(id, text, language)` — consumed by Task 7 (`batch/score_ocr_language.py`).
- Produces: `OCRTextRepository.update_lang_score(text_id, lang_score: float | None) -> None` — consumed by Task 7.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_ocr_text_repository.py
"""
Integration tests for repository/ocr_text.py's lang_score behaviour.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.ocr_text import OCRTextRepository
from Storage.models import Image, OCRText


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_scores_genuine_text_high(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(
        image,
        [(_BBOX, "when your friends finally get the joke", 0.95)],
        "en",
    )
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_scores_garbled_cross_language_misread_low(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(
        image,
        [(_BBOX, "ctapt 3gect xdbl qwzk", 0.72)],
        "en",
    )
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score == pytest.approx(0.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_leaves_short_text_unscored(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(image, [(_BBOX, "lol", 0.5)], "en")
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_texts_with_language_includes_lang_score(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    rows = await repo.get_all_texts_with_language()
    matches = [
        (text, confidence, language, lang_score)
        for text, confidence, language, lang_score in rows
        if text == "when your friends finally get the joke"
    ]
    assert len(matches) == 1
    assert matches[0][3] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_rows_for_scoring_defaults_to_unscored_only(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(
        image,
        [
            (_BBOX, "when your friends finally get the joke", 0.9),  # scored (1.0)
            (_BBOX, "lol", 0.5),  # unscored (None)
        ],
        "en",
    )
    await db_session.flush()

    unscored = await repo.get_rows_for_scoring(rescore_all=False)
    unscored_texts = {r.text for r in unscored if r.text in ("when your friends finally get the joke", "lol")}
    assert unscored_texts == {"lol"}

    all_rows = await repo.get_rows_for_scoring(rescore_all=True)
    all_texts = {r.text for r in all_rows if r.text in ("when your friends finally get the joke", "lol")}
    assert all_texts == {"when your friends finally get the joke", "lol"}


@pytest.mark.asyncio(loop_scope="session")
async def test_update_lang_score_writes_the_value(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(image, [(_BBOX, "lol", 0.5)], "en")
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score is None

    await repo.update_lang_score(row.id, 0.42)
    await db_session.flush()
    await db_session.refresh(row)
    assert row.lang_score == pytest.approx(0.42)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_ocr_text_repository.py -v`
(Requires `DATABASE_URL` pointed at a live pgvector instance, per `tests/integration/conftest.py`.)
Expected: FAIL — `test_overwrite_texts_scores_genuine_text_high` fails with `assert None == pytest.approx(1.0)` (column exists from Task 2 but nothing populates it yet); `get_rows_for_scoring` / `update_lang_score` tests fail with `AttributeError`.

- [ ] **Step 3: Implement the repository changes**

```python
# repository/ocr_text.py
import numpy
from sqlalchemy import delete, select, text, update
from sqlalchemy.sql.functions import count

from Storage.models import OCRText
from rules.lang_plausibility import score as compute_lang_score


class OCRTextRepository:

    def __init__(self, session):
        self.session = session

    async def count_texts(self) -> int:
        result = await self.session.execute(select(count(OCRText.id)))
        return result.scalar_one()

    async def delete_duplicate_texts(self) -> int:
        """Delete duplicate OCR rows per (image_id, normalized_text, language).

        Keeps the row with the highest confidence; on tie, keeps the earliest.
        Returns the number of deleted rows.
        """
        stmt = text("""
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY image_id, lower(trim(text)), language
                           ORDER BY confidence DESC NULLS LAST, created_at ASC
                       ) AS rn
                FROM ocr_texts
            )
            DELETE FROM ocr_texts
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """)
        result = await self.session.execute(stmt)
        return result.rowcount

    async def get_all_texts_with_language(self):
        result = await self.session.execute(
            select(OCRText.text, OCRText.confidence, OCRText.language, OCRText.lang_score)
        )
        return result.all()

    async def overwrite_texts(self, image, ocr_result, language):
        await self.session.execute(
            delete(OCRText).where(
                OCRText.image_id == image.id,
                OCRText.language == language,
            )
        )

        for bbox, text, confidence in ocr_result:
            # todo: threshold confidence
            # todo: create session once
            self.session.add(
                OCRText(
                    image_id=image.id,
                    text=text,
                    confidence=float(confidence),
                    bbox=[[v.item() if isinstance(v, numpy.int32) else v for v in p] for p in bbox],
                    language=language,
                    lang_score=compute_lang_score(text, language),
                )
            )

    async def get_rows_for_scoring(self, rescore_all: bool = False):
        """Rows to (re)score. By default, only rows with lang_score IS NULL."""
        query = select(OCRText.id, OCRText.text, OCRText.language)
        if not rescore_all:
            query = query.where(OCRText.lang_score.is_(None))
        result = await self.session.execute(query)
        return result.all()

    async def update_lang_score(self, text_id, lang_score: float | None) -> None:
        await self.session.execute(
            update(OCRText).where(OCRText.id == text_id).values(lang_score=lang_score)
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_ocr_text_repository.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add repository/ocr_text.py tests/integration/test_ocr_text_repository.py
git commit -m "feat(repository): compute and expose lang_score on ocr_texts rows"
```

---

## Task 4: `repository/images.py`

**Files:**
- Modify: `repository/images.py:17-29` (`get_images_and_ocr_texts`) and `repository/images.py:31-48` (`get_images_and_ocr_texts_without_tags`)
- Test: `tests/integration/test_images_repository.py`

**Interfaces:**
- Consumes: `OCRText.lang_score` (Task 2), `OCRTextRepository.overwrite_texts` (Task 3, to seed test data).
- Produces: `ImagesRepository.get_images_and_ocr_texts()` and `get_images_and_ocr_texts_without_tags(source)` now both yield rows of `(filename, image_id, text, confidence, lang_score)` — a breaking shape change consumed by Task 6 (`build_tags_from_ocr.py`).

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_images_repository.py
"""
Integration tests for repository/images.py's lang_score exposure.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.images import ImagesRepository
from repository.ocr_text import OCRTextRepository
from Storage.models import Image

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_includes_lang_score(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    result = await images_repo.get_images_and_ocr_texts()
    matches = [
        (filename, img_id, txt, confidence, lang_score)
        for filename, img_id, txt, confidence, lang_score in result.all()
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_tags_includes_lang_score(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, "ctapt 3gect xdbl qwzk", 0.7)], "en")
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    result = await images_repo.get_images_and_ocr_texts_without_tags("OCR")
    matches = [
        (filename, img_id, txt, confidence, lang_score)
        for filename, img_id, txt, confidence, lang_score in result.all()
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_images_repository.py -v`
Expected: FAIL with `ValueError: too many values to unpack (expected 4)` (current queries select 4 columns; test unpacks 5).

- [ ] **Step 3: Implement the repository changes**

```diff
     async def get_images_and_ocr_texts(self):
         query = (
             select(
                 self.img.filename,
                 self.img.id,
                 self.ocr.text,
-                self.ocr.confidence
+                self.ocr.confidence,
+                self.ocr.lang_score
             ).join(
                 self.ocr, self.ocr.image_id == self.img.id
             )
         )
         images_and_texts_results = await self.session.execute(query)
         return images_and_texts_results

     async def get_images_and_ocr_texts_without_tags(self, source: str):
         already_tagged = (
             select(ImageTag.image_id)
             .where(ImageTag.source == source)
             .distinct()
             .scalar_subquery()
         )
         query = (
             select(
                 self.img.filename,
                 self.img.id,
                 self.ocr.text,
-                self.ocr.confidence
+                self.ocr.confidence,
+                self.ocr.lang_score
             )
             .join(self.ocr, self.ocr.image_id == self.img.id)
             .where(self.img.id.not_in(already_tagged))
         )
         return await self.session.execute(query)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_images_repository.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add repository/images.py tests/integration/test_images_repository.py
git commit -m "feat(repository): expose ocr_texts.lang_score from ImagesRepository OCR queries"
```

---

## Task 5: `batch/build_bow.py`

**Files:**
- Modify: `batch/build_bow.py:1-16` (imports), `:87-101` (`main`), `:146-169` (`_build_ocr_bow`)
- Test: `tests/integration/test_build_ocr_bow_lang_filter.py`

**Interfaces:**
- Consumes: `rules.lang_plausibility.passes_language_filter` (Task 1); `OCRTextRepository.get_all_texts_with_language()` now returning `(text, confidence, language, lang_score)` (Task 3).

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_build_ocr_bow_lang_filter.py
"""
Integration test proving build_bow.py's OCR_LANG_SCORE_MIN filter excludes
cross-language garbage rows from the word-frequency output.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from batch.build_bow import _build_ocr_bow
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.models import Image, OCRText


@pytest.mark.asyncio(loop_scope="session")
async def test_build_ocr_bow_excludes_low_lang_score_rows(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    db_session.add_all([
        OCRText(
            image_id=image.id,
            text="genuine english words here",
            confidence=0.9,
            language="en",
            lang_score=1.0,
        ),
        OCRText(
            image_id=image.id,
            text="garbled cross language misread",
            confidence=0.9,
            language="en",
            lang_score=0.05,
        ),
    ])
    await db_session.flush()

    morph = make_morph()
    metrics = SimpleMetricsListener()
    output = await _build_ocr_bow(
        db_session,
        morph,
        confidence_min=0.4,
        lang_score_min=0.3,
        min_word_length=3,
        min_frequency=1,
        metrics=metrics,
    )

    en_lemmas = output.get("en", {})
    assert "genuine" in en_lemmas
    assert "garbled" not in en_lemmas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_build_ocr_bow_lang_filter.py -v`
Expected: FAIL with `TypeError: _build_ocr_bow() got an unexpected keyword argument 'lang_score_min'`

- [ ] **Step 3: Implement the changes**

Add the import:

```diff
 from metrics.listener import SimpleMetricsListener
 from rules.normalize import lemmatize_word, make_morph, tokenize
+from rules.lang_plausibility import passes_language_filter
 from Storage.db import AsyncSessionLocal
 from repository.ocr_text import OCRTextRepository
 from repository.ollama_descriptions import OllamaDescriptionsRepository
```

Update `main()`:

```diff
 async def main():
     text_source = os.getenv("TEXT_SOURCE", TEXT_SOURCE_OCR)
     ocr_confidence_min = float(os.getenv("OCR_CONFIDENCE_MIN", "0.4"))
+    ocr_lang_score_min = float(os.getenv("OCR_LANG_SCORE_MIN", "0.3"))
     min_word_length = int(os.getenv("BOW_MIN_WORD_LENGTH", "3"))
     min_frequency = int(os.getenv("BOW_MIN_FREQUENCY", "2"))
     output_file = os.getenv("BOW_OUTPUT_FILE")
     ignore_file = os.getenv("BOW_IGNORE_FILE")
     rules_file = os.getenv("RULES_FILE")
     unmatched_file = os.getenv("BOW_UNMATCHED_FILE")

     print(f"TEXT_SOURCE={text_source}")
     print(f"BOW_MIN_WORD_LENGTH={min_word_length}, BOW_MIN_FREQUENCY={min_frequency}")
     if text_source == TEXT_SOURCE_OCR:
         print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}")
+        print(f"OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
     print(f"BOW_OUTPUT_FILE={output_file}")
```

```diff
     async with AsyncSessionLocal() as session:
         if text_source == TEXT_SOURCE_OCR:
-            output = await _build_ocr_bow(session, morph, ocr_confidence_min, min_word_length, min_frequency, metrics)
+            output = await _build_ocr_bow(
+                session, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length, min_frequency, metrics
+            )
         elif text_source == TEXT_SOURCE_DESCRIPTIONS:
```

Update `_build_ocr_bow`:

```python
async def _build_ocr_bow(session, morph, confidence_min, lang_score_min, min_word_length, min_frequency, metrics):
    repo = OCRTextRepository(session)
    rows = await repo.get_all_texts_with_language()

    lang_counters = defaultdict(Counter)

    for text, confidence, language, lang_score in rows:
        metrics.increment("ocr.rows.total")
        if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
            if confidence is not None and confidence < confidence_min:
                metrics.increment("ocr.rows.skipped.low_confidence")
            else:
                metrics.increment("ocr.rows.skipped.low_lang_score")
            continue
        lang = language or "unknown"
        for word in tokenize(text):
            if len(word) < min_word_length or word.isdigit():
                continue
            lang_counters[lang][lemmatize_word(word, morph)] += 1
        metrics.increment("ocr.rows.processed")

    output = {}
    for lang, counter in sorted(lang_counters.items()):
        filtered = _apply_min_frequency(counter, min_frequency, metrics)
        output[lang] = dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))
        print(f"Unique lemmas ({lang}): {len(output[lang])}")
    return output
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv311/Scripts/python -m pytest tests/integration/test_build_ocr_bow_lang_filter.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/build_bow.py tests/integration/test_build_ocr_bow_lang_filter.py
git commit -m "feat(batch): filter build_bow OCR word counts by lang_score"
```

---

## Task 6: `batch/build_tags_from_ocr.py`

**Files:**
- Modify: `batch/build_tags_from_ocr.py`

**Interfaces:**
- Consumes: `rules.lang_plausibility.passes_language_filter` (Task 1); `ImagesRepository.get_images_and_ocr_texts()` / `get_images_and_ocr_texts_without_tags()` now returning 5-tuples with `lang_score` (Task 4).

**Testing note:** No new test file for this task. `main()` calls `session.commit()` internally (via `TagsRepository.delete_tags` and `TagsSaver.__aexit__`), which is incompatible with the rollback-based `db_session` fixture used by every other integration test in this plan (`tests/integration/conftest.py` wraps each test in `session.begin()` and rolls back at the end — a manual `commit()` inside the code under test breaks that isolation). This is a pre-existing limitation, not something this task introduces: `build_tags_from_ocr.py` has zero test coverage today for the same reason, as do `reset_ocr_status.py` and `deduplicate_ocr_texts.py`. The core filtering logic being wired in here (`passes_language_filter`) is already exhaustively unit-tested in Task 1; this task is purely wiring, identical in shape to the pre-existing (untested) confidence check it replaces.

- [ ] **Step 1: Make the change**

```diff
 import argparse
 import asyncio
 import os
 from pathlib import Path

 from metrics.listener import SimpleMetricsListener
 from rules.concept_tagger import ConceptTagger
+from rules.lang_plausibility import passes_language_filter
 from Storage.db import AsyncSessionLocal
 from repository.images import ImagesRepository
 from repository.tags import TagsRepository, TagsSaver

 _SCRIPT_DIR = Path(__file__).parent
 DATA_DIR = os.getenv("TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
 PROFILE = os.getenv("TAGGING_PROFILE", "general")
 OCR_CONFIDENCE_MIN = float(os.getenv("OCR_CONFIDENCE_MIN", "0.4"))
+OCR_LANG_SCORE_MIN = float(os.getenv("OCR_LANG_SCORE_MIN", "0.3"))

 engine = ConceptTagger.load(DATA_DIR, PROFILE)


 async def main(incremental: bool):
     async with AsyncSessionLocal() as session:
         tags_repo = TagsRepository(session)
         images_repo = ImagesRepository(session)

         if not incremental:
             await tags_repo.delete_tags("OCR")

         total_images = await images_repo.get_total_images()
         print(f"Total images: {total_images}")
         print(f"Tagging with profile '{PROFILE}' from {DATA_DIR} ...")
         print(f"Mode: {'incremental' if incremental else 'full'}")
+        print(f"OCR_CONFIDENCE_MIN={OCR_CONFIDENCE_MIN}, OCR_LANG_SCORE_MIN={OCR_LANG_SCORE_MIN}")

         if incremental:
             images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags("OCR")
         else:
             images_and_texts_results = await images_repo.get_images_and_ocr_texts()

         metrics = SimpleMetricsListener()

         async with TagsSaver(session) as tags_saver:
-            for filename, image_id, text, confidence in images_and_texts_results:
-                if confidence < OCR_CONFIDENCE_MIN:
+            for filename, image_id, text, confidence, lang_score in images_and_texts_results:
+                if not passes_language_filter(confidence, lang_score, OCR_CONFIDENCE_MIN, OCR_LANG_SCORE_MIN):
                     metrics.increment("images.skipped")
                     continue
                 result = engine.tag(text)
                 tag_count = len(result.tags)
                 for tag_name, tag_value in result.tags:
                     tags_saver.add_tag(image_id, tag_name, tag_value, "OCR")
                 metrics.increment("images.processed")
                 metrics.add("tags.total", tag_count)
                 metrics.bucket("tags_per_image", tag_count)

     print("Done:")
     metrics.print()
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test_db BASE_PATH=/tmp/test_images .venv311/Scripts/python -c "import batch.build_tags_from_ocr"`
Expected: no traceback. (`ConceptTagger.load(DATA_DIR, PROFILE)` runs at import time against the real `batch/data/tagging/` files already checked into the repo, so this needs no live DB — it only needs `DATABASE_URL` set to *something*, same as any module that imports `Storage.db`.)

- [ ] **Step 3: Commit**

```bash
git add batch/build_tags_from_ocr.py
git commit -m "fix(batch): filter build_tags_from_ocr by lang_score (was unfiltered by language)"
```

---

## Task 7: `batch/score_ocr_language.py`

**Files:**
- Create: `batch/score_ocr_language.py`

**Interfaces:**
- Consumes: `rules.lang_plausibility.score` (Task 1); `OCRTextRepository.get_rows_for_scoring` / `update_lang_score` (Task 3, already integration-tested there).

**Testing note:** No new test file. This script is a thin DB loop composing two already-tested pieces (`OCRTextRepository.get_rows_for_scoring`/`update_lang_score`, integration-tested in Task 3; `score()`, unit-tested in Task 1). It follows the exact same shape as the existing untested maintenance scripts `batch/reset_ocr_status.py` and `batch/deduplicate_ocr_texts.py` — neither has test coverage in this codebase, for the same reason discussed in Task 6 (their `main()` commits directly, incompatible with the rollback-based test fixture).

- [ ] **Step 1: Write the script**

```python
# batch/score_ocr_language.py
"""
Backfill lang_score for existing ocr_texts rows (added by
Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py). New rows are
scored automatically going forward by repository/ocr_text.py::overwrite_texts
— this script is only needed for rows written before that change, or to
recompute every row after tuning rules/lang_plausibility.py's constants.

Usage:
    python -m batch.score_ocr_language                # scores rows where lang_score IS NULL
    python -m batch.score_ocr_language --rescore-all   # recomputes lang_score for every row
"""
import argparse
import asyncio

from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository
from rules.lang_plausibility import score

COMMIT_EVERY = 500


async def run(rescore_all: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        repo = OCRTextRepository(session)
        rows = await repo.get_rows_for_scoring(rescore_all=rescore_all)
        print(f"Rows to score: {len(rows)}")

        scored = 0
        for row in rows:
            lang_score = score(row.text, row.language)
            await repo.update_lang_score(row.id, lang_score)
            scored += 1
            if scored % COMMIT_EVERY == 0:
                await session.commit()
                print(f"  scored {scored}/{len(rows)}")

        await session.commit()
        print(f"Done. Scored {scored} rows.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescore-all",
        action="store_true",
        help="Recompute lang_score for every row, not just unscored ones",
    )
    args = parser.parse_args()
    asyncio.run(run(rescore_all=args.rescore_all))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and the CLI parses**

Run: `DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test_db BASE_PATH=/tmp/test_images .venv311/Scripts/python -m batch.score_ocr_language --help`
Expected: prints the usage/help text with `--rescore-all`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add batch/score_ocr_language.py
git commit -m "feat(batch): add score_ocr_language backfill script"
```

---

## Task 8: `batch/data/tagging/golden_ocr_language.yaml`

**Files:**
- Create: `batch/data/tagging/golden_ocr_language.yaml`

**Interfaces:**
- Produces: golden set consumed by Task 9's `main()` (default `--golden` path) and by Task 10's manual eval run. Not consumed by Task 9's unit tests, which use synthetic fixtures.

This is a starter set (24 rows) covering genuine text in all three reader languages, the specific cross-language-misread failure mode from the spec, and short/edge rows that must pass through unscored. Every row's expected label was verified against `rules.lang_plausibility.score()` (Task 1) during planning — see the table below the file. The spec calls for growing this to ~250-300 rows sampled from real production data; that's follow-up work (see Side Note), not blocking for this plan.

- [ ] **Step 1: Write the golden set**

```yaml
# batch/data/tagging/golden_ocr_language.yaml
#
# Hand-labeled OCR rows for evaluating rules/lang_plausibility.py's
# garbage-filtering quality (batch/eval_ocr_language_filter.py).
#
# is_garbage: true  -> this row should be filtered out (wrong-language misread)
# is_garbage: false -> this row is genuine text (or too short to judge) and
#                       must NOT be filtered out
#
# This is a starter set (24 rows). Grow it by sampling real ocr_texts rows:
# stratify on images where a high-confidence `ru` row exists alongside `en`/`es`
# rows for the same image_id — the `en`/`es` rows are strong garbage candidates
# for manual review. Example query:
#
#   SELECT o.id, o.text, o.language, o.confidence, o.image_id
#   FROM ocr_texts o
#   WHERE o.image_id IN (
#       SELECT image_id FROM ocr_texts WHERE language = 'ru' AND confidence > 0.7
#   )
#   AND o.language IN ('en', 'es')
#   ORDER BY o.image_id;

# --- genuine Russian ---
- text: "СТАРТ ЗДЕСЬ"
  language: ru
  is_garbage: false
- text: "когда друзья наконец поняли шутку"
  language: ru
  is_garbage: false
- text: "это невозможно объяснить словами"
  language: ru
  is_garbage: false
- text: "лучший день моей жизни сегодня"
  language: ru
  is_garbage: false
- text: "никто не ожидал такого поворота"
  language: ru
  is_garbage: false

# --- genuine English ---
- text: "when your friends finally get the joke"
  language: en
  is_garbage: false
- text: "this cannot be explained in words"
  language: en
  is_garbage: false
- text: "best day of my life today"
  language: en
  is_garbage: false
- text: "nobody expected this plot twist"
  language: en
  is_garbage: false
- text: "me trying to act normal in public"
  language: en
  is_garbage: false

# --- genuine Spanish ---
- text: "cuando tus amigos por fin entienden el chiste"
  language: es
  is_garbage: false
- text: "esto no se puede explicar con palabras"
  language: es
  is_garbage: false
- text: "el mejor dia de mi vida hoy"
  language: es
  is_garbage: false
- text: "nadie esperaba este giro de la trama"
  language: es
  is_garbage: false
- text: "yo tratando de actuar normal en publico"
  language: es
  is_garbage: false

# --- garbage: `en` reader's Latin-glyph misread of a Cyrillic-only image ---
- text: "ctapt 3gect xdbl qwzk"
  language: en
  is_garbage: true
- text: "koraa apysba noham etotk"
  language: en
  is_garbage: true
- text: "nouemy annapat gsotkuu"
  language: en
  is_garbage: true

# --- garbage: `es` reader's Latin-glyph misread of a Cyrillic-only image ---
- text: "xtapq zbelo ktumna"
  language: es
  is_garbage: true
- text: "vprst konay tuzhda"
  language: es
  is_garbage: true
- text: "ficko qwenb starzt"
  language: es
  is_garbage: true

# --- short/edge rows: too short to score (lang_score = None), must pass through ---
- text: "42"
  language: en
  is_garbage: false
- text: "lol"
  language: en
  is_garbage: false
- text: "xd"
  language: es
  is_garbage: false
```

- [ ] **Step 2: Verify the YAML parses and every row has the required keys**

Run:
```
.venv311/Scripts/python -c "
import yaml
rows = yaml.safe_load(open('batch/data/tagging/golden_ocr_language.yaml', encoding='utf-8'))
assert len(rows) == 24
for r in rows:
    assert set(r.keys()) == {'text', 'language', 'is_garbage'}
    assert r['language'] in ('ru', 'en', 'es')
    assert isinstance(r['is_garbage'], bool)
print('OK', len(rows), 'rows')
"
```
Expected: `OK 24 rows`

- [ ] **Step 3: Commit**

```bash
git add batch/data/tagging/golden_ocr_language.yaml
git commit -m "test(batch): add starter golden set for OCR language plausibility eval"
```

---

## Task 9: `batch/eval_ocr_language_filter.py`

**Files:**
- Create: `batch/eval_ocr_language_filter.py`
- Test: `tests/batch/test_eval_ocr_language_filter.py`
- Create: `tests/batch/__init__.py` (empty, if it does not already exist)

**Interfaces:**
- Consumes: `rules.lang_plausibility.score` (Task 1).
- Produces: `_classify(row_score: float | None, threshold: float) -> bool`, `evaluate_at_threshold(scored_items: list[tuple[float | None, bool]], threshold: float) -> dict` (keys: `threshold, tp, fp, fn, tn, precision, recall, f1, false_suppression_rate`), `threshold_sweep(scored_items, thresholds: list[float]) -> list[dict]`, `_score_golden_items(items: list[dict]) -> list[tuple[float | None, bool]]` — all pure, no DB. `main()` is the CLI entry point (not unit tested — thin argparse + print wiring, same convention as `eval_rules.py`).

- [ ] **Step 1: Create the test package (if needed) and write the failing tests**

Run: `mkdir -p tests/batch && [ -f tests/batch/__init__.py ] || touch tests/batch/__init__.py` (skip if `tests/batch/__init__.py` already exists from a prior batch of work — it does in this repo, per `tests/batch/test_build_lemma_clusters.py`).

```python
# tests/batch/test_eval_ocr_language_filter.py
import pytest

from batch.eval_ocr_language_filter import (
    _classify,
    _score_golden_items,
    evaluate_at_threshold,
    threshold_sweep,
)


def test_classify_none_score_never_flagged():
    assert _classify(None, threshold=0.9) is False


def test_classify_low_score_flagged():
    assert _classify(0.1, threshold=0.3) is True


def test_classify_high_score_not_flagged():
    assert _classify(0.9, threshold=0.3) is False


def test_evaluate_at_threshold_basic_counts():
    scored_items = [
        (1.0, False),   # TN
        (0.0, True),    # TP
        (None, False),  # TN (pass-through)
        (0.1, False),   # FP - genuine row scored low, wrongly flagged
    ]
    result = evaluate_at_threshold(scored_items, threshold=0.3)

    assert result["tp"] == 1
    assert result["fp"] == 1
    assert result["fn"] == 0
    assert result["tn"] == 2
    assert result["precision"] == pytest.approx(0.5)
    assert result["recall"] == pytest.approx(1.0)
    assert result["false_suppression_rate"] == pytest.approx(1 / 3)


def test_evaluate_at_threshold_no_positives_defaults_to_perfect_precision_recall():
    scored_items = [(1.0, False), (0.9, False)]
    result = evaluate_at_threshold(scored_items, threshold=0.3)

    assert result["tp"] == 0
    assert result["fp"] == 0
    assert result["fn"] == 0
    assert result["precision"] == pytest.approx(1.0)
    assert result["recall"] == pytest.approx(1.0)
    assert result["false_suppression_rate"] == pytest.approx(0.0)


def test_threshold_sweep_returns_one_result_per_threshold():
    scored_items = [(1.0, False), (0.0, True)]
    thresholds = [0.1, 0.3, 0.5]

    results = threshold_sweep(scored_items, thresholds)

    assert [r["threshold"] for r in results] == thresholds


def test_score_golden_items_uses_real_lang_plausibility_score():
    items = [
        {"text": "when your friends finally get the joke", "language": "en", "is_garbage": False},
        {"text": "lol", "language": "en", "is_garbage": False},
    ]

    scored = _score_golden_items(items)

    assert scored[0][0] == pytest.approx(1.0)
    assert scored[0][1] is False
    assert scored[1][0] is None
    assert scored[1][1] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_eval_ocr_language_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.eval_ocr_language_filter'`

- [ ] **Step 3: Write the implementation**

```python
# batch/eval_ocr_language_filter.py
"""
Evaluate rules/lang_plausibility.py's garbage-filtering quality against a
hand-labeled golden set (batch/data/tagging/golden_ocr_language.yaml).

Usage:
    python -m batch.eval_ocr_language_filter \
        --golden batch/data/tagging/golden_ocr_language.yaml \
        [--threshold 0.3]

Prints precision/recall/F1 and false-suppression rate at the given threshold
(false-suppression rate first and separately — see the Metric section of
docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md for
why it's the headline number), plus a sweep across candidate thresholds to
support choosing OCR_LANG_SCORE_MIN deliberately.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from rules.lang_plausibility import score

_SWEEP_THRESHOLDS = [round(0.1 + 0.05 * i, 2) for i in range(11)]  # 0.10 .. 0.60


def _load_golden(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _score_golden_items(items: list[dict]) -> list[tuple[float | None, bool]]:
    return [(score(item["text"], item["language"]), item["is_garbage"]) for item in items]


def _classify(row_score: float | None, threshold: float) -> bool:
    """True = flagged as garbage. A None score (too short to judge) is never flagged."""
    if row_score is None:
        return False
    return row_score < threshold


def evaluate_at_threshold(scored_items: list[tuple[float | None, bool]], threshold: float) -> dict:
    tp = fp = fn = tn = 0
    for row_score, actual_garbage in scored_items:
        predicted_garbage = _classify(row_score, threshold)
        if predicted_garbage and actual_garbage:
            tp += 1
        elif predicted_garbage and not actual_garbage:
            fp += 1
        elif not predicted_garbage and actual_garbage:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    genuine_total = tn + fp
    false_suppression_rate = fp / genuine_total if genuine_total else 0.0

    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_suppression_rate": false_suppression_rate,
    }


def threshold_sweep(scored_items: list[tuple[float | None, bool]], thresholds: list[float]) -> list[dict]:
    return [evaluate_at_threshold(scored_items, t) for t in thresholds]


def _print_report(scored_items: list[tuple[float | None, bool]], threshold: float) -> None:
    result = evaluate_at_threshold(scored_items, threshold)

    print(f"Items: {len(scored_items)}")
    print(f"Threshold: {threshold}")
    print()
    genuine_total = result["fp"] + result["tn"]
    print(
        f"False-suppression rate: {result['false_suppression_rate']:.3f}  "
        f"({result['fp']} genuine rows wrongly flagged / {genuine_total} genuine total)"
    )
    print()
    print(f"Precision={result['precision']:.3f}  Recall={result['recall']:.3f}  F1={result['f1']:.3f}")
    print(f"TP={result['tp']} FP={result['fp']} FN={result['fn']} TN={result['tn']}")
    print()
    print("Threshold sweep:")
    header = f"{'Threshold':>10}  {'Prec':>7}  {'Rec':>7}  {'F1':>7}  {'FalseSupp':>10}"
    print(header)
    print("-" * len(header))
    for row in threshold_sweep(scored_items, _SWEEP_THRESHOLDS):
        print(
            f"{row['threshold']:>10.2f}  {row['precision']:>7.3f}  {row['recall']:>7.3f}  "
            f"{row['f1']:>7.3f}  {row['false_suppression_rate']:>10.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default="batch/data/tagging/golden_ocr_language.yaml")
    parser.add_argument("--threshold", type=float, default=0.3)
    args = parser.parse_args()

    items = _load_golden(Path(args.golden))
    if not items:
        print("Golden set is empty.")
        return

    scored_items = _score_golden_items(items)
    _print_report(scored_items, args.threshold)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv311/Scripts/python -m pytest tests/batch/test_eval_ocr_language_filter.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add batch/eval_ocr_language_filter.py tests/batch/test_eval_ocr_language_filter.py
git commit -m "feat(batch): add golden-set eval for OCR language plausibility filtering"
```

---

## Task 10: Run the eval and confirm the default threshold

**Files:** none (verification-only task; no code changes).

This closes the loop from the spec's Implementation Order (steps 7-9): use the eval script to justify `OCR_LANG_SCORE_MIN=0.3` rather than leaving it as an unverified guess.

- [ ] **Step 1: Run the eval script against the starter golden set**

Run: `.venv311/Scripts/python -m batch.eval_ocr_language_filter --golden batch/data/tagging/golden_ocr_language.yaml --threshold 0.3`

Expected output shape (exact numbers on this 24-row starter set, given the scores verified during planning — all 15 genuine rows score `1.0`, all 6 garbage rows score `0.0`, all 3 short rows score `None`):
```
Items: 24
Threshold: 0.3

False-suppression rate: 0.000  (0 genuine rows wrongly flagged / 18 genuine total)

Precision=1.000  Recall=1.000  F1=1.000
TP=6 FP=0 FN=0 TN=18
```

- [ ] **Step 2: Interpret the result**

A perfect score on this starter set is expected and **not sufficient evidence that `OCR_LANG_SCORE_MIN=0.3` is well-tuned** — every row here is a clean-cut example by construction (see Task 8). It confirms the scoring function and eval plumbing work end-to-end. Real confidence in the threshold requires the golden set to grow with real production rows sampled via the query in `batch/data/tagging/golden_ocr_language.yaml`'s header comment (harder cases: borderline confidence, slang, short-but-scoreable rows, proper nouns). Record this as a follow-up, not a blocker — do not hand-tune `_ZIPF_KNOWN_THRESHOLD` or `OCR_LANG_SCORE_MIN` further based on this 24-row set alone.

- [ ] **Step 3: (Optional, needs a live dev DB) Run the backfill and rebuild downstream artifacts**

If a live dev database is available (per `CLAUDE.md`'s environment setup):
```
python -m batch.score_ocr_language
python -m batch.build_bow          # TEXT_SOURCE=ocr, per existing env config
python -m batch.build_tags_from_ocr
```
Spot-check that known garbage clusters (e.g. `en`/`es` word lists on RU-heavy environments) shrink, per the spec's Implementation Order step 9. This step has no automated assertion — it's a manual production-data sanity check, not part of the test suite.

No commit for this task (no files changed).

---

## Self-Review

**Spec coverage:**
- Root cause / why script-based checks don't work → captured in the plan's Architecture line and Task 1.
- Per-row granularity decision → Task 1-4 design (row-level `lang_score`), no per-image aggregate added.
- `wordfreq` lexical scoring, not LID/script-check → Task 1.
- Additive storage, non-destructive → Task 2 (nullable column), Task 3 (`overwrite_texts` never skips a row for scoring reasons).
- `NULL` = pass-through → enforced in `passes_language_filter` (Task 1) and tested explicitly (Task 1, Task 9).
- Downstream threshold filtering in `build_bow.py` and `build_tags_from_ocr.py`, including the `build_tags_from_ocr.py` bug fix → Task 5, Task 6.
- Backfill script → Task 7.
- Golden set + eval script with false-suppression rate as headline metric + threshold sweep → Task 8, Task 9, Task 10.
- Non-Goals (no reader/routing changes, no per-image aggregate, no deletion, no new statistical LID) → respected throughout; nothing in this plan touches `extract_text_from_memes.py`'s reader set or `ocr_preprocess.py`.

**Placeholder scan:** no TBD/TODO introduced by this plan (the pre-existing `# todo: threshold confidence` / `# todo: create session once` comments in `repository/ocr_text.py` are left untouched — out of scope, not new).

**Type consistency:** `score(text, language) -> float | None` (Task 1) is the single definition used identically by `repository/ocr_text.py` (Task 3), `batch/score_ocr_language.py` (Task 7), and `batch/eval_ocr_language_filter.py` (Task 9). `passes_language_filter(confidence, lang_score, confidence_min, lang_score_min) -> bool` (Task 1) is used with the same positional order in `build_bow.py` (Task 5) and `build_tags_from_ocr.py` (Task 6). The `get_all_texts_with_language()` 4-tuple shape change (Task 3) and the `get_images_and_ocr_texts*` 5-tuple shape change (Task 4) are each consumed by exactly one downstream task (Task 5 and Task 6 respectively) with matching unpacking.