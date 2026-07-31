# Memes API Documentation

## Overview

The Memes API is a FastAPI-based backend for managing and searching meme images with support for tags, concepts, similarity search, and duplicate detection.

- **Version**: 0.1.0
- **Base URL**: `http://localhost:8081/api` (default)
- **Technology**: FastAPI with async/await support
- **Database**: PostgreSQL with SQLAlchemy (async)

## Server Configuration

The API supports multiple deployment configurations:
- Default: `http://127.0.0.1:8081`
- Metal environment: Port 8082 with `0.0.0.0` host
- General environment: Port 8081 with `0.0.0.0` host

## CORS Configuration

The API allows cross-origin requests from:
- `http://localhost`
- `http://127.0.0.1`
- Frontend origins configured via environment variables (`FRONTEND_ORIGIN`, `ALTERNATIVE_FRONTEND_ORIGIN`)

All HTTP methods and headers are allowed with credentials support enabled.

## Data Models

### Meme

Represents a meme image with metadata and tags.

```json
{
  "id": "string",
  "imageUrl": "string",
  "originalFileName": "string | null",
  "text": ["string"] | null,
  "tags": [MemeTag] | null,
  "flagged": "boolean | null",
  "clusterId": "integer | null",
  "cosineDistance": "number | null"
}
```

### MemeTag

Represents a tag associated with a meme.

```json
{
  "name": "string",
  "category": "string | null",
  "score": "number | null",
  "source": "string | null"
}
```

### MemeSearchResponse

Response format for meme search operations with pagination and facets.

```json
{
  "items": [Meme] | null,
  "facets": [Facet] | null,
  "nextCursor": "string | null",
  "hasNext": "boolean | null"
}
```

### Facet

Aggregated search facets for filtering.

```json
{
  "name": "string",
  "buckets": [FacetBucket]
}
```

### FacetBucket

Individual facet value with count.

```json
{
  "value": "string",
  "count": "number"
}
```

### Concept

Represents a concept/category that can be associated with memes.

```json
{
  "id": "number",
  "name": "string"
}
```

### TrendsRun

```json
{
  "runId": "string (UUID)",
  "createdAt": "string (ISO 8601)",
  "status": "started | completed | failed"
}
```

### TrendEntry

Aggregated count for a label/name pair within a run.

```json
{
  "label": "string",
  "name": "string",
  "value": "number"
}
```

### TrendHistoryEntry

A single data point in a time series.

```json
{
  "runId": "string (UUID)",
  "date": "string (YYYY-MM-DD)",
  "label": "string",
  "name": "string",
  "value": "number"
}
```

### SearchHistoryTag

A tag filter that was active during a search.

```json
{
  "category": "string",
  "value": "string"
}
```

### SearchHistoryItem

A single recorded search event.

```json
{
  "id": "string (UUID)",
  "searchedAt": "string (ISO 8601)",
  "query": "string | null",
  "client": "string",
  "resultCount": "number",
  "tags": [SearchHistoryTag]
}
```

- `query`: the text query, or `null` for tag-only searches
- `client`: `"web"` or `"android"`, inferred from `User-Agent`
- `resultCount`: number of items on the first page (≤ `limit`); `0` indicates no results

### SearchHistoryResponse

```json
{
  "items": [SearchHistoryItem],
  "nextCursor": "string | null",
  "hasNext": "boolean"
}
```

### UploadResponse

Response from `POST /api/uploads`.

```json
{
  "uploaded": [UploadedFile],
  "failed": [FailedFile],
  "total_accepted": "number",
  "total_failed": "number"
}
```

### UploadedFile

```json
{
  "original_filename": "string",
  "saved_as": "string",
  "size_bytes": "number",
  "content_type": "string",
  "status": "ok"
}
```

### FailedFile

```json
{
  "original_filename": "string",
  "reason": "string"
}
```

### BugReportResponse

