# Smart Search Phase 1 Hardening Round 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upsert `mark_done_by_id` to cut its per-image round-trips in half; fix the recurring `db_session`/commit fixture friction at its root via SQLAlchemy's SAVEPOINT pattern; add a real end-to-end test for `batch/build_ocr_lemmas.py`'s actual code path.

**Architecture:** Three tasks in a strict dependency order. Task 1 (upsert) is independent. Task 2 (fixture fix) must land and be fully validated across the entire `tests/integration/` suite before Task 3 begins, since Task 3's new test depends on the fixture behaving correctly. Task 3 extracts `build_ocr_lemmas.py`'s body into a testable `run(session, ...)` function, matching this codebase's existing pattern for session-accepting batch-job bodies (`batch/rebuild_duplicates.py`'s `create_tmp_duplicates(session)`).

**Tech Stack:** Python 3.11, SQLAlchemy 2.0.45 async ORM (Postgres dialect), PostgreSQL, pytest/pytest-asyncio.

## Global Constraints

- Match the existing upsert convention exactly: `sqlalchemy.dialects.postgresql.insert`, `.values(...)`, `.on_conflict_do_update(index_elements=[...], set_={...})` (see `repository/image_extras.py`'s `ImageExtrasRepository.set_flagged`, and this round's own sibling fix pattern from `OCRLemmasSaver.add_lemmas`).
- `Backend/tests/`, `tests/integration/`, `batch/tests/`, `tests/rules/` are separate `pytest.ini` roots — never combine them in one `pytest` invocation.
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line, e.g.:
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
- Task 2's fixture change affects every file under `tests/integration/` (~30 files) — the full suite must be run and confirmed green as its own checkpoint before any other task in this plan proceeds, not just the files this round directly touches.
- Spec: `docs/superpowers/specs/2026-07-23-smart-search-phase1-hardening-round3-design.md` — read it before starting if a task here is unclear on rationale.

---

### Task 1: Upsert `mark_done_by_id`

**Files:**
- Modify: `repository/image_procesing_status.py`
- Modify: `tests/integration/test_image_processing_status_repository.py`

**Interfaces:**
- Changes: `ImageProcessingStatusRepository.mark_done_by_id(image_id) -> None`'s internals only — signature and "no commit" contract unchanged, still callable exactly as before. Internally now does one `INSERT ... ON CONFLICT (image_id, pipeline) DO UPDATE` instead of `session.get()` + ORM add/update.

- [ ] **Step 1: Write the new failing test**

Add to `tests/integration/test_image_processing_status_repository.py` (directly after `test_mark_done_by_id_is_idempotent`):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_transitions_existing_row_to_done(db_session):
    """Proves the upsert is a real UPDATE on conflict, not a no-op --
    a row that already exists in some other status must transition to
    done, not be silently skipped."""
    image = await _insert_image(db_session)
    db_session.add(ImageProcessingStatus(image_id=image.id, pipeline=OCR_LEMMAS_PIPELINE, status="processing"))
    await db_session.flush()

    repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)
    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == OCR_LEMMAS_PIPELINE,
        )
    )
    row = result.scalar_one()
    assert row.status == "done"
    assert row.finished_at is not None
```

This test passes even against the *current* (pre-fix) implementation — that's fine, it's here to guard the upsert's correctness going forward, not to drive the change via a RED step. The actual behavior change (fewer round-trips) isn't independently observable through pytest assertions; Steps 2-3 verify it by reading the diff, not by a new failing test.

- [ ] **Step 2: Implement the upsert**

In `repository/image_procesing_status.py`, add the import (alongside the existing `sqlalchemy` import):

```python
from sqlalchemy.dialects.postgresql import insert
```

Replace `mark_done_by_id`:

```python
    async def mark_done_by_id(self, image_id) -> None:
        """No commit — caller controls commit timing via its own batch committer."""
        now = datetime.utcnow()
        stmt = (
            insert(ImageProcessingStatus)
            .values(image_id=image_id, pipeline=self.pipeline, status="done", finished_at=now)
            .on_conflict_do_update(
                index_elements=["image_id", "pipeline"],
                set_={"status": "done", "finished_at": now},
            )
        )
        await self.session.execute(stmt)
