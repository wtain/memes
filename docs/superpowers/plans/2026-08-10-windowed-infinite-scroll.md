# Windowed, Bidirectional Infinite Scroll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound `MemesList`'s unbounded DOM/memory growth by rendering through `react-virtuoso`, add scroll-up ("load earlier") support symmetric to today's scroll-down loading, and fix `ExploreDuplicatesPage` losing the user's place on return navigation.

**Architecture:** A new `useWindowedPagination` hook owns a capped deque of fetched pages plus cursor-replay bookkeeping, decoupled from rendering. Two rendering components consume it: `MemesList` (rewritten, `VirtuosoGrid`, for the five flat listings) and a new `MemesDuplicatesList` (`Virtuoso`, one cluster per row, for the duplicates page only — its own component because it's the only page needing cluster-boundary-aware rendering and cold backward loading). The duplicates backend gains a real "page before cursor" query so a cold deep link (`?cursor=X`) can load earlier than `X`, not just resume forward from it.

**Tech Stack:** React 19, TypeScript, `react-virtuoso` (new dependency), Vitest + `@testing-library/react`, FastAPI, SQLAlchemy async, pytest.

## Global Constraints

- Frontend: `tsc -b`, `eslint src/` (0 warnings — `--max-warnings 0`), `vitest run` must all pass before any commit that touches `Frontend/memes-frontend/`.
- Backend: no automated CI gate; per `CLAUDE.md`, confirm the server starts without import errors and manually verify the changed endpoint's response shape.
- Never combine `Backend/tests/` and `tests/integration/` in one `pytest` invocation (separate `asyncio_mode` configs — see `CLAUDE.md`'s known gotchas). Run them as separate commands.
- Any change to `shared/schemas/` requires regenerating both Frontend and Backend generated types and verifying no unexpected diff beyond the intended field.
- Fetch page sizes stay as they are today per listing type (21/36/40) — this plan does not change them.
- `MAX_PAGES = 4` is the windowing cap used throughout (see Task 5).

---

### Task 1: Add `previousCursor` to the shared `MemeSearchResponse` schema

The duplicates backward query (Task 2) needs a way to tell the client "the cursor to fetch one page further backward." Reusing `nextCursor` for this would make its meaning direction-dependent everywhere else it's used — instead, add a new, always-optional field that only the duplicates endpoint ever populates.

**Files:**
- Modify: `shared/schemas/memesearchresponse.schema.json`
- Modify (generated, do not hand-edit content — just regenerate): `Backend/app/types/generated/memesearchresponse.py`
- Modify (generated): `Frontend/memes-frontend/src/types/generated/all.d.ts`

**Interfaces:**
- Produces: `MemeSearchResponse.previousCursor: string | undefined` (frontend), `Schema.previousCursor: str | None = None` (backend Pydantic) — consumed by Task 2 (backend sets it) and Task 5/7 (frontend hook reads it).

- [ ] **Step 1: Add the field to the schema**

In `shared/schemas/memesearchresponse.schema.json`, add `previousCursor` alongside the existing `nextCursor`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "memesearchresponse.schema.json",
  "title": "MemeSearchResponse",
  "type": "object",
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "$ref": "./meme.schema.json"
      }
    },
    "facets": {
      "type": "array",
      "items": {
        "$ref": "./facet.schema.json"
      }
    },
    "nextCursor": { "type": "string" },
    "hasNext": { "type": "boolean" },
    "previousCursor": { "type": "string" }
  }
}
```

- [ ] **Step 2: Regenerate Backend types**

Run from `Backend/`:
```
datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```

Confirm `Backend/app/types/generated/memesearchresponse.py` now has `previousCursor: str | None = None` and nothing else changed unexpectedly (`git diff Backend/app/types/generated/`).

- [ ] **Step 3: Regenerate Frontend types**

Run from `Frontend/`:
```
bash generate-types.sh
```

Confirm `Frontend/memes-frontend/src/types/generated/all.d.ts`'s `MemeSearchResponse` interface now includes `previousCursor?: string` and nothing else changed unexpectedly (`git diff Frontend/memes-frontend/src/types/generated/`).

- [ ] **Step 4: Commit**

```bash
git add shared/schemas/memesearchresponse.schema.json Backend/app/types/generated/memesearchresponse.py Frontend/memes-frontend/src/types/generated/all.d.ts
git commit -m "feat: add previousCursor to MemeSearchResponse schema"
```

---

### Task 2: Backend — real "page before cursor" query for duplicates

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (add `get_duplicates_clustered_before`, near the existing `get_duplicates_clustered` at line 416)
- Modify: `Backend/app/services/image_service.py` (add `direction` param to `get_duplicates_clustered`)
- Modify: `Backend/app/api/images.py` (add `direction` query param to the `/duplicates` route, ~line 175)
- Test: `Backend/tests/test_image_service.py`
- Test: `Backend/tests/test_images_endpoints.py`

**Interfaces:**
- Consumes: `ImageRepository`'s existing `tuple_`, `aliased`, `Image`, `TmpImageClusters`, `ImageExtras` imports (already present in `image_repository.py`); `ImageService._decode_cluster_cursor`/`_encode_cluster_cursor` (already present, lines 401-418).
- Produces: `ImageRepository.get_duplicates_clustered_before(cursor_cluster_id: Optional[int], cursor_image_id: Optional[uuid.UUID], limit: int) -> list[tuple]` (same row shape as `get_duplicates_clustered`, in **descending** `(cluster_id, image_id)` order, up to `limit + 1` rows). `ImageService.get_duplicates_clustered(cursor, limit, threshold, direction: Literal["forward", "backward"] = "forward")` — response's `previousCursor` set only for `direction="backward"`.

- [ ] **Step 1: Write the failing service test**

Add to `Backend/tests/test_image_service.py`, in the same `TestGetDuplicatesClusteredPagination` class added by the previous cursor fix (reuse its `IMAGE_A`..`IMAGE_D` fixtures and `_make_fake_rows`):

```python
    def _wire_fake_repo_backward(self, mock_repo):
        full_rows = self._make_fake_rows()  # A,B,C in cluster 1; D in cluster 2

        async def fake_get_duplicates_clustered_before(cursor_cluster_id, cursor_image_id, limit):
            if cursor_cluster_id is not None and cursor_image_id is not None:
                cursor = (cursor_cluster_id, cursor_image_id)
                rows = [r for r in full_rows if (r[3], r[0]) < cursor]
            else:
                rows = list(full_rows)
            rows_desc = sorted(rows, key=lambda r: (r[3], r[0]), reverse=True)
            return rows_desc[: limit + 1]

        mock_repo.get_duplicates_clustered_before.side_effect = fake_get_duplicates_clustered_before
        mock_repo.get_texts.return_value = []
        mock_repo.get_tags.return_value = []

    async def test_backward_fetch_returns_items_before_cursor_in_ascending_order(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(2, self.IMAGE_D)  # anchor: right after D

        page = await service.get_duplicates_clustered(cursor=cursor, limit=2, threshold=0.2, direction="backward")

        # limit=2 takes the 2 rows immediately before D in descending order (C, B),
        # reversed back to ascending for the response.
        assert [item.id for item in page.items] == [str(self.IMAGE_B), str(self.IMAGE_C)]
        # Forward continuation from this backward page must be the cursor we anchored on.
        assert page.nextCursor == cursor
        assert page.hasNext is True
        # One more row (A) exists before this page, so previousCursor must be set.
        assert page.previousCursor is not None

    async def test_backward_fetch_at_true_beginning_has_no_previous_cursor(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(1, self.IMAGE_B)  # anchor: right after B

        page = await service.get_duplicates_clustered(cursor=cursor, limit=5, threshold=0.2, direction="backward")

        assert [item.id for item in page.items] == [str(self.IMAGE_A)]
        assert page.previousCursor is None  # nothing before A

    async def test_backward_fetch_with_no_earlier_items_returns_empty(self, service, mock_repo):
        self._wire_fake_repo_backward(mock_repo)
        cursor = service._encode_cluster_cursor(1, self.IMAGE_A)  # anchor: right before A, nothing earlier

        page = await service.get_duplicates_clustered(cursor=cursor, limit=5, threshold=0.2, direction="backward")

        assert page.items == []
        assert page.previousCursor is None
```

- [ ] **Step 2: Run to verify it fails**

```
cd Backend && pytest tests/test_image_service.py -k TestGetDuplicatesClusteredPagination -v
```
Expected: the three new tests FAIL with `AttributeError` (`get_duplicates_clustered_before` doesn't exist on the mock's auto-spec, or `direction` is an unexpected kwarg).

- [ ] **Step 3: Implement the repository method**

In `Backend/app/repositories/image_repository.py`, add immediately after `get_duplicates_clustered` (after line 446's closing, before `get_flagged`):

```python
    async def get_duplicates_clustered_before(self,
                             cursor_cluster_id: Optional[int],
                             cursor_image_id: Optional[uuid.UUID],
                             limit: int,):
        img = aliased(Image)
        cluster = aliased(TmpImageClusters)
        extras = aliased(ImageExtras)

        query = (
            select(
                img.id,
                img.filename,
                img.created_at,
                cluster.cluster_id,
                extras.flagged,
            )
            .join(cluster, cluster.image_id == img.id)
            .outerjoin(extras, img.id == extras.image_id)
            .where(img.status == "active")
        )

        if cursor_cluster_id is not None and cursor_image_id is not None:
            query = query.where(
                tuple_(cluster.cluster_id, img.id) < tuple_(cursor_cluster_id, cursor_image_id)
            )

        images = await self.session.execute(
            query.order_by(
                cluster.cluster_id.desc(),
                img.id.desc(),
           ).limit(limit + 1)
        )

        return [(id, filename, created_at, cluster_id, flagged, ) for (id, filename, created_at, cluster_id, flagged,) in images]
```

- [ ] **Step 4: Implement the service `direction` branch**

In `Backend/app/services/image_service.py`:

1. Change the import line (line 6) to:
```python
from typing import Optional, Literal
```

2. Replace the `get_duplicates_clustered` method body (currently lines 286-325) with:

```python
    async def get_duplicates_clustered(
            self,
            cursor: Optional[str],
            limit: int,
            threshold: float,
            direction: Literal["forward", "backward"] = "forward",
    ) -> MemeSearchResponse:
        cursor_cluster_id, cursor_image_id = self._decode_cluster_cursor(cursor)

        if direction == "backward":
            rows = await self.repo.get_duplicates_clustered_before(
                cursor_cluster_id=cursor_cluster_id,
                cursor_image_id=cursor_image_id,
                limit=limit,
            )
            has_more_before = len(rows) > limit
            rows = rows[:limit]
            images = list(reversed(rows))

            previous_cursor = None
            if has_more_before and images:
                first_id, _, _, first_cluster_id, _ = images[0]
                previous_cursor = self._encode_cluster_cursor(first_cluster_id, first_id)

            # A backward fetch is always anchored on a cursor that came from a
            # real forward position, so resuming forward from here always
            # means "go back to where we started."
            next_cursor = cursor
            has_next = True
        else:
            images = await self.repo.get_duplicates_clustered(
                cursor_cluster_id=cursor_cluster_id,
                cursor_image_id=cursor_image_id,
                limit=limit,
            )
            has_next = len(images) > limit
            images = images[:limit]

            if has_next and images:
                last_id, _, _, last_cluster_id, _ = images[-1]
                next_cursor = self._encode_cluster_cursor(last_cluster_id, last_id)
            else:
                next_cursor = None
            previous_cursor = None

        items = [
            Meme(
                id=str(id),
                imageUrl=f"/api/images/{id}",
                text=[],
                tags=[],
                originalFileName=filename,
                flagged=flagged if flagged is not None else False,
                clusterId=cluster_id,
            )
            for (id, filename, created_at, cluster_id, flagged, ) in images
        ]

        # safe on any length
        await self._fill_texts_and_tags(items)

        return MemeSearchResponse(
            items=items, nextCursor=next_cursor, hasNext=has_next,
            previousCursor=previous_cursor, facets=[],
        )
```

- [ ] **Step 5: Run to verify the service tests pass**

```
cd Backend && pytest tests/test_image_service.py -v
```
Expected: all PASS, including the three new backward-fetch tests and the pre-existing forward-pagination ones.

- [ ] **Step 6: Write the failing router test**

Add to `Backend/tests/test_images_endpoints.py`'s `TestGetDuplicateImages` class:

```python
    def test_get_duplicates_with_backward_direction(self, client, mock_image_service):
        """Test that direction=backward is passed through to the service."""
        mock_response = MemeSearchResponse(
            items=[], nextCursor="dup-cursor", hasNext=True, previousCursor=None, facets=[]
        )
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        response = client.get("/api/images/duplicates", params={"cursor": "dup-cursor", "direction": "backward"})

        assert response.status_code == 200
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["direction"] == "backward"
        assert call_kwargs["cursor"] == "dup-cursor"

    def test_get_duplicates_direction_defaults_to_forward(self, client, mock_image_service):
        mock_response = MemeSearchResponse(items=[], nextCursor=None, hasNext=False, facets=[])
        mock_image_service.get_duplicates_clustered.return_value = mock_response

        response = client.get("/api/images/duplicates")

        assert response.status_code == 200
        call_kwargs = mock_image_service.get_duplicates_clustered.call_args.kwargs
        assert call_kwargs["direction"] == "forward"

    def test_get_duplicates_invalid_direction_rejected(self, client, mock_image_service):
        response = client.get("/api/images/duplicates", params={"direction": "sideways"})
        assert response.status_code == 422
```

- [ ] **Step 7: Run to verify it fails**

```
cd Backend && pytest tests/test_images_endpoints.py -k TestGetDuplicateImages -v
```
Expected: FAIL — `direction` isn't a recognized kwarg on the route yet, so the mock's `call_args.kwargs` won't have it (the first two tests fail on the assertion; the third fails because an unrecognized query param is currently just ignored, not rejected with 422).

- [ ] **Step 8: Implement the router param**

In `Backend/app/api/images.py`, replace the `/duplicates` route (lines 174-184):

```python
# Must be before /{image_id} endpoint
@router.get("/duplicates", response_model=MemeSearchResponse)
async def get_duplicate_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.05, ge=0.0, le=1.0),
    cursor: Optional[str] = None,
    direction: Literal["forward", "backward"] = "forward",
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_duplicates_clustered(cursor=cursor, limit=limit, threshold=threshold, direction=direction)
```

(`Literal` is already imported at the top of this file — line 3.)

- [ ] **Step 9: Run to verify it passes**

```
cd Backend && pytest tests/test_images_endpoints.py -v
```
Expected: all PASS.

- [ ] **Step 10: Full Backend/tests root + integration root sanity check**

Per `CLAUDE.md`'s shared-code testing gotcha, this touches `image_repository.py`/`image_service.py` again — run both full roots:

```
cd Backend && pytest
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all PASS. (If the integration DB isn't reachable in this environment, note that explicitly rather than skipping silently.)

- [ ] **Step 11: Commit**

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py Backend/app/api/images.py Backend/tests/test_image_service.py Backend/tests/test_images_endpoints.py
git commit -m "feat: add backward pagination to the duplicates endpoint"
```

---

### Task 3: Add `react-virtuoso` dependency

**Files:**
- Modify: `Frontend/memes-frontend/package.json`
- Modify: `Frontend/memes-frontend/pnpm-lock.yaml` (auto-updated by pnpm)

- [ ] **Step 1: Install**

From `Frontend/memes-frontend/`:
```
pnpm add react-virtuoso
```

- [ ] **Step 2: Verify it builds**

```
tsc -b
```
Expected: PASS (no usage yet, just confirms the install didn't break type resolution).

- [ ] **Step 3: Commit**

```bash
git add Frontend/memes-frontend/package.json Frontend/memes-frontend/pnpm-lock.yaml
git commit -m "chore: add react-virtuoso dependency"
```

---

### Task 4: Frontend API layer — `direction` param for `iterateDuplicates`

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts:18`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts:92-117`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts:17`

**Interfaces:**
- Produces: `MemesApi.iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse>` — consumed by Task 7 (`MemesDuplicatesList`).

- [ ] **Step 1: Update the interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, replace line 18:

```typescript
  iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse>;
```

- [ ] **Step 2: Update the HTTP implementation**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, replace the `iterateDuplicates` method (lines 92-117):

```typescript
  async iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    if (threshold) params.set("threshold", String(threshold))
    if (direction) params.set("direction", direction)
    const response = await fetch(
      `${this.baseUrl}/api/images/duplicates?${params.toString()}`,
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

(Switching from manual string concatenation to `URLSearchParams` here to correctly handle the new optional 4th param without trailing-`&` bugs — matches the pattern `iterateFlaggedMemes` already uses a few methods below.)

- [ ] **Step 3: Update the test mock**

`Frontend/memes-frontend/src/test/mockApi.ts` line 17's `iterateDuplicates: vi.fn().mockResolvedValue(...)` doesn't need a signature change (vitest mocks are structurally typed against the interface automatically) — no edit needed here. Confirm by running `tsc -b` after Step 1-2.

- [ ] **Step 4: Verify**

From `Frontend/memes-frontend/`:
```
tsc -b
eslint src/api/
vitest run src/components/MemesList.test.tsx
```
Expected: all PASS (existing `iterateDuplicates` tests still call it with 2-3 args, which remains valid since the new param is optional).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts
git commit -m "feat: add direction param to iterateDuplicates"
```

---

### Task 5: `useWindowedPagination` hook

The core windowing/cursor-replay logic, independent of any rendering library — testable on its own via `renderHook`.

**Files:**
- Create: `Frontend/memes-frontend/src/hooks/useWindowedPagination.ts`
- Test: `Frontend/memes-frontend/src/hooks/useWindowedPagination.test.ts`

**Interfaces:**
- Consumes: `Meme` type from `../types/generated/all`.
- Produces (consumed by Tasks 6 and 7):
  ```typescript
  export type FetchDirection = "forward" | "backward"

  export type FetchPageResult = {
    items: Meme[]
    nextCursor?: string
    hasNext?: boolean
    previousCursor?: string
  }

  export type FetchPageFn = (cursor: string | undefined, direction: FetchDirection) => Promise<FetchPageResult>

  export type Page = {
    cursor: string | undefined
    items: Meme[]
  }

  export type UseWindowedPaginationOptions = {
    fetchPage: FetchPageFn
    initialCursor?: string
    resetKey: string
    maxPages?: number
    supportsColdBackward?: boolean
  }

  export type UseWindowedPaginationResult = {
    pages: Page[]
    items: Meme[]
    firstItemIndex: number
    loading: boolean
    hasMoreForward: boolean
    hasMoreBackward: boolean
    loadForward: () => Promise<void>
    loadBackward: () => Promise<void>
  }

  export function useWindowedPagination(options: UseWindowedPaginationOptions): UseWindowedPaginationResult

  export function cursorForVirtualIndex(pages: Page[], firstItemIndex: number, virtualIndex: number): string | undefined
  ```
  The hook automatically triggers a `loadForward()` on mount and whenever `resetKey` changes —
  callers (Tasks 6/7) don't need to call it themselves for the initial page, only for subsequent
  `endReached`/`startReached` events.

- [ ] **Step 1: Write the failing tests**

Create `Frontend/memes-frontend/src/hooks/useWindowedPagination.test.ts`. Note throughout: the hook
auto-triggers a `loadForward()` on mount and on every `resetKey` change (see Step 3), so most tests
`waitFor` that automatic first page rather than calling `loadForward()` manually for it — calling it
manually in the same tick would be a no-op against the already-in-flight auto-load (`loadingRef`
guards concurrent calls), and racing an assertion against it directly (without `waitFor`) would be
flaky.

```typescript
import { renderHook, act, waitFor } from '@testing-library/react'
import { useWindowedPagination, cursorForVirtualIndex, type FetchPageFn, type Page } from './useWindowedPagination'
import type { Meme } from '../types/generated/all'

function makeMemes(prefix: string, count: number): Meme[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${i}`, imageUrl: `/images/${prefix}-${i}.jpg`, text: [], tags: [],
  }))
}