Response from `POST /api/bug-reports`.

```json
{
  "original_filename": "string",
  "saved_as": "string",
  "size_bytes": "number"
}
```

### HealthResponse

```json
{
  "backend": "boolean",
  "database": "boolean"
}
```

### StatisticsResponse

```json
{
  "memes": {
    "total": "number",
    "pending": "number",
    "rejected": "number",
    "with_embeddings": "number",
    "with_ocr": "number",
    "with_tags": "number",
    "with_descriptions": "number",
    "with_concept_tags": "number",
    "flagged": "number",
    "duplicate_clusters": "number"
  },
  "content": {
    "ocr_texts": "number",
    "tags": "number",
    "tag_keys": "number",
    "tag_values": "number",
    "concepts": "number",
    "concept_image_sets": "number",
    "concept_images": "number"
  },
  "trends": {
    "runs": "number",
    "trend_sources": "number"
  }
}
```

## API Endpoints

### Uploads

#### Upload Images

Accept one or more image files and save them to the `incoming/` staging directory for downstream batch processing (OCR, embeddings, tagging, DB insert). Files are saved as `{uuid}.{ext}` to avoid collisions; the original filename is preserved in the response.

- **URL**: `POST /api/uploads`
- **Content-Type**: `multipart/form-data`
- **Form field**: `files` — one or more image files (required)

**Constraints**:

| Rule | Value |
|------|-------|
| Accepted MIME types | `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `image/bmp`, `image/tiff` |
| Accepted extensions | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tif`, `.tiff` |
| Max file size | 20 MB per file |
| Max files per request | 50 |

**Response** `200 OK` — `UploadResponse`:

```json
{
  "uploaded": [
    {
      "original_filename": "funny_cat.jpg",
      "saved_as": "a1b2c3d4-1234-5678-abcd-ef0123456789.jpg",
      "size_bytes": 204800,
      "content_type": "image/jpeg",
      "status": "ok"
    }
  ],
  "failed": [
    {
      "original_filename": "document.pdf",
      "reason": "Unsupported file type: application/pdf"
    }
  ],
  "total_accepted": 1,
  "total_failed": 1
}
```

Per-file type or size errors are reported in `failed[]` — the rest of the batch is still saved and the response is always `200`. If every file fails, `uploaded` is an empty array.

**Error Responses**:
- `422`: More than 50 files submitted, or malformed multipart body

**Storage**: `{BASE_PATH}/incoming/` (created automatically on first request). Not served by the API — the batch pipeline picks up files from this directory.

---

### Bug Reports

#### Upload Bug Report

Accept a single log file (e.g. from the Android client's "Save logs" flow) and save it to a dedicated `bug_reports/` directory, separate from `incoming/` and the images directory. Files are saved as `{timestamp}_{uuid}.{ext}` to keep them sortable and collision-free; the original filename is preserved in the response.

- **URL**: `POST /api/bug-reports`
- **Content-Type**: `multipart/form-data`
- **Form field**: `file` — a single log file (required)

**Constraints**:

| Rule | Value |
|------|-------|
| Accepted extensions | `.txt`, `.log` |
| Max file size | 10 MB |

**Response** `200 OK` — `BugReportResponse`:

```json
{
  "original_filename": "app-logs-20260705-101530.txt",
  "saved_as": "20260705T101532Z_a1b2c3d4-1234-5678-abcd-ef0123456789.txt",
  "size_bytes": 20480
}
```

**Error Responses**:
- `422`: Unsupported file extension, empty file, or file exceeds 10 MB
- `429`: Too many bug report requests from this client in the last minute

**Storage**: `{BASE_PATH}/bug_reports/` (or `BUG_REPORTS_PATH` override; created automatically on first request). Not served by the API.

---

### Images

#### Search Images

Search for memes with optional query, facets, and pagination.

- **URL**: `/api/images`
- **Method**: `GET`
- **Query Parameters**:
  - `q` (optional): Search query string — same tokenize/lemmatize/AND matching as `/api/recommendations`'s `q` (see below); both endpoints share one matching implementation.
  - `limit` (optional): Number of results (1-100, default: 20)
  - `facets` (optional): Facet filter string
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: no-cache
- **Example**: `GET /api/images?q=funny&limit=50`

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

#### Get Image Descriptions

Retrieve the AI-generated descriptions for a specific image (one entry per
configured prompt; see `image_descriptions.prompts_file`).

- **URL**: `/api/images/{image_id}/descriptions`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `ImageDescription[]` — `{ promptKey, text, modelUsed, createdAt, feedback }` per entry (`feedback` is `"approved"`, `"rejected"`, or absent/null). An image with no descriptions yet returns `200 []`, never `404`.
- **Example**: `GET /api/images/abc123/descriptions`

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

#### Get Meme Details

Retrieve detailed information about a specific meme.

- **URL**: `/api/images/meme/{image_id}`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `Meme`
- **Example**: `GET /api/images/meme/abc123`

#### Mark Meme as Flagged

Mark a meme as flagged.

- **URL**: `/api/images/meme/{image_id}/mark_flagged`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content)
- **Example**: `PUT /api/images/meme/abc123/mark_flagged`

