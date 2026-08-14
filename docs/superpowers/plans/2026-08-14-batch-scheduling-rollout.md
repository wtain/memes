# Batch Scheduling Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the ingestion pipeline's automatable prep stages and 7 downstream enrichment
scripts onto the existing embedded backend scheduler, so an operator no longer has to manually
run each `python -m batch.<script>` command by hand — while keeping Tier A/B review and
promotion explicitly human-gated.

**Architecture:** A new driver script (`batch/ingest_auto_prep.py`) chains the ingestion prep
steps in-process under a new, scheduler-only `BatchRun.kind`. Seven existing downstream scripts
are refactored to the self-tracking `main(trigger, run_id)` contract already established by
`batch/trends_batch.py` and `batch/move_flagged.py`. All 8 get registry entries and
`scheduler.jobs` config entries. One pre-existing bug (`ingest_find_duplicates.py` can rewind a
review run's `stage` backward) is fixed first, since the new driver would hit it every tick.

**Tech Stack:** Python 3.11, `asyncio`, existing `batch/run_tracking.py` helpers
(`tracked_run`/`finish_existing_run`), existing `repository/batch_runs.py`
(`BatchRunRepository`), pytest + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-08-14-batch-scheduling-rollout-design.md`

## Global Constraints

- No new third-party dependency.
- Never modify the internals of `ingest_hash_dedup.py`, `build_image_embeddings.py`, or
  `extract_text_from_memes.py` — `ingest_auto_prep.py` calls their existing `main()` functions
  unchanged.
- Every refactored script's direct-CLI (`python -m batch.<script> --env ... --incremental`)
  behavior must be preserved exactly — only the internal wiring changes (body moves into a
  `_process()` helper), not the CLI surface.
- Scheduled/admin-triggered runs of the 3 scripts with an `--incremental` flag
  (`build_tags_from_ocr`, `build_ocr_lemmas`, `build_tags_from_descriptions`) must always run
  incremental (`incremental=True`), never a full rebuild — a timer must never silently trigger
  a full reprocess.
- `kind` value for each new/refactored job equals its script/registry name exactly (no
  abbreviation) — matches the `move_flagged`/`unregister_deleted_images` registry precedent, not
  the shorter `trends_batch`→`trends` one.
- Per the "Running the right test scope" rule in `CLAUDE.md`: never combine `batch/tests/`,
  `tests/integration/`, and other test roots in one `pytest` invocation — run each root
  separately. `batch/tests/` tests must not require a live DB (mock `AsyncSessionLocal`/
  `BatchRunRepository`, matching `batch/tests/test_move_flagged.py`'s style); `tests/integration/`
  tests use the real `ocrdb_test` DB via the `db_session` fixture.

---

### Task 1: Fix `ingest_find_duplicates.py`'s stage-rewind bug

**Files:**
- Modify: `batch/ingest_find_duplicates.py`
- Test: `tests/integration/test_ingest_find_duplicates.py`

**Interfaces:**
- Produces: `should_advance_stage(current_stage: str | None, target_stage: str) -> bool` in
  `batch/ingest_find_duplicates.py` — pure predicate, no DB. Consumed by this file's own `main()`
  only (not exported for use elsewhere).

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_ingest_find_duplicates.py` (top-level functions, alongside the
existing `test_*` functions — no DB needed for these two, matching
`test_ingest_validate_formats.py`'s `test_should_advance_stage_only_from_hash_dedup` precedent):

```python
from batch.ingest_find_duplicates import find_batch_duplicates, should_advance_stage


def test_should_advance_stage_tier_a_only_before_tier_a_review():
    """A re-run of --tier tier_a (operator re-joins a batch mid-review per the runbook's
    Concurrency section, or the scheduled ingest_auto_prep driver re-running every tick) must
    not stomp a later stage like tier_b_review back to tier_a_review."""
    assert should_advance_stage("hash_dedup", "tier_a_review") is True
    assert should_advance_stage("format_validation", "tier_a_review") is True
    assert should_advance_stage("tier_a_review", "tier_a_review") is False
    assert should_advance_stage("tier_b_review", "tier_a_review") is False
    assert should_advance_stage(None, "tier_a_review") is False


def test_should_advance_stage_tier_b_from_anything_before_it():
    assert should_advance_stage("hash_dedup", "tier_b_review") is True
    assert should_advance_stage("format_validation", "tier_b_review") is True
    assert should_advance_stage("tier_a_review", "tier_b_review") is True
    assert should_advance_stage("tier_b_review", "tier_b_review") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_find_duplicates.py -v`
Expected: the two new tests FAIL with `ImportError: cannot import name 'should_advance_stage'`;
all 4 pre-existing tests in the file still PASS (or also fail to collect due to the same import
error — either way, this confirms the function doesn't exist yet).

- [ ] **Step 3: Implement `should_advance_stage` and use it in `main()`**

In `batch/ingest_find_duplicates.py`, add right after the existing `TIER_STAGE` line:

```python
TIER_STAGE = {"tier_a": "tier_a_review", "tier_b": "tier_b_review"}

_STAGE_ORDER = ["hash_dedup", "format_validation", "tier_a_review", "tier_b_review"]


def should_advance_stage(current_stage: str | None, target_stage: str) -> bool:
    """The stage must only ever advance, never rewind -- mirrors
    ingest_validate_formats.should_advance_stage's reasoning, generalized to two arbitrary
    stages instead of one fixed pair. A re-run of an earlier tier must not stomp a later
    stage back, which would make the frontend's tierForStage() drop the review queue until
    the later tier's find-duplicates call re-runs."""
    if current_stage not in _STAGE_ORDER:
        return False
    return _STAGE_ORDER.index(target_stage) > _STAGE_ORDER.index(current_stage)
```

Then change `main()`'s body (currently unconditional `set_stage`):

```python
        inserted = await find_batch_duplicates(session, active_run.run_id, k=resolved_k, threshold=threshold)
        target_stage = TIER_STAGE[tier]
        if should_advance_stage(active_run.stage, target_stage):
            await runs_repo.set_stage(active_run.run_id, target_stage)
        await session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_find_duplicates.py -v`
Expected: all PASS (the 2 new tests plus the 4 pre-existing ones).

- [ ] **Step 5: Run the full integration root to check for regressions**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all PASS. (Full root, not just this file — `ingest_find_duplicates.py`'s stage handling
is exercised indirectly by other ingestion tests too.)

- [ ] **Step 6: Commit**

```bash
git add batch/ingest_find_duplicates.py tests/integration/test_ingest_find_duplicates.py
git commit -m "fix: prevent ingest_find_duplicates from rewinding an ingestion run's stage"
```

---

### Task 2: Self-track `build_tags_from_ocr.py`

**Files:**
- Modify: `batch/build_tags_from_ocr.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_build_tags_from_ocr.py` (new)

**Interfaces:**
- Consumes: `batch.run_tracking.tracked_run(kind, trigger)`, `finish_existing_run(run_id)`
  (existing, unchanged).
- Produces: `build_tags_from_ocr.main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None`,
  self-tracked under `kind="build_tags_from_ocr"`. Registered in `batch_registry.yaml` as
  `build_tags_from_ocr: {module: batch.build_tags_from_ocr, kind: build_tags_from_ocr}`.
  Consumed by Task 10's scheduler config and already-generic `batch/run_wrapper.py` (no changes
  needed there).

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_build_tags_from_ocr.py`:

```python
"""
Unit tests for batch/build_tags_from_ocr.py's main() self-tracking contract
(tracked_run/finish_existing_run wrapping), matching batch/tests/test_move_flagged.py's
TestMain style. No real DB -- run tracking and _process are both mocked.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_ocr import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_ocr as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_ocr", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_ocr as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_explicit_incremental_false_is_respected(self):
        """Direct CLI use (python -m batch.build_tags_from_ocr, no --incremental) must still
        be able to force a full rebuild -- only the scheduler/admin default is True."""
        process_mock = AsyncMock()
        import batch.build_tags_from_ocr as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", incremental=False)

        process_mock.assert_awaited_once_with(incremental=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd batch && pytest tests/test_build_tags_from_ocr.py -v` (or `pytest batch/tests/test_build_tags_from_ocr.py -v` from repo root)
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'` (current
signature is `main(incremental: bool)`).

- [ ] **Step 3: Implement**

In `batch/build_tags_from_ocr.py`, change the imports:

```python
import argparse
import asyncio
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver
```

Rename `async def main(incremental: bool):` to `async def _process(incremental: bool) -> None:`
(body unchanged), then add below it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_ocr", trigger=trigger):
            await _process(incremental=incremental)
```

Change the `__main__` block's last line:

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no OCR tags yet (default: clear all and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental))  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_build_tags_from_ocr.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
build_tags_from_ocr:
  module: batch.build_tags_from_ocr
  kind: build_tags_from_ocr
```

- [ ] **Step 6: Run the full `batch/tests/` root to check for regressions**

Run: `pytest batch/tests/ -v`
Expected: all PASS, including the new file.

- [ ] **Step 7: Commit**

```bash
git add batch/build_tags_from_ocr.py batch/tests/test_build_tags_from_ocr.py environments/batch_registry.yaml
git commit -m "feat: self-track build_tags_from_ocr for scheduling/admin triggering"
```

---

### Task 3: Self-track `build_ocr_lemmas.py`

**Files:**
- Modify: `batch/build_ocr_lemmas.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_build_ocr_lemmas_main.py` (new — named distinctly from the existing
  `tests/integration/test_build_ocr_lemmas.py`, which tests the lower-level `run()` function
  and is unaffected by this change)

**Interfaces:**
- Produces: `build_ocr_lemmas.main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None`,
  self-tracked under `kind="build_ocr_lemmas"`. The existing `run(session, incremental,
  ocr_confidence_min, ocr_lang_score_min, min_word_length, morph, metrics)` function (used by
  `tests/integration/test_build_ocr_lemmas.py`) is untouched.

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_build_ocr_lemmas_main.py`:

```python
"""
Unit tests for batch/build_ocr_lemmas.py's main() self-tracking contract. No real DB --
mirrors batch/tests/test_build_tags_from_ocr.py's style. Does not touch the lower-level
run() function, which tests/integration/test_build_ocr_lemmas.py already covers against a
real DB.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_ocr_lemmas import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_ocr_lemmas as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_ocr_lemmas", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_ocr_lemmas as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_build_ocr_lemmas_main.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/build_ocr_lemmas.py`, change the imports:

```python
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.ocr_lemmas import group_lemmas_by_image
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.db import AsyncSessionLocal
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository, OCR_LEMMAS_PIPELINE
from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver
```

Leave the existing `async def run(session, incremental, ...)` function exactly as-is. Rename
`async def main(incremental: bool):` to `async def _process(incremental: bool) -> None:` (body
unchanged — it still calls `run(...)` internally), then add below it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_ocr_lemmas", trigger=trigger):
            await _process(incremental=incremental)
```

Change the `__main__` block's last line:

```python
    asyncio.run(main(incremental=args.incremental))  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_build_ocr_lemmas_main.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
build_ocr_lemmas:
  module: batch.build_ocr_lemmas
  kind: build_ocr_lemmas
```

- [ ] **Step 6: Run the full `batch/tests/` root, then the full `tests/integration/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all PASS, including `test_build_ocr_lemmas.py` unchanged (it imports `run` directly,
not `main`, so it's unaffected by this refactor — this run confirms that).

- [ ] **Step 7: Commit**

```bash
git add batch/build_ocr_lemmas.py batch/tests/test_build_ocr_lemmas_main.py environments/batch_registry.yaml
git commit -m "feat: self-track build_ocr_lemmas for scheduling/admin triggering"
```

---

### Task 4: Self-track `build_tags_from_descriptions.py`

**Files:**
- Modify: `batch/build_tags_from_descriptions.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_build_tags_from_descriptions.py` (new)

**Interfaces:**
- Produces: `build_tags_from_descriptions.main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None`,
  self-tracked under `kind="build_tags_from_descriptions"`.

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_build_tags_from_descriptions.py` (identical structure to Task 2's test,
substituting the module):

```python
"""
Unit tests for batch/build_tags_from_descriptions.py's main() self-tracking contract. No
real DB -- mirrors batch/tests/test_build_tags_from_ocr.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_descriptions import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_descriptions", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_build_tags_from_descriptions.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/build_tags_from_descriptions.py`, change the imports:

```python
import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver
from rules.engine import RulesEngine
```

Rename `async def main(incremental: bool):` to `async def _process(incremental: bool) -> None:`
(body unchanged), then add below it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_descriptions", trigger=trigger):
            await _process(incremental=incremental)
```

Change the `__main__` block's last line:

```python
    asyncio.run(main(incremental=args.incremental))  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_build_tags_from_descriptions.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
build_tags_from_descriptions:
  module: batch.build_tags_from_descriptions
  kind: build_tags_from_descriptions
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add batch/build_tags_from_descriptions.py batch/tests/test_build_tags_from_descriptions.py environments/batch_registry.yaml
git commit -m "feat: self-track build_tags_from_descriptions for scheduling/admin triggering"
```

---

### Task 5: Self-track `build_concept_embeddings.py`

**Files:**
- Modify: `batch/build_concept_embeddings.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_build_concept_embeddings_main.py` (new)

**Interfaces:**
- Produces: `build_concept_embeddings.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`,
  self-tracked under `kind="build_concept_embeddings"`. No `incremental` parameter — this
  script's `main()` takes none today (it always rebuilds all concepts from the configured
  files/images directory; unchanged behavior).

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_build_concept_embeddings_main.py`:

```python
"""
Unit tests for batch/build_concept_embeddings.py's main() self-tracking contract. No real
DB/model loading -- _process is mocked entirely.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_concept_embeddings import main


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
        import batch.build_concept_embeddings as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_concept_embeddings", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_build_concept_embeddings_main.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/build_concept_embeddings.py`, add to the imports:

```python
import uuid
...
from batch.run_tracking import finish_existing_run, tracked_run
```

(alongside the existing `import argparse`, `import asyncio`, etc. at the top).

Rename `async def main():` to `async def _process() -> None:` (body unchanged), then add below
it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_concept_embeddings", trigger=trigger):
            await _process()
```

Change the `__main__` block's last line:

```python
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_build_concept_embeddings_main.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
build_concept_embeddings:
  module: batch.build_concept_embeddings
  kind: build_concept_embeddings
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add batch/build_concept_embeddings.py batch/tests/test_build_concept_embeddings_main.py environments/batch_registry.yaml
git commit -m "feat: self-track build_concept_embeddings for scheduling/admin triggering"
```

---

### Task 6: Self-track `detect_entities_and_tag.py`

**Files:**
- Modify: `batch/detect_entities_and_tag.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_detect_entities_and_tag.py` (new)

**Interfaces:**
- Produces: `detect_entities_and_tag.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`,
  self-tracked under `kind="detect_entities_and_tag"`. No `incremental` parameter (script has
  none today).

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_detect_entities_and_tag.py` (same structure as Task 5's test):

```python
"""
Unit tests for batch/detect_entities_and_tag.py's main() self-tracking contract. No real
DB/model loading -- _process is mocked entirely.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.detect_entities_and_tag import main


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
        import batch.detect_entities_and_tag as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="detect_entities_and_tag", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.detect_entities_and_tag as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_detect_entities_and_tag.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/detect_entities_and_tag.py`, change the imports:

```python
import argparse
import asyncio
import os
import uuid

from ai.yolo import YoloAnimalDetector
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.image_format_filter import has_unsupported_image_extension
from config.settings import load_env
from Storage.db import AsyncSessionLocal

from repository.images import ImagesRepository
from repository.tags import TagsRepository, TagsSaver
```

Rename `async def main():` to `async def _process() -> None:` (body unchanged), then add below
it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="detect_entities_and_tag", trigger=trigger):
            await _process()
```

Change the `__main__` block's last line:

```python
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_detect_entities_and_tag.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
detect_entities_and_tag:
  module: batch.detect_entities_and_tag
  kind: detect_entities_and_tag
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add batch/detect_entities_and_tag.py batch/tests/test_detect_entities_and_tag.py environments/batch_registry.yaml
git commit -m "feat: self-track detect_entities_and_tag for scheduling/admin triggering"
```

---

### Task 7: Self-track `tag_images_from_concepts.py`

**Files:**
- Modify: `batch/tag_images_from_concepts.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_tag_images_from_concepts.py` (new)

**Interfaces:**
- Produces: `tag_images_from_concepts.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`,
  self-tracked under `kind="tag_images_from_concepts"`. No `incremental` parameter (script has
  none today).

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_tag_images_from_concepts.py` (same structure as Task 5's test):

```python
"""
Unit tests for batch/tag_images_from_concepts.py's main() self-tracking contract. No real
DB -- _process is mocked entirely.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.tag_images_from_concepts import main


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
        import batch.tag_images_from_concepts as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="tag_images_from_concepts", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.tag_images_from_concepts as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_tag_images_from_concepts.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/tag_images_from_concepts.py`, change the imports:

```python
import argparse
import asyncio
import json
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.concepts import ConceptsRepository
from repository.tags import TagsRepository, TagsSaver
```

Rename `async def main():` to `async def _process() -> None:` (body unchanged), then add below
it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="tag_images_from_concepts", trigger=trigger):
            await _process()
```

Change the `__main__` block's last line:

```python
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_tag_images_from_concepts.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
tag_images_from_concepts:
  module: batch.tag_images_from_concepts
  kind: tag_images_from_concepts
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add batch/tag_images_from_concepts.py batch/tests/test_tag_images_from_concepts.py environments/batch_registry.yaml
git commit -m "feat: self-track tag_images_from_concepts for scheduling/admin triggering"
```

---

### Task 8: Self-track `build_bow.py`

**Files:**
- Modify: `batch/build_bow.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_build_bow_main.py` (new — named distinctly from the existing
  `batch/tests/test_build_bow_vocab.py`, which tests the vocab-set helper functions and is
  unaffected by this change)

**Interfaces:**
- Produces: `build_bow.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`,
  self-tracked under `kind="build_bow"`. No `incremental` parameter (script has none today — it
  always rebuilds the full vocabulary file).

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_build_bow_main.py`:

```python
"""
Unit tests for batch/build_bow.py's main() self-tracking contract. No real DB -- _process is
mocked entirely. Does not touch the vocab-set helper functions, which
batch/tests/test_build_bow_vocab.py already covers.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_bow import main


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
        import batch.build_bow as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_bow", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_bow as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_build_bow_main.py -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'trigger'`.

- [ ] **Step 3: Implement**

In `batch/build_bow.py`, change the imports:

```python
import argparse
import asyncio
import json
import os
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import lemmatize_word, lemmatize_word_autodetect, make_morph, tokenize
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository
from repository.image_descriptions import ImageDescriptionsRepository
```

Rename `async def main():` to `async def _process() -> None:` (body unchanged — all the
existing helper functions like `_build_ocr_bow`/`_build_descriptions_bow` stay exactly as they
are), then add below it:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_bow", trigger=trigger):
            await _process()
```

Change the `__main__` block's last line:

```python
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_build_bow_main.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
build_bow:
  module: batch.build_bow
  kind: build_bow
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS, including the pre-existing `test_build_bow_vocab.py` unchanged (it imports
the vocab helper functions directly, not `main`).

- [ ] **Step 7: Commit**

```bash
git add batch/build_bow.py batch/tests/test_build_bow_main.py environments/batch_registry.yaml
git commit -m "feat: self-track build_bow for scheduling/admin triggering"
```

---

### Task 9: `batch/ingest_auto_prep.py` — the ingestion prep driver

**Files:**
- Create: `batch/ingest_auto_prep.py`
- Modify: `environments/batch_registry.yaml`
- Test: `batch/tests/test_ingest_auto_prep.py` (new)

**Interfaces:**
- Consumes: `ingest_hash_dedup.main(env: str | None) -> None`,
  `ingest_validate_formats.main(env: str | None) -> None`,
  `build_image_embeddings.main(incremental: bool, target_status: str = "active") -> None`,
  `extract_text_from_memes.main(path: str, target_status: str = "active") -> None`,
  `ingest_find_duplicates.main(env: str | None, tier: str, k: int | None) -> None` — all
  existing, unchanged, called with `env=None` so each falls back to the already-loaded
  `APP_ENV` (this driver's own `main()` is itself invoked via `run_wrapper.py`, which already
  calls `load_env(args.env)` before invoking any registered script's `main()`).
- Produces: `ingest_auto_prep.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`,
  self-tracked under `kind="ingestion_auto_prep"` — deliberately distinct from `kind="ingestion"`
  (see spec section 1 for why). Registered in `batch_registry.yaml` as
  `ingest_auto_prep: {module: batch.ingest_auto_prep, kind: ingestion_auto_prep}`.

- [ ] **Step 1: Write the failing test**

Create `batch/tests/test_ingest_auto_prep.py`:

```python
"""
Unit tests for batch/ingest_auto_prep.py -- the ingestion prep chain driver. No real DB; all
5 chained steps' main() functions are mocked, matching batch/tests/test_move_flagged.py's
chaining-test style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.ingest_auto_prep import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _patched_steps(module, **overrides):
    """Returns a dict of the 5 step mocks, pre-wired as no-op AsyncMocks unless overridden."""
    steps = {
        "ingest_hash_dedup": AsyncMock(),
        "ingest_validate_formats": AsyncMock(),
        "build_image_embeddings": AsyncMock(),
        "extract_text_from_memes": AsyncMock(),
        "ingest_find_duplicates": AsyncMock(),
    }
    steps.update(overrides)
    for name, mock in steps.items():
        getattr(module, name).main = mock
    return steps


class TestRunPrepChain:
    @pytest.mark.asyncio
    async def test_calls_all_five_steps_in_order_with_expected_args(self):
        import batch.ingest_auto_prep as module

        call_order = []

        steps = _patched_steps(module)
        for name, mock in steps.items():
            mock.side_effect = lambda *a, name=name, **kw: call_order.append(name)

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            await module._run_prep_chain()

        assert call_order == [
            "ingest_hash_dedup", "ingest_validate_formats", "build_image_embeddings",
            "extract_text_from_memes", "ingest_find_duplicates",
        ]
        steps["ingest_hash_dedup"].assert_awaited_once_with(env=None)
        steps["ingest_validate_formats"].assert_awaited_once_with(env=None)
        steps["build_image_embeddings"].assert_awaited_once_with(incremental=True, target_status="pending")
        steps["extract_text_from_memes"].assert_awaited_once_with("/fake/base", target_status="pending")
        steps["ingest_find_duplicates"].assert_awaited_once_with(env=None, tier="tier_a", k=None)

    @pytest.mark.asyncio
    async def test_runtime_error_from_a_later_step_is_swallowed(self):
        """The common case: the inbox is empty and no ingestion run is active, so steps 2-5
        raise 'No ingestion run is currently in progress' -- that must not fail the tick."""
        import batch.ingest_auto_prep as module

        steps = _patched_steps(
            module,
            ingest_validate_formats=AsyncMock(side_effect=RuntimeError("No ingestion run is currently in progress")),
        )

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            await module._run_prep_chain()  # must not raise

        steps["ingest_hash_dedup"].assert_awaited_once()
        steps["build_image_embeddings"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_error_from_hash_dedup_propagates(self):
        """Step 1 failing (e.g. PATH_INGESTION_SOURCE misconfigured) must fail the whole tick,
        not be swallowed like steps 2-5's expected 'nothing to do' error."""
        import batch.ingest_auto_prep as module

        steps = _patched_steps(
            module,
            ingest_hash_dedup=AsyncMock(side_effect=RuntimeError("PATH_INGESTION_SOURCE is required but not set")),
        )

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            with pytest.raises(RuntimeError, match="PATH_INGESTION_SOURCE"):
                await module._run_prep_chain()

        steps["ingest_validate_formats"].assert_not_awaited()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path(self):
        import batch.ingest_auto_prep as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_run_prep_chain", AsyncMock()) as chain_mock:
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="ingestion_auto_prep", trigger="scheduled")
        chain_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        import batch.ingest_auto_prep as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_run_prep_chain", AsyncMock()) as chain_mock:
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        chain_mock.assert_awaited_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest batch/tests/test_ingest_auto_prep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.ingest_auto_prep'`.

- [ ] **Step 3: Implement**

Create `batch/ingest_auto_prep.py`:

```python
"""
Ingestion prep driver: automates the ingestion pipeline's fully-automatable prep stages --
hash dedup through Tier A duplicate-finding -- so an operator no longer has to run 5 commands
by hand just to get newly-dropped inbox files into the Tier A review queue. Tier B
duplicate-finding and promotion stay manual; both depend on a human having finished the prior
tier's review, so auto-running them risks promoting images (or advancing the review queue) out
from under a reviewer. See
docs/superpowers/specs/2026-08-14-batch-scheduling-rollout-design.md.

Self-tracked under kind="ingestion_auto_prep", deliberately separate from the long-lived
kind="ingestion" row each real ingestion batch uses. That row can legitimately stay "started"
for days while a human works through Tier A/B review -- which is not orphaned, but the
scheduler's own orphan-recovery guard (max_runtime_minutes) would force-fail it if this
driver's own scheduler-tick bookkeeping shared that kind. This driver's kind only tracks "did a
tick run and did it fail" -- the real stats (files moved, embeddings created, candidates found)
already land in the kind="ingestion" run's own stats via each chained step's own tracking.

Note: ingest_hash_dedup.py's resolve_batch() always creates or joins a kind="ingestion" run,
even when the inbox is empty -- so the very first scheduled tick (in an environment with no
prior ingestion activity) creates an empty run that immediately advances through every stage
this driver touches (nothing to embed/OCR/search, so each step is a fast no-op) and then sits
open indefinitely at stage="tier_a_review" with zero pending images, until either real files are
later dropped (which correctly join it) or an operator manually runs ingest_promote.py (which
would immediately complete it, seeing zero pending). This is harmless -- the /ingestion UI just
shows an empty Tier A queue -- not a bug to work around here.
"""
import argparse
import asyncio
import uuid

from batch import (
    build_image_embeddings, extract_text_from_memes, ingest_find_duplicates,
    ingest_hash_dedup, ingest_validate_formats,
)
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings

_NOTHING_PENDING_STEPS = (
    "ingest_validate_formats", "build_image_embeddings", "extract_text_from_memes",
    "ingest_find_duplicates",
)


async def _run_prep_chain() -> None:
    await ingest_hash_dedup.main(env=None)

    steps = [
        ("ingest_validate_formats", lambda: ingest_validate_formats.main(env=None)),
        ("build_image_embeddings", lambda: build_image_embeddings.main(incremental=True, target_status="pending")),
        ("extract_text_from_memes", lambda: extract_text_from_memes.main(settings.BASE_PATH, target_status="pending")),
        ("ingest_find_duplicates", lambda: ingest_find_duplicates.main(env=None, tier="tier_a", k=None)),
    ]
    for name, step in steps:
        try:
            await step()
        except RuntimeError as e:
            print(f"ingest_auto_prep: nothing to do this tick, stopping at {name} ({e})")
            return


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run_prep_chain()
    else:
        async with tracked_run(kind="ingestion_auto_prep", trigger=trigger):
            await _run_prep_chain()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

Note on the test's `steps` dict and `getattr(module, name).main = mock` pattern: because
`ingest_auto_prep.py` imports the step modules themselves (`from batch import
ingest_hash_dedup, ...`) rather than their `main` functions directly, patching
`module.ingest_hash_dedup.main` (i.e., reaching through the imported module object) is what
the test's `_patched_steps` helper does — this matches how `batch/move_flagged.py`'s existing
test patches `mock_unregister.main` after `patch.object(module, "unregister_deleted_images")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest batch/tests/test_ingest_auto_prep.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the registry entry**

In `environments/batch_registry.yaml`, add:

```yaml
ingest_auto_prep:
  module: batch.ingest_auto_prep
  kind: ingestion_auto_prep
```

- [ ] **Step 6: Run the full `batch/tests/` root**

Run: `pytest batch/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add batch/ingest_auto_prep.py batch/tests/test_ingest_auto_prep.py environments/batch_registry.yaml
git commit -m "feat: add ingest_auto_prep driver for scheduled ingestion prep stages"
```

---

### Task 10: Scheduler config and manual verification

**Files:**
- Modify: `environments/settings.yaml`

**Interfaces:**
- Consumes: all 8 `kind` values established in Tasks 1-9 (`ingestion_auto_prep`,
  `build_tags_from_ocr`, `build_ocr_lemmas`, `build_tags_from_descriptions`,
  `build_concept_embeddings`, `detect_entities_and_tag`, `tag_images_from_concepts`,
  `build_bow`) and all 8 registered script names in `batch_registry.yaml`
  (`ingest_auto_prep` + the 7 downstream script names). No code changes needed —
  `Backend/app/scheduler.py`'s `_load_job_configs` already generically reads
  `scheduler.jobs` entries of arbitrary length.

- [ ] **Step 1: Add the 8 new job entries**

In `environments/settings.yaml`, extend the existing `scheduler.jobs` list (currently just
`trends_batch`) to:

```yaml
scheduler:
  enabled: false
  jobs:
    - name: trends_batch
      script: trends_batch
      batch_run_kind: trends
      interval_minutes: 360
      max_runtime_minutes: 60
      enabled: true
    - name: ingest_auto_prep
      script: ingest_auto_prep
      batch_run_kind: ingestion_auto_prep
      interval_minutes: 15
      max_runtime_minutes: 30
      enabled: true
    - name: build_tags_from_ocr
      script: build_tags_from_ocr
      batch_run_kind: build_tags_from_ocr
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_ocr_lemmas
      script: build_ocr_lemmas
      batch_run_kind: build_ocr_lemmas
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_tags_from_descriptions
      script: build_tags_from_descriptions
      batch_run_kind: build_tags_from_descriptions
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_concept_embeddings
      script: build_concept_embeddings
      batch_run_kind: build_concept_embeddings
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: detect_entities_and_tag
      script: detect_entities_and_tag
      batch_run_kind: detect_entities_and_tag
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: tag_images_from_concepts
      script: tag_images_from_concepts
      batch_run_kind: tag_images_from_concepts
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: build_bow
      script: build_bow
      batch_run_kind: build_bow
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
```

(Only the `jobs` list changes — the top-level `enabled: false` common default and each
environment's own `settings.<env>.yaml` override to `enabled: true` are unchanged; see the
comment already above this block in the file explaining why the common default stays `false`.)

- [ ] **Step 2: Run the Backend scheduler test suite to confirm no regressions**

Run: `cd Backend && pytest tests/test_scheduler.py -v`
Expected: all PASS unchanged — `_load_job_configs` is config-shape-generic, so 9 job entries
instead of 1 exercises the same code path already tested.

- [ ] **Step 3: Run the full Backend test suite**

Run: `cd Backend && pytest`
Expected: all PASS.

- [ ] **Step 4: Manual verification — `ingest_auto_prep` drains the inbox automatically**

1. In `environments/settings.yaml`, temporarily set `ingest_auto_prep`'s `interval_minutes: 1`
   and `max_runtime_minutes: 1` (revert after this check).
2. Start metal's backend: `set WATCHFILES_FORCE_POLLING=1` then
   `uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0`.
3. Drop one real test image into metal's configured inbox
   (`<BASE_PATH>\inbox\` — see `docs/runbooks/ingestion-pipeline.md`'s "Where do new images
   go?" table).
4. Within ~1 minute, confirm (without running any manual `python -m batch...` command):
   - The image reaches `pending` status with an embedding and OCR text.
   - It appears in the Tier A review queue at `/ingestion` (metal's frontend, `pnpm dev`).
   - `logs/metal/ingest_auto_prep_<timestamp>.log` was created with output from all 5 chained
     steps.
5. Revert `interval_minutes`/`max_runtime_minutes` back to `15`/`30`.

- [ ] **Step 5: Manual verification — the driver doesn't interfere with an open Tier B review**

1. Manually create an ingestion run and advance it to `tier_b_review` (drop a couple of test
   images, run the runbook's Stage 1-3 commands by hand, then `ingest_find_duplicates.py --tier
   tier_a` and `--tier tier_b`).
2. With `ingest_auto_prep`'s `interval_minutes` still temporarily at `1` (from Step 4), let a
   few ticks pass.
3. Confirm via `SELECT run_id, kind, stage FROM batch_runs WHERE kind = 'ingestion' ORDER BY
   created_at DESC LIMIT 1;` that `stage` stays `tier_b_review` — it must **not** revert to
   `tier_a_review`. This is the Task 1 fix's real-world confirmation.
4. Confirm a separate `kind = 'ingestion_auto_prep'` row is being created/updated on schedule,
   independent of the `kind = 'ingestion'` row's long lifetime.
5. Revert `interval_minutes`/`max_runtime_minutes` back to `15`/`30` if not already done.

- [ ] **Step 6: Commit**

```bash
git add environments/settings.yaml
git commit -m "feat: schedule ingestion prep and downstream enrichment batch jobs"
```

---

## Self-Review Notes

- **Spec coverage:** driver script + separate kind (Task 9), pre-existing stage-rewind bug fix
  (Task 1), 7 downstream scripts' self-tracking refactor (Tasks 2-8), registry entries (folded
  into each task), scheduler config with the spec's exact interval/max-runtime table (Task 10) —
  all covered. The spec's "Out of scope" items (Tier B/promote automation, `build_image_
  descriptions`, concept-discovery drafting tools) have no corresponding task, correctly.
- **Type/signature consistency:** every refactored script's `main()` signature
  (`trigger: str = "manual", run_id: uuid.UUID | None = None[, incremental: bool = True]`)
  matches across its own task's test and implementation, and matches the existing
  `trends_batch.py`/`move_flagged.py` precedent exactly. `_process()`'s parameter shape (with
  or without `incremental`) matches what each script's pre-existing body actually needs — the 3
  scripts that had `incremental: bool` keep it on `_process`; the 4 that had no parameters keep
  `_process()` parameterless.
- **Naming collision avoided:** `build_ocr_lemmas.py` already has a lower-level `run(session,
  incremental, ...)` function (used by an existing integration test) — Task 3 uses `_process`
  for the new top-level wrapper specifically to avoid shadowing or renaming that existing
  function.
- **Test-file naming collisions avoided:** `test_build_ocr_lemmas_main.py` (not
  `test_build_ocr_lemmas.py`, which already exists in `tests/integration/` for the `run()`
  function) and `test_build_bow_main.py` (not `test_build_bow_vocab.py`, which already exists
  in `batch/tests/` for the vocab helpers) — both deliberately distinct from pre-existing files.
- **Deviation from spec, noted:** the spec's driver pseudocode swallows "any `RuntimeError`" from
  steps 2-5 uniformly; Task 9's implementation matches that exactly, but additionally documents
  (in the new file's own docstring, not just this plan) the empty-run side effect discovered
  while researching `ingest_hash_dedup.resolve_batch()`'s unconditional run-creation — this is
  new information relative to the spec, recorded at the point future readers will actually look
  (the driver's docstring), not a deviation in behavior.
