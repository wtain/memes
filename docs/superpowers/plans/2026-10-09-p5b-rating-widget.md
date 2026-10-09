# P5b Rating Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner rate each result of the message-search page with three buttons (good fit, acceptable, not a fit), store the ratings with the message text (only when a rating exists), and export them into the gold-set import format for review.

**Architecture:** A dedicated `meme_text_ratings` table with an additive Alembic migration, a repository and a small service/router (`/api/meme-from-text/ratings`, kept apart from the stateless search router), a server-computed `meta.queryHash` on the search response, a frontend `useRatings` hook plus a `RatingBar` inside `ResultCard`, and a pure export builder behind a new `python -m evaluation export-ratings` command.

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres), Alembic, the generated Pydantic/TypeScript/Kotlin types, React 19 + React Compiler lint rules, vitest + Testing Library, the P6 `evaluation` package.

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5b-rating-widget-design.md` (read it first). Inherited: the P5 page and its addendum (`2026-10-08-p5-web-ui-design.md`), P4 (`retrieval/pipeline.py`, the search endpoint), P6 (`evaluation/gold.py`, `import-judgments`).

## Global Constraints

- Python is `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe` (a worktree has no venv). Run each pytest root as its own command (`Backend` from `Backend/`, `tests/retrieval`, `tests/evaluation`, `tests/taxonomy`, the whole `tests/integration`). **Never `pip install`, never `pnpm install`**; `Frontend/memes-frontend/node_modules` in the worktree is a junction the controller creates (git-ignored); if it is missing stop and report. Frontend commands run from `Frontend/memes-frontend/` with `pnpm exec`.
- **Database:** only the dedicated test database `postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`, given on the command line for integration tests (Tasks 1 and 2 only). Never any other URL, never `environments/.env.*`, **never run `alembic` against anything** (the controller applies migrations per environment; the migration is verified by a test that runs its `upgrade()`/`downgrade()` inside a rolled-back transaction on the test database). Never bind a dev server or any port.
- Work only inside your own worktree (earlier subagents wrote into the main checkout by mistake): before every commit run `git status` and `git rev-parse --show-toplevel` and confirm both point at the worktree.
- **The user's message text is never logged and never appears in an error message or an HTTP error body**, in backend or frontend. Rating text is stored only in `meme_text_ratings.query_text` and only because a rating exists.
- The React app renders in `StrictMode` and the ESLint ruleset includes the React Compiler rules: every effect must survive a double mount, no ref reads/writes during render, no synchronous `setState`/`dispatch` in an effect body, exhaustive deps. Restructure (never disable a rule; an `exhaustive-deps` disable only with a one-line justification when the omitted dependency is provably stable).
- Frontend gates before every frontend commit, with real output pasted in the report: `pnpm exec tsc -b; echo "exit $?"` (silent on success), `pnpm exec eslint src/ --max-warnings 0; echo "exit $?"`, the whole `pnpm exec vitest run`, and (Tasks 5) `pnpm exec vite build`. The controller re-runs every gate after each task.
- Regenerating types (Task 2): Python from `Backend/` with `datamodel-codegen` (command in `documents/generation.md`, executable `/h/workspace_sandbox/memes/.venv311/Scripts/datamodel-codegen.exe`), TypeScript with `bash generate-types.sh` from `Frontend/`, Kotlin with `python AndroidClient/scripts/generate_dtos.py` from the worktree root. Commit only real changes (revert timestamp-only churn in unrelated generated files with `git checkout -- <path>`). Never hand-edit generated files.
- Preserve each existing file's line endings (use the Edit tool; check `git ls-files --eol <file>`); new files may be LF. Do not round-trip repo text through PowerShell `Get/Set-Content`.
- Constants: grades 0 (not a fit), 1 (acceptable), 2 (good fit); message limit 600 characters (the parser's `parse.max_input_chars`); search text limit in the public schema 8,000 (the server truncates to 600 after normalisation, like the search endpoint).
- Commit messages end with these two trailer lines, exactly (Claude Sonnet 5.5, never another model name):
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01WUvgSm19vfxX68CBg4XvxY`.

## File Structure

| Path | Responsibility |
|---|---|
| `Storage/models.py` (modify), `Storage/alembic/versions/e6b1c4d8f203_add_meme_text_ratings.py` | `MemeTextRating` model and its migration |
| `repository/meme_text_ratings.py` | Async data access (upsert, delete, list, forget text, export read); never commits |
| `retrieval/query_hash.py` | Pure helper: normalise a message and hash it |
| `retrieval/pipeline.py` (modify) | `meta.queryHash` |
| `shared/schemas/ratingrequest.schema.json`, `ratingresponse.schema.json`, `ratingsresponse.schema.json`, `ratingitem.schema.json`, `memefromtextresponse.schema.json` (modify), `all.schema.json` (modify) + regenerated trees | Public schemas |
| `Backend/app/services/meme_text_rating_service.py`, `Backend/app/api/meme_text_ratings.py`, `Backend/app/main.py` (modify), `backend_api.md` (modify) | Service, router, docs |
| `Frontend/memes-frontend/src/reply/types.ts`, `fixtures.ts` (modify), `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`, `src/test/mockApi.ts` (modify) | API layer |
| `src/reply/useRatings.ts` | Ratings state hook |
| `src/components/reply/RatingBar.tsx`, `ForgetTextButton.tsx`, `ResultCard.tsx` (modify) | UI |
| `src/pages/ReplyPage.tsx` (modify) | Wiring |
| `evaluation/ratings_export.py`, `evaluation/__main__.py` (modify), `evaluation/gold/README.md` (modify) | Export builder and CLI |

## Deviations decided while planning

1. The hash helper lives in `retrieval/query_hash.py` (pure, stdlib plus `parse`) so the pipeline, the rating service and the export share one definition; `normalise_text` stays in `parse/parser.py`.
2. `meta.queryHash` is an **optional** string in the shared schema (the unavailable answer of an environment without a parser carries none; such an environment cannot be rated anyway).
3. The text-clearing endpoint answers `204 No Content` (no extra response schema), as do the two other DELETEs.
4. The rating bar's saved/cleared announcement lives in each card's own always-mounted `role="status"` element (not one global region), driven by the hook's last event.
5. A rollback after a failed save restores the last server-confirmed grade for that image (not the grade before the click), so rapid clicks cannot leave the UI out of step with the server.

---

### Task 1: Ratings data (model, migration, repository)

**Files:**
- Create: `Storage/alembic/versions/e6b1c4d8f203_add_meme_text_ratings.py`, `repository/meme_text_ratings.py`, `tests/integration/test_meme_text_ratings_repository.py`, `tests/integration/test_meme_text_ratings_migration.py`
- Modify: `Storage/models.py` (add the model after `MemeProfileSituationEmbedding`)

**Interfaces:**
- Produces: `Storage.models.MemeTextRating`; `repository.meme_text_ratings`: `RatingRow` (frozen dataclass: `query_hash, query_text, image_id, grade, rank, reading, parser_version, taxonomy_version, degraded, fallback, language, rated_at`), `MemeTextRatingRepository(session)` with `async upsert(*, query_hash, query_text, image_id, grade, rank, reading, parser_version, taxonomy_version, degraded, fallback, language) -> int` (the stored grade), `async delete(query_hash, image_id) -> bool`, `async grades_for(query_hash) -> list[tuple[uuid.UUID, int]]`, `async forget_text(query_hash) -> int`, `async image_is_active(image_id) -> bool`, `async all_ratings() -> list[RatingRow]`.

- [ ] **Step 1: Write the failing repository tests**

Create `tests/integration/test_meme_text_ratings_repository.py`:

```python
"""
Integration tests for repository/meme_text_ratings.py. Requires a live PostgreSQL instance with pgvector --
see tests/integration/conftest.py.
"""
import inspect
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from repository import meme_text_ratings
from repository.meme_text_ratings import MemeTextRatingRepository
from Storage.models import Image, MemeTextRating

HASH = "a" * 64


async def _image(session, status="active") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def _fields(image, **overrides):
    values = dict(query_hash=HASH, query_text="my manager scheduled a meeting", image_id=image.id, grade=2, rank=3,
                  reading="parsed", parser_version="p3-x", taxonomy_version=2, degraded=False, fallback="none",
                  language="en")
    values.update(overrides)
    return values


async def _count(session, query_hash=HASH) -> int:
    return (await session.execute(
        select(func.count()).select_from(MemeTextRating).where(MemeTextRating.query_hash == query_hash))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_inserts_then_updates_the_same_row(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)

    assert await repo.upsert(**_fields(image, grade=2)) == 2
    assert await repo.upsert(**_fields(image, grade=0, rank=5, reading="edited", degraded=True, fallback="timeout")) == 0

    assert await _count(db_session) == 1
    row = (await db_session.execute(select(MemeTextRating).where(MemeTextRating.image_id == image.id))).scalar_one()
    assert (row.grade, row.rank, row.reading, row.degraded, row.fallback) == (0, 5, "edited", True, "timeout")
    assert row.updated_at >= row.rated_at


@pytest.mark.asyncio(loop_scope="session")
async def test_the_same_image_can_be_rated_for_different_messages(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image, query_hash="a" * 64))
    await repo.upsert(**_fields(image, query_hash="b" * 64, grade=1))

    assert await _count(db_session, "a" * 64) == 1 and await _count(db_session, "b" * 64) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_grades_for_lists_one_message_ordered_by_image(db_session):
    first, second, other = await _image(db_session), await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first, grade=2))
    await repo.upsert(**_fields(second, grade=0))
    await repo.upsert(**_fields(other, query_hash="c" * 64, grade=1))

    grades = await repo.grades_for(HASH)

    assert sorted(grades) == sorted([(first.id, 2), (second.id, 0)])
    assert grades == sorted(grades, key=lambda pair: str(pair[0]))
    assert await repo.grades_for("d" * 64) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_removes_only_the_named_rating(db_session):
    first, second = await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first))
    await repo.upsert(**_fields(second))

    assert await repo.delete(HASH, first.id) is True
    assert await repo.delete(HASH, first.id) is False
    assert [image_id for image_id, _ in await repo.grades_for(HASH)] == [second.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_forget_text_clears_the_text_and_keeps_grades_and_ids(db_session):
    first, second = await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(first, grade=2))
    await repo.upsert(**_fields(second, grade=1))
    await repo.upsert(**_fields(first, query_hash="e" * 64, query_text="another message"))

    assert await repo.forget_text(HASH) == 2

    rows = (await db_session.execute(
        select(MemeTextRating).where(MemeTextRating.query_hash == HASH))).scalars().all()
    assert {(row.image_id, row.grade) for row in rows} == {(first.id, 2), (second.id, 1)}
    assert all(row.query_text is None for row in rows)
    other = (await db_session.execute(
        select(MemeTextRating).where(MemeTextRating.query_hash == "e" * 64))).scalar_one()
    assert other.query_text == "another message"


@pytest.mark.asyncio(loop_scope="session")
async def test_rating_again_after_forgetting_stores_the_text_again(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image))
    await repo.forget_text(HASH)
    await repo.upsert(**_fields(image, grade=1))

    row = (await db_session.execute(select(MemeTextRating).where(MemeTextRating.image_id == image.id))).scalar_one()
    assert row.query_text == "my manager scheduled a meeting"


@pytest.mark.asyncio(loop_scope="session")
async def test_image_is_active_only_for_active_images(db_session):
    active, pending = await _image(db_session), await _image(db_session, status="pending")
    repo = MemeTextRatingRepository(db_session)

    assert await repo.image_is_active(active.id) is True
    assert await repo.image_is_active(pending.id) is False
    assert await repo.image_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_all_ratings_returns_plain_rows_oldest_first(db_session):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    await repo.upsert(**_fields(image, query_hash="1" * 64))
    await repo.upsert(**_fields(image, query_hash="2" * 64, grade=0))

    rows = [row for row in await repo.all_ratings() if row.image_id == image.id]

    assert [row.query_hash for row in rows] == ["1" * 64, "2" * 64]
    assert rows[0].grade == 2 and rows[1].grade == 0 and rows[0].language == "en"
    assert isinstance(rows[0].rated_at, str)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("overrides", [{"grade": 3}, {"grade": -1}, {"rank": 0}, {"reading": "guessed"}])
async def test_the_database_rejects_invalid_values(db_session, overrides):
    image = await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    async with db_session.begin_nested():
        with pytest.raises(IntegrityError):
            await repo.upsert(**_fields(image, **overrides))


@pytest.mark.asyncio(loop_scope="session")
async def test_ratings_cascade_when_the_image_is_deleted(db_session):
    image = await _image(db_session)
    await MemeTextRatingRepository(db_session).upsert(**_fields(image))

    await db_session.delete(image)
    await db_session.flush()

    assert await _count(db_session) == 0


def test_the_repository_never_commits():
    assert "commit(" not in inspect.getsource(meme_text_ratings)
```