#### Unmark Meme as Flagged

Remove flagged status from a meme.

- **URL**: `/api/images/meme/{image_id}/unmark_flagged`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content)
- **Example**: `PUT /api/images/meme/abc123/unmark_flagged`

#### Get Flagged Status

Check if a meme is flagged.

- **URL**: `/api/images/meme/{image_id}/get_flagged`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `integer` (1 if flagged, 0 if not)
- **Example**: `GET /api/images/meme/abc123/get_flagged`

#### Get Untagged Images

Retrieve images that don't have any tags.

- **URL**: `/api/images/untagged`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: no-cache
- **Example**: `GET /api/images/untagged?limit=30`

#### Get Flagged Images

Retrieve images that have been marked as flagged.

- **URL**: `/api/images/flagged`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: no-cache
- **Example**: `GET /api/images/flagged?limit=30`

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

#### Get Duplicate Images

Find duplicate or near-duplicate images using clustering.

- **URL**: `/api/images/duplicates`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `threshold` (optional): Similarity threshold (0.0-1.0, default: 0.05)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: no-cache
- **Example**: `GET /api/images/duplicates?threshold=0.1&limit=50`

#### Get Image File

Retrieve the actual image file.

- **URL**: `/api/images/{image_id}`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Image file (FileResponse)
- **Headers**:
  - `Content-Type`: Detected from file extension
  - `Content-Disposition`: Inline with original filename (UTF-8 encoded)
  - Cache headers for optimal browser caching
- **Example**: `GET /api/images/abc123`
- **Error Responses**:
  - `404`: Image not found

---

### Recommendations

#### Get Random Recommendations

Returns a stable-randomized feed of memes, optionally filtered by a text query. Results are stable across pages for the same seed — the order is determined by `md5(image_id || seed)` so each (image, seed) pair always has the same position in the feed. Flagged images are never returned.

```
GET /api/recommendations
```

**Query Parameters**:

| Parameter | Type   | Default              | Description |
|-----------|--------|----------------------|-------------|
| `q`       | string | —                    | Optional search query. Tokenized and lemmatized per word (Russian words normalized to dictionary form via pymorphy3; other languages lowercased; pure-digit tokens like years are kept as-is). All resulting lemmas must match (AND), each against either the image's precomputed OCR-lemma index (`ocr_lemmas`, built offline by `batch/build_ocr_lemmas.py`) or some tag value (case-insensitive equality). Matching is per-image, not per-OCR-line, so a multi-word query matches even when its words came from different OCR-detected lines on the same meme. Empty string is treated as no query. |
| `seed`    | int    | server-random        | Randomization seed. On the first call (no `cursor`) a seed controls the shuffle. If omitted, the server picks one. On subsequent pages the seed is read from the `cursor` and the URL param is ignored (a mismatch is logged as a warning). |
| `limit`   | int    | 20                   | Items per page (1–100). |
| `cursor`  | string | —                    | Opaque pagination token from the previous response. Encodes `{seed, last_hash}` where `last_hash = md5(last_item_id || seed)`. |

