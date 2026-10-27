# R3c Refresh Stale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the group 3 jobs a "refresh stale" mode that replaces the stale copies a recut cut inherited with rows computed from the cut itself, and one driver job, `refresh_recut_cuts`, that runs them in dependency order.

**Architecture:** Each refresh selects `cut_proposed` images that still carry stale rows in the job's own table (`Storage/stale.py`), skips images whose upstream rows are still stale, then deletes the stale rows and writes the replacements in one transaction. Jobs whose rows hang off a description (embeddings, lemmas) or are derived on their own (profiles) need no flag: deleting a stale description cascades its copies, and the normal incremental run fills the gap. The driver reuses `description_pipeline`'s `Step`/`run_steps`.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async, PostgreSQL (`ocrdb_test`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3c-refresh-stale-design.md`

## Global Constraints

- No migration, no change to `Storage/models.py` (spec decision 6). Another worktree (`meme-popularity`) works in parallel: keep shared-file diffs additive (`CLAUDE.md`, `docs/batches/inventory.md`, `environments/batch_registry.yaml`, `docs/image-status.md`, `docs/image-derived-data.md`).
- A stale row is deleted only in the transaction that writes its replacement (decision 3). A failed image keeps its stale rows and is retried on the next run.
- A job skips an image whose upstream rows are still stale and counts it `skipped_upstream_stale` (decision 4).
- Refresh selection looks only at the stale flag; normal runs are unchanged. Refresh only ever touches `cut_proposed` images.
- Refresh stats keys: `images_selected`, `refreshed`, `skipped_upstream_stale`, `failed`, `images_remaining`.
- Many repo files use CRLF (`environments/batch_registry.yaml`, some docs): preserve line endings; edit with the Edit tool or a Python read/replace/write script, never PowerShell Get/Set-Content.
- Tests: run roots separately. `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`; `pytest tests/docs/`; `pytest batch/tests/`. The test DB is shared: assert on ids, never on global counters. Never hand a subagent the main `DATABASE_URL`.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Run git plainly from the worktree (no `-C`, no `cd`).

## Deviations from the spec (ruled while planning; Task 9 amends the spec text)

1. **Only five jobs get `--refresh-stale`:** `extract_text_from_memes`, `build_ocr_lemmas`, `build_tags_from_ocr`, `build_image_descriptions`, `build_tags_from_descriptions` (five). `build_image_description_embeddings` and `build_description_lemmas` are incremental by absence and the stale description's copies cascade away when it is deleted, so their normal run is the refresh. `build_meme_profiles` gets `--status {active,cut_proposed}` instead (its staleness is derived from an input fingerprint); `build_meme_profile_embeddings` already covers any image with a profile and `_write` deletes the embedding when the situation changes. No other flag is needed.
2. **`--refresh-stale` ignores `--status`** (it always means `cut_proposed`); it conflicts only with `--reset`, `--incremental`, `--retry-failed` (argparse error).
3. **Selection by own stale rows only.** A cut whose source had no row in a job's table has nothing stale to replace; the normal incremental run fills it once the cut is `active` (same principle as R3b decision 1). Documented, not handled.
4. **Descriptions refresh is sequential, records no failure rows** (it counts `failed` and retries every run); no consecutive-failure stop except the 20-unreadable-files stop in the OCR refresh.
5. **Copied description feedback is lost** when a stale description is replaced (the feedback rows cascade with it). The new description starts unreviewed.
6. OCR refresh deletes the cut's `image_classifications` and `ocr_text_embeddings` rows; the `image_processing_status` markers of other jobs are moot (R3a does not copy them).

## Review Focus

- A refresh run on an image that has no OCR text at all (zero rows after OCR): stale rows must end deleted, not left. (Tasks 2, 3, 4 test it.)
- A job failing halfway (exception in the middle of a run): stale rows of all not-yet-replaced images must still be there; nothing half-written committed. (Tasks 2, 3, 5 test it.)
- Running the refresh twice: the second run selects nothing and changes nothing. (Tasks 2 to 6.)
- An `active` image carrying stale rows (e.g. after approval) is never touched by a refresh. (Task 1 helper test; every job test.)
- `--refresh-stale` combined with `--reset` on a job must never reach the destructive path. (Task 1 helper test, Task 7 for profiles.)

## File Structure

| File | Responsibility |
|---|---|
| `Storage/stale.py` (modify) | `source` filter for tag rows; `stale_among` |
| `batch/utils/refresh_stale.py` (create) | `refresh_stats`, `reject_conflicts` shared by the jobs |
| `repository/ocr_text.py`, `repository/image_descriptions.py` (modify) | read OCR rows / non-rejected descriptions of a set of images |
| `batch/utils/ocr_refresh.py` (create) | OCR refresh selection and file producer (light imports, testable without GPU) |
| `batch/utils/batch_commit.py` (modify) | `on_refresh_done` |
| `batch/extract_text_from_memes.py`, `build_ocr_lemmas.py`, `build_tags_from_ocr.py`, `build_image_descriptions.py`, `build_tags_from_descriptions.py`, `build_meme_profiles.py` (modify) | the modes |
| `repository/meme_profile_inputs.py` (modify) | `list_images(status)` |
| `batch/refresh_recut_cuts.py` (create) | the driver |
| `environments/batch_registry.yaml`, docs, spec, tracker (modify) | registration and documentation |

---

### Task 1: Shared helpers

**Files:**
- Modify: `Storage/stale.py`
- Create: `batch/utils/refresh_stale.py`
- Modify: `repository/ocr_text.py`, `repository/image_descriptions.py`
- Test: `tests/integration/test_stale_helper.py` (extend), `tests/integration/test_refresh_stale_helpers.py` (create)

**Interfaces:**
- Produces:
  - `images_with_stale_rows(session, model, status, source: str | None = None) -> list[uuid.UUID]` (new `source`, only valid for `ImageTag`, else `ValueError`)
  - `delete_stale_rows(session, model, image_ids, source: str | None = None) -> int`
  - `stale_among(session, model, image_ids, source: str | None = None) -> set[uuid.UUID]` – those ids that still have a stale row in `model`
  - `batch.utils.refresh_stale.refresh_stats(*, selected: int, refreshed: int, skipped_upstream_stale: int = 0, failed: int = 0) -> dict[str, int]` (keys per Global Constraints; `images_remaining = selected - refreshed`)
  - `batch.utils.refresh_stale.reject_conflicts(parser, refresh_stale: bool, **flags: bool) -> None` – calls `parser.error("--refresh-stale cannot be combined with --<flag-with-dashes>")` for the first truthy flag when `refresh_stale` is true
  - `OCRTextRepository.get_rows_for_images(image_ids) -> list[tuple[image_id, text, confidence, language, lang_score]]` (chunks of 5000 ids, ordered by `created_at, id`)
  - `ImageDescriptionsRepository.get_texts_for_images(image_ids) -> list[tuple[image_id, text]]` (only non-rejected descriptions, `description_not_rejected`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_stale_helper.py` (it already has the `_image` helper):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_source_scopes_tag_selection_and_deletion(db_session):
    cut = await _image(db_session)
    db_session.add_all([
        ImageTag(image_id=cut.id, key="k", value="a", source="OCR", stale=True),
        ImageTag(image_id=cut.id, key="k", value="b", source="Ollama", stale=True),
    ])
    await db_session.flush()

    assert await images_with_stale_rows(db_session, ImageTag, ImageStatus.CUT_PROPOSED, source="Ollama") == [cut.id]
    assert await stale.delete_stale_rows(db_session, ImageTag, [cut.id], source="OCR") == 1
    left = (await db_session.execute(select(ImageTag.source).where(ImageTag.image_id == cut.id))).scalars().all()
    assert left == ["Ollama"]
    assert await images_with_stale_rows(db_session, ImageTag, ImageStatus.CUT_PROPOSED, source="OCR") == []


@pytest.mark.asyncio(loop_scope="session")
async def test_source_is_rejected_for_a_model_without_one(db_session):
    with pytest.raises(ValueError):
        await images_with_stale_rows(db_session, OCRText, ImageStatus.CUT_PROPOSED, source="OCR")


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_among_reports_only_the_listed_images_that_are_still_stale(db_session):
    still = await _image(db_session)
    done = await _image(db_session)
    unrelated = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=still.id, text="a", stale=True),
        OCRText(image_id=done.id, text="b", stale=False),
        OCRText(image_id=unrelated.id, text="c", stale=True),
    ])
    await db_session.flush()

    assert await stale.stale_among(db_session, OCRText, [still.id, done.id]) == {still.id}
    assert await stale.stale_among(db_session, OCRText, []) == set()