- [ ] **Step 2: Run to see it fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_meme_text_ratings_repository.py -q`
Expected: collection ERROR (`MemeTextRating` / `repository.meme_text_ratings` missing).

- [ ] **Step 3: Add the model**

In `Storage/models.py`, after `MemeProfileSituationEmbedding` (and its `__table_args__`), add (the file already imports `Column, String, Integer, Text, ForeignKey, DateTime, func, Index, Boolean, UniqueConstraint, CheckConstraint`, `UUID`, `sa`, `uuid`):

```python
class MemeTextRating(Base):
    """A rating the owner gave to one result of a meme-from-text search (epic meme-from-text, P5b).
    query_hash is sha256 of the message normalised like the parser (NFC, collapsed whitespace, cut at
    parse.max_input_chars); a change of that limit changes new hashes, old ratings keep their old hash.
    query_text is stored only because a rating exists and can be cleared per message (forget text)."""
    __tablename__ = "meme_text_ratings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_hash = Column(String(64), nullable=False)
    query_text = Column(Text, nullable=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False)
    grade = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)
    reading = Column(String(8), nullable=False)
    parser_version = Column(String, nullable=False)
    taxonomy_version = Column(Integer, nullable=False)
    degraded = Column(Boolean, nullable=False, server_default=sa.false(), default=False)
    fallback = Column(String, nullable=False, server_default="none", default="none")
    language = Column(String(8), nullable=False)
    rated_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("query_hash", "image_id", name="uq_meme_text_ratings_query_image"),
        Index("ix_meme_text_ratings_rated_at", "rated_at"),
        CheckConstraint("grade IN (0, 1, 2)", name="ck_meme_text_ratings_grade"),
        CheckConstraint("rank >= 1", name="ck_meme_text_ratings_rank"),
        CheckConstraint("reading IN ('parsed', 'edited')", name="ck_meme_text_ratings_reading"),
    )
```

- [ ] **Step 4: Write the repository**

Create `repository/meme_text_ratings.py`:

```python
"""Ratings of meme-from-text results (P5b). Never commits (CLAUDE.md): get_async_db owns the transaction."""
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from Storage.models import Image, MemeTextRating

CONFLICT = "uq_meme_text_ratings_query_image"


@dataclass(frozen=True)
class RatingRow:
    query_hash: str
    query_text: str | None
    image_id: uuid.UUID
    grade: int
    rank: int
    reading: str
    parser_version: str
    taxonomy_version: int
    degraded: bool
    fallback: str
    language: str
    rated_at: str


class MemeTextRatingRepository:
    def __init__(self, session):
        self.session = session

    async def upsert(self, *, query_hash: str, query_text: str | None, image_id: uuid.UUID, grade: int, rank: int,
                     reading: str, parser_version: str, taxonomy_version: int, degraded: bool, fallback: str,
                     language: str) -> int:
        """Insert, or update the existing rating of this message and image (the latest metadata wins)."""
        values = dict(query_hash=query_hash, query_text=query_text, image_id=image_id, grade=grade, rank=rank,
                      reading=reading, parser_version=parser_version, taxonomy_version=taxonomy_version,
                      degraded=degraded, fallback=fallback, language=language)
        update_values = {key: value for key, value in values.items() if key not in ("query_hash", "image_id")}
        update_values["updated_at"] = func.now()
        statement = (
            insert(MemeTextRating).values(**values)
            .on_conflict_do_update(constraint=CONFLICT, set_=update_values)
            .returning(MemeTextRating.grade)
        )
        return (await self.session.execute(statement)).scalar_one()

    async def delete(self, query_hash: str, image_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(MemeTextRating).where(MemeTextRating.query_hash == query_hash, MemeTextRating.image_id == image_id))
        return result.rowcount > 0

    async def grades_for(self, query_hash: str) -> list[tuple[uuid.UUID, int]]:
        rows = await self.session.execute(
            select(MemeTextRating.image_id, MemeTextRating.grade).where(MemeTextRating.query_hash == query_hash))
        return sorted(((image_id, grade) for image_id, grade in rows.all()), key=lambda pair: str(pair[0]))

    async def forget_text(self, query_hash: str) -> int:
        """Clear the stored message text of every rating of this message; grades and ids stay."""
        result = await self.session.execute(
            update(MemeTextRating).where(MemeTextRating.query_hash == query_hash).values(query_text=None))
        return result.rowcount

    async def image_is_active(self, image_id: uuid.UUID) -> bool:
        found = await self.session.execute(select(Image.id).where(Image.id == image_id, Image.status == "active"))
        return found.first() is not None

    async def all_ratings(self) -> list[RatingRow]:
        """Every rating, oldest first (the export reads this with the read-only role)."""
        rows = await self.session.execute(select(MemeTextRating).order_by(MemeTextRating.rated_at, MemeTextRating.id))
        return [
            RatingRow(row.query_hash, row.query_text, row.image_id, row.grade, row.rank, row.reading,
                      row.parser_version, row.taxonomy_version, bool(row.degraded), row.fallback, row.language,
                      row.rated_at.isoformat())
            for row in rows.scalars().all()
        ]
```

(`all_ratings` orders by `rated_at`; two upserts in one transaction share `now()`, so the secondary `id` order is random: if the test `test_all_ratings_returns_plain_rows_oldest_first` flakes for that reason, order by `(rated_at, query_hash)` instead and say so.)

- [ ] **Step 5: Write the migration and its test**

Create `Storage/alembic/versions/e6b1c4d8f203_add_meme_text_ratings.py`:

```python
"""add meme text ratings

Revision ID: e6b1c4d8f203
Revises: d5a9f3c1e7b2
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e6b1c4d8f203'
down_revision: Union[str, Sequence[str], None] = 'd5a9f3c1e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meme_text_ratings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('query_hash', sa.String(length=64), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=True),
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('reading', sa.String(length=8), nullable=False),
        sa.Column('parser_version', sa.String(), nullable=False),
        sa.Column('taxonomy_version', sa.Integer(), nullable=False),
        sa.Column('degraded', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('fallback', sa.String(), server_default='none', nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('rated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('grade IN (0, 1, 2)', name='ck_meme_text_ratings_grade'),
        sa.CheckConstraint('rank >= 1', name='ck_meme_text_ratings_rank'),
        sa.CheckConstraint("reading IN ('parsed', 'edited')", name='ck_meme_text_ratings_reading'),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('query_hash', 'image_id', name='uq_meme_text_ratings_query_image'),
    )
    op.create_index('ix_meme_text_ratings_rated_at', 'meme_text_ratings', ['rated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_meme_text_ratings_rated_at', table_name='meme_text_ratings')
    op.drop_table('meme_text_ratings')
```

Create `tests/integration/test_meme_text_ratings_migration.py` (runs the real `upgrade()`/`downgrade()` in a transaction that is rolled back, so the shared test database is untouched):

```python
"""The ratings migration creates exactly what the ORM model declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions" / "e6b1c4d8f203_add_meme_text_ratings.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_e6b1c4d8f203", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"]) for c in inspector.get_columns("meme_text_ratings")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("meme_text_ratings")}
    checks = {c["name"] for c in inspector.get_check_constraints("meme_text_ratings")}
    indexes = {i["name"] for i in inspector.get_indexes("meme_text_ratings")}
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("meme_text_ratings")}
    return columns, uniques, checks, indexes, foreign


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # the session fixture created the table from the model: capture its shape, then rebuild it from the migration
            model_shape = await connection.run_sync(_shape)
            await connection.execute(text("DROP TABLE meme_text_ratings"))

            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            migrated_shape = await connection.run_sync(_shape)
            assert migrated_shape == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "meme_text_ratings" not in tables
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "e6b1c4d8f203" and module.down_revision == "d5a9f3c1e7b2"
```

- [ ] **Step 6: Run the tests**

Each as its own command, `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line:
`... pytest tests/integration/test_meme_text_ratings_repository.py tests/integration/test_meme_text_ratings_migration.py -q` (PASS), then the WHOLE root `... pytest tests/integration -q` (`Storage/models.py` is shared code). If the migration test's shape comparison fails on cosmetic differences between the model-created and migration-created tables (for example server default text), compare only column names, nullability, unique constraints, check constraint names, index names and foreign keys, and say so.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/e6b1c4d8f203_add_meme_text_ratings.py repository/meme_text_ratings.py tests/integration/test_meme_text_ratings_repository.py tests/integration/test_meme_text_ratings_migration.py
git commit -m "feat(ratings): add the meme_text_ratings table, migration and repository"
```

---

### Task 2: Ratings API (hash helper, `meta.queryHash`, schemas, service, router, docs)

**Files:**
- Create: `retrieval/query_hash.py`, `tests/retrieval/test_query_hash.py`, `shared/schemas/ratingrequest.schema.json`, `ratingresponse.schema.json`, `ratingsresponse.schema.json`, `ratingitem.schema.json`, `Backend/app/services/meme_text_rating_service.py`, `Backend/app/api/meme_text_ratings.py`, `Backend/tests/test_meme_text_rating_service.py`, `Backend/tests/test_meme_text_ratings_endpoint.py`
- Modify: `retrieval/pipeline.py`, `tests/retrieval/test_pipeline.py`, `shared/schemas/memefromtextresponse.schema.json`, `shared/schemas/all.schema.json`, `Backend/app/main.py`, `backend_api.md`; regenerate the three type trees.

**Interfaces:**
- Consumes: Task 1's repository.
- Produces: `retrieval.query_hash.normalised_query(text, max_chars) -> str`, `query_hash(normalised) -> str`; response `meta.queryHash` (optional string) on every computed or cached search response; generated models `Backend.app.types.generated.ratingrequest.Schema` (fields `text, imageId, grade, rank, reading, parserVersion, taxonomyVersion, degraded, fallback, language`), `ratingresponse.Schema` (`queryHash, imageId, grade`), `ratingsresponse.Schema` (`queryHash, ratings: [ratingitem]`), TS `RatingRequest`, `RatingResponse`, `RatingsResponse`, `RatingItem`; the service `MemeTextRatingService(repository, max_chars)` and errors `RatingValidationError`, `UnknownImageError`; HTTP: `PUT /api/meme-from-text/ratings`, `DELETE /api/meme-from-text/ratings?queryHash=&imageId=` (204), `GET /api/meme-from-text/ratings?queryHash=`, `DELETE /api/meme-from-text/ratings/text?queryHash=` (204).

- [ ] **Step 1: Hash helper and the pipeline field (failing tests first)**

Create `tests/retrieval/test_query_hash.py`:

```python
import hashlib

