# R4 Review API and UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two read-only endpoints that list the `proposed` recut cuts and describe one, and an admin page at `/admin/recut` that shows the source with the proposed box drawn on it next to the cut, as a one-at-a-time queue.

**Architecture:** `image_cuts` rows are read by a new `RecutRepository` (no service layer); a new router `Backend/app/api/recut.py` returns the schema-generated models. Images come from the existing `GET /api/images/{id}`, which does not filter on status. The React page draws the box as a percentage overlay scaled from the source image's natural size; the open proposal is in `?cut=<id>`. Approve and reject buttons are shown disabled (R5 enables them).

**Tech Stack:** FastAPI, SQLAlchemy async, pytest (integration against `ocrdb_test`, router tests with a mocked repository), shared JSON schemas with generated TypeScript/Kotlin/Python types, React + Vite + vitest + eslint (React Compiler rules).

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r4-review-api-and-ui-design.md`

## Global Constraints

- No migration and no change to `Storage/models.py`; keep clear of the parallel `meme-popularity` work.
- Queue order is `confidence DESC NULLS LAST, id ASC`; the list holds `state = 'proposed'` rows with both `source_image_id` and `cut_image_id` non-null; `limit` 1 to 200 (default 50), `offset` >= 0.
- Detail answers a row in any state that still has both images; unknown id or a missing image is 404; a non-UUID id is 422; neighbours (`previousId`, `nextId`) are null for a row that is not `proposed`.
- Response models come from `Backend/app/types/generated/` (never hand-written in the router); wire field names are camelCase (`sourceImageId`, `cutImageId`, `detectorVersion`, `detectRunId`, `createdAt`, `previousId`, `nextId`).
- No image dimensions in the API; the overlay is scaled from the loaded image's natural size.
- Frontend types come from the generated `all.d.ts`; none hand-written. `eslint src/` must give 0 warnings; the app renders in StrictMode with the React Compiler rules (effects fetch with a cancel guard, refs touched only in handlers and effects).
- Approve and Reject are rendered and `disabled`; nothing in R4 changes data.
- Test roots run separately: `Backend/` from its own directory; `tests/integration/` with `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`. Python is `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe` (the venv lives in the main checkout, not the worktree).
- Many repo files use CRLF; preserve line endings (use the Edit tool for existing files). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A proposal with NULL `confidence` sorts after every scored one, in the list and in the neighbours (PostgreSQL puts NULLs first on `DESC`). Pinned in Task 2.
- Two proposals with equal confidence keep a stable order by `id`, so paging never repeats or skips one. Pinned in Task 2.
- The detail of an `approved`/`rejected` row (a link kept from earlier) answers 200 with null neighbours instead of 404. Pinned in Task 2.
- A malformed `?cut=` value or an id nobody has falls back to the first proposal instead of showing a blank page or looping. Pinned in Task 5.
- A source or cut file that is missing on disk shows a placeholder instead of a broken image, and the overlay never draws before the source size is known. Pinned in Task 6.

---

## File Structure

| File | Responsibility |
|---|---|
| `shared/schemas/recutproposal.schema.json`, `recutproposalpage.schema.json`, `recutproposaldetail.schema.json`, `all.schema.json` | wire contract |
| `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/.../Models.kt`, `Backend/app/types/generated/*` | regenerated types |
| `Backend/app/repositories/recut_repository.py` | the two queries, mapping rows to camelCase dicts |
| `Backend/app/api/recut.py`, `Backend/app/main.py` | the two endpoints and their registration |
| `tests/integration/test_recut_repository.py`, `Backend/tests/test_recut_endpoints.py` | backend tests |
| `backend_api.md`, `docs/image-status.md` | documentation |
| `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/api/http/HttpMemesApi.recut.test.ts` | API client |
| `src/recut/types.ts`, `fixtures.ts`, `boxOverlay.ts`, `useRecutQueue.ts` (+ tests) | page logic |
| `src/components/recut/BeforeAfter.tsx`, `BoxOverlay.tsx`, `QueueStrip.tsx`, `ProposalInfo.tsx` | presentation |
| `src/pages/RecutReviewPage.tsx` (+ test), `src/app/router.tsx`, `src/app/AppLayout.tsx` (+ test) | page, route, navigation |

`src/` below means `Frontend/memes-frontend/src/`.

---

### Task 1: Shared schemas and generated types

**Files:**
- Create: `shared/schemas/recutproposal.schema.json`, `shared/schemas/recutproposalpage.schema.json`, `shared/schemas/recutproposaldetail.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`, `Backend/app/types/generated/` (three new modules plus `__init__.py`)

**Interfaces:**
- Produces (Python): `Backend.app.types.generated.recutproposal.Schema`, `recutproposalpage.Schema`, `recutproposaldetail.Schema`.
- Produces (TypeScript, `all.d.ts`): `RecutProposal`, `RecutProposalPage`, `RecutProposalDetail`.

- [ ] **Step 1: Write the three schemas**

`shared/schemas/recutproposal.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "recutproposal.schema.json",
  "title": "RecutProposal",
  "description": "One proposed cut of a badly cropped image, waiting for review",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string" },
    "sourceImageId": { "type": "string" },
    "cutImageId": { "type": "string" },
    "confidence": { "type": ["number", "null"] },
    "box": {
      "type": "object",
      "additionalProperties": false,
      "description": "The proposed rectangle in pixels of the source image",
      "properties": {
        "x": { "type": "integer" },
        "y": { "type": "integer" },
        "w": { "type": "integer" },
        "h": { "type": "integer" }
      },
      "required": ["x", "y", "w", "h"]
    }
  },
  "required": ["id", "sourceImageId", "cutImageId", "confidence", "box"]
}
```

`shared/schemas/recutproposalpage.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "recutproposalpage.schema.json",
  "title": "RecutProposalPage",
  "description": "One page of the proposals waiting for review, highest confidence first",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./recutproposal.schema.json" } },
    "total": { "type": "integer" }
  },
  "required": ["items", "total"]
}
```

`shared/schemas/recutproposaldetail.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "recutproposaldetail.schema.json",
  "title": "RecutProposalDetail",
  "description": "One proposal with its provenance and its neighbours in the review queue",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string" },
    "sourceImageId": { "type": "string" },
    "cutImageId": { "type": "string" },
    "confidence": { "type": ["number", "null"] },
    "box": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "x": { "type": "integer" },
        "y": { "type": "integer" },
        "w": { "type": "integer" },
        "h": { "type": "integer" }
      },
      "required": ["x", "y", "w", "h"]
    },
    "state": { "type": "string" },
    "detector": { "type": "string" },
    "detectorVersion": { "type": "string" },
    "detectRunId": { "type": ["string", "null"] },
    "createdAt": { "type": "string" },
    "previousId": { "type": ["string", "null"] },
    "nextId": { "type": ["string", "null"] }
  },
  "required": ["id", "sourceImageId", "cutImageId", "confidence", "box", "state", "detector", "detectorVersion",
               "detectRunId", "createdAt", "previousId", "nextId"]
}
```

- [ ] **Step 2: Register them in `all.schema.json`**

Use the Edit tool: after the line `"ProfileCoverageImages": { "$ref": "profilecoverageimages.schema.json" },` add

```json
    "RecutProposal": { "$ref": "recutproposal.schema.json" },
    "RecutProposalPage": { "$ref": "recutproposalpage.schema.json" },
    "RecutProposalDetail": { "$ref": "recutproposaldetail.schema.json" },
```

- [ ] **Step 3: Regenerate the TypeScript types**

Run from `Frontend/` : `bash generate-types.sh` (needs `pnpm install` in `Frontend/memes-frontend/` first when the worktree has no `node_modules`; `npx` may need network for `json-schema-to-typescript`; if it cannot run, ledger a ruling and hand-add nothing: stop and ask).
Expected: `git diff --stat Frontend/memes-frontend/src/types/generated/all.d.ts` shows only added lines, and `grep -n "RecutProposal" Frontend/memes-frontend/src/types/generated/all.d.ts` lists the three interfaces.

- [ ] **Step 4: Regenerate the Kotlin DTOs**

Run from the repo root: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe AndroidClient/scripts/generate_dtos.py`.
Expected: `git diff --stat AndroidClient` shows `Models.kt` with only added lines (`RecutProposal`, `RecutProposalPage`, `RecutProposalDetail`).

- [ ] **Step 5: Regenerate the Python models**

From `Backend/` run the command in `documents/generation.md` (with `datamodel-codegen` from `.venv311\Scripts`):
`datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel`.
The generator rewrites a timestamp header in every module. Run `git status --short Backend/app/types/generated`, then `git checkout -- <file>` for every modified file whose only change is the `# timestamp:` line (keep `__init__.py` and the three new `recutproposal*.py` modules).
Expected: `git status --short Backend/app/types/generated` lists `__init__.py` (modified) and `recutproposal.py`, `recutproposalpage.py`, `recutproposaldetail.py` (new), nothing else.

- [ ] **Step 6: Verify the generated models import and validate**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -c "from Backend.app.types.generated.recutproposaldetail import Schema as D; from Backend.app.types.generated.recutproposalpage import Schema as P; d=D(id='a',sourceImageId='b',cutImageId='c',confidence=None,box={'x':0,'y':1,'w':2,'h':3},state='proposed',detector='margin',detectorVersion='1',detectRunId=None,createdAt='2026-10-28T10:00:00',previousId=None,nextId=None); print(P(items=[],total=0), d.box.w)"`
Expected: prints `items=[] total=0 2`.

- [ ] **Step 7: Commit**

```bash
git add shared/schemas Frontend/memes-frontend/src/types/generated AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt Backend/app/types/generated
git commit -m "feat(recut): R4 shared schemas and generated types"
```

---

### Task 2: Repository

**Files:**
- Create: `Backend/app/repositories/recut_repository.py`
- Test: `tests/integration/test_recut_repository.py`

**Interfaces:**
- Produces: `RecutRepository(session)` with
  - `async list_proposed(offset: int, limit: int) -> tuple[list[dict], int]` (items in queue order, total of the whole queue),
  - `async get(cut_id: uuid.UUID) -> dict | None` (detail dict; `None` for an unknown id or a row missing either image).
- Item dict keys: `id`, `sourceImageId`, `cutImageId`, `confidence`, `box` (`x`, `y`, `w`, `h`); the detail dict adds `state`, `detector`, `detectorVersion`, `detectRunId`, `createdAt` (ISO string), `previousId`, `nextId`. All ids are strings.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_recut_repository.py`:

```python
"""RecutRepository: the review queue of proposed cuts (screenshot recut R4). Requires a live PostgreSQL instance -- see conftest.
The test database is shared, so assertions are about the seeded ids and their relative order, not global counters."""
import uuid

import pytest

from Backend.app.repositories.recut_repository import RecutRepository
from Storage.models import CutState, Image, ImageCut, ImageStatus


async def _cut(session, confidence, state=CutState.PROPOSED, with_images=True):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add_all([source, cut])
    await session.flush()
    row = ImageCut(
        source_image_id=source.id if with_images else None,
        cut_image_id=cut.id if with_images else None,
        crop_x=10, crop_y=20, crop_w=300, crop_h=200,
        detector="margin_trim", detector_version="1", confidence=confidence, state=state)
    session.add(row)
    await session.flush()
    return row


async def _queue_ids(repo):
    items, total = await repo.list_proposed(0, 200)
    assert total <= 200, "the shared test database holds more proposals than one page; the order checks below need them all"
    return [item["id"] for item in items]


@pytest.mark.asyncio(loop_scope="session")
async def test_queue_is_ordered_by_confidence_with_null_last_and_ties_by_id(db_session):
    low = await _cut(db_session, 0.91)
    high = await _cut(db_session, 0.97)
    tie_a = await _cut(db_session, 0.94)
    tie_b = await _cut(db_session, 0.94)
    unscored = await _cut(db_session, None)
    repo = RecutRepository(db_session)

    ids = await _queue_ids(repo)

    mine = [str(r.id) for r in (high, tie_a, tie_b, low, unscored) if str(r.id) in ids]
    first_tie, second_tie = sorted([str(tie_a.id), str(tie_b.id)])
    assert [i for i in ids if i in mine] == [str(high.id), first_tie, second_tie, str(low.id), str(unscored.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_only_proposed_rows_with_both_images_are_listed(db_session):
    listed = await _cut(db_session, 0.9)
    detected = await _cut(db_session, 0.9, state=CutState.DETECTED)
    approved = await _cut(db_session, 0.9, state=CutState.APPROVED)
    rejected = await _cut(db_session, 0.9, state=CutState.REJECTED)
    orphan = await _cut(db_session, 0.9, with_images=False)
    repo = RecutRepository(db_session)

    ids = await _queue_ids(repo)

    assert str(listed.id) in ids
    for other in (detected, approved, rejected, orphan):
        assert str(other.id) not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_item_shape_and_box(db_session):
    row = await _cut(db_session, 0.8)
    repo = RecutRepository(db_session)

    items, total = await repo.list_proposed(0, 200)
    item = next(i for i in items if i["id"] == str(row.id))

    assert item == {"id": str(row.id), "sourceImageId": str(row.source_image_id), "cutImageId": str(row.cut_image_id),
                    "confidence": 0.8, "box": {"x": 10, "y": 20, "w": 300, "h": 200}}
    assert total == len(items)


@pytest.mark.asyncio(loop_scope="session")
async def test_paging_follows_the_full_order_and_total_is_the_whole_queue(db_session):
    for confidence in (0.71, 0.72, 0.73):
        await _cut(db_session, confidence)
    repo = RecutRepository(db_session)
    everything, total = await repo.list_proposed(0, 200)

    second, total_again = await repo.list_proposed(1, 1)

    assert second == [everything[1]]
    assert total_again == total == len(everything)


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_carries_provenance_and_neighbours_in_queue_order(db_session):
    for confidence in (0.51, 0.52, 0.53):
        await _cut(db_session, confidence)
    repo = RecutRepository(db_session)
    ids = await _queue_ids(repo)
    middle = ids[1]

    detail = await repo.get(uuid.UUID(middle))

    assert detail["id"] == middle and detail["state"] == "proposed"
    assert detail["detector"] == "margin_trim" and detail["detectorVersion"] == "1"
    assert detail["detectRunId"] is None and isinstance(detail["createdAt"], str)
    assert detail["previousId"] == ids[0] and detail["nextId"] == ids[2]
    first = await repo.get(uuid.UUID(ids[0]))
    last = await repo.get(uuid.UUID(ids[-1]))
    assert first["previousId"] is None and first["nextId"] == ids[1]
    assert last["nextId"] is None and last["previousId"] == ids[-2]


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_of_a_decided_row_has_no_neighbours(db_session):
    approved = await _cut(db_session, 0.9, state=CutState.APPROVED)
    repo = RecutRepository(db_session)

    detail = await repo.get(approved.id)

    assert detail["state"] == "approved"
    assert detail["previousId"] is None and detail["nextId"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_is_none_for_an_unknown_id_and_for_a_row_without_images(db_session):
    orphan = await _cut(db_session, 0.9, with_images=False)
    repo = RecutRepository(db_session)

    assert await repo.get(uuid.uuid4()) is None
    assert await repo.get(orphan.id) is None
```

- [ ] **Step 2: Run to verify it fails**

Run (PowerShell): `$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"; & "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest tests/integration/test_recut_repository.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'Backend.app.repositories.recut_repository'`.

- [ ] **Step 3: Implement the repository**

`Backend/app/repositories/recut_repository.py`:

```python
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import CutState, ImageCut

# The review queue: proposals with a cut file to show, best first. confidence is nullable and PostgreSQL sorts NULLs
# first on DESC, so unscored proposals are put last explicitly; id makes the order total so paging never repeats a row.
_QUEUE_FILTER = (
    ImageCut.state == CutState.PROPOSED,
    ImageCut.source_image_id.is_not(None),
    ImageCut.cut_image_id.is_not(None),
)
_QUEUE_ORDER = (ImageCut.confidence.desc().nulls_last(), ImageCut.id.asc())


def _item(row: ImageCut) -> dict:
    return {
        "id": str(row.id),
        "sourceImageId": str(row.source_image_id),
        "cutImageId": str(row.cut_image_id),
        "confidence": row.confidence,
        "box": {"x": row.crop_x, "y": row.crop_y, "w": row.crop_w, "h": row.crop_h},
    }


class RecutRepository:
    """Read side of the screenshot-recut review (R4). Never writes and never commits."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_proposed(self, offset: int, limit: int) -> tuple[list[dict], int]:
        rows = (await self.session.execute(
            select(ImageCut).where(*_QUEUE_FILTER).order_by(*_QUEUE_ORDER).offset(offset).limit(limit)
        )).scalars().all()
        total = (await self.session.execute(
            select(func.count()).select_from(ImageCut).where(*_QUEUE_FILTER)
        )).scalar_one()
        return [_item(row) for row in rows], total

    async def get(self, cut_id: uuid.UUID) -> dict | None:
        row = (await self.session.execute(
            select(ImageCut).where(
                ImageCut.id == cut_id, ImageCut.source_image_id.is_not(None), ImageCut.cut_image_id.is_not(None))
        )).scalar_one_or_none()
        if row is None:
            return None
        previous_id = next_id = None
        if row.state == CutState.PROPOSED:
            previous_id, next_id = await self._neighbours(cut_id)
        return {
            **_item(row),
            "state": row.state,
            "detector": row.detector,
            "detectorVersion": row.detector_version,
            "detectRunId": str(row.detect_run_id) if row.detect_run_id is not None else None,
            "createdAt": row.created_at.isoformat(),
            "previousId": previous_id,
            "nextId": next_id,
        }

    async def _neighbours(self, cut_id: uuid.UUID) -> tuple[str | None, str | None]:
        window = {"order_by": list(_QUEUE_ORDER)}
        ranked = (
            select(
                ImageCut.id.label("id"),
                func.lag(ImageCut.id).over(**window).label("previous_id"),
                func.lead(ImageCut.id).over(**window).label("next_id"),
            ).where(*_QUEUE_FILTER).subquery()
        )
        found = (await self.session.execute(
            select(ranked.c.previous_id, ranked.c.next_id).where(ranked.c.id == cut_id)
        )).one_or_none()
        if found is None:
            return None, None
        previous_id, next_id = found
        return (str(previous_id) if previous_id is not None else None,
                str(next_id) if next_id is not None else None)
```

- [ ] **Step 4: Run to verify it passes**

Run: the command of Step 2.
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/recut_repository.py tests/integration/test_recut_repository.py
git commit -m "feat(recut): R4 review queue repository"
```

---

### Task 3: Router, registration and API documentation

**Files:**
- Create: `Backend/app/api/recut.py`
- Modify: `Backend/app/main.py`, `backend_api.md`, `docs/image-status.md`
- Test: `Backend/tests/test_recut_endpoints.py`

**Interfaces:**
- Consumes: `RecutRepository.list_proposed(offset, limit)` and `.get(cut_id)` (Task 2); `Backend.app.types.generated.recutproposalpage.Schema` and `recutproposaldetail.Schema` (Task 1).
- Produces: `router` (prefix `/recut`, mounted under `/api`), dependency `get_recut_repository`, `GET /api/recut/proposals`, `GET /api/recut/proposals/{id}`.

- [ ] **Step 1: Write the failing tests**

`Backend/tests/test_recut_endpoints.py`:

```python
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.recut import get_recut_repository, router

ITEM = {"id": "c1", "sourceImageId": "s1", "cutImageId": "i1", "confidence": 0.93,
        "box": {"x": 10, "y": 20, "w": 300, "h": 200}}
DETAIL = {**ITEM, "state": "proposed", "detector": "margin_trim", "detectorVersion": "1", "detectRunId": None,
          "createdAt": "2026-10-28T10:00:00+00:00", "previousId": None, "nextId": "c2"}


@pytest.fixture
def client_and_repo():
    repo = type("R", (), {})()
    repo.list_proposed = AsyncMock(return_value=([ITEM], 7))
    repo.get = AsyncMock(return_value=DETAIL)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_recut_repository] = lambda: repo
    return TestClient(app), repo


def test_list_returns_the_page_and_total(client_and_repo):
    client, repo = client_and_repo
    response = client.get("/api/recut/proposals")
    assert response.status_code == 200
    assert response.json() == {"items": [ITEM], "total": 7}
    repo.list_proposed.assert_awaited_once_with(0, 50)


def test_list_passes_offset_and_limit_through(client_and_repo):
    client, repo = client_and_repo
    client.get("/api/recut/proposals?offset=100&limit=200")
    repo.list_proposed.assert_awaited_once_with(100, 200)


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "limit=abc"])
def test_list_bad_parameters_are_422(client_and_repo, query):
    client, repo = client_and_repo
    assert client.get(f"/api/recut/proposals?{query}").status_code == 422
    repo.list_proposed.assert_not_awaited()


def test_detail_returns_the_record(client_and_repo):
    client, repo = client_and_repo
    cut_id = uuid.uuid4()
    response = client.get(f"/api/recut/proposals/{cut_id}")
    assert response.status_code == 200 and response.json() == DETAIL
    repo.get.assert_awaited_once_with(cut_id)


def test_detail_unknown_is_404(client_and_repo):
    client, repo = client_and_repo
    repo.get.return_value = None
    response = client.get(f"/api/recut/proposals/{uuid.uuid4()}")
    assert response.status_code == 404 and response.json() == {"detail": "Proposal not found"}


def test_detail_with_an_id_that_is_not_a_uuid_is_422(client_and_repo):
    client, repo = client_and_repo
    assert client.get("/api/recut/proposals/not-a-uuid").status_code == 422
    repo.get.assert_not_awaited()
```

- [ ] **Step 2: Run to verify it fails**

Run (PowerShell, from `Backend/`): `Set-Location Backend; & "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest tests/test_recut_endpoints.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'Backend.app.api.recut'`.

- [ ] **Step 3: Implement the router**

`Backend/app/api/recut.py`:

```python
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.types.generated.recutproposaldetail import Schema as RecutProposalDetail
from Backend.app.types.generated.recutproposalpage import Schema as RecutProposalPage
from Storage.db import get_async_db

router = APIRouter(prefix="/recut", tags=["recut"])


async def get_recut_repository(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutRepository, None]:
    yield RecutRepository(db)


@router.get("/proposals", response_model=RecutProposalPage)
async def list_proposals(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                         repo: RecutRepository = Depends(get_recut_repository)):
    items, total = await repo.list_proposed(offset, limit)
    return {"items": items, "total": total}


@router.get("/proposals/{proposal_id}", response_model=RecutProposalDetail)
async def get_proposal(proposal_id: uuid.UUID, repo: RecutRepository = Depends(get_recut_repository)):
    detail = await repo.get(proposal_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return detail
```

- [ ] **Step 4: Register the router**

In `Backend/app/main.py` (Edit tool, keep line endings) add `from Backend.app.api.recut import router as recut_router` after the `admin_router` import line, and `app.include_router(recut_router, prefix="/api")` after the `app.include_router(admin_router, prefix="/api")` line.

- [ ] **Step 5: Run to verify it passes**

Run (from `Backend/`): `& "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest tests/test_recut_endpoints.py -v`
Expected: 9 passed. Then the whole Backend root: `& "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest -q` Expected: all pass (compare the failure count with `main` if anything fails; a failure unrelated to recut is reported by name).

- [ ] **Step 6: Smoke test the application import**

Run (repo root, PowerShell): `$env:DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test_db"; $env:BASE_PATH = "C:\tmp"; & "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -c "from Backend.app.main import app; print(sorted(r.path for r in app.routes if 'recut' in r.path))"`
Expected: `['/api/recut/proposals', '/api/recut/proposals/{proposal_id}']`. (Do not start uvicorn: the environment ports are occupied; the router test above already exercises the endpoints and `/api/diagnostics/health` and `/api/images` are untouched.)

- [ ] **Step 7: Document**

`backend_api.md`: read the lines around the `### Admin` heading (about line 1329) and insert, immediately before that heading, a section in the style of "Profile coverage (admin)":

```markdown
### Recut review

The review queue of the screenshot recut (epic `docs/superpowers/specs/2026-10-17-screenshot-recut/`): proposed cuts of badly cropped images, with the rectangle (in pixels of the source) the cut was taken from. Read-only; the approve and reject endpoints arrive with R5. Unauthenticated like the rest of the admin API. The images themselves are served by `GET /api/images/{id}`, which also serves `cut_proposed` images.

**List the proposals**

- **URL**: `/api/recut/proposals?offset=<n>&limit=<n>`
- **Method**: `GET`
- **Query Parameters**: `offset` (optional, >= 0, default 0), `limit` (optional, 1 to 200, default 50)
- **Response** `200`: `RecutProposalPage`: `items` (array of `{ id, sourceImageId, cutImageId, confidence, box }`, `confidence` is a number or `null`, `box` is `{ x, y, w, h }` in pixels of the source image) and `total` (every proposal waiting for review, not only this page). Only `proposed` proposals with both images are listed, ordered by confidence (highest first, unscored last) and then by `id`.
- **Error Responses**: `422` for a bad `offset` or `limit`

**One proposal**

- **URL**: `/api/recut/proposals/{id}`
- **Method**: `GET`
- **Response** `200`: `RecutProposalDetail`: the item fields plus `state` (`proposed`, `approved`, `rejected`...), `detector`, `detectorVersion`, `detectRunId` (or `null`), `createdAt` (ISO 8601) and `previousId`/`nextId`, the neighbours in the list order (`null` at either end, and both `null` when the proposal is not `proposed`). A proposal of any state is answered as long as both its images still exist, so a link survives its decision.
- **Error Responses**: `404` for an unknown id or a proposal whose source or cut image no longer exists; `422` for an `id` that is not a UUID

---

```

`docs/image-status.md`: in the `cut_proposed` row of the status table, read the line, then use the Edit tool to append to its "Invisible to search and browse" cell: `; served by GET /api/images/{id} and shown on the review page (/admin/recut, screenshot-recut R4)`.

- [ ] **Step 8: Run the documentation guards and commit**

Run: `& "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest tests/docs/ -q`
Expected: pass.

```bash
git add Backend/app/api/recut.py Backend/app/main.py Backend/tests/test_recut_endpoints.py backend_api.md docs/image-status.md
git commit -m "feat(recut): R4 review endpoints"
```

---

### Task 4: Frontend API client

**Files:**
- Modify: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`
- Create: `src/recut/types.ts`, `src/recut/fixtures.ts`
- Test: `src/api/http/HttpMemesApi.recut.test.ts`

**Interfaces:**
- Consumes: `RecutProposal`, `RecutProposalPage`, `RecutProposalDetail` from `src/types/generated/all` (Task 1); `CuratorRequestError` from `src/api/errors` (its `status` carries the HTTP status).
- Produces: `MemesApi.listRecutProposals(offset?: number, limit?: number): Promise<RecutProposalPage>`; `MemesApi.getRecutProposal(id: string): Promise<RecutProposalDetail>` (rejects with `CuratorRequestError`; 404 means unknown); `src/recut/types.ts` re-exporting the three types; fixtures `makeProposal(id, overrides?)`, `makeProposalPage(ids, total?)`, `makeProposalDetail(id, overrides?)`.

- [ ] **Step 1: Install frontend dependencies when needed**

If `Frontend/memes-frontend/node_modules` is missing in the worktree, run `pnpm install --frozen-lockfile` from `Frontend/memes-frontend/` (give it a long timeout).

- [ ] **Step 2: Write the failing test**

`src/api/http/HttpMemesApi.recut.test.ts`:

```ts
import { HttpMemesApi } from "./HttpMemesApi"
import { CuratorRequestError } from "../errors"
import { makeProposalDetail, makeProposalPage } from "../../recut/fixtures"

const api = new HttpMemesApi("http://backend")

function stubFetch(result: { ok: boolean; status: number; body?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: result.ok, status: result.status, json: async () => result.body })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("recut API", () => {
  it("lists proposals with offset and limit", async () => {
    const body = makeProposalPage(["a", "b"], 9)
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.listRecutProposals(50, 25)

    expect(result).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals?offset=50&limit=25")
  })

  it("lists proposals without a query string by default", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: makeProposalPage([]) })

    await api.listRecutProposals()

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals")
  })

  it("gets one proposal by id", async () => {
    const body = makeProposalDetail("a")
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    expect(await api.getRecutProposal("a")).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals/a")
  })

  it("rejects with the HTTP status on failure", async () => {
    stubFetch({ ok: false, status: 404 })

    await expect(api.getRecutProposal("zzz")).rejects.toMatchObject({ name: "CuratorRequestError", status: 404 })
    await expect(api.getRecutProposal("zzz")).rejects.toBeInstanceOf(CuratorRequestError)
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run (from `Frontend/memes-frontend/`): `pnpm exec vitest run src/api/http/HttpMemesApi.recut.test.ts`
Expected: FAIL, cannot resolve `../../recut/fixtures`.

- [ ] **Step 4: Types and fixtures**

`src/recut/types.ts`:

```ts
import type { RecutProposal, RecutProposalDetail, RecutProposalPage } from "../types/generated/all"

export type { RecutProposal, RecutProposalDetail, RecutProposalPage }
```

`src/recut/fixtures.ts`:

```ts
import type { RecutProposal, RecutProposalDetail, RecutProposalPage } from "./types"

export function makeProposal(id: string, overrides: Partial<RecutProposal> = {}): RecutProposal {
  return {
    id,
    sourceImageId: `src-${id}`,
    cutImageId: `cut-${id}`,
    confidence: 0.9,
    box: { x: 10, y: 20, w: 300, h: 200 },
    ...overrides,
  }
}

export function makeProposalPage(ids: string[], total: number = ids.length): RecutProposalPage {
  return { items: ids.map((id) => makeProposal(id)), total }
}

export function makeProposalDetail(id: string, overrides: Partial<RecutProposalDetail> = {}): RecutProposalDetail {
  return {
    ...makeProposal(id),
    state: "proposed",
    detector: "margin_trim",
    detectorVersion: "1",
    detectRunId: null,
    createdAt: "2026-10-28T10:00:00+00:00",
    previousId: null,
    nextId: null,
    ...overrides,
  }
}
```

- [ ] **Step 5: Interface, implementation and mock**

`src/api/MemesApi.ts` (Edit tool): add `RecutProposalDetail, RecutProposalPage,` to the generated-types import list; add to the interface, after `getProfileCoverageImages(...)`:

```ts
  listRecutProposals(offset?: number, limit?: number): Promise<RecutProposalPage>
  getRecutProposal(id: string): Promise<RecutProposalDetail>
```

`src/api/http/HttpMemesApi.ts`: add the same two names to its generated-types import, and add after `getProfileCoverageImages`:

```ts
  async listRecutProposals(offset?: number, limit?: number): Promise<RecutProposalPage> {
    const params = new URLSearchParams()
    if (offset !== undefined) params.set("offset", String(offset))
    if (limit !== undefined) params.set("limit", String(limit))
    const query = params.toString()
    const response = await fetch(`${this.baseUrl}/api/recut/proposals${query ? `?${query}` : ""}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async getRecutProposal(id: string): Promise<RecutProposalDetail> {
    const response = await fetch(`${this.baseUrl}/api/recut/proposals/${encodeURIComponent(id)}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }
```

`src/test/mockApi.ts`: import `{ makeProposalDetail, makeProposalPage } from '../recut/fixtures'` and add after `getProfileCoverageImages`:

```ts
    listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage([])),
    getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail('p1')),
```

- [ ] **Step 6: Run to verify it passes, then type-check**

Run: `pnpm exec vitest run src/api/http/HttpMemesApi.recut.test.ts` Expected: 4 passed. Then `pnpm exec tsc -b` Expected: no errors (any other `MemesApi` implementation or mock in `src/` that now lacks the two methods fails here; add them there).

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(recut): R4 frontend API client"
```

---

### Task 5: Box overlay helper and queue hook

**Files:**
- Create: `src/recut/boxOverlay.ts`, `src/recut/useRecutQueue.ts`
- Test: `src/recut/boxOverlay.test.ts`, `src/recut/useRecutQueue.test.ts`

**Interfaces:**
- Consumes: `MemesApi.listRecutProposals`, `.getRecutProposal`; `CuratorRequestError`; fixtures (Task 4).
- Produces:
  - `overlayStyle(box: Box, size: Size): { left: string; top: string; width: string; height: string }` and `removedShare(box: Box, size: Size): number` (0 to 1), with `Box = { x; y; w; h }`, `Size = { width; height }`.
  - `PAGE_SIZE = 50`; `useRecutQueue(memesApi: MemesApi, openId: string | null)` returning `{ items: RecutProposal[]; total: number; listStatus: "loading" | "ready" | "failed"; loadingMore: boolean; loadMore: () => void; detail: DetailState | null; reload: () => void }` where `DetailState = { status: "ready"; data: RecutProposalDetail } | { status: "missing" } | { status: "failed" }` and `detail === null` means loading (or no proposal open).

- [ ] **Step 1: Write the failing overlay test**

`src/recut/boxOverlay.test.ts`:

```ts
import { overlayStyle, removedShare } from "./boxOverlay"

describe("overlayStyle", () => {
  it("expresses the box in percentages of the source size", () => {
    expect(overlayStyle({ x: 10, y: 20, w: 50, h: 40 }, { width: 100, height: 200 }))
      .toEqual({ left: "10%", top: "10%", width: "50%", height: "20%" })
  })

  it("a box that fills the whole image covers all of it", () => {
    expect(overlayStyle({ x: 0, y: 0, w: 640, h: 480 }, { width: 640, height: 480 }))
      .toEqual({ left: "0%", top: "0%", width: "100%", height: "100%" })
  })

  it("keeps thirds readable", () => {
    expect(overlayStyle({ x: 1, y: 0, w: 1, h: 3 }, { width: 3, height: 3 }).left).toBe("33.3333%")
  })
})

describe("removedShare", () => {
  it("is the part of the source area outside the box", () => {
    expect(removedShare({ x: 0, y: 0, w: 50, h: 50 }, { width: 100, height: 100 })).toBe(0.75)
    expect(removedShare({ x: 0, y: 0, w: 100, h: 100 }, { width: 100, height: 100 })).toBe(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/recut/boxOverlay.test.ts` Expected: FAIL, cannot resolve `./boxOverlay`.

- [ ] **Step 3: Implement the helper**

`src/recut/boxOverlay.ts`:

```ts
export type Box = { x: number; y: number; w: number; h: number }
export type Size = { width: number; height: number }

const percent = (part: number, whole: number): string => `${Number(((part * 100) / whole).toFixed(4))}%`

/** The box as CSS percentages of the image it lies on, so the overlay stays right at any display size. */
export function overlayStyle(box: Box, size: Size): { left: string; top: string; width: string; height: string } {
  return {
    left: percent(box.x, size.width),
    top: percent(box.y, size.height),
    width: percent(box.w, size.width),
    height: percent(box.h, size.height),
  }
}

