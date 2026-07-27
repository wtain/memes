# Spec: Image Upload Endpoint

Status: done

## Overview

`POST /api/uploads` — accepts one or more image files via multipart upload and saves them to
an `incoming/` staging directory for downstream batch processing (OCR, embeddings, tagging, DB insert).

## Rationale

The existing batch pipeline ingests images from a filesystem directory. This endpoint provides a
controlled intake point for frontend and mobile clients to submit new images without touching the
batch pipeline directly. Files land in `incoming/`; a separate batch job promotes them through the
full processing pipeline.

---

## Endpoint

```
POST /api/uploads
Content-Type: multipart/form-data
```

### Form fields

| Field   | Type               | Required | Description                          |
|---------|--------------------|----------|--------------------------------------|
| `files` | `List[UploadFile]` | Yes      | One or more image files to upload    |

### Constraints

| Rule                  | Value                                                         |
|-----------------------|---------------------------------------------------------------|
| Accepted MIME types   | `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `image/bmp`, `image/tiff` |
| Accepted extensions   | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tif`, `.tiff` |
| Max file size         | 20 MB per file                                                |
| Max files per request | 50                                                            |

---

## Storage

| Property       | Value                                                                              |
|----------------|------------------------------------------------------------------------------------|
| Directory      | `INCOMING_PATH` env var if set; otherwise `BASE_PATH / "incoming"`   |
| Created        | At first request (or server startup) if it doesn't exist — `mkdir(parents=True, exist_ok=True)` |
| Saved filename | `{uuid4}.{original_extension}` — no collisions; original name preserved in response |

---

## Response DTOs

### `UploadedFile`

```json
{
  "original_filename": "funny_cat.jpg",
  "saved_as": "a1b2c3d4-1234-5678-abcd-ef0123456789.jpg",
  "size_bytes": 204800,
  "content_type": "image/jpeg",
  "status": "ok"
}
```

### `FailedFile`

```json
{
  "original_filename": "document.pdf",
  "reason": "Unsupported file type: application/pdf"
}
```

### `UploadResponse` (root)

```json
{
  "uploaded": [ /* UploadedFile[] */ ],
  "failed":   [ /* FailedFile[]   */ ],
  "total_accepted": 3,
  "total_failed": 1
}
```

---

## JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UploadResponse",
  "type": "object",
  "required": ["uploaded", "failed", "total_accepted", "total_failed"],
  "properties": {
    "uploaded": {
      "type": "array",
      "items": { "$ref": "#/definitions/UploadedFile" }
    },
    "failed": {
      "type": "array",
      "items": { "$ref": "#/definitions/FailedFile" }
    },
    "total_accepted": { "type": "integer", "minimum": 0 },
    "total_failed":   { "type": "integer", "minimum": 0 }
  },
  "definitions": {
    "UploadedFile": {
      "type": "object",
      "required": ["original_filename", "saved_as", "size_bytes", "content_type", "status"],
      "properties": {
        "original_filename": { "type": "string" },
        "saved_as":          { "type": "string" },
        "size_bytes":        { "type": "integer", "minimum": 0 },
        "content_type":      { "type": "string" },
        "status":            { "type": "string", "enum": ["ok"] }
      }
    },
    "FailedFile": {
      "type": "object",
      "required": ["original_filename", "reason"],
      "properties": {
        "original_filename": { "type": "string" },
        "reason":            { "type": "string" }
      }
    }
  }
}
```

---

## Architecture

```
app/api/uploads.py          ← router, request validation, response construction
app/services/image_store.py ← add INCOMING_DIR alongside existing IMAGES_DIR
```

No repository layer — this endpoint is filesystem-only (no SQL).  
No service layer — logic is simple enough to live directly in the router per project conventions.

---

## HTTP Status Codes

| Code  | Condition                                         |
|-------|---------------------------------------------------|
| `200` | Request processed (check `failed` for per-file errors) |
| `422` | No files provided, or malformed multipart body    |
| `500` | Directory creation failed, or disk write error    |

Per-file type/size errors do **not** fail the entire request — they appear in the `failed` array,
and accepted files are still saved. If every file fails, the response is still `200` with an empty
`uploaded` array.

---

## Validation rules (in order)

1. File count ≤ 50 (checked before reading any bytes; 422 if exceeded)
2. MIME type in allowlist (checked from `UploadFile.content_type`; added to `failed` if not)
3. Extension in allowlist (extracted from original filename; added to `failed` if not)
4. File size ≤ 20 MB (checked while streaming to disk; file is deleted if exceeded, added to `failed`)

---

## Notes

- No authentication — consistent with the rest of the API.
- No rate limiting — consistent with the rest of the API.
- Content-type is taken from the multipart header, not sniffed from magic bytes (internal tool).
- The `incoming/` directory is **not** served by the API — only `IMAGES_DIR` is.
- This endpoint does **not** insert anything into the database.