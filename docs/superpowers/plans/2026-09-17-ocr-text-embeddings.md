# OCR Text Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 4 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** Compute and durably store a sentence embedding (per image) of the OCR text of every
image classified `text_heavy`, so a follow-up spec can later use it as a duplicate-matching
signal. Compute+store only — no matching, clustering, or UI behavior changes in this plan.

**Architecture:** A new `ocr_text_embeddings` table (one row per text-heavy image, 384-dim
vector, HNSW cosine index) populated by a new batch script that reuses the existing OCR-text
filtering/concatenation logic — relocated into the shared `repository/` layer first so the new
script and the already-shipped review UI stay behaviorally identical, not two independently
maintained copies.

**Tech Stack:** SQLAlchemy + Alembic (Storage/), `sentence-transformers` via the existing
`ai/sbert.py` wrapper, pgvector (`Vector`/HNSW, already used by `Embedding` and
`DescriptionNoteEmbedding`), pytest (`Backend/tests/`, `tests/integration/`, `batch/tests/` —
never combined in one invocation).

**Spec:** docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md

## Global Constraints

- Embedding dimension: `OCR_TEXT_EMBEDDING_DIM = 384` (verified empirically against the real
  model — see the spec's Problem section — not the existing `TEXT_EMBEDDING_DIM = 1024` bge-large
  constant, which is a different model).