from retrieval.query_hash import normalised_query, query_hash


def test_normalisation_matches_the_parser_rules():
    assert normalised_query("  Hello \n  world\t", 600) == "Hello world"
    assert normalised_query("a" * 700, 600) == "a" * 600
    assert normalised_query("é", 600) == "é"          # NFC


def test_the_hash_is_sha256_of_the_normalised_text():
    text = normalised_query("My  manager   scheduled a meeting", 600)
    assert query_hash(text) == hashlib.sha256("My manager scheduled a meeting".encode("utf-8")).hexdigest()
    assert len(query_hash(text)) == 64


def test_variants_of_one_message_share_a_hash_and_different_messages_do_not():
    one = query_hash(normalised_query("Hello   world", 600))
    two = query_hash(normalised_query("Hello\nworld ", 600))
    other = query_hash(normalised_query("Hello there", 600))
    assert one == two and one != other
```

Add to `tests/retrieval/test_pipeline.py` (adapt the helper names to that file's `_pipeline`, `FakeRepository`, `SearchRequest` usage; read the file first):

```python
@pytest.mark.asyncio
async def test_the_response_meta_carries_the_query_hash_also_on_a_cache_hit():
    from retrieval.query_hash import normalised_query, query_hash
    pipeline = _pipeline()
    repository = FakeRepository(_candidates())
    expected = query_hash(normalised_query("  My friend   got promoted! ", 600))

    first = await pipeline.search(SearchRequest(text="  My friend   got promoted! "), repository)
    second = await pipeline.search(SearchRequest(text="My friend got promoted!"), repository)

    assert first.meta["queryHash"] == expected
    assert second.meta["cacheHit"] is True and second.meta["queryHash"] == expected
```

Create `retrieval/query_hash.py`:

```python
"""The identity of a message for ratings and exports (P5b): normalise like the parser, then sha256. Pure."""
import hashlib

from parse.parser import normalise_text


def normalised_query(text: str, max_chars: int) -> str:
    return normalise_text(text, max_chars)


def query_hash(normalised: str) -> str:
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
```

In `retrieval/pipeline.py` add `from retrieval.query_hash import query_hash` and, in `search`, add `"queryHash": query_hash(text),` to the `meta` dict (after `"model"`), where `text` is the already normalised message. In `memefromtextresponse.schema.json` add to `meta.properties`: `"queryHash": { "type": "string", "description": "sha256 of the normalised message; the key of its ratings" }` (not in `required`). Fix any existing test that asserts the exact set of meta keys. Run `tests/retrieval` (PASS).

- [ ] **Step 2: Schemas and type regeneration**

Create the four schemas (same style as `taxonomyresponse.schema.json`):

`shared/schemas/ratingitem.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ratingitem.schema.json",
  "title": "RatingItem",
  "description": "The grade given to one meme for one message",
  "type": "object",
  "properties": {
    "imageId": { "type": "string" },
    "grade": { "type": "integer", "minimum": 0, "maximum": 2 }
  },
  "required": ["imageId", "grade"]
}
```

`shared/schemas/ratingrequest.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ratingrequest.schema.json",
  "title": "RatingRequest",
  "description": "Rate one result of a meme-from-text search (0 not a fit, 1 acceptable, 2 good fit)",
  "type": "object",
  "properties": {
    "text": { "type": "string", "minLength": 1, "maxLength": 8000, "description": "The message that was searched; the server normalises it and derives the hash" },
    "imageId": { "type": "string" },
    "grade": { "type": "integer", "minimum": 0, "maximum": 2 },
    "rank": { "type": "integer", "minimum": 1, "description": "1-based position in the list shown (after duplicate collapse)" },
    "reading": { "type": "string", "enum": ["parsed", "edited"] },
    "parserVersion": { "type": "string", "maxLength": 128 },
    "taxonomyVersion": { "type": "integer", "minimum": 0 },
    "degraded": { "type": "boolean" },
    "fallback": { "type": "string", "maxLength": 128 },
    "language": { "type": "string", "enum": ["en", "ru", "es", "other"] }
  },
  "required": ["text", "imageId", "grade", "rank", "reading", "parserVersion", "taxonomyVersion", "degraded", "fallback", "language"]
}
```

`shared/schemas/ratingresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ratingresponse.schema.json",
  "title": "RatingResponse",
  "type": "object",
  "properties": {
    "queryHash": { "type": "string" },
    "imageId": { "type": "string" },
    "grade": { "type": "integer", "minimum": 0, "maximum": 2 }
  },
  "required": ["queryHash", "imageId", "grade"]
}
```

`shared/schemas/ratingsresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ratingsresponse.schema.json",
  "title": "RatingsResponse",
  "description": "The grades already given for one message",
  "type": "object",
  "properties": {
    "queryHash": { "type": "string" },
    "ratings": { "type": "array", "items": { "$ref": "./ratingitem.schema.json" } }
  },
  "required": ["queryHash", "ratings"]
}
```

Register in `shared/schemas/all.schema.json` after the `"TaxonomyResponse"` line: `"RatingItem": { "$ref": "ratingitem.schema.json" }, "RatingRequest": { "$ref": "ratingrequest.schema.json" }, "RatingResponse": { "$ref": "ratingresponse.schema.json" }, "RatingsResponse": { "$ref": "ratingsresponse.schema.json" },`. Regenerate the three trees (Global Constraints); expected real changes: new `rating*.py` modules and `__init__.py`, `memefromtextresponse.py` (an optional `queryHash`), `all.d.ts` (four interfaces and the `meta.queryHash?`), `Models.kt` (four data classes and `queryHash`).

- [ ] **Step 3: Service and router tests (failing first)**

Create `Backend/tests/test_meme_text_rating_service.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from Backend.app.services.meme_text_rating_service import (
    MemeTextRatingService, RatingValidationError, UnknownImageError,
)
from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from retrieval.query_hash import normalised_query, query_hash

IMAGE = str(uuid.uuid4())


def _request(**overrides):
    values = dict(text="  My manager   scheduled a meeting ", imageId=IMAGE, grade=2, rank=3, reading="parsed",
                  parserVersion="p3-x", taxonomyVersion=2, degraded=False, fallback="none", language="en")
    values.update(overrides)
    return RatingRequest.model_validate(values)


def _service(active=True):
    repository = MagicMock()
    repository.image_is_active = AsyncMock(return_value=active)
    repository.upsert = AsyncMock(side_effect=lambda **kwargs: kwargs["grade"])
    repository.delete = AsyncMock(return_value=True)
    repository.grades_for = AsyncMock(return_value=[(uuid.UUID(IMAGE), 2)])
    repository.forget_text = AsyncMock(return_value=2)
    return MemeTextRatingService(repository, 600), repository


async def test_put_stores_the_normalised_text_and_the_server_computed_hash():
    service, repository = _service()

    response = await service.put(_request())

    stored = repository.upsert.await_args.kwargs
    expected_text = normalised_query("  My manager   scheduled a meeting ", 600)
    assert stored["query_text"] == expected_text and stored["query_hash"] == query_hash(expected_text)
    assert (stored["grade"], stored["rank"], stored["reading"], stored["language"]) == (2, 3, "parsed", "en")
    assert stored["image_id"] == uuid.UUID(IMAGE) and stored["taxonomy_version"] == 2
    assert (response.queryHash, response.imageId, response.grade) == (stored["query_hash"], IMAGE, 2)


async def test_put_truncates_like_the_search_endpoint():
    service, repository = _service()
    await service.put(_request(text="x" * 700))
    assert repository.upsert.await_args.kwargs["query_text"] == "x" * 600


async def test_blank_text_is_rejected_without_touching_the_repository():
    service, repository = _service()
    with pytest.raises(RatingValidationError):
        await service.put(_request(text="   \n  "))
    repository.upsert.assert_not_called()


async def test_an_unknown_or_inactive_image_is_rejected():
    service, repository = _service(active=False)
    with pytest.raises(UnknownImageError):
        await service.put(_request())
    repository.upsert.assert_not_called()


async def test_a_malformed_image_id_is_a_validation_error():
    service, _ = _service()
    with pytest.raises(RatingValidationError):
        await service.put(_request(imageId="not-a-uuid"))


async def test_delete_list_and_forget_validate_the_hash():
    service, repository = _service()
    good = "a" * 64

    assert (await service.list(good)).ratings[0].grade == 2
    await service.delete(good, IMAGE)
    await service.forget_text(good)

    repository.delete.assert_awaited_once_with(good, uuid.UUID(IMAGE))
    repository.forget_text.assert_awaited_once_with(good)
    for bad in ("short", "z" * 64, "A" * 64):
        with pytest.raises(RatingValidationError):
            await service.list(bad)
    with pytest.raises(RatingValidationError):
        await service.delete(good, "nope")
```

Create `Backend/tests/test_meme_text_ratings_endpoint.py`:

```python
import logging
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.meme_text_ratings import get_rating_service, router
from Backend.app.services.meme_text_rating_service import RatingValidationError, UnknownImageError
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse

IMAGE = str(uuid.uuid4())
HASH = "a" * 64
BODY = {"text": "hello", "imageId": IMAGE, "grade": 2, "rank": 1, "reading": "parsed", "parserVersion": "p3-x",
        "taxonomyVersion": 2, "degraded": False, "fallback": "none", "language": "en"}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.put = AsyncMock(return_value=RatingResponse(queryHash=HASH, imageId=IMAGE, grade=2))
    service.delete = AsyncMock(return_value=None)
    service.list = AsyncMock(return_value=RatingsResponse.model_validate(
        {"queryHash": HASH, "ratings": [{"imageId": IMAGE, "grade": 2}]}))
    service.forget_text = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_rating_service] = lambda: service
    return TestClient(app), service