```

Create `tests/integration/test_refresh_stale_helpers.py`:

```python
"""batch/utils/refresh_stale.py and the two read helpers the refresh modes use."""
import argparse
import uuid

import pytest

from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from repository.image_descriptions import ImageDescriptionsRepository
from repository.ocr_text import OCRTextRepository
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback, OCRText


def test_refresh_stats_counts_what_is_still_stale():
    assert refresh_stats(selected=5, refreshed=3, skipped_upstream_stale=1, failed=1) == {
        "images_selected": 5, "refreshed": 3, "skipped_upstream_stale": 1, "failed": 1, "images_remaining": 2}


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def test_reject_conflicts_names_the_flag_and_exits():
    with pytest.raises(SystemExit):
        reject_conflicts(_parser(), True, reset=True, retry_failed=False)


def test_reject_conflicts_is_silent_without_the_mode_or_without_a_conflict():
    reject_conflicts(_parser(), False, reset=True)
    reject_conflicts(_parser(), True, reset=False, retry_failed=False)


async def _image(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_rows_for_images_returns_only_the_requested_images(db_session):
    a, b = await _image(db_session), await _image(db_session)
    db_session.add_all([OCRText(image_id=a.id, text="x", confidence=0.9, language="en", lang_score=0.8),
                        OCRText(image_id=b.id, text="y", confidence=0.9, language="en", lang_score=0.8)])
    await db_session.flush()

    rows = await OCRTextRepository(db_session).get_rows_for_images([a.id])
    assert rows == [(a.id, "x", 0.9, "en", 0.8)]
    assert await OCRTextRepository(db_session).get_rows_for_images([]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_description_texts_for_images_leave_out_rejected_descriptions(db_session):
    image = await _image(db_session)
    kept = ImageDescription(image_id=image.id, prompt_key="p1", model_used="m", text="kept")
    rejected = ImageDescription(image_id=image.id, prompt_key="p2", model_used="m", text="rejected")
    db_session.add_all([kept, rejected])
    await db_session.flush()
    db_session.add(ImageDescriptionFeedback(image_description_id=rejected.id, approved=False))
    await db_session.flush()

    rows = await ImageDescriptionsRepository(db_session).get_texts_for_images([image.id])
    assert rows == [(image.id, "kept")]
```

(Check the `ImageDescriptionFeedback` column names in `Storage/models.py` and match them; `tests/integration/test_description_not_rejected.py` shows the constructor.)

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_stale_helper.py tests/integration/test_refresh_stale_helpers.py -v`
Expected: FAIL (`source` unexpected keyword, `stale_among` / `batch.utils.refresh_stale` missing).

- [ ] **Step 3: Implement**

`Storage/stale.py`: add `source` handling and `stale_among`.

```python
def _scope_to_source(statement, model: type, source: str | None):
    if source is None:
        return statement
    if not hasattr(model, "source"):
        raise ValueError(f"{model.__name__} has no source column")
    return statement.where(model.source == source)
```

Change `images_with_stale_rows(session, model, status, source=None)`: after building `statement`, `statement = _scope_to_source(statement, model, source)`. Change `delete_stale_rows(session, model, image_ids, source=None)`: build `statement = delete(model).where(model.stale.is_(True), model.image_id.in_(chunk))` then `_scope_to_source(statement, model, source)` before executing. Add:

```python
async def stale_among(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID],
                      source: str | None = None) -> set[uuid.UUID]:
    """Those of `image_ids` that still have a stale row in `model` (the upstream-ready check of a refresh)."""
    _require_stale_model(model)
    ids = list(image_ids)
    found: set[uuid.UUID] = set()
    for start in range(0, len(ids), _CHUNK):
        statement = select(model.image_id).where(model.stale.is_(True), model.image_id.in_(ids[start:start + _CHUNK])).distinct()
        found.update((await session.execute(_scope_to_source(statement, model, source))).scalars().all())
    return found
```

`batch/utils/refresh_stale.py`:

```python
"""Pieces shared by the 'refresh stale' modes of the group 3 jobs (screenshot recut R3c)."""


def refresh_stats(*, selected: int, refreshed: int, skipped_upstream_stale: int = 0, failed: int = 0) -> dict[str, int]:
    return {"images_selected": selected, "refreshed": refreshed, "skipped_upstream_stale": skipped_upstream_stale,
            "failed": failed, "images_remaining": selected - refreshed}


def reject_conflicts(parser, refresh_stale: bool, **flags: bool) -> None:
    """--refresh-stale only selects stale rows of cuts; a flag that clears or widens the selection must not be combined with it."""
    if not refresh_stale:
        return
    for name, enabled in flags.items():
        if enabled:
            parser.error(f"--refresh-stale cannot be combined with --{name.replace('_', '-')}")
```

`repository/ocr_text.py` (add import `from Storage.models import OCRText` already there):

```python
    async def get_rows_for_images(self, image_ids) -> list[tuple]:
        """(image_id, text, confidence, language, lang_score) of the given images' OCR rows, oldest first."""
        ids = list(image_ids)
        rows: list[tuple] = []
        for start in range(0, len(ids), 5000):
            result = await self.session.execute(
                select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.language, OCRText.lang_score)
                .where(OCRText.image_id.in_(ids[start:start + 5000]))
                .order_by(OCRText.created_at, OCRText.id))
            rows.extend(tuple(row) for row in result.all())
        return rows
```

`repository/image_descriptions.py`:

```python
    async def get_texts_for_images(self, image_ids) -> list[tuple[uuid.UUID, str]]:
        """(image_id, text) of the given images' descriptions that are not rejected."""
        ids = list(image_ids)
        rows: list[tuple[uuid.UUID, str]] = []
        for start in range(0, len(ids), 5000):
            result = await self.session.execute(
                select(ImageDescription.image_id, ImageDescription.text)
                .where(ImageDescription.image_id.in_(ids[start:start + 5000]),
                       description_not_rejected(ImageDescription.id))
                .order_by(ImageDescription.created_at, ImageDescription.id))
            rows.extend((image_id, text) for image_id, text in result.all())
        return rows
```

(Verify `ImageDescription.created_at` exists; it is used in `repository/images.py`.)

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Storage/stale.py batch/utils/refresh_stale.py repository/ocr_text.py repository/image_descriptions.py tests/integration/test_stale_helper.py tests/integration/test_refresh_stale_helpers.py
git commit -m "feat(recut): shared helpers for the refresh-stale modes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: OCR lemmas refresh

**Files:**
- Modify: `batch/build_ocr_lemmas.py`
- Test: `tests/integration/test_build_ocr_lemmas.py` (extend; read its harness first)

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces: `async def refresh_stale_lemmas(session, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length) -> dict[str, int]` (returns `refresh_stats`); `main(..., refresh_stale: bool = False)`; CLI `--refresh-stale`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_build_ocr_lemmas.py` (reuse its morph/helpers; if it builds `morph` with `make_morph()`, do the same) add:

```python
from batch.build_ocr_lemmas import refresh_stale_lemmas
from Storage.models import Image, ImageStatus, OCRLemma, OCRText


async def _cut(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(image)
    await session.flush()
    return image


async def _refresh(session):
    return await refresh_stale_lemmas(session, make_morph(), 0.0, 0.0, 3)


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_replaces_stale_lemmas_with_the_new_ocr(db_session):
    cut = await _cut(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="cats sleeping", confidence=0.9, language="en", lang_score=0.9, stale=False),
        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True),
    ])
    await db_session.flush()

    stats = await _refresh(db_session)

    rows = (await db_session.execute(select(OCRLemma.lemma, OCRLemma.stale).where(OCRLemma.image_id == cut.id))).all()
    assert rows and all(stale is False for _, stale in rows)
    assert "stalecopy" not in [lemma for lemma, _ in rows]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_skips_an_image_whose_ocr_is_still_stale(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="cats", language="en", stale=True),
                        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True)])
    await db_session.flush()

    stats = await _refresh(db_session)

    assert stats["skipped_upstream_stale"] >= 1
    left = (await db_session.execute(select(OCRLemma.stale).where(OCRLemma.image_id == cut.id))).scalars().all()
    assert left == [True]


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_with_no_ocr_text_leaves_no_stale_lemmas(db_session):
    cut = await _cut(db_session)
    db_session.add(OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True))
    await db_session.flush()

    await _refresh(db_session)

    assert (await db_session.execute(select(OCRLemma).where(OCRLemma.image_id == cut.id))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_ignores_active_images_and_a_second_run_changes_nothing(db_session):
    active = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    db_session.add(active)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=active.id, lemma="keep", stale=True))
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="cats", language="en", confidence=0.9, lang_score=0.9),
                        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True)])
    await db_session.flush()

    await _refresh(db_session)
    second = await _refresh(db_session)

    assert (await db_session.execute(select(OCRLemma.stale).where(OCRLemma.image_id == active.id))).scalars().all() == [True]
    assert second["images_selected"] == 0 or cut.id not in await images_with_stale_rows(db_session, OCRLemma, ImageStatus.CUT_PROPOSED)