/** Share of the source area that lies outside the box (0 to 1). */
export function removedShare(box: Box, size: Size): number {
  return 1 - (box.w * box.h) / (size.width * size.height)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm exec vitest run src/recut/boxOverlay.test.ts` Expected: 4 passed.

- [ ] **Step 5: Write the failing hook tests**

`src/recut/useRecutQueue.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react"
import { CuratorRequestError } from "../api/errors"
import { makeMockApi } from "../test/mockApi"
import { makeProposalDetail, makeProposalPage } from "./fixtures"
import { PAGE_SIZE, useRecutQueue } from "./useRecutQueue"

describe("useRecutQueue", () => {
  it("loads the first page", async () => {
    const api = makeMockApi({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a", "b"], 2)) })
    const { result } = renderHook(() => useRecutQueue(api, null))

    expect(result.current.listStatus).toBe("loading")
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    expect(result.current.items.map((item) => item.id)).toEqual(["a", "b"])
    expect(result.current.total).toBe(2)
    expect(api.listRecutProposals).toHaveBeenCalledWith(0, PAGE_SIZE)
    expect(result.current.detail).toBeNull()
  })

  it("reports an empty queue", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRecutQueue(api, null))

    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    expect(result.current.items).toEqual([])
    expect(result.current.total).toBe(0)
  })

  it("reports a failed list and retries on reload", async () => {
    const list = vi.fn().mockRejectedValueOnce(new CuratorRequestError(500)).mockResolvedValue(makeProposalPage(["a"]))
    const api = makeMockApi({ listRecutProposals: list })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("failed"))

    act(() => result.current.reload())

    await waitFor(() => expect(result.current.listStatus).toBe("ready"))
    expect(result.current.items).toHaveLength(1)
  })

  it("loads the detail of the open proposal and follows it when the id changes", async () => {
    const get = vi.fn((id: string) => Promise.resolve(makeProposalDetail(id, { nextId: "next" })))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "a" as string | null } })

    await waitFor(() => expect(result.current.detail?.status).toBe("ready"))
    expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "a", nextId: "next" } })

    rerender({ id: "b" })
    expect(result.current.detail).toBeNull()
    await waitFor(() => expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "b" } }))
  })

  it("marks a 404 as missing and any other failure as failed", async () => {
    const get = vi.fn().mockRejectedValue(new CuratorRequestError(404))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "gone" as string | null } })
    await waitFor(() => expect(result.current.detail).toEqual({ status: "missing" }))

    get.mockRejectedValue(new CuratorRequestError(500))
    rerender({ id: "broken" })
    await waitFor(() => expect(result.current.detail).toEqual({ status: "failed" }))
  })

  it("ignores a detail that arrives after the open proposal changed", async () => {
    let resolveFirst: (value: ReturnType<typeof makeProposalDetail>) => void = () => {}
    const get = vi.fn((id: string) => id === "a"
      ? new Promise<ReturnType<typeof makeProposalDetail>>((resolve) => { resolveFirst = resolve })
      : Promise.resolve(makeProposalDetail(id)))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "a" as string | null } })

    rerender({ id: "b" })
    await waitFor(() => expect(result.current.detail).toMatchObject({ data: { id: "b" } }))
    await act(async () => { resolveFirst(makeProposalDetail("a")) })

    expect(result.current.detail).toMatchObject({ data: { id: "b" } })
  })

  it("loads the next page when asked and appends it without repeating a row", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(makeProposalPage(["a", "b"], 4))
      .mockResolvedValueOnce(makeProposalPage(["b", "c"], 4))
    const api = makeMockApi({ listRecutProposals: list })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    act(() => result.current.loadMore())

    await waitFor(() => expect(result.current.items.map((item) => item.id)).toEqual(["a", "b", "c"]))
    expect(list).toHaveBeenLastCalledWith(2, PAGE_SIZE)
  })

  it("does not ask for more when everything is loaded", async () => {
    const api = makeMockApi({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a"], 1)) })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    act(() => result.current.loadMore())

    expect(api.listRecutProposals).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 6: Run to verify it fails**

Run: `pnpm exec vitest run src/recut/useRecutQueue.test.ts` Expected: FAIL, cannot resolve `./useRecutQueue`.

- [ ] **Step 7: Implement the hook**

`src/recut/useRecutQueue.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { CuratorRequestError } from "../api/errors"
import type { RecutProposal, RecutProposalDetail } from "./types"

export const PAGE_SIZE = 50

type ListState = { items: RecutProposal[]; total: number; status: "loading" | "ready" | "failed" }
export type DetailState =
  | { status: "ready"; data: RecutProposalDetail }
  | { status: "missing" }
  | { status: "failed" }
type Loaded = { id: string; state: DetailState }

const LOADING: ListState = { items: [], total: 0, status: "loading" }

export function useRecutQueue(memesApi: MemesApi, openId: string | null) {
  const [list, setList] = useState<ListState>(LOADING)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Only touched in handlers and effects: one next-page request at a time.
  const loadingMoreRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    memesApi.listRecutProposals(0, PAGE_SIZE).then(
      (page) => { if (!cancelled) setList({ items: page.items, total: page.total, status: "ready" }) },
      () => { if (!cancelled) setList({ items: [], total: 0, status: "failed" }) },
    )
    return () => { cancelled = true }
  }, [memesApi, reloadKey])

  useEffect(() => {
    if (openId === null) return
    let cancelled = false
    memesApi.getRecutProposal(openId).then(
      (data) => { if (!cancelled) setLoaded({ id: openId, state: { status: "ready", data } }) },
      (error) => {
        if (cancelled) return
        const missing = error instanceof CuratorRequestError && error.status === 404
        setLoaded({ id: openId, state: { status: missing ? "missing" : "failed" } })
      },
    )
    return () => { cancelled = true }
  }, [memesApi, openId, reloadKey])

  // Identity changes only when the loaded list does, so a failed request does not retrigger an effect that calls it.
  const loadMore = useCallback(() => {
    if (list.status !== "ready" || loadingMoreRef.current || list.items.length >= list.total) return
    loadingMoreRef.current = true
    setLoadingMore(true)
    memesApi.listRecutProposals(list.items.length, PAGE_SIZE).then(
      (page) => setList((previous) => ({
        status: "ready",
        total: page.total,
        items: [...previous.items, ...page.items.filter((item) => !previous.items.some((known) => known.id === item.id))],
      })),
      () => {},
    ).finally(() => {
      loadingMoreRef.current = false
      setLoadingMore(false)
    })
  }, [memesApi, list.status, list.items.length, list.total])

  function reload() {
    setList(LOADING)
    setLoaded(null)
    setReloadKey((key) => key + 1)
  }

  return {
    items: list.items,
    total: list.total,
    listStatus: list.status,
    loadingMore,
    loadMore,
    detail: openId !== null && loaded !== null && loaded.id === openId ? loaded.state : null,
    reload,
  }
}
```

- [ ] **Step 8: Run to verify it passes**

Run: `pnpm exec vitest run src/recut` Expected: all pass (overlay 4, hook 8). Then `pnpm exec eslint src/recut --max-warnings 0` Expected: clean (the compiler rules may object to the `useCallback`/ref pattern; fix by restructuring, never by disabling a rule).

- [ ] **Step 9: Commit**

```bash
git add Frontend/memes-frontend/src/recut
git commit -m "feat(recut): R4 box overlay helper and queue hook"
```

---

### Task 6: Components, page, route and navigation

**Files:**
- Create: `src/components/recut/BoxOverlay.tsx`, `src/components/recut/BeforeAfter.tsx`, `src/components/recut/QueueStrip.tsx`, `src/components/recut/ProposalInfo.tsx`, `src/pages/RecutReviewPage.tsx`
- Modify: `src/app/router.tsx`, `src/app/AppLayout.tsx`, `src/app/AppLayout.test.tsx`
- Test: `src/pages/RecutReviewPage.test.tsx`

**Interfaces:**
- Consumes: `useRecutQueue`, `overlayStyle`, `removedShare`, fixtures, `MemesApi.getImageUrlById`.
- Produces: default export `RecutReviewPage({ memesApi })`, route `/admin/recut`, nav link "Recut".

- [ ] **Step 1: Write the failing page tests**

`src/pages/RecutReviewPage.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import RecutReviewPage from "./RecutReviewPage"
import { CuratorRequestError } from "../api/errors"
import { makeProposalDetail, makeProposalPage } from "../recut/fixtures"
import { makeMockApi } from "../test/mockApi"

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.search}</div>
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}, entry = "/admin/recut") {
  const api = makeMockApi({
    listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a", "b", "c"], 3)),
    getRecutProposal: vi.fn((id: string) => Promise.resolve(makeProposalDetail(id, {
      previousId: id === "a" ? null : id === "b" ? "a" : "b",
      nextId: id === "a" ? "b" : id === "b" ? "c" : null,
    }))),
    ...overrides,
  })
  render(
    <MemoryRouter initialEntries={[entry]}>
      <RecutReviewPage memesApi={api} />
      <Where />
    </MemoryRouter>,
  )
  return api
}

describe("RecutReviewPage", () => {
  it("shows an empty queue", async () => {
    setup({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage([], 0)) })
    expect(await screen.findByText("No proposals waiting for review")).toBeInTheDocument()
  })

  it("opens the first proposal when none is in the URL and rewrites the URL", async () => {
    setup()
    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=a"))
    expect(screen.getByAltText("Source image")).toHaveAttribute("src", "http://example.com/api/images/src-a")
    expect(screen.getByAltText("Proposed cut")).toHaveAttribute("src", "http://example.com/api/images/cut-a")
  })

  it("falls back to the first proposal when the id in the URL does not exist", async () => {
    const api = setup({
      getRecutProposal: vi.fn((id: string) => id === "nope"
        ? Promise.reject(new CuratorRequestError(404))
        : Promise.resolve(makeProposalDetail(id))),
    }, "/admin/recut?cut=nope")
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=a"))
    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
    expect(api.getRecutProposal).toHaveBeenCalledWith("nope")
  })

  it("steps with the buttons and the arrow keys", async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText("1 of 3")
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("2 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("where")).toHaveTextContent("?cut=b")

    await user.keyboard("{ArrowRight}")
    expect(await screen.findByText("3 of 3")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()

    await user.keyboard("{ArrowLeft}")
    expect(await screen.findByText("2 of 3")).toBeInTheDocument()
  })

  it("opens a proposal from the thumbnail strip", async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText("1 of 3")
    await user.click(screen.getByRole("button", { name: "Open proposal 3" }))
    expect(await screen.findByText("3 of 3")).toBeInTheDocument()
  })

  it("draws the box only once the source size is known, in percentages", async () => {
    setup({
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { box: { x: 100, y: 50, w: 400, h: 100 } })),
    })
    const source = await screen.findByAltText("Source image")
    expect(screen.queryByTestId("recut-box")).not.toBeInTheDocument()

    Object.defineProperty(source, "naturalWidth", { value: 1000 })
    Object.defineProperty(source, "naturalHeight", { value: 500 })
    fireEvent.load(source)

    const box = await screen.findByTestId("recut-box")
    expect(box).toHaveStyle({ left: "10%", top: "10%", width: "40%", height: "20%" })
    expect(screen.getByText(/Removes 92% of the source/)).toBeInTheDocument()
  })

  it("shows confidence, box and detector", async () => {
    setup({
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { confidence: 0.934, detector: "margin_trim", detectorVersion: "3" })),
    })
    expect(await screen.findByText("0.93")).toBeInTheDocument()
    expect(screen.getByText("10, 20, 300 x 200")).toBeInTheDocument()
    expect(screen.getByText("margin_trim 3")).toBeInTheDocument()
  })

  it("shows no confidence for an unscored proposal", async () => {
    setup({ getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { confidence: null })) })
    expect(await screen.findByText("not scored")).toBeInTheDocument()
  })

  it("keeps approve and reject visible but disabled", async () => {
    setup()
    await screen.findByText("1 of 3")
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled()
    expect(screen.getByText(/arrives with the approve\/reject step/)).toBeInTheDocument()
  })

  it("shows a placeholder for an image whose file is missing", async () => {
    setup()
    fireEvent.error(await screen.findByAltText("Proposed cut"))
    expect(await screen.findByText("Cut file missing")).toBeInTheDocument()
    expect(screen.getByAltText("Source image")).toBeInTheDocument()
  })

  it("offers a retry when the list cannot be loaded", async () => {
    const user = userEvent.setup()
    const list = vi.fn().mockRejectedValueOnce(new CuratorRequestError(500)).mockResolvedValue(makeProposalPage(["a"], 1))
    setup({ listRecutProposals: list })
    await user.click(await screen.findByRole("button", { name: "Retry" }))
    expect(await screen.findByText("1 of 1")).toBeInTheDocument()
  })

  it("loads the next page when the open proposal nears the end of what is loaded", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(makeProposalPage(["a", "b"], 3))
      .mockResolvedValueOnce(makeProposalPage(["c"], 3))
    setup({ listRecutProposals: list })
    await screen.findByText("1 of 3")
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(list).toHaveBeenLastCalledWith(2, 50)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/pages/RecutReviewPage.test.tsx` Expected: FAIL, cannot resolve `./RecutReviewPage`.

- [ ] **Step 3: Components**

`src/components/recut/BoxOverlay.tsx`:

```tsx
import { overlayStyle } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"

export default function BoxOverlay({ box, size }: { box: Box; size: Size }) {
  return (
    <div
      data-testid="recut-box"
      aria-hidden="true"
      className="pointer-events-none absolute border-2 border-red-500 bg-red-500/10"
      style={overlayStyle(box, size)}
    />
  )
}
```

`src/components/recut/BeforeAfter.tsx`:

```tsx
import { useState } from "react"
import { removedShare } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"
import BoxOverlay from "./BoxOverlay"

type Props = { sourceUrl: string; cutUrl: string; box: Box }

function Missing({ what }: { what: string }) {
  return <div className="flex h-48 items-center justify-center rounded border border-dashed text-sm text-gray-500">{what} file missing</div>
}

/** Mounted with key={proposal id}, so the load state of one proposal never leaks into the next. */
export default function BeforeAfter({ sourceUrl, cutUrl, box }: Props) {
  const [size, setSize] = useState<Size | null>(null)
  const [sourceFailed, setSourceFailed] = useState(false)
  const [cutFailed, setCutFailed] = useState(false)

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <figure aria-label="Source with the proposed box">
        <figcaption className="mb-1 text-sm font-medium">Source</figcaption>
        {sourceFailed ? (
          <Missing what="Source" />
        ) : (
          <div className="relative inline-block max-w-full">
            <img
              src={sourceUrl}
              alt="Source image"
              className="block max-h-[70vh] max-w-full"
              onLoad={(event) => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
              onError={() => setSourceFailed(true)}
            />
            {size !== null && size.width > 0 && size.height > 0 && <BoxOverlay box={box} size={size} />}
          </div>
        )}
        {size !== null && size.width > 0 && size.height > 0 && (
          <p className="mt-1 text-xs text-gray-600">Removes {Math.round(removedShare(box, size) * 100)}% of the source</p>
        )}
      </figure>
      <figure aria-label="Proposed cut">
        <figcaption className="mb-1 text-sm font-medium">Cut</figcaption>
        {cutFailed ? (
          <Missing what="Cut" />
        ) : (
          <img src={cutUrl} alt="Proposed cut" className="block max-h-[70vh] max-w-full" onError={() => setCutFailed(true)} />
        )}
      </figure>
    </div>
  )
}
```

`src/components/recut/QueueStrip.tsx`:

```tsx
import type { RecutProposal } from "../../recut/types"

type Props = {
  items: RecutProposal[]
  openId: string | null
  imageUrl: (imageId: string) => string
  onOpen: (id: string) => void
}

export default function QueueStrip({ items, openId, imageUrl, onOpen }: Props) {
  return (
    <ol className="flex gap-2 overflow-x-auto py-2" aria-label="Proposals waiting for review">
      {items.map((item, index) => (
        <li key={item.id}>
          <button
            type="button"
            aria-label={`Open proposal ${index + 1}`}
            aria-current={item.id === openId ? "true" : undefined}
            onClick={() => onOpen(item.id)}
            className={`block min-h-11 rounded border-2 ${item.id === openId ? "border-blue-600" : "border-transparent"}`}
          >
            <img src={imageUrl(item.cutImageId)} alt="" loading="lazy" className="h-16 w-16 object-cover" />
          </button>
        </li>
      ))}
    </ol>
  )
}
```

`src/components/recut/ProposalInfo.tsx`:

```tsx
import type { RecutProposalDetail } from "../../recut/types"

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  )
}

export default function ProposalInfo({ detail }: { detail: RecutProposalDetail }) {
  const { box } = detail
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Field label="Confidence" value={detail.confidence === null ? "not scored" : detail.confidence.toFixed(2)} />
      <Field label="Box (x, y, w x h)" value={`${box.x}, ${box.y}, ${box.w} x ${box.h}`} />
      <Field label="Detector" value={`${detail.detector} ${detail.detectorVersion}`} />
      <Field label="State" value={detail.state} />
    </dl>
  )
}
```

- [ ] **Step 4: The page**

`src/pages/RecutReviewPage.tsx`:

```tsx
import { useCallback, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import BeforeAfter from "../components/recut/BeforeAfter"
import ProposalInfo from "../components/recut/ProposalInfo"
import QueueStrip from "../components/recut/QueueStrip"
import { useRecutQueue } from "../recut/useRecutQueue"

type Props = { memesApi: MemesApi }

const LOAD_AHEAD = 5

export default function RecutReviewPage({ memesApi }: Props) {
  const [params, setParams] = useSearchParams()
  const openId = params.get("cut")
  const queue = useRecutQueue(memesApi, openId)
  const { items, total, listStatus, loadMore, detail } = queue

  const open = useCallback((id: string, replace = false) => setParams({ cut: id }, { replace }), [setParams])

  const firstId = items[0]?.id ?? null
  const detailStatus = detail?.status
  useEffect(() => {
    if (listStatus !== "ready" || firstId === null || firstId === openId) return
    if (openId === null || detailStatus === "missing") open(firstId, true)
  }, [listStatus, firstId, openId, detailStatus, open])

  const position = items.findIndex((item) => item.id === openId)
  useEffect(() => {
    if (position >= 0 && position >= items.length - LOAD_AHEAD) loadMore()
  }, [position, items.length, loadMore])

  const previousId = detail?.status === "ready" ? detail.data.previousId : null
  const nextId = detail?.status === "ready" ? detail.data.nextId : null
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return
      if (event.key === "ArrowLeft" && previousId !== null) open(previousId)
      if (event.key === "ArrowRight" && nextId !== null) open(nextId)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [previousId, nextId, open])

  let body: React.ReactNode
  if (listStatus === "loading") {
    body = <p role="status">Loading proposals...</p>
  } else if (listStatus === "failed") {
    body = (
      <div role="alert">
        <p>Could not load the proposals.</p>
        <button type="button" onClick={queue.reload} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else if (detail?.status === "ready") {
    const proposal = detail.data
    body = (
      <>
        <div className="mb-3 flex items-center gap-3">
          <button type="button" disabled={previousId === null} onClick={() => previousId !== null && open(previousId)}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Previous</button>
          <span className="text-sm">{position >= 0 ? `${position + 1} of ${total}` : `${total} waiting`}</span>
          <button type="button" disabled={nextId === null} onClick={() => nextId !== null && open(nextId)}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Next</button>
        </div>
        <BeforeAfter
          key={proposal.id}
          sourceUrl={memesApi.getImageUrlById(proposal.sourceImageId)}
          cutUrl={memesApi.getImageUrlById(proposal.cutImageId)}
          box={proposal.box}
        />
        <div className="mt-4"><ProposalInfo detail={proposal} /></div>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" disabled className="min-h-11 rounded border px-3 opacity-50">Approve</button>
          <button type="button" disabled className="min-h-11 rounded border px-3 opacity-50">Reject</button>
          <span className="text-xs text-gray-500">Deciding arrives with the approve/reject step</span>
        </div>
        <QueueStrip items={items} openId={openId} imageUrl={memesApi.getImageUrlById.bind(memesApi)} onOpen={(id) => open(id)} />
      </>
    )
  } else if (items.length === 0) {
    body = <p>No proposals waiting for review</p>
  } else if (detail?.status === "failed") {
    body = (
      <div role="alert">
        <p>Could not load this proposal.</p>
        <button type="button" onClick={queue.reload} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else {
    body = <p role="status">Loading proposal...</p>
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Recut review</h1>
      {body}
    </div>
  )
}
```

- [ ] **Step 5: Run the page tests**

Run: `pnpm exec vitest run src/pages/RecutReviewPage.test.tsx`
Expected: all pass. If the "unscored" text or the percentage text differs from the assertions, fix the component, not the test, unless the test contradicts the spec.

- [ ] **Step 6: Route and navigation, with a navigation test**

`src/app/router.tsx` (Edit tool): add `import RecutReviewPage from "../pages/RecutReviewPage";` after the `ProfileCoveragePage` import and, after the `/admin/profiles` route, `      { path: "/admin/recut", element: <RecutReviewPage memesApi={memesApi} /> },`.

`src/app/AppLayout.tsx` (Edit tool): after the `Profiles` NavLink block add

```tsx
          <NavLink
            to="/admin/recut"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Recut
          </NavLink>
```

`src/app/AppLayout.test.tsx`: read the file, then add a `<Route path="/admin/recut" element={<div>recut-content</div>} />` next to the profiles route in its test router and a test mirroring the profiles one:

```tsx
  it('marks only Recut as active on the recut page, not Admin', () => {
    renderApp('/admin/recut')
    expect(screen.getByText('Recut')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Admin')).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Recut')).toHaveAttribute('href', '/admin/recut')
  })
```

(Keep the file's existing quote style and helper names.)

- [ ] **Step 7: Run all frontend gates**

From `Frontend/memes-frontend/`: `pnpm exec tsc -b`, then `pnpm exec eslint src/ --max-warnings 0`, then `pnpm exec vitest run`, then from `Frontend/`: `bash generate-types.sh` followed by `git diff --stat Frontend/memes-frontend/src/types/generated/` (expected: no change relative to the Task 1 commit).
Expected: all clean/green.

- [ ] **Step 8: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(recut): R4 review page"
```

---

### Task 7: Spec status and the full backend suites

**Files:**
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r4-review-api-and-ui-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`

- [ ] **Step 1: Run the whole integration root and the other roots**

Run (PowerShell, repo root): `$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"; & "H:\workspace_sandbox\memes\.venv311\Scripts\python.exe" -m pytest tests/integration/ -q`
Expected: all pass (the R4 change touches no shared code, but the whole root is the contract). Then, each as its own command: `pytest tests/docs/ -q`, `pytest tests/rules/ -q`, `pytest batch/tests/ -q`, and from `Backend/`: `pytest -q`. Report any failure by name.

- [ ] **Step 2: Mark the spec and tracker**

Spec status line: `Status: implementation`; tracker row R4: status `implementation`. (`done` is set in the commit after the merge, as for R3. Edit tool / Python script that preserves line endings.)

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs(recut): R4 implementation status"
```