def test_put_returns_the_stored_rating(client_and_service):
    client, service = client_and_service
    response = client.put("/api/meme-from-text/ratings", json=BODY)
    assert response.status_code == 200
    assert response.json() == {"queryHash": HASH, "imageId": IMAGE, "grade": 2}
    assert service.put.await_args.args[0].text == "hello"


@pytest.mark.parametrize("patch", [{"grade": 3}, {"grade": -1}, {"rank": 0}, {"reading": "guessed"},
                                   {"text": ""}, {"language": "fr"}, {"text": "x" * 8001}])
def test_a_malformed_request_is_a_422(client_and_service, patch):
    client, _ = client_and_service
    assert client.put("/api/meme-from-text/ratings", json={**BODY, **patch}).status_code == 422


def test_validation_and_unknown_image_errors_map_to_422_and_404_without_the_text(client_and_service):
    client, service = client_and_service
    service.put.side_effect = RatingValidationError("the message is empty")
    assert client.put("/api/meme-from-text/ratings", json=BODY).status_code == 422
    service.put.side_effect = UnknownImageError()
    response = client.put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE"})
    assert response.status_code == 404 and "SECRET-MESSAGE" not in response.text


def test_delete_and_forget_answer_204(client_and_service):
    client, service = client_and_service
    assert client.delete(f"/api/meme-from-text/ratings?queryHash={HASH}&imageId={IMAGE}").status_code == 204
    assert client.delete(f"/api/meme-from-text/ratings/text?queryHash={HASH}").status_code == 204
    service.delete.assert_awaited_once_with(HASH, IMAGE)
    service.forget_text.assert_awaited_once_with(HASH)


def test_get_lists_the_grades_of_one_message(client_and_service):
    client, _ = client_and_service
    response = client.get(f"/api/meme-from-text/ratings?queryHash={HASH}")
    assert response.status_code == 200
    assert response.json() == {"queryHash": HASH, "ratings": [{"imageId": IMAGE, "grade": 2}]}


def test_a_bad_hash_is_a_422_and_a_missing_query_param_too(client_and_service):
    client, service = client_and_service
    service.list.side_effect = RatingValidationError("bad hash")
    assert client.get("/api/meme-from-text/ratings?queryHash=short").status_code == 422
    assert client.get("/api/meme-from-text/ratings").status_code == 422


def test_an_unexpected_error_is_a_generic_500_that_never_contains_the_text(client_and_service, caplog):
    client, service = client_and_service
    service.put.side_effect = RuntimeError("boom SECRET-MESSAGE")
    caplog.set_level(logging.DEBUG)

    response = client.put("/api/meme-from-text/ratings", json={**BODY, "text": "SECRET-MESSAGE"})

    assert response.status_code == 500
    assert "SECRET-MESSAGE" not in response.text and "SECRET-MESSAGE" not in caplog.text
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)
```

- [ ] **Step 4: Implement the service and router**

`Backend/app/services/meme_text_rating_service.py`:

```python
"""Ratings of meme-from-text results (P5b): validation, hashing and storage rules."""
import re
import uuid

from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse
from retrieval.query_hash import normalised_query, query_hash

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RatingValidationError(ValueError):
    """The request is well-formed JSON but not acceptable. The message never contains the user's text."""


class UnknownImageError(LookupError):
    """The image does not exist or is not active."""


def _image_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise RatingValidationError("imageId is not a valid id") from None


def _hash(value: str) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.match(value):
        raise RatingValidationError("queryHash must be 64 lowercase hex characters")
    return value


class MemeTextRatingService:
    def __init__(self, repository, max_chars: int):
        self.repository = repository
        self.max_chars = max_chars

    async def put(self, request: RatingRequest) -> RatingResponse:
        text = normalised_query(request.text, self.max_chars)
        if not text:
            raise RatingValidationError("the message is empty")
        image_id = _image_id(request.imageId)
        if not await self.repository.image_is_active(image_id):
            raise UnknownImageError()
        digest = query_hash(text)
        grade = await self.repository.upsert(
            query_hash=digest, query_text=text, image_id=image_id, grade=request.grade, rank=request.rank,
            reading=request.reading.value if hasattr(request.reading, "value") else request.reading,
            parser_version=request.parserVersion, taxonomy_version=request.taxonomyVersion,
            degraded=request.degraded, fallback=request.fallback,
            language=request.language.value if hasattr(request.language, "value") else request.language)
        return RatingResponse(queryHash=digest, imageId=str(image_id), grade=grade)

    async def delete(self, hash_value: str, image_id: str) -> None:
        await self.repository.delete(_hash(hash_value), _image_id(image_id))

    async def list(self, hash_value: str) -> RatingsResponse:
        digest = _hash(hash_value)
        grades = await self.repository.grades_for(digest)
        return RatingsResponse.model_validate(
            {"queryHash": digest, "ratings": [{"imageId": str(image_id), "grade": grade} for image_id, grade in grades]})

    async def forget_text(self, hash_value: str) -> None:
        await self.repository.forget_text(_hash(hash_value))
```

`Backend/app/api/meme_text_ratings.py`:

```python
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.meme_text_rating_service import (
    MemeTextRatingService, RatingValidationError, UnknownImageError,
)
from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse
from config.settings import settings
from repository.meme_text_ratings import MemeTextRatingRepository
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meme-from-text", tags=["meme-from-text"])


async def get_rating_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[MemeTextRatingService, None]:
    yield MemeTextRatingService(MemeTextRatingRepository(db), int(settings.get("parse.max_input_chars")))


def _fail(error: Exception) -> HTTPException:
    """Map an error without ever echoing the user's text: fixed details only; unexpected ones log the type."""
    if isinstance(error, RatingValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, UnknownImageError):
        return HTTPException(status_code=404, detail="image not found")
    logger.warning("rating request failed: %s", type(error).__name__)
    return HTTPException(status_code=500, detail="rating request failed")


@router.put("/ratings", response_model=RatingResponse)
async def put_rating(request: RatingRequest, service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        return await service.put(request)
    except Exception as error:
        raise _fail(error) from None


@router.delete("/ratings", status_code=204)
async def delete_rating(query_hash: str = Query(alias="queryHash"), image_id: str = Query(alias="imageId"),
                        service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        await service.delete(query_hash, image_id)
    except Exception as error:
        raise _fail(error) from None
    return Response(status_code=204)


@router.get("/ratings", response_model=RatingsResponse)
async def list_ratings(query_hash: str = Query(alias="queryHash"),
                       service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        return await service.list(query_hash)
    except Exception as error:
        raise _fail(error) from None


@router.delete("/ratings/text", status_code=204)
async def forget_rating_text(query_hash: str = Query(alias="queryHash"),
                             service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        await service.forget_text(query_hash)
    except Exception as error:
        raise _fail(error) from None
    return Response(status_code=204)
```

In `Backend/app/main.py` import `from Backend.app.api.meme_text_ratings import router as meme_text_ratings_router` and add `app.include_router(meme_text_ratings_router, prefix="/api")` directly after the `meme_from_text_router` line.

- [ ] **Step 5: Document**

In `backend_api.md` (same heading level and bullet style as the `POST /api/meme-from-text/search` section; check its real style first) add after the taxonomy section a section for the four rating calls: request fields (every field of `ratingrequest.schema.json`; `text` up to 8,000 characters, normalised and cut at 600 like the search; the hash is derived by the server and returned as `queryHash`), responses (`200` rating, `204` deletes, `404` unknown or inactive image, `422` validation with a fixed detail, `500` generic), the upsert rule (one rating per message and image), the text-clearing behaviour (grades and ids stay; rating again stores the text again), that ratings never affect ranking, and that the text is stored only because a rating exists and is never logged. In the search section's response description add `meta.queryHash`.

- [ ] **Step 6: Run the tests**

Each as its own command: `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q` (whole Backend root); `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/retrieval -q`; `cd Frontend/memes-frontend && pnpm exec tsc -b; echo "exit $?"` (the regenerated `all.d.ts` must compile). Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add retrieval shared/schemas Backend/app Backend/tests tests/retrieval backend_api.md Frontend/memes-frontend/src/types/generated AndroidClient
git commit -m "feat(ratings): add the ratings API, meta.queryHash, shared schemas and docs"
```

---

### Task 3: Frontend API layer

**Files:**
- Modify: `Frontend/memes-frontend/src/reply/types.ts`, `src/reply/fixtures.ts`, `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`, `src/test/mockApi.ts`
- Create: `src/api/http/HttpMemesApi.ratings.test.ts`

**Interfaces:**
- Consumes: Task 2's generated `RatingRequest`, `RatingResponse`, `RatingsResponse`, `RatingItem`.
- Produces: `types.ts`: re-exports of those four, `Grade = 0 | 1 | 2`, `GRADE_CHOICES` (`[{grade: 2, label: "Good fit"}, {grade: 1, label: "Acceptable"}, {grade: 0, label: "Not a fit"}]`, readonly), `RatingContext = Omit<RatingRequest, "imageId" | "grade">`; `MemesApi.putRating(request): Promise<RatingResponse>`, `deleteRating(queryHash, imageId): Promise<void>`, `getRatings(queryHash): Promise<RatingsResponse>`, `forgetRatingText(queryHash): Promise<void>`; `errors.ts`: `RatingFailedError(status)` (fixed message, no text); fixtures `makeResponse` meta gains `queryHash: "a".repeat(64)`; `mockApi` defaults for the four methods.

- [ ] **Step 1: Write the failing tests**

Create `src/api/http/HttpMemesApi.ratings.test.ts` (fetch mocked like `HttpMemesApi.memeFromText.test.ts`: copy its `stubFetch` helper; a response for 204 has no `json`):

```ts
import { HttpMemesApi } from "./HttpMemesApi"
import { RatingFailedError } from "../errors"

const api = new HttpMemesApi("http://backend")
const HASH = "a".repeat(64)

function stubFetch(result: { ok: boolean; status: number; body?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: result.ok, status: result.status, json: async () => result.body,
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

const request = {
  text: "hello", imageId: "img-1", grade: 2, rank: 1, reading: "parsed" as const, parserVersion: "p3-x",
  taxonomyVersion: 2, degraded: false, fallback: "none", language: "en" as const,
}

describe("ratings API", () => {
  it("puts a rating as JSON and returns the stored rating", async () => {
    const body = { queryHash: HASH, imageId: "img-1", grade: 2 }
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    expect(await api.putRating(request)).toEqual(body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/ratings")
    expect(init.method).toBe("PUT")
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" })
    expect(JSON.parse(init.body)).toEqual(request)
  })

  it("deletes one rating by hash and image id", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await api.deleteRating(HASH, "img 1")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`http://backend/api/meme-from-text/ratings?queryHash=${HASH}&imageId=img%201`)
    expect(init.method).toBe("DELETE")
  })

  it("lists the grades of a message", async () => {
    const body = { queryHash: HASH, ratings: [{ imageId: "img-1", grade: 1 }] }
    const fetchMock = stubFetch({ ok: true, status: 200, body })
    expect(await api.getRatings(HASH)).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe(`http://backend/api/meme-from-text/ratings?queryHash=${HASH}`)
  })

  it("forgets the stored text of a message", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await api.forgetRatingText(HASH)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`http://backend/api/meme-from-text/ratings/text?queryHash=${HASH}`)
    expect(init.method).toBe("DELETE")
  })

  it.each([
    ["putRating", () => api.putRating(request)],
    ["deleteRating", () => api.deleteRating(HASH, "img-1")],
    ["getRatings", () => api.getRatings(HASH)],
    ["forgetRatingText", () => api.forgetRatingText(HASH)],
  ])("%s maps a failure to a fixed-message error with the status and no text", async (_name, call) => {
    stubFetch({ ok: false, status: 500, body: { detail: "rating request failed" } })
    const error = await call().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(RatingFailedError)
    expect((error as RatingFailedError).status).toBe(500)
    expect((error as Error).message).not.toContain("hello")
  })
})
```

Run: `pnpm exec vitest run src/api/http/HttpMemesApi.ratings.test.ts` -> FAIL (methods missing).

- [ ] **Step 2: Implement**

`src/api/errors.ts` add:

```ts
/** A rating call failed. The message is fixed text: it never contains the user's message. */
export class RatingFailedError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Rating request failed (HTTP ${status})`)
    this.name = "RatingFailedError"
    this.status = status
  }
}
```

