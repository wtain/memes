# Human Description Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let anyone attach a free-text "description note" to an image, index it (lemmas) and embed it (SBERT), and make the main search box and similar-images endpoint treat it the same way they already treat OCR text — including as a fallback for images with no OCR text at all.

**Architecture:** Three new tables (`description_notes`, `description_note_embeddings`, `description_note_lemmas`) mirror the existing `ImageDescription`/`ImageDescriptionEmbedding`/`OCRLemma` shapes. CRUD on the note text is real-time (router → service → repository, upsert/delete); lemma indexing and embedding are computed by two new offline batch jobs, admin-triggerable only. Search integration extends `repository/ocr_lemmas.py`'s existing per-token source-union so no join/null-handling logic is needed for the "no OCR, note only" fallback case.

**Tech Stack:** FastAPI + SQLAlchemy async ORM (Postgres/pgvector), Alembic migrations, `ai.sbert.SbertModel` (`bge-large-en-v1.5`), React + TypeScript frontend, `datamodel-codegen`/`json2ts` for schema-driven types.

**Spec:** `docs/superpowers/specs/2026-08-20-description-notes-design.md`

## Global Constraints

- Description note embeddings use SBERT `bge-large-en-v1.5`, dimension `TEXT_EMBEDDING_DIM = 1024` (`Storage/models.py`) — **not** CLIP's 512-dim space.
- One note per image, edited in place. No edit history. No `updated_by` (no auth exists yet).
- The two write endpoints (`PUT`/`DELETE /api/images/{id}/description-note`) ship with **no auth** — every new write endpoint must be added to `docs/security/admin-permissions-todo.md`'s checklist in the same task that adds it.
- Both new batch jobs (`build_description_note_lemmas`, `build_description_note_embeddings`) are **manual-trigger only** via `/admin/batches` (registered in `environments/batch_registry.yaml`) — never added to `scheduler.jobs`.
- Windows: any batch script pulling in `batch/utils/progress.py`'s `ProgressTracker` needs `PYTHONIOENCODING=utf-8` set before running interactively (known repo gotcha — the "≈" character breaks Windows' default console codepage).
- `Storage/db.py`/`Storage/config.py` require `DATABASE_URL` already set in the shell before any batch/migration command that imports them — `--env`/`load_env()` do not substitute for this.
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line, pointed at the dedicated `ocrdb_test` database (user/password `ocr`, port 5432) — never the real dev database. Example: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`.
- Never run `Backend/tests/`, `tests/integration/`, `batch/tests/`, and `tests/rules/` in the same `pytest` invocation — each has its own `pytest.ini` with a different `asyncio_mode`; combining roots breaks collection.
- This plan touches `repository/ocr_lemmas.py` (shared search-matching code) — per CLAUDE.md's testing gotcha, run the **entire** `tests/integration/` root before merging, not just the new note-specific test files.

---

## Task 1: Data model and migration

**Files:**
- Modify: `Storage/models.py`
- Create: `Storage/alembic/versions/<generated>_add_description_notes_tables.py`
- Test: `tests/integration/test_description_notes_models.py`

**Interfaces:**
- Produces: ORM classes `DescriptionNote` (table `description_notes`, PK `image_id`, columns `text: Text`, `updated_at: DateTime`, `lemmas_built_at: DateTime | None`, `embedding_built_at: DateTime | None`), `DescriptionNoteEmbedding` (table `description_note_embeddings`, PK `description_note_id`, column `embedding: Vector(1024)`), `DescriptionNoteLemma` (table `description_note_lemmas`, composite PK `(image_id, lemma)`, column `phonetic_code: str | None`). `Image.description_note` relationship (`uselist=False`, `cascade="all, delete-orphan"`). All three tables cascade-delete on their `images.id` FK (`DescriptionNoteEmbedding` cascades transitively via `description_notes`' own FK to `images.id`).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_description_notes_models.py`:

```python
"""
Integration tests confirming the description_notes / description_note_embeddings /
description_note_lemmas tables and their ORM models are wired correctly, including
cascade-delete behavior.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma, Image

_DIM = 1024


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_round_trip(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    note = DescriptionNote(image_id=image.id, text="a cat wearing a hat")
    db_session.add(note)
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert row.text == "a cat wearing a hat"
    assert row.lemmas_built_at is None
    assert row.embedding_built_at is None
    assert row.updated_at is not None  # server_default=func.now() fired


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_embedding_round_trip(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="a dog in sunglasses")
    db_session.add(note)
    await db_session.flush()

    vector = [0.0] * _DIM
    vector[0] = 1.0
    db_session.add(DescriptionNoteEmbedding(description_note_id=note.image_id, embedding=vector))
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == note.image_id)
    )).scalar_one()
    assert row.embedding is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_lemma_composite_pk(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="funny cat meme")
    db_session.add(note)
    await db_session.flush()

    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="cat"))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="meme"))
    await db_session.flush()

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all()
    assert set(rows) == {"cat", "meme"}


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_image_cascades_to_note_embedding_and_lemmas(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="soon to be deleted")
    db_session.add(note)
    await db_session.flush()
    vector = [0.0] * _DIM
    db_session.add(DescriptionNoteEmbedding(description_note_id=note.image_id, embedding=vector))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="deleted"))
    await db_session.flush()

    await db_session.delete(image)
    await db_session.flush()

    assert (await db_session.execute(select(DescriptionNote))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionNoteEmbedding))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionNoteLemma))).scalars().all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_notes_models.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'DescriptionNote' from 'Storage.models'`.

- [ ] **Step 3: Add the models**

In `Storage/models.py`, add after `Image.image_extras = relationship(...)` (currently the last relationship line in the `Image` class, right after `image_extras = relationship("ImageExtras", back_populates="image", cascade="all, delete-orphan")`):

```python
    description_note = relationship(
        "DescriptionNote", uselist=False, back_populates="image", cascade="all, delete-orphan"
    )
    description_note_lemmas = relationship(
        "DescriptionNoteLemma", back_populates="image", cascade="all, delete-orphan"
    )
```

Then, anywhere after the `ImageExtras` class definition (e.g. right after it, to keep "extra per-image human-entered data" tables grouped together), add:

```python
class DescriptionNote(Base):
    __tablename__ = "description_notes"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    text = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
    # Staleness markers for the two batch jobs (build_description_note_lemmas /
    # build_description_note_embeddings): a note can be edited repeatedly after
    # creation (unlike ImageDescription, which is never edited), so "row exists"
    # alone isn't enough to know a lemma/embedding is up to date -- each job
    # reindexes when its built_at is NULL or older than updated_at.
    lemmas_built_at = Column(DateTime, nullable=True)
    embedding_built_at = Column(DateTime, nullable=True)

    image = relationship("Image", back_populates="description_note")
    embedding = relationship(
        "DescriptionNoteEmbedding", uselist=False,
        back_populates="note", cascade="all, delete-orphan",
    )


class DescriptionNoteEmbedding(Base):
    __tablename__ = "description_note_embeddings"

    description_note_id = Column(
        UUID(as_uuid=True), ForeignKey("description_notes.image_id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding = Column(Vector(TEXT_EMBEDDING_DIM))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_description_note_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    note = relationship("DescriptionNote", back_populates="embedding")


class DescriptionNoteLemma(Base):
    __tablename__ = "description_note_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)
    # Populated for schema symmetry with OCRLemma, but never queried by the
    # phonetic-erratives search fallback -- a human-typed note is deliberate
    # text, same rationale as ImageTag already being excluded from that
    # fallback. See docs/superpowers/specs/2026-08-20-description-notes-design.md.
    phonetic_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_description_note_lemmas_lemma", "lemma"),
        Index(
            "ix_description_note_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )

    image = relationship("Image", back_populates="description_note_lemmas")
```