```

(Import `images_with_stale_rows`, `uuid`, `select`, `pytest` as the file needs.) Simplify the last assertion to `assert cut.id not in await images_with_stale_rows(...)` if cleaner.

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_ocr_lemmas.py -v -k refresh`
Expected: FAIL (ImportError `refresh_stale_lemmas`).

- [ ] **Step 3: Implement**

In `batch/build_ocr_lemmas.py` add imports (`from batch.utils.refresh_stale import refresh_stats, reject_conflicts`, `from repository.ocr_text import OCRTextRepository`, `from Storage.models import ImageStatus, OCRLemma, OCRText`, `from Storage.stale import delete_stale_rows, images_with_stale_rows, stale_among`) and:

```python
async def refresh_stale_lemmas(session, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length) -> dict:
    """Replaces the stale lemma rows of cut_proposed images with lemmas of their (refreshed) OCR. One transaction:
    on any error nothing is committed and the stale rows stay."""
    selected = await images_with_stale_rows(session, OCRLemma, ImageStatus.CUT_PROPOSED)
    blocked = await stale_among(session, OCRText, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    rows = await OCRTextRepository(session).get_rows_for_images(ready)
    lemmas_by_image, _, _ = group_lemmas_by_image(
        rows, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length)
    status_repo = ImageProcessingStatusRepository(session, OCR_LEMMAS_PIPELINE)
    saver = OCRLemmasSaver(session)
    try:
        await delete_stale_rows(session, OCRLemma, ready)
        for image_id in ready:
            await saver.add_lemmas(image_id, lemmas_by_image.get(image_id, set()))
            await status_repo.mark_done_by_id(image_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))
```

(`OCRLemmasSaver.__aexit__` commits unconditionally, so it is used without `async with` on purpose.) Wire it: `_process(incremental, status, refresh_stale=False)` – when `refresh_stale`, open a session and `stats = await refresh_stale_lemmas(...)`, print it, return it; `main(..., refresh_stale: bool = False)` passes through and, when it returns stats, `record_stats(run_id, stats)` (import from `batch.run_tracking`, same pattern as `build_image_descriptions.main`). In `main` raise `ValueError` if `refresh_stale and not incremental`? No: refresh ignores `incremental`; just skip the `pending` guard when refreshing. CLI: add

```python
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Replace the stale lemmas of cut_proposed images (recut) with lemmas of their refreshed OCR; "
                             "ignores --status")
    ...
    reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)
```
and `main(incremental=args.incremental, status=args.status, refresh_stale=args.refresh_stale)`. Update the module docstring (one sentence).

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_build_ocr_lemmas.py -v`
Expected: PASS (new and old tests).

- [ ] **Step 5: Commit**

```bash
git add batch/build_ocr_lemmas.py tests/integration/test_build_ocr_lemmas.py
git commit -m "feat(recut): build_ocr_lemmas --refresh-stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: OCR tags refresh

**Files:**
- Modify: `batch/build_tags_from_ocr.py`
- Test: `tests/integration/test_build_tags_from_ocr_refresh.py` (create)

**Interfaces:**
- Consumes: Task 1 helpers (`source="OCR"`).
- Produces: `async def refresh_stale_ocr_tags(session, engine, ocr_confidence_min, ocr_lang_score_min) -> dict[str, int]`; `main(..., refresh_stale=False)`; CLI `--refresh-stale`.

`engine` is anything with `.tag(text, language=...)` returning an object with `.tags` (list of `(name, value)`), i.e. `ConceptTagger`.

- [ ] **Step 1: Write the failing tests**

