# Smart Search (Phase 1: Cross-Line Join + Lemma Matching) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace substring-based OCR text search with lemma-based matching against a precomputed per-image lemma index, fixing cross-line phrase matching and Russian case/word-form mismatches, and unify `/api/images` and `/api/recommendations` onto one shared matching implementation.

**Architecture:** A new `ocr_lemmas` table stores, per image, the union of lemmas from all its OCR lines (computed offline by a new batch job, reusing `rules/normalize.py`). A new shared function in `repository/ocr_lemmas.py` lemmatizes the query string at request time and matches its lemmas (AND across lemmas) against `ocr_lemmas` and `ImageTag.value`. Both backend repositories call this one function instead of their current divergent substring logic.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async ORM, PostgreSQL, pymorphy3, pytest / pytest-asyncio, Alembic.

## Global Constraints

- Target Python: 3.11 (`.venv311`) — see CLAUDE.md "Python environments".
- Windows dev: no special env vars needed for this work (no uvicorn `--reload` changes).
- Reuse existing settings — no new config keys: `settings.OCR.CONFIDENCE_MIN`, `settings.OCR.LANG_SCORE_MIN`, `settings.BOW.MIN_WORD_LENGTH` (see `environments/settings.yaml`).
- Repositories under `Backend/app/repositories/` must never call `session.commit()` — `get_async_db` handles it. Repositories under top-level `repository/` (batch-facing) already do call `session.commit()` themselves (see `repository/tags.py`) — the new writer in `repository/ocr_lemmas.py` follows that existing convention, not the Backend one.
- `Backend/tests/`, `tests/integration/`, `batch/tests/`, `tests/rules/` are separate `pytest.ini` roots — never combine them in one `pytest` invocation (see CLAUDE.md "Known gotchas").
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line against `ocrdb_test`, e.g.:
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
- `backend_api.md` must stay in sync with the actual routers/behavior — update it as part of this plan, not as an afterthought.
- Spec: `docs/superpowers/specs/2026-07-21-smart-search-design.md` — read it before starting if any task here is unclear on rationale.

---

### Task 1: `normalize()` gains `keep_digit_tokens`

**Files:**
- Modify: `rules/normalize.py:73-98` (the `normalize` function)
- Test: `tests/rules/test_normalize.py`

**Interfaces:**
- Produces: `normalize(text: str, morph: pymorphy3.MorphAnalyzer, min_length: int = 3, language: str | None = None, keep_digit_tokens: bool = False) -> set[str]`. When `keep_digit_tokens=True`, a token that is all-digits and at least `min_length` characters long is kept as a literal lemma (the digit string itself, unchanged) instead of being dropped. Default `False` — every existing caller (`build_bow.py`, rules engine) is unaffected.

- [ ] **Step 1: Write the failing tests**

Add to `tests/rules/test_normalize.py` (append to the bottom of the file):

```python
class TestNormalizeKeepDigitTokens:
    def test_digit_token_dropped_by_default(self):
        morph = make_morph()
        assert normalize("year 2020 report", morph) == {"year", "report"}

    def test_digit_token_kept_when_requested(self):
        morph = make_morph()
        assert normalize("year 2020 report", morph, keep_digit_tokens=True) == {"year", "2020", "report"}

    def test_short_digit_token_still_dropped_when_kept(self):
        morph = make_morph()
        assert normalize("a 12 report", morph, min_length=3, keep_digit_tokens=True) == {"report"}

    def test_kept_digit_token_is_not_lemmatized(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = normalize("2020", wrapped, keep_digit_tokens=True)
        assert result == {"2020"}
        wrapped.parse.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -v -k KeepDigitTokens`
Expected: FAIL — `TypeError: normalize() got an unexpected keyword argument 'keep_digit_tokens'`

- [ ] **Step 3: Implement `keep_digit_tokens`**

Replace the `normalize` function in `rules/normalize.py`:

```python
def normalize(
    text: str,
    morph: pymorphy3.MorphAnalyzer,
    min_length: int = 3,
    language: str | None = None,
    keep_digit_tokens: bool = False,
) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set.

    keep_digit_tokens=True keeps a pure-digit token (still subject to
    min_length) as a literal lemma instead of dropping it — used by search
    indexing/matching, where numeric queries (years, model numbers) should
    still be findable. Default False preserves the original tag/concept-
    vocabulary behavior for every other caller.
    """
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length:
            continue
        if word.isdigit():
            if keep_digit_tokens:
                result.add(word)
            continue
        lemma = lemmatize_word(word, morph, language)
        result.add(lemma)
        # r/subreddit OCR artifact: "r/Metallica" → "rimetallica" (slash read as 'i')
        m = _SUBREDDIT_OCR_RE.match(word)
        if m:
            suffix = m.group(1)
            if len(suffix) >= min_length:
                result.add(lemmatize_word(suffix, morph, language))
        # Trailing punctuation artifact: "SLAYER!!" → "slayerll" (!! read as ll)
        # Strip doubled trailing letter and emit the shorter form.
        if len(word) > min_length + 1 and word[-1].isalpha() and word[-1] == word[-2]:
            shorter = word[:-2]
            if len(shorter) >= min_length:
                result.add(lemmatize_word(shorter, morph, language))
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: PASS (all tests, including the pre-existing ones — confirms no regression)

- [ ] **Step 5: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: add keep_digit_tokens option to normalize() for search indexing"
```

---

### Task 2: `OCRLemma` model + migration

**Files:**
- Modify: `Storage/models.py` (add `OCRLemma` class; add relationship on `Image`)
- Create: `Storage/alembic/versions/<generated>_add_ocr_lemmas_table.py`

**Interfaces:**
- Produces: `Storage.models.OCRLemma` with columns `image_id` (UUID, FK to `images.id`, `ondelete="CASCADE"`, part of composite PK) and `lemma` (String, part of composite PK); table name `ocr_lemmas`; index `ix_ocr_lemmas_lemma` on `lemma`.

- [ ] **Step 1: Add the model**

In `Storage/models.py`, add the relationship to `Image` (inside the `Image` class, alongside the other `relationship(...)` lines, e.g. right after the `tags` line):

```python
    ocr_lemmas = relationship("OCRLemma", back_populates="image", cascade="all, delete-orphan")
```

Then add a new model class — place it directly after the `ImageTag` class (around line 224, right before `class Concept(Base):`):

```python
class OCRLemma(Base):
    __tablename__ = "ocr_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
    )

    image = relationship("Image", back_populates="ocr_lemmas")
```

- [ ] **Step 2: Confirm the model imports cleanly**

Run: `python -c "from Storage.models import OCRLemma; print(OCRLemma.__tablename__)"`
Expected: prints `ocr_lemmas`, no import errors.

- [ ] **Step 3: Generate the Alembic migration**

From `Storage/`, with env vars loaded for any one environment (the schema is identical across metal/general/it):

```powershell
Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic revision --autogenerate -m "add_ocr_lemmas_table"
```