- [ ] **Step 4: Create the Alembic migration file**

From `Storage/` (no `DATABASE_URL` needed — `alembic revision` without `--autogenerate` doesn't connect to the DB):

```bash
cd Storage && alembic revision -m "add description notes tables"
```

This creates a new file `Storage/alembic/versions/<generated_id>_add_description_notes_tables.py` with `down_revision = '70e319e084e8'` already filled in (the current head, per `duplicate_decisions`' migration). Open it and replace the `upgrade()`/`downgrade()` bodies:

```python
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Matches Storage.models.TEXT_EMBEDDING_DIM. Hardcoded rather than imported --
# migrations must stay valid even if the model constant changes later.
TEXT_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        'description_notes',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('lemmas_built_at', sa.DateTime(), nullable=True),
        sa.Column('embedding_built_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id'),
    )

    op.create_table(
        'description_note_embeddings',
        sa.Column('description_note_id', sa.UUID(), nullable=False),
        sa.Column('embedding', Vector(TEXT_EMBEDDING_DIM), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['description_note_id'], ['description_notes.image_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('description_note_id'),
    )
    # Created directly as HNSW/cosine in one step -- do not repeat the
    # btree-then-fix history image_description_embeddings went through
    # (2026_07_13 created a default btree index, fixed by a follow-up
    # 2026_07_16 migration).
    op.create_index(
        'ix_description_note_embeddings_embedding',
        'description_note_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )

    op.create_table(
        'description_note_lemmas',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('lemma', sa.String(), nullable=False),
        sa.Column('phonetic_code', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id', 'lemma'),
    )
    op.create_index('ix_description_note_lemmas_lemma', 'description_note_lemmas', ['lemma'], unique=False)
    # pg_trgm extension already created by 6fc209b37e8b_add_ocr_lemmas_trigram_index.py
    op.create_index(
        'ix_description_note_lemmas_lemma_trgm', 'description_note_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'}
    )


def downgrade() -> None:
    op.drop_table('description_note_lemmas')
    op.drop_index('ix_description_note_embeddings_embedding', table_name='description_note_embeddings')
    op.drop_table('description_note_embeddings')
    op.drop_table('description_notes')
```

- [ ] **Step 5: Apply the migration to the test database and run the test**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic -c Storage/alembic.ini upgrade head
```

(If run from inside `Storage/`, drop the `-c Storage/alembic.ini` and just use `alembic upgrade head`.)

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_notes_models.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 6: Verify downgrade is clean**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic -c Storage/alembic.ini downgrade -1
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic -c Storage/alembic.ini upgrade head
```
Expected: both commands exit 0, no errors. Re-run Step 5's test command to confirm the tables are back and tests still pass.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/ tests/integration/test_description_notes_models.py
git commit -m "feat: add description_notes/embeddings/lemmas tables"
```

---

## Task 2: Search integration — description notes as a search fallback

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Test: `tests/integration/test_description_note_search_matching.py`

**Interfaces:**
- Consumes: `DescriptionNoteLemma` (Task 1).
- Produces: no new public functions — `matching_image_ids` (existing) now also matches on `DescriptionNoteLemma` rows for the exact and trigram-fuzzy tiers.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_description_note_search_matching.py`:

```python
"""
Integration tests confirming description-note lemmas participate in the main search
match the same way OCR lemmas do -- including as a fallback for images with zero OCR
text, per docs/superpowers/specs/2026-08-20-description-notes-design.md.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids
from Storage.models import DescriptionNoteLemma, Image, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_a_note_is_found_by_exact_lemma_match(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_ocr_still_matches_unaffected(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_token_query_matches_across_ocr_and_note_sources_per_token(db_session):
    """AND is across tokens, OR is across sources within a token -- an image whose
    tokens are split across OCR and note text (neither source alone has both) must
    still match, since each token only needs to hit one source."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="pineapple"))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="upsidedown"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple upsidedown")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_note_lemma_does_not_match_a_different_image(db_session):
    image_a = Image(filename=f"{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b])
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image_a.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image_a.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_note_lemma_matches_via_trigram_fuzzy_fallback(db_session):
    """Exact match fails (typo'd query), trigram similarity against
    description_note_lemmas should still find it -- mirrors the existing OCR
    trigram-fallback behavior, now extended to notes."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pinapple")  # missing one 'e'

    assert ids == {image.id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_search_matching.py -v`
Expected: FAIL — `test_image_with_only_a_note_is_found_by_exact_lemma_match` and the fuzzy test get `ids == set()` instead of `{image.id}` (note lemmas aren't wired into matching yet). `test_image_with_only_ocr_still_matches_unaffected` should already PASS (regression baseline).

- [ ] **Step 3: Extend the search-matching union**

In `repository/ocr_lemmas.py`, update the import line:

```python
from Storage.models import DescriptionNoteLemma, ImageTag, OCRLemma
```

Replace `_exact_lemma_ids`:

```python
async def _exact_lemma_ids(session: AsyncSession, lemma: str) -> set:
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())
    note_subq = select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma == lemma)
    result = await session.execute(union(ocr_subq, tag_subq, note_subq))
    return {row[0] for row in result.all()}
```

Replace `_fuzzy_lemma_ids`:

```python
async def _fuzzy_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Trigram-similarity fallback, written to use the pg_trgm GIN index
    (ix_ocr_lemmas_lemma_trgm, and now also ix_description_note_lemmas_lemma_trgm)
    rather than a sequential scan.

    This is deliberately NOT `func.similarity(col, lemma) >= threshold`,
    which looks equivalent but is not: pg_trgm's GIN opclass only
    index-accelerates the `%` operator, not a raw `similarity()`
    function-call predicate — the latter forces a full seq scan
    (confirmed via EXPLAIN ANALYZE against the populated `metal` database:
    ~497ms seq scan vs ~0.3ms bitmap index scan, on 213,981 rows).

    `%`'s notion of "similar enough" is governed by the session GUC
    `pg_trgm.similarity_threshold` (default 0.3), not by an argument we
    pass in, so we set it explicitly via `SET LOCAL` immediately before
    the query to make `%` respect our configured
    settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD instead of pg_trgm's own
    default. `SET LOCAL`'s value cannot be a bound parameter (Postgres
    raises a syntax error), so the threshold is formatted directly into
    the SQL text — safe only because it's a trusted internal config value,
    never user input (unlike `lemma`, which stays a genuine bound
    parameter via `.op("%")(lemma)`). `SET LOCAL` is scoped to the current
    transaction and automatically reverts at its end, which is safe here
    because Storage/db.py's get_async_db keeps exactly one transaction
    open per request, so this can never leak into a later request that
    reuses the same pooled connection.
    """
    threshold = float(settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD)
    assert 0 < threshold <= 1, f"invalid fuzzy similarity threshold: {threshold}"
    await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {threshold}"))
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma.op("%")(lemma))
    tag_subq = select(distinct(ImageTag.image_id)).where(ImageTag.value.op("%")(lemma))
    note_subq = select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma.op("%")(lemma))
    result = await session.execute(union(ocr_subq, tag_subq, note_subq))
    return {row[0] for row in result.all()}
```

Leave `_stem_lemma_ids` and `_phonetic_lemma_ids` untouched — do not add `DescriptionNoteLemma` to either. Add a one-line comment above `_stem_lemma_ids` explaining why:

```python
async def _stem_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    ... (existing docstring unchanged) ...

    Not extended to DescriptionNoteLemma: this fallback only works because
    OCR indexing pre-stems "en"-tagged rows at index time (see
    OCRLemmasSaver/lemmatize_word's STEMMABLE_LANGUAGES branch). Description
    notes have no per-note language tag, so build_description_note_lemmas.py
    indexes with language=None (unstemmed) -- the note-lemma index never
    contains a pre-stemmed form for this tier to find, so adding it here
    would be dead code. Notes still get exact-match and trigram-fuzzy
    fallback coverage; trigram similarity catches most word-form variation
    in practice.
    """
```

Finally, update the opening lines of `matching_image_ids`'s docstring to mention the third source. Replace:

```python
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.
```

with:

```python
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index, description-note-lemma index, or tags contain every
    query lemma (AND); an empty set means no image matches.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_search_matching.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Run the full integration root (shared-code change gotcha)**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: PASS. This file touches `repository/ocr_lemmas.py`, shared by every caller of `matching_image_ids` — per CLAUDE.md's gotcha, the full root must pass, not just the new file.

- [ ] **Step 6: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_description_note_search_matching.py
git commit -m "feat: description notes participate in main search matching"
```

---

## Task 3: Description note CRUD (backend)

**Files:**
- Modify: `shared/schemas/meme.schema.json`
- Modify: `Backend/app/repositories/image_repository.py`
- Modify: `Backend/app/services/image_service.py`
- Modify: `Backend/app/api/images.py`
- Modify: `backend_api.md`
- Modify: `docs/security/admin-permissions-todo.md`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `Backend/app/types/generated/meme.py` (and any co-generated files)
- Test: `Backend/tests/test_images_endpoints.py`, `Backend/tests/test_image_service.py`, `tests/integration/test_description_notes_repository.py`

**Interfaces:**
- Consumes: `DescriptionNote` (Task 1).
- Produces: `ImageRepository.get_description_note(image_id) -> str | None`, `.set_description_note(image_id, text) -> None`, `.clear_description_note(image_id) -> None`; `ImageService.set_description_note(image_id, text) -> None`, `.clear_description_note(image_id) -> None`; `Meme.descriptionNote: str | None` field; routes `PUT /api/images/{image_id}/description-note`, `DELETE /api/images/{image_id}/description-note`.

- [ ] **Step 1: Add the schema field and regenerate types**

In `shared/schemas/meme.schema.json`, add a new property (after `cosineDistance`, before the closing of `properties`):

```json
    "descriptionNote": {
      "type": "string",
      "description": "Human-written free-text note about the image, if one has been set"
    }
```

Regenerate frontend types (from `Frontend/`):
```bash
cd Frontend && bash generate-types.sh
```
Regenerate backend types (from `Backend/`):
```bash
cd Backend && datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```
Confirm `Backend/app/types/generated/meme.py` now has `descriptionNote: str | None = None` and `Frontend/memes-frontend/src/types/generated/all.d.ts`'s `Meme` interface has `descriptionNote?: string;`. Run `git diff` on both generated paths to confirm only the expected field was added (CI gate compares generated types against a stale diff — this step keeps that gate green).

- [ ] **Step 2: Write the failing repository-level test**

Create `tests/integration/test_description_notes_repository.py`:

```python
"""
Integration tests for ImageRepository's description-note CRUD methods.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma, Image


@pytest.mark.asyncio(loop_scope="session")
async def test_get_description_note_returns_none_when_unset(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_description_note(str(image.id)) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_set_then_get_description_note(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a cat wearing a hat")
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) == "a cat wearing a hat"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_twice_overwrites_text_and_bumps_updated_at(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "first version")
    await db_session.flush()
    first_updated_at = (await db_session.execute(
        select(DescriptionNote.updated_at).where(DescriptionNote.image_id == image.id)
    )).scalar_one()

    await repo.set_description_note(str(image.id), "second version")
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert row.text == "second version"
    assert row.updated_at >= first_updated_at


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_deletes_row_and_cascades(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "will be cleared")
    await db_session.flush()
    db_session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=[0.0] * 1024))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="cleared"))
    await db_session.flush()

    await repo.clear_description_note(str(image.id))
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) is None
    assert (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == image.id)
    )).scalar_one_or_none() is None
    assert (await db_session.execute(
        select(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_on_unset_note_is_a_safe_noop(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.clear_description_note(str(image.id))  # no note ever set
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_notes_repository.py -v`
Expected: FAIL — `AttributeError: 'ImageRepository' object has no attribute 'get_description_note'`.

- [ ] **Step 4: Implement the repository methods**

In `Backend/app/repositories/image_repository.py`, update the model import line to add `DescriptionNote`:

```python
from Storage.models import (
    Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters,
    ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback,
    DescriptionNote, DescriptionNoteEmbedding,
)
```

Add these three methods (e.g. near `get_meme_data`, which they support):

```python
    async def get_description_note(self, image_id: str) -> Optional[str]:
        result = await self.session.execute(
            select(DescriptionNote.text).where(DescriptionNote.image_id == image_id)
        )
        return result.scalar_one_or_none()

    async def set_description_note(self, image_id: str, text: str) -> None:
        stmt = (
            insert(DescriptionNote)
            .values(image_id=image_id, text=text, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"text": text, "updated_at": func.now()},
            )
        )
        await self.session.execute(stmt)

    async def clear_description_note(self, image_id: str) -> None:
        """Deletes the note row; ON DELETE CASCADE removes any
        description_note_embeddings/description_note_lemmas rows for it
        automatically -- no separate cleanup needed."""
        await self.session.execute(
            delete(DescriptionNote).where(DescriptionNote.image_id == image_id)
        )
```

(`insert`, `func`, `delete`, `Optional` are already imported at the top of this file.)

- [ ] **Step 5: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_notes_repository.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Write the failing service-level test**

Add to `Backend/tests/test_image_service.py` (new class, e.g. after `TestGetSimilarDescriptionMode`):

```python
class TestDescriptionNoteService:
    async def test_set_description_note_saves_stripped_text(self, service, mock_repo):
        await service.set_description_note("image-1", "  a cat wearing a hat  ")

        mock_repo.set_description_note.assert_awaited_once_with("image-1", "a cat wearing a hat")
        mock_repo.clear_description_note.assert_not_called()

    async def test_set_description_note_with_empty_text_clears_instead(self, service, mock_repo):
        await service.set_description_note("image-1", "   ")

        mock_repo.clear_description_note.assert_awaited_once_with("image-1")
        mock_repo.set_description_note.assert_not_called()

    async def test_clear_description_note_delegates_to_repo(self, service, mock_repo):
        await service.clear_description_note("image-1")

        mock_repo.clear_description_note.assert_awaited_once_with("image-1")

    async def test_get_meme_includes_description_note(self, service, mock_repo):
        mock_repo.get_meme_data.return_value = ("file.jpg", [], [])
        mock_repo.get_is_flagged.return_value = False
        mock_repo.get_description_note.return_value = "a cat wearing a hat"

        result = await service.get_meme("image-1")

        assert result.descriptionNote == "a cat wearing a hat"

    async def test_get_meme_with_no_note_leaves_field_none(self, service, mock_repo):
        mock_repo.get_meme_data.return_value = ("file.jpg", [], [])
        mock_repo.get_is_flagged.return_value = False
        mock_repo.get_description_note.return_value = None

        result = await service.get_meme("image-1")

        assert result.descriptionNote is None
```

- [ ] **Step 7: Run test to verify it fails**

Run: `cd Backend && pytest tests/test_image_service.py -v`
Expected: FAIL — `AttributeError: 'ImageService' object has no attribute 'set_description_note'`.

- [ ] **Step 8: Implement the service methods and update `get_meme`**

In `Backend/app/services/image_service.py`, add two methods (e.g. after `_toggle_description_feedback`):

```python
    async def set_description_note(self, image_id: str, text: str) -> None:
        text = text.strip()
        if not text:
            await self.repo.clear_description_note(image_id)
            return
        await self.repo.set_description_note(image_id, text)

    async def clear_description_note(self, image_id: str) -> None:
        await self.repo.clear_description_note(image_id)
```

Update `get_meme`:

```python
    async def get_meme(self, image_id: str) -> Meme:
        filename, texts, tags = await self.repo.get_meme_data(image_id)
        is_flagged = await self.repo.get_is_flagged(image_id)
        note_text = await self.repo.get_description_note(image_id)
        return Meme(
            id=image_id,
            imageUrl=f"/api/images/{image_id}",
            text=texts,
            tags=[MemeTag(name=value, category=key) for key, value in tags],
            originalFileName=filename,
            flagged=is_flagged,
            descriptionNote=note_text,
        )
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd Backend && pytest tests/test_image_service.py -v`
Expected: PASS.

- [ ] **Step 10: Write the failing router test**

Add to `Backend/tests/test_images_endpoints.py` (new class, e.g. after `TestGetImageDescriptions`):

```python
class TestDescriptionNoteEndpoints:
    """Tests for PUT/DELETE /api/images/{image_id}/description-note."""

    def test_put_description_note_success(self, client, mock_image_service):
        mock_image_service.set_description_note.return_value = None

        response = client.put("/api/images/123/description-note", json={"text": "a cat wearing a hat"})

        assert response.status_code == 200
        mock_image_service.set_description_note.assert_called_once_with("123", "a cat wearing a hat")

    def test_put_description_note_requires_text_field(self, client, mock_image_service):
        response = client.put("/api/images/123/description-note", json={})

        assert response.status_code == 422
        mock_image_service.set_description_note.assert_not_called()

    def test_delete_description_note_success(self, client, mock_image_service):
        mock_image_service.clear_description_note.return_value = None

        response = client.delete("/api/images/123/description-note")

        assert response.status_code == 200
        mock_image_service.clear_description_note.assert_called_once_with("123")
```

- [ ] **Step 11: Run test to verify it fails**

Run: `cd Backend && pytest tests/test_images_endpoints.py -v -k DescriptionNote`
Expected: FAIL — 404 (no such route yet).

- [ ] **Step 12: Add the routes**

In `Backend/app/api/images.py`, add a request body model near the other small `BaseModel`s (e.g. after `DuplicateUndoDismissRequest`):

```python
class DescriptionNoteRequest(BaseModel):
    text: str
```

Add the two routes (e.g. right after `reject_description`, grouping with the other note/description-related endpoints — path shape `{image_id}/description-note` never collides with the bare-`{image_id}` file-serving route at the bottom of this file, so ordering relative to it doesn't matter):

```python
@router.put("/{image_id}/description-note")
async def set_description_note(
    image_id: str,
    body: DescriptionNoteRequest,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    await service.set_description_note(image_id, body.text)


@router.delete("/{image_id}/description-note")
async def delete_description_note(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    await service.clear_description_note(image_id)
```

- [ ] **Step 13: Run test to verify it passes**

Run: `cd Backend && pytest tests/test_images_endpoints.py -v -k DescriptionNote`
Expected: PASS (all 3 tests).

- [ ] **Step 14: Run the full Backend test root**

Run: `cd Backend && pytest`
Expected: PASS (no regressions from the `Meme` schema/model change).

- [ ] **Step 15: Update `backend_api.md`**

Add a new subsection near "Get Meme Details" (e.g. right after it), matching the existing doc style:

```markdown
#### Set Description Note

Set (or overwrite) a human-written free-text note on an image. No authentication today --
see docs/security/admin-permissions-todo.md.

- **URL**: `/api/images/{image_id}/description-note`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Body**: `{"text": "..."}`
- **Response**: Success (no content). An empty/whitespace-only `text` clears the note
  (equivalent to `DELETE`) rather than storing an empty string.
- **Cache**: no-cache
- **Example**: `PUT /api/images/abc123/description-note` with body `{"text": "a cat wearing a hat"}`

#### Delete Description Note

Clear an image's description note. Also removes its embedding and lemma index rows
(`ON DELETE CASCADE`), synchronously -- no batch run required for a cleared note to stop
appearing in search/similarity results. No authentication today -- see
docs/security/admin-permissions-todo.md.

- **URL**: `/api/images/{image_id}/description-note`
- **Method**: `DELETE`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content). Safe to call when no note is currently set.
- **Cache**: no-cache
- **Example**: `DELETE /api/images/abc123/description-note`
```

Also add one line to the "Get Meme Details" section's response description noting the new field: `Meme.descriptionNote` (string, optional — the image's human-written note, absent if none set).

- [ ] **Step 16: Update `docs/security/admin-permissions-todo.md`**

Append to the "Endpoints needing permission controls once a model exists" list:

```markdown
- `PUT /api/images/{id}/description-note` — anyone can currently overwrite any image's note.
- `DELETE /api/images/{id}/description-note` — anyone can currently clear any image's note.
```

- [ ] **Step 17: Commit**

```bash
git add shared/schemas/meme.schema.json Frontend/memes-frontend/src/types/generated/all.d.ts \
  Backend/app/types/generated/ Backend/app/repositories/image_repository.py \
  Backend/app/services/image_service.py Backend/app/api/images.py backend_api.md \
  docs/security/admin-permissions-todo.md Backend/tests/test_images_endpoints.py \
  Backend/tests/test_image_service.py tests/integration/test_description_notes_repository.py
git commit -m "feat: add description note CRUD endpoints"
```

---

## Task 4: Similar-images `source=description_note`

**Files:**
- Modify: `Backend/app/repositories/image_repository.py`
- Modify: `Backend/app/services/image_service.py`
- Modify: `Backend/app/api/images.py`
- Modify: `backend_api.md`
- Test: `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`, `tests/integration/test_similar_images.py`

**Interfaces:**
- Consumes: `DescriptionNoteEmbedding` (Task 1).
- Produces: `ImageRepository.get_description_note_embedding(image_id) -> list[float] | None`, `.get_similar_by_note(image_id, embedding, limit) -> list[tuple]` (same row shape as `get_similar`: `(image_id, distance, filename, flagged)`); `source="description_note"` branch in `ImageService.get_similar`; router `Literal["image", "description", "description_note"]`.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/integration/test_similar_images.py` (reusing its existing `_normalize`/fixtures pattern; add new imports and helpers at the top as needed):

```python
from Storage.models import DescriptionNote, DescriptionNoteEmbedding

_NOTE_DIM = 1024


def _unit_note_vector(index: int) -> list[float]:
    vec = [0.0] * _NOTE_DIM
    vec[index] = 1.0
    return vec


async def _insert_image_with_note_embedding(session, embedding_values: list[float], status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text="a note"))
    await session.flush()
    session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=embedding_values))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_note_finds_close_embedding(db_session):
    a = await _insert_image_with_note_embedding(db_session, _unit_note_vector(0))
    b = await _insert_image_with_note_embedding(db_session, _unit_note_vector(0))  # identical

    repo = ImageRepository(db_session)
    embedding = await repo.get_description_note_embedding(str(a))
    rows = await repo.get_similar_by_note(str(a), embedding, limit=10)

    similar_ids = {row[0] for row in rows}
    assert b in similar_ids
    assert a not in similar_ids  # excludes itself


