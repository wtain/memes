# Descriptions and Notes in Search, Similarity and Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ollama descriptions searchable, add a combined description+note similarity mode, tag human notes, and refresh a note's lemmas inline when it is edited.

**Architecture:** A new `description_lemmas` table (keyed by description, so rejected feedback is excluded at query time) feeds a fourth source in the smart-search union. A new `source=description_all` similarity mode takes the minimum cosine distance across all non-rejected description vectors plus the note vector. A new `build_tags_from_notes` job (source `"Note"`) mirrors `build_tags_from_descriptions`. `PUT` on a note rewrites that note's lemmas in-request using one shared normalization function.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async + pgvector, FastAPI, Alembic, pytest (`pytest-asyncio`), PostgreSQL test DB `ocrdb_test`.

**Spec:** `docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md` (read it first; this plan implements all of it).

## Global Constraints

- Python 3.11 via `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe` (from the repo root or a worktree root). On Windows set `PYTHONIOENCODING=utf-8` before running any batch script or pytest that imports `ProgressTracker`.
- Never combine test roots in one `pytest` invocation: run `tests/integration/`, `Backend/tests/` (from `Backend/`: `cd Backend && pytest`), `batch/tests/`, and `tests/rules/` as four separate commands.
- `tests/integration/` needs `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test` set explicitly. The schema is created from `Base.metadata`, so a model change is enough for those tests.
- Repositories never call `session.commit()`. Savers used by batch jobs commit in `__aexit__` (existing pattern).
- Never bind or restart the metal/general/IT backends/frontends, and never run batch jobs against their databases. A subagent must never receive `DATABASE_URL`; only the controller may run a live-DB read, with `DATABASE_URL_READONLY`.
- "Rejected description" = a row in `image_description_feedback` with `approved = FALSE`. No feedback row means included.
- Tag sources: `"Note"` for note tags (new); `"Ollama"` is untouched.
- Notes have no language tag: lemma index and tagging use `language=None`. Ollama descriptions are indexed with `language="en"`.
- Add a new endpoint value/behavior ⇒ update `backend_api.md` in the same change. Batch script added ⇒ update the CLAUDE.md batch list in the same change.
- Commit messages end with the two attribution lines from the session's system reminder (`Co-Authored-By: ...` and `Claude-Session: ...`).
- Work in a git worktree (parallel sessions exist). Tasks run sequentially in one worktree: Tasks 4, 5 and 6 all edit `Backend/app/repositories/image_repository.py`, and Tasks 2 and 5 both edit `environments/batch_registry.yaml`.

## File Structure

| File | Responsibility |
|---|---|
| `Storage/models.py` (modify) | `DescriptionLemma` model |
| `Storage/alembic/versions/<new>_add_description_lemmas_table.py` (create) | migration, hand-written (autogenerate needs a live DB) |
| `repository/image_descriptions.py` (modify) | `description_not_rejected()` shared predicate |
| `repository/description_lemmas.py` (create) | `DescriptionLemmasRepository`, `DescriptionLemmasSaver` |
| `batch/build_description_lemmas.py` (create) | indexes Ollama descriptions |
| `repository/ocr_lemmas.py` (modify) | description source in exact/fuzzy/stem matching |
| `Backend/app/repositories/image_repository.py` (modify) | combined similarity query, note clear cleanup, inline note lemmas |
| `Backend/app/services/image_service.py`, `Backend/app/api/images.py` (modify) | `description_all` source |
| `repository/tags.py` (modify) | `NOTE_TAG_SOURCE` constant |
| `repository/images.py` (modify) | `get_notes_needing_tags` |
| `batch/build_tags_from_notes.py` (create) | note tagging job |
| `repository/description_note_lemmas.py` (modify) | shared `compute_note_lemmas` / `note_lemma_set` |
| `batch/build_description_note_lemmas.py` (modify) | use the shared function |
| `environments/batch_registry.yaml`, `backend_api.md`, `CLAUDE.md`, `docs/data-flow.md`, `ARCHITECTURE.md` (modify) | registration + docs |

---

### Task 1: `DescriptionLemma` model, migration and the "not rejected" predicate

**Files:**
- Modify: `Storage/models.py` (append after `DescriptionNoteLemma`, ~line 540)
- Create: `Storage/alembic/versions/c4d8e1f2a5b7_add_description_lemmas_table.py`
- Modify: `repository/image_descriptions.py`
- Test: `tests/integration/test_description_not_rejected.py`

**Interfaces:**
- Produces: `Storage.models.DescriptionLemma` (columns `image_description_id`, `lemma`, `phonetic_code`, `created_at`); `repository.image_descriptions.description_not_rejected(description_id_col) -> ColumnElement[bool]` — a correlated `NOT EXISTS` predicate that is true when the description has no feedback row with `approved = FALSE`. Pass the description id column of whatever (possibly aliased) `ImageDescription` the outer query uses.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_description_not_rejected.py`:

```python
"""
Integration tests for repository.image_descriptions.description_not_rejected -- the single
definition of "rejected description" (feedback row with approved = FALSE) shared by search,
similarity and, later, tagging (task 149).

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_descriptions import description_not_rejected
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback


async def _description(db_session, prompt_key, feedback):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
        await db_session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_predicate_keeps_unreviewed_and_approved_and_drops_rejected(db_session):
    unreviewed = await _description(db_session, "a", None)
    approved = await _description(db_session, "b", True)
    rejected = await _description(db_session, "c", False)

    ids = set((await db_session.execute(
        select(ImageDescription.id).where(
            ImageDescription.id.in_([unreviewed.id, approved.id, rejected.id]),
            description_not_rejected(ImageDescription.id),
        )
    )).scalars().all())

    assert ids == {unreviewed.id, approved.id}
```

- [ ] **Step 2: Run to verify it fails**

Run: `$env:DATABASE_URL='postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test'; .venv311\Scripts\python.exe -m pytest tests/integration/test_description_not_rejected.py -q` (use the main checkout's `.venv311` path from a worktree: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`)
Expected: FAIL with `ImportError: cannot import name 'description_not_rejected'`.

- [ ] **Step 3: Implement predicate, model and migration**

In `repository/image_descriptions.py` change the imports and add the function above the class:

```python
import uuid

from sqlalchemy import delete, exists, select

from Storage.models import ImageDescription, ImageDescriptionFeedback


def description_not_rejected(description_id_col):
    """True when the description has no feedback row with approved = FALSE.

    The one definition of "rejected" (no feedback row = unreviewed = included). Takes the
    description id column so it works against an aliased ImageDescription too; the EXISTS
    correlates to whichever outer query contains that column.
    """
    return ~exists().where(
        ImageDescriptionFeedback.image_description_id == description_id_col,
        ImageDescriptionFeedback.approved.is_(False),
    )
```

Append to `Storage/models.py` after the `DescriptionNoteLemma` class:

```python
class DescriptionLemma(Base):
    __tablename__ = "description_lemmas"

    # Keyed by description (not image) so a rejected description's lemmas can be excluded at
    # query time via description_not_rejected(), with no batch rerun on feedback changes.
    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"), primary_key=True,
    )
    lemma = Column(String, primary_key=True)
    # Populated for schema symmetry with OCRLemma; never queried (descriptions are English
    # LLM output, the phonetic erratives fallback does not apply).
    phonetic_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_description_lemmas_lemma", "lemma"),
        Index(
            "ix_description_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )
```

Run `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m alembic heads` from `Storage/` and use the printed head as `down_revision` (it was `b7e2c4a91d30` when this plan was written; another session may have added one). Create `Storage/alembic/versions/c4d8e1f2a5b7_add_description_lemmas_table.py`:

```python
"""add description_lemmas table

