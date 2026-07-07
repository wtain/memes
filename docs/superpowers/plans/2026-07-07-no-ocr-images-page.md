# No-OCR Images Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a paginated "No OCR" view (backend endpoint + web frontend page) listing images with zero `ocr_texts` rows, mirroring the existing "Untagged" page.

**Architecture:** Three-layer backend addition (repository → service → router) copying the exact shape of the existing `get_untagged` path, swapping the `NOT EXISTS` check from `ImageTag` to `OCRText`. Frontend addition copying the exact shape of the existing Untagged page (API method → MemesList prop → page component → route → nav link). No new schemas, no DB migration.

**Tech Stack:** FastAPI + SQLAlchemy async ORM (backend), React + TypeScript + Vite + vitest (frontend web).

## Global Constraints

- No new DB tables, columns, or shared JSON schemas (spec: "No new database tables, columns, or shared JSON schemas are needed").
- "No OCR data" means zero rows in `ocr_texts` for the image — not a confidence threshold (spec: Definition section).
- Android is out of scope for this plan (spec: Summary / Out of scope).
- Repositories must not call `session.commit()` (CLAUDE.md backend pattern — not relevant here since this is a read-only query, but do not add one).
- `backend_api.md` must stay in sync with the actual routers (CLAUDE.md key invariant).
- Frontend: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` must all pass before considering the frontend task done (CLAUDE.md "Before committing frontend changes").

---

### Task 1: Backend repository + service — `get_no_ocr`

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (add method after `get_untagged`, which ends at line 191)
- Modify: `Backend/app/services/image_service.py` (add method after `get_untagged`, which ends at line 156)
- Test: `Backend/tests/test_repositories/` — no existing repository-level test file was found for `image_repository.py`; this task is verified through the router test in Task 2 instead (matching existing project convention, where `get_untagged`'s repository/service logic has no dedicated unit test either — only the endpoint test in `test_images_endpoints.py`).

**Interfaces:**
- Consumes: `Storage.models.OCRText` (already imported in `image_repository.py` — see `from Storage.models import Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters`), `Backend.app.types.generated.meme.Schema as Meme`, `Backend.app.types.generated.memesearchresponse.Schema as MemeSearchResponse` (already imported in `image_service.py`).
- Produces: `ImageRepository.get_no_ocr(cursor_created_at: Optional[datetime], cursor_id: Optional[uuid.UUID], limit: int) -> Sequence[Row]` (rows have `.id`, `.filename`, `.created_at`, `.flagged`), `ImageService.get_no_ocr(cursor: Optional[str], limit: int) -> MemeSearchResponse`. Task 2 (router) calls `service.get_no_ocr(cursor=cursor, limit=limit)`.

- [ ] **Step 1: Add `get_no_ocr` to `ImageRepository`**

In `Backend/app/repositories/image_repository.py`, insert immediately after the `get_untagged` method (after line 191, before the `# slow? index?` comment on line 193):

```python
    async def get_no_ocr(
            self,
            cursor_created_at: Optional[datetime],
            cursor_id: Optional[uuid.UUID],
            limit: int,
    ):
        img = aliased(Image)
        ocr = aliased(OCRText)
        extras = aliased(ImageExtras)

        exists_subquery = (
            select(ocr.image_id)
            .where(ocr.image_id == img.id)
            .correlate(img)
            .exists()
        )

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(~exists_subquery)
        )

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )
        return results.all()
```

- [ ] **Step 2: Add `get_no_ocr` to `ImageService`**

In `Backend/app/services/image_service.py`, insert immediately after the `get_untagged` method (after line 156, before the `get_duplicates` method on line 158):

```python
    async def get_no_ocr(
            self,
            cursor: Optional[str],
            limit: int,
    ) -> MemeSearchResponse:
        cursor_created_at, cursor_id = self._decode_cursor(cursor)

        rows = await self.repo.get_no_ocr(
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
        )

        items = [
            Meme(
                id=str(r.id),
                imageUrl=f"/api/images/{r.id}",
                text=[],
                tags=[],
                originalFileName=r.filename,
                flagged=r.flagged if r.flagged is not None else False
            )
            for r in rows
        ]

        await self._fill_texts_and_tags(items)

        return self._paginate_response(rows, items, limit)
```

- [ ] **Step 3: Verify the backend still imports cleanly**

Run: `cd Backend && python -c "import app.repositories.image_repository; import app.services.image_service"`
Expected: no output, exit code 0 (no import errors).