`src/reply/types.ts` add (and extend the type re-export line with `RatingItem, RatingRequest, RatingResponse, RatingsResponse` imported from `../types/generated/all`):

```ts
export type Grade = 0 | 1 | 2

/** Buttons of the rating bar, best first; the grade is the gold-set grade it maps to. */
export const GRADE_CHOICES = [
  { grade: 2, label: "Good fit" },
  { grade: 1, label: "Acceptable" },
  { grade: 0, label: "Not a fit" },
] as const satisfies readonly { grade: Grade; label: string }[]

/** Everything a rating records about the search besides which meme and which grade. */
export type RatingContext = Omit<RatingRequest, "imageId" | "grade">
```

`src/api/MemesApi.ts`: add `RatingRequest, RatingResponse, RatingsResponse` to the type import and the four methods to the interface (`putRating`, `deleteRating`, `getRatings`, `forgetRatingText`, signatures above). `src/api/http/HttpMemesApi.ts`: import `RatingFailedError` and the three types; implement:

```ts
  async putRating(request: RatingRequest): Promise<RatingResponse> {
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings`, {
      method: "PUT",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    })
    if (!response.ok) throw new RatingFailedError(response.status)
    return response.json()
  }

  async deleteRating(queryHash: string, imageId: string): Promise<void> {
    const params = new URLSearchParams({ queryHash, imageId })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings?${params.toString()}`, { method: "DELETE" })
    if (!response.ok) throw new RatingFailedError(response.status)
  }

  async getRatings(queryHash: string): Promise<RatingsResponse> {
    const params = new URLSearchParams({ queryHash })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings?${params.toString()}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new RatingFailedError(response.status)
    return response.json()
  }

  async forgetRatingText(queryHash: string): Promise<void> {
    const params = new URLSearchParams({ queryHash })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings/text?${params.toString()}`, { method: "DELETE" })
    if (!response.ok) throw new RatingFailedError(response.status)
  }
```

(The brief's URL test expects `img%201` for a space: `URLSearchParams` encodes a space as `+`; if so, assert `imageId=img+1` instead, keeping the intent that the value is URL-encoded.) `src/reply/fixtures.ts`: add `queryHash: "a".repeat(64)` to `makeResponse`'s `meta`. `src/test/mockApi.ts`: add defaults before `...overrides`: `putRating: vi.fn().mockResolvedValue({ queryHash: "a".repeat(64), imageId: "img", grade: 2 })`, `deleteRating: vi.fn().mockResolvedValue(undefined)`, `getRatings: vi.fn().mockResolvedValue({ queryHash: "a".repeat(64), ratings: [] })`, `forgetRatingText: vi.fn().mockResolvedValue(undefined)`.

- [ ] **Step 3: Gates and commit**

`pnpm exec vitest run src/api` (PASS), then the three gates (`tsc -b` with exit code, `eslint src/ --max-warnings 0`, whole `vitest run`).

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): add the ratings API methods, error type and fixtures"
```

---

### Task 4: `useRatings`, `RatingBar`, `ForgetTextButton`, `ResultCard`

**Files:**
- Create: `src/reply/useRatings.ts`, `src/reply/useRatings.test.ts`, `src/components/reply/RatingBar.tsx`, `RatingBar.test.tsx`, `ForgetTextButton.tsx`, `ForgetTextButton.test.tsx`
- Modify: `src/components/reply/ResultCard.tsx`, `ResultCard.test.tsx`

**Interfaces:**
- Consumes: Task 3 (`Grade`, `GRADE_CHOICES`, `RatingContext`, the four `MemesApi` methods).
- Produces: `useRatings(memesApi, queryHash: string | null)` returning `{ grades: Record<string, Grade>, hasRatings: boolean, lastEvent: RatingEvent | null, rate(context: RatingContext, imageId: string, grade: Grade | null): void, forgetText(): Promise<boolean> }` with `RatingEvent = { imageId: string; kind: "saved" | "cleared" | "failed" }`; `RatingBar({ grade, event, onRate })` with `event: "saved" | "cleared" | "failed" | null`; `ForgetTextButton({ onConfirm })`; `ResultCard` gains the optional prop `rating?: { grade: Grade | null; event: RatingBarEvent; onRate: (grade: Grade | null) => void }` (nothing renders without it).

- [ ] **Step 1: Write the failing tests**

`src/reply/useRatings.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import { useRatings } from "./useRatings"
import { makeMockApi } from "../test/mockApi"
import type { RatingContext } from "./types"

const HASH = "a".repeat(64)
const CONTEXT: RatingContext = {
  text: "hello", rank: 2, reading: "parsed", parserVersion: "p3-x", taxonomyVersion: 2, degraded: false,
  fallback: "none", language: "en",
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe("useRatings", () => {
  it("loads the grades of the message and reports whether any exist", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades).toEqual({ a: 1 }))
    expect(result.current.hasRatings).toBe(true)
    expect(api.getRatings).toHaveBeenCalledWith(HASH)
  })

  it("does nothing without a query hash", () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRatings(api, null))
    act(() => result.current.rate(CONTEXT, "a", 2))
    expect(api.getRatings).not.toHaveBeenCalled()
    expect(api.putRating).not.toHaveBeenCalled()
    expect(result.current.hasRatings).toBe(false)
  })

  it("a failed load is silent and leaves the bar empty", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())
    expect(result.current.grades).toEqual({})
  })

  it("rates optimistically, sends the full request and announces 'saved'", async () => {
    const put = deferred<unknown>()
    const api = makeMockApi({ putRating: vi.fn().mockReturnValue(put.promise) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => result.current.rate(CONTEXT, "a", 2))
    expect(result.current.grades.a).toBe(2)                      // optimistic, before the server answered
    await act(async () => { put.resolve({ queryHash: HASH, imageId: "a", grade: 2 }) })

    expect(api.putRating).toHaveBeenCalledWith({ ...CONTEXT, imageId: "a", grade: 2 })
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "saved" })
    expect(result.current.grades.a).toBe(2)
  })

  it("clearing deletes the rating and announces 'cleared'", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 2 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades.a).toBe(2))

    await act(async () => { result.current.rate(CONTEXT, "a", null) })

    expect(api.deleteRating).toHaveBeenCalledWith(HASH, "a")
    expect(result.current.grades.a).toBeUndefined()
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "cleared" })
  })

  it("a failed save rolls back to the last confirmed grade and reports 'failed'", async () => {
    const api = makeMockApi({
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }),
      putRating: vi.fn().mockRejectedValue(new Error("down")),
    })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades.a).toBe(1))

    await act(async () => { result.current.rate(CONTEXT, "a", 2) })

    expect(result.current.grades.a).toBe(1)
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "failed" })
  })

  it("rapid clicks on one image are sent in order and the last click wins", async () => {
    const first = deferred<unknown>()
    const put = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ queryHash: HASH, imageId: "a", grade: 0 })
    const api = makeMockApi({ putRating: put })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => { result.current.rate(CONTEXT, "a", 2); result.current.rate(CONTEXT, "a", 0) })
    expect(put).toHaveBeenCalledTimes(1)                          // the second waits for the first
    await act(async () => { first.resolve({ queryHash: HASH, imageId: "a", grade: 2 }) })

    expect(put.mock.calls.map((call) => call[0].grade)).toEqual([2, 0])
    expect(result.current.grades.a).toBe(0)
  })

  it("a failure of an earlier click does not undo a later successful one", async () => {
    const first = deferred<unknown>()
    const put = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ queryHash: HASH, imageId: "a", grade: 0 })
    const api = makeMockApi({ putRating: put })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => { result.current.rate(CONTEXT, "a", 2); result.current.rate(CONTEXT, "a", 0) })
    await act(async () => { first.reject(new Error("down")) })

    expect(result.current.grades.a).toBe(0)
  })

  it("a new message starts with an empty map and loads its own grades", async () => {
    const api = makeMockApi({
      getRatings: vi.fn().mockImplementation(async (hash: string) => ({
        queryHash: hash, ratings: hash === HASH ? [{ imageId: "a", grade: 2 }] : [],
      })),
    })
    const { result, rerender } = renderHook(({ hash }) => useRatings(api, hash), { initialProps: { hash: HASH as string | null } })
    await waitFor(() => expect(result.current.grades.a).toBe(2))

    rerender({ hash: "b".repeat(64) })

    expect(result.current.grades).toEqual({})
    await waitFor(() => expect(api.getRatings).toHaveBeenCalledWith("b".repeat(64)))
  })

  it("forgetting the text calls the API and reports success or failure", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRatings(api, HASH))
    let ok = false
    await act(async () => { ok = await result.current.forgetText() })
    expect(ok).toBe(true)
    expect(api.forgetRatingText).toHaveBeenCalledWith(HASH)

    ;(api.forgetRatingText as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("down"))
    await act(async () => { ok = await result.current.forgetText() })
    expect(ok).toBe(false)
  })

  it("loads the grades under StrictMode", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH), { wrapper: StrictMode })
    await waitFor(() => expect(result.current.grades).toEqual({ a: 1 }))
  })
})
```