Revision ID: c4d8e1f2a5b7
Revises: b7e2c4a91d30
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d8e1f2a5b7'
down_revision: Union[str, Sequence[str], None] = 'b7e2c4a91d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'description_lemmas',
        sa.Column('image_description_id', sa.UUID(), nullable=False),
        sa.Column('lemma', sa.String(), nullable=False),
        sa.Column('phonetic_code', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_description_id'], ['image_descriptions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_description_id', 'lemma'),
    )
    op.create_index('ix_description_lemmas_lemma', 'description_lemmas', ['lemma'], unique=False)
    # pg_trgm extension already created by 6fc209b37e8b_add_ocr_lemmas_trigram_index.py
    op.create_index(
        'ix_description_lemmas_lemma_trgm', 'description_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'},
    )


def downgrade() -> None:
    op.drop_table('description_lemmas')
```

- [ ] **Step 4: Run test and check the migration chain**

Run: the same pytest command. Expected: PASS.
Run (from `Storage/`): `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m alembic heads`. Expected: exactly one head, `c4d8e1f2a5b7`.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/c4d8e1f2a5b7_add_description_lemmas_table.py repository/image_descriptions.py tests/integration/test_description_not_rejected.py
git commit -m "feat: description_lemmas table and shared description_not_rejected predicate"
```

---

### Task 2: `build_description_lemmas` repository, saver and batch job

**Files:**
- Create: `repository/description_lemmas.py`
- Create: `batch/build_description_lemmas.py`
- Modify: `environments/batch_registry.yaml`
- Test: `tests/integration/test_description_lemmas_repository.py`, `tests/integration/test_build_description_lemmas.py`, `batch/tests/test_build_description_lemmas_main.py`

**Interfaces:**
- Consumes: `Storage.models.DescriptionLemma`, `ImageDescription` (Task 1).
- Produces:
  - `DescriptionLemmasRepository(session).get_descriptions_needing_lemmas() -> list[Row(id, text)]` — descriptions with no `description_lemmas` rows.
  - `DescriptionLemmasSaver(session)` — async context manager; `await saver.add_lemmas(description_id, lemmas: set[str])` (idempotent insert); commits on exit.
  - `batch.build_description_lemmas.run(session, morph, min_word_length) -> None` and `main(trigger="manual", run_id=None) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_description_lemmas_repository.py`:

```python
"""Integration tests for repository/description_lemmas.py. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from repository.description_lemmas import DescriptionLemmasRepository, DescriptionLemmasSaver
from Storage.models import DescriptionLemma, Image, ImageDescription


async def _description(db_session, text="a cat on a sofa", prompt_key="p"):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text=text)
    db_session.add(description)
    await db_session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_description_without_lemmas_is_selected_and_with_lemmas_is_not(db_session):
    fresh = await _description(db_session)
    indexed = await _description(db_session)
    db_session.add(DescriptionLemma(image_description_id=indexed.id, lemma="cat"))
    await db_session.flush()

    rows = await DescriptionLemmasRepository(db_session).get_descriptions_needing_lemmas()
    ids = {row.id for row in rows}

    assert fresh.id in ids
    assert indexed.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_add_lemmas_is_idempotent(db_session):
    description = await _description(db_session)

    async with DescriptionLemmasSaver(db_session) as saver:
        await saver.add_lemmas(description.id, {"cat", "sofa"})
        await saver.add_lemmas(description.id, {"cat", "sofa"})

    lemmas = set((await db_session.execute(
        select(DescriptionLemma.lemma).where(DescriptionLemma.image_description_id == description.id)
    )).scalars().all())
    assert lemmas == {"cat", "sofa"}
```

`tests/integration/test_build_description_lemmas.py`:

```python
"""Integration test for batch/build_description_lemmas.py's run(): descriptions are indexed as
English (stemmed), already-indexed descriptions are skipped. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from batch.build_description_lemmas import run
from rules.normalize import make_morph
from Storage.models import DescriptionLemma, Image, ImageDescription

_MORPH = make_morph()


async def _description(db_session, text):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text=text)
    db_session.add(description)
    await db_session.flush()
    return description


async def _lemmas(db_session, description):
    return set((await db_session.execute(
        select(DescriptionLemma.lemma).where(DescriptionLemma.image_description_id == description.id)
    )).scalars().all())


@pytest.mark.asyncio(loop_scope="session")
async def test_run_indexes_english_description_with_stemming(db_session):
    description = await _description(db_session, "Two cats sitting on sofas")

    await run(db_session, morph=_MORPH, min_word_length=3)

    lemmas = await _lemmas(db_session, description)
    assert "cat" in lemmas      # "cats" stemmed, so a query for "cat" matches exactly
    assert "sofa" in lemmas
    assert "cats" not in lemmas


@pytest.mark.asyncio(loop_scope="session")
async def test_run_skips_descriptions_that_already_have_lemmas(db_session):
    description = await _description(db_session, "a dog")
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma="sentinel"))
    await db_session.flush()

    await run(db_session, morph=_MORPH, min_word_length=3)

    assert await _lemmas(db_session, description) == {"sentinel"}
```

`batch/tests/test_build_description_lemmas_main.py`:

```python
"""Unit tests for batch/build_description_lemmas.py's main() self-tracking contract. No real DB."""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_description_lemmas import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path(self):
        process_mock = AsyncMock()
        import batch.build_description_lemmas as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="build_description_lemmas", trigger="manual")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_description_lemmas as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/integration/test_description_lemmas_repository.py tests/integration/test_build_description_lemmas.py -q` (with `DATABASE_URL`) and `pytest batch/tests/test_build_description_lemmas_main.py -q` separately.
Expected: FAIL with `ModuleNotFoundError` for `repository.description_lemmas` / `batch.build_description_lemmas`.

- [ ] **Step 3: Implement**