```

- [ ] **Step 3: Run the full file to confirm everything passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_processing_status_repository.py -v`
Expected: PASS, all tests in the file (the three pre-existing `mark_done_by_id` tests plus the new transition test, plus every unrelated `record_failure`/`get_image_ids_with_status`/`delete_all` test — confirming zero regression on methods this task doesn't touch).

- [ ] **Step 4: Commit**

```bash
git add repository/image_procesing_status.py tests/integration/test_image_processing_status_repository.py
git commit -m "perf: upsert mark_done_by_id instead of session.get()+add"
```

---

### Task 2: Fix the shared `db_session` fixture (SAVEPOINT pattern)

**Files:**
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Changes: the `db_session` fixture's *implementation* — its usage contract for every other test file is unchanged (still an `AsyncSession` yielded per test, still fully isolated/rolled back at teardown). No other file needs to change because of this fixture change alone — only the three tests in `test_ocr_lemmas_repository.py` that exist *specifically because* of the old limitation get simplified.

This task carries the highest blast radius in this plan (touches the fixture every integration test file depends on) — follow the steps in order and do not skip the full-suite checkpoint in Step 3.

- [ ] **Step 1: Replace the fixture**

Replace `tests/integration/conftest.py` in full:

```python
"""
Integration test fixtures — require a real PostgreSQL instance with pgvector.

Set DATABASE_URL to a test database before running:
    pytest tests/integration/ --co  # confirm collection
    pytest tests/integration/       # run (needs live DB)

The CI workflow (.github/workflows/integration-tests.yml) spins up
pgvector/pgvector:pg16 as a service and sets DATABASE_URL automatically.
"""
import os
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Ensure repo root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

os.environ.setdefault("BASE_PATH", "/tmp/test_images")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test",
)

from Storage.db import AsyncSessionLocal  # noqa: E402 — env must be set first
from Storage.models import Base  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine():
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine):
    """Yields a session wrapped in an outer transaction that is always rolled
    back after each test, isolating tests from each other -- same guarantee
    as before. Bound via join_transaction_mode="create_savepoint" (SQLAlchemy
    2.0+) so that code under test which calls session.commit() (e.g.
    repository classes that manage their own commit timing, like
    OCRLemmasSaver/OCRLemmasRepository.delete_all()) only commits an inner
    SAVEPOINT -- invisible to the test, which keeps using this same session
    normally before and after -- rather than ending the outer transaction the
    way the previous plain session.begin() wrapping did."""
    async with db_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()
```

Note: `sqlalchemy.orm.sessionmaker` is no longer imported — it was only used to build the old `db_session`'s session factory, which this replaces entirely.

If `join_transaction_mode` behaves differently than described here against the actually-installed SQLAlchemy (2.0.45, already confirmed installed) — e.g. a different required argument shape, or the "restart savepoint on commit" behavior needing an explicit event listener rather than being automatic — investigate directly against the installed library (its docstrings/source, or a small standalone script) rather than guessing further; this exact pattern is SQLAlchemy's own documented recommendation for "joining a Session into an external transaction for test suites," so if something doesn't match, the fix is almost certainly a small adjustment to this fixture's construction, not a sign the overall approach is wrong.

- [ ] **Step 2: Smoke-test the mechanism in isolation before touching anything else**

Before retrofitting any existing test, prove the core mechanism works with a throwaway check. Run just one of the three tests that previously needed the workaround, edited temporarily to skip its own `_fresh_session` workaround and query `db_session` directly instead — e.g., temporarily change `test_delete_all_clears_table` to query `db_session` after `delete_all()` instead of `_fresh_session(db_engine)`, run it in isolation:

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v -k test_delete_all_clears_table`

Expected: PASS, with no `InvalidRequestError: Can't operate on closed transaction` — this confirms the SAVEPOINT mechanism actually works before you commit to retrofitting all three tests and every other file in the suite. If this fails, stop and report BLOCKED with the exact error rather than proceeding to Step 3.

- [ ] **Step 3: Retrofit all three workaround tests, drop the workaround helper**

Replace `tests/integration/test_ocr_lemmas_repository.py` in full:

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
        await saver.add_lemmas(image.id, {"кот", "собака"})

    rows = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert set(rows) == {"кот", "собака"}


@pytest.mark.asyncio(loop_scope="session")
async def test_add_lemmas_is_safe_to_call_twice_for_same_pair(db_session):
    """Regression test: a duplicate (image_id, lemma) write (e.g. from an
    overlapping/concurrent job invocation) must be a no-op, not a crash."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"кот"})
        await saver.add_lemmas(image.id, {"кот"})

    rows = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert rows == ["кот"]


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

This drops the module docstring's workaround explanation (no longer applicable), the `_fresh_session` helper, the `db_engine` fixture parameter on the three affected tests, and their manual cleanup blocks — every test in the file now follows the same shape.

- [ ] **Step 4: Run this file, then the full `tests/integration/` suite**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: PASS, all 8 tests, no `_fresh_session`/workaround needed.

Then run the entire suite as the required checkpoint before this task is considered done:

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: PASS, every test across all ~30 files (should be the same total count as before this task, since no tests were added or removed, only simplified). Pay particular attention to any test that seeds data across multiple `db_session.flush()` calls or checks for isolation between tests — if the fixture change subtly broke rollback isolation, it would most likely show up as unexpected leftover data in an unrelated test, not necessarily a clean failure in this file.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "test: fix db_session fixture to support commit-calling code via SAVEPOINT"
```

---

### Task 3: End-to-end test for `build_ocr_lemmas.py`

**Files:**
- Modify: `batch/build_ocr_lemmas.py`
- Create: `tests/integration/test_build_ocr_lemmas.py`

**Interfaces:**
- Produces: `async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics) -> None` in `batch/build_ocr_lemmas.py` — the full body previously inline in `main()`, now directly callable and testable. `main()` becomes a thin wrapper.

**Depends on:** Task 2 must be complete (fixture fixed and fully validated) before starting this task — this task's new test relies on `db_session` correctly handling the commit-calling code inside `run()`.

- [ ] **Step 1: Extract `run()` from `main()`**

Replace `batch/build_ocr_lemmas.py` in full:

```python
import argparse
import asyncio

from batch.utils.ocr_lemmas import group_lemmas_by_image
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.db import AsyncSessionLocal
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository, OCR_LEMMAS_PIPELINE
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver


async def run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics):
    lemmas_repo = OCRLemmasRepository(session)
    images_repo = ImagesRepository(session)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)

    if not incremental:
        await lemmas_repo.delete_all()
        await status_repo.delete_all()
        await session.commit()

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

    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(
        simplified_rows, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length
    )
    metrics.add("ocr_rows.total", stats["rows_total"])
    metrics.add("ocr_rows.skipped", stats["rows_skipped"])
    metrics.add("ocr_rows.processed", stats["rows_processed"])

    print(f"Total images: {len(all_image_ids)}")
    tracker = ProgressTracker(len(all_image_ids), report_every=100, report_interval_secs=10)

    async with OCRLemmasSaver(session) as saver:
        for image_id in all_image_ids:
            lemma_set = lemmas_by_image.get(image_id, set())
            await saver.add_lemmas(image_id, lemma_set)
            await status_repo.mark_done_by_id(image_id)
            metrics.add("lemmas.total", len(lemma_set))
            metrics.bucket("lemmas_per_image", len(lemma_set))
            tracker.mark_done()

    tracker.summary()


async def main(incremental: bool):
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        await run(session, incremental, ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics)

    print("Lemmas:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images not yet marked done for the ocr_lemmas "
                             "pipeline (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))
```

- [ ] **Step 2: Confirm the batch/tests root still passes unaffected**

Run: `pytest batch/tests/ -v`
Expected: PASS — this extraction doesn't change `group_lemmas_by_image` or anything `batch/tests/test_ocr_lemmas_grouping.py` covers, just how `build_ocr_lemmas.py` calls it.

- [ ] **Step 3: Write the new end-to-end test file**

Create `tests/integration/test_build_ocr_lemmas.py`:

```python
"""
Integration tests for batch/build_ocr_lemmas.py's run() -- the full
grouping -> saving -> status-marking pipeline exercised end-to-end against
a real database. Distinct from the unit-level group_lemmas_by_image tests
(batch/tests/test_ocr_lemmas_grouping.py) and the repository-level
OCRLemmasSaver/ImageProcessingStatusRepository tests
(tests/integration/test_ocr_lemmas_repository.py,
tests/integration/test_image_processing_status_repository.py) -- this file
proves those pieces are wired together correctly through the real code path.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_ocr_lemmas import run
from metrics.listener import SimpleMetricsListener
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import OCR_LEMMAS_PIPELINE
from repository.ocr_text import OCRTextRepository
from rules.normalize import make_morph
from Storage.models import Image, OCRLemma

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]
_MORPH = make_morph()


