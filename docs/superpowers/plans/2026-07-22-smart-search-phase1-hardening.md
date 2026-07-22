# Smart Search Phase 1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the three Minor findings from the smart-search Phase 1 branch's final review: document an accepted lemmatization asymmetry, fix incremental-mode non-convergence for lemma-less images, and add an automated cross-endpoint-equivalence test.

**Architecture:** Three independent, non-overlapping fixes. Problem 1 is comment-only (no behavior change, confirmed via real corpus data that the case it might otherwise address doesn't occur). Problem 2 adopts the existing `ImageProcessingStatusRepository` pattern (already used elsewhere for per-image/per-pipeline completion tracking) instead of inferring "already processed" from the presence of output rows. Problem 3 adds a new integration test locking in behavior that already works.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, PostgreSQL, pytest/pytest-asyncio.

## Global Constraints

- Reuse existing settings and infrastructure — no new config keys, no new tables.
- `repository/` (top-level, batch-facing) classes may call `session.commit()` directly; `Backend/app/repositories/` classes must not (existing convention, unaffected by this plan — this plan doesn't touch `Backend/app/repositories/` write paths).
- `ImageProcessingStatusRepository.mark_done_by_id` must follow the exact "id-only, no commit — caller controls commit timing" convention already used by `record_failure`/`delete_all` in the same file, not the `mark_done(image)` convention used by `extract_text_from_memes.py` (which requires a full `Image` ORM object this plan's caller doesn't have).
- `Backend/tests/`, `tests/integration/`, `batch/tests/`, `tests/rules/` are separate `pytest.ini` roots — never combine them in one `pytest` invocation.
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line, e.g.:
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
- Spec: `docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md` — read it before starting if a task here is unclear on rationale.

---

### Task 1: Document the accepted lemmatization language asymmetry

**Files:**
- Modify: `batch/utils/ocr_lemmas.py`
- Modify: `repository/ocr_lemmas.py`

**Interfaces:** None — comment-only change, no signatures or behavior affected.

No test changes for this task — it's documentation only, and the design doc confirms (via real corpus data: `ocr_texts.language` is always a confident `ru`/`en`/`es`, never `NULL`/`"unknown"`) that there is no behavior gap to cover with a test.

- [ ] **Step 1: Add the comment in `batch/utils/ocr_lemmas.py`**

In `group_lemmas_by_image`, immediately above the `normalize(...)` call, add:

```python
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
```

- [ ] **Step 2: Add the comment in `repository/ocr_lemmas.py`**

In `matching_image_ids`, immediately above the `normalize(...)` call, add:

```python
    # language=None enables pymorphy3's script-based fallback (real Cyrillic
    # lemmatization) for a query string, which has no per-word language tag.
    # This is intentionally more thorough than the index side
    # (batch/utils/ocr_lemmas.py), which trusts each OCR row's own detected
    # language and skips lemmatization for confidently-non-Russian rows — see
    # that file's comment for the resulting (accepted) asymmetry.
    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
```

- [ ] **Step 3: Confirm nothing else changed**

Run: `git diff batch/utils/ocr_lemmas.py repository/ocr_lemmas.py`
Expected: only comment lines added, no code changes.

- [ ] **Step 4: Run the existing tests for both files to confirm no regression**

Run: `pytest batch/tests/test_ocr_lemmas_grouping.py -v`
Expected: PASS (unchanged — comments don't affect behavior)

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: PASS (unchanged)

- [ ] **Step 5: Commit**

```bash
git add batch/utils/ocr_lemmas.py repository/ocr_lemmas.py
git commit -m "doc: note accepted OCR-language lemmatization asymmetry"
```

---

### Task 2: Fix incremental-mode convergence for lemma-less images

**Files:**
- Modify: `repository/image_procesing_status.py`
- Modify: `repository/images.py`
- Modify: `batch/build_ocr_lemmas.py`
- Modify: `tests/integration/test_image_processing_status_repository.py`
- Modify: `tests/integration/test_images_repository.py`

**Interfaces:**
- Produces: `ImageProcessingStatusRepository.mark_done_by_id(image_id) -> None` — no commit (caller controls commit timing, same convention as the existing `record_failure`/`delete_all` in the same class). Sets `status="done"`, `finished_at=datetime.utcnow()`; creates the row if none exists for `(image_id, self.pipeline)`, otherwise updates it in place (idempotent — never creates a duplicate).
- Changes: `ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language()`'s "already indexed" check now queries `ImageProcessingStatus(pipeline="ocr_lemmas", status="done")` instead of `OCRLemma` presence. Method name and external contract (row shape, meaning "images not yet lemma-indexed") are unchanged.
- Consumes (in `batch/build_ocr_lemmas.py`): the above, plus already-existing `OCRLemmasRepository`/`OCRLemmasSaver` (unchanged).

- [ ] **Step 1: Write the failing tests for `mark_done_by_id`**

Add to `tests/integration/test_image_processing_status_repository.py` (after `test_record_failure_writes_without_committing`):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_writes_without_committing(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, "ocr_lemmas")

    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == "ocr_lemmas",
        )
    )
    row = result.scalar_one()
    assert row.status == "done"
    assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_is_idempotent(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, "ocr_lemmas")

    await repo.mark_done_by_id(image.id)
    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == "ocr_lemmas",
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_processing_status_repository.py -v -k mark_done_by_id`
Expected: FAIL — `AttributeError: 'ImageProcessingStatusRepository' object has no attribute 'mark_done_by_id'`

- [ ] **Step 3: Implement `mark_done_by_id`**

In `repository/image_procesing_status.py`, add this method (directly after `record_failure`, before `get_image_ids_with_status`):

```python
    async def mark_done_by_id(self, image_id) -> None:
        """No commit — caller controls commit timing via its own batch committer."""
        status = await self.session.get(
            ImageProcessingStatus, {"image_id": image_id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image_id=image_id, pipeline=self.pipeline)
            self.session.add(status)
        status.status = "done"
        status.finished_at = datetime.utcnow()
```

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_processing_status_repository.py -v`
Expected: PASS (all tests in the file — confirms no regression on the pre-existing `record_failure`/`get_image_ids_with_status`/`delete_all` tests)

- [ ] **Step 5: Write the failing tests for `ImagesRepository`'s updated check**

In `tests/integration/test_images_repository.py`, replace the existing
`test_get_images_and_ocr_texts_without_lemmas_excludes_indexed_images` test (it currently
seeds an `OCRLemma` row directly, which the method will no longer read):

```python
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

    status_repo = ImageProcessingStatusRepository(db_session, "ocr_lemmas")
    await status_repo.mark_done_by_id(indexed.id)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    matched_ids = {img_id for _filename, img_id, _text, _confidence, _language, _lang_score in rows}

    assert not_indexed.id in matched_ids
    assert indexed.id not in matched_ids
```

Then add this new regression test right after it (this is the actual bug the task fixes):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_lemmas_excludes_lemma_less_but_done_images(db_session):
    """Regression test: an image whose OCR text yields zero lemmas must still
    converge once marked done -- it must not be reprocessed forever just
    because it has no ocr_lemmas rows."""
    done_but_lemma_less = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(done_but_lemma_less)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(done_but_lemma_less, [(_BBOX, "xy", 0.9)], "en")
    await db_session.flush()

    status_repo = ImageProcessingStatusRepository(db_session, "ocr_lemmas")
    await status_repo.mark_done_by_id(done_but_lemma_less.id)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    matched_ids = {img_id for _filename, img_id, _text, _confidence, _language, _lang_score in rows}

    assert done_but_lemma_less.id not in matched_ids
```

Update the file's imports at the top: change

```python
from Storage.models import Image, OCRLemma
```

to

```python
from repository.image_procesing_status import ImageProcessingStatusRepository
from Storage.models import Image
```

(`OCRLemma` is dropped — nothing in this file references it anymore. `ImageProcessingStatus` the model class isn't imported here since the test bodies only ever go through `ImageProcessingStatusRepository`, never construct the model directly.)

- [ ] **Step 6: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: FAIL — the replaced test fails because `get_images_and_ocr_texts_without_lemmas_with_language` still checks `OCRLemma`, not `ImageProcessingStatus`, so `mark_done_by_id`'s effect is invisible to it and `indexed.id` incorrectly still appears in `matched_ids`. The new regression test fails the same way.

- [ ] **Step 7: Update `ImagesRepository`**

In `repository/images.py`, change the import line (line 6):

```python
from Storage.models import OCRText, Image, ImageDescription, ImageTag, ImageProcessingStatus
```

(`OCRLemma` is removed — nothing else in this file uses it.)

Replace `get_images_and_ocr_texts_without_lemmas_with_language`'s body:

```python
    async def get_images_and_ocr_texts_without_lemmas_with_language(self):
        already_indexed = (
            select(ImageProcessingStatus.image_id)
            .where(
                ImageProcessingStatus.pipeline == "ocr_lemmas",
                ImageProcessingStatus.status == "done",
            )
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

- [ ] **Step 8: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: PASS (all tests in the file — confirms no regression in the other lang-score-exposure tests)

- [ ] **Step 9: Wire `ImageProcessingStatusRepository` into `build_ocr_lemmas.py`**

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
from repository.images import ImagesRepository
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver

PIPELINE = "ocr_lemmas"


async def main(incremental: bool):
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH

    morph = make_morph()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        lemmas_repo = OCRLemmasRepository(session)
        images_repo = ImagesRepository(session)
        status_repo = ImageProcessingStatusRepository(session, PIPELINE)

        if not incremental:
            await lemmas_repo.delete_all()
            await status_repo.delete_all()

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
                await status_repo.mark_done_by_id(image_id)
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
                        help="Only process images not yet marked done for the ocr_lemmas "
                             "pipeline (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.incremental))
```

- [ ] **Step 10: Smoke-test the wiring against your local dev DB**

```powershell
python -m batch.build_ocr_lemmas --env metal --incremental
```

Expected: runs cleanly (exit 0); since metal's `ocr_lemmas` is already fully populated
from the Phase 1 rollout but the new `image_processing_status` pipeline rows don't exist
yet for `pipeline="ocr_lemmas"`, this run will reprocess every image once (same as any
first run under the new tracking scheme) — that's expected, not a bug. Run it a second
time immediately after:

```powershell
python -m batch.build_ocr_lemmas --env metal --incremental
```

Expected: `Total images: 0` (or very close to it) — this is the actual fix: a second
consecutive incremental run finds nothing left to do, including any lemma-less images,
because they're now correctly marked `done`.

- [ ] **Step 11: Run the full four-root test sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.

- [ ] **Step 12: Commit**

```bash
git add repository/image_procesing_status.py repository/images.py batch/build_ocr_lemmas.py tests/integration/test_image_processing_status_repository.py tests/integration/test_images_repository.py
git commit -m "fix: converge build_ocr_lemmas incremental mode for lemma-less images"
```

---

### Task 3: Automated cross-endpoint-equivalence test

**Files:**
- Create: `tests/integration/test_search_matching_equivalence.py`

**Interfaces:**
- Consumes: `Backend.app.repositories.image_repository.ImageRepository.search(q, tags, cursor_created_at, cursor_id, limit) -> (rows, facets)` (existing, unchanged), `Backend.app.repositories.recommendations_repository.RecommendationsRepository.get_recommendations(q, seed, last_hash, limit) -> rows` (existing, unchanged).

No implementation code changes in this task — the behavior being tested already works
(verified manually during Phase 1). This is a lock-in test: write it, run it, confirm it
passes immediately (there is no RED phase here, since nothing is being implemented).

- [ ] **Step 1: Write the test file**

Create `tests/integration/test_search_matching_equivalence.py`:

```python
"""
Integration test proving /api/images and /api/recommendations agree on which
images match a given query -- the core promise of the shared
repository.ocr_lemmas.matching_image_ids implementation both now use.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Storage.models import Image, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_single_lemma_query_returns_same_ids_on_both_endpoints(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="hedgehog"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    image_rows, _ = await ImageRepository(db_session).search(
        q="hedgehog", tags={}, cursor_created_at=None, cursor_id=None, limit=50
    )
    rec_rows = await RecommendationsRepository(db_session).get_recommendations(
        q="hedgehog", seed=1, last_hash=None, limit=50
    )

    image_ids = {r.id for r in image_rows}
    rec_ids = {r.id for r in rec_rows}
    assert image_ids == {matching.id}
    assert rec_ids == {matching.id}
    assert image_ids == rec_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_word_and_query_returns_same_ids_on_both_endpoints(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="grumpy"),
        OCRLemma(image_id=both.id, lemma="cat"),
        OCRLemma(image_id=only_one.id, lemma="grumpy"),
    ])
    await db_session.flush()

    image_rows, _ = await ImageRepository(db_session).search(
        q="grumpy cat", tags={}, cursor_created_at=None, cursor_id=None, limit=50
    )
    rec_rows = await RecommendationsRepository(db_session).get_recommendations(
        q="grumpy cat", seed=1, last_hash=None, limit=50
    )

    image_ids = {r.id for r in image_rows}
    rec_ids = {r.id for r in rec_rows}
    assert image_ids == {both.id}
    assert rec_ids == {both.id}
    assert image_ids == rec_ids
```

- [ ] **Step 2: Run to verify it passes immediately**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_search_matching_equivalence.py -v`
Expected: PASS, 2/2. If either test fails, that's a real regression signal — stop and
report it rather than adjusting the test to match wrong behavior.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_search_matching_equivalence.py
git commit -m "test: lock in /api/images and /api/recommendations matching-result equivalence"
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

- [ ] **Step 2: Re-run `build_ocr_lemmas.py --incremental` twice against each of the three
  real environments (metal/general/it), confirming the second run of each pair converges
  to (near-)zero images processed**

This confirms Task 2's fix actually converges on real production data, not just the
synthetic integration-test fixtures. Requires each environment's `.env.<name>` file and
`DATABASE_URL` (see `environments/Environments.md`).