`repository/description_lemmas.py`:

```python
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionLemma, ImageDescription


class DescriptionLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_descriptions_needing_lemmas(self):
        """Descriptions with no lemma rows yet. Descriptions are insert-only (re-describing
        deletes and re-inserts), so "no rows" is the whole staleness test. A description that
        normalizes to zero lemmas is re-selected on every run; harmless and cheap."""
        result = await self.session.execute(
            select(ImageDescription.id, ImageDescription.text)
            .where(~exists().where(DescriptionLemma.image_description_id == ImageDescription.id))
        )
        return result.all()


class DescriptionLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.description_count = 0

    async def add_lemmas(self, description_id, lemmas: set) -> None:
        self.description_count += 1
        if not lemmas:
            return
        stmt = (
            insert(DescriptionLemma)
            .values([
                {
                    "image_description_id": description_id,
                    "lemma": lemma,
                    "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
                }
                for lemma in lemmas
            ])
            .on_conflict_do_nothing(index_elements=["image_description_id", "lemma"])
        )
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total descriptions indexed: {self.description_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
```

`batch/build_description_lemmas.py`:

```python
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_lemmas import DescriptionLemmasRepository, DescriptionLemmasSaver
from rules.normalize import make_morph, normalize
from Storage.db import AsyncSessionLocal

# Ollama descriptions are English LLM output regardless of the meme's own language, so they are
# indexed as "en": words are stemmed at index time exactly like en-tagged OCR rows, which is what
# lets repository/ocr_lemmas.py's _stem_lemma_ids fallback find them.
_DESCRIPTION_LANGUAGE = "en"


async def run(session, morph, min_word_length: int) -> None:
    repo = DescriptionLemmasRepository(session)
    rows = await repo.get_descriptions_needing_lemmas()
    print(f"Found {len(rows)} description(s) needing lemma indexing")

    tracker = ProgressTracker(len(rows), report_every=100, report_interval_secs=10)

    async with DescriptionLemmasSaver(session) as saver:
        for description_id, text in rows:
            lemma_set = normalize(
                text, morph, min_length=min_word_length,
                language=_DESCRIPTION_LANGUAGE, keep_digit_tokens=True,
            )
            await saver.add_lemmas(description_id, lemma_set)
            tracker.mark_done()

    tracker.summary()


async def _process() -> None:
    morph = make_morph()
    min_word_length = settings.BOW.MIN_WORD_LENGTH
    async with AsyncSessionLocal() as session:
        await run(session, morph, min_word_length)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_description_lemmas", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

In `environments/batch_registry.yaml`, add directly after the `build_description_note_embeddings:` entry:

```yaml
build_description_lemmas:
  module: batch.build_description_lemmas
  kind: build_description_lemmas
```

- [ ] **Step 4: Run tests to verify they pass**

Run the two integration files and `pytest batch/tests/test_build_description_lemmas_main.py batch/tests/test_registry.py -q` (separately, as above). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repository/description_lemmas.py batch/build_description_lemmas.py environments/batch_registry.yaml tests/integration/test_description_lemmas_repository.py tests/integration/test_build_description_lemmas.py batch/tests/test_build_description_lemmas_main.py
git commit -m "feat: build_description_lemmas job indexing Ollama descriptions"
```

---

### Task 3: Description lemmas in smart search

**Files:**
- Modify: `repository/ocr_lemmas.py:5-13` (imports), `:21-26` (`_exact_lemma_ids`), `:60-64` (`_fuzzy_lemma_ids`), `:92-125` (`_stem_lemma_ids`)
- Test: `tests/integration/test_description_lemma_search_matching.py`

**Interfaces:**
- Consumes: `DescriptionLemma`, `ImageDescription`, `ImageDescriptionFeedback` models; `description_not_rejected` (Task 1).
- Produces: `matching_image_ids` additionally matches an image whose non-rejected Ollama description has a matching lemma (exact, trigram fuzzy, English-stem fallback). No signature change.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_description_lemma_search_matching.py`:

```python
"""
Integration tests: Ollama description lemmas are a fourth source in smart search, with rejected
descriptions excluded at query time. Requires a live PostgreSQL instance.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids
from Storage.models import (
    DescriptionLemma, Image, ImageDescription, ImageDescriptionFeedback, OCRLemma,
)


async def _image_with_description_lemma(db_session, lemma, feedback=None, prompt_key="p"):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma=lemma))
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
    await db_session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_a_description_is_found_by_exact_match(db_session):
    image = await _image_with_description_lemma(db_session, "pineapple")

    assert await matching_image_ids(db_session, "pineapple") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_approved_description_still_matches(db_session):
    image = await _image_with_description_lemma(db_session, "pineapple", feedback=True)

    assert await matching_image_ids(db_session, "pineapple") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_does_not_match(db_session):
    await _image_with_description_lemma(db_session, "pineapple", feedback=False)

    assert await matching_image_ids(db_session, "pineapple") == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_matches_via_trigram_fuzzy_fallback(db_session):
    image = await _image_with_description_lemma(db_session, "pineapples")

    # "pineapple" has no exact row, so the fuzzy fallback (length >= FUZZY_MIN_LEMMA_LENGTH) runs.
    assert image.id in await matching_image_ids(db_session, "pineappel")


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_matches_via_english_stem_fallback(db_session):
    image = await _image_with_description_lemma(db_session, "sofa")

    # "sofas" has no exact row; its English stem "sofa" does.
    assert image.id in await matching_image_ids(db_session, "sofas")


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_is_excluded_from_stem_and_fuzzy_fallbacks(db_session):
    rejected_stem = await _image_with_description_lemma(db_session, "sofa", feedback=False)
    rejected_fuzzy = await _image_with_description_lemma(db_session, "pineapples", feedback=False)

    stem_ids = await matching_image_ids(db_session, "sofas")
    fuzzy_ids = await matching_image_ids(db_session, "pineappel")

    assert rejected_stem.id not in stem_ids
    assert rejected_fuzzy.id not in fuzzy_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_token_query_matches_across_ocr_and_description_sources(db_session):
    """AND across tokens, OR across sources within a token."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=image.id, lemma="pineapple"),
        DescriptionLemma(image_description_id=description.id, lemma="sofa"),
    ])
    await db_session.flush()

    assert await matching_image_ids(db_session, "pineapple sofa") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_does_not_match_a_different_word(db_session):
    await _image_with_description_lemma(db_session, "pineapple")

    assert await matching_image_ids(db_session, "zebra") == set()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/test_description_lemma_search_matching.py -q` (with `DATABASE_URL`).
Expected: FAIL (the matching tests return `set()` instead of the image).

- [ ] **Step 3: Implement in `repository/ocr_lemmas.py`**

Update the model import line and add the predicate import:

```python
from repository.image_descriptions import description_not_rejected
from Storage.models import DescriptionLemma, DescriptionNoteLemma, ImageDescription, ImageTag, OCRLemma
```

Add a helper under `_get_morph` and use it in the three functions:

```python
def _description_image_ids(*lemma_filters):
    """Image ids of non-rejected Ollama descriptions whose lemma rows satisfy the filters."""
    return (
        select(ImageDescription.image_id)
        .join(DescriptionLemma, DescriptionLemma.image_description_id == ImageDescription.id)
        .where(*lemma_filters, description_not_rejected(ImageDescription.id))
    )
