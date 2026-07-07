# No-OCR Images Page

## Summary

Add a paginated view listing images that have no OCR data at all — no
`ocr_texts` rows, meaning the image was never processed or processing found
nothing. This mirrors the existing "Untagged" page (`/api/images/untagged`),
which lists images with no `image_tags` rows, but checks `ocr_texts` instead.

Scope: backend endpoint + frontend web page only. Android is explicitly out
of scope for this iteration (Android currently has no Untagged or Duplicates
screen either — only Flagged — so adding No-OCR there first would be
inconsistent; revisit once Android gets those).

No new database tables, columns, or shared JSON schemas are needed. The
feature reuses the existing `Meme` / `MemeSearchResponse` schemas and the
same cursor-pagination convention as `untagged` and `flagged`.

## Definition of "no OCR data"

An image has no OCR data if it has **zero rows** in `ocr_texts` — regardless
of confidence. This matches how "untagged" is defined (existence check on a
related table, not a quality threshold), and keeps the query and the mental
model simple: it also naturally surfaces images where OCR crashed or was
never run, not just images where confidence was low.

## Backend

### Repository — `Backend/app/repositories/image_repository.py`

Add `get_no_ocr`, copied from `get_untagged` (`image_repository.py:160-191`)
with the `NOT EXISTS` subquery checking `OCRText` instead of `ImageTag`:

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

`OCRText` is already imported in this file.

### Service — `Backend/app/services/image_service.py`

Add `get_no_ocr`, copied from `get_untagged` (`image_service.py:129-156`):

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

Note: `_fill_texts_and_tags` is safe to call even though these images have no
OCR text by definition — it's a no-op for `text` and still fills `tags`
(images can be tagged from descriptions/concepts without OCR text).

### Router — `Backend/app/api/images.py`

Add the endpoint **before** the `/{image_id}` catch-all route, alongside
`untagged`/`duplicates`/`flagged`:

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

### API contract — `backend_api.md`

Add a new subsection under Images, next to "Get Untagged Images":

```
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

### Backend tests — `Backend/tests/test_images_endpoints.py`

Add a test class mirroring `TestGetUntaggedImages` (`test_images_endpoints.py:599`
onward): success, custom limit, cursor pagination, empty results, limit
validation (>100 and 0). Mock `mock_image_service.get_no_ocr` the same way
`get_untagged` is mocked.

## Frontend (web)

### `MemesApi` interface — `Frontend/memes-frontend/src/api/MemesApi.ts`

Add:

```typescript
iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>;
```

### `HttpMemesApi` — `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`

Add `iterateNoOcrMemes`, copied from `iterateUntaggedMemes` (lines 38-60),
hitting `/api/images/no-ocr`.

### `MemesList` — `Frontend/memes-frontend/src/components/MemesList.tsx`

Add a `listNoOcr?: boolean` prop alongside `listUntagged`/`listDuplicates`/
`listFlagged`/`listRecommendations`, and a branch in `getResponseFromBackend`:

```typescript
if (listNoOcr) {
  return await memesApi.iterateNoOcrMemes(21, next)
}
```

Add `listNoOcr` to the destructured props and to the `useCallback` dependency
array (matching the existing pattern for `listUntagged` etc).

### New page — `Frontend/memes-frontend/src/pages/ExploreNoOcrPage.tsx`

Copy of `ExploreUntaggedPage.tsx`, passing `listNoOcr={true}` to `MemesList`.

### Router — `Frontend/memes-frontend/src/app/router.tsx`

Add:

```typescript
import ExploreNoOcrPage from "../pages/ExploreNoOcrPage";
...
{ path: "/no-ocr", element: <ExploreNoOcrPage memesApi={memesApi} /> },
```

### Nav — `Frontend/memes-frontend/src/app/AppLayout.tsx`

Add a `NavLink` to `/no-ocr` labeled "No OCR", placed after the "Duplicates"
link and before "Flagged" (grouping it with the other data-quality views).

### Test doubles & tests

- `Frontend/memes-frontend/src/test/mockApi.ts`: implement `iterateNoOcrMemes`
  so the mock satisfies the `MemesApi` interface (`tsc -b` will fail
  otherwise).
- `Frontend/memes-frontend/src/components/MemesList.test.tsx`: add a case
  exercising `listNoOcr={true}` calling `iterateNoOcrMemes`, mirroring
  existing `listUntagged`/`listFlagged` test cases.

## Out of scope

- Android client page (see Summary — deferred until Untagged/Duplicates also
  land on Android).
- Any change to how/when OCR runs, or to `reset_ocr_status`/`extract_text_from_memes`.
- A confidence-threshold variant of "no OCR" (see Definition section).
- New DB tables, columns, or shared JSON schemas — none needed.

## Verification

- `cd Backend && pytest tests/test_images_endpoints.py` — new test class passes.
- Manually hit `GET /api/images/no-ocr` against a running backend and confirm
  response shape matches `MemeSearchResponse`.
- From `Frontend/memes-frontend/`: `tsc -b && eslint src/ && vitest run`.
- Manually load `/no-ocr` in the browser (metal env, port 5173) and confirm
  images render, paginate via infinite scroll, and the nav link highlights
  correctly.