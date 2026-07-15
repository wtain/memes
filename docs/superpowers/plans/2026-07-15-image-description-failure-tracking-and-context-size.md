# Image description failure tracking + context size + batch size — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `batch/build_image_descriptions.py` from retrying permanently-failing `(image, prompt)` pairs forever, make the Ollama context window configurable (found too small for some images), and give this pipeline its own smaller, dedicated commit interval instead of sharing the generic `settings.GENERAL.BATCH_SIZE`.

**Architecture:** Reuse the existing `ImageProcessingStatus` table (already used by the OCR pipeline) keyed as `f"image_description:{prompt.key}"` — no schema change. Two new non-committing methods on `ImageProcessingStatusRepository` (the existing `mark_failed`/`mark_started` commit internally, which would break this pipeline's batch-size-based commit cadence if reused as-is). `_images_missing_prompts` gains a `retry_failed` flag: failed pairs are excluded from work by default, included again only with `--retry-failed`. `OllamaImageDescriber.describe()` gains a `num_ctx` parameter passed through as an `options` override to `ollama.chat()`.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Dynaconf, `ollama` Python client, pytest / pytest-asyncio.

**Full design reference:** `docs/superpowers/specs/2026-07-15-image-description-failure-tracking-and-context-size.md`.

## Global Constraints

- Target Python is 3.11 (`.venv311`) — run all commands with that venv active.
- Repositories must never call `session.commit()` — the two new `ImageProcessingStatusRepository` methods added here (`record_failure`, `delete_all`) must NOT commit, unlike the existing `mark_failed`/`mark_started` on the same class (which are left untouched, still used by OCR).
- No schema migration in this plan — `ImageProcessingStatus` already exists; only new `pipeline` string values are written into it.
- No automated test gate exists for batch scripts as a whole; the pure logic functions (`_images_missing_prompts`, repository methods) do get integration tests per this repo's established convention for this pipeline.

---

### Task 1: `num_ctx` and `batch_size` config keys

**Files:**
- Modify: `environments/settings.yaml`
- Modify: `batch/tests/test_env_loading.py`
- Modify: `Backend/tests/test_config_integration.py`

**Interfaces:**
- Produces: tracked settings keys `image_descriptions.num_ctx` (default `8192`) and `image_descriptions.batch_size` (default `50`), both global-only (no per-environment override). Consumed via `settings.get("image_descriptions.num_ctx")` / `settings.get("image_descriptions.batch_size")` in Task 4.

- [ ] **Step 1: Add the new keys to both config-integration test files' expectations (failing first)**

In `batch/tests/test_env_loading.py`, add to `_COMMON` (after `"IMAGE_DESCRIPTIONS.PROMPTS_FILE": None,`):

```python
    "IMAGE_DESCRIPTIONS.NUM_CTX": 8192,
    "IMAGE_DESCRIPTIONS.BATCH_SIZE": 50,
```

Make the identical edit in `Backend/tests/test_config_integration.py`'s `_COMMON` dict (same two lines, same position).

- [ ] **Step 2: Run both test files to confirm they fail**

```bash
pytest batch/tests/test_env_loading.py Backend/tests/test_config_integration.py -v
```

Expected: FAIL — `settings.get("IMAGE_DESCRIPTIONS.NUM_CTX")` / `.BATCH_SIZE` resolve to `None`, mismatching the `8192` / `50` expectations.

- [ ] **Step 3: Add the config keys**

In `environments/settings.yaml`, change:

```yaml
image_descriptions:
  model: llava
```

to:

```yaml
image_descriptions:
  model: llava
  num_ctx: 8192
  batch_size: 50
```

- [ ] **Step 4: Run the tests again to confirm they pass**

```bash
pytest batch/tests/test_env_loading.py Backend/tests/test_config_integration.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add environments/settings.yaml batch/tests/test_env_loading.py Backend/tests/test_config_integration.py
git commit -m "feat: add image_descriptions.num_ctx and .batch_size config keys"
```

---

### Task 2: `num_ctx` param on `OllamaImageDescriber`

**Files:**
- Modify: `ai/ollama.py`
- Modify: `tests/ai/test_ollama.py`

**Interfaces:**
- Produces: `OllamaImageDescriber.describe(path: str, prompt: str, model: str, num_ctx: int) -> str` (replaces the 3-arg signature from the prior feature).
- Consumed by: Task 4 (`batch/build_image_descriptions.py`).

- [ ] **Step 1: Update the failing test first**

In `tests/ai/test_ollama.py`, change `test_describe_passes_prompt_and_model_through`:

```python
def test_describe_passes_prompt_and_model_through(monkeypatch):
    def fake_chat(model, messages):
        assert model == "qwen2.5vl:7b"
        assert messages[0]["content"] == "Explain the joke."
        assert messages[0]["images"] == ["/path/to/image.jpg"]
        return {"message": {"content": "a description"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    describer = OllamaImageDescriber()

    result = describer.describe("/path/to/image.jpg", "Explain the joke.", "qwen2.5vl:7b")

    assert result == "a description"
```

to:

```python
def test_describe_passes_prompt_model_and_num_ctx_through(monkeypatch):
    def fake_chat(model, messages, options):
        assert model == "qwen2.5vl:7b"
        assert messages[0]["content"] == "Explain the joke."
        assert messages[0]["images"] == ["/path/to/image.jpg"]
        assert options == {"num_ctx": 8192}
        return {"message": {"content": "a description"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    describer = OllamaImageDescriber()

    result = describer.describe("/path/to/image.jpg", "Explain the joke.", "qwen2.5vl:7b", 8192)

    assert result == "a description"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/ai/test_ollama.py -v -k describe
```

Expected: FAIL — `TypeError: describe() takes 4 positional arguments but 5 were given` (or similar, since the call site now passes 4 args to the still-3-arg method).

- [ ] **Step 3: Implement**

In `ai/ollama.py`, replace:

```python
class OllamaImageDescriber:

    def describe(self, path: str, prompt: str, model: str) -> str:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [path]
            }]
        )
        return response['message']['content']
```

with:

```python
class OllamaImageDescriber:

    def describe(self, path: str, prompt: str, model: str, num_ctx: int) -> str:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [path]
            }],
            options={'num_ctx': num_ctx}
        )
        return response['message']['content']
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/ai/test_ollama.py -v
```

Expected: PASS (all 5 tests in the file).

- [ ] **Step 5: Commit**

```bash
git add ai/ollama.py tests/ai/test_ollama.py
git commit -m "feat: make Ollama context window (num_ctx) configurable per describe() call"
```

---

### Task 3: Failure-tracking methods on `ImageProcessingStatusRepository`

**Files:**
- Modify: `repository/image_procesing_status.py`
- Test: `tests/integration/test_image_processing_status_repository.py`

**Interfaces:**
- Produces: `ImageProcessingStatusRepository.record_failure(image_id, error: str) -> None` (no commit), `.get_image_ids_with_status(status: str) -> set[uuid.UUID]`, `.delete_all() -> None` (no commit). All three scoped to `self.pipeline`, same as the class's existing methods.
- Consumed by: Task 4 (`batch/build_image_descriptions.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_image_processing_status_repository.py`:

```python
"""
Integration tests for repository/image_procesing_status.py's failure-tracking methods.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_procesing_status import ImageProcessingStatusRepository
from Storage.models import Image, ImageProcessingStatus


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_record_failure_writes_without_committing(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, "image_description:general_description")

    await repo.record_failure(image.id, "context size exceeded")
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == "image_description:general_description",
        )
    )
    row = result.scalar_one()
    assert row.status == "failed"
    assert row.error_message == "context size exceeded"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_ids_with_status_filters_by_pipeline_and_status(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    repo_a = ImageProcessingStatusRepository(db_session, "image_description:general_description")
    repo_b = ImageProcessingStatusRepository(db_session, "image_description:humor_explanation")

    await repo_a.record_failure(image_a.id, "boom")
    await repo_b.record_failure(image_b.id, "boom")
    await db_session.flush()

    failed_for_a = await repo_a.get_image_ids_with_status("failed")
    assert failed_for_a == {image_a.id}

    failed_for_other_status = await repo_a.get_image_ids_with_status("done")
    assert failed_for_other_status == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_only_clears_its_own_pipeline(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    repo_a = ImageProcessingStatusRepository(db_session, "image_description:general_description")
    repo_b = ImageProcessingStatusRepository(db_session, "image_description:humor_explanation")

    await repo_a.record_failure(image_a.id, "boom")
    await repo_b.record_failure(image_b.id, "boom")
    await db_session.flush()

    await repo_a.delete_all()
    await db_session.flush()

    assert await repo_a.get_image_ids_with_status("failed") == set()
    assert await repo_b.get_image_ids_with_status("failed") == {image_b.id}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_processing_status_repository.py -v
```

Expected: FAIL — `AttributeError: 'ImageProcessingStatusRepository' object has no attribute 'record_failure'`.

- [ ] **Step 3: Implement**

In `repository/image_procesing_status.py`, add `uuid` to the imports and `select`/`delete` from sqlalchemy:

```python
import uuid
from datetime import datetime

from sqlalchemy import delete, select

from Storage.models import ImageProcessingStatus
```

Add three new methods to the end of the `ImageProcessingStatusRepository` class (after the existing `should_process`):

```python
    async def record_failure(self, image_id, error: str) -> None:
        """No commit — caller controls commit timing via its own batch committer."""
        status = await self.session.get(
            ImageProcessingStatus, {"image_id": image_id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image_id=image_id, pipeline=self.pipeline)
            self.session.add(status)
        status.status = "failed"
        status.error_message = error
        status.finished_at = datetime.utcnow()

    async def get_image_ids_with_status(self, status: str) -> set[uuid.UUID]:
        result = await self.session.execute(
            select(ImageProcessingStatus.image_id)
            .where(ImageProcessingStatus.pipeline == self.pipeline, ImageProcessingStatus.status == status)
        )
        return set(result.scalars().all())

    async def delete_all(self) -> None:
        """No commit — same convention as record_failure."""
        await self.session.execute(
            delete(ImageProcessingStatus).where(ImageProcessingStatus.pipeline == self.pipeline)
        )
```

Leave every existing method (`mark_started`, `get_image_status`, `mark_done`, `mark_failed`, `should_process`) exactly as-is — they're used by the OCR pipeline and out of scope here.

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_processing_status_repository.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add repository/image_procesing_status.py tests/integration/test_image_processing_status_repository.py
git commit -m "feat: add non-committing failure-tracking methods to ImageProcessingStatusRepository"
```

---

### Task 4: Wire failure tracking, `num_ctx`, and `batch_size` into the batch script

**Files:**
- Modify: `batch/build_image_descriptions.py`
- Modify: `tests/integration/test_build_image_descriptions.py`

**Interfaces:**
- Consumes: `ImageProcessingStatusRepository.record_failure/get_image_ids_with_status/delete_all` (Task 3), `OllamaImageDescriber.describe(path, prompt, model, num_ctx)` (Task 2), `settings.get("image_descriptions.num_ctx"/".batch_size")` (Task 1).
- Produces: `_status_repos(session, prompts) -> dict[str, ImageProcessingStatusRepository]`; `_images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts, retry_failed: bool = False, metrics=None)` (signature change — now takes `status_repos` and `retry_failed`, plus optional `metrics` for the `skipped.failed` counter); new `--retry-failed` CLI flag.

- [ ] **Step 1: Update the existing test's call site, then write the new failing test**

In `tests/integration/test_build_image_descriptions.py`, add `_status_repos` to the import:

```python
from batch.build_image_descriptions import _images_missing_prompts, _status_repos
```

Update the existing test's call site — change:

```python
    work = await _images_missing_prompts(images_repo, descriptions_repo, prompts)
```

to:

```python
    status_repos = _status_repos(db_session, prompts)
    work = await _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts)