- Embedding model: `"paraphrase-multilingual-MiniLM-L12-v2"` (`ai/sbert.py`'s own default; name
  it explicitly as a constant anyway, don't rely on the default silently).
- Classifier name/result strings to match against: `classifier == "text_heavy_v1"`,
  `result == "text_heavy"` (from `batch/utils/text_heavy_classifier.py`'s `CLASSIFIER_NAME`/
  `TEXT_HEAVY` constants — import them, don't re-type the literal strings, per the codebase's
  existing convention in `Backend/app/repositories/ingestion_repository.py`).
- OCR filtering thresholds: `settings.OCR.CONFIDENCE_MIN`, `settings.OCR.LANG_SCORE_MIN` (already
  used identically by `get_ocr_texts()` — same values, same meaning, no new settings keys).
- Batch commit cadence: `settings.GENERAL.BATCH_SIZE` (matches
  `build_description_note_embeddings.py`'s existing periodic-commit interval exactly — do not
  invent a new constant for this).
- Progress reporting: `settings.GENERAL.PROGRESS_EVERY` (matches
  `build_description_note_embeddings.py`'s `ProgressTracker` call exactly).
- Current alembic head at plan-authoring time: `6f770efe4fb4` (the `image_classifications`
  migration). This plan's migration's `down_revision` must be this value — confirm with
  `alembic heads` before generating, in case another branch has since moved the head.
- No task in this plan touches `tmp_duplicates`, `ingest_find_duplicates.py`,
  `rebuild_duplicates.py`, `clusterize.py`, `Backend/app/services/ingestion_service.py`, any
  frontend file, or `environments/batch_registry.yaml`'s existing entries — only appends one new
  entry. Any task whose diff touches those files beyond that is out of scope; flag it, don't
  implement it.
- `Backend/app/repositories/ingestion_repository.py`'s `get_ocr_texts()` behavior must not change
  in any observable way — Task 2's own refactor is verified by its *existing* test suite passing
  unchanged, not new tests.

---

### Task 1: Schema + migration

**Files:**
- Modify: `Storage/models.py:65` (insert new relationship line right after `classifications`),
  and append the new model class after `ImageClassification` (currently ends at line 437,
  directly before `class DescriptionNote(Base):`).
- Create: `Storage/alembic/versions/<generated>_add_ocr_text_embeddings_table.py`
- Test: `tests/integration/` (schema build only — no dedicated new test file; Task 3's
  integration tests are the first real exercise of this table).

**Interfaces:**
- Produces: `OCRTextEmbedding` model (`Storage.models.OCRTextEmbedding`), columns `image_id`
  (PK/FK to `images.id`, CASCADE), `embedding` (`Vector(384)`), `computed_at`. Constant
  `Storage.models.OCR_TEXT_EMBEDDING_DIM = 384`. `Image.ocr_text_embedding` relationship
  (`uselist=False`, one row or none per image). Task 3's repository imports
  `Storage.models.OCRTextEmbedding` and `Image`.

- [ ] **Step 1: Add the constant and model to Storage/models.py**

Insert this line immediately after line 65 (`classifications = relationship(...)`), still inside
the `Image` class body:

```python
    ocr_text_embedding = relationship(
        "OCRTextEmbedding", uselist=False, back_populates="image", cascade="all, delete-orphan"
    )
```

Then, immediately after the `ImageClassification` class (which currently ends right before
`class DescriptionNote(Base):` — find that exact boundary and insert before it, not at the very
end of the file), add:

```python
OCR_TEXT_EMBEDDING_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 -- verified empirically
                               # (SbertModel().embed_text(...).shape == (384,)), not
                               # bge-large-en-v1.5's 1024 (TEXT_EMBEDDING_DIM).


class OCRTextEmbedding(Base):
    __tablename__ = "ocr_text_embeddings"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(Vector(OCR_TEXT_EMBEDDING_DIM))
    computed_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="ocr_text_embedding")

    __table_args__ = (
        Index(
            "ix_ocr_text_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

Place the `OCR_TEXT_EMBEDDING_DIM` constant directly above the class, not up near
`EMBEDDING_DIM`/`TEXT_EMBEDDING_DIM` at the top of the file — this matches how
`ImageClassification`'s own constants (none needed one, but its class itself) were added in
place rather than reorganizing the file's top-of-file constant block.

- [ ] **Step 2: Generate the migration**

From `Storage/`, with `DATABASE_URL` pointed at a real reachable Postgres (the local `ocrdb_test`
database is fine for generation — see the repo's documented test-DB gotcha about
`create_all()`/`drop_all()` being independent of Alembic, which means you may need
`alembic stamp base && alembic upgrade head` first to get `ocrdb_test` correctly stamped before
autogenerate can diff against it, exactly as Task 1 of the text-heavy-classifier plan's
implementer had to do):

```powershell
cd Storage
alembic revision --autogenerate -m "add ocr_text_embeddings table"
```

Confirm the generated file's `down_revision` reads `'6f770efe4fb4'` (re-check
`alembic heads` first if this plan is executed after other work has landed — if the head has
moved, the new migration must chain off the *actual* current head, not this plan's stale value).

**Inspect the autogenerated diff before keeping it.** Confirm it contains *only*
`ocr_text_embeddings` DDL (the table, its PK/FK, the HNSW index) — no unrelated drift from
other in-flight schema changes. If autogenerate picks up unrelated pre-existing drift (as
happened in the classifier's own Task 1, where it found and correctly trimmed a duplicate
`index=True` on unrelated columns), trim it the same way: out of scope for this migration, leave
the underlying drift untouched, don't silently fix it here.

- [ ] **Step 3: Verify the migration cleanly upgrades and downgrades**

From `Storage/`, against `ocrdb_test`:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic downgrade -1
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" alembic upgrade head
```

All three must succeed with no errors. Confirm `alembic current` (same `DATABASE_URL`) reports
the new revision as head afterward.

- [ ] **Step 4: Run the full test suites**

```bash
cd Backend && pytest -q
cd .. && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
pytest batch/tests/ -q
```

Expected: all three pass, same counts as before this task (this task adds no new tests of its
own — it's pure schema, exercised for real starting in Task 3). If any count is *lower* than
before, something broke; investigate before proceeding.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/<generated_file>.py
git commit -m "feat: add ocr_text_embeddings table (test-db verified)"
```

---

### Task 2: Shared OCR-text-concatenation refactor

**Files:**
- Modify: `repository/ocr_text.py` (append the new function)
- Modify: `Backend/app/repositories/ingestion_repository.py:86-129` (the `get_ocr_texts` method
  body only — its signature and the surrounding class are untouched)
- Test: `Backend/tests/test_ingestion_repository.py` (existing suite — no new tests added; it
  must continue to pass unchanged, proving this refactor changed no observable behavior)

**Interfaces:**
- Produces: `repository.ocr_text.concatenate_ocr_rows(rows, confidence_min: float,
  lang_score_min: float) -> dict[UUID, str]` — a pure function, no DB access, no `self`.
- Consumes (Task 3): Task 3's `OCRTextEmbeddingsRepository.get_text_heavy_images_needing_embedding`
  calls this same function with rows shaped `(image_id, text, confidence, lang_score)`, identical
  to what `get_ocr_texts()` already selects.

- [ ] **Step 1: Add `concatenate_ocr_rows` to repository/ocr_text.py**

Append to the end of `repository/ocr_text.py` (after `update_lang_scores`, module-level function,
not a method on `OCRTextRepository` — it takes no `self`/session, it's pure):

```python
def concatenate_ocr_rows(rows, confidence_min: float, lang_score_min: float) -> dict:
    """rows: iterable of (image_id, text, confidence, lang_score) tuples -- e.g. straight from
    a SELECT OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score. Drops
    blocks below either threshold, orders survivors most-language-plausible first (Russian
    leads instead of the EN/ES EasyOCR readers' Latin transliteration noise), dedupes identical
    block text per image, and concatenates into one string per image_id. Images with no
    surviving block are simply absent from the result."""
    def _order_key(row):
        _, _, confidence, lang_score = row
        return (
            lang_score is None,
            -(lang_score if lang_score is not None else 0.0),
            confidence is None,
            -(confidence if confidence is not None else 0.0),
        )

    rows = sorted(rows, key=_order_key)
    by_image: dict = {}
    seen: dict = {}
    for image_id, text, confidence, lang_score in rows:
        if confidence is not None and confidence < confidence_min:
            continue
        if lang_score is not None and lang_score < lang_score_min:
            continue
        block = (text or "").strip()
        if not block:
            continue
        seen_for_image = seen.setdefault(image_id, set())
        if block in seen_for_image:
            continue
        seen_for_image.add(block)
        by_image.setdefault(image_id, []).append(block)
    return {image_id: " ".join(parts) for image_id, parts in by_image.items()}
```

No new imports needed in `repository/ocr_text.py` for this function (plain builtins only).

- [ ] **Step 2: Run this file's own module in isolation to sanity-check the move**

```bash
python -c "from repository.ocr_text import concatenate_ocr_rows; print('import OK')"
```

Expected: `import OK`, no syntax/import errors.

- [ ] **Step 3: Replace `get_ocr_texts()`'s body in Backend/app/repositories/ingestion_repository.py**

Add the import (alongside the existing `from batch.utils.text_heavy_classifier import
CLASSIFIER_NAME, TEXT_HEAVY` line, both are cross-package imports into this file):

```python
from repository.ocr_text import concatenate_ocr_rows
```

Replace the entire current `get_ocr_texts` method (currently lines 86-129: the docstring, the
`if not image_ids: return {}` guard, the `select(...)`/`execute`, the local `_order_key`
closure, and the `sorted`/`by_image`/`seen` loop) with:

```python
    async def get_ocr_texts(
        self, image_ids, confidence_min: float, lang_score_min: float
    ) -> dict:
        """Concatenated OCR text per image id for the review UI. See
        repository/ocr_text.py's concatenate_ocr_rows for the filtering/ordering/dedup
        rules this delegates to."""
        if not image_ids:
            return {}
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(image_ids))
        )
        return concatenate_ocr_rows(result.all(), confidence_min, lang_score_min)
```

The `_order_key` closure and the `sorted`/`by_image`/`seen` block are deleted from this file
entirely — they now live only in `concatenate_ocr_rows`. `select` and `OCRText` are already
imported in this file (used elsewhere); no other import changes needed here.

- [ ] **Step 4: Run the existing test suite — must pass unchanged**

```bash
cd Backend && pytest tests/test_ingestion_repository.py -v
```

Expected: the same test count and names as before this task (`TestGetOcrTexts`'s 5 cases:
`test_empty_ids_returns_empty_without_query`,
`test_drops_low_confidence_and_low_lang_score_blocks`,
`test_orders_by_lang_score_desc_then_confidence_desc`, `test_dedupes_identical_block_text`,
`test_none_lang_score_is_kept`), all still passing, zero changes to this test file itself. If any
of these fail, the refactor introduced a behavior change — stop and fix before proceeding; do not
edit the tests to match new behavior, since none was supposed to happen.

- [ ] **Step 5: Run the full Backend suite**

```bash
pytest -q
```

Expected: same total count as before this task (this task adds no new tests).

- [ ] **Step 6: Commit**

```bash
git add repository/ocr_text.py Backend/app/repositories/ingestion_repository.py
git commit -m "refactor: extract OCR-text concatenation into repository/ocr_text.py"
```

---

### Task 3: Repository + batch script + registry + CLAUDE.md entries + tests

**Files:**
- Create: `repository/ocr_text_embeddings.py`
- Create: `batch/build_ocr_text_embeddings.py`
- Create: `tests/integration/test_ocr_text_embeddings_repository.py`
- Create: `tests/integration/test_build_ocr_text_embeddings.py`
- Modify: `environments/batch_registry.yaml` (append one entry)
- Modify: `CLAUDE.md` (append one Maintenance entry, extend the one-liner list)

**Interfaces:**
- Consumes: Task 1's `Storage.models.OCRTextEmbedding`/`Image`. Task 2's
  `repository.ocr_text.concatenate_ocr_rows`. `batch.utils.text_heavy_classifier.CLASSIFIER_NAME`
  (`"text_heavy_v1"`) and `TEXT_HEAVY` (`"text_heavy"`) — import these, don't re-type the
  literals.
- Produces: `OCRTextEmbeddingsRepository(session).get_text_heavy_images_needing_embedding(
  classifier: str, confidence_min: float, lang_score_min: float, status: str = "active") ->
  dict[UUID, str]` and `.save(image_id, embedding: list[float]) -> None`. Batch script
  `run(session, confidence_min: float, lang_score_min: float, status: str) ->
  SimpleMetricsListener` / `main(status: str = "active", trigger: str = "manual", run_id:
  uuid.UUID | None = None) -> None`, same split convention as every other batch script this
  session (`fix_image_formats.py`, `classify_text_heavy.py`, `build_description_note_embeddings.py`).

- [ ] **Step 1: Write the repository**

Create `repository/ocr_text_embeddings.py`. Note this differs from the spec's own §3 code in one
small way, caught during this plan's self-review: the `classifier` parameter is the *classifier
name* (`"text_heavy_v1"`, `CLASSIFIER_NAME`) the caller passes in, but the *result* string is not
caller-varying — this method only ever means "the positive result" — so it's imported as
`TEXT_HEAVY` from `batch.utils.text_heavy_classifier` and compared directly, matching
`Backend/app/repositories/ingestion_repository.py`'s own established precedent for the identical
comparison in `get_text_heavy_ids` (grep that file for `TEXT_HEAVY` to see the style this
matches), rather than the spec's bare `"text_heavy"` string literal:

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from batch.utils.text_heavy_classifier import TEXT_HEAVY
from repository.ocr_text import concatenate_ocr_rows
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding


class OCRTextEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_text_heavy_images_needing_embedding(
        self, classifier: str, confidence_min: float, lang_score_min: float, status: str = "active",
    ) -> dict:
        """Concatenated OCR text (see concatenate_ocr_rows) per text_heavy image not yet in
        ocr_text_embeddings. Images whose filtered text comes out empty (possible: the
        classifier's own bbox-coverage filter only checks confidence, not lang_score, so a
        text_heavy image's OCR could in principle be entirely low-lang-score noise) are simply
        absent from the result -- unlike classify_text_heavy.py's "unreadable" outcome, this
        has no separate counter; the caller's to-embed count is just len(this dict)."""
        already_embedded = select(OCRTextEmbedding.image_id).scalar_subquery()
        text_heavy_ids = (
            select(ImageClassification.image_id)
            .where(ImageClassification.classifier == classifier, ImageClassification.result == TEXT_HEAVY)
            .scalar_subquery()
        )
        candidate_ids = (
            select(Image.id)
            .where(
                Image.status == status,
                Image.id.in_(text_heavy_ids),
                Image.id.not_in(already_embedded),
            )
        )
        candidates = (await self.session.execute(candidate_ids)).scalars().all()
        if not candidates:
            return {}

        rows = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(candidates))
        )
        texts = concatenate_ocr_rows(rows.all(), confidence_min, lang_score_min)
        return {image_id: texts[image_id] for image_id in candidates if image_id in texts}

    async def save(self, image_id, embedding: list[float]) -> None:
        stmt = (
            insert(OCRTextEmbedding)
            .values(image_id=image_id, embedding=embedding)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"embedding": embedding},
            )
        )
        await self.session.execute(stmt)
```

- [ ] **Step 2: Write the batch script**

Create `batch/build_ocr_text_embeddings.py`:

```python
"""Computes sentence embeddings for the OCR text of every image classified text_heavy (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md), storing them in
ocr_text_embeddings for a future duplicate-matching signal (see
docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md). Safe to re-run: only images not yet
in ocr_text_embeddings are processed."""
import argparse
import asyncio
import uuid

from ai.sbert import SbertModel
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.ocr_text_embeddings import OCRTextEmbeddingsRepository
from Storage.db import AsyncSessionLocal

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # ai/sbert.py's own default -- named
                                                             # explicitly here so the model in
                                                             # use is visible at a glance,
                                                             # defensive against that default
                                                             # ever changing later.


async def run(session, confidence_min: float, lang_score_min: float, status: str) -> SimpleMetricsListener:
    repo = OCRTextEmbeddingsRepository(session)
    texts = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, confidence_min, lang_score_min, status=status)
    print(f"Images to embed: {len(texts)}")

    embedder = SbertModel(model_name=EMBEDDING_MODEL)
    metrics = SimpleMetricsListener()
    tracker = ProgressTracker(total=len(texts), report_every=settings.GENERAL.PROGRESS_EVERY)

    for i, (image_id, text) in enumerate(texts.items()):
        vector = embedder.embed_text(text)
        await repo.save(image_id, vector.tolist())
        metrics.increment("embedded")
        tracker.mark_done()
        if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
            await session.commit()

    await session.commit()
    tracker.summary()
    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    confidence_min = settings.OCR.CONFIDENCE_MIN
    lang_score_min = settings.OCR.LANG_SCORE_MIN

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)
    else:
        async with tracked_run(kind="build_ocr_text_embeddings", trigger=trigger):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)

    print("Embedding results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
```

- [ ] **Step 3: Register in environments/batch_registry.yaml**

Append (this file currently ends at line 54 with the `classify_text_heavy` entry added earlier
this session — append directly after it):

```yaml
build_ocr_text_embeddings:
  module: batch.build_ocr_text_embeddings
  kind: build_ocr_text_embeddings
```

Confirm the `kind:` value here (`build_ocr_text_embeddings`) matches the string passed to
`tracked_run(kind=...)` in Step 2's `main()` exactly — this exact mismatch class was the Important
finding the text-heavy-classifier's own final review caught (registry entry present but
`kind` not matching would silently break the admin-trigger wiring the same way a missing entry
does).

