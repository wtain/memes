# R1 Data Model and Status Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `image_cuts` relation, the `cut_proposed` image status constants, a `stale` marker on four derived tables with a shared helper, the image status registry and the derived-data inventory, each guarded by tests.

**Architecture:** New SQLAlchemy models and constants in `Storage/models.py`, one Alembic revision, a small helper module `Storage/stale.py`, two reference documents under `docs/` kept honest by tests that compare them with the code. No batch job or API behaviour changes.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy 2 async, Alembic, PostgreSQL 18 with pgvector, pytest with `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-17-r1-data-model-and-status-registry-design.md` (epic: `docs/superpowers/specs/2026-10-17-screenshot-recut/README.md`)

## Global Constraints

- `images.status` stays a free `String(20)`; no check constraint on it; existing `"active"` literals elsewhere are not rewritten.
- Repositories and helpers never call `session.commit()` (`get_async_db` owns commit and rollback).
- ORM models live only in `Storage/models.py`.
- The `stale` column is `Boolean NOT NULL DEFAULT false` (`server_default=sa.false()`), on exactly these tables: `ocr_texts`, `ocr_lemmas`, `image_descriptions`, `tags`. No index on it.
- `image_cuts` columns, constraints and indexes are exactly those in spec section 3.1; `source_image_id`, `cut_image_id` and `detect_run_id` use `ON DELETE SET NULL`.
- Live-database rollout (metal, general, IT) happens only with the user's explicit go-ahead; those environments are always running.
- Test commands: never combine test roots in one `pytest` invocation. Integration tests need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`.
- Python for tests and tools is `.venv311/Scripts/python.exe` (repo root as cwd).
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Windows: do not round-trip repo text through PowerShell `Get-Content`/`Set-Content`; use the Edit tool.

## Review Focus

- An empty list of image ids passed to `delete_stale_rows`: returns 0 and issues no statement (Task 3 test).
- Stale rows of an image in another status than requested (for example an `active` image while asking for `cut_proposed`): neither selected nor deleted (Task 3 tests).
- More ids than one statement should carry (thousands of ids): deletion still works through chunking (Task 3 test with a small chunk size).
- Both images of a proposal deleted: the `image_cuts` row survives with both columns null and then still counts as history, not as a live row (Task 1 test).
- A future table with a foreign key to `images` that nobody classified: the inventory test fails and names it (Task 4 test of the comparison function, not only of the real documents).

## File Structure

| File | Responsibility |
|---|---|
| `Storage/models.py` (modify) | `ImageStatus`, `CutState`, `StaleMixin`, `ImageCut`; mixin applied to four models |
| `Storage/alembic/versions/b8d4f1a6c935_add_image_cuts_and_stale_columns.py` (create) | The migration |
| `Storage/stale.py` (create) | `STALE_MODELS`, `images_with_stale_rows`, `delete_stale_rows`, `stale_counts` |
| `docs/image-status.md` (create) | Status registry |
| `docs/image-derived-data.md` (create) | Inventory of everything that hangs off an image |
| `tests/integration/test_image_cuts_model.py` (create) | Constants, `image_cuts` constraints, `stale` defaults |
| `tests/integration/test_image_cuts_migration.py` (create) | Migration matches the model, downgrades, continues the chain |
| `tests/integration/test_stale_helper.py` (create) | Helper behaviour |
| `tests/docs/test_image_status_registry.py` (create) | Registry matches `ImageStatus` |
| `tests/docs/test_image_derived_data_inventory.py` (create) | Inventory matches the metadata |
| `CLAUDE.md`, `docs/batches/inventory.md`, `docs/batches/execution-model.md`, two older specs (modify) | One-line pointers to the registry |

---

### Task 1: Constants, mixin and models

**Files:**
- Modify: `Storage/models.py` (imports are already sufficient; add constants and the mixin after `PROFILE_EMBEDDING_DIM`, add `ImageCut` after the `Image` class's last relationship block, apply the mixin to four classes)
- Test: `tests/integration/test_image_cuts_model.py`

**Interfaces:**
- Produces: `ImageStatus` (`ACTIVE`, `PENDING`, `REJECTED`, `CUT_PROPOSED`, `ALL: tuple[str, ...]`); `CutState` (`DETECTED`, `PROPOSED`, `APPROVED`, `REJECTED`, `FAILED`, `LIVE: tuple`, `ALL: tuple`); `StaleMixin` (column `stale`); `ImageCut` (table `image_cuts`, columns as in spec 3.1); `OCRText`, `OCRLemma`, `ImageDescription`, `ImageTag` gain `stale`.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/recut-r1-data-model
```

- [ ] **Step 2: Write the failing tests**

Create `tests/integration/test_image_cuts_model.py`:

```python
"""R1: status/state constants, the image_cuts relation and the stale marker.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py."""
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from Storage.models import (
    CutState, Image, ImageCut, ImageDescription, ImageStatus, ImageTag, OCRLemma, OCRText, StaleMixin,
)


def test_image_status_constants():
    assert ImageStatus.ACTIVE == "active"
    assert ImageStatus.PENDING == "pending"
    assert ImageStatus.REJECTED == "rejected"
    assert ImageStatus.CUT_PROPOSED == "cut_proposed"
    assert set(ImageStatus.ALL) == {"active", "pending", "rejected", "cut_proposed"}


def test_cut_state_constants():
    assert set(CutState.ALL) == {"detected", "proposed", "approved", "rejected", "failed"}
    assert set(CutState.LIVE) == {"detected", "proposed", "approved"}


def _cut(source_id, cut_id=None, state=CutState.PROPOSED, **box):
    return ImageCut(
        source_image_id=source_id, cut_image_id=cut_id, source_content_hash="h",
        crop_x=box.get("crop_x", 10), crop_y=box.get("crop_y", 20),
        crop_w=box.get("crop_w", 300), crop_h=box.get("crop_h", 200),
        detector="test", detector_version="1", confidence=0.9, state=state,
    )


async def _images(session, count):
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(count)]
    session.add_all(images)
    await session.flush()
    return images


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_live_row_for_one_source_is_rejected(db_session):
    source, cut_a, cut_b = await _images(db_session, 3)
    db_session.add(_cut(source.id, cut_a.id, CutState.PROPOSED))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, cut_b.id, CutState.DETECTED))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejected_row_does_not_block_a_new_live_row(db_session):
    source, cut_a, cut_b = await _images(db_session, 3)
    db_session.add(_cut(source.id, cut_a.id, CutState.REJECTED))
    db_session.add(_cut(source.id, cut_b.id, CutState.PROPOSED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_two_failed_rows_for_one_source_are_allowed(db_session):
    source, = await _images(db_session, 1)
    db_session.add(_cut(source.id, None, CutState.FAILED))
    db_session.add(_cut(source.id, None, CutState.FAILED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cut_image_belongs_to_one_proposal(db_session):
    source_a, source_b, cut = await _images(db_session, 3)
    db_session.add(_cut(source_a.id, cut.id))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source_b.id, cut.id))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("box", [
    {"crop_w": 0}, {"crop_h": 0}, {"crop_w": -5}, {"crop_x": -1}, {"crop_y": -1},
])
async def test_the_box_check_rejects_empty_or_negative_boxes(db_session, box):
    source, = await _images(db_session, 1)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, None, **box))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_state_check_rejects_unknown_states(db_session):
    source, = await _images(db_session, 1)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_cut(source.id, None, "approvd"))
            await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_either_image_keeps_the_proposal(db_session):
    source, cut_image = await _images(db_session, 2)
    cut = _cut(source.id, cut_image.id, CutState.APPROVED)
    db_session.add(cut)
    await db_session.flush()

    await db_session.execute(delete(Image).where(Image.id == source.id))
    await db_session.refresh(cut)
    assert cut.source_image_id is None and cut.cut_image_id == cut_image.id

    await db_session.execute(delete(Image).where(Image.id == cut_image.id))
    await db_session.refresh(cut)
    assert cut.source_image_id is None and cut.cut_image_id is None
    assert cut.state == CutState.APPROVED
    assert cut.source_content_hash == "h"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_proposal_with_both_images_gone_does_not_block_a_new_live_row(db_session):
    """Both ids null: the partial unique index must not treat the history row as live for any source."""
    first, = await _images(db_session, 1)
    db_session.add(_cut(first.id, None, CutState.APPROVED))
    await db_session.flush()
    await db_session.execute(delete(Image).where(Image.id == first.id))
    second, = await _images(db_session, 1)
    db_session.add(_cut(second.id, None, CutState.PROPOSED))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_defaults_are_filled_by_the_database(db_session):
    source, = await _images(db_session, 1)
    cut = _cut(source.id)
    db_session.add(cut)
    await db_session.flush()
    await db_session.refresh(cut)
    assert cut.id is not None and cut.created_at is not None and cut.decided_at is None
    assert cut.detect_run_id is None


def test_the_four_derived_models_carry_the_stale_mixin():
    for model in (OCRText, OCRLemma, ImageDescription, ImageTag):
        assert issubclass(model, StaleMixin), model.__name__


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_defaults_to_false_on_every_marked_table(db_session):
    image, = await _images(db_session, 1)
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t")
    rows = [
        OCRText(image_id=image.id, text="hello"),
        OCRLemma(image_id=image.id, lemma="hello"),
        description,
        ImageTag(image_id=image.id, key="k", value="v", source="OCR"),
    ]
    db_session.add_all(rows)
    await db_session.flush()
    for row in rows:
        await db_session.refresh(row)
        assert row.stale is False, type(row).__name__
    found = (await db_session.execute(select(OCRText.stale).where(OCRText.image_id == image.id))).scalars().all()
    assert found == [False]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_model.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'CutState' from 'Storage.models'`.

- [ ] **Step 4: Add the constants and the mixin**

In `Storage/models.py`, directly after the `PROFILE_EMBEDDING_DIM = 1024` line, add:

```python
class ImageStatus:
    """Values of images.status (a free String(20), deliberately not an enum or a check constraint).
    Meaning, who sets each value, who reads it and the allowed transitions: docs/image-status.md,
    which is authoritative."""
    ACTIVE = "active"
    PENDING = "pending"
    REJECTED = "rejected"
    CUT_PROPOSED = "cut_proposed"
    ALL = (ACTIVE, PENDING, REJECTED, CUT_PROPOSED)


class CutState:
    """Values of image_cuts.state (epic screenshot-recut). LIVE states hold the one-per-source slot."""
    DETECTED = "detected"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    LIVE = (DETECTED, PROPOSED, APPROVED)
    ALL = (DETECTED, PROPOSED, APPROVED, REJECTED, FAILED)


class StaleMixin:
    """Marks a derived row that was copied from a source image to its cut and must be rewritten by
    the 'refresh stale' mode of the job that owns the table (epic screenshot-recut, ADR-001).
    Only tables whose jobs select work by absence of rows carry it; see
    docs/image-derived-data.md. Rewritten rows take the default (False), so nothing needs clearing."""
    stale = Column(Boolean, nullable=False, server_default=sa.false(), default=False)


```

- [ ] **Step 5: Apply the mixin to the four models**

Use the Edit tool on `Storage/models.py`, one edit per class line (each string is unique in the file):

- `class OCRText(Base):` becomes `class OCRText(StaleMixin, Base):`
- `class ImageDescription(Base):` becomes `class ImageDescription(StaleMixin, Base):`
- `class ImageTag(Base):` becomes `class ImageTag(StaleMixin, Base):`
- `class OCRLemma(Base):` becomes `class OCRLemma(StaleMixin, Base):`

- [ ] **Step 6: Add the `ImageCut` model**

In `Storage/models.py`, directly before the line `class ImageMetrics(Base):`, add:

```python
_CUT_LIVE_PREDICATE = "state IN ('detected', 'proposed', 'approved')"


class ImageCut(Base):
    """One proposal to replace a badly cropped image (source) with a tighter cut (epic screenshot-recut).
    The record outlives both images: the three foreign keys are SET NULL, so a removed source or a rejected
    and removed cut leaves the history (box, detector, state) behind, with source_content_hash as the audit
    anchor. At most one live (detected/proposed/approved) row per source; rejected and failed rows may
    repeat. The box is in source pixels and is validated against the source size in code, not here."""
    __tablename__ = "image_cuts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    cut_image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    source_content_hash = Column(String, nullable=True)
    crop_x = Column(Integer, nullable=False)
    crop_y = Column(Integer, nullable=False)
    crop_w = Column(Integer, nullable=False)
    crop_h = Column(Integer, nullable=False)
    detector = Column(String(50), nullable=False)
    detector_version = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=True)
    detect_run_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_image_cuts_live_source", "source_image_id", unique=True,
              postgresql_where=text(_CUT_LIVE_PREDICATE)),
        Index("uq_image_cuts_cut_image_id", "cut_image_id", unique=True),
        Index("ix_image_cuts_detect_run_id", "detect_run_id"),
        Index("ix_image_cuts_state", "state"),
        CheckConstraint("crop_x >= 0 AND crop_y >= 0 AND crop_w > 0 AND crop_h > 0", name="ck_image_cuts_box"),
        CheckConstraint("state IN ('detected', 'proposed', 'approved', 'rejected', 'failed')",
                        name="ck_image_cuts_state"),
    )


```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_model.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add Storage/models.py tests/integration/test_image_cuts_model.py
git commit -m "feat(storage): image_cuts model, ImageStatus/CutState constants and StaleMixin" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Alembic migration

**Files:**
- Create: `Storage/alembic/versions/b8d4f1a6c935_add_image_cuts_and_stale_columns.py`
- Test: `tests/integration/test_image_cuts_migration.py`