describe('useWindowedPagination', () => {
  it('fetches the first page automatically on mount with no cursor', async () => {
    const fetchPage: FetchPageFn = vi.fn().mockResolvedValue({
      items: makeMemes('p0', 3), nextCursor: 'c1', hasNext: true,
    })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k' }))

    await waitFor(() => expect(result.current.items).toHaveLength(3))

    expect(fetchPage).toHaveBeenCalledWith(undefined, 'forward')
    expect(result.current.hasMoreForward).toBe(true)
  })

  it('evicts the oldest page and shifts firstItemIndex once maxPages is exceeded', async () => {
    const fetchPage: FetchPageFn = vi.fn()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded
    await act(async () => { await result.current.loadForward() }) // p1
    const indexBeforeEviction = result.current.firstItemIndex
    await act(async () => { await result.current.loadForward() }) // p2 -- evicts p0

    // p0 (2 items) evicted; only p1 + p2 remain (4 items), firstItemIndex advanced by 2.
    expect(result.current.items.map(m => m.id)).toEqual(['p1-0', 'p1-1', 'p2-0', 'p2-1'])
    expect(result.current.firstItemIndex).toBe(indexBeforeEviction + 2)
  })

  it('replays a remembered cursor on loadBackward after eviction', async () => {
    const fetchPage: FetchPageFn = vi.fn()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded, cursor undefined
    await act(async () => { await result.current.loadForward() }) // p1, cursor c1
    await act(async () => { await result.current.loadForward() }) // p2, cursor c2 -- p0 evicted

    fetchPage.mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    const firstItemIndexBefore = result.current.firstItemIndex
    await act(async () => { await result.current.loadBackward() })

    expect(fetchPage).toHaveBeenLastCalledWith(undefined, 'forward') // p0's own cursor was undefined
    expect(result.current.items[0].id).toBe('p0-0')
    expect(result.current.firstItemIndex).toBe(firstItemIndexBefore - 2)
  })

  it('does not attempt loadBackward with no history and supportsColdBackward unset', async () => {
    const fetchPage: FetchPageFn = vi.fn().mockResolvedValue({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k' }))
    await waitFor(() => expect(result.current.items).toHaveLength(2))

    expect(result.current.hasMoreBackward).toBe(false)
    const callCountBefore = fetchPage.mock.calls.length
    await act(async () => { await result.current.loadBackward() })
    expect(fetchPage.mock.calls.length).toBe(callCountBefore)
  })

  it('supportsColdBackward calls fetchPage with direction "backward" using the anchor cursor', async () => {
    const fetchPage: FetchPageFn = vi.fn()
      .mockResolvedValueOnce({ items: [], hasNext: false }) // the auto forward load on mount
      .mockResolvedValueOnce({ items: makeMemes('before', 2), previousCursor: undefined }) // the backward fetch
    const { result } = renderHook(() =>
      useWindowedPagination({ fetchPage, resetKey: 'k', initialCursor: 'deep-link-cursor', supportsColdBackward: true })
    )
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1))
    expect(result.current.hasMoreBackward).toBe(true)

    await act(async () => { await result.current.loadBackward() })

    expect(fetchPage).toHaveBeenLastCalledWith('deep-link-cursor', 'backward')
    expect(result.current.items.map(m => m.id)).toEqual(['before-0', 'before-1'])
  })

  it('marks coldBackward exhausted once a backward fetch returns no items', async () => {
    const fetchPage: FetchPageFn = vi.fn().mockResolvedValue({ items: [] })
    const { result } = renderHook(() =>
      useWindowedPagination({ fetchPage, resetKey: 'k', initialCursor: 'x', supportsColdBackward: true })
    )
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1)) // the auto forward load on mount

    await act(async () => { await result.current.loadBackward() })

    expect(result.current.hasMoreBackward).toBe(false)
    expect(result.current.items).toHaveLength(0)
  })

  it('resets all state when resetKey changes', async () => {
    const fetchPage: FetchPageFn = vi.fn().mockResolvedValue({ items: makeMemes('a', 1), hasNext: false })
    const { result, rerender } = renderHook(
      ({ key }) => useWindowedPagination({ fetchPage, resetKey: key }),
      { initialProps: { key: 'first' } }
    )
    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['a-0']))

    fetchPage.mockResolvedValue({ items: makeMemes('b', 1), hasNext: false })
    rerender({ key: 'second' })
    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['b-0']))
  })
})