```python
"""build_tags_from_ocr --refresh-stale (screenshot recut R3c)."""
import types
import uuid

import pytest
from sqlalchemy import select

from batch.build_tags_from_ocr import refresh_stale_ocr_tags
from Storage.models import Image, ImageStatus, ImageTag, OCRText


class FakeEngine:
    def tag(self, text, language="unknown"):
        return types.SimpleNamespace(tags=[("topic", "cats")] if "cat" in text else [])


async def _cut(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(image)
    await session.flush()
    return image


async def _tags(session, image, source="OCR"):
    return (await session.execute(select(ImageTag.key, ImageTag.value, ImageTag.stale)
                                  .where(ImageTag.image_id == image.id, ImageTag.source == source))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_replaces_stale_ocr_tags_and_leaves_other_sources(db_session):
    cut = await _cut(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="a cat", confidence=0.9, language="en", lang_score=0.9),
        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True),
        ImageTag(image_id=cut.id, key="topic", value="ollama", source="Ollama", stale=True),
    ])
    await db_session.flush()

    stats = await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert await _tags(db_session, cut) == [("topic", "cats", False)]
    assert await _tags(db_session, cut, "Ollama") == [("topic", "ollama", True)]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_new_ocr_without_tags_leaves_no_stale_ocr_tag(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="nothing", confidence=0.9, language="en", lang_score=0.9),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    await db_session.flush()

    await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert await _tags(db_session, cut) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_an_image_whose_ocr_is_still_stale(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="a cat", language="en", stale=True),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    await db_session.flush()

    stats = await refresh_stale_ocr_tags(db_session, FakeEngine(), 0.0, 0.0)

    assert stats["skipped_upstream_stale"] >= 1
    assert await _tags(db_session, cut) == [("topic", "dogs", True)]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_engine_failure_keeps_every_stale_tag(db_session):
    cut = await _cut(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="a cat", confidence=0.9, language="en", lang_score=0.9),
                        ImageTag(image_id=cut.id, key="topic", value="dogs", source="OCR", stale=True)])
    await db_session.flush()

    class Broken:
        def tag(self, text, language="unknown"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await refresh_stale_ocr_tags(db_session, Broken(), 0.0, 0.0)

    assert await _tags(db_session, cut) == [("topic", "dogs", True)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_build_tags_from_ocr_refresh.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

In `batch/build_tags_from_ocr.py` (imports: `from collections import defaultdict`, `from batch.utils.refresh_stale import refresh_stats, reject_conflicts`, `from repository.ocr_text import OCRTextRepository`, `from Storage.models import ImageStatus, ImageTag, OCRText`, `from Storage.stale import delete_stale_rows, images_with_stale_rows, stale_among`):

```python
async def refresh_stale_ocr_tags(session, engine, ocr_confidence_min, ocr_lang_score_min) -> dict:
    """Replaces the stale OCR tags of cut_proposed images with tags of their (refreshed) OCR. The tags are computed
    before anything is deleted, so an engine failure leaves every stale tag in place."""
    selected = await images_with_stale_rows(session, ImageTag, ImageStatus.CUT_PROPOSED, source="OCR")
    blocked = await stale_among(session, OCRText, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    tags_by_image = defaultdict(set)
    for image_id, text, confidence, language, lang_score in await OCRTextRepository(session).get_rows_for_images(ready):
        if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
            continue
        tags_by_image[image_id].update(engine.tag(text, language=language or "unknown").tags)
    try:
        await delete_stale_rows(session, ImageTag, ready, source="OCR")
        for image_id in ready:
            for name, value in sorted(tags_by_image.get(image_id, ())):
                session.add(ImageTag(image_id=image_id, key=name, value=value, source="OCR"))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))
```

Wire `_process(incremental, refresh_stale=False)`: when refreshing, after `engine = ConceptTagger.load(...)` open a session, call it, print and return the stats; `main(..., refresh_stale=False)` records stats via `record_stats` (as in Task 2). CLI `--refresh-stale` with `reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)`. Update the docstring.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_build_tags_from_ocr_refresh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/build_tags_from_ocr.py tests/integration/test_build_tags_from_ocr_refresh.py
git commit -m "feat(recut): build_tags_from_ocr --refresh-stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: OCR refresh

**Files:**
- Create: `batch/utils/ocr_refresh.py`
- Modify: `batch/utils/batch_commit.py`, `batch/extract_text_from_memes.py`
- Test: `tests/integration/test_ocr_refresh.py` (create)

**Interfaces:**
- Produces:
  - `batch.utils.ocr_refresh.MAX_CONSECUTIVE_UNREADABLE = 20`
  - `async def select_refresh_images(session) -> list[Image]` – `cut_proposed` images with stale `ocr_texts`, oldest first
  - `async def refresh_producer(path, images, io_queue, metrics_listener, tracker) -> None` – puts `(filename, bytes, read_seconds, image)` items then `None`; counts unreadable files (`error.reading`, `tracker.skip()`); after 20 consecutive unreadable files puts `None` and raises `RuntimeError`
  - `BatchCommitter.on_refresh_done(image, metrics: dict) -> None`
  - `extract_text_from_memes.main(trigger, run_id, path, target_status, refresh_stale: bool = False)`; CLI `--refresh-stale`

- [ ] **Step 1: Write the failing tests**

```python
"""OCR refresh pieces (screenshot recut R3c): selection, the file producer and the per-image commit hook.
The GPU pipeline itself is not run here."""
import asyncio
import types
import uuid

import pytest
from sqlalchemy import select

from batch.utils.batch_commit import BatchCommitter
from batch.utils.ocr_refresh import MAX_CONSECUTIVE_UNREADABLE, refresh_producer, select_refresh_images
from Storage.models import (Image, ImageClassification, ImageStatus, OCRText, OCRTextEmbedding)