```

`_exact_lemma_ids`: after `note_subq` add

```python
    description_subq = _description_image_ids(DescriptionLemma.lemma == lemma)
    result = await session.execute(union(ocr_subq, tag_subq, note_subq, description_subq))
```
(replacing the existing `union(ocr_subq, tag_subq, note_subq)` call).

`_fuzzy_lemma_ids`: after `note_subq` add

```python
    description_subq = _description_image_ids(DescriptionLemma.lemma.op("%")(lemma))
    result = await session.execute(union(ocr_subq, tag_subq, note_subq, description_subq))
```
and update its docstring's index list to mention `ix_description_lemmas_lemma_trgm`.

`_stem_lemma_ids`: replace the body and update the docstring paragraph that says notes are excluded by appending: "Description lemmas ARE included: build_description_lemmas.py indexes Ollama descriptions with language=\"en\", so they are pre-stemmed like en OCR rows."

```python
    stem = stem_english_word(lemma)
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == stem)
    description_subq = _description_image_ids(DescriptionLemma.lemma == stem)
    result = await session.execute(union(ocr_subq, description_subq))
    return {row[0] for row in result.all()}
```

Also update `matching_image_ids`'s docstring ("OCR-lemma index, description-note-lemma index, or tags") to add "Ollama-description-lemma index (rejected descriptions excluded)".

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_description_lemma_search_matching.py -q`, then the **entire** `pytest tests/integration/ -q` (shared search code changed; per CLAUDE.md the whole root is required).
Expected: all PASS. If `test_description_lemma_matches_via_trigram_fuzzy_fallback` fails on similarity score, pick a closer misspelling pair (the threshold is `settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD`) rather than weakening the assertion.

- [ ] **Step 5: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_description_lemma_search_matching.py
git commit -m "feat: Ollama description lemmas as a smart-search source, rejected excluded"
```

---

### Task 4: Combined similarity mode `source=description_all`

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (imports line 12-16; new methods after `get_similar_by_description`, ~line 188)
- Modify: `Backend/app/services/image_service.py:177-191`
- Modify: `Backend/app/api/images.py:111`
- Modify: `backend_api.md`
- Test: `tests/integration/test_backend_image_repository_description_all.py`, `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`

**Interfaces:**
- Consumes: `description_not_rejected` (Task 1).
- Produces:
  - `ImageRepository.has_text_embedding(image_id: str) -> bool` — the image has at least one non-rejected description embedding or a note embedding.
  - `ImageRepository.get_similar_by_description_all(image_id: str, limit: int = 10)` -> rows of `(image_id, distance, filename, flagged)` ordered by ascending minimum cosine distance over all (source vector, candidate vector) pairs; candidates are active images other than the source.
  - `GET /api/images/{id}/similar?source=description_all`; 404 detail `"No description or note embedding found for this image"`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_backend_image_repository_description_all.py`:

```python
"""
Integration tests for ImageRepository.get_similar_by_description_all / has_text_embedding:
min cosine distance over all non-rejected description vectors plus the note vector.
Requires a live PostgreSQL instance.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import (
    DescriptionNote, DescriptionNoteEmbedding, Image, ImageDescription,
    ImageDescriptionEmbedding, ImageDescriptionFeedback, TEXT_EMBEDDING_DIM,
)


def _vec(*head):
    v = [0.0] * TEXT_EMBEDDING_DIM
    for i, x in enumerate(head):
        v[i] = x
    return v


async def _image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    return image


async def _description_vector(db_session, image, vector, prompt_key="p", feedback=None):
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(ImageDescriptionEmbedding(image_description_id=description.id, embedding=vector))
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
    await db_session.flush()


async def _note_vector(db_session, image, vector):
    db_session.add(DescriptionNote(image_id=image.id, text="note"))
    await db_session.flush()
    db_session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=vector))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_note_of_source_matches_ollama_description_of_candidate(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _note_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, candidate, _vec(1.0, 0.0))

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    by_id = {row[0]: row[1] for row in rows}
    assert by_id[candidate.id] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_distance_is_minimum_over_all_pairs(db_session):
    source = await _image(db_session)
    candidate = await _image(db_session)
    await _description_vector(db_session, source, _vec(1.0, 0.0), prompt_key="a")
    await _note_vector(db_session, candidate, _vec(0.0, 1.0))           # orthogonal: distance 1
    await _description_vector(db_session, candidate, _vec(1.0, 0.0), prompt_key="b")  # identical: 0

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    by_id = {row[0]: row[1] for row in rows}
    assert by_id[candidate.id] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_vectors_are_ignored_on_both_sides(db_session):
    source = await _image(db_session)
    near_but_rejected = await _image(db_session)
    await _description_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, near_but_rejected, _vec(1.0, 0.0), feedback=False)

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)

    assert near_but_rejected.id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_source_itself_and_non_active_candidates_and_orders_by_distance(db_session):
    source = await _image(db_session)
    close = await _image(db_session)
    far = await _image(db_session)
    pending = await _image(db_session, status="pending")
    await _description_vector(db_session, source, _vec(1.0, 0.0))
    await _description_vector(db_session, close, _vec(1.0, 0.1))
    await _description_vector(db_session, far, _vec(0.0, 1.0))
    await _description_vector(db_session, pending, _vec(1.0, 0.0))

    rows = await ImageRepository(db_session).get_similar_by_description_all(str(source.id), limit=50)
    ids = [row[0] for row in rows]

    assert source.id not in ids
    assert pending.id not in ids
    assert ids.index(close.id) < ids.index(far.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_has_text_embedding_reflects_available_vectors(db_session):
    with_note = await _image(db_session)
    with_description = await _image(db_session)
    only_rejected = await _image(db_session)
    nothing = await _image(db_session)
    await _note_vector(db_session, with_note, _vec(1.0))
    await _description_vector(db_session, with_description, _vec(1.0))
    await _description_vector(db_session, only_rejected, _vec(1.0), feedback=False)

    repo = ImageRepository(db_session)
    assert await repo.has_text_embedding(str(with_note.id)) is True
    assert await repo.has_text_embedding(str(with_description.id)) is True
    assert await repo.has_text_embedding(str(only_rejected.id)) is False
    assert await repo.has_text_embedding(str(nothing.id)) is False
```

