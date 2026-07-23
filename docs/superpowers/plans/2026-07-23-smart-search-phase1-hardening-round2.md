# Smart Search Phase 1 Hardening Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two backlog items surfaced while implementing Round 1's hardening: `OCRLemmasSaver.add_lemmas`'s insert-not-upsert fragility, and images whose OCR text entirely fails the confidence/lang-score filter never converging under `--incremental`.

**Architecture:** Two independent fixes touching overlapping files (`batch/utils/ocr_lemmas.py`, `batch/build_ocr_lemmas.py`, `repository/ocr_lemmas.py`) and their tests. Task 1 fixes the convergence gap by having `group_lemmas_by_image` track every seen image_id, not just ones with a surviving lemma. Task 2 fixes the upsert safety by switching `OCRLemmasSaver.add_lemmas` to a Postgres `ON CONFLICT DO NOTHING` insert.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM (Postgres dialect), PostgreSQL, pytest/pytest-asyncio.

## Global Constraints

- Reuse existing settings and infrastructure — no new config keys, no new tables.
- Match the existing upsert convention in this codebase exactly: `repository/image_extras.py`'s `ImageExtrasRepository.set_flagged` (`from sqlalchemy.dialects.postgresql import insert`, `.values(...)`, `.on_conflict_do_update(index_elements=[...], set_={...})`) — this plan's Task 2 uses `.on_conflict_do_nothing(index_elements=[...])` instead, since lemma presence is binary (nothing to update on conflict).
- `Backend/tests/`, `tests/integration/`, `batch/tests/`, `tests/rules/` are separate `pytest.ini` roots — never combine them in one `pytest` invocation.
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line, e.g.:
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
- Spec: `docs/superpowers/specs/2026-07-23-smart-search-phase1-hardening-round2-design.md` — read it before starting if a task here is unclear on rationale.

---

### Task 1: Every fetched image gets marked done, not just ones with a surviving lemma

**Files:**
- Modify: `batch/utils/ocr_lemmas.py`
- Modify: `batch/build_ocr_lemmas.py`
- Modify: `batch/tests/test_ocr_lemmas_grouping.py`

**Interfaces:**
- Changes: `group_lemmas_by_image(rows, morph, confidence_min, lang_score_min, min_word_length)` now returns a 3-tuple `(lemmas_by_image, all_image_ids, stats)` instead of a 2-tuple — `all_image_ids: set` is every distinct `image_id` seen in `rows`, regardless of whether any row for it passed the filter.
- Consumes (in `batch/build_ocr_lemmas.py`): the new `all_image_ids`, plus already-existing `OCRLemmasSaver.add_lemmas`/`ImageProcessingStatusRepository.mark_done_by_id` (unchanged by this task).

- [ ] **Step 1: Write the failing/updated tests**

Replace `batch/tests/test_ocr_lemmas_grouping.py` in full:

```python
from rules.normalize import make_morph
from batch.utils.ocr_lemmas import group_lemmas_by_image

_MORPH = make_morph()


def test_unions_lemmas_across_multiple_rows_for_same_image():
    rows = [
        ("img-1", "звоню в", 0.9, "ru", 1.0),
        ("img-1", "полицию", 0.9, "ru", 1.0),
    ]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "полиция" in lemmas_by_image["img-1"]
    assert "звонить" in lemmas_by_image["img-1"]
    assert all_image_ids == {"img-1"}


def test_separate_images_kept_separate():
    rows = [
        ("img-1", "cat picture", 0.9, "en", 1.0),
        ("img-2", "dog picture", 0.9, "en", 1.0),
    ]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "cat" in lemmas_by_image["img-1"]
    assert "dog" not in lemmas_by_image["img-1"]
    assert "dog" in lemmas_by_image["img-2"]
    assert all_image_ids == {"img-1", "img-2"}


def test_low_confidence_row_skipped():
    rows = [("img-1", "cat picture", 0.1, "en", 1.0)]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats == {"rows_total": 1, "rows_skipped": 1, "rows_processed": 0}
    assert all_image_ids == {"img-1"}


def test_low_lang_score_row_skipped():
    rows = [("img-1", "cat picture", 0.9, "en", 0.0)]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats["rows_skipped"] == 1
    assert all_image_ids == {"img-1"}


def test_digit_tokens_kept_as_lemmas():
    rows = [("img-1", "made in 2020", 0.9, "en", 1.0)]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "2020" in lemmas_by_image["img-1"]
    assert all_image_ids == {"img-1"}


def test_stats_counts_total_and_processed():
    rows = [
        ("img-1", "cat", 0.9, "en", 1.0),
        ("img-1", "dog", 0.1, "en", 1.0),
    ]
    _, _, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert stats == {"rows_total": 2, "rows_skipped": 1, "rows_processed": 1}


def test_no_rows_returns_empty():
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image([], _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert lemmas_by_image == {}
    assert all_image_ids == set()
    assert stats == {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}


def test_image_with_all_rows_filtered_out_still_appears_in_all_image_ids():
    """Regression test: an image whose every OCR row fails the
    confidence/lang-score filter must still be tracked as seen, so the
    caller can mark it done and stop reprocessing it forever."""
    rows = [
        ("img-1", "cat picture", 0.1, "en", 1.0),
        ("img-1", "more text", 0.1, "en", 1.0),
    ]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert all_image_ids == {"img-1"}
    assert stats == {"rows_total": 2, "rows_skipped": 2, "rows_processed": 0}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest batch/tests/test_ocr_lemmas_grouping.py -v`
Expected: FAIL — every test raises `ValueError: not enough values to unpack (expected 3, got 2)` (or similar), since `group_lemmas_by_image` still returns a 2-tuple.

- [ ] **Step 3: Implement the 3-tuple return in `batch/utils/ocr_lemmas.py`**

Replace the function body:

```python
def group_lemmas_by_image(rows, morph, confidence_min, lang_score_min, min_word_length):
    """
    rows: iterable of (image_id, text, confidence, language, lang_score).

    Returns (lemmas_by_image, all_image_ids, stats):
      - lemmas_by_image: dict[image_id, set[str]] — the union of lemmas
        across every surviving OCR row for that image. This union is what
        makes cross-line phrase matching work: a multi-word query matches
        as soon as each word's lemma is present anywhere in the image's
        set, regardless of which OCR line contributed it.
      - all_image_ids: set of every distinct image_id seen in rows,
        regardless of whether any of its rows passed the filter -- lets
        the caller mark an image done even when it produced zero lemmas
        (including when every one of its rows was filtered out).
      - stats: {"rows_total": int, "rows_skipped": int, "rows_processed": int}
    """
    lemmas_by_image = defaultdict(set)
    all_image_ids = set()
    stats = {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}

    for image_id, text, confidence, language, lang_score in rows:
        all_image_ids.add(image_id)
        stats["rows_total"] += 1
        if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
            stats["rows_skipped"] += 1
            continue
        # Lemmatized using this row's own detected language (always a confident
        # ru/en/es per EasyOCR — never None/"unknown" in practice, confirmed
        # against real corpus data). A Cyrillic row EasyOCR confidently but
        # wrongly tagged non-ru still only gets lowercased here, while the same
        # word in a search query (repository/ocr_lemmas.py's matching_image_ids)
        # always gets real lemmatization via language=None's script-based
        # fallback. Accepted asymmetry — fixing OCR language misdetection is a
        # separate problem (see docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md).
        lemmas_by_image[image_id] |= normalize(
            text, morph, min_length=min_word_length, language=language, keep_digit_tokens=True
        )
        stats["rows_processed"] += 1

    return dict(lemmas_by_image), all_image_ids, stats
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest batch/tests/test_ocr_lemmas_grouping.py -v`
Expected: PASS, all 8 tests.

- [ ] **Step 5: Update the caller in `batch/build_ocr_lemmas.py`**

Replace:

```python
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
                await status_repo.mark_done_by_id(image_id)
                metrics.add("lemmas.total", len(lemma_set))
                metrics.bucket("lemmas_per_image", len(lemma_set))
                tracker.mark_done()
```

with:

```python
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
                saver.add_lemmas(image_id, lemma_set)
                await status_repo.mark_done_by_id(image_id)
                metrics.add("lemmas.total", len(lemma_set))
                metrics.bucket("lemmas_per_image", len(lemma_set))
                tracker.mark_done()
```

