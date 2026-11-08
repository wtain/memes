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
  "cosineDistance": "number | null",
  "descriptionNote": "string | null"
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
  "tags": [SearchHistoryTag],
  "kind": "string",
  "language": "string | null",
  "queryHash": "string | null",
  "situation": "string | null",
  "degraded": "boolean | null",
  "fallback": "string | null",
  "parseMs": "number | null",
  "retrievalMs": "number | null"
}
```

- `query`: the text query, or `null` for tag-only searches
- `client`: `"web"` or `"android"`, inferred from `User-Agent`
- `resultCount`: number of items on the first page (≤ `limit`); `0` indicates no results
- `kind`: `"search"` (a `GET /api/images` search) or `"meme_text"` (a meme-from-text search)
- The remaining fields are optional and only filled for `meme_text` rows: `language` (detected language), `queryHash` (hash of the normalised message, the same value as `meta.queryHash` and the ratings `queryHash`), `situation` (the parsed English situation phrase), `degraded` / `fallback` (whether the parse fell back and why), `parseMs` / `retrievalMs` (stage timings in milliseconds)

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
    "duplicate_clusters": "number",
    "ocr_missing_text_heavy_classification": "number",
    "text_heavy_missing_embeddings": "number",
    "embeddings_missing_lemmas": "number"
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
  },
  "computed_at": "string | null (ISO 8601, UTC)"
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
  - `record` (optional, default `true`): `false` suppresses the search-history row, the search impressions and the `searchId`; intended for admin tools (the comics editor) whose searches must not count as user searches.
- **Ordering**: with a non-empty `q`, results are ordered by relevance (per-token best hit, source weight x match-tier weight, summed across tokens) plus a small capped popularity bonus (`weight * min(log2(popularity), max_log2)`, from `search.ranking.popularity.weight` and `.max_log2`; weight 0 turns it off), then by recency. Without `q` they are ordered by recency (unchanged). `nextCursor` stays an opaque string; relevance pages embed the score in it. A cursor in the other format (e.g. a recency cursor sent with `q`) is ignored and the listing restarts at the first page. Weights live in `search.ranking.*` in `environments/settings.yaml`.
- **Pagination contract**: `nextCursor` addresses the last returned item, so the next page neither skips nor repeats a result. This holds for `GET /api/images` (search), `GET /api/images/untagged`, `GET /api/images/no-ocr` and `GET /api/images/flagged`.
- **Response**: `MemeSearchResponse`
- **`searchId`** (optional string): the id of the `search_history` row this search wrote, for the client to pass on with a save (`POST /api/images/meme/{image_id}/saves?searchId=`) or a miss (`POST /api/history/{search_id}/not-found`), epic meme-popularity S6. Absent for the unfiltered browse feed (no `q`, no facets), when the history write failed, and when `popularity.search_outcomes.enabled` is false.
- **Impressions**: every image in the returned page is recorded as a search impression in `meme_impressions` (epic meme-popularity, S1), "show more" pages included, but only when the request has a non-blank `q` or at least one facet: a listing with neither is the browse feed (Explore, the empty Search page) and is not recorded. Rank is the 1-based position within the page. Best effort: a failed write is logged and never changes the response; `popularity.impressions.enabled: false` switches it off. Needs the S1 migration (`meme_impressions`). Read the counts with `GET /api/images/meme/{image_id}/impressions`.
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
  - `source` (optional): `image` (default) ranks by CLIP visual-embedding similarity; `description` ranks by LLM-description text-embedding similarity (only images sharing at least one prompt's description embedding with the source image are candidates; descriptions the user rejected are ignored on both sides, and the source image 404s if all of its descriptions are rejected); `description_note` ranks by human-written description-note text-embedding similarity; `description_all` ranks by the minimum cosine distance across all of the image's non-rejected Ollama description embeddings and its note embedding, versus every other active image's (no prompt_key restriction; `cosineDistance` is that minimum; 404 when the image has no non-rejected description embedding and no note embedding, so an image whose only descriptions are rejected also 404s). Returns 404 if the source image has no embedding of the requested kind.
- **Response**: `MemeSearchResponse` — each `Meme` item includes `cosineDistance` (float, lower = more similar; not comparable between `source=image` and `source=description` responses — different embedding spaces)
  - `record` (optional, boolean, default `true`): `false` skips recording impressions, same meaning as on `GET /api/images`. The comics editor's "Neighbours" search sends `record=false`.
- **Impressions**: unless `record=false`, the returned images (never the source image) are recorded as `similar` impressions, with the same best-effort rule as in Search Images.
- **Example**: `GET /api/images/abc123/similar?limit=10`
- **Example**: `GET /api/images/abc123/similar?limit=60&record=false`
- **Example**: `GET /api/images/abc123/similar?source=description&limit=10`
- **Example**: `GET /api/images/abc123/similar?source=description_note&limit=10`
- **Example**: `GET /api/images/abc123/similar?source=description_all&limit=10`

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
back to no-feedback instead of re-approving. Any feedback change (approve, reject or clear)
also deletes that image's description-derived (`source="Ollama"`) tags in the same
transaction; the next `build_tags_from_descriptions` run rebuilds them from the
non-rejected descriptions, so until then the image has no such tags.

- **URL**: `/api/images/{image_id}/descriptions/{prompt_key}/approve`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
  - `prompt_key`: Identifies which description (see `image_descriptions.prompts_file`)
- **Response**: `DescriptionFeedbackResponse` — `{ feedback: "approved" | "rejected" | null }`, the resulting state. `404` if no description exists for that `(image_id, prompt_key)` pair.
- **Example**: `PUT /api/images/abc123/descriptions/general_description/approve`

#### Reject Image Description

Record a human "rejected" judgment on one AI-generated description. Toggles
the same way as Approve, in the opposite direction. A rejected description is not
hidden in the description list, but it is excluded from smart search, from
`/similar?source=description` and `source=description_all`, and from description
tagging. Tag side effect as for Approve.

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
- **Response**: `Meme` — includes `descriptionNote` (string, optional — the image's
  human-written note, absent if none set)
- **Example**: `GET /api/images/meme/abc123`

#### Set Description Note

Set (or overwrite) a human-written free-text note on an image. No authentication today --
see docs/security/admin-permissions-todo.md.

- **URL**: `/api/images/{image_id}/description-note`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Body**: `{"text": "..."}`
- **Response**: Success (no content). An empty/whitespace-only `text` clears the note
  (equivalent to `DELETE`) rather than storing an empty string. A non-empty `text` also
  refreshes that note's lemma index in the same request (smart search sees it immediately);
  `Note` tags (`build_tags_from_notes`) and the note embedding (`build_description_note_embeddings`)
  are still updated only by their batch jobs.
- **Cache**: no-cache
- **Example**: `PUT /api/images/abc123/description-note` with body `{"text": "a cat wearing a hat"}`

#### Delete Description Note

Clear an image's description note. Also removes its embedding row (`ON DELETE CASCADE`)
and lemma index rows (deleted explicitly by the repository -- that table's FK cascades off
the image, not the note), synchronously -- no batch run required for a cleared note to stop
appearing in search/similarity results. The image's `Note` tags are deleted too (as when `PUT` is given empty text). No authentication today -- see
docs/security/admin-permissions-todo.md.

- **URL**: `/api/images/{image_id}/description-note`
- **Method**: `DELETE`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content). Safe to call when no note is currently set.
- **Cache**: no-cache
- **Example**: `DELETE /api/images/abc123/description-note`

#### Mark Meme as Flagged

Mark a meme as flagged.

- **URL**: `/api/images/meme/{image_id}/mark_flagged`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Query Parameters**:
  - `reason` (optional): Free-text reason for flagging (max 50 chars), stored alongside the flag
  - `duplicateOf` (optional): id of the image that survives this one as its duplicate. Requires `reason` to be
    `duplicate_review` (popularity source `cluster_review`) or `agent_review` (source `agent_review`); the flagged
    image's popularity moves into the survivor. Without `duplicateOf` a flag never changes popularity.
- **Errors**: `422` when `duplicateOf` is given with another or no `reason`, when `duplicateOf` or `image_id` is
  not a well-formed UUID, or when they are equal. A well-formed UUID with no image row returns `200` and records
  nothing. Validation happens before anything is written. A popularity recording failure never fails the flag;
  re-flagging against another survivor keeps the first recorded edge. `422` `{detail: {code: "comics_source_reserved"}}`
  when `reason` is `comics_source` (reserved for the comics build; nothing is written). `409`
  `{detail: {code: "comics_source_flag"}}` when the image is already flagged with reason `comics_source`: flagging it
  again with a different reason is refused.
- **Response**: Success (no content)
- **Example**: `PUT /api/images/meme/abc123/mark_flagged`
- **Example (with reason)**: `PUT /api/images/meme/abc123/mark_flagged?reason=duplicate_review`
- **Example (with survivor)**: `PUT /api/images/meme/abc123/mark_flagged?reason=agent_review&duplicateOf=def456-...`

#### Get Meme Popularity

Popularity of one meme and where it came from. Read-only: writes nothing. Needs the P1 popularity migration
(`images.popularity`, `popularity_absorptions`).

- **URL**: `/api/images/meme/{image_id}/popularity`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image (UUID)
- **Response**: `MemePopularity`
  - `popularity` (integer >= 0): the cached `images.popularity`, returned as stored
  - `status`: `normal`, `flagged` (flagged and still holds popularity) or `discarded` (popularity 0, i.e. absorbed
    into another image; wins over a flag)
  - `breakdown`: list of `{source, count}` rows with the counts credited to this image. `own` comes first, then the
    sources by `count` descending, ties by source name, and `adjustment` (a manual set, see below) last. Empty when
    `discarded`. An `adjustment` count is signed (negative when popularity was lowered); every other count is
    positive. Rows come from the open absorption edges, so after cache drift they may not sum to `popularity`.
- **Errors**: `404` for an unknown or non-UUID `image_id` (a deliberate difference from `GET /meme/{id}`)
- **Cache**: no-cache
- **Example**: `GET /api/images/meme/550e8400-e29b-41d4-a716-446655440000/popularity`

#### Set Meme Popularity

Set the popularity of one meme to an exact number. Records an `adjustment` edge with the signed difference from
the raw tree value (1 plus the weight below the image, before the floor of 1), so the shown number lands exactly on N and the change is visible in the `breakdown` and the number stays derivable from the log. Idempotent
by value: setting the number the image already has writes nothing.

- **URL**: `/api/images/meme/{image_id}/popularity`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image (UUID)
- **Body**: `MemePopularityUpdate`
  - `popularity` (integer, 1 to 1000000): the new popularity
- **Response**: `MemePopularity`, the same shape as the read above, after the change
- **Errors**:
  - `404`: unknown or non-UUID `image_id`
  - `409`: the image is not `active`, or it is discarded (popularity 0, absorbed into another image; set the
    popularity of the surviving image instead)
  - `422`: `popularity` missing, not an integer, below 1 or above 1000000
- **Cache**: no-cache
- **Example**: `PUT /api/images/meme/550e8400-e29b-41d4-a716-446655440000/popularity` with `{"popularity": 40}`

#### Record Meme Save

Appends one row to the save log (epic meme-popularity, S2). Android calls it after a save to the gallery has
succeeded. Needs the S2 migration (`meme_saves`).

- **URL**: `/api/images/meme/{image_id}/saves`
- **Method**: `POST`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image (UUID); an image of any status is accepted
- **Query Parameters**:
  - `searchId` (optional): the `searchId` of the search that led to this save (epic meme-popularity, S6). It links the
    save to that `search_history` row (`meme_saves.search_history_id`) so the discovery report's `misses` view can tell
    a search that ended in a save from one that did not. A malformed value is ignored; when the link cannot be stored
    (switch `popularity.search_outcomes.enabled` off, or the S6 migration has not run) the save is still recorded without it
- **Body**: none
- **Response**: `204`, no body
- **Idempotency**: none. Every call counts, repeat saves of the same meme included, so a client must not retry
  (a retry double counts). Clients treat the report as best effort: any failure is ignored and never fails the save.
- **Errors**: `404` for an unknown or non-UUID `image_id`; nothing is inserted in that case
- **Cache**: no-cache
- **Example**: `POST /api/images/meme/550e8400-e29b-41d4-a716-446655440000/saves`

#### Get Meme Saves

How often a meme was saved. Read-only: writes nothing.

- **URL**: `/api/images/meme/{image_id}/saves`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image (UUID)
- **Response**: `MemeSaves`
  - `imageId`: the requested id, canonical form
  - `count` (integer >= 0): saves of the image itself plus saves of every copy folded into it, transitively,
    through open popularity absorption edges (undoing a fold hands the saves back). Saves of a deleted image still
    count. This is a separate number from popularity and is never added to it.
  - `lastSavedAt` (date-time or `null`): newest save among those counted; `null` when `count` is 0
- **Errors**: `404` for an unknown or non-UUID `image_id`
- **Cache**: no-cache
- **Example**: `GET /api/images/meme/550e8400-e29b-41d4-a716-446655440000/saves`

#### Get Meme Impressions

How often search showed a meme (epic meme-popularity, S1). Read-only: writes nothing. There is no write route: only the
server records impressions, as a side effect of `GET /api/images`, `GET /api/images/{image_id}/similar` and
`POST /api/meme-from-text/search`. Needs the S1 migration (`meme_impressions`); before it has run this route answers 500
while searches keep working.

- **URL**: `/api/images/meme/{image_id}/impressions`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image (UUID); an image of any status is accepted
- **Response**: `MemeImpressions`
  - `imageId`: the requested id, canonical form
  - `count` (integer >= 0): impressions of the image itself plus those of every copy folded into it, transitively,
    through open popularity absorption edges (undoing a fold hands them back). Impressions of a deleted image still
    count. A separate number from popularity and saves, never added to either.
  - `countWindow` (integer >= 0): the same count restricted to the last `windowDays` days
  - `windowDays` (integer >= 1): the window, from `popularity.impressions.window_days` (default 30)
  - `lastShownAt` (date-time or `null`): newest impression among those counted; `null` when `count` is 0
- **Errors**: `404` for an unknown or non-UUID `image_id`
- **Cache**: no-cache
- **Example**: `GET /api/images/meme/550e8400-e29b-41d4-a716-446655440000/impressions`

#### Unmark Meme as Flagged

Remove flagged status from a meme. Unflagging reverses the image's open popularity edge (reason `unflag`) when its
source is `cluster_review`, `agent_review` or `file_duplicates` (so also edges recorded by `detect_file_duplicates`,
not only flags made with `duplicateOf`), restoring the popularity of both images; with no such edge popularity is
untouched.

- **URL**: `/api/images/meme/{image_id}/unmark_flagged`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Errors**: `409` `{detail: {code: "comics_source_flag"}}` when the image's flag reason is `comics_source`: unflagging it is refused and nothing is changed.
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

#### Get Trending Memes

The "Trending memes" list. `window=all` (default): memes with the most copies, most popular first. `window=30d`: memes that picked up the most duplicates in the last 30 days. Read-only.

- **URL**: `/api/images/trending`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 36)
  - `cursor` (optional): Pagination cursor for next page
  - `window` (optional): `all` (default, the all-time list) or `30d` (last 30 days). Any other value is a 422.
- **Response**: `TrendingMemesResponse`: `items` (each `{meme: Meme, popularity: int, windowGain?: int}`), `nextCursor` (optional), `hasNext`. No facets. `windowGain` (integer >= 1) is present only with `window=30d`; all-time items omit the key.
- **Window `30d`**: `windowGain` of a meme is the sum of the weights of `absorb` edges in `popularity_absorptions` that are open (not reversed), were created in the last 30 days (database clock), have `weight > 0` and are not `adjustment` edges (a manual "set popularity to N" does not count), credited to the root of the duplicate tree today (a chain A absorbed B, then A absorbed into C credits C with both edges). Eligible roots: `status = 'active'`, not flagged, `windowGain >= 1`; `search.trending.min_popularity` is not applied. Order: `windowGain` descending, then `created_at` descending, then `id` descending. `popularity` is the meme's current total and can be below `windowGain` after a manual adjustment.
- **Eligibility** (`window=all`): `status = 'active'`, not flagged (an image without an `image_extras` row counts as not flagged) and `popularity >= search.trending.min_popularity` (setting in `environments/settings.yaml`, int >= 1, required, default 2). A discarded image (popularity 0) is therefore never listed.
- **Order** (`window=all`): `popularity` descending, then `created_at` descending, then `id` descending.
- **Cursor**: opaque; keyset over `(popularity, created_at, id)` (`window=30d`: `(windowGain, created_at, id)`) and it addresses the last returned item. A cursor from the other window is foreign too. An invalid, truncated or foreign cursor (for example one from the search endpoint) is not an error: the list restarts at page 1.
- **Cache**: no-cache
- **Example**: `GET /api/images/trending?limit=36`, `GET /api/images/trending?window=30d`

#### Get Duplicate Images

Find duplicate or near-duplicate images using clustering.

- **URL**: `/api/images/duplicates`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `threshold` (optional): Similarity threshold (0.0-1.0, default: 0.05)
  - `cursor` (optional): Pagination cursor for next page
  - `direction` (optional): `forward` or `backward` (default: `forward`). `backward` walks to clusters before `cursor` instead of after — used for cold backward loading (e.g. resuming a scroll position after browser Back navigation). With `direction=backward` and no `cursor`, the response is empty (there's no anchor to page backward from).
- **Response**: `MemeSearchResponse` — `previousCursor` (opaque string, nullable) is set when there are more clusters before the current page (`direction=backward` pages only; forward pages always return `previousCursor: null`). Pass it as `cursor` with `direction=backward` to continue paging backward.
- **Cache**: no-cache
- **Example**: `GET /api/images/duplicates?threshold=0.1&limit=50`
- **Example (backward)**: `GET /api/images/duplicates?cursor=<cursor>&direction=backward`

#### Dismiss Duplicate Cluster

Record that a set of images in a duplicate cluster are confirmed **not** duplicates of each
other. Looks up the cluster's current members server-side (never trusts client-supplied ids as
the full membership) and records all `C(N, 2)` pairs among the target set. Does not trigger a
`clusterize` rerun — the effect is visible the next time `clusterize` runs (manually, via
`/admin/batches`). See
`docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md` and
`docs/superpowers/specs/2026-09-26-duplicate-cluster-partial-resolution.md`.

- **URL**: `/api/images/duplicates/clusters/{cluster_id}/dismiss`
- **Method**: `POST`
- **Path Parameters**:
  - `cluster_id`: The cluster's `clusterId` as returned by `GET /api/images/duplicates`
- **Body** (optional): `{"member_ids": ["...", "..."]}` — a subset of the cluster's member ids to
  dismiss as not-duplicates of each other. Omitted or `null` dismisses the whole cluster (every
  member pair) — today's existing behavior.
- **Response**: `{"pairs": [{"image_id1": "...", "image_id2": "..."}, ...]}` — every pair recorded
- **Errors**: `404` if `cluster_id` doesn't currently exist in `tmp_clusters`; `400` if
  `member_ids` contains an id that isn't a member of the cluster, or fewer than 2 ids
- **Cache**: no-cache
- **Example**: `POST /api/images/duplicates/clusters/141/dismiss`
- **Example (subset)**: `POST /api/images/duplicates/clusters/141/dismiss` with body
  `{"member_ids": ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]}`

#### Get Cluster Similarity

Presentation-only pairwise details for a duplicate cluster's active members, to help a human reviewer
judge which members are most/least alike before dismissing a subset: the OCR-lemma overlap
coefficient and the stored candidate rows (distance and signal) of each pair. Never written
anywhere — computed fresh on each request. See
`docs/superpowers/specs/2026-09-26-duplicate-cluster-partial-resolution.md` and
`docs/superpowers/specs/2026-11-02-ocr-dedup-review-details-design.md`.

- **URL**: `/api/images/duplicates/clusters/{cluster_id}/similarity`
- **Method**: `GET`
- **Path Parameters**:
  - `cluster_id`: The cluster's `clusterId` as returned by `GET /api/images/duplicates`
- **Response**: `{"pairs": [{"image_id1": "...", "image_id2": "...", "overlap": 0.75,
  "candidates": [{"distance": 0.031, "source": "clip"}, ...]}, ...], "capped": false}`
  - `pairs` has one entry for every combination of two active members (`image_id1 < image_id2`).
  - `overlap` is a coefficient between 0.0 and 1.0, or `null` when either image has no OCR lemmas.
  - `candidates` are the stored `tmp_duplicates` rows of the pair, closest first; `source` is `clip`
    or `ocr_text` (safety-net rows are stored as `clip`). Empty when the two members are only
    linked through other members of the cluster.
  - `capped` is `true` and `pairs` is empty when the cluster has more than 100 members.
- **Cache**: no-cache
- **Example**: `GET /api/images/duplicates/clusters/141/similarity`

#### Undo Dismiss Duplicates

Delete previously-recorded not-duplicate decisions for the given pairs, so they can be
re-clustered again on the next `clusterize` run.

- **URL**: `/api/images/duplicates/pairs/undo-dismiss`
- **Method**: `POST`
- **Body**: `{"pairs": [{"image_id1": "...", "image_id2": "..."}, ...]}`
- **Response**: Success (no content)
- **Cache**: no-cache
- **Example**: `POST /api/images/duplicates/pairs/undo-dismiss` with body `{"pairs": [{"image_id1": "abc", "image_id2": "def"}]}`

#### List Duplicate Decisions

Recent not-duplicate decisions, newest first — for undoing a decision from a past session.

- **URL**: `/api/images/duplicates/decisions`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `offset` (optional): Number of results to skip (default: 0)
- **Response**: `{"items": [{"image_id1", "filename1", "image_id2", "filename2", "decided_at"}, ...], "total": N}`
- **Cache**: no-cache
- **Example**: `GET /api/images/duplicates/decisions?limit=10`

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

### Meme From Text

#### Search Memes From A Message

Finds memes that make a good reply to a pasted message (English, Russian or Spanish). The message is parsed into a query profile (situation phrase plus emotion, stance, topic and tone as taxonomy keys), then matched against the stored meme profiles. The search neither stores nor logs the message text (only an explicit rating stores it, see Rate A Result).

- **URL**: `/api/meme-from-text/search`
- **Method**: `POST`
- **Request Body** (`MemeFromTextRequest`):
  - `text` (string, required, 1 to 8000 characters): the message to reply to (the server uses the first 600 characters)
  - `languageHint` (string, optional): `en`, `ru`, `es` or `other`; skips language detection
  - `profile` (`QueryProfile`, optional): an edited query profile. When present the message is not parsed and the profile is validated against the taxonomy. It must be a full `QueryProfile`: send back the `profile` object returned by a previous response with your edits (required fields `taxonomyVersion`, `language`, `parserVersion`, `degraded` and the four axis lists `emotion`, `stance`, `topic`, `tone`)
  - `filters` (object, optional): `emotion`, `stance`, `topic`, `tone` (arrays of taxonomy keys) and `tags` (array of `{ "key", "value" }`). Values within one axis are OR-ed, different axes are AND-ed; a tag filter matches any of the listed pairs. Each array holds at most 64 entries and every key, value or tag string is at most 128 characters
  - `cursor` (string, optional): opaque cursor from a previous response; it is tied to the same request (text, profile and filters); at most 512 characters
  - `limit` (integer, optional, 1 to 50, default 20)
- **`searchId`** (optional string): the id of the message's `search_history` row, present only on the response to a fresh message that was logged (a refine, filter change or "show more" has none; a client keeps the fresh search's id for those) and only while `popularity.search_outcomes.enabled` is true.
- **Impressions**: the returned items are recorded as `reply` impressions (every page, "show more" included; rank is the position within the page), with the same best-effort rule as in Search Images. The impression carries the id of the message's `search_history` row only for a fresh message that was logged; otherwise it has none. It stores no message text.
- **Response**: `MemeFromTextResponse`
  - `items` (array of `MemeFromTextResult`):
    - `meme`: the existing `Meme` object
    - `score` (number): fused relevance score
    - `axisPath` (boolean): `false` for a lexical-only hit on an image that has no meme profile (appears only in degraded responses, unless `lexical.only_when_degraded` is switched off)
    - `explanation`: `axes[]` (`axis` one of situation, emotion, stance, topic, tone, lexical; `score`, `weight`, `contribution`, `neutral`, `queryValues`, `matched[]` with `queryKey`, `memeKey`, `similarity`, `memeConfidence`), `situationText`, `useWhen` (both optional). On the degraded path (the parse failed) the situation axis's `queryValues` holds the raw message text
    - `clusterId` (integer, optional) and `clusterSize` (integer, optional): the duplicate cluster collapsed into this result, the result itself included
    - Fields whose value is null (`situationText`, `useWhen`, `profile.situation`, `nextCursor`, `clusterId`, `clusterSize`) are omitted from the response; clients must treat them as optional
  - `facets` (array of `Facet`): one per axis, counted over the collapsed, filtered candidate set
  - `nextCursor` (string, optional) and `hasNext` (boolean): `nextCursor` addresses the last returned item
  - `profile` (`QueryProfile`): how the message was understood (echo of the edited profile when one was sent)
  - `degraded` (boolean) and `fallback` (string): `none`, or the reason (`unavailable`, `timeout`, `invalid_output`, `empty_input`, `breaker_open`, `embed_unavailable`, or several joined with `+`)
  - `meta`: `available`, `parseMs`, `embedMs`, `retrievalMs`, `candidates`, `cacheHit`, `parserVersion`, `model`, `queryHash` (optional string: the sha256 of the normalised message, the key of its ratings, see Rate A Result; present on every computed or cached answer, absent when `meta.available` is `false`). `retrievalMs` is the time from the end of the embedding to the end of the response work (candidate generation, scoring, fusion, duplicate collapse and facets); on a cache hit `parseMs` and `embedMs` are 0 and `retrievalMs` is the time the hit itself took
- **Behavior**:
  - Results are cached per request for 120 seconds when healthy and 10 seconds when degraded (the response carries `Cache-Control: no-store`; the cache is server side and `meta.cacheHit` reports it)
  - Lexical matching of the message text (only the 20 longest distinct tokens, `search.meme_from_text.lexical.max_tokens`) runs only when the parse or the embedding fell back (`degraded: true`); healthy requests do not use it, so `explanation` has no `lexical` axis then (`search.meme_from_text.lexical.only_when_degraded`, default true)
  - Flagged, inactive and profile-less images never appear on the axis path; a profile-less image can only appear as a lexical-only hit with `axisPath: false`, which therefore happens only in degraded responses (unless `lexical.only_when_degraded` is switched off)
  - The endpoint never fails because a model failed: it answers with `degraded: true` and a `fallback` reason (a failed parse falls back to lexical retrieval, a failed embedding to the axes without the situation)
  - An environment without a parser (no `parse.model`, the `it` environment), or whose pipeline could not be built at startup (a misconfigured key shows as this answer plus one startup warning), answers `200` with `items: []`, `degraded: true`, `fallback: "unavailable"` and `meta.available: false`. An environment with a parser but no profiles answers `meta.available: true` with empty or lexical-only results
- **Error Responses**:
  - `422`: malformed request (missing or empty `text`, `limit` outside 1 to 50, unknown `languageHint`, wrongly typed `filters`) or an invalid edited `profile`; for an invalid profile `detail` is the list of error strings. Request limits: `text` 8000 characters, filter arrays 64 entries, filter and tag strings 128 characters, `cursor` 512 characters. For request validation errors `detail` is a list of `{ "loc", "msg", "type" }` objects only; the rejected input is never echoed (no `input`, no `ctx`), so the message text cannot appear in the body
  - `500`: the search failed unexpectedly (for example a database error); the body is the generic `{"detail": "meme-from-text search failed"}` and neither the body nor the server log contains the message text
- **Example**: `POST /api/meme-from-text/search`

```json
{ "text": "My boss just moved the deadline up again", "languageHint": "en", "filters": { "emotion": ["anger"] }, "limit": 10 }
```

#### Get The Vocabulary

The vocabulary behind query profiles, for the chips editor and facet labels of the web UI.

- **URL**: `/api/meme-from-text/taxonomy`
- **Method**: `GET`
- **Response** `200`: `{"version": 2, "axes": [...]}` where each axis has `name` (`situation`, `emotion`, `stance`, `topic`, `tone`), `type` (`free_text` or `vocabulary`), `maxChars` (free-text axes) or `maxValues` (vocabulary axes), and for vocabulary axes `values`: a list of `{key, definition, labels: {en, ru, es}}`. `version` equals the `taxonomyVersion` of query profiles; a client that sees a different version in a search response should refetch.
- **Caching**: `Cache-Control: public, max-age=300`. The data does not depend on the database and is identical in every environment.

#### Rate A Result

Lets a person grade how well a result fits a message (0 not a fit, 1 acceptable, 2 good fit). Ratings are for evaluation only and never affect ranking. A message is identified by its hash: the server normalises the text (the first 600 characters, whitespace collapsed, like the search) and derives the sha256, which the search returns as `meta.queryHash` and these calls return as `queryHash`.

Trust model: like the rest of the API these endpoints are unauthenticated, and a `queryHash` is only as secret as its message, so anyone on the network who can guess an exact message can read or delete its ratings. They are meant for a single-owner tool, not a public deployment. A message containing a NUL character cannot be rated (`422`).

**Rate (create or change)**

- **URL**: `/api/meme-from-text/ratings`
- **Method**: `PUT`
- **Request Body** (`RatingRequest`, all fields required):
  - `text` (string, 1 to 8000 characters): the message that was searched; normalised and cut at 600 characters like the search
  - `imageId` (string): the rated meme
  - `grade` (integer, 0 to 2)
  - `rank` (integer, at least 1): 1-based position in the list shown, after duplicate collapse
  - `reading` (string): `parsed` or `edited` (whether the results came from the parsed or an edited profile)
  - `parserVersion` (string, at most 128 characters), `taxonomyVersion` (integer, at least 0), `degraded` (boolean), `fallback` (string, at most 128 characters), `language` (`en`, `ru`, `es` or `other`): the search context, copied from the response
- **Response** `200`: `RatingResponse` with `queryHash`, `imageId` and `grade`
- **Behavior**: one rating per message and image; rating again replaces the grade and context (upsert). The message text is stored (normalised) with the rating; it is never written to application logs or error bodies. It is also recorded in the query log (`GET /api/history?kind=meme_text`) for every fresh message, and both the rating text and the log rows are cleared by `DELETE /api/meme-from-text/ratings/text`.

**List the grades of one message**

- **URL**: `/api/meme-from-text/ratings?queryHash=<hash>`
- **Method**: `GET`
- **Response** `200`: `RatingsResponse`: `queryHash` and `ratings` (array of `{ imageId, grade }`)

**Remove one rating**

- **URL**: `/api/meme-from-text/ratings?queryHash=<hash>&imageId=<id>`
- **Method**: `DELETE`
- **Response** `204` (also when there was nothing to remove)

**Clear the stored text of a message**

- **URL**: `/api/meme-from-text/ratings/text?queryHash=<hash>`
- **Method**: `DELETE`
- **Response** `204`
- **Behavior**: erases the stored message text for every rating of that message; the grades, the hash and the image ids stay. Rating the same message again stores the text again. It also deletes that message's rows from the query log (`GET /api/history?kind=meme_text`), matched by `queryHash`.

**Error Responses** (all four calls):
  - `404`: unknown or inactive image (`PUT`), `{"detail": "image not found"}`
  - `422`: malformed request, a blank message after normalisation, an `imageId` that is not a valid id or a `queryHash` that is not 64 lowercase hex characters, or a message containing a NUL character; for request validation errors `detail` is a list of `{ "loc", "msg", "type" }` objects only (no `input`, no `ctx`, the rejected input is never echoed), and for errors raised by the service it is a fixed string; neither contains the message text
  - `500`: unexpected failure; the body is the generic `{"detail": "rating request failed"}` and neither the body nor the server log contains the message text

#### Meme profile curation

Lets a person read, correct, confirm and reset the profile (situation, use-when and taxonomy axis values) that the meme-from-text search matches a meme by. A saved profile is marked human verified, and the batch jobs never rewrite a human verified profile (they report it as needing review when the taxonomy has moved on).

Trust model: like the rest of the API these endpoints are unauthenticated and the last write wins (there is no version check), so they are meant for a single-owner tool, not a public deployment. Concurrent curator and batch writes to one image are serialised by a row lock on the profile; a curator request waits at most 10 seconds for it and otherwise fails with the generic `500`. Only active images can be curated.

**`CuratedProfile`** (the response of the first three calls, all fields always present):
  - `imageId` (string), `exists` (boolean: `false` for an image with no profile yet, in which case the other fields are empty and `vector` is `none`), `humanVerified` (boolean), `needsReview` (boolean: human verified under an older taxonomy version than the current one)
  - `taxonomyVersion` (integer or `null`: the version the profile was written under), `currentTaxonomyVersion` (integer)
  - `situation`, `useWhen` (string or `null`), `situationConfidence` (number 0 to 1 or `null`; `null` for human text)
  - `values`: `{ emotion, stance, topic, tone }`, each an array of `{ key, confidence, source }` (`source` is `model` or `human`; a human value has confidence `1.0`)
  - `vector`: the state of the situation embedding used by the search: `fresh` (a situation embedding made with the configured model exists), `pending` (there is a situation but its embedding is missing or was made with another model, so the next `build_meme_profile_embeddings` run will compute it) or `none` (no situation text). `vector` compares the model name only: a changed model digest is detected by the embeddings job, not by this field
  - `extractedAt`, `updatedAt` (ISO 8601 strings or `null`)

**Get a profile**

- **URL**: `/api/meme-from-text/profiles/{imageId}`
- **Method**: `GET`
- **Response** `200`: `CuratedProfile`

**Save a human edit**

- **URL**: `/api/meme-from-text/profiles/{imageId}`
- **Method**: `PUT`
- **Request Body** (`CuratedProfileSaveRequest`, all fields required; replaces the whole profile):
  - `situation` (string or `null`, at most 160 characters)
  - `useWhen` (string or `null`, at most 300 characters)
  - `values`: `{ emotion, stance, topic, tone }`, each an array of taxonomy keys (strings, at most 48 characters each; the taxonomy limits the number of values per axis)
- **Response** `200`: the saved `CuratedProfile`
- **Behavior**: the profile becomes human verified at the current taxonomy version, and every value is stored as `source: human`, confidence `1.0`. Axis values take effect for the search at once. When the situation text changed, the new situation vector is computed inline (bounded by `profile_editor.embed_timeout_seconds`) and stored in the same transaction as the profile, so it takes effect at once too; if the embedding service is unavailable or too slow the profile is still saved and `vector` is `pending` until the next embeddings run. A save also clears the image's failure record for the profile pipeline. A saved, confirmed or reset profile is used by the search at once, but a repeated identical query can be served from the search result cache for up to 120 seconds. The situation text is never logged.

**Confirm a profile**

- **URL**: `/api/meme-from-text/profiles/{imageId}/confirm`
- **Method**: `POST`
- **Response** `200`: the `CuratedProfile`, now human verified at the current taxonomy version
- **Behavior**: marks the existing profile as verified without changing its content. Confirm re-validates the stored profile like a save does and refuses it (`422`) when it is no longer valid for the current vocabulary (a removed key, more values on an axis than the vocabulary now allows, a situation over the limit): edit and save it instead. A confirm also clears the image's failure record for the profile pipeline. An image with no profile answers `409`.

**Reset a profile**

- **URL**: `/api/meme-from-text/profiles/{imageId}`
- **Method**: `DELETE`
- **Response** `204` (also when there was nothing to reset)
- **Behavior**: deletes the profile and its situation embedding, and also clears the image's failure record for the profile pipeline, so the next `build_meme_profiles` run extracts the image again.

**Error Responses** (all four calls):
  - `404`: unknown or inactive image, `{"detail": "image not found"}`
  - `409`: Confirm on an image with no profile; the body is a fixed `{"detail": "..."}` message
  - `422`: malformed request (a missing axis, a value that is not a string, a text over its limit; `detail` is a list of `{ "loc", "msg", "type" }` objects only, the rejected input is never echoed), an `imageId` that is not a valid id, an unknown vocabulary key, too many values on an axis, or Confirm on a profile that is no longer valid for the current vocabulary (a fixed string, never the free text)
  - `500`: unexpected failure; the body is the generic `{"detail": "profile request failed"}` and neither the body nor the server log contains the profile text

#### Profile coverage (admin)

Live numbers on how much of the active library has a profile, and the images behind each number. Unauthenticated like the rest of the admin API. The counts are computed on every call (they are not a snapshot).

**Coverage counts**

- **URL**: `/api/admin/profile-coverage`
- **Method**: `GET`
- **Response** `200`: `ProfileCoverage`:
  - `active` (active images), `profiled` (with a profile), `withoutProfile`, `humanVerified`, `needsReview` (human verified under an older taxonomy version), `staleTaxonomy` (model profiles written under an older taxonomy version), `failed` (images whose profile extraction is recorded as permanently failed, whether or not they also have a profile), `pendingVector` (profiles whose situation vector is missing or stale for the configured embedding model), `currentTaxonomyVersion` (all integers)
  - `lastRuns`: `{ profiles, embeddings }`, the most recent `build_meme_profiles` and `build_meme_profile_embeddings` run, each `null` when there was none or `{ runId, status, startedAt, completedAt (null while running), stats }`. `stats` is the free-form object the run recorded, as of that run (it does not change with later edits) or `null`.

**Images of one category**

- **URL**: `/api/admin/profile-coverage/images?category=<category>&cursor=<id>&limit=<n>`
- **Method**: `GET`
- **Query Parameters**:
  - `category` (required): `without_profile`, `needs_review`, `stale_taxonomy`, `failed`, `human_verified` or `pending_vector` (same meaning as the counts)
  - `cursor` (optional): the `nextCursor` of the previous page
  - `limit` (optional, 1 to 100, default 48)
- **Response** `200`: `ProfileCoverageImages`: `items` (array of `{ id, imageUrl, humanVerified }`, `imageUrl` is `/api/images/{id}`), `nextCursor` (string or `null` on the last page), `total` (images in the whole category). Pages are ordered by image id.
- **Error Responses**: `422` for an unknown `category`, a `limit` outside 1 to 100 or a `cursor` that is not a valid id; `500` for an unexpected failure

#### Discovery report (admin)

The three views of `python -m tools.discovery_report` (see `docs/discovery-report.md`), served for the `/admin/discovery` page. Read only: each request runs in a `READ ONLY` transaction. Unauthenticated like the rest of the admin API (see `docs/security/admin-permissions-todo.md`). Search text is returned to the caller and never logged. Responses are sent with no-cache headers. Every route returns the top `limit` rows by `sort`; there is no paging.

**Memes that keep coming back**

- **URL**: `/api/admin/discovery/memes`
- **Method**: `GET`
- **Query Parameters** (defaults equal the CLI's):
  - `windowDays` (optional, at least 1, default `popularity.impressions.window_days`)
  - `sources` (optional, comma list of absorption sources, default every source except `adjustment`)
  - `minRedrops` (optional, default 1), `maxImpressions` (optional, at least 0; needs the impressions table)
  - `sort` (optional): `per_100` (default), `redrops`, `redrops_window`, `popularity`, `saves`, `impressions`
  - `limit` (optional, 1 to 200, default 50)
- **Response** `200`: `DiscoveryMemes`: `windowDays`, `sources`, `impressionsAvailable` (false when the impressions table does not exist yet), `items`. Each item: `id`, `imageUrl` (`/api/images/{id}`), `filename`, `redrops`, `redropsWindow`, `sources` (array of `{ source, count }`), `popularity`, `saves`, `impressions` and `impressionsWindow` (`null` without the impressions table), `lastBack` (ISO time or `null`), `per100` and `per100Infinite`. `per100` is re-drops in the window per 100 impressions in the window; JSON cannot carry infinity, so a meme re-dropped in the window and never shown there has `per100: null` and `per100Infinite: true`; without impression data `per100` is `null` and `per100Infinite` false.

**Searches that found little**

- **URL**: `/api/admin/discovery/queries`
- **Method**: `GET`
- **Query Parameters**: `kind` (`search` default, `meme_text`, `all`), `maxResults` (default 3), `includeDegraded` (default false), `sinceDays`, `minTimes` (default 1), `sort` (`times` default, `last`, `min_results`), `limit` (1 to 200, default 50)
- **Response** `200`: `DiscoveryQueries`: `kind` and `items`: `query` (normalised, empty for a facet-only search), `tags` (array of `category:value`), `kind`, `times`, `firstAt`, `lastAt`, `minResults`, `avgResults`, `web`, `android`, `degraded`, `fallbacks` (array)

**Searches that ended without a save**

- **URL**: `/api/admin/discovery/misses`
- **Method**: `GET`
- **Query Parameters**: `gapMinutes` (default 5), `kind` (`search` default, `meme_text`, `all`), `sinceDays`, `minTimes` (default 1), `explicitOnly` (default false), `sort` (`times` default, `explicit`, `last`), `limit` (1 to 200, default 50)
- **Response** `200`: `DiscoveryMisses`: `status` (`ok`, `no_schema` when the search-outcomes migration has not run, `no_outcomes` when there is no linked save or press yet), `cutoff` (ISO time of the first outcome, or `null`; older searches are not counted), `gapMinutes`, `kind` and `items`: `query`, `tags`, `kind`, `times` (sessions), `explicit` (presses of "I didn't find it"), `searches` (average searches per session), `firstAt`, `lastAt`, `web`, `android`

**Errors (all three)**: `422` for a value out of range or an unknown `sort`, `kind` or source (the body is `{"detail": "<our message>"}`, never search text); `500` with `{"detail": "discovery request failed"}` for an unexpected failure.

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

Get the top-ranked images associated with a specific concept. Only `active` images are ranked (not `pending`, `rejected` or `cut_proposed`).

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

List distinct dates that have at least one *completed* trend run. Runs that are still in
progress, failed, or were aborted are excluded — they have no usable trend data, so their date
must not shadow the actual last successful run's date.

- **URL**: `/api/trends/dates`
- **Method**: `GET`
- **Query Parameters**:
  - `label` (optional): Filter by label
  - `name` (optional): Filter by name
- **Response**: `Array<string>` (YYYY-MM-DD, descending)
- **Example**: `GET /api/trends/dates?label=reddit`

#### Get Runs for Date

List all runs that occurred on a given date, regardless of status (started/completed/failed/
aborted) — unlike Get Available Dates and Get Latest Run for Date, this endpoint is not filtered
to completed runs, since it's meant for inspecting a date's full run history including failures.

- **URL**: `/api/trends/dates/{run_date}/runs`
- **Method**: `GET`
- **Path Parameters**:
  - `run_date`: Date in `YYYY-MM-DD` format
- **Response**: `Array<TrendsRun>`
- **Example**: `GET /api/trends/dates/2026-06-10/runs`

#### Get Latest Run for Date

Return the most recent *completed* run for a given date (404 if none — e.g. if every run on
that date is still in progress, failed, or was aborted).

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

#### Report "I didn't find it"

The web's explicit miss for one search (epic meme-popularity, S6). Needs the S6 migration (`search_not_found`).

- **URL**: `/api/history/{search_id}/not-found`
- **Method**: `POST`
- **Path Parameters**:
  - `search_id`: the `searchId` returned by a search response
- **Response**: `204`, no body, for any value (an unknown, pruned or malformed id is not an error). Best effort: the press
  is stored once per search (a repeat is a no-op); with `popularity.search_outcomes.enabled: false` nothing is stored
- **Cache**: no-cache

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
| `kind`    | string | `search` | `search` (image searches only), `meme_text` or `all`; any other value is a `422` |

`meme_text` rows hold the pasted message text (in `query`), its parse and the outcome of the search. The message text is therefore stored in the query log; it is removed per message by `DELETE /api/meme-from-text/ratings/text`. This endpoint is unauthenticated, like every endpoint of the backend (see `docs/security/admin-permissions-todo.md`).

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

Returns row counts across all major tables from a precomputed snapshot (table `statistics_snapshots`), refreshed hourly, after every wrapped batch run, and on demand via the `build_statistics` job in `/admin/batches`. `computed_at` says when the snapshot was computed. If no snapshot exists yet (fresh environment), or the stored snapshot is older than 2 hours, the request computes and stores a fresh one, then returns it.

- **URL**: `/api/diagnostics/statistics`
- **Method**: `GET`
- **Query Parameters**:
  - `live` (boolean, optional, default `false`): when `true`, skips the stored snapshot, computes the statistics on the fly, stores the result as the new snapshot and returns it (`computed_at` is then the time of this request). The statistics page calls the plain endpoint first (instant) and then `?live=true` to refresh.
- **Response**: `StatisticsResponse`
- **Example**: `GET /api/diagnostics/statistics` or `GET /api/diagnostics/statistics?live=true`

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
    "flagged": 82,
    "duplicate_clusters": 14,
    "ocr_missing_text_heavy_classification": 484,
    "text_heavy_missing_embeddings": 0,
    "embeddings_missing_lemmas": 5
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
  },
  "comics": {
    "draft": 2,
    "built": 5,
    "panelsKept": 9,
    "panelsRetired": 11,
    "builtUnprocessed": 1
  },
  "computed_at": "2026-09-29T12:00:00Z"
}
```

