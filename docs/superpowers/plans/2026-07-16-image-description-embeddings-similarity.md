# Image description embeddings + description-based similarity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate the placeholder `ImageDescriptionEmbedding` table with text embeddings of each `ImageDescription` row, and add a `source=image|description` mode to `GET /api/images/{image_id}/similar` so images can be ranked by textual/semantic similarity as an alternative to the existing CLIP visual-similarity ranking.

**Architecture:** A new batch script embeds description text via `ai/sbert.py`'s existing `SbertModel` class (reused as-is with a different `model_name`) and a new repository. A new `ImageRepository` query implements same-prompt-only, best-match-across-shared-prompts comparison via `GROUP BY` + `MIN`. The API/service layer branches on a new `source` query param; response schema is unchanged.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, `sentence-transformers`, FastAPI, pytest / pytest-asyncio.

**Full design reference:** `docs/superpowers/specs/2026-07-16-image-description-embeddings-similarity.md`.

## Global Constraints

- Target Python is 3.11 (`.venv311`) — run all commands with that venv active.
- Repositories must never call `session.commit()` — batch scripts and the Backend's `get_async_db` own commit lifecycle, not repository methods.
- No schema migration in this plan — `ImageDescriptionEmbedding` already exists (from the prior multi-prompt-descriptions work); this plan only populates and reads it.
- `backend_api.md` must stay in sync with the actual routers (per this repo's CLAUDE.md) — the `source` param addition must be documented there.
- The mode-switch default (`source="image"`) must preserve current behavior for existing callers that don't pass `source` at all — this is an additive API change, not a breaking one.

---

### Task 1: Description embedding generation (batch job + repository)

**Files:**
- Create: `repository/image_description_embeddings.py`
- Create: `batch/build_image_description_embeddings.py`
- Test: `tests/integration/test_image_description_embeddings_repository.py`

**Interfaces:**
- Produces: `ImageDescriptionEmbeddingsRepository` with `get_descriptions_without_embedding()` (returns rows with `.id`/`.text`), `save(description_id, embedding: list[float])` (no commit), `delete_all()` (no commit).
- Consumed by: the new batch script only in this plan (Task 2/3 read via a different, Backend-side repository method, not this one).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_image_description_embeddings_repository.py`:

```python
"""
Integration tests for repository/image_description_embeddings.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_description_embeddings import ImageDescriptionEmbeddingsRepository
from Storage.models import Image, ImageDescription, ImageDescriptionEmbedding

_DIM = 1024


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _insert_description(session, text: str = "a description") -> ImageDescription:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    description = ImageDescription(
        image_id=image.id, prompt_key="general_description", model_used="llava", text=text,
    )
    session.add(description)
    await session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_without_embedding_excludes_already_embedded(db_session):
    embedded = await _insert_description(db_session, text="already embedded")
    unembedded = await _insert_description(db_session, text="needs embedding")

    repo = ImageDescriptionEmbeddingsRepository(db_session)
    repo.save(embedded.id, _text_unit_vector(0))
    await db_session.flush()

    rows = await repo.get_descriptions_without_embedding()
    ids = {row.id for row in rows}

    assert embedded.id not in ids
    assert unembedded.id in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_save_persists_the_embedding(db_session):
    description = await _insert_description(db_session)
    repo = ImageDescriptionEmbeddingsRepository(db_session)

    vec = _text_unit_vector(5)
    repo.save(description.id, vec)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageDescriptionEmbedding.embedding)
        .where(ImageDescriptionEmbedding.image_description_id == description.id)
    )
    stored = result.scalar_one()
    assert list(stored) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_removes_every_row(db_session):
    description_a = await _insert_description(db_session)
    description_b = await _insert_description(db_session)
    repo = ImageDescriptionEmbeddingsRepository(db_session)
    repo.save(description_a.id, _text_unit_vector(0))
    repo.save(description_b.id, _text_unit_vector(1))
    await db_session.flush()

    await repo.delete_all()
    await db_session.flush()

    rows = await repo.get_descriptions_without_embedding()
    ids = {row.id for row in rows}
    assert description_a.id in ids
    assert description_b.id in ids
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_description_embeddings_repository.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'repository.image_description_embeddings'`.

- [ ] **Step 3: Implement the repository**

Create `repository/image_description_embeddings.py`:

```python
from sqlalchemy import delete, select

from Storage.models import ImageDescription, ImageDescriptionEmbedding


class ImageDescriptionEmbeddingsRepository:

    def __init__(self, session):
        self.session = session

    async def get_descriptions_without_embedding(self):
        has_embedding = select(ImageDescriptionEmbedding.image_description_id).distinct().scalar_subquery()
        result = await self.session.execute(
            select(ImageDescription.id, ImageDescription.text)
            .where(ImageDescription.id.not_in(has_embedding))
        )
        return result.all()

    def save(self, description_id, embedding: list[float]) -> None:
        self.session.add(ImageDescriptionEmbedding(
            image_description_id=description_id, embedding=embedding,
        ))

    async def delete_all(self) -> None:
        await self.session.execute(delete(ImageDescriptionEmbedding))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_image_description_embeddings_repository.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Create the batch script**

Create `batch/build_image_description_embeddings.py`:

```python
import argparse
import asyncio

from ai.sbert import SbertModel
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from repository.image_description_embeddings import ImageDescriptionEmbeddingsRepository

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"


async def main(reset: bool):
    async with AsyncSessionLocal() as session:
        embeddings_repo = ImageDescriptionEmbeddingsRepository(session)

        if reset:
            print("Deleting all description embeddings...")
            await embeddings_repo.delete_all()
            await session.commit()
            print("Done")

        rows = await embeddings_repo.get_descriptions_without_embedding()
        print(f"Found {len(rows)} description(s) needing embeddings")

        embedder = SbertModel(model_name=EMBEDDING_MODEL)
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        for i, (description_id, text) in enumerate(rows):
            vector = embedder.embed_text(text)
            embeddings_repo.save(description_id, vector.tolist())
            tracker.mark_done()
            if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
                await session.commit()

        await session.commit()

    tracker.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing description embeddings before running "
                             "(default: fill only descriptions missing an embedding)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.reset))
```

- [ ] **Step 6: Import/`--help` sanity check**

```bash
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" python -c "import batch.build_image_description_embeddings"
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" python -m batch.build_image_description_embeddings --help
```

Expected: no traceback; `--help` output lists `--env` and `--reset`.

- [ ] **Step 7: Commit**

```bash
git add repository/image_description_embeddings.py batch/build_image_description_embeddings.py tests/integration/test_image_description_embeddings_repository.py
git commit -m "feat: add batch job to populate image description embeddings"
```

---

### Task 2: `get_similar_by_description` + `has_description_embedding` on `ImageRepository`

**Files:**
- Modify: `Backend/app/repositories/image_repository.py`
- Modify: `tests/integration/test_backend_image_repository.py`

**Interfaces:**
- Produces: `ImageRepository.get_similar_by_description(image_id: str, limit: int = 10)` returning `(image_id, distance, filename, flagged)`-shaped rows (same shape as the existing `get_similar`). `ImageRepository.has_description_embedding(image_id: str) -> bool`.
- Consumed by: Task 3 (`Backend/app/services/image_service.py`).

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_backend_image_repository.py`, add to the imports (`Storage.models` import block):

```python
from Storage.models import (
    Embedding,
    Image,
    ImageDescription,
    ImageDescriptionEmbedding,
    ImageExtras,
    ImageTag,
    OCRText,
    TmpDuplicates,
    TmpImageClusters,
)
```

Add a second dimension helper near the existing `_DIM = 512` / `_unit_vector` (do not modify those — description embeddings are a different, 1024-dim vector space):

```python
_TEXT_DIM = 1024


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * _TEXT_DIM
    vec[index] = 1.0
    return vec


async def _insert_description(session, image, prompt_key: str, text: str = "a description") -> ImageDescription:
    description = ImageDescription(
        image_id=image.id, prompt_key=prompt_key, model_used="llava", text=text,
    )
    session.add(description)
    await session.flush()
    return description
```

Add a new test section after the existing `# get_embedding / get_similar` section (before `# get_untagged / get_no_ocr`):

```python
# --------------------------------------------------------------------------
# get_similar_by_description / has_description_embedding
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_matches_only_same_prompt_key_and_excludes_self(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    same_prompt_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    different_prompt_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, same_prompt_candidate, different_prompt_candidate])
    await db_session.flush()

    query_desc = await _insert_description(db_session, query_image, "general_description")
    same_desc = await _insert_description(db_session, same_prompt_candidate, "general_description")
    different_desc = await _insert_description(db_session, different_prompt_candidate, "humor_explanation")

    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=same_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=different_desc.id, embedding=_text_unit_vector(0)),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    ids = [r.image_id for r in rows]
    assert query_image.id not in ids
    assert same_prompt_candidate.id in ids
    assert different_prompt_candidate.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_takes_minimum_distance_across_shared_prompts(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, candidate])
    await db_session.flush()

    query_general = await _insert_description(db_session, query_image, "general_description")
    query_humor = await _insert_description(db_session, query_image, "humor_explanation")
    cand_general = await _insert_description(db_session, candidate, "general_description")
    cand_humor = await _insert_description(db_session, candidate, "humor_explanation")

    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_general.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=query_humor.id, embedding=_text_unit_vector(0)),
        # general_description pair is far (orthogonal); humor_explanation pair is identical (close)
        ImageDescriptionEmbedding(image_description_id=cand_general.id, embedding=_text_unit_vector(1)),
        ImageDescriptionEmbedding(image_description_id=cand_humor.id, embedding=_text_unit_vector(0)),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    row = next(r for r in rows if r.image_id == candidate.id)
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_includes_flagged_status(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    flagged_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, flagged_candidate])
    await db_session.flush()

    query_desc = await _insert_description(db_session, query_image, "general_description")
    cand_desc = await _insert_description(db_session, flagged_candidate, "general_description")
    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=cand_desc.id, embedding=_text_unit_vector(0)),
    ])
    db_session.add(ImageExtras(image_id=flagged_candidate.id, flagged=True))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    row = next(r for r in rows if r.image_id == flagged_candidate.id)
    assert row.flagged is True


@pytest.mark.asyncio(loop_scope="session")
async def test_has_description_embedding_true_when_present_false_otherwise(db_session):
    embedded_image = Image(filename=f"{uuid.uuid4()}.jpg")
    unembedded_image = Image(filename=f"{uuid.uuid4()}.jpg")
    no_description_image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([embedded_image, unembedded_image, no_description_image])
    await db_session.flush()

    embedded_desc = await _insert_description(db_session, embedded_image, "general_description")
    await _insert_description(db_session, unembedded_image, "general_description")
    db_session.add(ImageDescriptionEmbedding(image_description_id=embedded_desc.id, embedding=_text_unit_vector(0)))
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.has_description_embedding(embedded_image.id) is True
    assert await repo.has_description_embedding(unembedded_image.id) is False
    assert await repo.has_description_embedding(no_description_image.id) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v -k "similar_by_description or has_description_embedding"
```

Expected: FAIL — `AttributeError: 'ImageRepository' object has no attribute 'get_similar_by_description'`.

- [ ] **Step 3: Implement**

In `Backend/app/repositories/image_repository.py`, change the imports (line 7 and line 12):

```python
from sqlalchemy import select, tuple_, distinct, and_, union_all, func
```

```python
from Storage.models import (
    Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters,
    ImageDescription, ImageDescriptionEmbedding,
)
```

Add these two methods immediately after the existing `get_similar` method (after its closing `return result.all()`, before `get_meme_data`):

```python
    async def get_similar_by_description(self, image_id: str, limit: int = 10):
        source_desc, source_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
        cand_desc, cand_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
        img, extras = aliased(Image), aliased(ImageExtras)

        result = await self.session.execute(
            select(
                cand_desc.image_id,
                func.min(source_emb.embedding.cosine_distance(cand_emb.embedding)).label("distance"),
                img.filename,
                extras.flagged,
            )
            .select_from(source_desc)
            .join(source_emb, source_emb.image_description_id == source_desc.id)
            .join(cand_desc, cand_desc.prompt_key == source_desc.prompt_key)
            .join(cand_emb, cand_emb.image_description_id == cand_desc.id)
            .join(img, img.id == cand_desc.image_id)
            .outerjoin(extras, extras.image_id == cand_desc.image_id)
            .where(source_desc.image_id == image_id, cand_desc.image_id != image_id)
            .group_by(cand_desc.image_id, img.filename, extras.flagged)
            .order_by("distance")
            .limit(limit)
        )
        return result.all()

    async def has_description_embedding(self, image_id: str) -> bool:
        result = await self.session.execute(
            select(ImageDescriptionEmbedding.image_description_id)
            .join(ImageDescription, ImageDescription.id == ImageDescriptionEmbedding.image_description_id)
            .where(ImageDescription.image_id == image_id)
            .limit(1)
        )
        return result.first() is not None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v
```

Expected: PASS (all tests in the file, including the 4 new ones — run the whole file, not just the new tests, to confirm nothing regressed).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/image_repository.py tests/integration/test_backend_image_repository.py
git commit -m "feat: add get_similar_by_description and has_description_embedding to ImageRepository"
```

---

### Task 3: Wire `source` mode through the service, API, and docs

**Files:**
- Modify: `Backend/app/services/image_service.py`
- Modify: `Backend/app/api/images.py`
- Modify: `Backend/tests/test_images_endpoints.py`
- Modify: `backend_api.md`

**Interfaces:**
- Consumes: `ImageRepository.get_similar_by_description`/`has_description_embedding` (Task 2).
- Produces: `ImageService.get_similar(image_id, limit=10, source="image")`; `GET /api/images/{image_id}/similar?source=image|description`.

- [ ] **Step 1: Update the existing router tests' call-site expectations, then add a new one**

In `Backend/tests/test_images_endpoints.py`, the `TestGetSimilarImages` class has 3 existing tests each asserting `mock_image_service.get_similar.assert_called_once_with(<id>, limit=10)`. Once the router passes `source` through on every call (including the default), these assertions will fail unless updated. Change all 3 occurrences:

```python
        mock_image_service.get_similar.assert_called_once_with("123", limit=10)
```
→
```python
        mock_image_service.get_similar.assert_called_once_with("123", limit=10, source="image")
```

```python
        mock_image_service.get_similar.assert_called_once_with("456", limit=10)
```
→
```python
        mock_image_service.get_similar.assert_called_once_with("456", limit=10, source="image")
```

```python
        mock_image_service.get_similar.assert_called_once_with(uuid_id, limit=10)
```
→
```python
        mock_image_service.get_similar.assert_called_once_with(uuid_id, limit=10, source="image")
```

Then add a new test to the same `TestGetSimilarImages` class:

```python
    def test_get_similar_images_with_source_description(self, client, mock_image_service):
        """Test that the source query param is passed through to the service."""
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_similar.return_value = mock_response

        response = client.get("/api/images/123/similar?source=description")

        assert response.status_code == 200
        mock_image_service.get_similar.assert_called_once_with("123", limit=10, source="description")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest Backend/tests/test_images_endpoints.py -v -k TestGetSimilarImages
```

Expected: FAIL — the 3 existing tests fail on the assertion (actual call still `("123", limit=10)`, no `source`), and the new test fails because `source` isn't a valid query param yet (or the mock assertion doesn't match).

- [ ] **Step 3: Implement — service layer**

In `Backend/app/services/image_service.py`, replace:

```python
    async def get_similar(self, image_id: str, limit: int = 10) -> MemeSearchResponse:
        embedding = await self.repo.get_embedding(image_id)
        if embedding is None:
            raise HTTPException(status_code=404, detail="No embedding found for this image")
        rows = await self.repo.get_similar(image_id, embedding.tolist(), limit=limit)
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

with:

```python
    async def get_similar(self, image_id: str, limit: int = 10, source: str = "image") -> MemeSearchResponse:
        if source == "description":
            if not await self.repo.has_description_embedding(image_id):
                raise HTTPException(status_code=404, detail="No description embedding found for this image")
            rows = await self.repo.get_similar_by_description(image_id, limit=limit)
        else:
            embedding = await self.repo.get_embedding(image_id)
            if embedding is None:
                raise HTTPException(status_code=404, detail="No embedding found for this image")
            rows = await self.repo.get_similar(image_id, embedding.tolist(), limit=limit)

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

Both branches yield the same `(image_id, distance, filename, flagged)` row shape (matching `get_similar_by_description`'s column order from Task 2), so the `Meme`-building comprehension needs no change.

- [ ] **Step 4: Implement — router layer**

In `Backend/app/api/images.py`, add `Literal` to the `typing` import (line 3):

```python
from typing import Optional, AsyncGenerator, Literal
```

Replace:

```python
@router.get("/{image_id}/similar", response_model=MemeSearchResponse)
async def get_similar_images(
    image_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=100),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_similar(image_id, limit=limit)
```

with:

```python
@router.get("/{image_id}/similar", response_model=MemeSearchResponse)
async def get_similar_images(
    image_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=100),
    source: Literal["image", "description"] = "image",
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_similar(image_id, limit=limit, source=source)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest Backend/tests/test_images_endpoints.py -v
```

Expected: PASS (all tests in the file, not just `TestGetSimilarImages` — confirm nothing else regressed).

- [ ] **Step 6: Update `backend_api.md`**

Replace:

```markdown
#### Get Similar Images

Find images similar to a given image.

- **URL**: `/api/images/{image_id}/similar`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 10)
- **Response**: `MemeSearchResponse` — each `Meme` item includes `cosineDistance` (float, lower = more similar)
- **Example**: `GET /api/images/abc123/similar?limit=10`
```

with:

```markdown
#### Get Similar Images

Find images similar to a given image.

- **URL**: `/api/images/{image_id}/similar`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 10)
  - `source` (optional): `image` (default) ranks by CLIP visual-embedding similarity; `description` ranks by LLM-description text-embedding similarity (only images sharing at least one prompt's description embedding with the source image are candidates). Returns 404 if the source image has no embedding of the requested kind.
- **Response**: `MemeSearchResponse` — each `Meme` item includes `cosineDistance` (float, lower = more similar; not comparable between `source=image` and `source=description` responses — different embedding spaces)
- **Example**: `GET /api/images/abc123/similar?limit=10`
- **Example**: `GET /api/images/abc123/similar?source=description&limit=10`
```

- [ ] **Step 7: Commit**

```bash
git add Backend/app/services/image_service.py Backend/app/api/images.py Backend/tests/test_images_endpoints.py backend_api.md
git commit -m "feat: add source=image|description mode to GET /images/{id}/similar"
```

---

## Final verification (after all tasks)

```bash
pytest Backend/tests/ -q
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q
pytest batch/tests/ tests/rules/ tests/ai/ -q
```

Run each as a separate invocation, not combined (this repo's `Backend/tests/` and root-level test dirs have different `pytest.ini` `asyncio_mode` settings — combining them in one command breaks Backend's async test collection; see `CLAUDE.md`'s "Known gotchas" section).

Then, per this repo's CLAUDE.md pre-commit checklist: confirm the Backend server starts without import errors and manually hit `GET /api/images/{some_id}/similar?source=description` against a real environment that has description embeddings populated (requires running `build_image_description_embeddings.py` first).