describe('cursorForVirtualIndex', () => {
  const pages: Page[] = [
    { cursor: undefined, items: makeMemes('p0', 2) },
    { cursor: 'c1', items: makeMemes('p1', 3) },
  ]

  it('returns the owning page cursor for an index within the second page', () => {
    // firstItemIndex=100 -> p0 occupies [100,101], p1 occupies [102,103,104]
    expect(cursorForVirtualIndex(pages, 100, 103)).toBe('c1')
  })

  it('returns the first page cursor for an index within the first page', () => {
    expect(cursorForVirtualIndex(pages, 100, 100)).toBeUndefined()
  })

  it('clamps to the first page cursor for an out-of-range low index', () => {
    expect(cursorForVirtualIndex(pages, 100, 50)).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```
vitest run src/hooks/useWindowedPagination.test.ts
```
Expected: FAIL — module `./useWindowedPagination` doesn't exist yet.

- [ ] **Step 3: Implement the hook**

Create `Frontend/memes-frontend/src/hooks/useWindowedPagination.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react"
import type { Meme } from "../types/generated/all"

export type FetchDirection = "forward" | "backward"

export type FetchPageResult = {
  items: Meme[]
  nextCursor?: string
  hasNext?: boolean
  previousCursor?: string
}

export type FetchPageFn = (cursor: string | undefined, direction: FetchDirection) => Promise<FetchPageResult>

export type Page = {
  cursor: string | undefined
  items: Meme[]
}

export type UseWindowedPaginationOptions = {
  fetchPage: FetchPageFn
  initialCursor?: string
  /** Changing this re-fetches from scratch (mirrors filter/tagFilters changes today). */
  resetKey: string
  maxPages?: number
  supportsColdBackward?: boolean
}

export type UseWindowedPaginationResult = {
  pages: Page[]
  items: Meme[]
  firstItemIndex: number
  loading: boolean
  hasMoreForward: boolean
  hasMoreBackward: boolean
  loadForward: () => Promise<void>
  loadBackward: () => Promise<void>
}

const DEFAULT_MAX_PAGES = 4
const FIRST_ITEM_INDEX_START = 10_000

export function cursorForVirtualIndex(pages: Page[], firstItemIndex: number, virtualIndex: number): string | undefined {
  let localIndex = virtualIndex - firstItemIndex
  if (localIndex < 0) return pages[0]?.cursor
  for (const page of pages) {
    if (localIndex < page.items.length) return page.cursor
    localIndex -= page.items.length
  }
  return pages[pages.length - 1]?.cursor
}

export function useWindowedPagination({
  fetchPage,
  initialCursor,
  resetKey,
  maxPages = DEFAULT_MAX_PAGES,
  supportsColdBackward = false,
}: UseWindowedPaginationOptions): UseWindowedPaginationResult {
  const [pages, setPages] = useState<Page[]>([])
  const [firstItemIndex, setFirstItemIndex] = useState(FIRST_ITEM_INDEX_START)
  const [loading, setLoading] = useState(false)
  const [hasMoreForward, setHasMoreForward] = useState(true)
  const [hasMoreBackward, setHasMoreBackward] = useState(supportsColdBackward)

  const fetchPageRef = useRef(fetchPage)
  useEffect(() => { fetchPageRef.current = fetchPage })

  const pagesRef = useRef<Page[]>([])
  const loadingRef = useRef(false)
  const hasMoreForwardRef = useRef(true)
  const nextForwardCursorRef = useRef<string | undefined>(initialCursor)
  const visitedCursorsRef = useRef<(string | undefined)[]>([])
  const windowStartRef = useRef(0)
  const coldBackwardExhaustedRef = useRef(false)
  const initialCursorRef = useRef(initialCursor)

  const loadForward = useCallback(async () => {
    if (loadingRef.current || !hasMoreForwardRef.current) return
    loadingRef.current = true
    setLoading(true)
    try {
      const cursor = nextForwardCursorRef.current
      const result = await fetchPageRef.current(cursor, "forward")
      const newPage: Page = { cursor, items: result.items }
      visitedCursorsRef.current.push(cursor)
      nextForwardCursorRef.current = result.nextCursor
      hasMoreForwardRef.current = result.hasNext ?? false
      setHasMoreForward(hasMoreForwardRef.current)

      let nextPages = [...pagesRef.current, newPage]
      if (nextPages.length > maxPages) {
        const evicted = nextPages[0]
        nextPages = nextPages.slice(1)
        windowStartRef.current += 1
        setFirstItemIndex(idx => idx + evicted.items.length)
      }
      pagesRef.current = nextPages
      setPages(nextPages)
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }, [maxPages])

  const loadBackward = useCallback(async () => {
    if (loadingRef.current) return
    const canReplay = windowStartRef.current > 0
    const canColdBackward = supportsColdBackward && !coldBackwardExhaustedRef.current
    if (!canReplay && !canColdBackward) return

    loadingRef.current = true
    setLoading(true)
    try {
      let cursor: string | undefined
      let items: Meme[]

      if (canReplay) {
        cursor = visitedCursorsRef.current[windowStartRef.current - 1]
        const result = await fetchPageRef.current(cursor, "forward")
        items = result.items
        windowStartRef.current -= 1
      } else {
        const anchor = pagesRef.current[0]?.cursor ?? initialCursorRef.current
        const result = await fetchPageRef.current(anchor, "backward")
        if (result.items.length === 0) {
          coldBackwardExhaustedRef.current = true
          setHasMoreBackward(false)
          return
        }
        cursor = result.previousCursor
        items = result.items
        visitedCursorsRef.current.unshift(cursor)
      }

      const newPage: Page = { cursor, items }
      let nextPages = [newPage, ...pagesRef.current]
      setFirstItemIndex(idx => idx - newPage.items.length)

      if (nextPages.length > maxPages) {
        nextPages = nextPages.slice(0, -1)
      }

      pagesRef.current = nextPages
      setPages(nextPages)
      setHasMoreBackward(windowStartRef.current > 0 || (supportsColdBackward && !coldBackwardExhaustedRef.current))
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }, [maxPages, supportsColdBackward])

  const loadForwardRef = useRef(loadForward)
  useEffect(() => { loadForwardRef.current = loadForward })

  useEffect(() => {
    // Reset all bookkeeping and re-fetch from scratch.
    pagesRef.current = []
    setPages([])
    setFirstItemIndex(FIRST_ITEM_INDEX_START)
    loadingRef.current = false
    setLoading(false)
    hasMoreForwardRef.current = true
    setHasMoreForward(true)
    nextForwardCursorRef.current = initialCursor
    initialCursorRef.current = initialCursor
    visitedCursorsRef.current = []
    windowStartRef.current = 0
    coldBackwardExhaustedRef.current = false
    setHasMoreBackward(supportsColdBackward)

    loadForwardRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  const items = pages.flatMap(p => p.items)

  return { pages, items, firstItemIndex, loading, hasMoreForward, hasMoreBackward, loadForward, loadBackward }
}
```

- [ ] **Step 4: Run to verify it passes**

```
vitest run src/hooks/useWindowedPagination.test.ts
```
Expected: all PASS.

- [ ] **Step 5: Typecheck and lint**

```
tsc -b
eslint src/hooks/
```
Expected: PASS, 0 warnings. (If eslint flags the `react-hooks/exhaustive-deps` disable comment as unnecessary or flags something else in the reset effect, resolve by keeping `resetKey` as the effect's only intended dependency — this effect is deliberately not re-running on `initialCursor`/`supportsColdBackward` changes mid-session, only on an explicit reset.)

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/hooks/useWindowedPagination.ts Frontend/memes-frontend/src/hooks/useWindowedPagination.test.ts
git commit -m "feat: add useWindowedPagination hook for bidirectional page windowing"
```

---

### Task 6: Rewrite `MemesList` to use `Virtuoso` (row-chunked grid) + the hook

Drops all duplicates-specific props (`listDuplicates`, `groupByCluster`, `initialCursor`, `onCursorChange` move to the new `MemesDuplicatesList` in Task 7). The five callers that don't use those props (`SearchPage`, `ExploreUntaggedPage`, `ExploreFlaggedPage`, `ExploreNoOcrPage`, `RecommendationsPage`) need no changes.

**Revised from the original design**: uses plain `Virtuoso` with items chunked into rows of up to 6
(not `VirtuosoGrid`) — see the design spec's "Row chunking" section for why (`VirtuosoGridProps` in
the installed `react-virtuoso@4.18.11` has no `firstItemIndex`, confirmed against its `.d.ts`, so it
can't support jump-free backward loading, which this task needs to wire up too).

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesList.tsx` (full rewrite)
- Modify: `Frontend/memes-frontend/src/components/MemesList.test.tsx`

**Interfaces:**
- Consumes: `useWindowedPagination` (Task 5), `MemesApi` (unchanged for the 4 non-duplicates `iterate*` methods + `searchMemes`/`getRecommendations`).
- Produces: `MemesListProps` (narrowed — see Step 3) unchanged in name for the 5 pages that already import it.

- [ ] **Step 1: Update the test file's virtuoso mock and expectations**

Replace `Frontend/memes-frontend/src/components/MemesList.test.tsx` in full:

```typescript
import { render, screen, waitFor } from '@testing-library/react'
import { MemesList } from './MemesList'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data: unknown[]; itemContent: (index: number, item: unknown) => React.ReactNode; endReached?: (index: number) => void; startReached?: (index: number) => void }) => (
    <div>
      {props.data.map((item, i) => (
        <div key={i}>{props.itemContent(i, item)}</div>
      ))}
    </div>
  ),
}))

describe('MemesList', () => {
  it('calls searchMemes on mount', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith({
        cursor: undefined,
        limit: 36,
        query: undefined,
        tags: [],
      })
    })
  })

  it('shows "Nothing to show" when response is empty', async () => {
    render(<MemesList memesApi={makeMockApi()} />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    })
  })

  it('renders a card for each returned meme', async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({
        items: [DEFAULT_MOCK_MEME],
        facets: [],
        hasNext: false,
      }),
    })
    render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getByRole('img', { name: DEFAULT_MOCK_MEME.id })).toBeInTheDocument()
    })
  })

  it('calls iterateUntaggedMemes instead of searchMemes when listUntagged is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listUntagged />)
    await waitFor(() => {
      expect(api.iterateUntaggedMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('calls iterateNoOcrMemes instead of searchMemes when listNoOcr is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listNoOcr />)
    await waitFor(() => {
      expect(api.iterateNoOcrMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('calls iterateFlaggedMemes instead of searchMemes when listFlagged is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listFlagged />)
    await waitFor(() => {
      expect(api.iterateFlaggedMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('invokes onFacetsChanged with facets from the response', async () => {
    const onFacetsChanged = vi.fn()
    const facets = [{ name: 'category', buckets: [{ value: 'cats', count: 5 }] }]
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [], facets, hasNext: false }),
    })
    render(<MemesList memesApi={api} onFacetsChanged={onFacetsChanged} />)
    await waitFor(() => {
      expect(onFacetsChanged).toHaveBeenCalledWith(facets)
    })
  })

  it('applies tagFilters as tags in the search request', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} tagFilters={{ category: ['cats', 'dogs'] }} />)
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith(
        expect.objectContaining({
          tags: expect.arrayContaining([
            { category: 'category', name: 'cats' },
            { category: 'category', name: 'dogs' },
          ]),
        })
      )
    })
  })

  it('re-fetches from scratch when the filter changes', async () => {
    const api = makeMockApi()
    const { rerender } = render(<MemesList memesApi={api} filter="first query" />)
    await waitFor(() => expect(api.searchMemes).toHaveBeenCalledWith(expect.objectContaining({ query: 'first query' })))

    rerender(<MemesList memesApi={api} filter="second query" />)
    await waitFor(() => expect(api.searchMemes).toHaveBeenCalledWith(expect.objectContaining({ query: 'second query' })))
  })

  it('chunks items into rows of up to 6 for the grid layout', async () => {
    const items = Array.from({ length: 8 }, (_, i) => ({ ...DEFAULT_MOCK_MEME, id: `m${i}` }))
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items, facets: [], hasNext: false }),
    })
    const { container } = render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getAllByRole('img')).toHaveLength(8)
    })
    // 8 items at 6 columns -> one full row of 6, one partial row of 2.
    const rowWrappers = container.querySelectorAll('.grid.grid-cols-1.md\\:grid-cols-6')
    expect(rowWrappers).toHaveLength(2)
  })
})
```

(Dropped the `listDuplicates`/`groupByCluster` test — that behavior moves to `MemesDuplicatesList.test.tsx` in Task 7. Added an explicit `listFlagged` case, a filter-change re-fetch case, and a row-chunking case, all real `MemesList` behaviors that weren't directly tested before.)

- [ ] **Step 2: Run to verify it fails**

```
vitest run src/components/MemesList.test.tsx
```
Expected: FAIL (component still has the old `IntersectionObserver`-based implementation and `listDuplicates`/`groupByCluster` props the test no longer references — some tests may pass by coincidence, but the `react-virtuoso` mock target doesn't exist in the real import graph yet, and `listFlagged`/filter-change/chunking assertions are new).

- [ ] **Step 3: Implement the rewrite**

Replace `Frontend/memes-frontend/src/components/MemesList.tsx` in full:

```typescript
import { useCallback, useMemo, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { useWindowedPagination, type FetchPageFn } from "../hooks/useWindowedPagination"
import type { MemesApi } from "../api/MemesApi"
import type { Facet, Meme } from "../types/generated/all"

type MemesListProps = {
  memesApi: MemesApi
  filter?: string
  onFacetsChanged?: (facets: Facet[]) => void
  tagFilters?: Record<string, string[]>
  listUntagged?: boolean
  listFlagged?: boolean
  listNoOcr?: boolean
  listRecommendations?: boolean
}

const COLUMNS = 6

// Chunks `items` into rows of up to COLUMNS, aligning the first row to the item's absolute
// position in the true (unbounded) sequence -- so row boundaries stay stable and page-boundary-
// agnostic as pages are evicted from the front, matching today's seamless grid flow instead of
// visibly breaking at every page edge.
function chunkIntoAlignedRows(items: Meme[], globalStart: number): Meme[][] {
  const rows: Meme[][] = []
  if (items.length === 0) return rows
  const offset = ((globalStart % COLUMNS) + COLUMNS) % COLUMNS
  const firstRowSize = Math.min(COLUMNS - offset, items.length)
  rows.push(items.slice(0, firstRowSize))
  for (let i = firstRowSize; i < items.length; i += COLUMNS) {
    rows.push(items.slice(i, i + COLUMNS))
  }
  return rows
}

export function MemesList({ memesApi, filter, onFacetsChanged, tagFilters, listUntagged, listFlagged, listNoOcr, listRecommendations }: MemesListProps) {
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)

  const fetchPage: FetchPageFn = useCallback(async (cursor) => {
    if (filter && filter.length > 0 && filter.length < 2) {
      return { items: [], hasNext: false }
    }

    const tags = tagFilters
      ? Object.entries(tagFilters).flatMap(([name, values]) =>
          values.map(value => ({ category: name, name: value }))
        )
      : []

    let response
    if (listUntagged) {
      response = await memesApi.iterateUntaggedMemes(21, cursor)
    } else if (listFlagged) {
      response = await memesApi.iterateFlaggedMemes(40, cursor)
    } else if (listNoOcr) {
      response = await memesApi.iterateNoOcrMemes(21, cursor)
    } else if (listRecommendations) {
      response = await memesApi.getRecommendations(filter, 36, cursor)
    } else {
      response = await memesApi.searchMemes({ cursor, limit: 36, query: filter, tags })
    }

    if (onFacetsChanged) onFacetsChanged(response.facets ?? [])

    return {
      items: (response.items ?? []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] })),
      nextCursor: response.nextCursor,
      hasNext: response.hasNext,
    }
  }, [filter, tagFilters, memesApi, onFacetsChanged, listUntagged, listFlagged, listNoOcr, listRecommendations])

  const resetKey = `${filter ?? ""}:${JSON.stringify(tagFilters ?? {})}:${listUntagged}:${listFlagged}:${listNoOcr}:${listRecommendations}`

  const { items, firstItemIndex, hasMoreForward, hasMoreBackward, loading, loadForward, loadBackward } = useWindowedPagination({
    fetchPage,
    resetKey,
  })

  const rows = useMemo(() => chunkIntoAlignedRows(items, firstItemIndex), [items, firstItemIndex])
  const rowFirstItemIndex = Math.floor(firstItemIndex / COLUMNS)

  return (
    <div>
      <Virtuoso
        useWindowScroll
        firstItemIndex={rowFirstItemIndex}
        data={rows}
        startReached={() => { if (hasMoreBackward) loadBackward() }}
        endReached={() => { if (hasMoreForward) loadForward() }}
        itemContent={(_index, row) => (
          <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
            {row.map(meme => (
              <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
            ))}
          </div>
        )}
      />

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {loading && items.length > 0 && (
        <div className="h-10 flex items-center justify-center"><span>Loading...</span></div>
      )}

      {items.length === 0 && !loading && (
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

```
vitest run src/components/MemesList.test.tsx
```
Expected: all PASS.

- [ ] **Step 5: Update the five callers**

None of `SearchPage.tsx`, `ExploreUntaggedPage.tsx`, `ExploreFlaggedPage.tsx`, `ExploreNoOcrPage.tsx`, `RecommendationsPage.tsx` pass `listDuplicates`, `groupByCluster`, `initialCursor`, or `onCursorChange` today (confirmed by grep — only `ExploreDuplicatesPage` did, and that page moves to `MemesDuplicatesList` in Task 7). No changes needed to these five files. Confirm with:
```
tsc -b
```
Expected: PASS (would fail here if any of the five passed a now-removed prop).

- [ ] **Step 6: Full frontend check**

```
tsc -b
eslint src/
vitest run
```
Expected: all PASS, 0 eslint warnings. (`ExploreDuplicatesPage.tsx` will still fail `tsc -b` at this point since it still imports the old `MemesList` API with `listDuplicates`/`groupByCluster` — that's expected and fixed in Task 8. If Step 6 fails only on `ExploreDuplicatesPage.tsx`, that's the known, expected pre-Task-8 state; don't chase it here.)

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemesList.tsx Frontend/memes-frontend/src/components/MemesList.test.tsx
git commit -m "feat: rewrite MemesList on Virtuoso (row-chunked grid) + useWindowedPagination"
```

---

### Task 7: New `MemesDuplicatesList` component

`Virtuoso` (single column), one whole cluster per row, cold-backward-capable, drives `ExploreDuplicatesPage`'s persisted cursor.

**Files:**
- Create: `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`
- Create: `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx`

**Interfaces:**
- Consumes: `useWindowedPagination`, `cursorForVirtualIndex` (Task 5); `MemesApi.iterateDuplicates(limit, cursor, threshold, direction)` (Task 4).
- Produces: `MemesDuplicatesListProps = { memesApi: MemesApi; initialCursor?: string; onCursorChange?: (cursor: string | undefined) => void }` — consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

Create `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx`:

```typescript
import { render, screen, waitFor } from '@testing-library/react'
import { MemesDuplicatesList } from './MemesDuplicatesList'
import { makeMockApi } from '../test/mockApi'
import type { Meme } from '../types/generated/all'

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data: unknown[]; itemContent: (index: number, item: unknown) => React.ReactNode }) => (
    <div>
      {props.data.map((item, i) => (
        <div key={i}>{props.itemContent(i, item)}</div>
      ))}
    </div>
  ),
}))

function clusterMeme(id: string, clusterId: number): Meme {
  return { id, imageUrl: `/images/${id}.jpg`, text: [], tags: [], clusterId }
}

describe('MemesDuplicatesList', () => {
  it('calls iterateDuplicates on mount with no cursor by default', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, undefined, 0.2)
    })
  })

  it('starts from the URL-provided initialCursor', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} initialCursor="deep-link" />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, "deep-link", 0.2)
    })
  })

  it('groups same-cluster members into one row and renders them together', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 2)],
        hasNext: false,
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'a' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'b' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'c' })).toBeInTheDocument()
    })
  })

  it('shows "Nothing to show" when there are no clusters', async () => {
    render(<MemesDuplicatesList memesApi={makeMockApi()} />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```
vitest run src/components/MemesDuplicatesList.test.tsx
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the component**

Create `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`:

Note on `firstItemIndex`: the hook's `firstItemIndex` is computed at **item** granularity, but `Virtuoso` here is fed **cluster rows** (fewer entries, different cardinality) — passing the item-level index straight through would misalign virtuoso's prepend-without-jump bookkeeping. This component tracks its own `rowFirstItemIndex` by diffing `pages` (identified by each page's `cursor`, which the hook guarantees changes on every genuine prepend/evict) between renders and adjusting by the number of distinct clusters the added/evicted page contributed. This slightly over/under-counts by one row in the rare case a cluster's members straddle a page boundary (a cluster present in two adjacent pages gets counted once per page) — an accepted approximation given clusters are typically small relative to the 40-item page size; the visible effect of getting it wrong is a one-row scroll adjustment, self-corrected on the next load, not a data-correctness issue.

```typescript
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { useWindowedPagination, cursorForVirtualIndex, type FetchPageFn, type Page } from "../hooks/useWindowedPagination"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
  initialCursor?: string
  onCursorChange?: (cursor: string | undefined) => void
}

type ClusterRow = { clusterId: number | string; members: Meme[] }

const CURSOR_DEBOUNCE_MS = 300
const ROW_FIRST_ITEM_INDEX_START = 10_000

function distinctClusterCount(page: Page): number {
  return new Set(page.items.map(m => m.clusterId ?? "unknown")).size
}

export function MemesDuplicatesList({ memesApi, initialCursor, onCursorChange }: Props) {
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)
  const cursorChangeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onCursorChangeRef = useRef(onCursorChange)
  onCursorChangeRef.current = onCursorChange

  const fetchPage: FetchPageFn = useCallback(async (cursor, direction) => {
    const response = await memesApi.iterateDuplicates(40, cursor, 0.2, direction)
    return {
      items: (response.items ?? []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] })),
      nextCursor: response.nextCursor,
      hasNext: response.hasNext,
      previousCursor: response.previousCursor,
    }
  }, [memesApi])

  const { pages, items, firstItemIndex, hasMoreForward, hasMoreBackward, loadForward, loadBackward } = useWindowedPagination({
    fetchPage,
    initialCursor,
    resetKey: "duplicates",
    supportsColdBackward: true,
  })

  // Group the currently-windowed items into whole clusters, in the order
  // they first appear. A cluster still being filled in by an in-flight
  // adjacent page briefly renders with fewer members than it truly has --
  // accepted, self-heals as soon as that page loads (same characteristic
  // the pre-existing unwindowed implementation already had).
  const clusterRows: ClusterRow[] = useMemo(() => {
    const map = new Map<number | string, Meme[]>()
    const order: (number | string)[] = []
    for (const meme of items) {
      const key = meme.clusterId ?? "unknown"
      if (!map.has(key)) { map.set(key, []); order.push(key) }
      map.get(key)!.push(meme)
    }
    return order.map(clusterId => ({ clusterId, members: map.get(clusterId)! }))
  }, [items])

  const prevPagesRef = useRef<Page[]>([])
  const [rowFirstItemIndex, setRowFirstItemIndex] = useState(ROW_FIRST_ITEM_INDEX_START)

  useEffect(() => {
    const prevPages = prevPagesRef.current
    prevPagesRef.current = pages

    if (prevPages.length === 0 || pages.length === 0) return
    if (pages[0].cursor === prevPages[0].cursor) return

    const prevFirstStillPresentAt = pages.findIndex(p => p.cursor === prevPages[0].cursor)
    if (prevFirstStillPresentAt > 0) {
      const prependedRowCount = pages.slice(0, prevFirstStillPresentAt).reduce((sum, p) => sum + distinctClusterCount(p), 0)
      setRowFirstItemIndex(idx => idx - prependedRowCount)
    } else {
      setRowFirstItemIndex(idx => idx + distinctClusterCount(prevPages[0]))
    }
  }, [pages])

  const handleRangeChanged = useCallback((range: { startIndex: number }) => {
    if (!onCursorChangeRef.current) return
    if (cursorChangeTimerRef.current) clearTimeout(cursorChangeTimerRef.current)
    cursorChangeTimerRef.current = setTimeout(() => {
      const cursor = cursorForVirtualIndex(pages, firstItemIndex, range.startIndex)
      onCursorChangeRef.current?.(cursor)
    }, CURSOR_DEBOUNCE_MS)
  }, [pages, firstItemIndex])

  return (
    <div>
      <Virtuoso
        useWindowScroll
        firstItemIndex={rowFirstItemIndex}
        data={clusterRows}
        startReached={() => loadBackward()}
        endReached={() => { if (hasMoreForward) loadForward() }}
        rangeChanged={handleRangeChanged}
        itemContent={(_index, row) => (
          <div>
            <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
              {row.members.map(meme => (
                <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
              ))}
            </div>
            <hr className="my-4 border-gray-300" />
          </div>
        )}
      />

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {clusterRows.length === 0 && (
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

```
vitest run src/components/MemesDuplicatesList.test.tsx
```
Expected: all PASS.

- [ ] **Step 5: Lint and typecheck**

```
tsc -b
eslint src/components/MemesDuplicatesList.tsx src/components/MemesDuplicatesList.test.tsx
```
Expected: PASS, 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx
git commit -m "feat: add MemesDuplicatesList with cluster-row virtualization and cold backward loading"
```

---

### Task 8: Wire `ExploreDuplicatesPage` to `MemesDuplicatesList`

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/ExploreDuplicatesPage.tsx`

- [ ] **Step 1: Replace the implementation**

Replace `Frontend/memes-frontend/src/pages/ExploreDuplicatesPage.tsx` in full:

```typescript
import { useSearchParams } from "react-router-dom"
import { MemesDuplicatesList } from "../components/MemesDuplicatesList"
import type { MemesApi } from "../api/MemesApi"

type ExploreDuplicatesPageProps = {
  memesApi: MemesApi
}

export default function ExploreDuplicatesPage({ memesApi }: ExploreDuplicatesPageProps) {
  const [params, setParams] = useSearchParams()
  const initialCursor = params.get("cursor") ?? undefined

  function handleCursorChange(cursor: string | undefined) {
    setParams(cursor ? { cursor } : {}, { replace: true })
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesDuplicatesList
        memesApi={memesApi}
        initialCursor={initialCursor}
        onCursorChange={handleCursorChange}
      />
    </div>
  )
}
```

- [ ] **Step 2: Verify**

From `Frontend/memes-frontend/`:
```
tsc -b
eslint src/
vitest run
```
Expected: all PASS, 0 warnings — this closes out the "expected failure" noted in Task 6 Step 6.

- [ ] **Step 3: Commit**

```bash
git add Frontend/memes-frontend/src/pages/ExploreDuplicatesPage.tsx
git commit -m "feat: wire ExploreDuplicatesPage to MemesDuplicatesList"
```

---

### Task 9: Final integration verification

**Files:** none (verification only).

- [ ] **Step 1: Full frontend suite**

From `Frontend/memes-frontend/`:
```
tsc -b
eslint src/ --max-warnings 0
vitest run
```
Expected: all PASS.

- [ ] **Step 2: Full backend suites (run separately per the shared-code testing rule)**

```
cd Backend && pytest
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all PASS.

- [ ] **Step 3: Manual check per the `run` skill**

1. Start one environment's backend + frontend dev servers (per `CLAUDE.md`; do not bind the always-occupied metal/general/IT ports if those environments are already running on this machine — use whichever is free).
2. On the Search page: scroll down far enough to load several pages, open DevTools' Elements panel, confirm the mounted `MemeCard` count stays bounded (doesn't keep growing past ~`MAX_PAGES` worth of items) rather than accumulating forever.
3. Scroll back up; confirm earlier images reappear without a visible layout jump.
4. On the Duplicates page: scroll down a few pages, click a meme's permalink (`/memes/{id}` link in its detail modal) to navigate away, then use the browser Back button. Confirm the page resumes near the same cluster instead of snapping back to the top.
5. Still on Duplicates: manually add `?cursor=<some cursor captured from the address bar during step 4>` to a fresh page load and confirm scrolling up from there loads clusters that came before it (the cold-backward path), not just forward continuation.

- [ ] **Step 4: Update `backend_api.md`**

Per `CLAUDE.md`'s API contract rule, document the new `direction` query param and `previousCursor` response field on `GET /api/images/duplicates` in `backend_api.md`.

- [ ] **Step 5: Commit the docs update**

```bash
git add backend_api.md
git commit -m "docs: document duplicates endpoint direction/previousCursor"
```