`comics` counts the multi-image comics (draft and built by `comics.state`), `panelsKept` (panels with `kept = true`, in any comics), `panelsRetired` (panels with `kept = false` in built comics) and `builtUnprocessed`: built comics images (`comics.built_image_id`) for which the OCR job has not recorded a `done` marker (`image_processing_status`, pipeline `easyocr:en`) or that have no embedding. The OCR marker is used instead of `ocr_texts` rows because an image without text legitimately has no OCR row. A stored snapshot written before the `comics` block existed fails response validation and is recomputed on read (the same path as any other stale payload shape), so the block is always present in the response.

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
- `tier_remaining` (int, nullable) and `blocked_total` (int, nullable): live review-progress counts,
  computed on every call. Both `null` outside `tier_a_review`/`tier_b_review`/`promoted` stages (no
  active tier to count); both real integers inside them. `tier_remaining` is scoped to whichever tier
  `stage` currently is; `blocked_total` is batch-wide across both tiers.
- **Example**: `GET /api/ingestion/run`

```json
{
  "run_id": "b3f1...",
  "status": "started",
  "stage": "tier_a_review",
  "stats": { "intake": 3, "hash_duplicates_in_batch": 0, "hash_duplicates_cross_corpus": 0, "registered": 3 },
  "tier_remaining": 2,
  "blocked_total": 2,
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

Each edge also carries `distance_source` — `"clip"` (CLIP visual embedding) or `"ocr_text"`
(OCR-text sentence embedding, text-heavy-vs-text-heavy pairs only), per
`2026-09-18-text-embedding-duplicate-matching.md`. The two signals use different distance scales,
so `distance_source` tells a reviewer which scale a given `distance` value is on.

- **URL**: `/api/ingestion/clusters/{tier}`
- **Method**: `GET`
- **Path params**: `tier` — `tier_a` (pre-OCR, tight threshold) or `tier_b` (post-OCR-prepass, loose threshold)
- **Query params**:
  - `cursor` — optional, opaque pagination token. Omit for the first page.
  - `limit` — optional, default `40`, must be between `1` and `200`.
- **Response**: `ClusterPage`
- **Example**: `GET /api/ingestion/clusters/tier_a`

```json
{
  "items": [
    {
      "members": [
        { "image_id": "1a2b...", "filename": "meme_01.jpg", "status": "pending", "ocr_text": "Не смешно", "text_heavy": false },
        { "image_id": "3c4d...", "filename": "meme_02.jpg", "status": "active", "ocr_text": "Не смешно, совсем", "text_heavy": true }
      ],
      "edges": [
        { "image_id1": "1a2b...", "image_id2": "3c4d...", "distance": 0.041, "match_source": "cross_corpus", "distance_source": "clip" }
      ],
      "total_members": 2
    }
  ],
  "next_cursor": "0.041|1a2b...",
  "has_next": true
}
```

Clusters are ordered by their tightest edge (closest match first). `next_cursor` is `null` on the last page; pass it back verbatim as `?cursor=` for the next page. A stale/invalid cursor restarts from the first page rather than erroring.

A large union-find component is split into tight subgroups for review — each subgroup is a separate item in `items`. Decisions are per-image and settle every candidate pair regardless of which subgroup an image is shown in.

Each cluster carries `total_members` — the subgroup's full member count before capping. `members` is capped (currently 60) per cluster — the tightest by minimum incident edge distance, returned tightest-first — and `edges` is filtered to those members. A cluster with `total_members > len(members)` is shown **read-only** in the review UI: its shown members are context only, with no Keep/Reject controls, permanently — because keeping/rejecting a shown member would settle its candidate pairs against the members not returned (`keep` marks every pair touching the image as reviewed), potentially promoting unreviewed near-duplicates. In practice this can only occur for Tier A: Tier B routes to `GET /api/ingestion/review/tier_b` instead, which has no such cap. Tier A has no per-image review path, so an oversized Tier A group stays context-only and promote-blocked, with its unresolved pairs intact, for as long as it stays oversized.

Each member carries `text_heavy` — `true` if the [text-heavy classifier](docs/superpowers/specs/2026-09-15-text-heavy-classifier.md) marked the image `text_heavy`; `false` for both a `not_text_heavy` result and an image not yet classified (e.g. a `pending` image before `classify_text_heavy` has run against it). Compute+store only — this flag doesn't affect matching or candidate selection.

#### Tier B Review

One item per pending image with an unresolved Tier B candidate pair, ordered by the image's
tightest candidate distance. Candidates are capped at 10, prioritized by whether the candidate has
ever been reviewed before: a candidate that's still `pending` (never reviewed, from this same
intake) sorts ahead of one that's already `active` (reviewed and approved in an earlier ingestion),
tightest distance first within each group — surfacing genuinely-new comparisons ahead of a tighter
match against an already-vetted corpus image. `total_candidates` is the uncapped count. Decisions
go to `/api/ingestion/clusters/tier_b/resolve` as usual and settle every pair touching the decided
image.

- **URL**: `/api/ingestion/review/tier_b`
- **Method**: `GET`
- **Query params**:
  - `cursor` — optional, opaque pagination token. Omit for the first page.
  - `limit` — optional, default `40`, must be between `1` and `200`.
- **Response**: `TierBReviewPage` — `items[]` of `{ image, candidates: [{ member, distance, match_source, distance_source }], total_candidates }`, plus `next_cursor` (opaque string, `null` on the last page) and `has_next`.
- **Example**: `GET /api/ingestion/review/tier_b`

```json
{
  "items": [
    {
      "image": { "image_id": "1a2b...", "filename": "new_01.jpg", "status": "pending", "ocr_text": "Не смешно", "text_heavy": false },
      "candidates": [
        {
          "member": { "image_id": "3c4d...", "filename": "meme_02.jpg", "status": "active", "ocr_text": "Не смешно, совсем", "text_heavy": true },
          "distance": 0.08,
          "match_source": "cross_corpus",
          "distance_source": "clip"
        }
      ],
      "total_candidates": 5
    }
  ],
  "next_cursor": "0.08|1a2b...",
  "has_next": true
}
```

#### Resolve Cluster

Applies per-image `reject`/`keep` decisions. `reject` flips the image's `status` to
`rejected` and moves its file to `BASE_PATH/rejected/`; `keep` marks every candidate pair
touching that image as reviewed for this tier, so it won't resurface in this tier's queue.
Partial resolution is allowed — omit members you're not ready to decide yet.
Each decision is applied independently -- one decision's failure (database or filesystem) never
discards others in the same call. `failed` lists decisions whose database write didn't apply
(safe to retry as-is); `move_failed` lists rejects whose database write committed but whose file
move failed (the image is correctly `rejected` in the database -- `undo_reject` on it is safe and
will simply find nothing to move back).

Popularity: each `reject` also records, server-side, the best remaining neighbour of the rejected image as its
winner (source `ingest_tier_a` or `ingest_tier_b`). The winner is chosen from the pair band of the tier among
`active` and `pending` neighbours, excluding images rejected anywhere in the same request, preferring `active`,
then images kept in the request, then the most popular, then the oldest. The client sends no winner. A
popularity failure never fails the reject.

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
{
  "rejected": ["a1b2..."],
  "kept": ["c3d4..."],
  "failed": [],
  "move_failed": []
}
```