```

Then add a new test function to the same file:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_images_missing_prompts_excludes_failed_pairs_unless_retrying(db_session):
    image = await _insert_image(db_session)

    descriptions_repo = ImageDescriptionsRepository(db_session)
    prompts = [
        PromptConfig(key="general_description", prompt="What is shown?"),
        PromptConfig(key="humor_explanation", prompt="Explain the joke."),
    ]
    images_repo = ImagesRepository(db_session)
    status_repos = _status_repos(db_session, prompts)

    await status_repos["general_description"].record_failure(image.id, "context size exceeded")
    await db_session.flush()

    work_default = await _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts)
    work_by_image_default = {image_id: missing for _, image_id, missing in work_default}
    assert {p.key for p in work_by_image_default[image.id]} == {"humor_explanation"}

    work_retry = await _images_missing_prompts(
        images_repo, descriptions_repo, status_repos, prompts, retry_failed=True
    )
    work_by_image_retry = {image_id: missing for _, image_id, missing in work_retry}
    assert {p.key for p in work_by_image_retry[image.id]} == {"general_description", "humor_explanation"}
```

This needs `ImageDescriptionsRepository` and `ImagesRepository` already imported (they are, from the file's existing imports) and `PromptConfig` (already imported) — no new imports beyond `_status_repos` added above.

- [ ] **Step 2: Run tests to verify the new one fails**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_image_descriptions.py -v
```

Expected: FAIL — `ImportError: cannot import name '_status_repos' from 'batch.build_image_descriptions'`.

- [ ] **Step 3: Rewrite `batch/build_image_descriptions.py`**

Replace the entire file:

```python
import argparse
import asyncio
import os

from ai.image_description_prompts import load_prompts, resolve_model
from ai.ollama import OllamaImageDescriber
from batch.utils.description_batch_commit import DescriptionBatchCommitter
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.image_descriptions import ImageDescriptionsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository


def _status_repos(session, prompts):
    return {p.key: ImageProcessingStatusRepository(session, f"image_description:{p.key}") for p in prompts}


async def _load_existing_pairs(descriptions_repo, prompts):
    existing = {}
    for prompt in prompts:
        existing[prompt.key] = await descriptions_repo.get_image_ids_with_prompt(prompt.key)
    return existing


async def _load_failed_pairs(status_repos, prompts):
    failed = {}
    for prompt in prompts:
        failed[prompt.key] = await status_repos[prompt.key].get_image_ids_with_status("failed")
    return failed


async def _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts,
                                   retry_failed: bool = False, metrics=None):
    succeeded = await _load_existing_pairs(descriptions_repo, prompts)
    failed = {} if retry_failed else await _load_failed_pairs(status_repos, prompts)

    result = await images_repo.get_all_images()
    work = []
    for filename, image_id in result:
        missing = []
        for p in prompts:
            if image_id in succeeded[p.key]:
                continue
            if not retry_failed and image_id in failed[p.key]:
                if metrics is not None:
                    metrics.increment("skipped.failed")
                continue
            missing.append(p)
        if missing:
            work.append((filename, image_id, missing))
    return work