Append to `Backend/tests/test_image_service.py` (use the module's existing `service` / `mock_repo` fixtures and class style, next to `TestGetSimilarDescriptionMode`):

```python
class TestGetSimilarDescriptionAllMode:
    async def test_raises_404_when_no_text_embedding(self, service, mock_repo):
        mock_repo.has_text_embedding.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await service.get_similar("image-1", limit=10, source="description_all")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No description or note embedding found for this image"
        mock_repo.get_similar_by_description_all.assert_not_called()

    async def test_happy_path_calls_repo_get_similar_by_description_all(self, service, mock_repo):
        mock_repo.has_text_embedding.return_value = True
        mock_repo.get_similar_by_description_all.return_value = [
            ("image-4", 0.02, "fourth.png", False),
        ]

        result = await service.get_similar("image-1", limit=7, source="description_all")

        mock_repo.get_similar_by_description_all.assert_awaited_once_with("image-1", limit=7)
        assert [item.id for item in result.items] == ["image-4"]
        assert [item.cosineDistance for item in result.items] == [0.02]
```

Append to `Backend/tests/test_images_endpoints.py`, next to `test_get_similar_images_with_description_note_source` (same fixtures):

```python
    def test_get_similar_images_with_description_all_source(self, client, mock_image_service):
        mock_image_service.get_similar.return_value = {"items": []}

        response = client.get("/api/images/123/similar?source=description_all")

        assert response.status_code == 200
        mock_image_service.get_similar.assert_called_once_with("123", limit=10, source="description_all")
```
(Match the surrounding test's exact `mock_image_service.get_similar.return_value` shape; copy it from `test_get_similar_images_with_description_note_source` at line ~542.)

- [ ] **Step 2: Run to verify they fail**

Run: integration file (with `DATABASE_URL`); then `cd Backend && pytest tests/test_image_service.py tests/test_images_endpoints.py -q`.
Expected: FAIL (`AttributeError`/422 for the unknown source).

- [ ] **Step 3: Implement**

`Backend/app/repositories/image_repository.py` — add `from repository.image_descriptions import description_not_rejected` to the imports, then add after `get_similar_by_description`:

```python
    @staticmethod
    def _text_vectors(image_id_filter=None):
        """UNION ALL of (image_id, embedding) over non-rejected Ollama description vectors and
        note vectors. Both come from bge-large-en-v1.5, so they share one space."""
        description_vectors = (
            select(
                ImageDescription.image_id.label("image_id"),
                ImageDescriptionEmbedding.embedding.label("embedding"),
            )
            .select_from(ImageDescriptionEmbedding)
            .join(ImageDescription, ImageDescription.id == ImageDescriptionEmbedding.image_description_id)
            .where(description_not_rejected(ImageDescription.id))
        )
        note_vectors = select(
            DescriptionNoteEmbedding.description_note_id.label("image_id"),
            DescriptionNoteEmbedding.embedding.label("embedding"),
        )
        if image_id_filter is not None:
            description_vectors = description_vectors.where(ImageDescription.image_id == image_id_filter)
            note_vectors = note_vectors.where(DescriptionNoteEmbedding.description_note_id == image_id_filter)
        return union_all(description_vectors, note_vectors)

    async def has_text_embedding(self, image_id: str) -> bool:
        vectors = self._text_vectors(image_id_filter=image_id).subquery()
        result = await self.session.execute(select(vectors.c.image_id).limit(1))
        return result.first() is not None

    async def get_similar_by_description_all(self, image_id: str, limit: int = 10):
        """Min cosine distance over every (source vector, candidate vector) pair, where an
        image's vectors are its non-rejected Ollama description embeddings plus its note
        embedding. Unlike get_similar_by_description, prompt_key is not required to match, so a
        note can be compared with an Ollama description."""
        source = self._text_vectors(image_id_filter=image_id).subquery("source_vectors")
        candidates = self._text_vectors().subquery("candidate_vectors")
        img, extras = aliased(Image), aliased(ImageExtras)

        distance = func.min(source.c.embedding.cosine_distance(candidates.c.embedding)).label("distance")
        result = await self.session.execute(
            select(candidates.c.image_id, distance, img.filename, extras.flagged)
            .select_from(source)
            .join(candidates, candidates.c.image_id != image_id)
            .join(img, img.id == candidates.c.image_id)
            .outerjoin(extras, extras.image_id == candidates.c.image_id)
            .where(img.status == "active")
            .group_by(candidates.c.image_id, img.filename, extras.flagged)
            .order_by("distance")
            .limit(limit)
        )
        return result.all()
```

Add `union_all` is already imported at the top of this file (`from sqlalchemy import ... union_all ...`); keep it.

`Backend/app/services/image_service.py` — in `get_similar`, add a branch before `else`:

```python
        elif source == "description_all":
            if not await self.repo.has_text_embedding(image_id):
                raise HTTPException(status_code=404, detail="No description or note embedding found for this image")
            rows = await self.repo.get_similar_by_description_all(image_id, limit=limit)
```

`Backend/app/api/images.py:111`:

```python
    source: Literal["image", "description", "description_note", "description_all"] = "image",
```

`backend_api.md` — find the `GET /api/images/{id}/similar` section (`grep -n "description_note" backend_api.md`) and add `description_all` to the `source` values with: "min cosine distance across all of the image's non-rejected Ollama description embeddings and its note embedding, versus every other active image's; no prompt_key restriction. 404 when the image has neither. `cosineDistance` is that minimum."

- [ ] **Step 4: Run tests**

Run the integration file, then `cd Backend && pytest -q`. If the SQL fails to compile (`candidates.c.embedding` lacking `cosine_distance`), wrap with `cast`/`type_coerce(…, Vector(TEXT_EMBEDDING_DIM))` in `_text_vectors` rather than changing the test.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py Backend/app/api/images.py backend_api.md tests/integration/test_backend_image_repository_description_all.py Backend/tests/test_image_service.py Backend/tests/test_images_endpoints.py
git commit -m "feat: similar?source=description_all combining descriptions and notes"
```

---

### Task 5: `build_tags_from_notes` and note-delete tag cleanup

**Files:**
- Modify: `repository/tags.py` (constant)
- Modify: `repository/images.py` (new `get_notes_needing_tags`)
- Modify: `Backend/app/repositories/image_repository.py:264-277` (`clear_description_note`)
- Create: `batch/build_tags_from_notes.py`
- Modify: `environments/batch_registry.yaml`
- Test: `tests/integration/test_images_repository_note_tagging.py`, `tests/integration/test_description_notes_repository.py` (add a test), `batch/tests/test_build_tags_from_notes.py`

**Interfaces:**
- Consumes: `TagsRepository.delete_tags_for_images(source, image_ids)` and `TagsSaver` (already in `repository/tags.py`); `ConceptTagger`.
- Produces: `repository.tags.NOTE_TAG_SOURCE = "Note"`; `ImagesRepository(session).get_notes_needing_tags(source: str, status: str = "active") -> list[Row(image_id, text)]`; `batch.build_tags_from_notes.main(trigger="manual", run_id=None, incremental=True)` and `_process(incremental: bool)`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_images_repository_note_tagging.py`:

```python
"""
Integration tests for ImagesRepository.get_notes_needing_tags -- the staleness selection behind
build_tags_from_notes --incremental. Timestamps are set explicitly: server_default now() is constant
inside the test's outer transaction. Requires a live PostgreSQL instance.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from repository.images import ImagesRepository
from Storage.models import DescriptionNote, Image, ImageTag

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image_with_note(db_session, note_updated_at, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNote(image_id=image.id, text="a cat", updated_at=note_updated_at))
    await db_session.flush()
    return image


def _tag(image, created_at, source="Note"):
    return ImageTag(image_id=image.id, key="k", value="v", source=source, created_at=created_at)


async def _selected(db_session, image):
    rows = await ImagesRepository(db_session).get_notes_needing_tags("Note")
    return [text for image_id, text in rows if image_id == image.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_note_without_note_tags_is_selected(db_session):
    image = await _image_with_note(db_session, _T0)

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_note_older_than_its_tags_is_skipped(db_session):
    image = await _image_with_note(db_session, _T0)
    db_session.add(_tag(image, _T0 + timedelta(hours=1)))
    await db_session.flush()

    assert await _selected(db_session, image) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_note_edited_after_its_tags_is_selected(db_session):
    image = await _image_with_note(db_session, _T0 + timedelta(hours=2))
    db_session.add(_tag(image, _T0 + timedelta(hours=1)))
    await db_session.flush()

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_other_sources_tags_do_not_count(db_session):
    image = await _image_with_note(db_session, _T0)
    db_session.add(_tag(image, _T0 + timedelta(hours=1), source="Ollama"))
    await db_session.flush()

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_non_active_images_are_not_selected(db_session):
    image = await _image_with_note(db_session, _T0, status="pending")

    assert await _selected(db_session, image) == []
```

Append to `tests/integration/test_description_notes_repository.py` (it already imports `ImageRepository`, `Image`, `DescriptionNote`, `select`, `uuid`, `pytest`; add `ImageTag` to its `Storage.models` import):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_deletes_note_tags_but_not_other_sources(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=image.id, key="k", value="v", source="Note"),
        ImageTag(image_id=image.id, key="k", value="v", source="OCR"),
    ])
    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a cat")
    await db_session.flush()

    await repo.clear_description_note(str(image.id))
    await db_session.flush()

    sources = (await db_session.execute(
        select(ImageTag.source).where(ImageTag.image_id == image.id)
    )).scalars().all()
    assert sources == ["OCR"]