**Response** `200 OK` — `MemeSearchResponse`:

```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "imageUrl": "/api/images/550e8400-e29b-41d4-a716-446655440000",
      "originalFileName": "funny_cat.jpg",
      "text": ["When you debug for 6 hours"],
      "tags": [{ "name": "cats", "category": "subject", "score": 1, "source": "tagger" }],
      "flagged": false
    }
  ],
  "facets": [],
  "nextCursor": "eyJzZWVkIjogMTIzNDUsICJsYXN0X2hhc2giOiAiYWJjZGVmIn0=",
  "hasNext": true
}
```

- `facets` is always an empty array.
- `text` contains clean OCR strings (no confidence annotation).
- To fetch subsequent pages, pass `nextCursor` as `cursor` in the next request.
- Starting a new session (no `cursor`) with the same `seed` reproduces the same order.

**Pagination behaviour**:
The cursor is a keyset based on `md5(image_id || seed)`. This means pagination is independent of `limit` — you can change `limit` between pages without skipping or duplicating items. However, images added after the first request may appear or be skipped depending on their hash position relative to the current page boundary.

---

### Concepts

#### Get All Concepts

Retrieve all available concepts.

- **URL**: `/api/concepts`
- **Method**: `GET`
- **Response**: `Array<Concept>`
- **Example**: `GET /api/concepts`

#### Get Top Images for Concept

Get the top-ranked images associated with a specific concept.

- **URL**: `/api/concepts/top-images`
- **Method**: `GET`
- **Query Parameters**:
  - `concept_id`: ID of the concept
- **Response**: `MemeSearchResponse`
- **Example**: `GET /api/concepts/top-images?concept_id=5`

#### Get Concepts for Image

Retrieve all concepts associated with a specific image.

- **URL**: `/api/concepts/for-image`
- **Method**: `GET`
- **Query Parameters**:
  - `image_id`: ID of the image
- **Response**: `Array<Concept>`
- **Example**: `GET /api/concepts/for-image?image_id=abc123`

#### Get Concept by ID

Retrieve details of a specific concept.

- **URL**: `/api/concepts/{concept_id}`
- **Method**: `GET`
- **Path Parameters**:
  - `concept_id`: Unique identifier of the concept
- **Response**: `Concept`
- **Example**: `GET /api/concepts/5`

### Trends

#### Get Available Dates

List distinct dates for which trend runs exist.

- **URL**: `/api/trends/dates`
- **Method**: `GET`
- **Query Parameters**:
  - `label` (optional): Filter by label
  - `name` (optional): Filter by name
- **Response**: `Array<string>` (YYYY-MM-DD, descending)
- **Example**: `GET /api/trends/dates?label=reddit`

#### Get Runs for Date

List all runs that occurred on a given date.

- **URL**: `/api/trends/dates/{run_date}/runs`
- **Method**: `GET`
- **Path Parameters**:
  - `run_date`: Date in `YYYY-MM-DD` format
- **Response**: `Array<TrendsRun>`
- **Example**: `GET /api/trends/dates/2026-06-10/runs`

#### Get Latest Run for Date

Return only the most recent run for a given date.

- **URL**: `/api/trends/dates/{run_date}/runs/latest`
- **Method**: `GET`
- **Path Parameters**:
  - `run_date`: Date in `YYYY-MM-DD` format
- **Response**: `TrendsRun`
- **Example**: `GET /api/trends/dates/2026-06-10/runs/latest`