(Note: `saver.add_lemmas` here is still called synchronously without `await` — Task 2 of this plan changes it to an async, upserting call. Don't add `await` yet in this task; that's Task 2's job, and adding it here would make this task's diff harder to review in isolation.)

- [ ] **Step 6: Run the full batch/tests root to confirm no regression**

Run: `pytest batch/tests/ -v`
Expected: PASS, all tests (including the other files in this root).

- [ ] **Step 7: Commit**

```bash
git add batch/utils/ocr_lemmas.py batch/build_ocr_lemmas.py batch/tests/test_ocr_lemmas_grouping.py
git commit -m "fix: mark every fetched image done, even when all its OCR rows are filtered out"
```

---

### Task 2: Upsert-safe `OCRLemmasSaver.add_lemmas`

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Modify: `batch/build_ocr_lemmas.py`
- Modify: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Changes: `OCRLemmasSaver.add_lemmas(image_id, lemmas: set) -> None` becomes `async def add_lemmas(image_id, lemmas: set) -> None`. Behavior: a multi-row Postgres `INSERT ... ON CONFLICT (image_id, lemma) DO NOTHING`, batched as one statement per call (one row per lemma). Callers must now `await` it.

- [ ] **Step 1: Write the failing/updated tests**

In `tests/integration/test_ocr_lemmas_repository.py`, update the existing
`test_saver_writes_one_row_per_lemma` to await the now-async call:

```python
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
```

Add this new test directly after it:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v -k add_lemmas`
Expected: FAIL — `TypeError: object NoneType can't be used in 'await' expression` (or similar), since `add_lemmas` is currently a plain sync method that returns `None`, not an awaitable.

- [ ] **Step 3: Implement the upsert in `repository/ocr_lemmas.py`**

Add the import (alongside the existing `sqlalchemy` imports):

```python
from sqlalchemy.dialects.postgresql import insert
```

Replace `OCRLemmasSaver.add_lemmas`:

```python
    async def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        if not lemmas:
            return
        stmt = (
            insert(OCRLemma)
            .values([{"image_id": image_id, "lemma": lemma} for lemma in lemmas])
            .on_conflict_do_nothing(index_elements=["image_id", "lemma"])
        )
        await self.session.execute(stmt)
```

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: PASS, all tests in the file (confirms no regression on the other `matching_image_ids`/`delete_all` tests).

- [ ] **Step 5: Add the `await` at the one caller in `batch/build_ocr_lemmas.py`**

Change:

```python
                saver.add_lemmas(image_id, lemma_set)
```

to:

```python
                await saver.add_lemmas(image_id, lemma_set)
```

- [ ] **Step 6: Run the full four-root test sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.

- [ ] **Step 7: Commit**

```bash
git add repository/ocr_lemmas.py batch/build_ocr_lemmas.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "fix: make OCRLemmasSaver.add_lemmas upsert-safe (ON CONFLICT DO NOTHING)"
```

---

### Task 3: Final sweep and real-data verification

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

- [ ] **Step 2: Re-run `build_ocr_lemmas.py --incremental` against `metal` and confirm the
  previously-known ~585 all-filtered images are now included exactly once, then excluded**

This is a real-data confirmation that Task 1's fix closes the gap that was already
observed against production data (not just the synthetic test fixtures). Requires
`metal`'s `.env.metal` file and `DATABASE_URL` (see `environments/Environments.md`).
Incremental mode makes no destructive changes (no `delete_all()` calls) — it only adds
rows for images not yet marked done, so this is safe to run without the same caution a
full-mode rebuild needs.

```powershell
python -m batch.build_ocr_lemmas --env metal --incremental
```

Expected: `Total images` is non-zero (the ~585 previously-stuck images, plus any newly
imported images since the last run) and the run completes without an `IntegrityError`.

Run it again immediately after:

```powershell
python -m batch.build_ocr_lemmas --env metal --incremental
```

Expected: `Total images: 0` — the previously-stuck images are now correctly marked done
and excluded, confirming both fixes together (Task 1's convergence fix, and Task 2's
upsert safety in case any of those images had partial prior state).