- [ ] **Step 4: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py
git commit -m "feat: add get_no_ocr to image repository and service"
```

---

### Task 2: Backend router endpoint + tests

**Files:**
- Modify: `Backend/app/api/images.py` (add route after `get_untagged_images`, lines 114-122)
- Modify: `Backend/tests/test_images_endpoints.py` (add `TestGetNoOcrImages` class after `TestGetUntaggedImages`, which ends at line 710; also update the module docstring's endpoint list at the top)

**Interfaces:**
- Consumes: `ImageService.get_no_ocr(cursor: Optional[str], limit: int) -> MemeSearchResponse` (produced in Task 1).
- Produces: `GET /api/images/no-ocr` route, response model `MemeSearchResponse`. Task 3 (frontend) calls this exact path with `limit` and `cursor` query params.

- [ ] **Step 1: Write the failing tests**

In `Backend/tests/test_images_endpoints.py`, add after the `TestGetUntaggedImages` class (after line 710, before whatever class follows — check with `grep -n "^class" Backend/tests/test_images_endpoints.py` if unsure of the exact insertion point):

```python
class TestGetNoOcrImages:
    """Tests for GET /api/images/no-ocr endpoint."""

    def test_get_no_ocr_images_success(self, client, mock_image_service):
        """Test getting no-OCR images successfully."""
        # Arrange
        mock_response = MemeSearchResponse(
            items=[
                Meme(
                    id="no-ocr-1",
                    imageUrl="/api/images/no-ocr-1",
                    text=[],
                    tags=[],
                    originalFileName="no-ocr1.jpg",
                    flagged=False
                ),
                Meme(
                    id="no-ocr-2",
                    imageUrl="/api/images/no-ocr-2",
                    text=[],
                    tags=[],
                    originalFileName="no-ocr2.jpg",
                    flagged=False
                )
            ],
            nextCursor="next-no-ocr",
            hasNext=True,
            facets=[]
        )
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "no-ocr-1"
        assert len(data["items"][0]["text"]) == 0
        assert data["hasNext"] is True
        mock_image_service.get_no_ocr.assert_called_once()
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["limit"] == 20
        assert call_kwargs["cursor"] is None

    def test_get_no_ocr_images_with_limit(self, client, mock_image_service):
        """Test getting no-OCR images with custom limit."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr", params={"limit": 50})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_get_no_ocr_images_with_cursor(self, client, mock_image_service):
        """Test pagination of no-OCR images with cursor."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr", params={"cursor": "cursor123"})

        # Assert
        assert response.status_code == 200
        call_kwargs = mock_image_service.get_no_ocr.call_args.kwargs
        assert call_kwargs["cursor"] == "cursor123"

    def test_get_no_ocr_images_empty_results(self, client, mock_image_service):
        """Test getting no-OCR images when none found."""
        # Arrange
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_no_ocr.return_value = mock_response

        # Act
        response = client.get("/api/images/no-ocr")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 0
        assert data["hasNext"] is False

    def test_get_no_ocr_images_limit_validation(self, client, mock_image_service):
        """Test limit validation for no-OCR images endpoint."""
        # Test limit too high
        response = client.get("/api/images/no-ocr", params={"limit": 101})
        assert response.status_code == 422

        # Test limit too low
        response = client.get("/api/images/no-ocr", params={"limit": 0})
        assert response.status_code == 422