#### Get Entries for Run

Return aggregated label/name counts for a specific run.

- **URL**: `/api/trends/runs/{run_id}`
- **Method**: `GET`
- **Path Parameters**:
  - `run_id`: UUID of the run
- **Query Parameters**:
  - `label` (optional): Filter by label
  - `name` (optional): Filter by name
  - `min_value` (optional): Minimum count threshold (default: 10)
- **Response**: `Array<TrendEntry>`
- **Example**: `GET /api/trends/runs/abc-123?min_value=5`

#### Get Trend History

Time series of label/name counts across all runs.

- **URL**: `/api/trends/history`
- **Method**: `GET`
- **Query Parameters**:
  - `label` (optional): Filter by label
  - `name` (optional): Filter by name
  - `min_value` (optional): Minimum count threshold (default: 10)
- **Response**: `Array<TrendHistoryEntry>`
- **Example**: `GET /api/trends/history?label=reddit&min_value=20`

---

### Search History

Search history is recorded automatically as a side-effect of `GET /api/images` — there is no separate write endpoint. The client identity (`web` or `android`) is inferred from the `User-Agent` header (`okhttp/*` → `android`, anything else → `web`).

#### Get Search History

```
GET /api/history
```

Returns a cursor-paginated list of recorded search events, newest first.

**Query Parameters**:

| Parameter | Type   | Default | Description |
|-----------|--------|---------|-------------|
| `limit`   | int    | 50      | Items per page (1–200) |
| `cursor`  | string | —       | Pagination cursor from previous response |
| `client`  | string | —       | Filter by client: `web` or `android` |

**Response** `200 OK` — `SearchHistoryResponse`:

```json
{
  "items": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "searchedAt": "2026-06-15T10:30:00+00:00",
      "query": "draven",
      "client": "web",
      "resultCount": 12,
      "tags": [
        { "category": "genre", "value": "metal" }
      ]
    }
  ],
  "nextCursor": "eyJpZCI6ICI...",
  "hasNext": true
}
```

---

### Diagnostics

#### Health Check

Returns backend liveness and a live database connectivity probe.

- **URL**: `/api/diagnostics/health`
- **Method**: `GET`
- **Response**: `HealthResponse`
  - `backend` is always `true` (the server is running)
  - `database` is `true` only if a `SELECT 1` query succeeds
- **Example**: `GET /api/diagnostics/health`

```json
{ "backend": true, "database": true }
```

#### Statistics

Returns row counts across all major tables in a single SQL round-trip.

- **URL**: `/api/diagnostics/statistics`
- **Method**: `GET`
- **Response**: `StatisticsResponse`
- **Example**: `GET /api/diagnostics/statistics`

```json
{
  "memes": {
    "total": 12400,
    "pending": 6,
    "rejected": 2,
    "with_embeddings": 11800,
    "with_ocr": 9200,
    "with_tags": 10100,
    "without_tags": 2300,
    "with_descriptions": 3400,
    "with_concept_tags": 8700,
    "flagged": 82
  },
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
  "trends": {
    "runs": 14,
    "trend_sources": 6
  }
}
```

---

### Ingestion

Tier A/B near-duplicate review for new images brought in via `batch/ingest_hash_dedup.py` /
`batch/ingest_find_duplicates.py` — see
`docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md`. All endpoints below
implicitly operate on the current *active* ingestion run (`batch_runs`, `kind="ingestion"`,
`status="started"`) — there is at most one at a time.

#### Get Run Status

- **URL**: `/api/ingestion/run`
- **Method**: `GET`
- **Response**: `RunStatusResponse`
- **Errors**: `404` if no ingestion run is currently in progress
- **Example**: `GET /api/ingestion/run`

```json
{
  "run_id": "b3f1...",
  "status": "started",
  "stage": "tier_a_review",
  "stats": { "intake": 3, "hash_duplicates_in_batch": 0, "hash_duplicates_cross_corpus": 0, "registered": 3 },
  "created_at": "2026-07-25T18:58:12Z",
  "completed_at": null
}
```

