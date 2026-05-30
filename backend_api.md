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
  "tags": [MemeTag] | null
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

## API Endpoints

### Images

#### Search Images

Search for memes with optional query, facets, and pagination.

- **URL**: `/api/images`
- **Method**: `GET`
- **Query Parameters**:
  - `q` (optional): Search query string
  - `limit` (optional): Number of results (1-100, default: 20)
  - `facets` (optional): Facet filter string
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: 30 seconds
- **Example**: `GET /api/images?q=funny&limit=50`

#### Get Similar Images

Find images similar to a given image.

- **URL**: `/api/images/{image_id}/similar`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `MemeSearchResponse`
- **Example**: `GET /api/images/abc123/similar`

#### Get Meme Details

Retrieve detailed information about a specific meme.

- **URL**: `/api/images/meme/{image_id}`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `Meme`
- **Example**: `GET /api/images/meme/abc123`

#### Mark Meme as Excluded

Mark a meme as excluded from search results.

- **URL**: `/api/images/meme/{image_id}/mark_excluded`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content)
- **Example**: `PUT /api/images/meme/abc123/mark_excluded`

#### Unmark Meme as Excluded

Remove exclusion status from a meme.

- **URL**: `/api/images/meme/{image_id}/unmark_excluded`
- **Method**: `PUT`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: Success (no content)
- **Example**: `PUT /api/images/meme/abc123/unmark_excluded`

#### Get Exclusion Status

Check if a meme is excluded.

- **URL**: `/api/images/meme/{image_id}/get_excluded`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `integer` (1 if excluded, 0 if not)
- **Example**: `GET /api/images/meme/abc123/get_excluded`

#### Get Untagged Images

Retrieve images that don't have any tags.

- **URL**: `/api/images/untagged`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: 30 seconds
- **Example**: `GET /api/images/untagged?limit=30`

#### Get Duplicate Images

Find duplicate or near-duplicate images using clustering.

- **URL**: `/api/images/duplicates`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `threshold` (optional): Similarity threshold (0.0-1.0, default: 0.05)
  - `cursor` (optional): Pagination cursor for next page
- **Response**: `MemeSearchResponse`
- **Cache**: 30 seconds
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