`src/components/reply/RatingBar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import RatingBar from "./RatingBar"

describe("RatingBar", () => {
  it("renders a labelled group with three toggle buttons", () => {
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const group = screen.getByRole("group", { name: "Rate this result" })
    expect(group).toBeInTheDocument()
    for (const label of ["Good fit", "Acceptable", "Not a fit"]) {
      expect(screen.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false")
    }
  })

  it("marks the active grade pressed", () => {
    render(<RatingBar grade={1} event={null} onRate={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Acceptable" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "false")
  })

  it("reports the clicked grade, and null when the active one is clicked again", async () => {
    const user = userEvent.setup()
    const onRate = vi.fn()
    const { rerender } = render(<RatingBar grade={null} event={null} onRate={onRate} />)
    await user.click(screen.getByRole("button", { name: "Good fit" }))
    expect(onRate).toHaveBeenLastCalledWith(2)
    rerender(<RatingBar grade={2} event={null} onRate={onRate} />)
    await user.click(screen.getByRole("button", { name: "Good fit" }))
    expect(onRate).toHaveBeenLastCalledWith(null)
    await user.click(screen.getByRole("button", { name: "Not a fit" }))
    expect(onRate).toHaveBeenLastCalledWith(0)
  })

  it("keeps focus on the clicked button", async () => {
    const user = userEvent.setup()
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const button = screen.getByRole("button", { name: "Acceptable" })
    await user.click(button)
    expect(button).toHaveFocus()
  })

  it("announces saved and cleared in an always-mounted status element", () => {
    const { rerender } = render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const status = screen.getByRole("status")
    expect(status).toBeEmptyDOMElement()
    rerender(<RatingBar grade={2} event="saved" onRate={vi.fn()} />)
    expect(screen.getByRole("status")).toBe(status)
    expect(status).toHaveTextContent("Saved: Good fit")
    rerender(<RatingBar grade={null} event="cleared" onRate={vi.fn()} />)
    expect(status).toHaveTextContent("Rating cleared")
  })

  it("shows an alert when saving failed", () => {
    render(<RatingBar grade={null} event="failed" onRate={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't save the rating")
  })

  it("has touch targets of at least 44 px", () => {
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    for (const button of screen.getAllByRole("button")) expect(button.className).toContain("min-h-11")
  })
})
```

`src/components/reply/ForgetTextButton.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ForgetTextButton from "./ForgetTextButton"

describe("ForgetTextButton", () => {
  it("asks for confirmation, explains the effect, and only then calls onConfirm", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockResolvedValue(true)
    render(<ForgetTextButton onConfirm={onConfirm} />)

    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByText(/rating again stores the text again/i)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole("status")).toHaveTextContent("Text forgotten")
  })

  it("can be cancelled", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(<ForgetTextButton onConfirm={onConfirm} />)
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Forget this message's text" })).toBeInTheDocument()
  })

  it("reports a failure", async () => {
    const user = userEvent.setup()
    render(<ForgetTextButton onConfirm={vi.fn().mockResolvedValue(false)} />)
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't forget the text")
  })
})
```

Add to `ResultCard.test.tsx` (read the file for its `setup` helper; extend it to accept `rating`):

```tsx
  it("shows the rating bar only when rating props are given, and wires clicks to onRate", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<ResultCard result={makeResult("a")} memesApi={makeMockApi()} labelOf={labelOf} onOpen={vi.fn()} />)
    expect(screen.queryByRole("group", { name: "Rate this result" })).not.toBeInTheDocument()

    const onRate = vi.fn()
    rerender(<ResultCard result={makeResult("a")} memesApi={makeMockApi()} labelOf={labelOf} onOpen={vi.fn()}
      rating={{ grade: 1, event: null, onRate }} />)
    expect(screen.getByRole("button", { name: "Acceptable" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "Good fit" }))
    expect(onRate).toHaveBeenCalledWith(2)
  })
```

Run `pnpm exec vitest run src/reply/useRatings.test.ts src/components/reply` -> FAIL (modules missing).

- [ ] **Step 2: Implement**

`src/reply/useRatings.ts`:

```ts
import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { Grade, RatingContext } from "./types"

export type RatingEvent = { imageId: string; kind: "saved" | "cleared" | "failed" }

type Loaded = { hash: string | null; grades: Record<string, Grade> }

export function useRatings(memesApi: MemesApi, queryHash: string | null) {
  const [loaded, setLoaded] = useState<Loaded>({ hash: null, grades: {} })
  const [lastEvent, setLastEvent] = useState<RatingEvent | null>(null)
  // Grades of the message on screen; a different message starts empty (derived, no setState in an effect).
  const grades = loaded.hash === queryHash ? loaded.grades : {}

  const confirmed = useRef<Record<string, Grade | null>>({})
  const chains = useRef(new Map<string, Promise<void>>())
  const hashRef = useRef(queryHash)

  useEffect(() => {
    hashRef.current = queryHash
    confirmed.current = {}
    chains.current = new Map()
    if (!queryHash) return
    let cancelled = false
    memesApi.getRatings(queryHash).then(
      (response) => {
        if (cancelled) return
        const fetched: Record<string, Grade> = {}
        for (const item of response.ratings) fetched[item.imageId] = item.grade as Grade
        for (const [imageId, grade] of Object.entries(fetched)) confirmed.current[imageId] = grade
        // grades the user set while the load was in flight win over the fetched ones
        setLoaded((previous) => ({
          hash: queryHash, grades: { ...fetched, ...(previous.hash === queryHash ? previous.grades : {}) },
        }))
      },
      () => { /* a failed load is silent: the bar starts empty */ },
    )
    return () => { cancelled = true }
  }, [memesApi, queryHash])

  function setGrade(hash: string, imageId: string, grade: Grade | null) {
    setLoaded((previous) => {
      const base = previous.hash === hash ? previous.grades : {}
      const next = { ...base }
      if (grade === null) delete next[imageId]
      else next[imageId] = grade
      return { hash, grades: next }
    })
  }

  function rate(context: RatingContext, imageId: string, grade: Grade | null) {
    const hash = queryHash
    if (!hash) return
    setGrade(hash, imageId, grade)
    const run = async () => {
      if (hashRef.current !== hash) return                      // the message changed while this click waited
      try {
        if (grade === null) await memesApi.deleteRating(hash, imageId)
        else await memesApi.putRating({ ...context, imageId, grade })
        confirmed.current[imageId] = grade
        if (hashRef.current === hash) {
          setGrade(hash, imageId, grade)
          setLastEvent({ imageId, kind: grade === null ? "cleared" : "saved" })
        }
      } catch {
        if (hashRef.current === hash) {
          setGrade(hash, imageId, confirmed.current[imageId] ?? null)
          setLastEvent({ imageId, kind: "failed" })
        }
      }
    }
    const previous = chains.current.get(imageId) ?? Promise.resolve()
    chains.current.set(imageId, previous.then(run))
  }

  async function forgetText(): Promise<boolean> {
    if (!queryHash) return false
    try {
      await memesApi.forgetRatingText(queryHash)
      return true
    } catch {
      return false
    }
  }

  return { grades, hasRatings: Object.keys(grades).length > 0, lastEvent, rate, forgetText }
}
```

`src/components/reply/RatingBar.tsx`:

```tsx
import type { Grade } from "../../reply/types"
import { GRADE_CHOICES } from "../../reply/types"

export type RatingBarEvent = "saved" | "cleared" | "failed" | null

type Props = {
  grade: Grade | null
  event: RatingBarEvent
  onRate: (grade: Grade | null) => void
}

export default function RatingBar({ grade, event, onRate }: Props) {
  const active = GRADE_CHOICES.find((choice) => choice.grade === grade)
  const announcement = event === "saved" && active ? `Saved: ${active.label}` : event === "cleared" ? "Rating cleared" : ""

  return (
    <div role="group" aria-label="Rate this result" className="mt-2 flex flex-wrap items-center gap-2 text-sm">
      {GRADE_CHOICES.map((choice) => (
        <button
          key={choice.grade}
          type="button"
          aria-pressed={grade === choice.grade}
          onClick={() => onRate(grade === choice.grade ? null : choice.grade)}
          className={`min-h-11 rounded-md border px-3 ${grade === choice.grade ? "border-blue-600 bg-blue-600 font-medium text-white" : "bg-white"}`}
        >
          {choice.label}
        </button>
      ))}
      <span role="status" className="sr-only">{announcement}</span>
      {event === "failed" && <span role="alert" className="text-red-700">Couldn't save the rating</span>}
    </div>
  )
}
```

`src/components/reply/ForgetTextButton.tsx`:

```tsx
import { useState } from "react"

type Props = {
  onConfirm: () => Promise<boolean>
}

type Phase = "idle" | "confirming" | "working" | "done" | "failed"

export default function ForgetTextButton({ onConfirm }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")

  async function confirm() {
    setPhase("working")
    setPhase((await onConfirm()) ? "done" : "failed")
  }

  if (phase === "confirming" || phase === "working") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>This clears the stored text of this message; its ratings stay. Rating again stores the text again.</span>
        <button type="button" disabled={phase === "working"} onClick={confirm} className="min-h-11 rounded border px-3">Confirm</button>
        <button type="button" onClick={() => setPhase("idle")} className="min-h-11 rounded border px-3">Cancel</button>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <button type="button" onClick={() => setPhase("confirming")} className="min-h-11 text-blue-600 hover:underline">
        Forget this message's text
      </button>
      <span role="status">{phase === "done" ? "Text forgotten" : ""}</span>
      {phase === "failed" && <span role="alert" className="text-red-700">Couldn't forget the text</span>}
    </div>
  )
}
```

`src/components/reply/ResultCard.tsx`: add `import RatingBar from "./RatingBar"` and `import type { RatingBarEvent } from "./RatingBar"` and `Grade`; add the prop `rating?: { grade: Grade | null; event: RatingBarEvent; onRate: (grade: Grade | null) => void }`, and render directly after the existing badge/button row (before the explanation panel): `{rating && <RatingBar grade={rating.grade} event={rating.event} onRate={rating.onRate} />}`.

- [ ] **Step 3: Gates and commit**

`pnpm exec vitest run src/reply src/components/reply` (PASS), then the three gates. If the hook violates a React Compiler rule (for example refs written in the effect are fine, reads during render are not), restructure without changing behaviour.

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): add the ratings hook, the rating bar and the forget-text action"
```

---

### Task 5: Wire the page

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/ReplyPage.tsx`, `src/pages/ReplyPage.test.tsx`

**Interfaces:**
- Consumes: Task 4; the existing `useMessageSearch` state (`state.submitted`, `state.editedProfile`, `state.meta`, `state.fallback`, `state.status`, `state.items`).
- Produces: each `ResultCard` receives `rating={{ grade, event, onRate }}` with `rank = index + 1`; `ForgetTextButton` in the results header when `ratings.hasRatings`.

- [ ] **Step 1: Write the failing page tests** (add to `ReplyPage.test.tsx`, reusing its helpers `renderPage`, `search`, `calls`; read the file first)