```

Also update the module docstring at the top of the file (lines 3-10) to add `- get_no_ocr_images` after `- get_untagged_images` (line 8).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Backend && pytest tests/test_images_endpoints.py::TestGetNoOcrImages -v`
Expected: FAIL — 404 on `/api/images/no-ocr` (route doesn't exist yet), or `AttributeError` on `mock_image_service.get_no_ocr` since the route isn't wired to call it.

- [ ] **Step 3: Add the router endpoint**

In `Backend/app/api/images.py`, insert immediately after `get_untagged_images` (after line 122, before the `# Must be before /{image_id} endpoint` comment on line 125 that precedes `get_duplicate_images`):

```python
# Must be before /{image_id} endpoint
@router.get("/no-ocr", response_model=MemeSearchResponse)
async def get_no_ocr_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_no_ocr(cursor=cursor, limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Backend && pytest tests/test_images_endpoints.py::TestGetNoOcrImages -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd Backend && pytest`
Expected: all tests pass (same pass count as before, plus the 5 new ones).

- [ ] **Step 6: Commit**

```bash
git add Backend/app/api/images.py Backend/tests/test_images_endpoints.py
git commit -m "feat: add GET /api/images/no-ocr endpoint"
```

---

### Task 3: Update `backend_api.md`

**Files:**
- Modify: `backend_api.md` (add subsection after "Get Untagged Images", which ends at line 440, before "Get Duplicate Images" at line 442)

**Interfaces:**
- Consumes: the endpoint contract from Task 2 (`GET /api/images/no-ocr`, params `limit`/`cursor`, response `MemeSearchResponse`).
- Produces: documentation only — no code interface.

- [ ] **Step 1: Add the doc section**

In `backend_api.md`, insert after line 440 (`- **Example**: \`GET /api/images/untagged?limit=30\``) and before line 442 (`#### Get Duplicate Images`):

```markdown

#### Get No-OCR Images

Retrieve images that have no OCR text at all.

- **URL**: `/api/images/no-ocr`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: no-cache
- **Example**: `GET /api/images/no-ocr?limit=30`
```

- [ ] **Step 2: Commit**

```bash
git add backend_api.md
git commit -m "docs: document GET /api/images/no-ocr in backend_api.md"
```

---

### Task 4: Frontend API client — `iterateNoOcrMemes`

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts` (add method to interface, after `iterateUntaggedMemes` on line 6)
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts` (add implementation, after `iterateUntaggedMemes`, lines 38-60)
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts` (add mock, after `iterateUntaggedMemes` on line 15)

**Interfaces:**
- Consumes: `MemeSearchResponse` type from `../types/generated/all` (already imported in both files).
- Produces: `MemesApi.iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>`. Task 5 (`MemesList`) calls `memesApi.iterateNoOcrMemes(21, next)`.

- [ ] **Step 1: Add the method to the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, insert after line 6 (`iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>`):

```typescript
  iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>
```

- [ ] **Step 2: Implement it in `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, insert after the `iterateUntaggedMemes` method (after line 60, before `iterateDuplicates` on line 62):

```typescript
  async iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    let paramsString = ""
    if (limit) {
      paramsString += `limit=${limit}&`
    }
    if (cursor) {
      paramsString += `cursor=${cursor}`
    }
    const response = await fetch(
      `${this.baseUrl}/api/images/no-ocr?${paramsString}`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }
```

- [ ] **Step 3: Add the mock to `mockApi.ts`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, insert after line 15 (`iterateUntaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),`):

```typescript
    iterateNoOcrMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
```

- [ ] **Step 4: Type-check to confirm the interface, implementation, and mock all agree**

Run (from `Frontend/memes-frontend/`): `tsc -b`
Expected: no errors. (This is the verification step for this task — there's no separate unit test for a thin fetch wrapper, matching the existing convention for `iterateUntaggedMemes`/`iterateDuplicates`, which also have no dedicated test file.)

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: add iterateNoOcrMemes to MemesApi client"
```

---

### Task 5: `MemesList` — `listNoOcr` prop + test

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesList.tsx`
- Modify: `Frontend/memes-frontend/src/components/MemesList.test.tsx` (add test after the `listDuplicates` test, lines 59-66)

**Interfaces:**
- Consumes: `MemesApi.iterateNoOcrMemes` (produced in Task 4).
- Produces: `MemesList` accepts a `listNoOcr?: boolean` prop. Task 6 (`ExploreNoOcrPage`) passes `listNoOcr={true}`.

- [ ] **Step 1: Write the failing test**

In `Frontend/memes-frontend/src/components/MemesList.test.tsx`, insert after the `listDuplicates` test (after line 66, before the `onFacetsChanged` test on line 68):

```typescript
  it('calls iterateNoOcrMemes instead of searchMemes when listNoOcr is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listNoOcr />)
    await waitFor(() => {
      expect(api.iterateNoOcrMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `Frontend/memes-frontend/`): `vitest run src/components/MemesList.test.tsx`
Expected: FAIL — `listNoOcr` prop doesn't exist / `api.iterateNoOcrMemes` never called (component falls through to `searchMemes`).

- [ ] **Step 3: Add the prop and branch to `MemesList`**

In `Frontend/memes-frontend/src/components/MemesList.tsx`:

Modify the `MemesListProps` type (lines 7-16) to add the new prop after `listFlagged?: boolean` (line 14):

```typescript
type MemesListProps = {
  memesApi: MemesApi
  filter?: string
  onFacetsChanged?: (facets: Facet[]) => void
  tagFilters?: Record<string, string[]>
  listUntagged?: boolean
  listDuplicates?: boolean
  listFlagged?: boolean
  listNoOcr?: boolean
  listRecommendations?: boolean
}
```

Modify the component signature (line 18) to destructure `listNoOcr`:

```typescript
export function MemesList({ memesApi, filter, onFacetsChanged, tagFilters, listUntagged, listDuplicates, listFlagged, listNoOcr, listRecommendations }: MemesListProps) {
```

Modify `getResponseFromBackend` (lines 86-108) to add a branch after the `listFlagged` check (after line 98, before `listRecommendations` on line 99):

```typescript
      if (listNoOcr) {
        return await memesApi.iterateNoOcrMemes(21, next)
      }
```

Modify the `useCallback` dependency array (line 109) to include `listNoOcr`:

```typescript
  }, [filter, tagFilters, memesApi, onFacetsChanged, listUntagged, listDuplicates, listFlagged, listNoOcr, listRecommendations])
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `Frontend/memes-frontend/`): `vitest run src/components/MemesList.test.tsx`
Expected: all tests in the file pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemesList.tsx Frontend/memes-frontend/src/components/MemesList.test.tsx
git commit -m "feat: add listNoOcr prop to MemesList"
```

---

### Task 6: `ExploreNoOcrPage`, route, and nav link

**Files:**
- Create: `Frontend/memes-frontend/src/pages/ExploreNoOcrPage.tsx`
- Modify: `Frontend/memes-frontend/src/app/router.tsx`
- Modify: `Frontend/memes-frontend/src/app/AppLayout.tsx`

**Interfaces:**
- Consumes: `MemesList` with `listNoOcr` prop (produced in Task 5), `MemesApi` type.
- Produces: page component `ExploreNoOcrPage`, route `/no-ocr`, nav link labeled "No OCR". Nothing downstream depends on these (leaf of the dependency chain) — verified manually and via lint/build, not a unit test, matching how `ExploreUntaggedPage`/`ExploreDuplicatesPage`/`ExploreFlaggedPage` have no dedicated page-level tests today.

- [ ] **Step 1: Create the page component**

Create `Frontend/memes-frontend/src/pages/ExploreNoOcrPage.tsx`:

```typescript
import { MemesList } from "../components/MemesList"
import type { MemesApi } from "../api/MemesApi"

