# API Reference

All endpoints are prefixed with `/api`.

Paginated endpoints use **cursor-based pagination**. The response includes `nextCursor` (opaque string) and `hasNext` (bool). Pass `cursor=<nextCursor>` on the next request to fetch the next page.

### Response types

**`MemeSearchResponse`**
```json
{
  "items": [{ "id": "...", "imageUrl": "/api/images/<id>", "originalFileName": "...", "text": [...], "tags": [...], "excluded": false }],
  "facets": [{ "name": "...", "buckets": [{ "value": "...", "count": 0 }] }],
  "nextCursor": "...",
  "hasNext": true
}
```

---

## Images

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/images` | Search images |
| `GET` | `/api/images/excluded` | Excluded images |
| `GET` | `/api/images/untagged` | Images with no tags |
| `GET` | `/api/images/duplicates` | Duplicate image clusters |
| `GET` | `/api/images/meme/{image_id}` | Single image details |
| `PUT` | `/api/images/meme/{image_id}/mark_excluded` | Mark as excluded |
| `PUT` | `/api/images/meme/{image_id}/unmark_excluded` | Remove excluded flag |
| `GET` | `/api/images/meme/{image_id}/get_excluded` | Returns `1` if excluded, `0` otherwise |
| `GET` | `/api/images/{image_id}/similar` | Visually similar images |
| `GET` | `/api/images/{image_id}` | Download image file |

### `GET /api/images`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | — | Full-text search against OCR text |
| `limit` | int 1–100 | 20 | Page size |
| `facets` | string | — | Filter by tags, comma-separated `key:value` pairs (e.g. `emotion:happy,style:meme`) |
| `cursor` | string | — | Pagination cursor from previous response |

Returns `MemeSearchResponse` including tag facet counts over the full filtered set.

### `GET /api/images/excluded`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int 1–100 | 20 | Page size |
| `cursor` | string | — | Pagination cursor from previous response |

Returns `MemeSearchResponse` for images where the excluded flag is set.

### `GET /api/images/untagged`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int 1–100 | 20 | Page size |
| `cursor` | string | — | Pagination cursor from previous response |

Returns `MemeSearchResponse` for images that have no tags.

### `GET /api/images/duplicates`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int 1–100 | 20 | Page size |
| `threshold` | float 0.0–1.0 | 0.05 | Cosine distance threshold for duplicate detection |
| `cursor` | string | — | Pagination cursor from previous response |

Returns `MemeSearchResponse` grouped by duplicate cluster.

---

## Concepts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/concepts` | All concepts |
| `GET` | `/api/concepts/{concept_id}` | Single concept |
| `GET` | `/api/concepts/top-images?concept_id=` | Top images for a concept |
| `GET` | `/api/concepts/for-image?image_id=` | Concepts associated with an image |

---

# Run server
```commandline
uvicorn app.main:app --reload --env-file app/.env --port 8081 
```

```commandline
uvicorn app.main:app --reload --reload-delay 1 --env-file app/.env --port 8081 --reload-dir app
```

```commandline
uvicorn app.main:app --reload --env-file app/.env --port 8081 --use-colors --loop asyncio
```

```commandline
uvicorn app.main:app --reload --reload-dir app --env-file app/.env --port 8081
```

```commandline
uvicorn app.main:app --reload --reload-dir app --reload-exclude "*.pyc" --env-file app/.env --port 8081
```

```commandline
python -m uvicorn app.main:app --reload --reload-dir app --env-file app/.env --port 8081

python -m uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.metal --port 8081
python -m uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.general --port 8082
```