async def main(reset: bool, limit: int | None = None, retry_failed: bool = False):
    BASE_PATH = settings.BASE_PATH
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    prompts_file = settings.get("image_descriptions.prompts_file")
    if not prompts_file:
        raise RuntimeError(
            "image_descriptions.prompts_file is not configured for this environment"
        )
    prompts_path = os.path.join(os.path.dirname(__file__), prompts_file)
    prompts = load_prompts(prompts_path)
    print(f"Loaded {len(prompts)} prompt(s): {[p.key for p in prompts]}")

    num_ctx = settings.get("image_descriptions.num_ctx")
    batch_size = settings.get("image_descriptions.batch_size")

    metrics = SimpleMetricsListener()
    describer = OllamaImageDescriber()

    async with AsyncSessionLocal() as session:
        descriptions_repo = ImageDescriptionsRepository(session)
        images_repo = ImagesRepository(session)
        status_repos = _status_repos(session, prompts)

        if reset:
            print("Deleting all descriptions...")
            await descriptions_repo.delete_all()
            for status_repo in status_repos.values():
                await status_repo.delete_all()
            await session.commit()
            print("Done")

        work = await _images_missing_prompts(
            images_repo, descriptions_repo, status_repos, prompts, retry_failed, metrics
        )
        if limit is not None:
            work = work[:limit]

        committer = DescriptionBatchCommitter(session, batch_size=batch_size)
        tracker = ProgressTracker(total=len(work), report_every=settings.GENERAL.PROGRESS_EVERY)

        for filename, image_id, missing in work:
            path = os.path.join(base_path, filename)

            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                metrics.increment("skipped.webp")
                tracker.skip()
                continue

            for prompt in missing:
                model = resolve_model(prompt, settings)
                try:
                    text = describer.describe(path, prompt.prompt, model, num_ctx)
                    committer.save_description(image_id, prompt.key, model, text)
                    metrics.increment("saved")
                except Exception as e:
                    print(f"Model failed for {path} [{prompt.key}]: {e}")
                    metrics.increment("error.model")
                    await status_repos[prompt.key].record_failure(image_id, str(e))

            await committer.on_image_done()
            tracker.mark_done()

        await committer.close()

    tracker.summary()
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing descriptions before running "
                             "(default: fill only missing image/prompt pairs)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most this many images (default: no limit)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-attempt previously-failed image/prompt pairs "
                             "(default: skip pairs that failed on a prior run)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.reset, args.limit, args.retry_failed))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_image_descriptions.py -v
```

Expected: PASS (2 tests — the original `test_images_missing_prompts_returns_only_uncovered_pairs` and the new `test_images_missing_prompts_excludes_failed_pairs_unless_retrying`).

- [ ] **Step 5: Import/`--help` sanity check**

```bash
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" python -c "import batch.build_image_descriptions"
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" python -m batch.build_image_descriptions --help
```

Expected: no traceback; `--help` output lists `--env`, `--reset`, `--limit`, and `--retry-failed`.

- [ ] **Step 6: Commit**

```bash
git add batch/build_image_descriptions.py tests/integration/test_build_image_descriptions.py
git commit -m "feat: skip previously-failed image/prompt pairs by default, wire num_ctx and dedicated batch_size"
```

---

## Final verification (after all tasks)

```bash
pytest batch/tests/ tests/rules/ tests/ai/ -q
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q
pytest Backend/tests/ -q
```

All should pass. Note: `tests/integration/` and `Backend/tests/` must be run as separate `pytest` invocations (not combined in one command with the others) — this repo has multiple `pytest.ini` files with different `asyncio_mode` settings, and combining test roots in a single invocation from repo root causes Backend's async tests to be collected under the wrong plugin config.
