# Description Approve/Reject Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user Approve or Reject each AI-generated image description, inline, on both Web and Android, with the tri-state result stored server-side and surfaced back through the existing descriptions fetch — plus basic aggregate counts in the diagnostics statistics endpoint.

**Architecture:** One new Postgres table (`image_description_feedback`, one optional row per description — presence encodes the tri-state) behind two new toggle endpoints (`PUT .../approve`, `PUT .../reject`) and an extended `GET .../descriptions` response. Both clients regenerate their DTOs from the shared JSON schema, add one API method, and wire an inline button pair into their existing descriptions UI.

**Tech Stack:** FastAPI + SQLAlchemy async ORM + Alembic (backend), React + TypeScript + Vitest (Web), Kotlin + Jetpack Compose + Retrofit + JUnit/MockK (Android).

## Global Constraints

- Feedback is a single global value per description — no per-user attribution (spec's Non-goals).
- A rejected description stays visible and still participates in `source=description` similarity search — this plan makes **no** change to `get_similar_by_description` or any ranking/filtering logic.
- Tri-state toggle: clicking the currently-active button clears feedback back to `null`; clicking the other button switches directly. No third "clear" endpoint — `approve`/`reject` are each self-toggling.
- Both clients update UI state only after the server response arrives — no optimistic updates for this feature (Android's `toggleFlagged` is optimistic, but that is a different, pre-existing feature; this plan does not touch it or follow its pattern).
- Every schema/type change must be regenerated via the project's existing generator scripts, never hand-edited into a `generated/` file.
- `backend_api.md` must be updated for every endpoint/response-shape change in the same task that makes the change.

---

## Task 1: Data model + migration

**Files:**
- Modify: `Storage/models.py:96-101` (add relationship), insert new class after line 123 (before `class Embedding`)
- Create: `Storage/alembic/versions/<generated>_add_image_description_feedback_table.py` (via `alembic revision --autogenerate`)

**Interfaces:**
- Produces: `ImageDescriptionFeedback` SQLAlchemy model — table `image_description_feedback`, PK/FK `image_description_id` (UUID, FK to `image_descriptions.id`, `ondelete="CASCADE"`), `approved` (Boolean, not nullable), `created_at` (DateTime). Absence of a row = no feedback given.

- [ ] **Step 1: Add the relationship on `ImageDescription`**

In `Storage/models.py`, change lines 96-100 from:

```python
    image = relationship("Image", back_populates="descriptions")
    embedding = relationship(
        "ImageDescriptionEmbedding", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )
```

to:

```python
    image = relationship("Image", back_populates="descriptions")
    embedding = relationship(
        "ImageDescriptionEmbedding", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )
    feedback = relationship(
        "ImageDescriptionFeedback", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )
```

- [ ] **Step 2: Add the `ImageDescriptionFeedback` model**

In `Storage/models.py`, immediately after the `ImageDescriptionEmbedding` class (after the line `    description = relationship("ImageDescription", back_populates="embedding")` and before the blank lines leading into `class Embedding(Base):`), insert:

```python
class ImageDescriptionFeedback(Base):
    __tablename__ = "image_description_feedback"

    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    approved = Column(Boolean, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    description = relationship("ImageDescription", back_populates="feedback")
```

`Boolean` is already imported at the top of `Storage/models.py` (line 6) — no new imports needed.

- [ ] **Step 3: Generate the migration**

Run from `Storage/`, with the `general` environment's DB credentials loaded (PowerShell):

```powershell
cd Storage
Get-Content ..\environments\.env.general | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
..\.venv311\Scripts\alembic.exe revision --autogenerate -m "add image_description_feedback table"
```

Expected: a new file appears under `Storage/alembic/versions/`, printed by the command as `Generating ...`.

- [ ] **Step 4: Verify the generated migration**

Open the new file. Confirm `upgrade()` contains a `op.create_table('image_description_feedback', ...)` with columns `image_description_id` (UUID, not nullable), `approved` (Boolean, not nullable), `created_at` (DateTime), a `ForeignKeyConstraint(['image_description_id'], ['image_descriptions.id'], ondelete='CASCADE')`, and `PrimaryKeyConstraint('image_description_id')` — matching the shape of the existing `957d8e420fd5_add_image_extras_table.py` migration (same repo, same pattern: PK/FK-only table, no separate index needed since the PK already indexes it). Confirm `downgrade()` contains the matching `op.drop_table('image_description_feedback')`. If autogenerate produced anything unexpected (e.g. picked up unrelated drift from another environment's schema), adjust the file by hand to contain only this table's `create_table`/`drop_table` pair.

- [ ] **Step 5: Apply and round-trip the migration**

```powershell
..\.venv311\Scripts\alembic.exe upgrade head
..\.venv311\Scripts\alembic.exe downgrade -1
..\.venv311\Scripts\alembic.exe upgrade head
..\.venv311\Scripts\alembic.exe current
```

Expected: all four commands succeed with no errors; `current` prints the new revision's ID as `(head)`.

- [ ] **Step 6: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/
git commit -m "feat: add image_description_feedback table"
```

---

## Task 2: Backend repository, service, router, and shared schema

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (imports at top; `get_descriptions` method at line 177; new methods)
- Modify: `Backend/app/services/image_service.py` (imports at top; `get_descriptions` method at line 111; new methods)
- Modify: `Backend/app/api/images.py` (imports at top; new endpoints after `get_image_descriptions`)
- Modify: `shared/schemas/imagedescription.schema.json` (add `feedback` property)
- Create: `shared/schemas/descriptionfeedbackresponse.schema.json`
- Modify: `shared/schemas/all.schema.json` (add `DescriptionFeedbackResponse` entry)
- Run: `Backend/generate-types.sh`'s command (regenerates `Backend/app/types/generated/`)
- Modify: `backend_api.md` (Get Image Descriptions section; two new endpoint sections)
- Modify: `Backend/tests/test_image_service.py` (new test classes)
- Modify: `Backend/tests/test_images_endpoints.py` (new test classes; update existing `TestGetImageDescriptions` assertions)

**Interfaces:**
- Consumes: `ImageDescriptionFeedback` model from Task 1 (`Storage.models.ImageDescriptionFeedback`, columns `image_description_id`, `approved`).
- Produces:
  - `ImageRepository.get_description_id(image_id: str, prompt_key: str) -> uuid.UUID | None`
  - `ImageRepository.get_description_feedback(description_id) -> bool | None`
  - `ImageRepository.set_description_feedback(description_id, approved: bool) -> None`
  - `ImageRepository.clear_description_feedback(description_id) -> None`
  - `ImageRepository.get_descriptions(image_id: str)` — now yields 5-tuples `(prompt_key, text, model_used, created_at, approved)` instead of 4-tuples.
  - `ImageService.approve_description_feedback(image_id: str, prompt_key: str) -> str | None`
  - `ImageService.reject_description_feedback(image_id: str, prompt_key: str) -> str | None`
  - `ImageService.get_descriptions(image_id: str) -> list[ImageDescription]` — each item now carries `.feedback: str | None`.
  - `PUT /api/images/{image_id}/descriptions/{prompt_key}/approve` → `DescriptionFeedbackResponse`
  - `PUT /api/images/{image_id}/descriptions/{prompt_key}/reject` → `DescriptionFeedbackResponse`
  - `Backend.app.types.generated.descriptionfeedbackresponse.Schema` (Pydantic, field `feedback: str | None`)

### Repository

- [ ] **Step 1: Add the new import and repository methods**

In `Backend/app/repositories/image_repository.py`, change the top imports (lines 1-16) from:

```python
from collections import defaultdict
from datetime import datetime
from typing import Optional
import uuid

import sqlalchemy
from sqlalchemy import select, tuple_, distinct, and_, union_all, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import (
    Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters,
    ImageDescription, ImageDescriptionEmbedding,
)
from graph.uf import UnionFind
```

to:

```python
from collections import defaultdict
from datetime import datetime
from typing import Optional
import uuid

import sqlalchemy
from sqlalchemy import select, tuple_, distinct, and_, union_all, func, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import (
    Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters,
    ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback,
)
from graph.uf import UnionFind
```

Then replace the existing `get_descriptions` method (currently lines 177-188):

```python
    async def get_descriptions(self, image_id: str):
        result = await self.session.execute(
            select(
                ImageDescription.prompt_key,
                ImageDescription.text,
                ImageDescription.model_used,
                ImageDescription.created_at,
            )
            .where(ImageDescription.image_id == image_id)
            .order_by(ImageDescription.prompt_key)
        )
        return result.all()
```

with:

```python
    async def get_descriptions(self, image_id: str):
        result = await self.session.execute(
            select(
                ImageDescription.prompt_key,
                ImageDescription.text,
                ImageDescription.model_used,
                ImageDescription.created_at,
                ImageDescriptionFeedback.approved,
            )
            .outerjoin(
                ImageDescriptionFeedback,
                ImageDescriptionFeedback.image_description_id == ImageDescription.id,
            )
            .where(ImageDescription.image_id == image_id)
            .order_by(ImageDescription.prompt_key)
        )
        return result.all()

    async def get_description_id(self, image_id: str, prompt_key: str) -> Optional[uuid.UUID]:
        result = await self.session.execute(
            select(ImageDescription.id)
            .where(ImageDescription.image_id == image_id, ImageDescription.prompt_key == prompt_key)
        )
        return result.scalar_one_or_none()

    async def get_description_feedback(self, description_id) -> Optional[bool]:
        result = await self.session.execute(
            select(ImageDescriptionFeedback.approved)
            .where(ImageDescriptionFeedback.image_description_id == description_id)
        )
        return result.scalar_one_or_none()

    async def set_description_feedback(self, description_id, approved: bool) -> None:
        stmt = (
            insert(ImageDescriptionFeedback)
            .values(image_description_id=description_id, approved=approved)
            .on_conflict_do_update(
                index_elements=["image_description_id"],
                set_={"approved": approved},
            )
        )
        await self.session.execute(stmt)

    async def clear_description_feedback(self, description_id) -> None:
        await self.session.execute(
            delete(ImageDescriptionFeedback)
            .where(ImageDescriptionFeedback.image_description_id == description_id)
        )
```

This mirrors `set_flagged`'s existing `insert(...).on_conflict_do_update(...)` pattern (same file, `set_flagged` method) for the upsert, and is a plain `delete()` for the clear case — no precedent needed, `delete` is now imported above.

### Service

- [ ] **Step 2: Update `get_descriptions` mapping and add toggle methods**

In `Backend/app/services/image_service.py`, replace the existing `get_descriptions` method (currently lines 111-121):

```python
    async def get_descriptions(self, image_id: str) -> list[ImageDescription]:
        rows = await self.repo.get_descriptions(image_id)
        return [
            ImageDescription(
                promptKey=prompt_key,
                text=text,
                modelUsed=model_used,
                createdAt=created_at.isoformat(),
            )
            for prompt_key, text, model_used, created_at in rows
        ]
```

with:

```python
    async def get_descriptions(self, image_id: str) -> list[ImageDescription]:
        rows = await self.repo.get_descriptions(image_id)
        return [
            ImageDescription(
                promptKey=prompt_key,
                text=text,
                modelUsed=model_used,
                createdAt=created_at.isoformat(),
                feedback=_feedback_label(approved),
            )
            for prompt_key, text, model_used, created_at, approved in rows
        ]

    async def approve_description_feedback(self, image_id: str, prompt_key: str) -> Optional[str]:
        return await self._toggle_description_feedback(image_id, prompt_key, target_approved=True)

    async def reject_description_feedback(self, image_id: str, prompt_key: str) -> Optional[str]:
        return await self._toggle_description_feedback(image_id, prompt_key, target_approved=False)

    async def _toggle_description_feedback(
        self, image_id: str, prompt_key: str, target_approved: bool
    ) -> Optional[str]:
        description_id = await self.repo.get_description_id(image_id, prompt_key)
        if description_id is None:
            raise HTTPException(status_code=404, detail="Description not found")

        current = await self.repo.get_description_feedback(description_id)
        if current is target_approved:
            await self.repo.clear_description_feedback(description_id)
            return None

        await self.repo.set_description_feedback(description_id, target_approved)
        return _feedback_label(target_approved)
```

Then add the module-level helper function `_feedback_label`, placed right after the imports and before `class ImageService:`:

```python
def _feedback_label(approved: Optional[bool]) -> Optional[str]:
    if approved is None:
        return None
    return "approved" if approved else "rejected"
```

`Optional` is already imported (`from typing import Optional`, line 6) and `HTTPException` is already imported (line 8) — no new imports needed in this file.

### Shared schema

- [ ] **Step 3: Add `feedback` to `ImageDescription`**

In `shared/schemas/imagedescription.schema.json`, change:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "imagedescription.schema.json",
  "title": "ImageDescription",
  "type": "object",
  "properties": {
    "promptKey": {
      "type": "string",
      "description": "Identifies which configured prompt produced this description (see image_descriptions.prompts_file)"
    },
    "text": { "type": "string" },
    "modelUsed": {
      "type": "string",
      "description": "The Ollama model that generated this text"
    },
    "createdAt": { "type": "string" }
  },
  "required": ["promptKey", "text", "modelUsed", "createdAt"]
}
```

to:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "imagedescription.schema.json",
  "title": "ImageDescription",
  "type": "object",
  "properties": {
    "promptKey": {
      "type": "string",
      "description": "Identifies which configured prompt produced this description (see image_descriptions.prompts_file)"
    },
    "text": { "type": "string" },
    "modelUsed": {
      "type": "string",
      "description": "The Ollama model that generated this text"
    },
    "createdAt": { "type": "string" },
    "feedback": {
      "type": "string",
      "description": "\"approved\" or \"rejected\" if a human has reviewed this description; absent/null if no feedback given yet"
    }
  },
  "required": ["promptKey", "text", "modelUsed", "createdAt"]
}
```

(`feedback` deliberately left out of `required`, matching how `Meme.flagged` is optional in `meme.schema.json` — this generates an optional/nullable field on every platform, not a required-nullable one.)

- [ ] **Step 4: Create the new response schema**

Create `shared/schemas/descriptionfeedbackresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "descriptionfeedbackresponse.schema.json",
  "title": "DescriptionFeedbackResponse",
  "type": "object",
  "properties": {
    "feedback": {
      "type": "string",
      "description": "Resulting state after the toggle: \"approved\", \"rejected\", or absent/null if cleared back to no feedback"
    }
  },
  "required": []
}
```

- [ ] **Step 5: Register the new schema in `all.schema.json`**

In `shared/schemas/all.schema.json`, change:

```json
    "ImageDescription":    { "$ref": "imagedescription.schema.json" },
```

to:

```json
    "ImageDescription":    { "$ref": "imagedescription.schema.json" },
    "DescriptionFeedbackResponse": { "$ref": "descriptionfeedbackresponse.schema.json" },
```

### Backend type regeneration

- [ ] **Step 6: Regenerate Backend generated types**

Run from `Backend/` (the exact command from `Backend/generate-types.sh`):

```powershell
cd Backend
..\.venv311\Scripts\datamodel-codegen.exe --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```

Expected: `Backend/app/types/generated/imagedescription.py` gains a `feedback: str | None = None` field, and a new `Backend/app/types/generated/descriptionfeedbackresponse.py` appears with `class Schema(BaseModel): feedback: str | None = None`.

- [ ] **Step 7: Diff-check the regeneration**

```bash
git diff Backend/app/types/generated/
git status --short Backend/app/types/generated/
```

Confirm only `imagedescription.py` changed (new `feedback` field) and `descriptionfeedbackresponse.py` was added — no unrelated files touched (regeneration timestamps in file headers are expected to change only for files whose content actually changed, since `datamodel-codegen` skips untouched files' timestamps... if it rewrites timestamps on every file regardless of content change, that's fine too, just confirm no *content* other than the intended fields changed).

### Router

- [ ] **Step 8: Add the two new endpoints**

In `Backend/app/api/images.py`, add this import alongside the existing generated-type imports (after line 20, `from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse`):

```python
from Backend.app.types.generated.descriptionfeedbackresponse import Schema as DescriptionFeedbackResponse
```

Then add two new endpoints immediately after `get_image_descriptions` (after line 73, before `@router.get("/meme/{image_id}", response_model=Meme)`):

```python
@router.put("/{image_id}/descriptions/{prompt_key}/approve", response_model=DescriptionFeedbackResponse)
async def approve_description(
    image_id: str,
    prompt_key: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    feedback = await service.approve_description_feedback(image_id, prompt_key)
    return DescriptionFeedbackResponse(feedback=feedback)


@router.put("/{image_id}/descriptions/{prompt_key}/reject", response_model=DescriptionFeedbackResponse)
async def reject_description(
    image_id: str,
    prompt_key: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    feedback = await service.reject_description_feedback(image_id, prompt_key)
    return DescriptionFeedbackResponse(feedback=feedback)
```

### backend_api.md

- [ ] **Step 9: Document the `feedback` field and the two new endpoints**

In `backend_api.md`, change the `Get Image Descriptions` section's Response line:

```
- **Response**: `ImageDescription[]` — `{ promptKey, text, modelUsed, createdAt }` per entry. An image with no descriptions yet returns `200 []`, never `404`.
```

to:

```
- **Response**: `ImageDescription[]` — `{ promptKey, text, modelUsed, createdAt, feedback }` per entry (`feedback` is `"approved"`, `"rejected"`, or absent/null). An image with no descriptions yet returns `200 []`, never `404`.
```

Then insert two new subsections immediately after the `Get Image Descriptions` section (after its `- **Example**:` line, before `#### Get Meme Details`):

```markdown
#### Approve Image Description

Record a human "approved" judgment on one AI-generated description. Toggles:
calling this when the description is already approved clears the feedback
back to no-feedback instead of re-approving.

- **URL**: `/api/images/{image_id}/descriptions/{prompt_key}/approve`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
  - `prompt_key`: Identifies which description (see `image_descriptions.prompts_file`)
- **Response**: `DescriptionFeedbackResponse` — `{ feedback: "approved" | "rejected" | null }`, the resulting state. `404` if no description exists for that `(image_id, prompt_key)` pair.
- **Example**: `PUT /api/images/abc123/descriptions/general_description/approve`

#### Reject Image Description

Record a human "rejected" judgment on one AI-generated description. Toggles
the same way as Approve, in the opposite direction. Does **not** hide the
description or exclude it from semantic-similarity search — this is a pure
feedback signal.

- **URL**: `/api/images/{image_id}/descriptions/{prompt_key}/reject`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
  - `prompt_key`: Identifies which description (see `image_descriptions.prompts_file`)
- **Response**: `DescriptionFeedbackResponse` — `{ feedback: "approved" | "rejected" | null }`, the resulting state. `404` if no description exists for that `(image_id, prompt_key)` pair.
- **Example**: `PUT /api/images/abc123/descriptions/general_description/reject`
```

### Tests

- [ ] **Step 10: Write service-level tests**

In `Backend/tests/test_image_service.py`, add `from datetime import datetime` to the top import block (after `import pytest`), then append these new test classes at the end of the file:

```python
class TestGetDescriptionsFeedback:
    async def test_feedback_is_none_when_no_row(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), None),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback is None

    async def test_feedback_approved_maps_to_string(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), True),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback == "approved"

    async def test_feedback_rejected_maps_to_string(self, service, mock_repo):
        mock_repo.get_descriptions.return_value = [
            ("general_description", "A cat.", "llava", datetime(2026, 7, 19, 12, 0, 0), False),
        ]

        result = await service.get_descriptions("image-1")

        assert result[0].feedback == "rejected"


class TestApproveDescriptionFeedback:
    async def test_raises_404_when_description_missing(self, service, mock_repo):
        mock_repo.get_description_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.approve_description_feedback("image-1", "unknown_prompt")

        assert exc_info.value.status_code == 404
        mock_repo.set_description_feedback.assert_not_called()

    async def test_approve_when_no_prior_feedback_sets_approved(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = None

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", True)
        mock_repo.clear_description_feedback.assert_not_called()
        assert result == "approved"

    async def test_approve_when_already_approved_clears(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = True

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.clear_description_feedback.assert_awaited_once_with("desc-uuid-1")
        mock_repo.set_description_feedback.assert_not_called()
        assert result is None

    async def test_approve_when_currently_rejected_switches(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = False

        result = await service.approve_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", True)
        assert result == "approved"


class TestRejectDescriptionFeedback:
    async def test_raises_404_when_description_missing(self, service, mock_repo):
        mock_repo.get_description_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.reject_description_feedback("image-1", "unknown_prompt")

        assert exc_info.value.status_code == 404

    async def test_reject_when_no_prior_feedback_sets_rejected(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = None

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", False)
        assert result == "rejected"

    async def test_reject_when_already_rejected_clears(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = False

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.clear_description_feedback.assert_awaited_once_with("desc-uuid-1")
        mock_repo.set_description_feedback.assert_not_called()
        assert result is None

    async def test_reject_when_currently_approved_switches(self, service, mock_repo):
        mock_repo.get_description_id.return_value = "desc-uuid-1"
        mock_repo.get_description_feedback.return_value = True

        result = await service.reject_description_feedback("image-1", "general_description")

        mock_repo.set_description_feedback.assert_awaited_once_with("desc-uuid-1", False)
        assert result == "rejected"
```

- [ ] **Step 11: Run the service tests**

```bash
cd Backend
python -m pytest tests/test_image_service.py -v
```

Expected: all tests pass, including the pre-existing `TestGetSimilarImageMode`/`TestGetSimilarDescriptionMode` classes and the new ones above.

- [ ] **Step 12: Write endpoint-level tests**

In `Backend/tests/test_images_endpoints.py`, update the existing `TestGetImageDescriptions.test_get_image_descriptions_success` test — its `ImageDescription(...)` construction (currently lines 534-539) needs a `feedback` value since the field now exists on the type (it's optional, so omitting it is also valid, but explicitly asserting it keeps the test meaningful). Change:

```python
    def test_get_image_descriptions_success(self, client, mock_image_service):
        mock_image_service.get_descriptions.return_value = [
            ImageDescription(
                promptKey="general_description",
                text="A cat wearing a hat.",
                modelUsed="qwen2.5vl:7b",
                createdAt="2026-07-18T12:00:00",
            )
        ]

        response = client.get("/api/images/123/descriptions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["promptKey"] == "general_description"
        assert data[0]["text"] == "A cat wearing a hat."
        assert data[0]["modelUsed"] == "qwen2.5vl:7b"
        mock_image_service.get_descriptions.assert_called_once_with("123")
```

to:

```python
    def test_get_image_descriptions_success(self, client, mock_image_service):
        mock_image_service.get_descriptions.return_value = [
            ImageDescription(
                promptKey="general_description",
                text="A cat wearing a hat.",
                modelUsed="qwen2.5vl:7b",
                createdAt="2026-07-18T12:00:00",
                feedback="approved",
            )
        ]

        response = client.get("/api/images/123/descriptions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["promptKey"] == "general_description"
        assert data[0]["text"] == "A cat wearing a hat."
        assert data[0]["modelUsed"] == "qwen2.5vl:7b"
        assert data[0]["feedback"] == "approved"
        mock_image_service.get_descriptions.assert_called_once_with("123")

    def test_get_image_descriptions_feedback_absent_when_none(self, client, mock_image_service):
        mock_image_service.get_descriptions.return_value = [
            ImageDescription(
                promptKey="general_description",
                text="A cat wearing a hat.",
                modelUsed="qwen2.5vl:7b",
                createdAt="2026-07-18T12:00:00",
            )
        ]

        response = client.get("/api/images/123/descriptions")

        assert response.status_code == 200
        assert response.json()[0]["feedback"] is None
```

Then append these new test classes at the end of the file:

```python
class TestApproveDescription:
    """Tests for PUT /api/images/{image_id}/descriptions/{prompt_key}/approve endpoint."""

    def test_approve_success(self, client, mock_image_service):
        mock_image_service.approve_description_feedback.return_value = "approved"

        response = client.put("/api/images/123/descriptions/general_description/approve")

        assert response.status_code == 200
        assert response.json() == {"feedback": "approved"}
        mock_image_service.approve_description_feedback.assert_called_once_with("123", "general_description")

    def test_approve_toggle_clears(self, client, mock_image_service):
        mock_image_service.approve_description_feedback.return_value = None

        response = client.put("/api/images/123/descriptions/general_description/approve")

        assert response.status_code == 200
        assert response.json() == {"feedback": None}

    def test_approve_not_found(self, client, mock_image_service):
        from fastapi import HTTPException
        mock_image_service.approve_description_feedback.side_effect = HTTPException(
            status_code=404, detail="Description not found"
        )

        response = client.put("/api/images/123/descriptions/unknown_prompt/approve")

        assert response.status_code == 404


class TestRejectDescription:
    """Tests for PUT /api/images/{image_id}/descriptions/{prompt_key}/reject endpoint."""

    def test_reject_success(self, client, mock_image_service):
        mock_image_service.reject_description_feedback.return_value = "rejected"

        response = client.put("/api/images/123/descriptions/general_description/reject")

        assert response.status_code == 200
        assert response.json() == {"feedback": "rejected"}
        mock_image_service.reject_description_feedback.assert_called_once_with("123", "general_description")

    def test_reject_toggle_clears(self, client, mock_image_service):
        mock_image_service.reject_description_feedback.return_value = None

        response = client.put("/api/images/123/descriptions/general_description/reject")

        assert response.status_code == 200
        assert response.json() == {"feedback": None}

    def test_reject_not_found(self, client, mock_image_service):
        from fastapi import HTTPException
        mock_image_service.reject_description_feedback.side_effect = HTTPException(
            status_code=404, detail="Description not found"
        )

        response = client.put("/api/images/123/descriptions/unknown_prompt/reject")

        assert response.status_code == 404
```

- [ ] **Step 13: Run the endpoint tests**

```bash
cd Backend
python -m pytest tests/test_images_endpoints.py -v
```

Expected: all tests pass, including the pre-existing classes and the new ones above.

- [ ] **Step 14: Smoke-test the running server**

Confirm the server still starts without import errors and the existing smoke-test endpoints still respond (per this repo's "Before committing backend changes" convention):

```powershell
set WATCHFILES_FORCE_POLLING=1
..\.venv311\Scripts\uvicorn.exe Backend.app.main:app --env-file environments/.env.general --port 8092 --host 0.0.0.0
```

In another shell: `curl http://localhost:8092/api/diagnostics/health` and `curl "http://localhost:8092/api/images?limit=1"` both return `200`. Use port `8092` (not `8082`) since `8082` is the developer's always-running `general` environment — see `environments/Environments.md`. Stop the temporary server afterward.

- [ ] **Step 15: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py \
  Backend/app/api/images.py Backend/app/types/generated/ \
  shared/schemas/imagedescription.schema.json shared/schemas/descriptionfeedbackresponse.schema.json \
  shared/schemas/all.schema.json backend_api.md \
  Backend/tests/test_image_service.py Backend/tests/test_images_endpoints.py
git commit -m "feat: add description approve/reject feedback endpoints"
```

---

## Task 3: Statistics

**Files:**
- Modify: `Backend/app/repositories/diagnostics_repository.py` (imports; `get_statistics` query)
- Modify: `Backend/app/api/diagnostics.py` (`ContentStats` model; `statistics` endpoint construction)
- Modify: `backend_api.md` (Statistics section)
- Create: `Backend/tests/test_diagnostics_endpoints.py`

**Interfaces:**
- Consumes: `ImageDescriptionFeedback` model from Task 1.
- Produces: `StatisticsResponse.content` gains `descriptions_approved: int`, `descriptions_rejected: int`, `descriptions_feedback_total: int`.

- [ ] **Step 1: Add the three new subqueries**

In `Backend/app/repositories/diagnostics_repository.py`, change the import (line 1) from:

```python
from sqlalchemy import exists, func, select, text, true
```

to (no change needed — `true` is already imported and sufficient), but change the `Storage.models` import (lines 4-8) from:

```python
from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, ImageDescription, TmpImageClusters, TrendsRun, TrendSource,
)
```

to:

```python
from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, ImageDescription, ImageDescriptionFeedback, TmpImageClusters, TrendsRun, TrendSource,
)
```

Then in the `get_statistics` method, add three more scalar subqueries to the `select(...)` call, right after the `trend_sources` one (the last one, currently ending the tuple at line 64):

```python
                select(func.count()).select_from(TrendSource)
                    .scalar_subquery().label("trend_sources"),
```

becomes:

```python
                select(func.count()).select_from(TrendSource)
                    .scalar_subquery().label("trend_sources"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .where(ImageDescriptionFeedback.approved == true())
                    .scalar_subquery().label("descriptions_approved"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .where(ImageDescriptionFeedback.approved == false())
                    .scalar_subquery().label("descriptions_rejected"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .scalar_subquery().label("descriptions_feedback_total"),
```

This needs `false` imported alongside the existing `true` import — change line 1 from:

```python
from sqlalchemy import exists, func, select, text, true
```

to:

```python
from sqlalchemy import exists, func, select, text, true, false
```

- [ ] **Step 2: Surface the new fields in the API response**

In `Backend/app/api/diagnostics.py`, change the `ContentStats` model (currently lines 29-36):

```python
class ContentStats(BaseModel):
    ocr_texts: int
    tags: int
    tag_keys: int
    tag_values: int
    concepts: int
    concept_image_sets: int
    concept_images: int
```

to:

```python
class ContentStats(BaseModel):
    ocr_texts: int
    tags: int
    tag_keys: int
    tag_values: int
    concepts: int
    concept_image_sets: int
    concept_images: int
    descriptions_approved: int
    descriptions_rejected: int
    descriptions_feedback_total: int
```

Then change the `statistics` endpoint's `ContentStats(...)` construction (currently lines 80-87):

```python
        content=ContentStats(
            ocr_texts=row.ocr_texts,
            tags=row.tags,
            tag_keys=row.tag_keys,
            tag_values=row.tag_values,
            concepts=row.concepts,
            concept_image_sets=row.concept_image_sets,
            concept_images=row.concept_images,
        ),
```

to:

```python
        content=ContentStats(
            ocr_texts=row.ocr_texts,
            tags=row.tags,
            tag_keys=row.tag_keys,
            tag_values=row.tag_values,
            concepts=row.concepts,
            concept_image_sets=row.concept_image_sets,
            concept_images=row.concept_images,
            descriptions_approved=row.descriptions_approved,
            descriptions_rejected=row.descriptions_rejected,
            descriptions_feedback_total=row.descriptions_feedback_total,
        ),
```

- [ ] **Step 3: Also update the shared schema for Web/Android consistency**

In `shared/schemas/statisticscontentstats.schema.json`, change:

```json
    "concept_images":     { "type": "integer", "description": "Individual reference images across all concept sets" }
  },
  "required": ["ocr_texts", "tags", "tag_keys", "tag_values", "concepts", "concept_image_sets", "concept_images"]
```

to:

```json
    "concept_images":     { "type": "integer", "description": "Individual reference images across all concept sets" },
    "descriptions_approved":      { "type": "integer", "description": "Descriptions with approved human feedback" },
    "descriptions_rejected":      { "type": "integer", "description": "Descriptions with rejected human feedback" },
    "descriptions_feedback_total": { "type": "integer", "description": "Descriptions with any human feedback (approved + rejected)" }
  },
  "required": ["ocr_texts", "tags", "tag_keys", "tag_values", "concepts", "concept_image_sets", "concept_images", "descriptions_approved", "descriptions_rejected", "descriptions_feedback_total"]
```

(Backend's own response model is the hand-rolled `ContentStats` in `diagnostics.py` edited above — it does not read this schema file. This schema-file edit exists purely so Web's and Android's regenerated `StatisticsResponse` types gain the same three fields; see Task 4 and Task 5.)

- [ ] **Step 4: Update `backend_api.md`**

In `backend_api.md`'s Statistics section's JSON example, change:

```json
  "content": {
    "ocr_texts": 31000,
    "tags": 48500,
    "tag_keys": 12,
    "tag_values": 340,
    "concepts": 47,
    "concept_image_sets": 63,
    "concept_images": 14200
  },
```

to:

```json
  "content": {
    "ocr_texts": 31000,
    "tags": 48500,
    "tag_keys": 12,
    "tag_values": 340,
    "concepts": 47,
    "concept_image_sets": 63,
    "concept_images": 14200,
    "descriptions_approved": 210,
    "descriptions_rejected": 34,
    "descriptions_feedback_total": 244
  },
```

- [ ] **Step 5: Write endpoint tests**

Create `Backend/tests/test_diagnostics_endpoints.py`:

```python
"""
Tests for the diagnostics endpoints.
Endpoints tested:
- health
- statistics (including the description-feedback counts)
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from Backend.app.api.diagnostics import router as diagnostics_router

app = FastAPI()
app.include_router(diagnostics_router, prefix="/api")


@pytest.fixture
def mock_diagnostics_repo():
    return AsyncMock()


@pytest.fixture
def client(mock_diagnostics_repo):
    async def override_get_diagnostics_repo():
        yield mock_diagnostics_repo

    from Backend.app.api.diagnostics import get_diagnostics_repo
    app.dependency_overrides[get_diagnostics_repo] = override_get_diagnostics_repo

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _fake_stats_row(**overrides):
    defaults = dict(
        total_memes=100, with_embeddings=90, with_ocr=80, with_tags=70,
        without_tags=30, with_descriptions=60, with_concept_tags=40,
        flagged=5, duplicate_clusters=3,
        ocr_texts=200, tags=300, concepts=10, concept_image_sets=12,
        concept_images=150,
        tag_keys=8, tag_values=90,
        trends_runs=4, trend_sources=2,
        descriptions_approved=21, descriptions_rejected=3, descriptions_feedback_total=24,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStatistics:
    def test_statistics_includes_description_feedback_counts(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row()

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["content"]["descriptions_approved"] == 21
        assert data["content"]["descriptions_rejected"] == 3
        assert data["content"]["descriptions_feedback_total"] == 24

    def test_statistics_zero_feedback(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(
            descriptions_approved=0, descriptions_rejected=0, descriptions_feedback_total=0,
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["content"]["descriptions_approved"] == 0
        assert data["content"]["descriptions_rejected"] == 0
        assert data["content"]["descriptions_feedback_total"] == 0
```

- [ ] **Step 6: Run the new tests**

```bash
cd Backend
python -m pytest tests/test_diagnostics_endpoints.py -v
```

Expected: both tests pass.

- [ ] **Step 7: Run the full Backend test suite as a regression check**

```bash
cd Backend
python -m pytest
```

Expected: all tests pass (no `tests/integration/` in this invocation — see this repo's CLAUDE.md gotcha about not combining test roots).

- [ ] **Step 8: Commit**

```bash
git add Backend/app/repositories/diagnostics_repository.py Backend/app/api/diagnostics.py \
  shared/schemas/statisticscontentstats.schema.json backend_api.md \
  Backend/tests/test_diagnostics_endpoints.py
git commit -m "feat: surface description feedback counts in statistics endpoint"
```

---

## Task 4: Web wiring

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts` (interface)
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts` (implementation)
- Run: `Frontend/generate-types.sh` (regenerates `Frontend/memes-frontend/src/types/generated/all.d.ts`)
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts` (add mock method)
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.tsx` (inline buttons + handler)
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.test.tsx` (new tests)

**Interfaces:**
- Consumes: `PUT /api/images/{id}/descriptions/{promptKey}/approve` and `.../reject` (Task 2); `ImageDescription.feedback` (Task 2, regenerated type).
- Produces: `MemesApi.setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }>` (`feedback` absent/undefined means cleared, matching how the regenerated `ImageDescription.feedback` type is also optional rather than a `| null` union — see Step 1)

- [ ] **Step 1: Regenerate Web types**

Run from `Frontend/`:

```bash
bash generate-types.sh
```

Then verify no unexpected diff beyond the intended change:

```bash
git diff Frontend/memes-frontend/src/types/generated/all.d.ts
```

Expected: `ImageDescription` gains `feedback?: string;`, and a new `DescriptionFeedbackResponse` interface (`{ feedback?: string; }`) appears in the file.

- [ ] **Step 2: Add the method to the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, change:

```ts
  getDescriptions(id: string): Promise<ImageDescription[]>
```

to:

```ts
  getDescriptions(id: string): Promise<ImageDescription[]>

  setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }>
```

- [ ] **Step 3: Implement it in `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, add this method right after `getDescriptions` (after its closing `}`, before `getImageUrl`):

```ts
  async setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${imageId}/descriptions/${promptKey}/${action}`,
      { method: "PUT", headers: { "Accept": "application/json" } }
    )

    if (!response.ok) {
      throw new Error(`Failed to set description feedback: ${response.status}`)
    }

    return response.json()
  }
```

- [ ] **Step 4: Add the mock to `mockApi.ts`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, add to the object inside `makeMockApi` (after the `getImageIsFlagged` line):

```ts
    getImageIsFlagged: vi.fn().mockResolvedValue(false),
    setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: undefined }),
```

- [ ] **Step 5: Wire the UI into `MemeDetails.tsx`**

In `Frontend/memes-frontend/src/components/MemeDetails.tsx`, add a handler function right after `toggleFlagged` (after its closing `}`, before `bumpControls`):

```tsx
  function setDescriptionFeedback(promptKey: string, action: "approve" | "reject") {
    memesApi.setDescriptionFeedback(meme.id, promptKey, action).then(resp => {
      setDescriptions(prev => prev.map(d =>
        d.promptKey === promptKey ? { ...d, feedback: resp.feedback } : d
      ))
    })
  }
```

Then change the descriptions rendering block (currently lines 196-209):

```tsx
        <div>
          <strong>Descriptions:</strong>
          {descriptions.length === 0 ? (
            <p className="text-gray-400">No description available</p>
          ) : (
            <ul className="ml-2 space-y-2">
              {descriptions.map(d => (
                <li key={d.promptKey}>
                  <span className="font-medium">{humanizePromptKey(d.promptKey)}:</span> {d.text}
                </li>
              ))}
            </ul>
          )}
        </div>
```

to:

```tsx
        <div>
          <strong>Descriptions:</strong>
          {descriptions.length === 0 ? (
            <p className="text-gray-400">No description available</p>
          ) : (
            <ul className="ml-2 space-y-2">
              {descriptions.map(d => (
                <li key={d.promptKey}>
                  <span className="font-medium">{humanizePromptKey(d.promptKey)}:</span> {d.text}
                  <button
                    onClick={() => setDescriptionFeedback(d.promptKey, "approve")}
                    aria-label={`Approve ${humanizePromptKey(d.promptKey)}`}
                    className={`ml-2 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "approved" ? "bg-green-600 text-white border-green-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                  >
                    👍
                  </button>
                  <button
                    onClick={() => setDescriptionFeedback(d.promptKey, "reject")}
                    aria-label={`Reject ${humanizePromptKey(d.promptKey)}`}
                    className={`ml-1 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "rejected" ? "bg-red-600 text-white border-red-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                  >
                    👎
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
```

- [ ] **Step 6: Write the failing tests**

In `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`, add a new `describe` block right after the existing `describe('descriptions', ...)` block (after its closing `})`, before `describe('similarity mode toggle', ...)`):

```tsx
  describe('description feedback', () => {
    it('renders Approve/Reject buttons per description', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByLabelText('Approve General description')).toBeInTheDocument()
        expect(screen.getByLabelText('Reject General description')).toBeInTheDocument()
      })
    })

    it('clicking Approve calls the API and updates the button state from the response', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'approved' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(api.setDescriptionFeedback).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'general_description', 'approve')
      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')
    })

    it('clicking the already-approved button again clears feedback', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00', feedback: 'approved' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: undefined }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(screen.getByLabelText('Approve General description')).not.toHaveClass('bg-green-600')
    })

    it('clicking Reject while approved switches directly to rejected', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00', feedback: 'approved' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'rejected' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Reject General description').click()
      await act(async () => {})

      expect(api.setDescriptionFeedback).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'general_description', 'reject')
      expect(screen.getByLabelText('Reject General description')).toHaveClass('bg-red-600')
      expect(screen.getByLabelText('Approve General description')).not.toHaveClass('bg-green-600')
    })

    it('feedback on one description does not affect another', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
          { promptKey: 'humor_explanation', text: 'Because cats.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'approved' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')
      expect(screen.getByLabelText('Approve Humor explanation')).not.toHaveClass('bg-green-600')
    })
  })
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd Frontend/memes-frontend
npx vitest run src/components/MemeDetails.test.tsx
```

Expected: all tests pass, including the pre-existing ones and the 5 new ones above.

- [ ] **Step 8: Run the full pre-commit gate**

```bash
cd Frontend/memes-frontend
npx tsc -b
npx eslint src/ --max-warnings 0
npx vitest run
```

Expected: `tsc -b` prints nothing (clean), `eslint` reports 0 warnings, `vitest run` shows all test files passing.

- [ ] **Step 9: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts \
  Frontend/memes-frontend/src/types/generated/all.d.ts Frontend/memes-frontend/src/test/mockApi.ts \
  Frontend/memes-frontend/src/components/MemeDetails.tsx Frontend/memes-frontend/src/components/MemeDetails.test.tsx
git commit -m "feat: add description approve/reject buttons to Web"
```

---

## Task 5: Android wiring

**Files:**
- Run: `AndroidClient/scripts/generate_dtos.py` (regenerates `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`)
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt` (call site)
- Modify: `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`

**Interfaces:**
- Consumes: same two endpoints as Task 4; `ImageDescription.feedback` (Task 2, regenerated `Models.kt`).
- Produces:
  - `MemeApiService.approveDescription(id: String, promptKey: String): DescriptionFeedbackResponse`
  - `MemeApiService.rejectDescription(id: String, promptKey: String): DescriptionFeedbackResponse`
  - `MemeRepository.setDescriptionFeedback(id: String, promptKey: String, action: String): Result<DescriptionFeedbackResponse>`
  - `MemeDetailViewModel.setDescriptionFeedback(promptKey: String, action: String)`

- [ ] **Step 1: Regenerate Android DTOs**

Run from repo root:

```powershell
python AndroidClient/scripts/generate_dtos.py
```

Then verify:

```bash
git diff AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
```

Expected: `ImageDescription` data class gains `@SerialName("feedback") val feedback: String? = null`, and a new `DescriptionFeedbackResponse` data class appears (`@SerialName("feedback") val feedback: String? = null`).

- [ ] **Step 2: Add the two Retrofit methods**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`, add the import (alongside the existing `com.memebrowser.app.data.model.ImageDescription` import):

```kotlin
import com.memebrowser.app.data.model.DescriptionFeedbackResponse
```

Then add two new methods right after `getDescriptions` (after its line, before `@Streaming`):

```kotlin
    @PUT("api/images/{id}/descriptions/{promptKey}/approve")
    suspend fun approveDescription(@Path("id") id: String, @Path("promptKey") promptKey: String): DescriptionFeedbackResponse

    @PUT("api/images/{id}/descriptions/{promptKey}/reject")
    suspend fun rejectDescription(@Path("id") id: String, @Path("promptKey") promptKey: String): DescriptionFeedbackResponse
```

- [ ] **Step 3: Add the repository method**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`, add the import:

```kotlin
import com.memebrowser.app.data.model.DescriptionFeedbackResponse
```

Then add a new method right after `getDescriptions` (after its closing `}`, before `uploadImages`):

```kotlin
    suspend fun setDescriptionFeedback(id: String, promptKey: String, action: String): Result<DescriptionFeedbackResponse> = runCatching {
        if (action == "approve") api.approveDescription(id, promptKey) else api.rejectDescription(id, promptKey)
    }
```

- [ ] **Step 4: Add the ViewModel method**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt`, add a new public function right after `setSimilarSource` (after its closing `}`, before `saveToGallery`):

```kotlin
    fun setDescriptionFeedback(promptKey: String, action: String) {
        viewModelScope.launch {
            repo.setDescriptionFeedback(memeId, promptKey, action)
                .onSuccess { resp ->
                    _state.update { current ->
                        current.copy(
                            descriptions = current.descriptions.map { d ->
                                if (d.promptKey == promptKey) d.copy(feedback = resp.feedback) else d
                            }
                        )
                    }
                }
        }
    }
```

- [ ] **Step 5: Wire the UI into `DescriptionsBottomSheet.kt`**

Replace the full file content of `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt`:

```kotlin
package com.memebrowser.app.ui.detail

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.memebrowser.app.data.model.ImageDescription

private fun humanizePromptKey(promptKey: String): String {
    val spaced = promptKey.replace('_', ' ')
    return spaced.replaceFirstChar { it.uppercase() }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DescriptionsBottomSheet(
    descriptions: List<ImageDescription>,
    onDismiss: () -> Unit,
    onFeedback: (promptKey: String, action: String) -> Unit = { _, _ -> }
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 32.dp)
        ) {
            Text(
                text = "Description",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(bottom = 8.dp)
            )
            if (descriptions.isEmpty()) {
                Text(
                    text = "No description available",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                descriptions.forEach { description ->
                    Row(modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                        Text(
                            text = humanizePromptKey(description.promptKey),
                            style = MaterialTheme.typography.labelLarge,
                            modifier = Modifier.weight(1f)
                        )
                        IconButton(onClick = { onFeedback(description.promptKey, "approve") }) {
                            Icon(
                                Icons.Filled.ThumbUp,
                                contentDescription = "Approve ${humanizePromptKey(description.promptKey)}",
                                tint = if (description.feedback == "approved") Color(0xFF2E7D32) else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        IconButton(onClick = { onFeedback(description.promptKey, "reject") }) {
                            Icon(
                                Icons.Filled.ThumbDown,
                                contentDescription = "Reject ${humanizePromptKey(description.promptKey)}",
                                tint = if (description.feedback == "rejected") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                    Text(
                        text = description.text,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
        }
    }
}
```

- [ ] **Step 6: Wire the call site in `MemeDetailScreen.kt`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt`, change:

```kotlin
        if (showDescriptions) {
            DescriptionsBottomSheet(
                descriptions = state.descriptions,
                onDismiss = { showDescriptions = false }
            )
        }
```

to:

```kotlin
        if (showDescriptions) {
            DescriptionsBottomSheet(
                descriptions = state.descriptions,
                onDismiss = { showDescriptions = false },
                onFeedback = { promptKey, action -> viewModel.setDescriptionFeedback(promptKey, action) }
            )
        }
```

- [ ] **Step 7: Write the failing tests**

In `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`, append this new test class at the end of the file, right before the file's final closing (after the `TestGetDescriptionsFeedback`-equivalent isn't needed here — just append after the last existing test, inside the `MemeDetailViewModelTest` class, before its closing `}`):

```kotlin
    @Test
    fun `setDescriptionFeedback calls repo and updates matching description in state`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00"),
            ImageDescription(promptKey = "humor_explanation", text = "Because cats.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00"),
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        coEvery { repo.setDescriptionFeedback("meme-1", "general_description", "approve") } returns
            Result.success(DescriptionFeedbackResponse(feedback = "approved"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setDescriptionFeedback("general_description", "approve")

        viewModel.state.test {
            val state = awaitItem()
            val updated = state.descriptions.first { it.promptKey == "general_description" }
            val untouched = state.descriptions.first { it.promptKey == "humor_explanation" }
            assertEquals("approved", updated.feedback)
            assertNull(untouched.feedback)
        }
        coVerify(exactly = 1) { repo.setDescriptionFeedback("meme-1", "general_description", "approve") }
    }

    @Test
    fun `setDescriptionFeedback clears feedback when response is null`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00", feedback = "approved"),
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        coEvery { repo.setDescriptionFeedback("meme-1", "general_description", "approve") } returns
            Result.success(DescriptionFeedbackResponse(feedback = null))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setDescriptionFeedback("general_description", "approve")

        viewModel.state.test {
            val state = awaitItem()
            assertNull(state.descriptions.first { it.promptKey == "general_description" }.feedback)
        }
    }
```

Add the new import at the top of the file, alongside the existing `com.memebrowser.app.data.model.ImageDescription` import:

```kotlin
import com.memebrowser.app.data.model.DescriptionFeedbackResponse
```

- [ ] **Step 8: Run the tests**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
cd AndroidClient
.\gradlew :app:testDebugUnitTest --no-daemon
```

Expected: `BUILD SUCCESSFUL`, all tests pass including the two new ones and the pre-existing `descriptions are populated in state` / `getDescriptions failure is silent` tests.

- [ ] **Step 9: Compile-check the instrumented test source set**

`MemeDetailScreenTest.kt` (androidTest) calls `DescriptionsBottomSheet` only indirectly via `MemeDetailScreen` (not directly), and `DescriptionsBottomSheet`'s new `onFeedback` parameter has a default value (`{ _, _ -> }`), so no existing test call site should break — but per this repo's CLAUDE.md gotcha (`androidTest` is compiled by a separate Gradle task than `testDebugUnitTest`), verify explicitly:

```powershell
.\gradlew :app:compileDebugKotlin :app:compileDebugAndroidTestKotlin --no-daemon
```

Expected: `BUILD SUCCESSFUL`. If it fails because some other test constructs `DescriptionsBottomSheet` directly with positional args, fix that call site to use named args or add the new parameter — search first with `grep -rn "DescriptionsBottomSheet(" AndroidClient/app/src` to confirm how many call sites exist before assuming none broke.

- [ ] **Step 10: Manual/device verification note**

No connected device or emulator is available in this sandboxed environment — this step cannot be executed here. Record it as a known gap the same way spec 2's Task 4 did (compile-check only), rather than skipping silently.

- [ ] **Step 11: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt \
  AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt
git commit -m "feat: add description approve/reject buttons to Android"
```