#### List Pending Images

- **URL**: `/api/ingestion/pending`
- **Method**: `GET`
- **Response**: `PendingImage[]`
- **Example**: `GET /api/ingestion/pending`

```json
[
  { "image_id": "a1b2...", "filename": "new.jpg", "created_at": "2026-07-25T18:58:12Z" }
]
```

#### List Tier Clusters

Near-duplicate candidate clusters for the given tier — one merged query covering both
in-batch and cross-corpus matches (`match_source` per edge), per
`2026-07-25-duplicate-clustering-incremental-design.md`. Only clusters with at least one
still-undecided member are returned — resolved rows drop out automatically.

- **URL**: `/api/ingestion/clusters/{tier}`
- **Method**: `GET`
- **Path params**: `tier` — `tier_a` (pre-OCR, tight threshold) or `tier_b` (post-OCR-prepass, loose threshold)
- **Response**: `Cluster[]`
- **Example**: `GET /api/ingestion/clusters/tier_a`

```json
[
  {
    "members": [
      { "image_id": "a1b2...", "filename": "new.jpg", "status": "pending" },
      { "image_id": "c3d4...", "filename": "existing.jpg", "status": "active" }
    ],
    "edges": [
      { "image_id1": "a1b2...", "image_id2": "c3d4...", "distance": 0.021, "match_source": "cross_corpus" }
    ]
  }
]
```

#### Resolve Cluster

Applies per-image `reject`/`keep` decisions. `reject` flips the image's `status` to
`rejected` and moves its file to `BASE_PATH/rejected/`; `keep` marks every candidate pair
touching that image as reviewed for this tier, so it won't resurface in this tier's queue.
Partial resolution is allowed — omit members you're not ready to decide yet.

- **URL**: `/api/ingestion/clusters/{tier}/resolve`
- **Method**: `POST`
- **Body**: `ResolveRequest`
- **Response**: `ResolveResponse`
- **Example**:

```json
// Request
{ "decisions": [
  { "image_id": "a1b2...", "decision": "reject" },
  { "image_id": "c3d4...", "decision": "keep" }
] }

// Response
{ "rejected": ["a1b2..."], "kept": ["c3d4..."] }
```

#### Undo Reject

Reverts a `rejected` image back to `pending` and moves its file back out of
`BASE_PATH/rejected/`.

- **URL**: `/api/ingestion/images/{image_id}/undo-reject`
- **Method**: `POST`
- **Response**: `UndoRejectResponse`
- **Errors**: `404` if the image doesn't exist or isn't currently `rejected`
- **Example**: `POST /api/ingestion/images/a1b2.../undo-reject`

```json
{ "image_id": "a1b2...", "status": "pending" }
```

---

### Admin

Manual triggers for a small allow-listed set of maintenance batch jobs (currently
`trends_batch`, `move_flagged`, `unregister_deleted_images` — see
`environments/batch_registry.yaml` / `batch_registry.<environment>.yaml` for the live list and
the module each name maps to), plus status lookup for runs triggered this way.
`POST .../run` spawns the batch script as a detached subprocess and returns immediately with
`status: "running"` — it does not wait for the batch to finish. Progress and completion are
observed by polling `GET .../runs/{run_id}` or `GET .../runs`. Output is also written to
`logs/<environment>/<batch_name>_<timestamp>.log` on the machine running the backend.

#### Trigger Batch Run

- **URL**: `/api/admin/batches/{batch_name}/run`
- **Method**: `POST`
- **Path params**: `batch_name` — one of the names registered in `batch_registry.yaml` (e.g.
  `trends_batch`, `move_flagged`, `unregister_deleted_images`)