```tsx
const HASH = "a".repeat(64)

function ratedResponse(extra = {}) {
  return makeResponse({ items: [makeResult("a"), makeResult("b")], ...extra })
}

describe("ReplyPage ratings", () => {
  it("rates a result with the text that was searched, its rank and the parsed reading", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user, "my manager moved the meeting")
    await screen.findAllByRole("group", { name: "Rate this result" })

    const groups = screen.getAllByRole("group", { name: "Rate this result" })
    await user.click(within(groups[1]).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(1))
    expect(api.putRating).toHaveBeenCalledWith(expect.objectContaining({
      text: "my manager moved the meeting", imageId: "b", grade: 2, rank: 2, reading: "parsed",
      parserVersion: "p3-test", taxonomyVersion: 2, degraded: false, fallback: "none", language: "en",
    }))
    expect(within(groups[1]).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "true")
  })

  it("uses the submitted text, not the box's current text, and marks an edited reading", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user, "first message")
    await screen.findAllByRole("group", { name: "Rate this result" })
    await user.type(screen.getByLabelText("Message to reply to"), " and more typed afterwards")
    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(screen.getByText("Edited")).toBeInTheDocument())

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))

    await waitFor(() => expect(api.putRating).toHaveBeenCalled())
    expect(api.putRating).toHaveBeenCalledWith(expect.objectContaining({ text: "first message", reading: "edited", grade: 1 }))
  })

  it("shows grades loaded for the message and keeps them after a refine", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 0 }] }),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(first).getByRole("button", { name: "Not a fit" })).toHaveAttribute("aria-pressed", "true"))
    expect(api.getRatings).toHaveBeenCalledWith(HASH)

    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(calls(api).length).toBeGreaterThanOrEqual(2), { timeout: 2000 })

    const again = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    expect(within(again).getByRole("button", { name: "Not a fit" })).toHaveAttribute("aria-pressed", "true")
  })

  it("clicking the active grade clears the rating", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 2 }] }),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(first).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "true"))

    await user.click(within(first).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(api.deleteRating).toHaveBeenCalledWith(HASH, "a"))
  })

  it("shows an inline error and rolls back when saving fails", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      putRating: vi.fn().mockRejectedValue(new Error("down")),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await user.click(within(first).getByRole("button", { name: "Good fit" }))

    expect(await within(first).findByRole("alert")).toHaveTextContent("Couldn't save the rating")
    expect(within(first).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "false")
  })

  it("offers to forget the text only once a message has ratings, and forgets it after confirmation", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [] }),
    }))
    await search(user)
    await screen.findAllByRole("group", { name: "Rate this result" })
    expect(screen.queryByRole("button", { name: "Forget this message's text" })).not.toBeInTheDocument()

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))
    await user.click(await screen.findByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Confirm" }))

    await waitFor(() => expect(api.forgetRatingText).toHaveBeenCalledWith(HASH))
  })

  it("shows no rating bar when the response carries no query hash or the page is unavailable", async () => {
    const user = userEvent.setup()
    const response = ratedResponse()
    delete (response.meta as { queryHash?: string }).queryHash
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(response) }))
    await search(user)
    await screen.findAllByRole("img", { name: /^[ab]$/ })
    expect(screen.queryByRole("group", { name: "Rate this result" })).not.toBeInTheDocument()
  })
})
```

(`makeResponse` already sets `meta.queryHash` to 64 `a`s, so `HASH` matches.) Run `pnpm exec vitest run src/pages/ReplyPage.test.tsx` -> the new tests FAIL.

- [ ] **Step 2: Implement** in `src/pages/ReplyPage.tsx`:

```tsx
import ForgetTextButton from "../components/reply/ForgetTextButton"
import { useRatings } from "../reply/useRatings"
import type { RatingContext } from "../reply/types"
```

inside the component, after `useTaxonomy`:

```tsx
  const queryHash = state.meta?.queryHash ?? null
  const ratings = useRatings(memesApi, state.status === "unavailable" ? null : queryHash)

  const ratingContext = (rank: number): RatingContext => ({
    text: state.submitted ?? "",
    rank,
    reading: state.editedProfile !== null ? "edited" : "parsed",
    parserVersion: state.meta?.parserVersion ?? "",
    taxonomyVersion: profile?.taxonomyVersion ?? 0,
    degraded: state.fallback !== "none",
    fallback: state.fallback,
    language: profile?.language ?? "other",
  })
```

in the results list, pass the new prop to each card (`state.items.map((result, index) => ...)`):

```tsx
                  rating={queryHash ? {
                    grade: ratings.grades[result.meme.id] ?? null,
                    event: ratings.lastEvent?.imageId === result.meme.id ? ratings.lastEvent.kind : null,
                    onRate: (grade) => ratings.rate(ratingContext(index + 1), result.meme.id, grade),
                  } : undefined}
```

and, directly above the results grid (after `ActiveFilters`), `{ratings.hasRatings && <div className="mb-3"><ForgetTextButton onConfirm={ratings.forgetText} /></div>}`.

- [ ] **Step 3: Gates and commit**

`pnpm exec vitest run src/pages src/reply src/components/reply`, then the four gates (`tsc -b` with exit code, `eslint`, whole `vitest run`, `vite build`).

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): rate results on the /reply page and forget a message's text"
```

---

### Task 6: Evaluation export

**Files:**
- Create: `evaluation/ratings_export.py`, `tests/evaluation/test_ratings_export.py`
- Modify: `evaluation/__main__.py`, `tests/evaluation/test_cli.py`, `evaluation/gold/README.md`

**Interfaces:**
- Consumes: Task 1's `RatingRow` / `all_ratings()`, Task 2's `retrieval.query_hash`, `evaluation.gold` (`GoldQuery`, `Judgment`, `load_queries`, `load_judgments`, `load_corpus`, `dump_json`).
- Produces: `evaluation.ratings_export.build_export(ratings, queries, judgments, corpus_image_ids, *, max_chars, round_number, today, rater="rating-ui") -> ExportResult` with `judgments_by_language: dict[str, dict]` (each value a sheet-export-shaped dict `{rater, date, round, language, judgments: [{queryId, imageId, grade}]}`), `candidates: dict` (`{"version": 1, "candidates": [...]}`), `report: dict`; CLI `python -m evaluation export-ratings --round N [--out DIR] [--force]`.

- [ ] **Step 1: Write the failing tests** (`tests/evaluation/test_ratings_export.py`; read `tests/evaluation/` for how existing tests build `GoldQuery` and `ExpectedProfile`, and reuse that helper style)

```python
import uuid

from evaluation.gold import ExpectedProfile, GoldQuery, Judgment
from evaluation.ratings_export import build_export
from repository.meme_text_ratings import RatingRow
from retrieval.query_hash import normalised_query, query_hash

IMG_A, IMG_B, IMG_C = (str(uuid.uuid4()) for _ in range(3))


def _query(query_id, text, language="en"):
    return GoldQuery(query_id, language, text, "short", "easy", ExpectedProfile("s", (), (), (), ()), True)


def _h(text):
    return query_hash(normalised_query(text, 600))


def _rating(text, image, grade, *, rank=1, language="en", reading="parsed", degraded=False, text_kept=True, parser="p3-x"):
    return RatingRow(_h(text), text if text_kept else None, uuid.UUID(image), grade, rank, reading, parser, 2, degraded,
                     "none", language, "2026-10-09T10:00:00")


def _build(ratings, queries=(), judgments=(), corpus=(IMG_A, IMG_B, IMG_C)):
    return build_export(ratings, list(queries), list(judgments), set(corpus), max_chars=600, round_number=3,
                        today="2026-10-09")


def test_ratings_of_a_known_gold_query_become_import_ready_judgments_per_language():
    result = _build([_rating("Guess who got the promotion!", IMG_A, 2), _rating("Mi jefe", IMG_B, 1, language="es")],
                    queries=[_query("q001", "Guess who got the promotion!"), _query("q021", "Mi jefe", "es")])

    en = result.judgments_by_language["en"]
    assert (en["rater"], en["date"], en["round"], en["language"]) == ("rating-ui", "2026-10-09", 3, "en")
    assert en["judgments"] == [{"queryId": "q001", "imageId": IMG_A, "grade": 2}]
    assert result.judgments_by_language["es"]["judgments"] == [{"queryId": "q021", "imageId": IMG_B, "grade": 1}]
    assert result.candidates["candidates"] == []