#### Undo Reject

Reverts a `rejected` image back to `pending` and moves its file back out of
`BASE_PATH/rejected/`. It also reverses the popularity edge recorded when the image was rejected (reason
`undo_reject`); a popularity failure never fails the call.

- **URL**: `/api/ingestion/images/{image_id}/undo-reject`
- **Method**: `POST`
- **Response**: `UndoRejectResponse`
- **Errors**: `404` if the image doesn't exist or isn't currently `rejected`
- **Example**: `POST /api/ingestion/images/a1b2.../undo-reject`

```json
{ "image_id": "a1b2...", "status": "pending" }
```

---

### Recut review

The review queue of the screenshot recut (epic `docs/superpowers/specs/2026-10-17-screenshot-recut/`): proposed cuts of badly cropped images, with the rectangle (in pixels of the source) the cut was taken from. The review page decides a proposal with the POST endpoints below; a decision moves no file (removal is `move_flagged`, then `unregister_deleted_images`), but an approval also copies the source file to `BASE_PATH/pre-cuts/<filename>` after the commit (never overwriting; a failed copy is logged and does not fail the approval). Unauthenticated like the rest of the admin API. The images themselves are served by `GET /api/images/{id}`, which also serves `cut_proposed` images.

**List the proposals**

- **URL**: `/api/recut/proposals?offset=<n>&limit=<n>`
- **Method**: `GET`
- **Query Parameters**: `offset` (optional, >= 0, default 0), `limit` (optional, 1 to 200, default 50)
- **Response** `200`: `RecutProposalPage`: `items` (array of `{ id, sourceImageId, cutImageId, confidence, box }`, `confidence` is a number or `null`, `box` is `{ x, y, w, h }` in pixels of the source image) and `total` (every proposal waiting for review, not only this page). Only `proposed` proposals with both images are listed, ordered by confidence (highest first, unscored last) and then by `id`.
- **Error Responses**: `422` for a bad `offset` or `limit`