- [ ] **Step 4: Add the CLAUDE.md entry**

In the `# Maintenance (run as needed)` block, extend the one-liner list at (currently) line 226
from:

```
fix_image_formats          classify_text_heavy
```

to:

```
fix_image_formats          classify_text_heavy       build_ocr_text_embeddings
```

Then add the full entry directly after `classify_text_heavy`'s own entry (currently ending at
line 251, right before the blank line before `# Concept discovery...`):

```
build_ocr_text_embeddings   → computes sentence embeddings (paraphrase-multilingual-MiniLM-
                               L12-v2, 384-dim) for the OCR text of every text_heavy-classified
                               image, storing them in ocr_text_embeddings. Compute+store only --
                               no matching/routing logic reads this table yet. Admin-triggerable
                               from /admin/batches, manual-trigger only, not scheduled. --status
                               defaults to active; --status pending covers an in-flight
                               ingestion batch. See
                               docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md.
```

- [ ] **Step 5: Write the repository integration tests**

Create `tests/integration/test_ocr_text_embeddings_repository.py`:

```python
"""Integration tests for repository/ocr_text_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from repository.ocr_text_embeddings import OCRTextEmbeddingsRepository
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding

CONFIDENCE_MIN = 0.4
LANG_SCORE_MIN = 0.3


async def _text_heavy_image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}))
    await db_session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_returns_concatenated_text_for_a_text_heavy_image(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {image.id: "hello world"}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_already_embedded_images(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()
    db_session.add(OCRTextEmbedding(image_id=image.id, embedding=[0.0] * 384))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_non_text_heavy_image_even_with_ocr_text(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result="not_text_heavy", details={}))
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_image_whose_ocr_text_is_entirely_filtered_out(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="noise", confidence=0.1, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_upserts_on_second_call(db_session):
    image = await _text_heavy_image(db_session)
    repo = OCRTextEmbeddingsRepository(db_session)

    await repo.save(image.id, [0.1] * 384)
    await db_session.commit()
    await repo.save(image.id, [0.2] * 384)
    await db_session.commit()

    rows = (await db_session.execute(
        select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].embedding[0] == pytest.approx(0.2)
```