async def _insert_image_with_ocr(session, text: str, confidence: float, language: str = "en") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    ocr_repo = OCRTextRepository(session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, text, confidence)], language)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_full_mode_indexes_lemmas_and_marks_image_done(db_session):
    image = await _insert_image_with_ocr(db_session, "grumpy cat picture", 0.9)

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=False, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    lemmas = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert "grumpy" in lemmas
    assert "cat" in lemmas

    status = await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).get_image_status(image.id)
    assert status.status == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_all_filtered_image_still_gets_marked_done(db_session):
    """Regression test for the Round 2 convergence fix, proven through the
    real run() code path rather than only through the unit-tested
    group_lemmas_by_image function in isolation."""
    image = await _insert_image_with_ocr(db_session, "garbled text", 0.1)  # below confidence_min=0.4

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=False, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    lemmas = (await db_session.execute(
        select(OCRLemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert lemmas == []

    status = await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).get_image_status(image.id)
    assert status is not None
    assert status.status == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_mode_skips_already_done_images(db_session):
    already_done = await _insert_image_with_ocr(db_session, "old news", 0.9)
    not_yet_done = await _insert_image_with_ocr(db_session, "grumpy cat", 0.9)

    await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).mark_done_by_id(already_done.id)
    await db_session.flush()

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=True, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    already_done_lemmas = (await db_session.execute(
        select(OCRLemma).where(OCRLemma.image_id == already_done.id)
    )).scalars().all()
    assert already_done_lemmas == []  # untouched -- was already marked done, never reprocessed

    not_yet_done_lemmas = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == not_yet_done.id)
    )).scalars().all()
    assert "grumpy" in not_yet_done_lemmas
    assert "cat" in not_yet_done_lemmas
```

- [ ] **Step 4: Run the new test file**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_ocr_lemmas.py -v`
Expected: PASS, all 3 tests. If any fails with a transaction/commit-related error, that means Task 2's fixture fix has a gap — stop and report BLOCKED rather than adding a workaround here; the whole point of Task 2 was to not need one anymore.

- [ ] **Step 5: Run the full four-root test sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.

- [ ] **Step 6: Commit**

```bash
git add batch/build_ocr_lemmas.py tests/integration/test_build_ocr_lemmas.py
git commit -m "test: add end-to-end integration test for build_ocr_lemmas.py's run()"
```

---

### Task 4: Final sweep

**Files:** None (verification only).

- [ ] **Step 1: Full four-root test sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.
