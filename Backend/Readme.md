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

## Trends

Trend = number of entity (person, band, etc.) occurrences in news feeds, aggregated per batch run.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/trends/dates` | Available dates with trend data |
| `GET` | `/api/trends/dates/{date}/runs` | All runs for a date |
| `GET` | `/api/trends/dates/{date}/runs/latest` | Latest run for a date |
| `GET` | `/api/trends/runs/{run_id}` | Trend entries for a specific run |
| `GET` | `/api/trends/history` | Per-run trend history across all dates |

### Response types

**`TrendsRun`**
```json
{ "runId": "uuid", "createdAt": "2026-06-07T10:00:00+00:00", "status": "completed" }
```

**`TrendEntry`**
```json
{ "label": "person", "name": "Ozzy Osbourne", "value": 42 }
```

**`TrendHistoryEntry`**
```json
{ "runId": "uuid", "date": "2026-06-07", "label": "person", "name": "Ozzy Osbourne", "value": 42 }
```

### `GET /api/trends/dates`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string | — | Filter to dates where this label has data |
| `name` | string | — | Filter to dates where this entity name has data |

Returns `list[string]` of ISO dates (`YYYY-MM-DD`), newest first.

### `GET /api/trends/dates/{date}/runs`

Returns `list[TrendsRun]` for the given date (`YYYY-MM-DD`), newest first.

### `GET /api/trends/dates/{date}/runs/latest`

Returns the single most recent `TrendsRun` for the given date, or **404** if no run exists.

### `GET /api/trends/runs/{run_id}`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string | — | Filter by label (e.g. `person`) |
| `name` | string | — | Filter by entity name |
| `min_value` | int | 10 | Minimum citation count (HAVING sum ≥ N) |

Returns `list[TrendEntry]` ordered by value descending.

### `GET /api/trends/history`

At least one of `label` or `name` is required (returns 422 otherwise).

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string | — | Filter by label |
| `name` | string | — | Filter by entity name |
| `min_value` | int | 10 | Minimum citation count per run |

Returns `list[TrendHistoryEntry]` — one row per (run, entity), ordered by date desc then value desc.

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