- **Response**: `RunTriggerResponse`
- **Errors**:
  - `404` if `batch_name` isn't a registered batch
  - `409` if a run of that batch is already in progress
- **Example**: `POST /api/admin/batches/move_flagged/run`

```json
{ "run_id": "b3f1c2a4-...", "status": "running" }
```

  Triggering `move_flagged` also chains an automatic `unregister_deleted_images` run afterward,
  which produces its own second, independent `batch_runs` row (visible via `GET .../runs`).
  There is no way to request the `--no-chain` opt-out through the API — that flag is
  direct-CLI-only.

#### Get Run Status

- **URL**: `/api/admin/batches/runs/{run_id}`
- **Method**: `GET`
- **Response**: `RunStatusResponse`
- **Errors**: `404` if `run_id` doesn't exist or isn't one of the admin-triggerable batch kinds
  (e.g. it belongs to the scheduler or the ingestion pipeline instead)
- **Example**: `GET /api/admin/batches/runs/b3f1c2a4-...`

```json
{
  "run_id": "b3f1c2a4-...",
  "batch_name": "move_flagged",
  "trigger": "manual",
  "status": "running",
  "created_at": "2026-07-28T10:15:00Z",
  "completed_at": null,
  "error": null
}
```

`status` is one of `running`, `completed`, `failed`. `error` is populated only when `status`
is `failed`.

#### List Batch Runs

- **URL**: `/api/admin/batches/runs`
- **Method**: `GET`
- **Query params**: `limit` (default `50`), `offset` (default `0`)
- **Response**: `RunListResponse`
- **Example**: `GET /api/admin/batches/runs?limit=10&offset=0`

```json
{
  "items": [
    {
      "run_id": "b3f1c2a4-...",
      "batch_name": "move_flagged",
      "trigger": "manual",
      "status": "completed",
      "created_at": "2026-07-28T10:15:00Z",
      "completed_at": "2026-07-28T10:15:42Z",
      "error": null
    }
  ],
  "total": 1
}
```

Only runs of the admin-triggerable kinds are included — scheduler and ingestion runs are
excluded even though they share the same `batch_runs` table.

---

## Running the API

### Development Mode

```bash
# Using Metal environment
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.metal --port 8081 --host 0.0.0.0

# Using General environment
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.general --port 8082 --host 0.0.0.0
```

### Environment Variables

Required environment variables:
- `FRONTEND_ORIGIN`: Primary frontend URL for CORS
- `ALTERNATIVE_FRONTEND_ORIGIN`: Alternative frontend URL for CORS
- `WATCHFILES_FORCE_POLLING`: Set to `1` for file watching in certain environments

## Authentication & Security

**Current Status**: This API has **no authentication mechanism**. All endpoints are publicly accessible.

**Recommendations for Production**:
- Implement JWT-based authentication
- Add rate limiting per client
- Use HTTPS only
- Validate and sanitize all inputs
- Implement CORS properly for your deployment

See roadmap in [Readme.md](./Readme.md) for planned user authentication features.

## Error Handling

The API uses standard HTTP status codes:
- `200 OK`: Successful request
- `404 Not Found`: Resource not found (image or concept not found)
- `422 Unprocessable Entity`: Validation error (automatically handled by FastAPI)
- `500 Internal Server Error`: Database or processing error

**Response Format**:
```json
{
  "detail": "Error message describing what went wrong"
}
```

**Common Error Scenarios**:
- Invalid `limit` parameter (must be 1-100)
- Invalid UUID format for image_id
- Non-existent image or concept
- Database connection issues

## Pagination

Endpoints that return multiple items use cursor-based pagination:
- Use the `cursor` query parameter to fetch the next page
- Check `hasNext` in the response to determine if more results are available
- The `nextCursor` field contains the cursor value for the next page

## Caching

The API implements two caching strategies:
- **Short cache** (30 seconds): Used for search and list endpoints
- **Image cache**: Used for static image file responses with browser-optimized headers