@pytest.mark.asyncio(loop_scope="session")
async def test_get_description_note_embedding_returns_none_when_absent(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_description_note_embedding(str(image.id)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_similar_images.py -v -k note`
Expected: FAIL — `AttributeError: 'ImageRepository' object has no attribute 'get_description_note_embedding'`.

- [ ] **Step 3: Implement the repository methods**

In `Backend/app/repositories/image_repository.py`, add (e.g. right after `get_similar`):

```python
    async def get_description_note_embedding(self, image_id: str):
        result = await self.session.execute(
            select(DescriptionNoteEmbedding.embedding)
            .where(DescriptionNoteEmbedding.description_note_id == image_id)
        )
        return result.scalars().first()

    async def get_similar_by_note(self, image_id: str, embedding, limit: int = 10):
        img = aliased(Image)
        embed = aliased(DescriptionNoteEmbedding)
        extras = aliased(ImageExtras)
        result = await self.session.execute(
            select(embed.description_note_id, embed.embedding.cosine_distance(embedding), img.filename, extras.flagged)
            .join(img, img.id == embed.description_note_id)
            .outerjoin(extras, img.id == extras.image_id)
            .filter(embed.description_note_id != image_id, img.status == "active")
            .order_by(embed.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return result.all()
```

(`DescriptionNoteEmbedding` is already imported from Task 3's Step 4 import-line change.)

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_similar_images.py -v -k note`
Expected: PASS.

- [ ] **Step 5: Write the failing service test**

Add to `Backend/tests/test_image_service.py` (new class):

```python
class TestGetSimilarDescriptionNoteMode:
    async def test_raises_404_when_no_note_embedding(self, service, mock_repo):
        mock_repo.get_description_note_embedding.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.get_similar("image-1", limit=10, source="description_note")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "No description note embedding found for this image"
        mock_repo.get_similar_by_note.assert_not_called()

    async def test_happy_path_calls_repo_get_similar_by_note(self, service, mock_repo):
        embedding = [0.1, 0.2, 0.3]
        mock_repo.get_description_note_embedding.return_value = embedding
        mock_repo.get_similar_by_note.return_value = [
            ("image-2", 0.02, "second.png", False),
        ]

        result = await service.get_similar("image-1", limit=5, source="description_note")

        mock_repo.get_similar_by_note.assert_awaited_once_with("image-1", embedding, limit=5)
        assert [item.id for item in result.items] == ["image-2"]
        assert [item.cosineDistance for item in result.items] == [0.02]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd Backend && pytest tests/test_image_service.py -v -k DescriptionNoteMode`
Expected: FAIL — `source="description_note"` currently falls through to the `else` (image) branch, so `mock_repo.get_embedding` gets called instead, and the mocked `get_description_note_embedding`/`get_similar_by_note` assertions fail.

- [ ] **Step 7: Implement the service dispatch branch**

In `Backend/app/services/image_service.py`, update `get_similar`:

```python
    async def get_similar(self, image_id: str, limit: int = 10, source: str = "image") -> MemeSearchResponse:
        if source == "description":
            if not await self.repo.has_description_embedding(image_id):
                raise HTTPException(status_code=404, detail="No description embedding found for this image")
            rows = await self.repo.get_similar_by_description(image_id, limit=limit)
        elif source == "description_note":
            embedding = await self.repo.get_description_note_embedding(image_id)
            if embedding is None:
                raise HTTPException(status_code=404, detail="No description note embedding found for this image")
            rows = await self.repo.get_similar_by_note(image_id, embedding, limit=limit)
        else:
            embedding = await self.repo.get_embedding(image_id)
            if embedding is None:
                raise HTTPException(status_code=404, detail="No embedding found for this image")
            rows = await self.repo.get_similar(image_id, embedding, limit=limit)

        items = [
            Meme(
                id=str(iid),
                imageUrl=f"/api/images/{iid}",
                text=[],
                tags=[],
                originalFileName=fname,
                flagged=flagged if flagged is not None else False,
                cosineDistance=float(dist),
            )
            for iid, dist, fname, flagged in rows
        ]
        return MemeSearchResponse(items=items)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd Backend && pytest tests/test_image_service.py -v`
Expected: PASS (all tests in the file, including the pre-existing `description`/`image` mode tests — unaffected).

- [ ] **Step 9: Write the failing router test**

Add to `Backend/tests/test_images_endpoints.py`, in `TestGetSimilarImages` (mirrors the existing `source="description"` test near line 532):

```python
    def test_get_similar_images_with_description_note_source(self, client, mock_image_service):
        mock_image_service.get_similar.return_value = MemeSearchResponse(items=[], facets=[], hasNext=False)

        response = client.get("/api/images/123/similar?source=description_note")

        assert response.status_code == 200
        mock_image_service.get_similar.assert_called_once_with("123", limit=10, source="description_note")
```

- [ ] **Step 10: Run test to verify it fails**

Run: `cd Backend && pytest tests/test_images_endpoints.py -v -k description_note_source`
Expected: FAIL — 422 Unprocessable Entity (`"description_note"` not a valid value for the current `Literal["image", "description"]`).

- [ ] **Step 11: Extend the router's `source` parameter**

In `Backend/app/api/images.py`, update `get_similar_images`:

```python
@router.get("/{image_id}/similar", response_model=MemeSearchResponse)
async def get_similar_images(
    image_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=100),
    source: Literal["image", "description", "description_note"] = "image",
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_similar(image_id, limit=limit, source=source)
```

- [ ] **Step 12: Run test to verify it passes**

Run: `cd Backend && pytest tests/test_images_endpoints.py -v -k description_note_source`
Expected: PASS.

- [ ] **Step 13: Run the full Backend test root**

Run: `cd Backend && pytest`
Expected: PASS.

- [ ] **Step 14: Update `backend_api.md`**

In the "Get Similar Images" section, update the `source` query parameter description:

```markdown
  - `source` (optional): `image` (default) ranks by CLIP visual-embedding similarity; `description` ranks by LLM-description text-embedding similarity (only images sharing at least one prompt's description embedding with the source image are candidates); `description_note` ranks by human-written description-note text-embedding similarity. Returns 404 if the source image has no embedding of the requested kind.
```

Add a new example line after the existing `source=description` example:

```markdown
- **Example**: `GET /api/images/abc123/similar?source=description_note&limit=10`
```

- [ ] **Step 15: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py \
  Backend/app/api/images.py backend_api.md Backend/tests/test_image_service.py \
  Backend/tests/test_images_endpoints.py tests/integration/test_similar_images.py
git commit -m "feat: add source=description_note to similar-images endpoint"
```

---

## Task 5: Batch job — description note lemma indexing

**Files:**
- Create: `repository/description_note_lemmas.py`
- Create: `batch/build_description_note_lemmas.py`
- Modify: `environments/batch_registry.yaml`
- Modify: `CLAUDE.md`
- Test: `tests/integration/test_description_note_lemmas_repository.py`, `tests/integration/test_build_description_note_lemmas.py`

**Interfaces:**
- Consumes: `DescriptionNote`, `DescriptionNoteLemma` (Task 1).
- Produces: `DescriptionNoteLemmasRepository.get_notes_needing_lemmas() -> list[tuple[UUID, str]]`, `.mark_lemmas_built(image_id) -> None`; `DescriptionNoteLemmasSaver` (async context manager) with `.replace_lemmas(image_id, lemmas: set[str]) -> None`; `batch.build_description_note_lemmas.run(session, morph, min_word_length) -> None` (session-injected, directly testable — mirrors `build_ocr_lemmas.run`); `.main(trigger="manual", run_id=None) -> None`.

- [ ] **Step 1: Write the failing repository test**

Create `tests/integration/test_description_note_lemmas_repository.py`:

```python
"""
Integration tests for repository/description_note_lemmas.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from repository.description_note_lemmas import DescriptionNoteLemmasRepository, DescriptionNoteLemmasSaver
from Storage.models import DescriptionNote, DescriptionNoteLemma, Image


async def _insert_note(session, text: str) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text=text))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_new_note_is_returned_as_needing_lemmas(db_session):
    image_id = await _insert_note(db_session, "a cat wearing a hat")

    rows = await DescriptionNoteLemmasRepository(db_session).get_notes_needing_lemmas()

    assert (image_id, "a cat wearing a hat") in rows


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_lemmas_built_excludes_note_from_next_query(db_session):
    image_id = await _insert_note(db_session, "a dog in sunglasses")
    repo = DescriptionNoteLemmasRepository(db_session)

    await repo.mark_lemmas_built(image_id)
    await db_session.flush()

    rows = await repo.get_notes_needing_lemmas()
    assert image_id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_edited_note_becomes_stale_again_after_being_built(db_session):
    """Confirms the built_at < updated_at staleness check: re-editing a note
    already indexed must make it eligible for re-indexing again."""
    image_id = await _insert_note(db_session, "original text")
    repo = DescriptionNoteLemmasRepository(db_session)
    await repo.mark_lemmas_built(image_id)
    await db_session.flush()
    assert image_id not in {row[0] for row in await repo.get_notes_needing_lemmas()}

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    note.text = "edited text"
    note.updated_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    await db_session.flush()

    rows = await repo.get_notes_needing_lemmas()
    assert image_id in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_replaces_existing_lemmas_rather_than_merging(db_session):
    image_id = await _insert_note(db_session, "first version")
    db_session.add(DescriptionNoteLemma(image_id=image_id, lemma="stale"))
    await db_session.flush()

    async with DescriptionNoteLemmasSaver(db_session) as saver:
        await saver.replace_lemmas(image_id, {"fresh"})

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image_id)
    )).scalars().all()
    assert set(rows) == {"fresh"}


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_with_empty_lemma_set_just_clears(db_session):
    image_id = await _insert_note(db_session, "only stopwords")
    db_session.add(DescriptionNoteLemma(image_id=image_id, lemma="old"))
    await db_session.flush()

    async with DescriptionNoteLemmasSaver(db_session) as saver:
        await saver.replace_lemmas(image_id, set())

    rows = (await db_session.execute(
        select(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image_id)
    )).scalars().all()
    assert rows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_lemmas_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repository.description_note_lemmas'`.

- [ ] **Step 3: Implement the repository**

Create `repository/description_note_lemmas.py`:

```python
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionNote, DescriptionNoteLemma


class DescriptionNoteLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_notes_needing_lemmas(self):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .where(or_(
                DescriptionNote.lemmas_built_at.is_(None),
                DescriptionNote.lemmas_built_at < DescriptionNote.updated_at,
            ))
        )
        return result.all()

    async def mark_lemmas_built(self, image_id) -> None:
        await self.session.execute(
            update(DescriptionNote)
            .where(DescriptionNote.image_id == image_id)
            .values(lemmas_built_at=func.now())
        )


class DescriptionNoteLemmasSaver:
    """Unlike OCRLemmasSaver.add_lemmas (append-only -- OCR text is never
    edited after extraction), a description note can be edited to remove
    words, so replace_lemmas clears any existing rows for the image before
    inserting the new set, rather than merging via on_conflict_do_nothing."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    async def replace_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        await self.session.execute(
            delete(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image_id)
        )
        if not lemmas:
            return
        stmt = insert(DescriptionNoteLemma).values([
            {
                "image_id": image_id,
                "lemma": lemma,
                "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
            }
            for lemma in lemmas
        ])
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total notes indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_lemmas_repository.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Write the failing batch-script test**

Create `tests/integration/test_build_description_note_lemmas.py`. This calls `run(session, ...)` directly with the `db_session` fixture — **not** `_process()`/`main()`, which open their own separate `AsyncSessionLocal()` connection. `db_session` (`tests/integration/conftest.py`) wraps its work in an outer transaction that is always rolled back at test end, joined via `join_transaction_mode="create_savepoint"` — a `session.commit()` inside the test only commits an inner SAVEPOINT, invisible to any other connection. A separate `AsyncSessionLocal()` session opened by `_process()` would therefore see zero rows no matter what the test commits first. `run(session, ...)` sidesteps this by working on the exact same session/connection the test already holds — the same split `batch/build_ocr_lemmas.py` uses (`run(session, ...)` vs. its `_process()` wrapper), verified against `tests/integration/test_build_ocr_lemmas.py`, which calls `run(db_session, ...)` directly for this exact reason.

```python
"""
Integration test for batch/build_description_note_lemmas.py's run() -- the full
staleness-selection -> lemma-building -> saving -> mark-built pipeline, exercised
end-to-end against a real database. Mirrors tests/integration/test_build_ocr_lemmas.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_description_note_lemmas import run
from rules.normalize import make_morph
from Storage.models import DescriptionNote, DescriptionNoteLemma, Image

_MORPH = make_morph()


@pytest.mark.asyncio(loop_scope="session")
async def test_run_indexes_notes_needing_lemmas_and_marks_built(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNote(image_id=image.id, text="a funny cat meme"))
    await db_session.flush()

    await run(db_session, morph=_MORPH, min_word_length=3)

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all()
    assert "cat" in rows
    assert "meme" in rows

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert note.lemmas_built_at is not None
```

- [ ] **Step 6: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_description_note_lemmas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.build_description_note_lemmas'`.

- [ ] **Step 7: Implement the batch script**

Create `batch/build_description_note_lemmas.py`. Note the `run(session, morph, min_word_length)` / `_process()` split — `run` takes an injected session (directly testable, matching `build_ocr_lemmas.run`), `_process` is the thin wrapper that owns the real session lifecycle for `main()`:

```python
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_note_lemmas import DescriptionNoteLemmasRepository, DescriptionNoteLemmasSaver
from rules.normalize import make_morph, normalize
from Storage.db import AsyncSessionLocal


async def run(session, morph, min_word_length: int) -> None:
    lemmas_repo = DescriptionNoteLemmasRepository(session)
    rows = await lemmas_repo.get_notes_needing_lemmas()
    print(f"Found {len(rows)} description note(s) needing lemma indexing")

    tracker = ProgressTracker(len(rows), report_every=100, report_interval_secs=10)

    async with DescriptionNoteLemmasSaver(session) as saver:
        for image_id, text in rows:
            # language=None: no per-note language tag exists, so this
            # matches matching_image_ids' own query-time convention
            # (script-based pymorphy3 fallback). Means the note-lemma
            # index is never pre-stemmed for English -- see the comment
            # above _stem_lemma_ids in repository/ocr_lemmas.py.
            lemma_set = normalize(
                text, morph, min_length=min_word_length, language=None, keep_digit_tokens=True
            )
            await saver.replace_lemmas(image_id, lemma_set)
            await lemmas_repo.mark_lemmas_built(image_id)
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
        async with tracked_run(kind="build_description_note_lemmas", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 8: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_description_note_lemmas.py -v`
Expected: PASS.

- [ ] **Step 9: Register the job and document it**

In `environments/batch_registry.yaml`, add:

```yaml
build_description_note_lemmas:
  module: batch.build_description_note_lemmas
  kind: build_description_note_lemmas
```

In `CLAUDE.md`'s batch pipeline list, add a new line directly after the existing `build_ocr_lemmas` entry:

```
build_description_note_lemmas → per-image lemma index for human description notes (see
                              docs/superpowers/specs/2026-08-20-description-notes-design.md);
                              admin-triggerable from /admin/batches, manual-trigger only, not
                              scheduled
```

- [ ] **Step 10: Run the full integration root**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add repository/description_note_lemmas.py batch/build_description_note_lemmas.py \
  environments/batch_registry.yaml CLAUDE.md \
  tests/integration/test_description_note_lemmas_repository.py \
  tests/integration/test_build_description_note_lemmas.py
git commit -m "feat: add build_description_note_lemmas batch job"
```

---

## Task 6: Batch job — description note embeddings

**Files:**
- Create: `repository/description_note_embeddings.py`
- Create: `batch/build_description_note_embeddings.py`
- Modify: `environments/batch_registry.yaml`
- Modify: `CLAUDE.md`
- Test: `tests/integration/test_description_note_embeddings_repository.py`

**Interfaces:**
- Consumes: `DescriptionNote`, `DescriptionNoteEmbedding` (Task 1).
- Produces: `DescriptionNoteEmbeddingsRepository.get_notes_needing_embedding() -> list[tuple[UUID, str]]`, `.save(image_id, embedding: list[float]) -> None` (upserts the embedding and marks `embedding_built_at`); `batch.build_description_note_embeddings.main(trigger="manual", run_id=None) -> None`.

- [ ] **Step 1: Write the failing repository test**

Create `tests/integration/test_description_note_embeddings_repository.py`:

```python
"""
Integration tests for repository/description_note_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from repository.description_note_embeddings import DescriptionNoteEmbeddingsRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, Image

_DIM = 1024


async def _insert_note(session, text: str) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text=text))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_new_note_is_returned_as_needing_embedding(db_session):
    image_id = await _insert_note(db_session, "a cat wearing a hat")

    rows = await DescriptionNoteEmbeddingsRepository(db_session).get_notes_needing_embedding()

    assert (image_id, "a cat wearing a hat") in rows


@pytest.mark.asyncio(loop_scope="session")
async def test_save_creates_embedding_and_marks_built(db_session):
    image_id = await _insert_note(db_session, "a dog in sunglasses")
    repo = DescriptionNoteEmbeddingsRepository(db_session)

    await repo.save(image_id, [0.0] * _DIM)
    await db_session.flush()

    embedding_row = (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == image_id)
    )).scalar_one()
    assert embedding_row.embedding is not None

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    assert note.embedding_built_at is not None

    rows = await repo.get_notes_needing_embedding()
    assert image_id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_twice_overwrites_existing_embedding(db_session):
    image_id = await _insert_note(db_session, "overwrite me")
    repo = DescriptionNoteEmbeddingsRepository(db_session)
    await repo.save(image_id, [0.0] * _DIM)
    await db_session.flush()

    second_vector = [1.0] * _DIM
    await repo.save(image_id, second_vector)
    await db_session.flush()

    rows = (await db_session.execute(select(DescriptionNoteEmbedding))).scalars().all()
    assert len(rows) == 1  # upsert, not a second row


@pytest.mark.asyncio(loop_scope="session")
async def test_edited_note_becomes_stale_again_after_being_embedded(db_session):
    image_id = await _insert_note(db_session, "original text")
    repo = DescriptionNoteEmbeddingsRepository(db_session)
    await repo.save(image_id, [0.0] * _DIM)
    await db_session.flush()
    assert image_id not in {row[0] for row in await repo.get_notes_needing_embedding()}

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    note.text = "edited text"
    note.updated_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    await db_session.flush()

    rows = await repo.get_notes_needing_embedding()
    assert image_id in {row[0] for row in rows}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_embeddings_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repository.description_note_embeddings'`.

- [ ] **Step 3: Implement the repository**

Create `repository/description_note_embeddings.py`:

```python
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from Storage.models import DescriptionNote, DescriptionNoteEmbedding


class DescriptionNoteEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_notes_needing_embedding(self):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .where(or_(
                DescriptionNote.embedding_built_at.is_(None),
                DescriptionNote.embedding_built_at < DescriptionNote.updated_at,
            ))
        )
        return result.all()

    async def save(self, image_id, embedding: list[float]) -> None:
        stmt = (
            insert(DescriptionNoteEmbedding)
            .values(description_note_id=image_id, embedding=embedding)
            .on_conflict_do_update(
                index_elements=["description_note_id"],
                set_={"embedding": embedding},
            )
        )
        await self.session.execute(stmt)
        await self.session.execute(
            update(DescriptionNote)
            .where(DescriptionNote.image_id == image_id)
            .values(embedding_built_at=func.now())
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_description_note_embeddings_repository.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Implement the batch script**

No dedicated integration test for this file itself — matching the existing precedent (`batch/build_image_description_embeddings.py` has no batch-script-level test either, only `ai/sbert`'s own unit test and the repository-level tests above cover the logic that doesn't require loading the real SBERT model). Create `batch/build_description_note_embeddings.py`:

```python
import argparse
import asyncio
import uuid

from ai.sbert import SbertModel
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_note_embeddings import DescriptionNoteEmbeddingsRepository
from Storage.db import AsyncSessionLocal

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


async def _process() -> None:
    async with AsyncSessionLocal() as session:
        repo = DescriptionNoteEmbeddingsRepository(session)
        rows = await repo.get_notes_needing_embedding()
        print(f"Found {len(rows)} description note(s) needing embeddings")

        embedder = SbertModel(model_name=EMBEDDING_MODEL)
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        for i, (image_id, text) in enumerate(rows):
            vector = embedder.embed_text(text)
            await repo.save(image_id, vector.tolist())
            tracker.mark_done()
            if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
                await session.commit()

        await session.commit()
    tracker.summary()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_description_note_embeddings", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 6: Manual smoke test**

Since there's no automated test for this file (matching precedent), verify it runs against the test database with a real note and the real SBERT model:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONIOENCODING=utf-8 \
  python -c "
import asyncio
from Storage.db import AsyncSessionLocal
from Storage.models import DescriptionNote, Image
import uuid

async def seed():
    async with AsyncSessionLocal() as s:
        img = Image(filename=f'{uuid.uuid4()}.jpg')
        s.add(img)
        await s.flush()
        s.add(DescriptionNote(image_id=img.id, text='a cat wearing a hat'))
        await s.commit()
        print('seeded', img.id)

asyncio.run(seed())
"
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONIOENCODING=utf-8 \
  python -m batch.build_description_note_embeddings
```

Expected: script prints `Found 1 description note(s) needing embeddings`, then a progress summary, exits 0. Confirm via `psql`/a quick `select count(*) from description_note_embeddings;` that a row now exists.

- [ ] **Step 7: Register the job and document it**

In `environments/batch_registry.yaml`, add:

```yaml
build_description_note_embeddings:
  module: batch.build_description_note_embeddings
  kind: build_description_note_embeddings
```

In `CLAUDE.md`'s batch pipeline list, add a line directly after the `build_description_note_lemmas` line added in Task 5:

```
build_description_note_embeddings → SBERT embeddings (bge-large-en-v1.5, 1024-dim) for
                              non-empty description notes; admin-triggerable from
                              /admin/batches, manual-trigger only, not scheduled
```

- [ ] **Step 8: Commit**

```bash
git add repository/description_note_embeddings.py batch/build_description_note_embeddings.py \
  environments/batch_registry.yaml CLAUDE.md \
  tests/integration/test_description_note_embeddings_repository.py
git commit -m "feat: add build_description_note_embeddings batch job"
```

---

## Task 7: Frontend — API client and UI

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts`
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.tsx`
- Test: `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`

**Interfaces:**
- Consumes: `Meme.descriptionNote` field (Task 3), `PUT`/`DELETE /api/images/{id}/description-note` (Task 3).
- Produces: `MemesApi.setDescriptionNote(imageId: string, text: string): Promise<void>`, `.deleteDescriptionNote(imageId: string): Promise<void>`.

- [ ] **Step 1: Add the two methods to the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, add after `setDescriptionFeedback`:

```ts
  setDescriptionNote(imageId: string, text: string): Promise<void>

  deleteDescriptionNote(imageId: string): Promise<void>
```

- [ ] **Step 2: Add the two methods to `mockApi.ts`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, add to the returned object (e.g. after `setDescriptionFeedback`):

```ts
    setDescriptionNote: vi.fn().mockResolvedValue(undefined),
    deleteDescriptionNote: vi.fn().mockResolvedValue(undefined),
```

- [ ] **Step 3: Write the failing component test**

Add to `Frontend/memes-frontend/src/components/MemeDetails.test.tsx` (new `describe` block, e.g. after `descriptions`):

```tsx
describe('description note', () => {
  it('renders the existing note text in a textarea', async () => {
    renderMemeDetails({ ...DEFAULT_MOCK_MEME, descriptionNote: 'a cat wearing a hat' })
    await act(async () => {})

    expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('a cat wearing a hat')
  })

  it('renders an empty textarea when no note is set', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME)
    await act(async () => {})

    expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
  })

  it('clicking Save calls setDescriptionNote with the current textarea value', async () => {
    const { api } = renderMemeDetails(DEFAULT_MOCK_MEME)
    await act(async () => {})

    const textarea = screen.getByRole('textbox', { name: 'Description note' })
    await act(async () => {
      fireEvent.change(textarea, { target: { value: 'a dog in sunglasses' } })
    })
    await act(async () => {
      screen.getByRole('button', { name: 'Save note' }).click()
    })

    expect(api.setDescriptionNote).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'a dog in sunglasses')
  })

  it('clicking Clear calls deleteDescriptionNote and empties the textarea', async () => {
    const { api } = renderMemeDetails({ ...DEFAULT_MOCK_MEME, descriptionNote: 'a cat wearing a hat' })
    await act(async () => {})

    await act(async () => {
      screen.getByRole('button', { name: 'Clear note' }).click()
    })

    expect(api.deleteDescriptionNote).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
    expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
  })
})
```

Add `fireEvent` to the existing `@testing-library/react` import at the top of the file:

```tsx
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd Frontend/memes-frontend && pnpm test -- MemeDetails`
Expected: FAIL — `Unable to find role="textbox" with name "Description note"` (no such UI yet), and `api.setDescriptionNote is not a function` (mock exists but `MemeDetails` never calls it).

- [ ] **Step 5: Implement the UI**

In `Frontend/memes-frontend/src/components/MemeDetails.tsx`:

Add a new piece of state near the other `useState` calls:

```tsx
  const [noteText, setNoteText] = useState(meme.descriptionNote ?? "")
```

Add a `useEffect` to keep it in sync when `meme` changes (the existing `useFetchById` hooks re-fetch on `meme.id` change, but `descriptionNote` is a plain prop field, not fetched separately):

```tsx
  useEffect(() => {
    setNoteText(meme.descriptionNote ?? "")
  }, [meme.id, meme.descriptionNote])
```

Add two handlers near `setDescriptionFeedback`:

```tsx
  function saveNote() {
    memesApi.setDescriptionNote(meme.id, noteText)
  }

  function clearNote() {
    memesApi.deleteDescriptionNote(meme.id).then(() => setNoteText(""))
  }
```

Add the UI block in the JSX, between the "Tags" block and the "Flagged" checkbox block:

```tsx
        <div>
          <label htmlFor="description-note" className="block mb-1">
            <strong>Description note:</strong>
          </label>
          <textarea
            id="description-note"
            aria-label="Description note"
            value={noteText}
            onChange={e => setNoteText(e.target.value)}
            className="w-full border rounded p-2 text-sm"
            rows={3}
          />
          <div className="flex gap-2 mt-1">
            <button
              onClick={saveNote}
              aria-label="Save note"
              className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-100"
            >
              Save note
            </button>
            <button
              onClick={clearNote}
              aria-label="Clear note"
              className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-100"
            >
              Clear note
            </button>
          </div>
        </div>
```

Note: no permission gating on this UI (matches the "everyone can edit for now" decision) — leave a one-line comment above the block pointing at the pending-auth doc:

```tsx
        {/* No permission gating yet -- see docs/security/admin-permissions-todo.md */}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd Frontend/memes-frontend && pnpm test -- MemeDetails`
Expected: PASS (all tests in the file, including the pre-existing ones — unaffected).

- [ ] **Step 7: Implement the HTTP client methods**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, add two methods (e.g. after `setDescriptionFeedback`):

```ts
  async setDescriptionNote(imageId: string, text: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/images/${imageId}/description-note`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ text }),
    })

    if (!response.ok) {
      throw new Error(`Failed to set description note: ${response.status}`)
    }
  }

  async deleteDescriptionNote(imageId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/images/${imageId}/description-note`, {
      method: "DELETE",
      headers: { "Accept": "application/json" },
    })

    if (!response.ok) {
      throw new Error(`Failed to delete description note: ${response.status}`)
    }
  }
```

(This is the first `DELETE` call in the frontend — every other "delete-like" mutation so far is modeled as a `POST` action-endpoint (e.g. `undo-dismiss`). That's fine; there's simply no existing DELETE to pattern-match against, so this follows the same `if (!response.ok) throw` convention every other method here uses.)

- [ ] **Step 8: Run the full frontend check suite**

```bash
cd Frontend/memes-frontend
pnpm build   # tsc -b && vite build
pnpm lint:ci
pnpm test
```
Expected: all three pass with zero errors/warnings.

- [ ] **Step 9: Manual verification in the browser**

Start one environment's backend and frontend (e.g. metal):

```powershell
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
```
```bash
cd Frontend/memes-frontend && pnpm dev
```

Open a meme detail page, confirm: the note textarea appears, typing + Save persists (reload the page and the text is still there), Clear empties it and a reload confirms it stays empty. Also hit `GET /api/images/meme/{id}` directly (or check the network tab) to confirm `descriptionNote` appears in the JSON response.

- [ ] **Step 10: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts \
  Frontend/memes-frontend/src/test/mockApi.ts Frontend/memes-frontend/src/components/MemeDetails.tsx \
  Frontend/memes-frontend/src/components/MemeDetails.test.tsx
git commit -m "feat: add description note editing UI"
```

---

## Final verification (whole-branch)

After all 7 tasks are complete, before merging:

- [ ] `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v` — full root, PASS.
- [ ] `cd Backend && pytest` — full root, PASS.
- [ ] `cd Frontend/memes-frontend && pnpm build && pnpm lint:ci && pnpm test` — all PASS.
- [ ] `git diff Frontend/memes-frontend/src/types/generated/` — empty (generated types committed and match schema).
- [ ] Confirm `backend_api.md` documents all 4 new/changed endpoints (`PUT`/`DELETE .../description-note`, extended `.../similar` `source` values, and the `Meme.descriptionNote` field).
- [ ] Confirm `docs/security/admin-permissions-todo.md` lists both new write endpoints.
- [ ] Confirm `CLAUDE.md`'s batch pipeline list documents both new batch jobs.
- [ ] Update the spec's status line in `docs/superpowers/specs/2026-08-20-description-notes-design.md` from `approved` to `done` once merged.