def test_matching_ignores_whitespace_and_case_of_normalisation_only():
    result = _build([_rating("  Guess   who got the promotion! ", IMG_A, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language["en"]["judgments"][0]["queryId"] == "q001"
    result = _build([_rating("guess who got the promotion!", IMG_A, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language == {} and len(result.candidates["candidates"]) == 1


def test_an_already_judged_pair_is_excluded_and_reported_with_both_grades():
    existing = [Judgment("q001", IMG_A, 1, "ramiz", "2026-10-04", 1)]
    result = _build([_rating("Guess who got the promotion!", IMG_A, 2), _rating("Guess who got the promotion!", IMG_B, 0)],
                    queries=[_query("q001", "Guess who got the promotion!")], judgments=existing)

    assert result.judgments_by_language["en"]["judgments"] == [{"queryId": "q001", "imageId": IMG_B, "grade": 0}]
    assert result.report["alreadyJudged"] == [
        {"queryId": "q001", "imageId": IMG_A, "existingGrade": 1, "ratingGrade": 2, "agree": False}]


def test_an_image_outside_the_frozen_corpus_is_excluded_and_listed():
    outside = str(uuid.uuid4())
    result = _build([_rating("Guess who got the promotion!", outside, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language == {}
    assert result.report["outsideCorpus"] == [{"queryId": "q001", "imageId": outside, "grade": 2}]


def test_new_messages_go_to_the_candidates_file_grouped_by_message():
    result = _build([
        _rating("A brand new message", IMG_A, 2, rank=1), _rating("A brand new message", IMG_B, 0, rank=4, reading="edited"),
        _rating("Another one", IMG_C, 1, text_kept=False),
    ])
    candidates = {c["queryHash"]: c for c in result.candidates["candidates"]}
    first = candidates[_h("A brand new message")]
    assert first["text"] == "A brand new message" and first["language"] == "en"
    assert [(r["imageId"], r["grade"], r["rank"], r["reading"]) for r in first["ratings"]] == [
        (IMG_A, 2, 1, "parsed"), (IMG_B, 0, 4, "edited")]
    assert candidates[_h("Another one")]["text"] is None            # the text was forgotten


def test_the_summary_counts_every_rating():
    result = _build([
        _rating("m1", IMG_A, 2), _rating("m1", IMG_B, 0, degraded=True, reading="edited"),
        _rating("m2", IMG_C, 2, language="ru", parser="p3-y"),
    ])
    summary = result.report["summary"]
    assert summary["total"] == 3 and summary["byGrade"] == {"0": 1, "1": 0, "2": 2}
    assert summary["byLanguage"] == {"en": 2, "ru": 1} and summary["byReading"] == {"edited": 1, "parsed": 2}
    assert summary["degraded"] == 1 and summary["byParserVersion"] == {"p3-x": 2, "p3-y": 1}


def test_the_output_is_deterministic_and_sorted():
    ratings = [_rating("Guess who got the promotion!", IMG_B, 1), _rating("Guess who got the promotion!", IMG_A, 2)]
    queries = [_query("q001", "Guess who got the promotion!")]
    first = _build(ratings, queries=queries)
    second = _build(list(reversed(ratings)), queries=queries)
    assert first.judgments_by_language == second.judgments_by_language
    ids = [j["imageId"] for j in first.judgments_by_language["en"]["judgments"]]
    assert ids == sorted(ids)


def test_nothing_to_export_gives_empty_results():
    result = _build([])
    assert result.judgments_by_language == {} and result.candidates["candidates"] == []
    assert result.report["summary"]["total"] == 0
```

CLI tests added to `tests/evaluation/test_cli.py`:

```python
def test_export_ratings_options_and_refusal_without_the_read_only_url(tmp_path, monkeypatch):
    args = cli.build_parser().parse_args(["export-ratings", "--round", "4", "--out", str(tmp_path / "x"), "--force"])
    assert (args.round, args.out, args.force) == (4, str(tmp_path / "x"), True)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr:ocr@localhost/ocrdb")
    with pytest.raises(SystemExit):
        cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "export-ratings", "--round", "4"])


def test_export_ratings_refuses_to_overwrite_an_existing_export(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr_readonly:x@localhost/ocrdb")
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.json").write_text("{}", encoding="utf-8")
    code = cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "export-ratings", "--round", "4", "--out", str(out)])
    assert code == 1 and "already contains an export" in capsys.readouterr().err
```

Run `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/evaluation -q` -> new tests FAIL.

- [ ] **Step 2: Implement** `evaluation/ratings_export.py`:

```python
"""Turn stored ratings into gold-set review files (P5b). Pure: no database, no files, no clock."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from evaluation.gold import GoldQuery, Judgment
from retrieval.query_hash import normalised_query, query_hash

RATER = "rating-ui"


@dataclass(frozen=True)
class ExportResult:
    judgments_by_language: dict[str, dict]
    candidates: dict
    report: dict


def _summary(ratings: Sequence) -> dict:
    return {
        "total": len(ratings),
        "byGrade": {str(grade): sum(1 for r in ratings if r.grade == grade) for grade in (0, 1, 2)},
        "byLanguage": dict(sorted(Counter(r.language for r in ratings).items())),
        "byReading": dict(sorted(Counter(r.reading for r in ratings).items())),
        "degraded": sum(1 for r in ratings if r.degraded),
        "byParserVersion": dict(sorted(Counter(r.parser_version for r in ratings).items())),
    }


def build_export(ratings: Iterable, queries: Sequence[GoldQuery], judgments: Sequence[Judgment],
                 corpus_image_ids: set[str], *, max_chars: int, round_number: int, today: str,
                 rater: str = RATER) -> ExportResult:
    ratings = sorted(ratings, key=lambda r: (r.query_hash, str(r.image_id)))
    gold_by_hash = {query_hash(normalised_query(q.text, max_chars)): q for q in queries}
    existing = {(j.query_id, j.image_id): j.grade for j in judgments}

    importable: dict[str, list[dict]] = defaultdict(list)
    already_judged, outside_corpus = [], []
    unknown: dict[str, list] = defaultdict(list)

    for rating in ratings:
        image_id = str(rating.image_id)
        gold = gold_by_hash.get(rating.query_hash)
        if gold is None:
            unknown[rating.query_hash].append(rating)
        elif image_id not in corpus_image_ids:
            outside_corpus.append({"queryId": gold.id, "imageId": image_id, "grade": rating.grade})
        elif (gold.id, image_id) in existing:
            already = existing[(gold.id, image_id)]
            already_judged.append({"queryId": gold.id, "imageId": image_id, "existingGrade": already,
                                   "ratingGrade": rating.grade, "agree": already == rating.grade})
        else:
            importable[gold.language].append({"queryId": gold.id, "imageId": image_id, "grade": rating.grade})

    judgments_by_language = {
        language: {"rater": rater, "date": today, "round": round_number, "language": language,
                   "judgments": sorted(entries, key=lambda e: (e["queryId"], e["imageId"]))}
        for language, entries in sorted(importable.items())
    }

    candidates = []
    for digest, rows in sorted(unknown.items()):
        text = next((r.query_text for r in rows if r.query_text), None)
        language = Counter(r.language for r in rows).most_common(1)[0][0]
        candidates.append({
            "queryHash": digest, "text": text, "language": language,
            "ratings": [{"imageId": str(r.image_id), "grade": r.grade, "rank": r.rank, "reading": r.reading,
                         "parserVersion": r.parser_version, "degraded": r.degraded} for r in rows],
        })

    report = {
        "summary": _summary(ratings),
        "importable": sum(len(v["judgments"]) for v in judgments_by_language.values()),
        "alreadyJudged": already_judged,
        "outsideCorpus": outside_corpus,
        "candidateMessages": len(candidates),
    }
    return ExportResult(judgments_by_language, {"version": 1, "candidates": candidates}, report)
```

In `evaluation/__main__.py` add (next to the other async commands; the file's existing helpers `require_readonly`, `_error`, `dump_json`, `load_queries`, `load_judgments`, `load_corpus`, `date_class` and the lazy-import convention apply: read it first):

```python
async def cmd_export_ratings(args, gold: Path) -> int:
    """Turn the owner's ratings into review files (never an automatic merge into the frozen gold set)."""
    require_readonly()
    out = Path(args.out)
    if out.exists() and any(out.iterdir()) and not args.force:
        return _error(f"{out} already contains an export (use --force to replace it)")
    from config.settings import settings
    from evaluation.ratings_export import build_export
    from repository.meme_text_ratings import MemeTextRatingRepository
    from Storage.db import AsyncSessionLocal
    max_chars = int(settings.get("parse.max_input_chars", 600))
    async with AsyncSessionLocal() as session:
        ratings = await MemeTextRatingRepository(session).all_ratings()
    result = build_export(ratings, load_queries(gold / "queries.json"), load_judgments(gold / "judgments.json"),
                          set(load_corpus(gold / "corpus.json").image_ids()), max_chars=max_chars,
                          round_number=args.round, today=date_class.today().isoformat())
    out.mkdir(parents=True, exist_ok=True)
    for language, data in result.judgments_by_language.items():
        (out / f"judgments-{language}.json").write_text(dump_json(data), encoding="utf-8", newline="\n")
    (out / "candidate-queries.json").write_text(dump_json(result.candidates), encoding="utf-8", newline="\n")
    (out / "summary.json").write_text(dump_json(result.report), encoding="utf-8", newline="\n")
    summary = result.report["summary"]
    print(f"{summary['total']} ratings: {result.report['importable']} importable judgments in "
          f"{len(result.judgments_by_language)} file(s), {len(result.report['alreadyJudged'])} already judged, "
          f"{len(result.report['outsideCorpus'])} outside the corpus, {result.report['candidateMessages']} new "
          f"message(s); wrote {out}")
    return 0
```

Parser (in `build_parser`) and handler registration: `export = commands.add_parser("export-ratings")` with `--round` (int, required), `--out` (default `evaluation/work/ratings-export`), `--force` (store_true); add `"export-ratings": cmd_export_ratings` to `async_handlers`. (The default `--out` is relative to the working directory; the CLI test passes an explicit one.) The refusal-without-the-read-only-URL test relies on `require_readonly()` running first.

`evaluation/gold/README.md`: add a section "Ratings export (P5b)": what `export-ratings` reads and writes, the three outputs, that `judgments-<lang>.json` are import-ready (`import-judgments judgments-en.json ...`, round `N`), that pairs already judged and images outside the corpus are listed in `summary.json` and excluded, that `candidate-queries.json` is for review only (turning a candidate into a gold query is a deliberate step with an expected profile and a new frozen fingerprint), and that ratings only cover results the owner was shown, so they must not be mixed into metrics without a judging round.

- [ ] **Step 3: Run the tests and commit**

`/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/evaluation -q` (PASS; each root its own command), `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m evaluation export-ratings --help` (exit 0 with no `DATABASE_URL`), and `tests/retrieval -q`.

```bash
git add evaluation tests/evaluation
git commit -m "feat(evaluation): add export-ratings (import-ready judgments, candidate queries, summary)"
```

---

### Task 7: Live check, migration notes, documentation, review report (controller steps)

Controller only (needs a seeded database, Ollama and a browser). **Do not delegate any of it.**

- [ ] **Step 1: Seed the test database and start a minimal stack** (never a real environment): populate `ocrdb_test` with a few profiled synthetic images (the P4 load script's `_populate`/helpers or the repository fixtures), run a minimal FastAPI app (the search, taxonomy and ratings routers, images router, CORS for the dev port, no scheduler) against `ocrdb_test` on a free port with `BASE_PATH` pointing at a folder with a few placeholder images, and the frontend dev server on a free port with `VITE_BACKEND_API_URL` set. Never bind 8081-8083 or 5173-5175.
- [ ] **Step 2: Browser check:** search; rate good fit, acceptable, not a fit; re-rate; click the active grade to clear; reload and re-search (grades return); edit a chip then rate (`reading: edited` in the stored row); forget the text (grades stay, `query_text` null); an unreachable-Ollama backend (degraded flag stored); a failed save (stop the backend, click, see the inline error and the rollback); keyboard-only operation of the bar. Verify the stored rows with a read-only SQL query.
- [ ] **Step 3: Export check:** run `export-ratings` against the seeded database with the read-only role; inspect `judgments-<lang>.json`, `candidate-queries.json` and `summary.json`; confirm `import-judgments` accepts a produced file against a copy of the gold directory.
- [ ] **Step 4: Documentation:** P5b spec addendum (what was built, the live check, deviations listed above, the exact migration commands for metal, general and it, handoff to P5c), tracker row P5b `done`, `CLAUDE.md` (the ratings endpoints, the table, `export-ratings`, the privacy rule), `docs/superpowers/reviews/2026-10-09-p5b-rating-widget-review.md`.
- [ ] **Step 5: Final verification:** every gate (frontend four, Backend root, `tests/retrieval`, `tests/evaluation`, `tests/taxonomy`, the whole `tests/integration`), generated types without diff; commit; wait for the user's word to merge. Tell the user to run `alembic upgrade head` on each environment and restart the backends.

---

## Self-review (plan against spec)

| Spec item | Task |
|---|---|
| Decision 1 table, constraints, upsert key, no effect on retrieval | 1 |
| Decision 2 privacy: text only with a rating, never logged, forget text | 1 (`forget_text`), 2 (router), 4 and 5 (UI) |
| Decision 3 endpoints, `meta.queryHash`, shared schemas, `backend_api.md` | 2 |
| Decision 4 rating bar, optimistic with rollback, per-image ordering, remembered grades, forget-text action | 3, 4, 5 |
| Decision 5 export: gold matching by hash, import-ready files, already-judged and corpus exclusions, candidates, summary, read-only role, README | 6 |
| Testing section | tests in every task; whole integration root in 1; migration verified without any real database |
| Acceptance 1 to 5 | 1 to 6 |
| Acceptance 6 (addendum, live check, migration commands, docs) | 7 |

Type and name consistency checks: `RatingRow`/`MemeTextRatingRepository` (Task 1) are consumed by the service (Task 2) and the export (Task 6); the hash is always `query_hash(normalised_query(text, max_chars))`; `RatingContext = Omit<RatingRequest, "imageId" | "grade">` matches the request fields the page builds; `GRADE_CHOICES` grades equal the gold grades; `RatingBarEvent` is defined in `RatingBar.tsx` and imported by `ResultCard`; the fixtures' `makeResponse` meta hash equals the `HASH` constant of the page tests.