Expected: a new file `Storage/alembic/versions/<hash>_add_ocr_lemmas_table.py` is created, with a real `revision`/`down_revision` pair (Alembic fills these from the current migration head — don't hand-edit them).

- [ ] **Step 4: Verify the generated migration body**

Open the generated file and confirm `upgrade()`/`downgrade()` match this shape (column/type details, not the revision IDs, which Alembic already filled in):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ocr_lemmas',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('lemma', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id', 'lemma')
    )
    op.create_index('ix_ocr_lemmas_lemma', 'ocr_lemmas', ['lemma'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ocr_lemmas_lemma', table_name='ocr_lemmas')
    op.drop_table('ocr_lemmas')
```

If autogenerate produced something equivalent but differently ordered/named (e.g. index name), that's fine — leave Alembic's own naming. If it's missing the `ondelete='CASCADE'` on the FK (autogenerate sometimes drops this), add it by hand.

- [ ] **Step 5: Apply the migration to your local dev DB**

```powershell
alembic upgrade head
```

Expected: no errors; `\d ocr_lemmas` in `psql` shows the table with the composite PK and the lemma index.

- [ ] **Step 6: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/
git commit -m "feat: add ocr_lemmas table for per-image search lemma index"
```

---

### Task 3: Pure per-image lemma-grouping helper

**Files:**
- Create: `batch/utils/ocr_lemmas.py`
- Test: `batch/tests/test_ocr_lemmas_grouping.py`

**Interfaces:**
- Consumes: `rules.normalize.normalize(text, morph, min_length, language, keep_digit_tokens)` (Task 1), `rules.lang_plausibility.passes_language_filter(confidence, lang_score, confidence_min, lang_score_min)` (existing).
- Produces: `group_lemmas_by_image(rows, morph, confidence_min, lang_score_min, min_word_length) -> tuple[dict[uuid.UUID, set[str]], dict[str, int]]`. `rows` is an iterable of `(image_id, text, confidence, language, lang_score)` tuples. Returns `(lemmas_by_image, stats)` where `stats` has keys `"rows_total"`, `"rows_skipped"`, `"rows_processed"`.

This module has **no DB imports** (no `Storage.db`), so it can be unit-tested without `DATABASE_URL` set — same reasoning as `batch/utils/file_hash.py`.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_ocr_lemmas_grouping.py`:

```python
from rules.normalize import make_morph
from batch.utils.ocr_lemmas import group_lemmas_by_image

_MORPH = make_morph()


def test_unions_lemmas_across_multiple_rows_for_same_image():
    rows = [
        ("img-1", "звоню в", 0.9, "ru", 1.0),
        ("img-1", "полицию", 0.9, "ru", 1.0),
    ]
    lemmas_by_image, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "полиция" in lemmas_by_image["img-1"]
    assert "звонить" in lemmas_by_image["img-1"]


def test_separate_images_kept_separate():
    rows = [
        ("img-1", "cat picture", 0.9, "en", 1.0),
        ("img-2", "dog picture", 0.9, "en", 1.0),
    ]
    lemmas_by_image, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "cat" in lemmas_by_image["img-1"]
    assert "dog" not in lemmas_by_image["img-1"]
    assert "dog" in lemmas_by_image["img-2"]


def test_low_confidence_row_skipped():
    rows = [("img-1", "cat picture", 0.1, "en", 1.0)]
    lemmas_by_image, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats == {"rows_total": 1, "rows_skipped": 1, "rows_processed": 0}


def test_low_lang_score_row_skipped():
    rows = [("img-1", "cat picture", 0.9, "en", 0.0)]
    lemmas_by_image, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats["rows_skipped"] == 1


def test_digit_tokens_kept_as_lemmas():
    rows = [("img-1", "made in 2020", 0.9, "en", 1.0)]
    lemmas_by_image, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "2020" in lemmas_by_image["img-1"]


def test_stats_counts_total_and_processed():
    rows = [
        ("img-1", "cat", 0.9, "en", 1.0),
        ("img-1", "dog", 0.1, "en", 1.0),
    ]
    _, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert stats == {"rows_total": 2, "rows_skipped": 1, "rows_processed": 1}


def test_no_rows_returns_empty():
    lemmas_by_image, stats = group_lemmas_by_image([], _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert lemmas_by_image == {}
    assert stats == {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest batch/tests/test_ocr_lemmas_grouping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.utils.ocr_lemmas'`

- [ ] **Step 3: Implement the helper**

Create `batch/utils/ocr_lemmas.py`:

```python
from collections import defaultdict

from rules.lang_plausibility import passes_language_filter
from rules.normalize import normalize


def group_lemmas_by_image(rows, morph, confidence_min, lang_score_min, min_word_length):
    """
    rows: iterable of (image_id, text, confidence, language, lang_score).

    Returns (lemmas_by_image, stats):
      - lemmas_by_image: dict[image_id, set[str]] — the union of lemmas
        across every surviving OCR row for that image. This union is what
        makes cross-line phrase matching work: a multi-word query matches
        as soon as each word's lemma is present anywhere in the image's
        set, regardless of which OCR line contributed it.
      - stats: {"rows_total": int, "rows_skipped": int, "rows_processed": int}
    """
    lemmas_by_image = defaultdict(set)
    stats = {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}

    for image_id, text, confidence, language, lang_score in rows:
        stats["rows_total"] += 1
        if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
            stats["rows_skipped"] += 1
            continue
        lemmas_by_image[image_id] |= normalize(
            text, morph, min_length=min_word_length, language=language, keep_digit_tokens=True
        )
        stats["rows_processed"] += 1

    return dict(lemmas_by_image), stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest batch/tests/test_ocr_lemmas_grouping.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add batch/utils/ocr_lemmas.py batch/tests/test_ocr_lemmas_grouping.py
git commit -m "feat: add pure per-image OCR lemma grouping helper"
```

---

### Task 4: `ImagesRepository` incremental-lookup method

**Files:**
- Modify: `repository/images.py`
- Test: `tests/integration/test_images_repository.py`

**Interfaces:**
- Consumes: `Storage.models.OCRLemma` (Task 2).
- Produces: `ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language() -> Sequence[tuple[filename, image_id, text, confidence, language, lang_score]]` — same row shape as `get_images_and_ocr_texts_with_language()`, restricted to images with no `ocr_lemmas` rows yet.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_images_repository.py`:

```python
from Storage.models import OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_lemmas_excludes_indexed_images(db_session):
    indexed = Image(filename=f"{uuid.uuid4()}.jpg")
    not_indexed = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([indexed, not_indexed])
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(indexed, [(_BBOX, "already indexed text", 0.9)], "en")
    await ocr_repo.overwrite_texts(not_indexed, [(_BBOX, "not indexed yet", 0.9)], "en")
    await db_session.flush()

    db_session.add(OCRLemma(image_id=indexed.id, lemma="already"))
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    matched_ids = {img_id for _filename, img_id, _text, _confidence, _language, _lang_score in rows}

    assert not_indexed.id in matched_ids
    assert indexed.id not in matched_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v -k without_lemmas`
Expected: FAIL — `AttributeError: 'ImagesRepository' object has no attribute 'get_images_and_ocr_texts_without_lemmas_with_language'`

- [ ] **Step 3: Implement the method**

In `repository/images.py`, add `OCRLemma` to the import (line 6): `from Storage.models import OCRText, Image, ImageDescription, ImageTag, OCRLemma`. Then add the method (e.g. directly after `get_images_and_ocr_texts_without_tags_with_language`):

```python
    async def get_images_and_ocr_texts_without_lemmas_with_language(self):
        already_indexed = (
            select(OCRLemma.image_id)
            .distinct()
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_indexed))
        )
        result = await self.session.execute(query)
        return result.fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add repository/images.py tests/integration/test_images_repository.py
git commit -m "feat: add ImagesRepository lookup for images missing from the OCR lemma index"
```

---

### Task 5: Shared writer + matching function (`repository/ocr_lemmas.py`)

**Files:**
- Create: `repository/ocr_lemmas.py`
- Test: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Consumes: `Storage.models.OCRLemma`, `Storage.models.ImageTag`, `rules.normalize.normalize`/`make_morph` (Task 1/existing), `config.settings.settings`.
- Produces:
  - `OCRLemmasRepository(session).delete_all() -> None` — deletes all rows, commits.
  - `OCRLemmasSaver(session)` — async context manager; `.add_lemmas(image_id, lemmas: set[str])` stages rows; commits on exit.
  - `matching_image_ids(session, q: Optional[str]) -> Optional[set]` — `None` means "apply no filter" (falsy `q`, or every query token normalized away to nothing); otherwise the set of matching image IDs (AND across query lemmas; empty set means no image matches).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_ocr_lemmas_repository.py`:

```python
"""
Integration tests for repository/ocr_lemmas.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver, matching_image_ids
from Storage.models import Image, ImageTag, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_writes_one_row_per_lemma(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    async with OCRLemmasSaver(db_session) as saver:
        saver.add_lemmas(image.id, {"кот", "собака"})

    rows = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert set(rows) == {"кот", "собака"}


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_clears_table(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="кот"))
    await db_session.flush()

    await OCRLemmasRepository(db_session).delete_all()

    remaining = (await db_session.execute(select(OCRLemma))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio(loop_scope="session")
async def test_no_query_returns_none(db_session):
    assert await matching_image_ids(db_session, None) is None
    assert await matching_image_ids(db_session, "") is None
    assert await matching_image_ids(db_session, "   ") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_single_lemma_matches_indexed_image(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="полиция"),
        OCRLemma(image_id=other.id, lemma="магазин"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "полицию")

    assert ids == {matching.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_word_query_requires_all_lemmas(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="звонить"),
        OCRLemma(image_id=both.id, lemma="полиция"),
        OCRLemma(image_id=only_one.id, lemma="звонить"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "звоню в полицию")

    assert ids == {both.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_value_matches_query_lemma(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="животное", value="кот", source="rules"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "коты")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_match_returns_empty_set(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ids = await matching_image_ids(db_session, "nonexistentword")

    assert ids == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repository.ocr_lemmas'`

- [ ] **Step 3: Implement `repository/ocr_lemmas.py`**

```python
from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from Storage.models import ImageTag, OCRLemma


@lru_cache(maxsize=1)
def _get_morph():
    return make_morph()


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.
    """
    if not q:
        return None

    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
    if not lemmas:
        return None

    matching_ids: Optional[set] = None
    for lemma in lemmas:
        ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
        tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())

        result = await session.execute(union(ocr_subq, tag_subq))
        lemma_ids = {row[0] for row in result.all()}

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids


class OCRLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def delete_all(self) -> None:
        print("Deleting all ocr_lemmas rows...")
        await self.session.execute(delete(OCRLemma))
        await self.session.commit()
        print("Done")


class OCRLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        for lemma in lemmas:
            self.session.add(OCRLemma(image_id=image_id, lemma=lemma))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total images indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "feat: add shared OCR-lemma writer and query-time matching function"
```

---

### Task 6: `batch/build_ocr_lemmas.py` batch job

**Files:**
- Create: `batch/build_ocr_lemmas.py`
- Modify: `CLAUDE.md` (batch pipeline list)

**Interfaces:**
- Consumes: `ImagesRepository.get_images_and_ocr_texts_with_language()` (existing), `ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language()` (Task 4), `batch.utils.ocr_lemmas.group_lemmas_by_image` (Task 3), `repository.ocr_lemmas.OCRLemmasRepository`/`OCRLemmasSaver` (Task 5).
- Produces: a runnable script, `python -m batch.build_ocr_lemmas [--env {metal,general,it}] [--incremental]`.

No dedicated test for this file's `main()` — same convention as `build_tags_from_ocr.py`, which has no test file either; its logic is entirely delegated to the already-tested `group_lemmas_by_image` (Task 3) and repository methods (Tasks 4/5). It's verified by the manual rollout step in Task 9.

- [ ] **Step 1: Implement the script**

Create `batch/build_ocr_lemmas.py`:

```python
import argparse
import asyncio

from batch.utils.ocr_lemmas import group_lemmas_by_image
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver


async def main(incremental: bool):
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        lemmas_repo = OCRLemmasRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await lemmas_repo.delete_all()

        print(f"Mode: {'incremental' if incremental else 'full'}")
        print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}, OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
        print(f"BOW_MIN_WORD_LENGTH={min_word_length}")

        if incremental:
            rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
        else:
            rows = await images_repo.get_images_and_ocr_texts_with_language()

        simplified_rows = [
            (image_id, text, confidence, language, lang_score)
            for _filename, image_id, text, confidence, language, lang_score in rows
        ]

        lemmas_by_image, stats = group_lemmas_by_image(
            simplified_rows, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length
        )
        metrics.add("ocr_rows.total", stats["rows_total"])
        metrics.add("ocr_rows.skipped", stats["rows_skipped"])
        metrics.add("ocr_rows.processed", stats["rows_processed"])

        print(f"Total images: {len(lemmas_by_image)}")
        tracker = ProgressTracker(len(lemmas_by_image), report_every=100, report_interval_secs=10)

        async with OCRLemmasSaver(session) as saver:
            for image_id, lemma_set in lemmas_by_image.items():
                saver.add_lemmas(image_id, lemma_set)
                metrics.add("lemmas.total", len(lemma_set))
                metrics.bucket("lemmas_per_image", len(lemma_set))
                tracker.mark_done()

        tracker.summary()

    print("Lemmas:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no ocr_lemmas rows yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))
```

- [ ] **Step 2: Smoke-test it against your local dev DB**

```powershell
python -m batch.build_ocr_lemmas --env metal
```

Expected: prints mode/thresholds, a total image count, progress lines, and a final metrics summary with `ocr_rows.*` and `lemmas.*` entries; exits 0.

- [ ] **Step 3: Update the batch pipeline list in CLAUDE.md**

In `CLAUDE.md`, in the "Batch pipeline (execution order)" code block, change:

```
build_tags_from_ocr        → rule-based tags from OCR text
```

to:

```
build_tags_from_ocr        → rule-based tags from OCR text
build_ocr_lemmas           → per-image lemma index for smart search (see
                              docs/superpowers/specs/2026-07-21-smart-search-design.md);
                              --incremental skips images already indexed
```

- [ ] **Step 4: Commit**

```bash
git add batch/build_ocr_lemmas.py CLAUDE.md
git commit -m "feat: add build_ocr_lemmas batch job to the pipeline"
```

---

### Task 7: Wire `/api/images` onto the shared matching function

**Files:**
- Modify: `Backend/app/repositories/image_repository.py:23-54` (`_build_filtered_ids_query`)
- Modify: `tests/integration/test_backend_image_repository.py`

**Interfaces:**
- Consumes: `repository.ocr_lemmas.matching_image_ids(session, q)` (Task 5).

- [ ] **Step 1: Update the existing integration test to use the new index**

In `tests/integration/test_backend_image_repository.py`, replace `test_search_filters_by_text` (it currently seeds `OCRText` directly, which the matching function no longer reads):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_search_filters_by_text(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="boyfriend"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, _ = await repo.search(q="boyfriend", tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    ids = {r.id for r in rows}
    assert matching.id in ids
    assert other.id not in ids
```

Add `OCRLemma` to that file's import (line 17-27 `from Storage.models import (...)`). Add one more test right after it for the cross-line/lemma-matching behavior this whole feature exists for:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_search_matches_lemma_regardless_of_query_word_form(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(matching)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=matching.id, lemma="полиция"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, _ = await repo.search(q="полицию", tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    assert matching.id in {r.id for r in rows}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v -k search_filters_by_text or search_matches_lemma`
Expected: FAIL — the substring query doesn't see `OCRLemma` rows at all, so `matching.id` is absent from results (test_search_filters_by_text) and the case-mismatched query finds nothing (test_search_matches_lemma_regardless_of_query_word_form).

- [ ] **Step 3: Update `_build_filtered_ids_query`**

In `Backend/app/repositories/image_repository.py`, replace the method body:

```python
    async def _build_filtered_ids_query(
        self,
        q: Optional[str],
        tags: dict[str, set],
    ):
        """Returns a scalar-subquery of image IDs matching q and tags, unpaginated."""
        img = aliased(Image)
        image_tag = aliased(ImageTag)

        query = select(img.id)

        matching_ids = await matching_image_ids(self.session, q)
        if matching_ids is not None:
            query = query.where(img.id.in_(matching_ids))

        if tags:
            tag_queries = [
                select(distinct(image_tag.image_id)).where(
                    and_(image_tag.key == key, image_tag.value.in_(values))
                )
                for key, values in tags.items()
            ]
            tags_result = await self.session.execute(union_all(*tag_queries))
            query = query.where(img.id.in_([id for (id,) in tags_result.all()]))

        return query
```

Add the import at the top of the file (alongside the other `from ... import ...` lines): `from repository.ocr_lemmas import matching_image_ids`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v`
Expected: PASS (all tests in the file — confirms no regression in the other `search`/tag/pagination tests)

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/image_repository.py tests/integration/test_backend_image_repository.py
git commit -m "feat: switch /api/images text search to shared OCR-lemma matching"
```

---

### Task 8: Wire `/api/recommendations` onto the shared matching function

**Files:**
- Modify: `Backend/app/repositories/recommendations_repository.py`
- Modify: `Backend/app/services/recommendations_service.py`
- Modify: `tests/integration/test_backend_recommendations_repository.py`
- Modify: `Backend/tests/test_recommendations_endpoints.py`

**Interfaces:**
- Consumes: `repository.ocr_lemmas.matching_image_ids(session, q)` (Task 5).
- Changes: `RecommendationsRepository.get_recommendations(q: Optional[str], seed, last_hash, limit)` — parameter renamed from `words: list[str]` to `q: Optional[str]`. `RecommendationsService.get_recommendations` passes `q` straight through; `_parse_query` is removed (its job now lives inside `matching_image_ids`).

- [ ] **Step 1: Update the repository integration tests**

In `tests/integration/test_backend_recommendations_repository.py`:

- Change every `await repo.get_recommendations(words=[...], ...)` call to `await repo.get_recommendations(q=..., ...)`, e.g. `words=[]` → `q=None`, `words=["cat"]` → `q="cat"`, `words=["cat", "dog"]` → `q="cat dog"`.
- Replace OCR-based fixture setup with `OCRLemma` rows (matching function no longer reads `OCRText` directly), and **delete** `test_words_filter_ignores_low_confidence_ocr` — confidence filtering happened in this repository before; now it's exclusively `build_ocr_lemmas.py`'s responsibility (already covered by Task 3's `test_low_confidence_row_skipped`), so there's nothing left for this repository to test there.

Resulting file (replace the whole file with this — it's a small file and the diff is easier to read as a full replacement):

```python
"""
Integration tests for Backend/app/repositories/recommendations_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

RecommendationsRepository.get_recommendations previously referenced a
column, ImageExtras.exclude, that didn't exist after the excluded -> flagged
rename (commit 4b9bca5 missed this file), breaking every
/api/recommendations request with an AttributeError. Router-level tests
mock the service and never execute the real query, so nothing caught it —
these tests exercise the query against a real schema.
"""
import hashlib
import uuid

import pytest

from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Storage.models import Image, ImageExtras, ImageTag, OCRLemma

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


def _md5_hash(image_id, seed: int) -> str:
    return hashlib.md5(f"{image_id}{seed}".encode("utf-8")).hexdigest()


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_flagged_images(db_session):
    kept = Image(filename=f"{uuid.uuid4()}.jpg")
    flagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([kept, flagged])
    await db_session.flush()
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert kept.id in ids
    assert flagged.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_includes_image_with_no_extras_row(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    matches = [r for r in rows if r.id == image.id]
    assert len(matches) == 1
    assert matches[0].flagged is None


@pytest.mark.asyncio(loop_scope="session")
async def test_includes_explicitly_unflagged_image(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageExtras(image_id=image.id, flagged=False))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    matches = [r for r in rows if r.id == image.id]
    assert len(matches) == 1
    assert matches[0].flagged is False


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_matches_ocr_lemma_index(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="cat"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat", seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert matching.id in ids
    assert other.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_matches_tag(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="animal", value="cat", source="rules"))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat", seed=1, last_hash=None, limit=50)

    assert image.id in {r.id for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_requires_all_words(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_cat = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_cat])
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=both.id, key="a", value="cat", source="rules"),
        ImageTag(image_id=both.id, key="b", value="dog", source="rules"),
        ImageTag(image_id=only_cat.id, key="a", value="cat", source="rules"),
    ])
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat dog", seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert both.id in ids
    assert only_cat.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_no_matches_returns_empty_list(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="nonexistentword", seed=1, last_hash=None, limit=50)

    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_last_hash_pagination_excludes_seen_and_earlier(db_session):
    seed = 777
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(4)]
    db_session.add_all(images)
    await db_session.flush()

    ordered = sorted(images, key=lambda img: _md5_hash(img.id, seed))

    repo = RecommendationsRepository(db_session)
    cutoff = _md5_hash(ordered[1].id, seed)
    rows = await repo.get_recommendations(q=None, seed=seed, last_hash=cutoff, limit=50)

    returned_ids = [r.id for r in rows if r.id in {img.id for img in images}]
    expected_ids = [img.id for img in ordered[2:]]
    assert returned_ids == expected_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_returns_at_most_limit_plus_one(db_session):
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(5)]
    db_session.add_all(images)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=3, last_hash=None, limit=2)

    assert len(rows) <= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_recommendations_repository.py -v`
Expected: FAIL — `TypeError: get_recommendations() got an unexpected keyword argument 'q'`

- [ ] **Step 3: Update `RecommendationsRepository`**

Replace `Backend/app/repositories/recommendations_repository.py` in full:

```python
from typing import Optional

from sqlalchemy import select, func, cast, String, literal, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from repository.ocr_lemmas import matching_image_ids
from Storage.models import Image, ImageExtras


class RecommendationsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_recommendations(
        self,
        q: Optional[str],
        seed: int,
        last_hash: Optional[str],
        limit: int,
    ) -> list:
        img = aliased(Image)
        extras = aliased(ImageExtras)

        hash_expr = func.md5(func.concat(cast(img.id, String), literal(str(seed))))

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(or_(extras.flagged.is_(None), extras.flagged == False))
        )

        matching_ids = await matching_image_ids(self.session, q)
        if matching_ids is not None:
            if not matching_ids:
                return []
            query = query.where(img.id.in_(matching_ids))

        if last_hash is not None:
            query = query.where(hash_expr > last_hash)

        query = query.order_by(hash_expr.asc()).limit(limit + 1)

        result = await self.session.execute(query)
        return result.all()
```

- [ ] **Step 4: Update `RecommendationsService`**

In `Backend/app/services/recommendations_service.py`, change `get_recommendations` to pass `q` straight through, and delete `_parse_query`:

```python
    async def get_recommendations(
        self,
        q: Optional[str],
        seed: int,
        last_hash: Optional[str],
        limit: int,
    ) -> MemeSearchResponse:
        rows = await self.repo.get_recommendations(q=q, seed=seed, last_hash=last_hash, limit=limit)
```

(Delete the `words = self._parse_query(q)` line, and delete the `_parse_query` static method entirely — its splitting/lemmatizing responsibility now lives inside `matching_image_ids`.)

- [ ] **Step 5: Remove the now-dead service unit tests**

In `Backend/tests/test_recommendations_endpoints.py`, delete the entire `class TestParseQuery:` block (it tests a method that no longer exists).

- [ ] **Step 6: Run all recommendations tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_recommendations_repository.py -v`
Expected: PASS

Run: `pytest Backend/tests/test_recommendations_endpoints.py -v`
Expected: PASS (router/service-mock tests — unaffected by the repository change since they mock `RecommendationsService` entirely)

- [ ] **Step 7: Commit**

```bash
git add Backend/app/repositories/recommendations_repository.py Backend/app/services/recommendations_service.py tests/integration/test_backend_recommendations_repository.py Backend/tests/test_recommendations_endpoints.py
git commit -m "feat: switch /api/recommendations text search to shared OCR-lemma matching"
```

---

### Task 9: Documentation, rollout, and final smoke test

**Files:**
- Modify: `backend_api.md`

- [ ] **Step 1: Update `backend_api.md`'s `/api/recommendations` `q` row**

Replace the `q` row in the Recommendations query-parameters table (currently: `Split on whitespace; all words must match (AND). Each word is matched case-insensitively against the combined OCR text (confidence > 0.8, all blocks joined with a space) **or** any tag value. Empty string is treated as no query.`) with:

```
| `q`       | string | —                    | Optional search query. Tokenized and lemmatized per word (Russian words normalized to dictionary form via pymorphy3; other languages lowercased; pure-digit tokens like years are kept as-is). All resulting lemmas must match (AND), each against either the image's precomputed OCR-lemma index (`ocr_lemmas`, built offline by `batch/build_ocr_lemmas.py`) or some tag value (case-insensitive equality). Matching is per-image, not per-OCR-line, so a multi-word query matches even when its words came from different OCR-detected lines on the same meme. Empty string is treated as no query. |
```

- [ ] **Step 2: Update `backend_api.md`'s `/api/images` search section**

In the "Search Images" section, change the `q` bullet from `Search query string` to:

```
  - `q` (optional): Search query string — same tokenize/lemmatize/AND matching as `/api/recommendations`'s `q` (see below); both endpoints share one matching implementation.
```

- [ ] **Step 3: Commit the doc update**

```bash
git add backend_api.md
git commit -m "doc: update backend_api.md for lemma-based search matching"
```

- [ ] **Step 4: Full test sweep (per CLAUDE.md's separate-roots rule — run each command separately)**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: all four pass.

- [ ] **Step 5: Rollout — backfill the lemma index on each running local environment**

This is a required manual step, not optional follow-up (see spec's Rollout section) — `ocr_lemmas` starts empty, so search returns nothing until this runs:

```powershell
python -m batch.build_ocr_lemmas --env metal
python -m batch.build_ocr_lemmas --env general
python -m batch.build_ocr_lemmas --env it
```

- [ ] **Step 6: Manual verification against a real running backend**

Start one environment's backend (e.g. metal, per CLAUDE.md's uvicorn command), then confirm the two motivating cases from the spec now work:

```powershell
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
```

In another terminal:
```powershell
curl "http://localhost:8081/api/diagnostics/health"
curl "http://localhost:8081/api/images?limit=1"
curl "http://localhost:8081/api/images?q=<a Russian query with a declined word form you know exists in your metal corpus>"
curl "http://localhost:8081/api/recommendations?q=<the same query>"
```

Expected: health and `limit=1` smoke checks return 200 (per CLAUDE.md's "Before committing backend changes"); the query returns images whose OCR text contains a different grammatical form of the query word, and both endpoints return consistent results for the same `q`.