type Props = {
  memesApi: MemesApi
}

export default function ExploreNoOcrPage({ memesApi }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">No OCR</h1>
      <MemesList memesApi={memesApi} listNoOcr={true} />
    </div>
  )
}
```

- [ ] **Step 2: Add the route**

In `Frontend/memes-frontend/src/app/router.tsx`, add the import after line 11 (`import ExploreDuplicatesPage from "../pages/ExploreDuplicatesPage";`):

```typescript
import ExploreNoOcrPage from "../pages/ExploreNoOcrPage";
```

Add the route after line 33 (`{ path: "/duplicates", element: <ExploreDuplicatesPage memesApi={memesApi} /> },`), before the `/flagged` route on line 34:

```typescript
      { path: "/no-ocr", element: <ExploreNoOcrPage memesApi={memesApi} /> },
```

- [ ] **Step 3: Add the nav link**

In `Frontend/memes-frontend/src/app/AppLayout.tsx`, insert after the "Duplicates" `NavLink` block (after line 33, before the "Flagged" `NavLink` on line 35):

```typescript
          <NavLink
            to="/no-ocr"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            No OCR
          </NavLink>

```

- [ ] **Step 4: Type-check, lint, and run the full frontend test suite**

Run (from `Frontend/memes-frontend/`):
```bash
tsc -b
eslint src/ --max-warnings 0
vitest run
```
Expected: all three pass with no errors.

- [ ] **Step 5: Manual smoke test**

Start the metal backend and frontend dev server per `CLAUDE.md`:
```powershell
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
```
```bash
cd Frontend/memes-frontend && pnpm dev
```
Open `http://localhost:5173/no-ocr` in a browser. Confirm: the "No OCR" nav link appears and highlights when active, the page loads images (or shows "Nothing to show" if every image has OCR data), and scrolling triggers pagination if there are more than ~21 results.

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/pages/ExploreNoOcrPage.tsx Frontend/memes-frontend/src/app/router.tsx Frontend/memes-frontend/src/app/AppLayout.tsx
git commit -m "feat: add No OCR explore page, route, and nav link"
```

---

## Self-Review Notes

- **Spec coverage:** Repository/service (Task 1) ✓, router endpoint (Task 2) ✓, backend tests (Task 2) ✓, `backend_api.md` (Task 3) ✓, `MemesApi`/`HttpMemesApi` (Task 4) ✓, `mockApi.ts` (Task 4) ✓, `MemesList` prop + test (Task 5) ✓, `ExploreNoOcrPage` + route + nav (Task 6) ✓, manual verification (Task 6, Step 5) ✓. Android and DB/schema changes are explicitly out of scope per the spec and are not present in any task.
- **Placeholder scan:** No TBD/TODO markers; every step has literal code or an exact command with expected output.
- **Type consistency:** `get_no_ocr` name and signature match across `ImageRepository` (Task 1), `ImageService` (Task 1), router (Task 2), and tests (Task 2). `iterateNoOcrMemes` name and signature match across `MemesApi` interface, `HttpMemesApi`, `mockApi.ts`, and `MemesList` (Tasks 4-5). `listNoOcr` prop name matches between `MemesList` (Task 5) and `ExploreNoOcrPage` (Task 6).