```

`batch/tests/test_build_tags_from_notes.py`:

```python
"""Unit tests for batch/build_tags_from_notes.py. No real DB -- mocks like
test_build_tags_from_descriptions.py."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_notes import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class _StubEngine:
    def __init__(self, tags_by_text):
        self.tags_by_text = tags_by_text
        self.calls = []

    def tag(self, text, language=None):
        from rules.concept_tagger import TagResult
        self.calls.append((text, language))
        return TagResult(tags=self.tags_by_text.get(text, []), trace=[])


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _RecordingTagsSaver:
    instances = []

    def __init__(self, session):
        self.added = []
        _RecordingTagsSaver.instances.append(self)

    def add_tag(self, image_id, tag_name, value, source):
        self.added.append((image_id, tag_name, value, source))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _patches(module, rows, engine):
    _RecordingTagsSaver.instances = []
    tags_repo = AsyncMock()
    images_repo = AsyncMock()
    images_repo.get_total_images.return_value = 0
    images_repo.get_notes_needing_tags.return_value = rows
    images_repo.get_all_notes.return_value = rows

    stack = ExitStack()
    for p in (
        patch.object(module, "AsyncSessionLocal", return_value=_FakeSession()),
        patch.object(module, "TagsRepository", return_value=tags_repo),
        patch.object(module, "ImagesRepository", return_value=images_repo),
        patch.object(module, "TagsSaver", _RecordingTagsSaver),
        patch.object(module.ConceptTagger, "load", return_value=engine),
    ):
        stack.enter_context(p)
    return tags_repo, images_repo, stack


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_defaults_to_incremental(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_notes as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_notes", trigger="manual")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_notes as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)


class TestProcess:
    @pytest.mark.asyncio
    async def test_incremental_tags_notes_with_no_language_and_rewrites_selected_images(self):
        import batch.build_tags_from_notes as module

        engine = _StubEngine({"a cat on a sofa": [("animal", "cat")]})
        rows = [("img-1", "a cat on a sofa")]
        tags_repo, _images_repo, stack = _patches(module, rows, engine)

        with stack:
            await module._process(incremental=True)

        assert engine.calls == [("a cat on a sofa", None)]
        tags_repo.delete_tags_for_images.assert_awaited_once()
        source, image_ids = tags_repo.delete_tags_for_images.await_args.args
        assert source == "Note" and set(image_ids) == {"img-1"}
        assert _RecordingTagsSaver.instances[0].added == [("img-1", "animal", "cat", "Note")]

    @pytest.mark.asyncio
    async def test_full_mode_deletes_all_note_tags_and_reads_every_note(self):
        import batch.build_tags_from_notes as module

        engine = _StubEngine({})
        tags_repo, images_repo, stack = _patches(module, [("img-1", "x")], engine)

        with stack:
            await module._process(incremental=False)

        tags_repo.delete_tags.assert_awaited_once_with("Note")
        tags_repo.delete_tags_for_images.assert_not_awaited()
        images_repo.get_all_notes.assert_awaited_once()
        images_repo.get_notes_needing_tags.assert_not_awaited()
```

- [ ] **Step 2: Run to verify they fail**

Run: both integration files (with `DATABASE_URL`); `pytest batch/tests/test_build_tags_from_notes.py -q`.
Expected: FAIL (`AttributeError: ... get_notes_needing_tags`, `ModuleNotFoundError: batch.build_tags_from_notes`, and the clear test leaves the `Note` tag behind).

- [ ] **Step 3: Implement**

`repository/tags.py`: below `_DELETE_CHUNK_SIZE` add

```python
NOTE_TAG_SOURCE = "Note"
```

`repository/images.py` — update imports to include `DescriptionNote`:
`from Storage.models import OCRText, Image, ImageDescription, ImageTag, ImageProcessingStatus, DescriptionNote`
and add these methods after `get_images_and_descriptions_needing_tags`:

```python
    async def get_notes_needing_tags(self, source: str, status: str = "active"):
        """(image_id, text) of notes whose `source` tags are missing or stale (note edited after
        the image's newest `source` tag). A note that yields no tags has nothing to compare
        against, so it is re-selected every call: harmless, tagging is idempotent."""
        latest_tag = (
            select(ImageTag.image_id, func.max(ImageTag.created_at).label("latest"))
            .where(ImageTag.source == source)
            .group_by(ImageTag.image_id)
            .subquery()
        )
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .join(self.img, self.img.id == DescriptionNote.image_id)
            .outerjoin(latest_tag, latest_tag.c.image_id == DescriptionNote.image_id)
            .where(
                self.img.status == status,
                or_(latest_tag.c.latest.is_(None), DescriptionNote.updated_at > latest_tag.c.latest),
            )
        )
        return result.all()

    async def get_all_notes(self, status: str = "active"):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .join(self.img, self.img.id == DescriptionNote.image_id)
            .where(self.img.status == status)
        )
        return result.all()
```

`Backend/app/repositories/image_repository.py` — in `clear_description_note`, before the `delete(DescriptionNote)` statement add (and extend its docstring: "Note-derived tags (source 'Note') are deleted too, so a cleared note stops producing tags"):

```python
        await self.session.execute(
            delete(ImageTag).where(ImageTag.image_id == image_id, ImageTag.source == NOTE_TAG_SOURCE)
        )
```
with `from repository.tags import NOTE_TAG_SOURCE` added to the imports.

`batch/build_tags_from_notes.py`:

```python
import argparse
import asyncio
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import NOTE_TAG_SOURCE, TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent


async def _process(incremental: bool) -> None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    engine = ConceptTagger.load(data_dir, profile)

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags(NOTE_TAG_SOURCE)

        print(f"Tagging notes with profile '{profile}' from {data_dir} ...")
        print(f"Mode: {'incremental' if incremental else 'full'}")

        if incremental:
            notes = await images_repo.get_notes_needing_tags(NOTE_TAG_SOURCE)
            # Tags are rewritten from the current note text, so drop the old ones first; same
            # session as the saver below, so both commit together.
            await tags_repo.delete_tags_for_images(NOTE_TAG_SOURCE, {image_id for image_id, _text in notes})
        else:
            notes = await images_repo.get_all_notes()

        metrics = SimpleMetricsListener()
        async with TagsSaver(session) as tags_saver:
            for image_id, text in notes:
                # Notes carry no language tag, so language=None (script-based fallback), the same
                # convention batch/build_description_note_lemmas.py indexes with.
                result = engine.tag(text, language=None)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, NOTE_TAG_SOURCE)
                metrics.increment("notes.processed")
                metrics.add("tags.total", len(result.tags))
        print("Tags:")
        metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_notes", trigger=trigger):
            await _process(incremental=incremental)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only (re)tag notes with no Note tags yet or edited since their last tagging "
                             "(default: clear all Note tags and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental))
```

`environments/batch_registry.yaml`: add after `build_tags_from_descriptions:`

```yaml
build_tags_from_notes:
  module: batch.build_tags_from_notes
  kind: build_tags_from_notes
```

- [ ] **Step 4: Run tests**

Run both integration files (with `DATABASE_URL`), then `pytest batch/tests/test_build_tags_from_notes.py batch/tests/test_registry.py -q`, then `cd Backend && pytest -q`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repository/tags.py repository/images.py Backend/app/repositories/image_repository.py batch/build_tags_from_notes.py environments/batch_registry.yaml tests/integration/test_images_repository_note_tagging.py tests/integration/test_description_notes_repository.py batch/tests/test_build_tags_from_notes.py
git commit -m "feat: build_tags_from_notes job; clearing a note deletes its Note tags"
```

---

### Task 6: Inline note-lemma refresh on `PUT`

**Files:**
- Modify: `repository/description_note_lemmas.py` (shared function)
- Modify: `batch/build_description_note_lemmas.py:13-34`
- Modify: `Backend/app/repositories/image_repository.py:253-262` (`set_description_note`)
- Test: `tests/integration/test_description_notes_repository.py` (add tests), `tests/integration/test_description_note_lemmas_shared.py`

**Interfaces:**
- Produces: `repository.description_note_lemmas.note_lemma_set(text, morph, min_word_length) -> set[str]` (the one normalization of a note, `language=None`, `keep_digit_tokens=True`) and `compute_note_lemmas(text) -> set[str]` (same, with the process-wide cached morph and `settings.BOW.MIN_WORD_LENGTH`). `ImageRepository.set_description_note(image_id, text)` now also replaces that note's `description_note_lemmas` rows and sets `lemmas_built_at = updated_at`, in the same transaction.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_description_note_lemmas_shared.py`:

```python
"""The note-lemma normalization is one function shared by the batch job and PUT. No DB needed, but
lives in the integration root because it imports repository code that needs DATABASE_URL set."""
from repository.description_note_lemmas import compute_note_lemmas, note_lemma_set
from rules.normalize import make_morph, normalize


def test_note_lemma_set_matches_the_batch_jobs_original_normalize_call():
    morph = make_morph()
    text = "A cat wearing a hat 42"

    expected = normalize(text, morph, min_length=3, language=None, keep_digit_tokens=True)

    assert note_lemma_set(text, morph, 3) == expected
    assert "42" in note_lemma_set(text, morph, 3)


def test_compute_note_lemmas_uses_configured_min_length():
    lemmas = compute_note_lemmas("a cat wearing a hat")

    assert "cat" in lemmas and "hat" in lemmas
```

Append to `tests/integration/test_description_notes_repository.py` (add `DescriptionNoteLemma` is already imported there):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_set_description_note_writes_lemmas_and_marks_them_built(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a pineapple wearing a hat")
    await db_session.flush()

    lemmas = set((await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all())
    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert {"pineapple", "hat"} <= lemmas
    assert note.lemmas_built_at == note.updated_at   # batch staleness predicate will skip it


@pytest.mark.asyncio(loop_scope="session")
async def test_set_description_note_replaces_lemmas_when_edited(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "pineapple")
    await db_session.flush()
    await repo.set_description_note(str(image.id), "zebra")
    await db_session.flush()

    lemmas = set((await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all())
    assert lemmas == {"zebra"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/integration/test_description_note_lemmas_shared.py tests/integration/test_description_notes_repository.py -q` (with `DATABASE_URL`).
Expected: FAIL (`ImportError: cannot import name 'compute_note_lemmas'`; lemma assertions fail).

- [ ] **Step 3: Implement**

`repository/description_note_lemmas.py` — update imports and add the functions above the repository class:

```python
from functools import lru_cache

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionNote, DescriptionNoteLemma


@lru_cache(maxsize=1)
def _get_morph():
    return make_morph()


def note_lemma_set(text: str, morph, min_word_length: int) -> set[str]:
    """The one normalization of a human note into lemmas, shared by the batch job and by
    PUT /description-note. language=None: notes have no language tag, so this matches
    matching_image_ids' own query-time convention (script-based pymorphy3 fallback) and the
    index is never pre-stemmed for English -- see the comment above _stem_lemma_ids in
    repository/ocr_lemmas.py."""
    return normalize(text, morph, min_length=min_word_length, language=None, keep_digit_tokens=True)


def compute_note_lemmas(text: str) -> set[str]:
    return note_lemma_set(text, _get_morph(), settings.BOW.MIN_WORD_LENGTH)
```

`batch/build_description_note_lemmas.py` — replace the `normalize(...)` call and drop the now-unused `normalize` import (keep `make_morph`):

```python
from repository.description_note_lemmas import (
    DescriptionNoteLemmasRepository, DescriptionNoteLemmasSaver, note_lemma_set,
)
from rules.normalize import make_morph
```
```python
            lemma_set = note_lemma_set(text, morph, min_word_length)
```
(delete the old explanatory comment block above it; it moved into `note_lemma_set`'s docstring).

`Backend/app/repositories/image_repository.py` — add imports `from repository.description_note_lemmas import DescriptionNoteLemmasSaver, compute_note_lemmas` and rewrite `set_description_note`:

```python
    async def set_description_note(self, image_id: str, text: str) -> None:
        stmt = (
            insert(DescriptionNote)
            .values(image_id=image_id, text=text, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"text": text, "updated_at": func.now()},
            )
            .returning(DescriptionNote.updated_at)
        )
        updated_at = (await self.session.execute(stmt)).scalar_one()

        # Refresh this note's lemma index in the same transaction so an edited note is searchable
        # immediately. Stamped with the observed updated_at (same value the batch's staleness
        # predicate compares against), so build_description_note_lemmas skips it. Tags and the
        # embedding stay batch-only: the backend has neither rapidfuzz nor the SBERT model.
        saver = DescriptionNoteLemmasSaver(self.session)
        await saver.replace_lemmas(image_id, compute_note_lemmas(text))
        await self.session.execute(
            sqlalchemy.update(DescriptionNote)
            .where(DescriptionNote.image_id == image_id)
            .values(lemmas_built_at=updated_at)
        )
```
(`sqlalchemy` is already imported at the top of this file; `update` is not in its `from sqlalchemy import` list, hence the qualified name. `DescriptionNoteLemmasSaver.replace_lemmas` does not commit; only its `__aexit__` does and it is not used as a context manager here.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_description_note_lemmas_shared.py tests/integration/test_description_notes_repository.py tests/integration/test_build_description_note_lemmas.py -q` (with `DATABASE_URL`), then `cd Backend && pytest -q`.
Expected: PASS (the pre-existing batch integration test still passes because `run()`'s signature is unchanged).

- [ ] **Step 5: Commit**

```bash
git add repository/description_note_lemmas.py batch/build_description_note_lemmas.py Backend/app/repositories/image_repository.py tests/integration/test_description_note_lemmas_shared.py tests/integration/test_description_notes_repository.py
git commit -m "feat: PUT description-note refreshes that note's lemmas inline"
```

---

### Task 7: Docs, full verification and rollout notes

**Files:**
- Modify: `docs/data-flow.md`, `CLAUDE.md`, `ARCHITECTURE.md`, `backend_api.md` (PUT/DELETE note behavior)
- Modify: spec status line

- [ ] **Step 1: Update docs**

- `docs/data-flow.md`: in the signal/consumer matrix add `description_lemmas` (producer `build_description_lemmas`, consumer smart search) and mark Ollama description as a search source; add `source=description_all` to the similarity modes; add the `Note` tag source and `build_tags_from_notes`; add "Latency after a note edit: lemmas immediate (inline in PUT); tags after `build_tags_from_notes`; similarity vector after `build_description_note_embeddings`". In section 5 (known gaps) remove "descriptions are not searchable" and "notes do not join tagging or description similarity". Remove the "Ollama descriptions are not in that set" sentence in section 1.
- `CLAUDE.md` batch list: add entries (match the style of neighbours) for `build_description_lemmas` (indexes each Ollama description as English; incremental by construction; admin-triggerable, manual only; excluded when the description is rejected, at query time) and `build_tags_from_notes` (ConceptTagger, source "Note", `language=None`; `--incremental` re-tags notes edited after their newest Note tag; admin-triggerable, manual only). Add `build_description_lemmas` to the `build_description_note_lemmas` neighbourhood.
- `ARCHITECTURE.md`: add both jobs to the pipeline lists where `build_tags_from_descriptions` appears (lines ~126, ~258, ~420, ~602).
- `backend_api.md`: under `PUT /api/images/{id}/description-note` state that the note's lemmas are refreshed in the request; under `DELETE` (and PUT-with-empty-text) state that `Note` tags are removed.
- Set the spec's `Status:` to `done` and its `Plan:` line is already correct.

- [ ] **Step 2: Full verification (four separate commands)**

```
$env:DATABASE_URL='postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test'; pytest tests/integration/ -q
cd Backend; pytest -q
pytest batch/tests/ -q
pytest tests/rules/ -q
```
Expected: all PASS, 0 failures. Report counts. Then from `Storage/` run `alembic heads` (one head) and confirm the server imports: `python -c "import Backend.app.main"` from the repo root with `DATABASE_URL` set to the test DB.

- [ ] **Step 3: Controller-only performance check (not for subagents)**

The controller (not a subagent) runs, against one environment with `DATABASE_URL_READONLY` only: an `EXPLAIN (ANALYZE)` of the `get_similar_by_description_all` SELECT for an image with a note and several descriptions. This is a SELECT, so it is safe on the read-only role. If it exceeds ~2 s on the metal corpus, open a follow-up to switch to per-vector HNSW lookups merged in the service (spec section 2); do not block this plan on it.

- [ ] **Step 4: Commit**

```bash
git add docs/data-flow.md CLAUDE.md ARCHITECTURE.md backend_api.md docs/superpowers/specs/2026-10-01-description-notes-search-similarity-tagging-design.md
git commit -m "docs: descriptions and notes in search, similarity and tagging"
```

- [ ] **Step 5: Rollout notes for the user (do not run)**

Per environment (metal, general; IT has no tagging vocabulary): `alembic upgrade head`, then `build_description_lemmas`, then `build_tags_from_notes` (full run once), set `PYTHONIOENCODING=utf-8`. Never run these against the live environments from a subagent.