**Interfaces:**
- Consumes: the models of Task 1 (the test compares the migration's result with the model-created schema).
- Produces: revision `b8d4f1a6c935`, `down_revision = 'f3a9c27b5d14'` (current head).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_image_cuts_migration.py` (same pattern as `test_meme_text_ratings_migration.py`: the session fixture builds the schema from the models, the test rebuilds the affected objects from the migration inside a rolled-back transaction and compares):

```python
"""The image_cuts migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "b8d4f1a6c935_add_image_cuts_and_stale_columns.py")
STALE_TABLES = ("ocr_texts", "ocr_lemmas", "image_descriptions", "tags")


def _load():
    spec = importlib.util.spec_from_file_location("migration_b8d4f1a6c935", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _norm(value):
    """Lower-case and drop whitespace/parentheses so cosmetic renderings of the same SQL compare equal."""
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if not ch.isspace() and ch not in "()")


def _cut_shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns("image_cuts")}
    checks = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints("image_cuts")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]),
                           _norm(i.get("dialect_options", {}).get("postgresql_where")))
               for i in inspector.get_indexes("image_cuts")}
    primary = tuple(inspector.get_pk_constraint("image_cuts")["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("image_cuts")}
    return columns, checks, indexes, primary, foreign


def _stale_shape(connection):
    inspector = inspect(connection)
    shape = {}
    for table in STALE_TABLES:
        column = next((c for c in inspector.get_columns(table) if c["name"] == "stale"), None)
        shape[table] = None if column is None else (str(column["type"]), column["nullable"], _norm(column["default"]))
    return shape


def _both(connection):
    return _cut_shape(connection), _stale_shape(connection)


async def _drop_what_the_migration_adds(connection):
    await connection.execute(text("DROP TABLE image_cuts"))
    for table in STALE_TABLES:
        await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN stale"))


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_both)
            assert all(model_shape[1].values()), "the model must put stale on all four tables"

            await _drop_what_the_migration_adds(connection)
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_both) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "image_cuts" not in tables
            assert await connection.run_sync(_stale_shape) == {table: None for table in STALE_TABLES}
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_rows_read_false_after_the_migration(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _drop_what_the_migration_adds(connection)
            await connection.execute(text("INSERT INTO images (id, filename) VALUES (gen_random_uuid(), 'x.jpg')"))
            await connection.execute(text(
                "INSERT INTO ocr_texts (id, image_id, text) SELECT gen_random_uuid(), id, 'hello' FROM images"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            flags = (await connection.execute(text("SELECT stale FROM ocr_texts"))).scalars().all()
            assert flags == [False]
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "DROP INDEX uq_image_cuts_live_source",
    "ALTER TABLE image_cuts DROP CONSTRAINT ck_image_cuts_box",
    "ALTER TABLE tags ALTER COLUMN stale SET DEFAULT true",
])
async def test_the_shape_guard_catches_drift(db_engine, drift):
    """Negative control: a deliberately altered schema must NOT compare equal to the model-created one."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await connection.run_sync(_both)
            await connection.execute(text(drift))
            assert await connection.run_sync(_both) != before
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "b8d4f1a6c935" and module.down_revision == "f3a9c27b5d14"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_migration.py -v`
Expected: FAIL (`FileNotFoundError` for the migration file).

- [ ] **Step 3: Write the migration**

Create `Storage/alembic/versions/b8d4f1a6c935_add_image_cuts_and_stale_columns.py`:

```python
"""add image_cuts and stale columns

Revision ID: b8d4f1a6c935
Revises: f3a9c27b5d14
Create Date: 2026-10-17 12:00:00.000000

Epic screenshot-recut, project R1. Adds the image_cuts relation and a stale flag on the four derived
tables whose jobs select work by absence of rows. NOT NULL DEFAULT false is a metadata-only change on
PostgreSQL 11+ (the three environments run 18.3), so no table is rewritten.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8d4f1a6c935'
down_revision: Union[str, Sequence[str], None] = 'f3a9c27b5d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STALE_TABLES = ("ocr_texts", "ocr_lemmas", "image_descriptions", "tags")
LIVE_PREDICATE = "state IN ('detected', 'proposed', 'approved')"


def upgrade() -> None:
    op.create_table(
        'image_cuts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('source_image_id', sa.UUID(), nullable=True),
        sa.Column('cut_image_id', sa.UUID(), nullable=True),
        sa.Column('source_content_hash', sa.String(), nullable=True),
        sa.Column('crop_x', sa.Integer(), nullable=False),
        sa.Column('crop_y', sa.Integer(), nullable=False),
        sa.Column('crop_w', sa.Integer(), nullable=False),
        sa.Column('crop_h', sa.Integer(), nullable=False),
        sa.Column('detector', sa.String(length=50), nullable=False),
        sa.Column('detector_version', sa.String(length=50), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('detect_run_id', sa.UUID(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('crop_x >= 0 AND crop_y >= 0 AND crop_w > 0 AND crop_h > 0', name='ck_image_cuts_box'),
        sa.CheckConstraint("state IN ('detected', 'proposed', 'approved', 'rejected', 'failed')",
                           name='ck_image_cuts_state'),
        sa.ForeignKeyConstraint(['source_image_id'], ['images.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cut_image_id'], ['images.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['detect_run_id'], ['batch_runs.run_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_image_cuts_live_source', 'image_cuts', ['source_image_id'], unique=True,
                    postgresql_where=sa.text(LIVE_PREDICATE))
    op.create_index('uq_image_cuts_cut_image_id', 'image_cuts', ['cut_image_id'], unique=True)
    op.create_index('ix_image_cuts_detect_run_id', 'image_cuts', ['detect_run_id'])
    op.create_index('ix_image_cuts_state', 'image_cuts', ['state'])

    for table in STALE_TABLES:
        op.add_column(table, sa.Column('stale', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in reversed(STALE_TABLES):
        op.drop_column(table, 'stale')
    op.drop_index('ix_image_cuts_state', table_name='image_cuts')
    op.drop_index('ix_image_cuts_detect_run_id', table_name='image_cuts')
    op.drop_index('uq_image_cuts_cut_image_id', table_name='image_cuts')
    op.drop_index('uq_image_cuts_live_source', table_name='image_cuts')
    op.drop_table('image_cuts')
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_migration.py tests/integration/test_image_cuts_model.py -v`
Expected: all PASS. If `test_the_migration_matches_the_model_and_downgrades` reports a shape difference, fix the migration or the model so both declare the same thing (do not loosen the comparison).

- [ ] **Step 5: Confirm Alembic sees a single head**

Run: `.venv311/Scripts/python.exe -I -m alembic -c Storage/alembic.ini heads`
Expected: one line ending in `b8d4f1a6c935 (head)`.

- [ ] **Step 6: Commit**

```bash
git add Storage/alembic/versions/b8d4f1a6c935_add_image_cuts_and_stale_columns.py tests/integration/test_image_cuts_migration.py
git commit -m "feat(storage): migration for image_cuts and the stale columns" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The stale helper

**Files:**
- Create: `Storage/stale.py`
- Test: `tests/integration/test_stale_helper.py`

**Interfaces:**
- Consumes: `StaleMixin`, `OCRText`, `OCRLemma`, `ImageDescription`, `ImageTag`, `Image` from `Storage.models`.
- Produces (exact signatures):
  - `STALE_MODELS: tuple[type, ...]` = `(OCRText, OCRLemma, ImageDescription, ImageTag)`
  - `async def images_with_stale_rows(session: AsyncSession, model: type, status: str) -> list[uuid.UUID]`
  - `async def delete_stale_rows(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID]) -> int`
  - `async def stale_counts(session: AsyncSession) -> dict[str, int]` (keys are table names)
  - `images_with_stale_rows` and `delete_stale_rows` raise `ValueError` for a model outside `STALE_MODELS`. None of the three commits.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_stale_helper.py`:

```python
"""Storage/stale.py: select and delete stale rows without committing.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py."""
import uuid

import pytest
from sqlalchemy import select

import Storage.stale as stale
from Storage.models import (
    DescriptionLemma, Embedding, Image, ImageDescription, ImageDescriptionFeedback, ImageStatus, ImageTag, OCRLemma,
    OCRText, StaleMixin,
)
from Storage.stale import STALE_MODELS, delete_stale_rows, images_with_stale_rows, stale_counts


async def _image(session, status=ImageStatus.CUT_PROPOSED):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def test_stale_models_are_exactly_the_mixin_users():
    assert set(STALE_MODELS) == set(StaleMixin.__subclasses__())


@pytest.mark.asyncio(loop_scope="session")
async def test_selects_only_stale_rows_of_images_in_the_requested_status(db_session):
    cut = await _image(db_session, ImageStatus.CUT_PROPOSED)
    active = await _image(db_session, ImageStatus.ACTIVE)
    fresh = await _image(db_session, ImageStatus.CUT_PROPOSED)
    db_session.add_all([
        OCRText(image_id=cut.id, text="a", stale=True),
        OCRText(image_id=cut.id, text="b", stale=True),   # two stale rows, one image: reported once
        OCRText(image_id=active.id, text="c", stale=True),
        OCRText(image_id=fresh.id, text="d", stale=False),
    ])
    await db_session.flush()

    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.CUT_PROPOSED) == [cut.id]
    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.ACTIVE) == [active.id]
    assert await images_with_stale_rows(db_session, OCRText, ImageStatus.PENDING) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_deletes_only_stale_rows_of_the_listed_images(db_session):
    target = await _image(db_session)
    other = await _image(db_session)
    db_session.add_all([
        ImageTag(image_id=target.id, key="k", value="stale", source="OCR", stale=True),
        ImageTag(image_id=target.id, key="k", value="kept-note", source="Note", stale=False),
        ImageTag(image_id=other.id, key="k", value="other-stale", source="OCR", stale=True),
    ])
    await db_session.flush()

    assert await delete_stale_rows(db_session, ImageTag, [target.id]) == 1
    left = (await db_session.execute(select(ImageTag.value).order_by(ImageTag.value))).scalars().all()
    assert left == ["kept-note", "other-stale"]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_empty_id_list_deletes_nothing_and_issues_no_statement(db_session, monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("no statement expected for an empty id list")

    monkeypatch.setattr(db_session, "execute", boom)
    assert await delete_stale_rows(db_session, OCRText, []) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_deletion_is_chunked_for_long_id_lists(db_session, monkeypatch):
    monkeypatch.setattr(stale, "_CHUNK", 2)
    images = [await _image(db_session) for _ in range(5)]
    db_session.add_all([OCRText(image_id=i.id, text="t", stale=True) for i in images])
    await db_session.flush()

    assert await delete_stale_rows(db_session, OCRText, [i.id for i in images]) == 5
    assert (await db_session.execute(select(OCRText))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_a_stale_description_takes_its_children_with_it(db_session):
    image = await _image(db_session)
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t", stale=True)
    db_session.add(description)
    await db_session.flush()
    db_session.add_all([
        ImageDescriptionFeedback(image_description_id=description.id, approved=True),
        DescriptionLemma(image_description_id=description.id, lemma="x"),
    ])
    await db_session.flush()

    assert await delete_stale_rows(db_session, ImageDescription, [image.id]) == 1
    assert (await db_session.execute(select(ImageDescriptionFeedback))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionLemma))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_counts_reports_every_marked_table(db_session):
    image = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=image.id, text="a", stale=True),
        OCRLemma(image_id=image.id, lemma="a", stale=True),
        OCRLemma(image_id=image.id, lemma="b", stale=False),
    ])
    await db_session.flush()
    counts = await stale_counts(db_session)
    assert set(counts) == {"ocr_texts", "ocr_lemmas", "image_descriptions", "tags"}
    assert counts["ocr_texts"] >= 1 and counts["ocr_lemmas"] >= 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("call", ["select", "delete"])
async def test_a_model_without_the_mixin_is_refused(db_session, call):
    with pytest.raises(ValueError):
        if call == "select":
            await images_with_stale_rows(db_session, Embedding, ImageStatus.ACTIVE)
        else:
            await delete_stale_rows(db_session, Embedding, [uuid.uuid4()])


@pytest.mark.asyncio(loop_scope="session")
async def test_the_helpers_never_commit(db_session, monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("helpers must not commit")

    image = await _image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a", stale=True))
    await db_session.flush()
    monkeypatch.setattr(db_session, "commit", boom)
    await images_with_stale_rows(db_session, OCRText, ImageStatus.CUT_PROPOSED)
    await delete_stale_rows(db_session, OCRText, [image.id])
    await stale_counts(db_session)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_stale_helper.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'Storage.stale'`.

- [ ] **Step 3: Write the helper**

Create `Storage/stale.py`:

```python
"""Shared helpers for rows marked stale (epic screenshot-recut, ADR-001).

A cut image receives copies of model-derived rows from its source, flagged `stale`. A job's
'refresh stale' mode uses these functions: find the images that still carry stale rows, delete those
rows, then rewrite them with the job's normal logic (rewritten rows take the default stale=False, so
nothing needs clearing). None of these functions commits; get_async_db owns commit and rollback.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Image, ImageDescription, ImageTag, OCRLemma, OCRText