**One proposal**

- **URL**: `/api/recut/proposals/{id}`
- **Method**: `GET`
- **Response** `200`: `RecutProposalDetail`: the item fields plus `state` (`proposed`, `approved`, `rejected`...), `detector`, `detectorVersion`, `detectRunId` (or `null`), `createdAt` (ISO 8601), `detectedBox` (`{ x, y, w, h }`, the detector's original box once the owner has adjusted the proposal, otherwise `null`) and `previousId`/`nextId`, the neighbours in the list order (`null` at either end, and both `null` when the proposal is not `proposed`). A proposal of any state is answered as long as both its images still exist, so a link survives its decision.
- **Error Responses**: `404` for an unknown id or a proposal whose source or cut image no longer exists; `422` for an `id` that is not a UUID

**Review statistics**

- **URL**: `/api/recut/stats`
- **Method**: `GET`
- **Response** `200`: `RecutStats`: `bands` (six, in a fixed order: `0.6-0.7`, `0.7-0.8`, `0.8-0.9`, `0.9-1.0`, `1.0`, `unscored`; each `{ label, lower, upper, waiting, approved, rejected, adjusted, approveRate }`, `lower`/`upper` are `null` for `unscored`; `adjusted` counts the proposals of the band that carry a stored detector box, i.e. whose box was adjusted, in any of the three states) and `total` (the same shape, label `total`). `waiting` counts proposals in state `proposed`; `approveRate` is `approved / (approved + rejected)`, or `null` while nothing in the band is decided. Counted by detector confidence, computed on every request. Bands: lower edge inclusive, anything under 0.7 is in the first band (the detector's floor is 0.6), `1.0` is its own band.

**Approve or reject a proposal**

- **URL**: `/api/recut/proposals/{id}/approve` and `/api/recut/proposals/{id}/reject`
- **Method**: `POST`, no body
- **Response** `200`: `RecutDecision`: `id`, `state` (`approved` or `rejected`) and `decidedAt` (ISO 8601).
  - Approve: the cut becomes `active`, the source is flagged (remarks `recut_approved`) for `move_flagged`, the cut takes over the source's popularity and text ratings, and the `approved` event is recorded.
  - Reject: the cut is flagged (remarks `recut_rejected`), the source is untouched, and the `rejected` event is recorded.
- **Error Responses**: `404` for an unknown id; `422` for an `id` that is not a UUID; `409` with `detail` one of `already_decided` (the proposal is not `proposed`), `cut_missing` (no cut image, or it is not `cut_proposed`), and for approve only `source_missing`, `source_not_active`, `source_flagged` (the source is already flagged for removal). Nothing is changed on a `409`.

**Adjust the box of a proposal**

- **URL**: `/api/recut/proposals/{id}/adjust`
- **Method**: `POST`
- **Body**: `RecutAdjustRequest`: `{ x, y, w, h }`, integers in pixels of the source image (`x`, `y` >= 0, `w`, `h` >= 16)
- **Response** `200`: the updated `RecutProposalDetail`. The proposal keeps its id, state (`proposed`) and `confidence` (the detector's, for its original box); `box` is the new box, `cutImageId` is a NEW cut image (cropped from the source in its format, derived data copied from the source exactly like the first cut, so OCR, lemmas, tags and descriptions are stale until the next `recut_pipeline` run), the previous cut image is deleted, and `detectedBox` holds the detector's box (written by the first adjustment only). The `adjusted` event is recorded once per proposal. The old cut file is removed after the commit; a failed request leaves no new file.
- **Error Responses**: `404` for an unknown id; `409` with `detail` one of `already_decided`, `cut_missing`, `source_missing`, `source_not_active`, `source_flagged` (as for approve), `source_changed` (the source file differs from the one the detector saw) or `source_unreadable`; `422` for an `id` that is not a UUID, a body that is malformed, a side under 16 px, a box equal to the current box, or a box that does not fit the source (`detail` says which); `500` with `detail` `write_error` when the new cut file could not be written. Nothing is changed on any error. The backend process must be able to write into `BASE_PATH`. Do not adjust while a recut batch job (`create_recut_cuts`, `refresh_recut_cuts`, `recut_pipeline`) is running: the old cut image and file are deleted and the endpoint does not check for a running job.

---

### Comics

A comics is a named, ordered list of images (panels) that is later built into one image (epic `docs/superpowers/specs/2026-11-02-multi-image-comics/`). A comics is a `draft` (panels can change) or `built` (frozen). An image is a panel of at most one comics. Panel `position` values are stored 0-based. Unauthenticated like the rest of the API. Every error body is `{ "detail": { "code": <code>, ... } }`.

Error codes: `not_found` (404); `name_taken`, `built` (the comics is built and cannot change), `image_in_comics` (+ `comicsId`, `comicsName` of the comics holding the image), `not_buildable` (+ `reason`, see Build), `popularity_conflict` (409); `image_not_addable` (+ `reason`: `not_active`, `flagged`, `live_cut` 409; `not_found` 404); `name_invalid` (+ `reason`), `reorder_mismatch`, `keep_invalid`, `compose_failed` (+ `reason`, `position`) (422). A body or parameter that does not validate is a plain 422.

Shapes: `ComicsSummary` `{ id, name, state, panelCount, coverImageId, createdAt, builtAt }` (cover is the built image for a built comics, else the first panel's image, `null` when none or retired); `ComicsDetail` `{ id, name, state, builtImageId, builtAt, panels: [{ id, position, imageId, sourceFilename, kept, sourceImageId }] }` (panels in position order; `imageId` is `null` when the source image is gone; `kept` is true when the panel's source image stayed active after a build; `sourceImageId` is the panel's original source image id, kept after `imageId` is cleared).

**List comics**

- **URL**: `/api/comics?q=<text>&limit=<n>&offset=<n>`
- **Method**: `GET`
- **Query Parameters**: `q` (optional, case-insensitive substring of the name), `limit` (1 to 100, default 50), `offset` (>= 0, default 0)
- **Response** `200`: `ComicsPage`: `items` (`ComicsSummary`, newest first) and `hasMore`.
- **Error Responses**: `422` for a bad `limit` or `offset`

**Create a comics**

- **URL**: `/api/comics`
- **Method**: `POST`
- **Body**: `{ name }`
- **Response** `201`: `ComicsDetail` (an empty draft)
- **Error Responses**: `409` `name_taken`; `422` `name_invalid`

**One comics**

- **URL**: `/api/comics/{id}`
- **Method**: `GET`
- **Response** `200`: `ComicsDetail`
- **Error Responses**: `404` `not_found`; `422` for an `id` that is not a UUID

**Rename a comics**

- **URL**: `/api/comics/{id}`
- **Method**: `PATCH`
- **Body**: `{ name }`
- **Response** `200`: `ComicsDetail`
- **Error Responses**: `404` `not_found`; `409` `built`, `name_taken`; `422` `name_invalid`

**Delete a comics**

- **URL**: `/api/comics/{id}`
- **Method**: `DELETE`
- **Response** `204` (no body); its panels go with it, the images are untouched
- **Error Responses**: `404` `not_found`; `409` `built`

**Add a panel**

- **URL**: `/api/comics/{id}/panels`
- **Method**: `POST`
- **Body**: `{ imageId, move? }`. The image becomes the last panel. With `move: true` an image that is already a panel of another draft comics is taken from it (the other comics' panels are renumbered).
- **Response** `201`: the `ComicsDetail` of `{id}`
- **Error Responses**: `404` `not_found` (comics, or with `move` the image is in no comics), `image_not_addable` with reason `not_found`; `409` `built`, `image_in_comics` (without `move`), `image_not_addable`; `422` for an `imageId` that is not a UUID

**Remove a panel**

- **URL**: `/api/comics/{id}/panels/{panelId}`
- **Method**: `DELETE`
- **Response** `204` (no body); the remaining panels are renumbered 0..n-1
- **Error Responses**: `404` `not_found` (unknown comics, or the panel is not a panel of `{id}`); `409` `built`

**Reorder the panels**

- **URL**: `/api/comics/{id}/panels/order`
- **Method**: `PUT`
- **Body**: `{ panelIds }`, every panel id of the comics exactly once, in the new order
- **Response** `200`: `ComicsDetail`
- **Error Responses**: `404` `not_found`; `409` `built`; `422` `reorder_mismatch` (the list is stale: nothing is changed) or an id that is not a UUID

**Preview of a draft**

- **URL**: `/api/comics/{id}/preview?maxWidth=1200`
- **Method**: `GET`
- **Query Parameters**: `maxWidth` (optional, 200 to 2400, default 1200): the preview is scaled down to this width, never up.
- **Response** `200`: `image/jpeg` bytes of the panels tiled in position order (2 per row), with `Cache-Control: no-store`. Nothing is stored. At most two previews are composed at a time per backend process; others wait.
- **Error Responses**: `404` `not_found`; `409` `built` (only drafts have a preview); `422` `{ code: "compose_failed", reason, position }` where `reason` is `no_panels` (position `null`), `unreadable` (a missing or damaged file), `unsupported_format`, `animated` or `too_large`, and `position` is the 1-based panel it concerns or `null`; `422` for a `maxWidth` out of range

**Build a comics**

- **URL**: `/api/comics/{id}/build`
- **Method**: `POST`
- **Body** (optional): `{ keepPanelIds? }`, default `[]`: panel ids of this comics whose source image stays active. The source of every other panel is flagged with `reason` `comics_source` and moves to `comics-sources/<name>` at the next move flagged run.
- **Response** `200`: `ComicsBuildResult` `{ comics: ComicsDetail, builtImageId }`. The comics is now `built` and frozen; the new image is registered active. Its derived data (embedding, OCR, tags, ...) is filled by the incremental jobs, not by this call: once the build is committed, the call starts the `comics_pipeline` batch (the same start as `POST /api/admin/batches/comics_pipeline/run`, a detached subprocess, trigger `manual`) and returns without waiting for it. The start is best effort: if a `comics_pipeline` run is already going nothing is started and the image waits for the next run, and any failure to start is only logged; the response is `200` either way. A refused build never starts it. Two builds whose output is byte-identical are serialised on the content hash, so the second is refused with `same_as_existing`.
- **Error Responses**: `404` `not_found`; `409` `built` (already built), `not_buildable` (`reason`: `too_few_panels`, `panel_missing`, `image_not_active` and `image_already_built` (internal consistency refusals, not expected in normal use), `panel_unavailable` with 1-based `position` and `cause` (`not_active`, `flagged`, `live_cut`, `file_missing`, `file_changed` or `already_discarded`), or `same_as_existing` with `imageId`), `popularity_conflict`; `422` `keep_invalid` (an id that is not a panel of this comics), `compose_failed` (`reason` as for the preview, `position` 1-based or `null`), or an id that is not a UUID. On any error nothing is changed. Flagging or unflagging a comics source through the images API is refused with `409` `comics_source_flag`.

**Addability of images**

- **URL**: `/api/comics/addability?imageIds=<id>,<id>,...`
- **Method**: `GET`
- **Query Parameters**: `imageIds` (required, 1 to 100 comma-separated UUIDs)
- **Response** `200`: `{ items: [{ imageId, addable, reason, comicsId, comicsName }] }`. `reason` is `null` when addable, else `not_found`, `not_active`, `flagged`, `live_cut` or `in_comics` (the last with `comicsId`/`comicsName`).
- **Error Responses**: `422` for no ids, more than 100 or a malformed UUID

**The comics of an image**

- **URL**: `/api/comics/by-image/{imageId}`
- **Method**: `GET`
- **Response** `200`: `{ comics: { id, name, state, position } | null }`; `null` when the image is no panel of any comics
- **Error Responses**: `422` for an `imageId` that is not a UUID

**Suggested comics (the candidate queue, C9)**

Groups of images that were saved seconds apart and look alike, found by the `detect_comics_candidates` batch job. These routes are declared before `/api/comics/{id}`.

- **URL**: `/api/comics/candidates?offset=0&limit=20`
- **Method**: `GET`
- **Query Parameters**: `offset` (default 0), `limit` (1 to 100, default 20)
- **Response** `200`: `ComicsCandidatePage` `{ items: [{ id, maxDistance, maxGapSeconds, images: [{ id, filename, width, height }] }], hasMore }`. Only open candidates whose images are all still active, not flagged, in no comics and present are listed, smallest `maxDistance` first (then id); `images` are in the group's order; `width`/`height` may be `null`. No total is returned.
- **Error Responses**: `422` for a parameter out of range

**Accept a suggestion**

- **URL**: `/api/comics/candidates/{candidateId}/accept`
- **Method**: `POST`
- **Body** (optional): `{ imageIds? }`, an ordered subset of the group's images (at least 2, no repeats); default all images in the group's order. Images switched off are not dismissed.
- **Response** `201`: the `ComicsDetail` of the new draft, named `Comics <first image's file name without extension>` (the same rule as the web app's default name; on a name conflict it is retried once as `... <first 8 characters of the first image id>`). The draft is created, the images added in order and the candidate marked `accepted` in one transaction.
- **Error Responses**: `404` `not_found`; `409` `candidate_decided` (with `state`), `candidate_incomplete` (no `imageIds` sent and a deleted image left fewer images than the group was stored with; with `size` and `remaining`), `image_not_addable` (with `reason` `not_active`, `flagged` or `live_cut`, and `imageId`) or `image_in_comics` (with `comicsId`, `comicsName`, `imageId`) for the first image that can no longer be added, `name_taken` (both names taken); `422` `subset_invalid` (`reason`: `too_few_images`, `unknown_image` for an id outside the group, `duplicate_image`) or an id that is not a UUID. On any error nothing is changed.

**Dismiss a suggestion**

- **URL**: `/api/comics/candidates/{candidateId}/dismiss`
- **Method**: `POST`
- **Response** `204` (no body); the candidate is `dismissed` for good. Dismissing an already dismissed candidate is a no-op `204`.
- **Error Responses**: `404` `not_found`; `409` `candidate_decided` (it was accepted); `422` for an id that is not a UUID

---

### Admin

Manual triggers for every batch job registered in `environments/batch_registry.yaml` /
`batch_registry.<environment>.yaml` (module + `kind` per name — `GET .../names` returns the
live list, fresh-read from that file on every call, so an edit there takes effect immediately
with no backend restart), plus status lookup for runs triggered this way. Which kinds count as
"admin" is derived from the same registry, not a separate hardcoded list — anything registered
is automatically admin-triggerable and shows up in run history.
`POST .../run` spawns the batch script as a detached subprocess and returns immediately with
`status: "running"` — it does not wait for the batch to finish. Progress and completion are
observed by polling `GET .../runs/{run_id}` or `GET .../runs`. Output is also written to
`logs/<environment>/<batch_name>_<timestamp>.log` on the machine running the backend.

#### List Batch Names

- **URL**: `/api/admin/batches/names`
- **Method**: `GET`
- **Response**: `BatchNamesResponse`
- **Example**: `GET /api/admin/batches/names`

```json
{ "names": ["trends_batch", "move_flagged", "unregister_deleted_images", "build_bow", "..."] }
```

#### Trigger Batch Run

- **URL**: `/api/admin/batches/{batch_name}/run`
- **Method**: `POST`
- **Path params**: `batch_name` — one of the names from `GET .../names`
- **Response**: `RunTriggerResponse`
- **Errors**:
  - `404` if `batch_name` isn't a registered batch
  - `409` if a run of that batch is already in progress
- **Example**: `POST /api/admin/batches/move_flagged/run`

```json
{ "run_id": "b3f1c2a4-...", "status": "running" }
```

  Triggering `move_flagged` also chains an automatic `unregister_deleted_images` run afterward,
  and triggering `build_concept_embeddings` similarly chains `tag_images_from_concepts` — each
  produces its own second, independent `batch_runs` row (visible via `GET .../runs`). There is
  no way to request the `--no-chain` opt-out through the API — that flag is direct-CLI-only.

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

#### Batch Metrics

- **URL**: `/api/admin/batches/metrics`
- **Method**: `GET`
- **Query params**: `process` (required; `recut`, `ingest_tier_a`, `ingest_tier_b` or `library_duplicates`), `limit` (number of runs, `1` to `200`, default `50`)
- **Response**: `BatchMetricsResponse`; unknown `process` is `404`, a `limit` out of range is `422`
- **Example**: `GET /api/admin/batches/metrics?process=recut&limit=10`

```json
{
  "process": "recut",
  "items": [
    {
      "run_id": "b3f1c2a4-...",
      "run_created_at": "2026-10-18T09:00:00Z",
      "counts": { "proposed": 4, "adjusted": 1, "approved": 1, "rejected": 1 },
      "metrics": { "approve_rate": 0.5, "decided_share": 0.5, "pending": 2, "adjusted_share": 0.25 }
    }
  ],
  "unattributed": {
    "counts": { "proposed": 0, "adjusted": 0, "approved": 0, "rejected": 0 },
    "metrics": { "approve_rate": null, "decided_share": null, "pending": 0, "adjusted_share": null }
  }
}
```

One item per detection run that has at least one event, newest run first. A decision is attributed to the run that
produced the proposal, however late it arrives. Rates are computed on every request and never stored:
`approve_rate` = approved / (approved + rejected), `decided_share` = (approved + rejected) / proposed, `pending` =
proposed - approved - rejected (pending proposals are not counted as rejections), `adjusted_share` = adjusted / proposed
(`adjusted` counts proposals whose box the owner corrected at least once; it is not a decision and is independent of
`approved`/`rejected`). A rate is `null` when its
denominator is zero. `unattributed` holds events whose batch run was pruned or never known; its counts are all zero when there
are none. Events come from the process code (screenshot recut: projects R3 and R5), so the list is empty until they ship.

The two ingestion processes (`ingest_tier_a`, `ingest_tier_b`, M2a) have events `proposed`, `rejected` and `kept`, one per
pending image, attributed to the ingestion run (`images.ingestion_batch_id`; the `run_id` of an item is that run). The metrics
are `reject_share` = rejected / (rejected + kept), `decided_share` = (rejected + kept) / proposed and `pending` = proposed -
rejected - kept; `proposed` is recorded when `ingest_find_duplicates` finds the image in the tier's review queue,
`rejected` / `kept` by the review's reject / keep decision (a reject also closes the image's open proposal in the other tier as
`rejected`, unless the owner kept it there; undoing a reject retracts its `rejected` in both tiers), and `ingest_promote`
records `kept` for promoted images that were proposed and never decided. `reject_share` is a share of decisions, not a
precision: a kept image can be the winner of a true duplicate group. The images of an aborted run stay pending.

The library process (`library_duplicates`, M2b) counts the owner's decisions on the `/duplicates` page, with events `confirmed`
and `dismissed` (no `proposed`, the page has no queue), one per image, attributed to the latest completed `clusterize` run at
decision time (`unattributed` when none exists yet). `confirmed` = the images flagged with reason `duplicate_review` plus
their keeper (`duplicateOf`); flags by the review agent or by `detect_file_duplicates` count for nothing. `dismissed` = every
member of a dismissed set. The metric is `confirm_share` = confirmed / (confirmed + dismissed). Unflagging retracts the loser's
`confirmed` (and the keeper's once no page flag points at it); undoing a dismissal retracts the `dismissed` of every image
left with no decision pair. An image counts once per event, under the run of its first decision. `confirm_share` is a share of
decided images, not a detector precision. A metrics failure never fails the flag or the dismissal (counts can then be one short).

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