- [ ] **Step 6: Run the repository tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_text_embeddings_repository.py -v
```

Expected: 5/5 pass.

- [ ] **Step 7: Write the batch script integration tests**

Create `tests/integration/test_build_ocr_text_embeddings.py`:

```python
"""Integration tests for batch/build_ocr_text_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_ocr_text_embeddings import run
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding

CONFIDENCE_MIN = 0.4
LANG_SCORE_MIN = 0.3


async def _text_heavy_image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}))
    await db_session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_embeds_and_persists_a_text_heavy_image(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()

    assert metrics.counters_dict() == {"embedded": 1}
    row = (await db_session.execute(
        select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id)
    )).scalar_one()
    assert len(row.embedding) == 384


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_finds_no_candidates_for_already_embedded_images(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    first = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()
    assert first.counters_dict() == {"embedded": 1}

    second = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()
    assert second.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_non_text_heavy_image_is_excluded(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result="not_text_heavy", details={}))
    db_session.add(OCRText(image_id=image.id, text="a real sentence", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_entirely_filtered_text_is_excluded(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="noise", confidence=0.1, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")

    assert metrics.counters_dict() == {}
```

- [ ] **Step 8: Run the batch script tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_ocr_text_embeddings.py -v
```

Expected: 4/4 pass. Note this test suite genuinely loads the real SBERT model (via
`SbertModel()` inside `run()`) — expect it to be noticeably slower than a typical mocked test
(first-run model download/load can take real wall-clock time); this is expected, not a hang.

- [ ] **Step 9: Run the full DATABASE_URL integration root**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: all pass, previous count + 9 (5 repository + 4 batch-script tests added this task).

- [ ] **Step 10: Run the other two test roots**

```bash
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

Expected: both pass, unchanged counts (this task adds no tests to either root).

- [ ] **Step 11: Commit**

```bash
git add repository/ocr_text_embeddings.py batch/build_ocr_text_embeddings.py \
  environments/batch_registry.yaml CLAUDE.md \
  tests/integration/test_ocr_text_embeddings_repository.py \
  tests/integration/test_build_ocr_text_embeddings.py
git commit -m "feat: compute and store OCR text embeddings for text-heavy images"
```

---

### Task 4: Live rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** Its migration step and its embedding-computation step
both write to live `metal`/`general`/`it` databases the developer's own running backends depend
on continuously — exactly the case `CLAUDE.md`'s "Live database access for agents" section and
this session's own established pattern (the text-heavy classifier's own Task 4, the Tier B
partial-index work, the dimension-capture work) exist for. The controller runs every step below
itself, never a subagent.

**Before Step 2 (the first live-environment write), stop and get the user's explicit go-ahead.**
Name the three environments this will touch and what it does (a brand-new, empty table via a
plain migration, then running the new `build_ocr_text_embeddings.py --status active` batch job
against the existing text_heavy corpus) before proceeding.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md` (status + a "Rollout
  outcome" section with the real counts and spot-check findings).

- [ ] **Step 1: Confirm the plan and get the go-ahead**

Summarize for the user: Tasks 1-3's code is merged; Step 2 below applies the new migration (a
brand-new, empty table) to `metal`, `general`, and `it` in turn, then Step 3 runs
`build_ocr_text_embeddings.py --status active` against each to embed the existing text_heavy
corpus (406 / 4,084 / 178 images respectively, per the classifier's own most recent rollout
counts — re-verify these via a fresh read-only count before running, since more images may have
been classified since). Wait for explicit confirmation before continuing.

- [ ] **Step 2: Apply the migration to all three environments**

Per this repo's documented migration workflow, for each of `metal`, `general`, `it` in turn (use
absolute paths for `Get-Content` — the relative `..\environments\.env.<env>` form has
intermittently resolved against the wrong cwd in this exact session; verify with
`Write-Host "DATABASE_URL set: $($env:DATABASE_URL -ne $null)"` after loading if in doubt):

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes\Storage"
alembic upgrade head
```

Confirm each environment's backend (`/api/diagnostics/health`) still responds normally after its
migration, before moving to the next environment.

- [ ] **Step 3: Run the embedding script against all three environments**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.build_ocr_text_embeddings --env <environment> --status active
```

(`PYTHONIOENCODING=utf-8` per the already-documented `ProgressTracker` Windows gotcha —
`build_ocr_text_embeddings.py` pulls in the same `ProgressTracker` the classifier's own rollout
needed this for. Run each environment as a background task if it looks likely to exceed a few
minutes — general's ~4,084-image corpus embedded at whatever the real per-image SBERT cost turns
out to be; there is no periodic-commit risk here the way there briefly was for
`classify_text_heavy.py`, since this script already commits every `settings.GENERAL.BATCH_SIZE`
images from Task 2's design, not just once at the end.)

Optionally also `--status pending` for any environment with an in-flight ingestion batch
(check via the ingestion review queue, same as the classifier's own rollout did). Confirm each
environment's backend health after each run.

- [ ] **Step 4: Verify the results (read-only)**

For each environment, via `DATABASE_URL_READONLY` (never the main `DATABASE_URL`):

```sql
SELECT count(*) FROM ocr_text_embeddings;
```

Expected: roughly matches that environment's `text_heavy` count from
`SELECT count(*) FROM image_classifications WHERE classifier = 'text_heavy_v1' AND result =
'text_heavy'` (allowing for the empty-after-filtering exclusion — expect it to be close, not
exact).

Then, still read-only, pick a small number of real stored embeddings per environment and inspect
distances directly:

```sql
SELECT a.image_id, b.image_id, a.embedding <=> b.embedding AS distance
FROM ocr_text_embeddings a, ocr_text_embeddings b
WHERE a.image_id < b.image_id
ORDER BY distance
LIMIT 10;
```

Pull the filenames for the tightest few pairs (`JOIN images`) and open the actual files (same
manual-verification approach used throughout the classifier spec's own validation) to confirm the
tightest real pairs found this way look like genuine template/text reposts by eye — this is a
sanity check on real corpus-scale data, not a threshold decision (that's the follow-up spec's
job, with a larger, more systematic sample).

- [ ] **Step 5: Mark the spec done**

`docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`: `status: approved` → `status: done`;
add a "## Rollout outcome" section with each environment's embedded-image count and the
tightest-pair spot-check findings from Step 4.

```bash
git add docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md
git commit -m "docs: mark OCR text embeddings spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- §1 (schema) → Task 1.
- §2 (shared refactor) → Task 2, transcribed verbatim from the spec, with the exact deletion
  scope (the `_order_key` closure and `sorted`/`by_image`/`seen` block) spelled out so the
  implementer isn't left to infer what "replace the body" means.
- §3 (repository) → Task 3 Step 1, transcribed verbatim from the spec, with one correction over
  the spec's own literal code: the spec's example used a bare `"text_heavy"` string in the
  `ImageClassification.result ==` comparison; this plan corrects that to the imported `TEXT_HEAVY`
  constant, matching `Backend/app/repositories/ingestion_repository.py`'s own established
  precedent for the identical comparison (caught during this plan's own self-review, not left for
  the implementer to notice or silently deviate on).
- §4 (batch script) → Task 3 Step 2, transcribed verbatim.
- §5 (registry + CLAUDE.md) → Task 3 Steps 3-4, transcribed verbatim, with the exact current
  line content quoted so the diff is unambiguous.
- Testing section → Task 1 Step 4 (no new tests, full-suite regression check), Task 2 Steps 4-5
  (existing suite as the safety net), Task 3 Steps 5-10 (all four new-behavior test cases per the
  spec's Testing section, plus a `save()` upsert test consistent with every prior
  embeddings/classifications repository this session).
- Rollout section → Task 4 in full, with the classifier's own most-recent rollout counts carried
  forward as the expected starting point (flagged to re-verify, since more images may have been
  classified since this plan was written).
- Non-goals respected: no task touches `tmp_duplicates`, `ingest_find_duplicates.py`,
  `rebuild_duplicates.py`, `clusterize.py`, `ingestion_service.py`, any frontend file, or any
  existing `batch_registry.yaml` entry.

**Placeholder scan:** none found. Every code step has complete, literal content; the one place
the spec's own code needed a correction (the `TEXT_HEAVY` constant vs. bare string) is spelled
out explicitly as a plan-level refinement, not left as an unresolved question.

**Type consistency:** `get_text_heavy_images_needing_embedding(classifier: str, confidence_min:
float, lang_score_min: float, status: str = "active") -> dict` is identical between Task 3 Step 1
(definition) and Step 2 (batch script's call site) and Step 5 (test call sites). `run(session,
confidence_min, lang_score_min, status) -> SimpleMetricsListener`'s signature matches between
Task 3 Step 2's definition and Step 7's every test call site (all four tests call it positionally
in the same order). `save(image_id, embedding: list[float])` matches between Step 1's definition
and Step 5's test usage.