STALE_MODELS = (OCRText, OCRLemma, ImageDescription, ImageTag)

# ids per DELETE statement; asyncpg allows 32767 bind parameters per statement
_CHUNK = 5000


def _require_stale_model(model: type) -> None:
    if model not in STALE_MODELS:
        raise ValueError(f"{getattr(model, '__name__', model)} has no stale column")


async def images_with_stale_rows(session: AsyncSession, model: type, status: str) -> list[uuid.UUID]:
    """Ids of images in `status` having at least one stale row in `model`, each reported once."""
    _require_stale_model(model)
    statement = (
        select(model.image_id)
        .join(Image, Image.id == model.image_id)
        .where(model.stale.is_(True), Image.status == status)
        .distinct()
        .order_by(model.image_id)
    )
    return list((await session.execute(statement)).scalars().all())


async def delete_stale_rows(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID]) -> int:
    """Delete the stale rows of the given images (rows that are not stale are never touched). Returns the count."""
    _require_stale_model(model)
    ids = list(image_ids)
    deleted = 0
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        result = await session.execute(
            delete(model).where(model.stale.is_(True), model.image_id.in_(chunk))
        )
        deleted += result.rowcount
    return deleted


async def stale_counts(session: AsyncSession) -> dict[str, int]:
    """Stale row count per marked table, keyed by table name."""
    counts = {}
    for model in STALE_MODELS:
        statement = select(func.count()).select_from(model).where(model.stale.is_(True))
        counts[model.__tablename__] = (await session.execute(statement)).scalar_one()
    return counts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_stale_helper.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add Storage/stale.py tests/integration/test_stale_helper.py
git commit -m "feat(storage): shared helpers for stale rows" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Registry, inventory and the tests that keep them honest

**Files:**
- Create: `docs/image-status.md`, `docs/image-derived-data.md`
- Create: `tests/docs/__init__.py` (empty; create with `touch`, an empty Write call is rejected by the IDE dialog)
- Test: `tests/docs/test_image_status_registry.py`, `tests/docs/test_image_derived_data_inventory.py`

**Interfaces:**
- Consumes: `ImageStatus`, `Base.metadata` from `Storage.models`.
- Produces: the two documents. Parsing contract the tests rely on: in `image-status.md`, the table under the heading `## Statuses`; in `image-derived-data.md`, the table under the heading `## Inventory`. In both, each data row starts with `` | `name` `` and the first backticked token of the first cell is the status or table name.

- [ ] **Step 1: Write the failing registry test**

```bash
mkdir -p tests/docs && touch tests/docs/__init__.py
```

Create `tests/docs/test_image_status_registry.py`:

```python
"""docs/image-status.md is the authoritative registry of images.status values; it must list exactly ImageStatus."""
import re
from pathlib import Path

from Storage.models import ImageStatus

DOC = Path(__file__).resolve().parents[2] / "docs" / "image-status.md"


def documented_statuses(markdown: str) -> set[str]:
    """First backticked token of the first cell of each table row under '## Statuses'."""
    found, inside = set(), False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Statuses"
            continue
        if inside:
            match = re.match(r"\|\s*`([^`]+)`", line)
            if match:
                found.add(match.group(1))
    return found


def test_the_registry_lists_exactly_the_image_statuses():
    assert documented_statuses(DOC.read_text(encoding="utf-8")) == set(ImageStatus.ALL)


def test_the_parser_reads_only_the_statuses_section():
    table = ("## Statuses\n\n| Status | Meaning |\n|---|---|\n| `active` | x |\n| `bogus` | y |\n\n"
             "## Other\n| `pending` | z |\n")
    assert documented_statuses(table) == {"active", "bogus"}


def test_every_status_appears_in_the_transitions_section():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Transitions", 1)[1].split("\n## ", 1)[0]
    for status in ImageStatus.ALL:
        assert f"`{status}`" in section, status
```

- [ ] **Step 2: Write the failing inventory test**

Create `tests/docs/test_image_derived_data_inventory.py`:

```python
"""docs/image-derived-data.md classifies every table that hangs off images; a new table fails until classified."""
import re
from pathlib import Path

from sqlalchemy import Column, ForeignKey, MetaData, Table
from sqlalchemy.dialects.postgresql import UUID

from Storage.models import Base

DOC = Path(__file__).resolve().parents[2] / "docs" / "image-derived-data.md"


def tables_off_images(metadata: MetaData) -> set[str]:
    """Tables with a foreign key to images, plus tables with a foreign key to any such table (transitively)."""
    found = {
        table.name for table in metadata.tables.values()
        if table.name != "images" and any(fk.column.table.name == "images" for fk in table.foreign_keys)
    }
    grew = True
    while grew:
        grew = False
        for table in metadata.tables.values():
            if table.name in found or table.name == "images":
                continue
            if any(fk.column.table.name in found for fk in table.foreign_keys):
                found.add(table.name)
                grew = True
    return found


def documented_tables(markdown: str) -> dict[str, str]:
    """Table name -> group cell, from the rows under '## Inventory'."""
    rows, inside = {}, False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Inventory"
            continue
        if inside:
            match = re.match(r"\|\s*`([^`]+)`[^|]*\|\s*([^|]+?)\s*\|", line)
            if match:
                rows.setdefault(match.group(1), match.group(2))
    return rows


def unclassified(metadata: MetaData, documented: dict[str, str]) -> list[str]:
    return sorted(tables_off_images(metadata) - set(documented))


def test_every_table_off_images_is_classified():
    assert unclassified(Base.metadata, documented_tables(DOC.read_text(encoding="utf-8"))) == []


def test_the_inventory_names_no_table_that_does_not_exist():
    documented = set(documented_tables(DOC.read_text(encoding="utf-8")))
    assert documented <= set(Base.metadata.tables), sorted(documented - set(Base.metadata.tables))


def test_every_row_has_a_group():
    for table, group in documented_tables(DOC.read_text(encoding="utf-8")).items():
        assert group, table


def test_the_new_relation_and_nested_children_are_found():
    found = tables_off_images(Base.metadata)
    assert {"image_cuts", "meme_profile_values", "description_lemmas"} <= found
    assert "images" not in found and "batch_runs" not in found


def test_an_unclassified_future_table_is_reported_by_name():
    """Negative control on a toy schema: a table nobody documented must be named, including a nested one."""
    metadata = MetaData()
    Table("images", metadata, Column("id", UUID, primary_key=True))
    widgets = Table("widgets", metadata, Column("id", UUID, primary_key=True),
                    Column("image_id", UUID, ForeignKey("images.id")))
    Table("gadgets", metadata, Column("widget_id", UUID, ForeignKey(widgets.c.id)))
    assert unclassified(metadata, {"widgets": "2"}) == ["gadgets"]
    assert unclassified(metadata, {}) == ["gadgets", "widgets"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv311/Scripts/python.exe -m pytest tests/docs -v`
Expected: the registry and inventory tests that read the documents FAIL with `FileNotFoundError`; the parser, toy-schema and `found` tests do not read the documents and may PASS.

- [ ] **Step 4: Write `docs/image-status.md`**

````markdown
# Image status registry

Authoritative definition of the values of `images.status`. Specs, `CLAUDE.md` and the batch documents link here and do not
redefine statuses. A new status needs a row here in the same change; `tests/docs/test_image_status_registry.py` fails
otherwise. The constants live in `Storage/models.py` (`ImageStatus`); the column itself is a free `String(20)`.

## Statuses

| Status | Meaning | Set by | Read by |
|---|---|---|---|
| `active` | In the library and searchable. The column default, and the value every list query filters on unless told otherwise | column default; `ingest_promote`; approval of a recut proposal (screenshot-recut R5) | all default queries; jobs without `--status` |
| `pending` | Ingested into an in-flight ingestion run and awaiting duplicate review. The file is already in `BASE_PATH` | `ingest_hash_dedup` | ingestion review endpoints and page; jobs with `--status pending`; `ingest_promote`; `ingest_abort` |
| `rejected` | Rejected during ingestion review. The file was moved out of the library, the row is kept so the decision can be undone | ingestion review | ingestion tooling only |
| `cut_proposed` | A proposed cut of a badly cropped image, awaiting human review. Invisible to search and browse | the cut step (screenshot-recut R3) | recut review (R4); jobs with `--status cut_proposed` (R3) |

## Transitions

- `pending` to `active`: `ingest_promote`, once the image has no unresolved duplicate candidate pairs.
- `pending` to `rejected`: ingestion review confirms a duplicate.
- `rejected` to `pending`: undo in the ingestion review UI (the file is moved back).
- `pending` to removed: `ingest_abort` deletes the row and moves the file back to the ingestion source.
- `cut_proposed` to `active`: the proposal is approved (screenshot-recut R5). The source image is flagged for the existing `move_flagged` path in the same change.
- `cut_proposed` to removed: the proposal is rejected. The cut is flagged and leaves through `move_flagged` and `unregister_deleted_images`.
- `active` has no outgoing status change. An `active` image leaves the library by being flagged (`image_extras.flagged`), not by a status.

## Not image statuses

These look like statuses but belong to something else. Do not mix them with the table above.

| Name | Where | Values | Meaning |
|---|---|---|---|
| `batch_runs.status` | one row per batch job run | `started`, `completed`, `failed`, ... (`RunStatus`) | State of a run, including an ingestion run |
| `image_processing_status.status` | one row per image and pipeline | `processing`, `done`, `failed` | Whether a job has handled that image; also what makes some jobs skip it |
| `image_cuts.state` | one row per recut proposal | `detected`, `proposed`, `approved`, `rejected`, `failed` (`CutState`) | Where a proposal is in its own lifecycle |
| `image_extras.flagged` | one row per image | true / false with a reason | Marks an image to be moved out of the library by `move_flagged` |

Related designs: `docs/superpowers/specs/2026-07-25-image-visibility-status-design.md` (why the column and the default exist),
`docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md` (`pending` and `rejected`),
`docs/superpowers/specs/2026-10-17-screenshot-recut/README.md` (`cut_proposed`).
````

Before saving, check the transitions list against `docs/superpowers/specs/2026-07-25-image-visibility-status-design.md`, `2026-07-24-ingestion-pipeline-design.md` and `2026-08-07-ingestion-abort-design.md`; if a described transition differs from the code or those specs, fix the document (the test only checks the status names).

- [ ] **Step 5: Write `docs/image-derived-data.md`**

The facts below were verified on 2026-10-17 against `Storage/models.py`; re-check any row that no longer matches the models and fix the document, not the test.

````markdown
# Derived data of an image

What hangs off an `images` row and what happens to each part when a recut proposal creates a cut image (epic
screenshot-recut, `docs/superpowers/specs/2026-10-17-screenshot-recut/`). Authoritative for the question "what is all the data
of an image". `tests/docs/test_image_derived_data_inventory.py` fails when a table with a foreign key to `images` (or to one of
those tables) is missing here. Changing the role of a table also means editing this file.

Groups: **1** copied as is (human-authored or not pixel-dependent). **2** not copied; recomputed by the existing pipeline
(pixel-dependent). **3** copied, flagged or derived as stale, refreshed later. **relation** is bookkeeping, not derived data.
**none** is not copied and not recomputed for the cut by this feature.

The cut's `images` row: new `id` and `filename`; `created_at` inherited from the source; `status = cut_proposed`;
`ingestion_batch_id` empty; `width`, `height` and `content_hash` computed from the new file.

## Inventory

| Table | Group | At cut time | Refresh path |
|---|---|---|---|
| `image_cuts` | relation | The proposal itself | not applicable |
| `description_notes` | 1 | Copied as is, including `lemmas_built_at` and `embedding_built_at` | none |
| `description_note_lemmas` | 1 | Copied | none |
| `description_note_embeddings` | 1 | Copied | none |
| `image_extras` | 1 | `remarks` copied; `flagged` and `flagged_reason` left empty | none |
| `meme_text_ratings` | 1, deferred | Not copied at cut time. Moved to the cut on approve, stay with the source on reject | none |
| `embeddings` | 2 | Not copied (CLIP vector) | `build_image_embeddings --status cut_proposed` |
| `image_classifications` | 2 | Not copied (text-heavy classifier) | `classify_text_heavy --status cut_proposed` |
| `ocr_text_embeddings` | 2 | Not copied | `build_ocr_text_embeddings`, after OCR and classification |
| `ocr_texts` | 3 | Copied with each polygon shifted by the crop origin; a row is kept when its polygon centre lies inside the crop (points clipped to the crop), otherwise dropped; `stale = true` | `extract_text_from_memes`, refresh mode |
| `ocr_lemmas` | 3 | Copied wholesale, `stale = true`. Keyed by image and lemma, not by OCR row, so they cannot be filtered by the crop | `build_ocr_lemmas`, refresh mode, after the OCR refresh |
| `image_descriptions` | 3 | Copied, `stale = true` | `build_image_descriptions`, refresh mode |
| `image_description_embeddings` | 3, via parent | Copied with the description | rebuilt for the new description row by `build_image_description_embeddings` |
| `image_description_feedback` | 3, via parent | Copied with the description; lost when the stale description is replaced (the feedback judged the old text) | none |
| `description_lemmas` | 3, via parent | Copied with the description | rebuilt for the new description row by `build_description_lemmas` |
| `tags` | 1, 2 or 3 by source | See "Tags by source" below | see below |
| `meme_profiles` | 1 or 3 | Copied exactly. A human-verified profile is group 1 and never rewritten; a model-made one is group 3 with derived staleness | `build_meme_profiles`, through the existing fingerprint rule |
| `meme_profile_values` | 1 or 3 | Copied with its profile | with the profile |
| `meme_profile_situation_embeddings` | 1 or 3 | Copied with its profile | `build_meme_profile_embeddings`, with the profile |
| `image_processing_status` | none | Not copied: a copied `done` would claim work that was not done. The refresh mode selects by stale rows and bypasses the marker | the jobs create their own markers |
| `tmp_duplicates` | none | Not copied | the duplicate jobs run as usual (source/cut pairs are excluded, screenshot-recut R5) |
| `tmp_clusters` | none | Not copied | `clusterize` |
| `duplicate_decisions` | none | Not copied | not applicable |
| `processing_errors` | none | Not copied | produced by the jobs |
| `image_metrics` | none | Not copied | produced by the jobs |

## Tags by source

`tags.source` takes these values in the code today (verified 2026-10-17: no manual or human-sourced tags exist on any environment):

| Source | Group | At cut time | Refresh path |
|---|---|---|---|
| `Note` | 1 | Copied (derived from the copied note, not from pixels) | none |
| `YOLO` | 2 | Not copied | `detect_entities_and_tag` |
| `CONCEPT` | 2 | Not copied | `tag_images_from_concepts` |
| `OCR` | 3 | Copied, `stale = true` | `build_tags_from_ocr`, refresh mode |
| `Ollama` | 3 | Copied, `stale = true` | `build_tags_from_descriptions`, refresh mode |

## Refresh order

The OCR refresh rewrites `ocr_texts`, so everything derived from OCR runs after it: OCR, then OCR lemmas and OCR tags, then
descriptions, then description embeddings, lemmas and tags, then the profile and its embedding. `description_pipeline` already
orders the last part. The order is part of the contract: a lemma row rebuilt before the OCR refresh would be fresh in name only.
````

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv311/Scripts/python.exe -m pytest tests/docs -v`
Expected: all PASS. If `test_every_table_off_images_is_classified` names a table, it exists in `Storage/models.py` but is missing from the document: classify it (do not weaken the test).

- [ ] **Step 7: Commit**

```bash
git add docs/image-status.md docs/image-derived-data.md tests/docs
git commit -m "docs: image status registry and derived-data inventory with sync tests" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Pointers to the registry

**Files:**
- Modify: `CLAUDE.md`, `docs/batches/inventory.md`, `docs/batches/execution-model.md`, `docs/superpowers/specs/2026-07-25-image-visibility-status-design.md`, `docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md`

No new behaviour; each edit adds one pointer and removes nothing. Use the Edit tool (never PowerShell `Get-Content`/`Set-Content`).

- [ ] **Step 1: `CLAUDE.md`**

Under `## Key invariants`, directly after the bullet `- \`backend_api.md\` must stay in sync with the actual routers.`, add:

```markdown
- Image `status` values (`active`, `pending`, `rejected`, `cut_proposed`) are defined in one place, `docs/image-status.md`. Add a row there in the same change as any new status; link to it instead of redefining one. What hangs off an image, and how a recut copies it, is `docs/image-derived-data.md` (kept in sync by `tests/docs/`).
```

- [ ] **Step 2: Batch documents**

Read the first lines of `docs/batches/inventory.md` and `docs/batches/execution-model.md`. In each, after the opening paragraph that follows the title and status line, insert a separate paragraph:

```markdown
Image statuses (`--status active|pending|cut_proposed`) are defined in `docs/image-status.md`; this document does not redefine them.
```

- [ ] **Step 3: The two older specs**

In `docs/superpowers/specs/2026-07-25-image-visibility-status-design.md` and `docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md`, directly under the status line (and any `Plan:`/`Follow-ups:` lines), add:

```markdown
Image status values are defined in `docs/image-status.md` (authoritative since 2026-10-17); this document keeps its design history.
```

- [ ] **Step 4: Verify and commit**

Run: `.venv311/Scripts/python.exe -m pytest tests/docs -v`
Expected: PASS.

```bash
git add CLAUDE.md docs/batches docs/superpowers/specs/2026-07-25-image-visibility-status-design.md docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md
git commit -m "docs: point CLAUDE.md, batch docs and the ingestion specs at the status registry" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Full verification, spec bookkeeping and rollout

**Files:**
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-17-r1-data-model-and-status-registry-design.md` (status line), `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md` (R1 row)

- [ ] **Step 1: Run the whole integration root**

Shared model code changed, so run the entire root, not only the new files.

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/ -v`
Expected: all PASS (new files included). Any failure in an older file is caused by this change until proven otherwise (the new columns and table are the only differences): investigate it, do not skip it.

- [ ] **Step 2: Run the other roots, one invocation each**

```bash
.venv311/Scripts/python.exe -m pytest tests/docs -v
.venv311/Scripts/python.exe -m pytest tests/rules -v
.venv311/Scripts/python.exe -m pytest batch/tests -v
cd Backend && ../.venv311/Scripts/python.exe -m pytest -v
```
Expected: all PASS.

- [ ] **Step 3: Confirm no batch or API code changed**

Run: `git diff --stat main...HEAD -- batch Backend`
Expected: no output (R1 changes neither).

- [ ] **Step 4: Mark the spec and the tracker**

Set the status line of the R1 spec to `Status: done` and keep the `Plan:` line under it. In `tracker.md` change the R1 row status from `planned` to `done`.

```bash
git add docs/superpowers/specs/2026-10-17-screenshot-recut
git commit -m "docs: mark recut R1 done" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Live rollout (STOP: needs the user's explicit go-ahead)**

The metal, general and IT databases are always in use. Do not run this step without the user saying so for this rollout. The migration takes a brief lock to add four columns (metadata only) and creates one empty table; old code ignores both, so it is safe to run before or after deploying code.

For each environment in turn (`metal`, `general`, `it`), after the user confirms a backup exists, from `Storage/` in PowerShell (as in `CLAUDE.md`):

```powershell
Get-Content ..\environments\.env.<env> | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic upgrade head
```

Then verify with the read-only role only (`DATABASE_URL_READONLY`, never the write URL): `SELECT count(*) FROM image_cuts;` returns 0, and `SELECT count(*) FROM ocr_texts WHERE stale;` returns 0 (and likewise for `ocr_lemmas`, `image_descriptions`, `tags`). Report each environment's result before moving to the next.

---

## Self-Review

**Spec coverage** (spec section to task): 3.1 `image_cuts` columns, indexes, checks, FKs: Tasks 1 and 2. 3.2 four `stale` columns, defaults, no index: Tasks 1 and 2. 3.3 rollout: Task 6 step 5. 4 code (`ImageCut`, mixin, constants, `Storage/stale.py` with three functions, no repository, no job changes): Tasks 1 and 3; Task 6 step 3 checks no batch or API change. 5 staleness table: encoded in the inventory (Task 4). 6 registry and links: Tasks 4 and 5. 7 inventory: Task 4. 9 testing: model and migration tests (Tasks 1, 2), helper tests (Task 3), registry sync and inventory completeness (Task 4), full integration root (Task 6). 10 acceptance criteria 1 to 8: criteria 1 to 3 Tasks 1, 2 and 6; 4 and 5 Task 4; 6 Task 3; 7 Task 5; 8 Task 6. Spec sections 8 (cross-cutting rules for later projects) and 11 (out of scope) need no code in R1.

**Placeholders:** none; every code step shows the code, every document step shows the full text.

**Type consistency:** `ImageStatus.ALL`, `CutState.LIVE/ALL`, `StaleMixin`, `ImageCut`, `STALE_MODELS`, `images_with_stale_rows`, `delete_stale_rows`, `stale_counts` and the private `_CHUNK` are named identically in Tasks 1, 3 and 4. Index names `uq_image_cuts_live_source`, `uq_image_cuts_cut_image_id`, `ix_image_cuts_detect_run_id`, `ix_image_cuts_state` and constraint names `ck_image_cuts_box`, `ck_image_cuts_state` match between the model (Task 1) and the migration (Task 2). Revision id `b8d4f1a6c935` and parent `f3a9c27b5d14` match the test.

**Review Focus coverage:** empty id list (Task 3), wrong-status image (Task 3), long id list (Task 3 chunk test), both images deleted (Task 1), unclassified future table (Task 4 toy-schema test).