class Counter:
    def __init__(self):
        self.counts = {}

    def increment(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1


class Tracker:
    skipped = 0

    def skip(self):
        self.skipped += 1


async def _image(session, status=ImageStatus.CUT_PROPOSED):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_select_returns_only_cut_images_with_stale_ocr(db_session):
    stale_cut = await _image(db_session)
    fresh_cut = await _image(db_session)
    stale_active = await _image(db_session, ImageStatus.ACTIVE)
    db_session.add_all([OCRText(image_id=stale_cut.id, text="a", stale=True),
                        OCRText(image_id=fresh_cut.id, text="b", stale=False),
                        OCRText(image_id=stale_active.id, text="c", stale=True)])
    await db_session.flush()

    ids = [image.id for image in await select_refresh_images(db_session)]

    assert stale_cut.id in ids and fresh_cut.id not in ids and stale_active.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_producer_reads_files_and_ends_the_queue(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"data")
    image = types.SimpleNamespace(filename="a.jpg")
    queue = asyncio.Queue()
    metrics, tracker = Counter(), Tracker()

    await refresh_producer(str(tmp_path), [image], queue, metrics, tracker)

    file, data, read_seconds, item = await queue.get()
    assert (file, data, item) == ("a.jpg", b"data", image)
    assert await queue.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_producer_counts_unreadable_files_and_stops_after_twenty_in_a_row(tmp_path):
    images = [types.SimpleNamespace(filename=f"missing{i}.jpg") for i in range(MAX_CONSECUTIVE_UNREADABLE + 5)]
    queue = asyncio.Queue()
    metrics, tracker = Counter(), Tracker()

    with pytest.raises(RuntimeError):
        await refresh_producer(str(tmp_path), images, queue, metrics, tracker)

    assert metrics.counts["error.reading"] == MAX_CONSECUTIVE_UNREADABLE
    assert await queue.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_done_clears_what_was_computed_from_the_old_text(db_session):
    cut = await _image(db_session)
    other = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="old", language="ru", stale=True),
        ImageClassification(image_id=cut.id, classifier="text_heavy", result="text_heavy"),
        OCRTextEmbedding(image_id=cut.id, embedding=[0.1] * 384),
        ImageClassification(image_id=other.id, classifier="text_heavy", result="text_heavy"),
    ])
    await db_session.flush()

    committer = BatchCommitter(db_session, batch_size=1000, pipeline="easyocr:en")
    await committer.on_refresh_done(cut, {"read_time_ms": 0, "preprocess_time_ms": 0, "ocr_time_ms": 0, "total_time_ms": 0})

    assert (await db_session.execute(select(OCRText).where(OCRText.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(select(ImageClassification).where(ImageClassification.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(select(ImageClassification).where(ImageClassification.image_id == other.id))).scalars().all() != []
```

(Check how other tests build `OCRTextEmbedding` vectors and `ImageMetricsRepository.overwrite_metrics`' expected keys in `repository/image_metrics.py`; match the metrics dict keys used by `gpu_consumer`. In the last test the stale OCR row stands for "left over after rewriting": in the real flow `overwrite_texts` has replaced them already, `on_refresh_done` only sweeps leftovers.)

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_ocr_refresh.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

`batch/utils/ocr_refresh.py`:

```python
"""Pieces of extract_text_from_memes' --refresh-stale mode (screenshot recut R3c). Kept free of the OCR stack's heavy
imports so they can be tested without a GPU."""
import asyncio
import os
import time

from sqlalchemy import select

from Storage.models import Image, ImageStatus, OCRText
from Storage.stale import images_with_stale_rows

MAX_CONSECUTIVE_UNREADABLE = 20


async def select_refresh_images(session) -> list[Image]:
    """cut_proposed images whose OCR rows are still the stale copies from the source."""
    ids = await images_with_stale_rows(session, OCRText, ImageStatus.CUT_PROPOSED)
    if not ids:
        return []
    result = await session.execute(select(Image).where(Image.id.in_(ids)).order_by(Image.created_at, Image.id))
    return list(result.scalars().all())


async def refresh_producer(path, images, io_queue, metrics_listener, tracker) -> None:
    """Feeds the cut files to the OCR pipeline. An unreadable file leaves its image stale; 20 in a row stop the run."""
    consecutive = 0
    for image in images:
        started = time.perf_counter()
        try:
            with open(os.path.join(path, image.filename), "rb") as handle:
                data = handle.read()
        except OSError as error:
            print(f"Error: {error}")
            metrics_listener.increment("error.reading")
            tracker.skip()
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_UNREADABLE:
                await io_queue.put(None)
                raise RuntimeError(f"{consecutive} unreadable cut files in a row; stopping the OCR refresh")
            continue
        consecutive = 0
        await io_queue.put((image.filename, data, time.perf_counter() - started, image))
    await io_queue.put(None)
```

(`asyncio` import unused: drop it.)

`batch/utils/batch_commit.py`: add imports `from sqlalchemy import delete`, `from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding`, `from Storage.stale import delete_stale_rows` and:

```python
    async def on_refresh_done(self, image: Image, metrics: dict) -> None:
        """A cut's OCR was rewritten: sweep what is still the source's copy and what was computed from the old text
        (the text-heavy classification and the OCR-text embedding), so classify_text_heavy and
        build_ocr_text_embeddings recompute them from the new OCR."""
        await delete_stale_rows(self._session, OCRText, [image.id])
        await self._session.execute(delete(ImageClassification).where(ImageClassification.image_id == image.id))
        await self._session.execute(delete(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id))
        await self.on_image_done(image, metrics)
```

`batch/extract_text_from_memes.py`:
- imports: `from batch.run_tracking import finish_existing_run, record_stats, tracked_run`, `from batch.utils.ocr_refresh import refresh_producer, select_refresh_images`, `from batch.utils.refresh_stale import refresh_stats, reject_conflicts`.
- `gpu_consumer(..., refresh: bool = False)`: at the end of each image replace `await committer.on_image_done(image, {...})` by building the dict once and `await (committer.on_refresh_done if refresh else committer.on_image_done)(image, timings)`; add `metrics_listener.increment("refreshed")` when `refresh`.
- `run(path, batch_size, progress_every, target_status, refresh_stale=False) -> dict`: when `refresh_stale`, open a session, `images = await select_refresh_images(session)`, `total = len(images)`, and use `refresh_producer(path, images, io_queue, metrics_listener, tracker)` instead of `io_producer`; pass `refresh=refresh_stale` to `gpu_consumer`. At the end `return refresh_stats(selected=len(images), refreshed=metrics_listener.counters_dict().get("refreshed", 0), failed=len(images) - refreshed)` in refresh mode, `{}` otherwise. (`counters_dict()` exists on `SimpleMetricsListener`; confirm its name in `metrics/listener.py`.) Note: `failed` here = images not refreshed (unreadable, undecodable); name it `failed`.
- `_process(path, target_status, refresh_stale=False)` returns `run(...)`'s dict; `main(..., refresh_stale=False)` records stats in refresh mode via `record_stats` (for the `run_id` and `tracked_run` branches alike).
- CLI: `--refresh-stale` flag (help: "Re-run OCR on the cut_proposed images that still carry the stale OCR copied from their source; ignores --status") and no other conflict flags exist on this script, so no `reject_conflicts` call is needed.
- Docstring: add the mode.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_ocr_refresh.py tests/integration/test_ocr_text_repository.py -v` then `python -c "import ast,sys; ast.parse(open('batch/extract_text_from_memes.py',encoding='utf-8').read())"`.
Expected: PASS, no syntax error. (The full pipeline is verified live in R7's first run; say so in the ledger.)

- [ ] **Step 5: Commit**

```bash
git add batch/utils/ocr_refresh.py batch/utils/batch_commit.py batch/extract_text_from_memes.py tests/integration/test_ocr_refresh.py
git commit -m "feat(recut): extract_text_from_memes --refresh-stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Descriptions refresh

**Files:**
- Modify: `batch/build_image_descriptions.py`, `repository/image_descriptions.py`
- Test: `tests/integration/test_build_image_descriptions.py` (extend; read its harness)

**Interfaces:**
- Consumes: Task 1.
- Produces: `async def refresh_stale_descriptions(session, prompts, describer, base_path, num_ctx, model_for, limit=None) -> dict[str, int]`; `ImageDescriptionsRepository.delete_stale_for_prompt(image_id, prompt_key) -> None`; `main(..., refresh_stale=False)`; CLI `--refresh-stale`.
  - `describer.describe(path, prompt_text, model, num_ctx) -> str` (existing signature); `model_for(prompt) -> str`; `prompts` are the objects from `load_prompts` (`.key`, `.prompt`).

- [ ] **Step 1: Write the failing tests**

```python
import types

from batch.build_image_descriptions import refresh_stale_descriptions
from Storage.models import ImageDescription, ImageDescriptionEmbedding, ImageStatus, DescriptionLemma


PROMPTS = [types.SimpleNamespace(key="p1", prompt="describe"), types.SimpleNamespace(key="p2", prompt="describe more")]


class FakeDescriber:
    def __init__(self, fail_keys=()):
        self.fail_keys = fail_keys
        self.calls = []

    def describe(self, path, prompt_text, model, num_ctx):
        self.calls.append((path, prompt_text))
        if prompt_text in self.fail_keys:
            raise RuntimeError("model down")
        return f"new text for {prompt_text}"


async def _cut_with_stale_descriptions(session, keys=("p1", "p2")):
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add(cut)
    await session.flush()
    for key in keys:
        session.add(ImageDescription(image_id=cut.id, prompt_key=key, model_used="old", text=f"old {key}", stale=True))
    await session.flush()
    return cut


async def _rows(session, image):
    return (await session.execute(select(ImageDescription.prompt_key, ImageDescription.text, ImageDescription.stale)
                                  .where(ImageDescription.image_id == image.id).order_by(ImageDescription.prompt_key))).all()


async def _refresh(session, describer, limit=None):
    return await refresh_stale_descriptions(session, PROMPTS, describer, "/tmp/images", 2048, lambda p: "model", limit)


@pytest.mark.asyncio(loop_scope="session")
async def test_every_prompt_is_redescribed_and_the_stale_copies_go(db_session):
    cut = await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber())

    assert await _rows(db_session, cut) == [("p1", "new text for describe", False), ("p2", "new text for describe more", False)]
    assert stats["refreshed"] >= 1 and stats["failed"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_prompt_keeps_its_stale_copy_and_the_image_stays_selectable(db_session):
    cut = await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber(fail_keys=("describe more",)))

    assert await _rows(db_session, cut) == [("p1", "new text for describe", False), ("p2", "old p2", True)]
    assert stats["failed"] >= 1
    assert cut.id in await images_with_stale_rows(db_session, ImageDescription, ImageStatus.CUT_PROPOSED)


@pytest.mark.asyncio(loop_scope="session")
async def test_replacing_a_description_drops_its_copied_embedding_and_lemmas(db_session):
    cut = await _cut_with_stale_descriptions(db_session, keys=("p1",))
    description = (await db_session.execute(select(ImageDescription).where(ImageDescription.image_id == cut.id))).scalar_one()
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma="old"))
    await db_session.flush()

    await _refresh(db_session, FakeDescriber())

    assert (await db_session.execute(select(DescriptionLemma).where(DescriptionLemma.image_description_id == description.id))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_description_of_a_prompt_that_is_no_longer_configured_is_removed(db_session):
    cut = await _cut_with_stale_descriptions(db_session, keys=("p1", "retired"))

    await _refresh(db_session, FakeDescriber())

    assert [key for key, _, _ in await _rows(db_session, cut)] == ["p1", "p2"]


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_caps_the_images_and_reports_the_rest_as_remaining(db_session):
    first = await _cut_with_stale_descriptions(db_session)
    second = await _cut_with_stale_descriptions(db_session)

    stats = await _refresh(db_session, FakeDescriber(), limit=1)

    assert stats["images_remaining"] >= 1
```

(The `DescriptionLemma` constructor columns: check `Storage/models.py`. The test file may already define similar helpers; reuse, do not duplicate imports.)

The `limit=1` test asserts only `>=` because the shared DB may hold other cut images with stale descriptions.

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_build_image_descriptions.py -v -k "redescribed or failed_prompt or copied_embedding or retired or limit_caps"`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

`repository/image_descriptions.py`:

```python
    async def delete_stale_for_prompt(self, image_id, prompt_key: str) -> None:
        """Removes the stale copy of one (image, prompt) description; its feedback, embedding and lemmas cascade."""
        await self.session.execute(delete(ImageDescription).where(
            ImageDescription.image_id == image_id, ImageDescription.prompt_key == prompt_key,
            ImageDescription.stale.is_(True)))
```

`batch/build_image_descriptions.py` (imports: `from batch.utils.refresh_stale import refresh_stats, reject_conflicts`, `from Storage.models import ImageDescription, ImageStatus`, `from Storage.stale import delete_stale_rows, images_with_stale_rows`):

```python
async def refresh_stale_descriptions(session, prompts, describer, base_path, num_ctx, model_for, limit=None) -> dict:
    """Re-describes the cut_proposed images that still carry the stale descriptions copied from their source.
    A prompt that succeeds replaces its stale row (same transaction, committed per image); a prompt that fails keeps
    its stale row, nothing is recorded as failed and the image is retried on the next run. Sequential: the volume is
    the number of cuts, not the corpus."""
    ids = await images_with_stale_rows(session, ImageDescription, ImageStatus.CUT_PROPOSED)
    selected = ids[:limit] if limit is not None else ids
    repo = ImageDescriptionsRepository(session)
    filenames = {image_id: filename for filename, image_id in await ImagesRepository(session).get_all_images(ImageStatus.CUT_PROPOSED)}
    refreshed = failed = skipped = 0
    for image_id in selected:
        path = os.path.join(base_path, filenames[image_id])
        if has_unsupported_image_extension(path):
            skipped += 1
            continue
        all_ok = True
        for prompt in prompts:
            try:
                text = await asyncio.to_thread(describer.describe, path, prompt.prompt, model_for(prompt), num_ctx)
            except Exception as error:
                print(f"Model failed for {path} [{prompt.key}]: {error}")
                all_ok = False
                continue
            await repo.delete_stale_for_prompt(image_id, prompt.key)
            repo.save(image_id, prompt.key, model_for(prompt), text)
        if all_ok:
            await session.flush()
            await delete_stale_rows(session, ImageDescription, [image_id])  # prompts that are no longer configured
            refreshed += 1
        else:
            failed += 1
        await session.commit()
    stats = refresh_stats(selected=len(ids), refreshed=refreshed, failed=failed)
    stats["images_remaining"] = len(ids) - refreshed
    return stats
```

Careful: `delete_stale_rows(..., [image_id])` after the loop would also delete stale rows of prompts whose replacement just got saved? No: replaced ones are no longer stale (new row inserted non-stale, old stale deleted by `delete_stale_for_prompt`). Remaining stale rows are retired prompts. Correct only when `all_ok`. The `filenames` lookup: `get_all_images(status)` returns a result of `(filename, id)` rows; iterate it (it is an iterable result), and an image deleted meanwhile would raise `KeyError`: use `filenames.get(image_id)` and skip if missing (count `skipped`).

`refresh_stats` has no `skipped`; leave skipped images counted in remaining via `selected - refreshed`; fine, drop the unused `skipped` variable or report nothing. Keep `skipped` only if used – remove it.

Wire: `_process(reset, limit, retry_failed, refresh_stale=False)`: when `refresh_stale`, after prompts and `OllamaImageDescriber()` are ready, open the session and return `await refresh_stale_descriptions(session, prompts, describer, base_path, num_ctx, lambda p: resolve_model(p, settings), limit)`; `main(..., refresh_stale=False)` passes it through (stats are already recorded there). CLI: `--refresh-stale`; `reject_conflicts(parser, args.refresh_stale, reset=args.reset, retry_failed=args.retry_failed)`; `--limit` stays allowed. Update the docstring.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_build_image_descriptions.py tests/integration/test_image_descriptions_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/build_image_descriptions.py repository/image_descriptions.py tests/integration/test_build_image_descriptions.py
git commit -m "feat(recut): build_image_descriptions --refresh-stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Description tags refresh

**Files:**
- Modify: `batch/build_tags_from_descriptions.py`
- Test: `tests/integration/test_build_tags_from_descriptions_refresh.py` (create)

**Interfaces:**
- Consumes: Task 1 (`ImageDescriptionsRepository.get_texts_for_images`, `source="Ollama"`).
- Produces: `async def refresh_stale_description_tags(session, engine) -> dict[str, int]`; `main(..., refresh_stale=False)`; CLI `--refresh-stale`.

- [ ] **Step 1: Write the failing tests**

Same shape as Task 3 (reuse `FakeEngine` idea: tags `("topic", "cats")` when the text contains "cat"), with `ImageDescription` rows instead of `OCRText`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_replaces_stale_ollama_tags_from_the_new_descriptions(db_session):
    # cut with a fresh description "a cat" and a stale Ollama tag "dogs" and a stale OCR tag
    # -> Ollama tags == [("topic","cats",False)], the OCR stale tag is untouched
    ...

@pytest.mark.asyncio(loop_scope="session")
async def test_skips_an_image_whose_descriptions_are_still_stale(db_session):
    # description stale=True + stale Ollama tag -> skipped_upstream_stale >= 1, tag unchanged
    ...

@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_descriptions_do_not_feed_the_tags(db_session):
    # two fresh descriptions, one rejected via ImageDescriptionFeedback(approved=False); only the kept one tags
    ...

@pytest.mark.asyncio(loop_scope="session")
async def test_an_engine_failure_keeps_every_stale_tag(db_session):
    ...
```

Write each out in full following Task 3's tests (same `_cut`, `_tags` helpers with `source="Ollama"`); the descriptions need `prompt_key` and `model_used` (unique per image + prompt).

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_build_tags_from_descriptions_refresh.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

In `batch/build_tags_from_descriptions.py`:

```python
async def refresh_stale_description_tags(session, engine) -> dict:
    """Replaces the stale Ollama tags of cut_proposed images with tags of their (refreshed) descriptions; tags are
    computed before anything is deleted."""
    selected = await images_with_stale_rows(session, ImageTag, ImageStatus.CUT_PROPOSED, source=_SOURCE)
    blocked = await stale_among(session, ImageDescription, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    tags_by_image = defaultdict(set)
    for image_id, text in await ImageDescriptionsRepository(session).get_texts_for_images(ready):
        tags_by_image[image_id].update(engine.tag(text, language=_DESCRIPTION_LANGUAGE).tags)
    try:
        await delete_stale_rows(session, ImageTag, ready, source=_SOURCE)
        for image_id in ready:
            for name, value in sorted(tags_by_image.get(image_id, ())):
                session.add(ImageTag(image_id=image_id, key=name, value=value, source=_SOURCE))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))
```

Imports as in Task 3 plus `ImageDescription`, `ImageDescriptionsRepository`. Wire `_process(incremental, refresh_stale=False)`, `main(..., refresh_stale=False)` (with `record_stats`), the CLI flag and `reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)`; update the docstring.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_build_tags_from_descriptions_refresh.py tests/integration/test_images_repository_description_tagging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/build_tags_from_descriptions.py tests/integration/test_build_tags_from_descriptions_refresh.py
git commit -m "feat(recut): build_tags_from_descriptions --refresh-stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Profiles for cuts

**Files:**
- Modify: `repository/meme_profile_inputs.py`, `batch/build_meme_profiles.py`
- Test: `tests/integration/test_build_meme_profiles.py` (extend; it already drives `_process` with a fake extractor, see its `_SessionFactory`, `_fake_extractor_class` and `_stub_settings`)

**Interfaces:**
- Produces: `MemeProfileInputsRepository.list_images(status: str = "active")` (`list_active_images` stays and delegates); `_process(reset, limit, retry_failed, only_ids=None, status="active")`; `main(..., status="active")`; CLI `--status {active,cut_proposed}` on `build_meme_profiles`.
- Behaviour with `status="cut_proposed"`: candidates are the `cut_proposed` images minus those with stale `ocr_texts` or `image_descriptions` (counted `skipped.upstream_stale`); `reset` with a non-active status raises `ValueError` before anything is deleted.

- [ ] **Step 1: Write the failing tests**

Follow the harness used by the existing `_process` tests in this file (patch `load_config`, `settings`, `AsyncSessionLocal` with `_SessionFactory(db_session)`, extractor with `_fake_extractor_class(calls)`). Add:

```python
async def test_cut_proposed_status_profiles_the_cut_and_leaves_active_images(...):
    # an active image and a cut_proposed image, both with a long fresh description;
    # _process(reset=False, limit=None, retry_failed=False, status="cut_proposed", only_ids={active.id, cut.id})
    # -> exactly the cut got a MemeProfile row
async def test_cut_with_stale_inputs_is_skipped_until_they_are_refreshed(...):
    # cut with a stale ImageDescription -> no extractor call for it, stats["skipped.upstream_stale"] >= 1
async def test_reset_is_refused_for_the_cut_status(...):
    # with pytest.raises(ValueError): await module._process(True, None, False, status="cut_proposed")
    # and an existing model-generated profile of an active image is still there
```

Use `only_ids` in the first two to keep the shared DB's other images out (the existing tests do the same – check them). Fill the bodies in following the neighbouring tests exactly.

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_build_meme_profiles.py -v -k "cut"`
Expected: FAIL (`status` unexpected keyword).

- [ ] **Step 3: Implement**

`repository/meme_profile_inputs.py`:

```python
    async def list_images(self, status: str = "active") -> list[tuple[uuid.UUID, str, str | None]]:
        result = await self.session.execute(
            select(Image.id, Image.filename, Image.content_hash)
            .where(Image.status == status)
            .order_by(Image.created_at, Image.id)
        )
        return [(image_id, filename, content_hash) for image_id, filename, content_hash in result.all()]

    async def list_active_images(self) -> list[tuple[uuid.UUID, str, str | None]]:
        return await self.list_images("active")
```

`batch/build_meme_profiles.py`: `_collect_candidates(session, config, only_ids=None, status="active")` calls `inputs_repo.list_images(status)`. In `_process`, add `status: str = "active"`; at the top (before `load_config`): `if reset and status != "active": raise ValueError("build_meme_profiles: --reset only applies to --status active")`. After `candidates = await _collect_candidates(...)`:

```python
        if status != "active":
            # A cut's OCR and descriptions are the source's stale copies until their refresh ran; profiling them would
            # extract from the wrong text, so wait for the refresh (R3c).
            ids = [candidate.image_id for candidate in candidates]
            blocked = (await stale_among(session, OCRText, ids)) | (await stale_among(session, ImageDescription, ids))
            candidates = [candidate for candidate in candidates if candidate.image_id not in blocked]
            metrics.add("skipped.upstream_stale", len(blocked))
```

Imports: `from Storage.models import ImageDescription, OCRText`, `from Storage.stale import stale_among`. `main(..., status="active")` passes through. CLI: `parser.add_argument("--status", choices=["active", "cut_proposed"], default="active", help="Images to profile (default: active). cut_proposed covers recut cuts after their OCR and descriptions were refreshed; not combinable with --reset")`, and `parser.error` if `args.reset and args.status != "active"`. Update the docstring.

`build_meme_profile_embeddings` needs no change (it embeds any profile without a vector and `_write` drops the embedding when the situation changes).

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_build_meme_profiles.py tests/integration/test_meme_profile_inputs_repository.py tests/integration/test_build_meme_profile_embeddings.py -v` and `pytest tests/docs/` (the `--status` guard: `{active, cut_proposed}` is a subset of the real statuses; if `tests/docs/test_image_status_registry.py` now demands `build_meme_profiles` be listed in `docs/image-status.md`, add it there in this task: a row in "Accepted by `--status`" saying it accepts `active` and `cut_proposed`).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repository/meme_profile_inputs.py batch/build_meme_profiles.py tests/integration/test_build_meme_profiles.py docs/image-status.md
git commit -m "feat(recut): build_meme_profiles --status cut_proposed

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The driver

**Files:**
- Create: `batch/refresh_recut_cuts.py`
- Modify: `environments/batch_registry.yaml` (CRLF)
- Test: `tests/integration/test_refresh_recut_cuts.py` (create)

**Interfaces:**
- Consumes: `batch.description_pipeline.Step`, `run_steps`, `OK`, `FAILED`, `SKIPPED_DEPENDENCY`, `SKIPPED_BUSY`; every job's `main`. Before writing the step list read the real signatures of `classify_text_heavy.main` and `build_ocr_text_embeddings.main` (R3b added `--status`; call them with `status="cut_proposed"`; if `main` takes a different parameter name use that).
- Produces: `refresh_recut_cuts.STEPS`, `main(trigger="manual", run_id=None)`; registry kind `refresh_recut_cuts`.

Steps (name: needs):
`ocr` (`extract_text_from_memes.main(trigger=t, refresh_stale=True)`); `classify` (`classify_text_heavy.main(trigger=t, status="cut_proposed")`: ocr); `ocr_text_embeddings` (`build_ocr_text_embeddings.main(... "cut_proposed")`: classify); `ocr_lemmas` (`build_ocr_lemmas.main(trigger=t, refresh_stale=True)`: ocr); `ocr_tags` (`build_tags_from_ocr.main(trigger=t, refresh_stale=True)`: ocr); `describe` (`build_image_descriptions.main(trigger=t, refresh_stale=True, limit=settings.get("image_descriptions.max_per_run"))`); `description_embeddings` (`build_image_description_embeddings.main(trigger=t)`: describe); `description_lemmas` (`build_description_lemmas.main(trigger=t)`: describe); `description_tags` (`build_tags_from_descriptions.main(trigger=t, refresh_stale=True)`: describe); `meme_profiles` (`build_meme_profiles.main(trigger=t, status="cut_proposed", limit=settings.get("meme_profiles.max_per_run"))`: ocr, describe); `meme_profile_embeddings` (`build_meme_profile_embeddings.main(trigger=t)`: meme_profiles).

- [ ] **Step 1: Write the failing tests**

```python
"""refresh_recut_cuts: step order, dependencies and run stats. The steps themselves are replaced by fakes."""
import pytest

from batch import refresh_recut_cuts as driver
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, SKIPPED_DEPENDENCY, Step, run_steps
from repository.batch_runs import BatchAlreadyRunningError


def test_steps_come_in_dependency_order_and_only_name_earlier_steps():
    seen = []
    for step in driver.STEPS:
        assert set(step.needs) <= set(seen), step.name
        seen.append(step.name)
    assert [s.name for s in driver.STEPS][0] == "ocr"


def test_the_expected_dependencies():
    needs = {step.name: set(step.needs) for step in driver.STEPS}
    assert needs["classify"] == {"ocr"} and needs["ocr_text_embeddings"] == {"classify"}
    assert needs["ocr_lemmas"] == needs["ocr_tags"] == {"ocr"}
    assert needs["description_embeddings"] == needs["description_lemmas"] == needs["description_tags"] == {"describe"}
    assert needs["meme_profiles"] == {"ocr", "describe"}
    assert needs["meme_profile_embeddings"] == {"meme_profiles"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_ocr_step_skips_its_dependants_but_not_the_description_chain(monkeypatch):
    ran = []

    def fake(name, fail=False, busy=False):
        async def run(trigger):
            ran.append(name)
            if fail:
                raise RuntimeError("boom")
            if busy:
                raise BatchAlreadyRunningError(name)
        return run

    steps = [Step(s.name, fake(s.name, fail=s.name == "ocr", busy=s.name == "describe"), s.needs) for s in driver.STEPS]
    results = await run_steps(steps, "manual")

    assert results["ocr"]["status"] == FAILED
    assert results["classify"]["status"] == SKIPPED_DEPENDENCY and results["ocr_lemmas"]["status"] == SKIPPED_DEPENDENCY
    assert results["describe"]["status"] == SKIPPED_BUSY
    assert results["description_embeddings"]["status"] == OK   # a busy step is not a failed one
    assert results["meme_profiles"]["status"] == SKIPPED_DEPENDENCY   # needs the failed ocr
```

(Check `BatchAlreadyRunningError`'s constructor and that `description_pipeline.run_steps` treats `SKIPPED_BUSY` as non-blocking: its `blocked` check only lists FAILED/SKIPPED_DEPENDENCY, as read. Add a test with `main` using a patched `STEPS` and `tracked_run` only if the existing description_pipeline tests show the pattern; otherwise cover `main` via the stats helper below.)

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_refresh_recut_cuts.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

`batch/refresh_recut_cuts.py`:

```python
"""Refresh driver for recut cuts (screenshot recut R3c): replaces the stale derived data a cut inherited from its source
(OCR, OCR lemmas and tags, descriptions and what hangs off them, meme profile) by running the refresh modes of the
group 3 jobs in dependency order. Manual-trigger only (via /admin/batches); not scheduled; when to run it is decided in R7.

Same model as description_pipeline: every child is called in process with run_id=None so it self-tracks under its own
kind, an already-running child is skipped, and a failed step only skips the steps that need it. Everything is incremental
(a refresh selects only what is still stale), so resuming after a failure is triggering again. See
docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3c-refresh-stale-design.md.
"""
```

then imports of the jobs, `STEPS` per the table above (lambdas like `description_pipeline.STEPS`), and:

```python
async def _run(run_id, trigger):
    results = await run_steps(STEPS, trigger)
    async with AsyncSessionLocal() as session:
        still_stale = await stale_counts(session)
    await record_stats(run_id, {"steps": results, "still_stale": still_stale})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"refresh_recut_cuts: steps failed: {', '.join(failed)}")
```
(`stale_counts` counts all statuses, which includes `active` rows; name the key `stale_rows_total` and say so in the docstring; the per-status split is R7's concern.) `main(trigger="manual", run_id=None)` exactly like `description_pipeline.main`, kind `refresh_recut_cuts`; CLI with `--env`.

Registry (CRLF): after the `description_pipeline` entry add

```yaml
refresh_recut_cuts:
  module: batch.refresh_recut_cuts
  kind: refresh_recut_cuts
```
using a Python read/replace/write script that preserves `\r\n`. Run `pytest batch/tests/` and `tests/integration/test_batch_registry*.py` (whatever guards the registry; `grep -rl batch_registry tests`) to confirm the registry loads.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_refresh_recut_cuts.py -v` then the registry guard tests found above.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/refresh_recut_cuts.py environments/batch_registry.yaml tests/integration/test_refresh_recut_cuts.py
git commit -m "feat(recut): refresh_recut_cuts driver

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Documentation, spec and tracker

**Files:**
- Modify: `docs/image-derived-data.md`, `docs/image-status.md`, `docs/batches/inventory.md`, `docs/batches/execution-model.md` (only if it lists drivers), `CLAUDE.md` (batch list), the R3c spec, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`
- Test: `tests/docs/` (existing guards)

- [ ] **Step 1: Update the docs**

- `docs/image-derived-data.md`: in the group 3 rows (`ocr_texts`, `ocr_lemmas`, `image_descriptions` and its children, `tags` OCR/Ollama, meme profile) name how the refresh happens (`--refresh-stale` on the job, or "normal run after the description refresh" for embeddings and lemmas of descriptions, or `build_meme_profiles --status cut_proposed`) and the OCR refresh's clearing of `image_classifications` and `ocr_text_embeddings`. Mark R3c implemented where the file tracks implementation per row.
- `docs/image-status.md`: the cut's life: `create_recut_cuts`, `build_image_embeddings --status cut_proposed --incremental`, `refresh_recut_cuts` (the OCR refresh now clears the classification rows, so the earlier "run before the refresh leaves stale rows" caveat is gone when the driver is used); `build_meme_profiles` listed among the `--status` jobs (done in Task 7).
- `docs/batches/inventory.md`: a row for `refresh_recut_cuts` (admin-triggerable, manual only, not scheduled) and the new flag on the five jobs plus `--status` on `build_meme_profiles`.
- `CLAUDE.md` batch list: add a `refresh_recut_cuts` entry after `create_recut_cuts` and a sentence on each changed job (`--refresh-stale`; profiles `--status`). Keep existing wording; additive edits only (the `meme-popularity` worktree edits this file too).
- Spec: set `Status: planned`, add `Plan: docs/superpowers/plans/2026-10-26-r3c-refresh-stale.md`, and replace the sections the "Deviations" block overrides: section 2 (flag list, `--status` ignored, conflicts), section 3 table (descriptions' embeddings/lemmas via normal run, profiles via `--status`), section 5 (descriptions record no failures, the 20-unreadable-files stop applies to OCR only), plus a sentence for deviation 3 (own-stale-rows selection) and 5 (feedback lost). Add `Follow-ups: none` is not needed.
- Tracker row R3c: status `planned`.

- [ ] **Step 2: Run the docs guards**

Run: `pytest tests/docs/ -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs CLAUDE.md
git commit -m "docs(recut): R3c refresh stale

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Whole-root verification

- [ ] **Step 1: Run every root, separately**

```
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
pytest tests/docs/ -v
pytest batch/tests/ -v
pytest tests/rules/ -v
```
Expected: all PASS. Any failure, including one you did not cause, is reported by name; shared selection code (`ImagesRepository`, `OCRTextRepository`, `BatchCommitter`) is touched, so the whole integration root is the gate.

- [ ] **Step 2: No commit** (nothing changes). Hand over to the final whole-branch review.
