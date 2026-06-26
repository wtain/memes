# Database Schema

Canonical source: `Storage/models.py`. This document describes each table's purpose, column invariants, and lifecycle. The Alembic migration history lives in `Storage/alembic/versions/`.

---

## Core image tables

### `images`
Primary record for every registered image file.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Generated at registration time |
| `filename` | String NOT NULL | Bare filename only (no path). Combined with `BASE_PATH` at runtime |
| `content_hash` | String nullable | SHA-256 of file bytes; populated by `detect_file_duplicates` |
| `width` | Integer nullable | Set during registration if readable |
| `height` | Integer nullable | Set during registration if readable |
| `created_at` | DateTime | Server default `now()` |

Indexes: composite `(created_at DESC, id DESC)` — used by all cursor-paginated queries.

### `image_extras`
Optional per-image flags. Separate table so that the `images` row stays small and extras are only joined when needed.

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID PK, FK → images | One row per image (at most) |
| `exclude` | Boolean nullable | `true` = excluded from search results and batch jobs |
| `remarks` | Text nullable | Free-text notes |

### `image_metrics`
Timing data from the OCR pipeline run. One row per image.

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID PK, FK → images | |
| `read_time_ms` | Numeric | File read duration |
| `preprocess_time_ms` | Numeric | Image preprocessing duration |
| `ocr_time_ms` | Numeric | EasyOCR duration |
| `total_time_ms` | Numeric | End-to-end pipeline duration |
| `created_at` | DateTime | |

### `image_processing_status`
Tracks whether a specific pipeline stage has run for an image. Used to make `extract_text_from_memes` skip already-processed images.

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID PK, FK → images | |
| `pipeline` | String PK | e.g. `"easyocr:en"`. Composite PK with image_id |
| `status` | String NOT NULL | `processing` \| `done` \| `failed` |
| `started_at` | DateTime nullable | |
| `finished_at` | DateTime nullable | |
| `error_message` | Text nullable | Populated on `failed` |

### `processing_errors`
Error log from batch pipelines. Multiple rows per image allowed (one per failure).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `stage` | String NOT NULL | `read` \| `preprocess` \| `ocr` \| `persist` |
| `message` | Text NOT NULL | |
| `created_at` | DateTime | |

---

## Text and embeddings

### `ocr_texts`
One row per detected text region per image. Multiple rows per image are normal (one per bounding box / language).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `text` | Text NOT NULL | Detected text content |
| `confidence` | Float | OCR confidence 0–1 |
| `bbox` | JSON nullable | Bounding polygon or `[x, y, w, h]` |
| `language` | String(8) | Default `"en"`. Values: `en`, `es`, `ru` |
| `created_at` | DateTime | |

### `ollama_description`
LLM-generated image descriptions. One row per image (re-run overwrites via batch clear).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `text` | Text NOT NULL | Full description text |
| `created_at` | DateTime | |

### `embeddings`
CLIP ViT-B-32 vector embeddings. One row per image; batch rebuild overwrites.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `embedding` | Vector(512) | 512-dimensional CLIP embedding |
| `created_at` | DateTime | |

---

## Tagging

### `tags`
Flat tag table — no separate tag catalogue. Each row is one `(key, value)` pair on one image.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `key` | String | Tag category, e.g. `genre`, `animal`, `object` |
| `value` | String | Tag value, e.g. `metal`, `cat` |
| `source` | String | Who created the tag: `OCR`, `DESCRIPTION`, `YOLO`, `CONCEPT`, `USER` |
| `created_at` | DateTime | |

Batch jobs that own a `source` wipe all their own tags before rebuilding (e.g. `build_tags_from_ocr` deletes `source=OCR` then re-inserts).

---

## Duplicate detection (temporary tables)

Both tables are **rebuild-on-demand** — they are dropped and recreated by batch jobs. Do not treat them as persistent storage or add application FK constraints pointing at them.

### `tmp_duplicates`
Pairwise cosine distances between all image embeddings. Created by `rebuild_duplicates.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK (ROW_NUMBER) | Sequential integer, not UUID |
| `image_id1` | UUID FK → images | |
| `image_id2` | UUID FK → images | |
| `distance` | Float NOT NULL | Cosine distance (0 = identical, 2 = maximally different) |
| `created_at` | DateTime | Copied from `image_id1.created_at` |

Indexes: `(id DESC, distance)`, `(distance)`, `(id DESC)`.

⚠️ `rebuild_duplicates.py` runs `DROP TABLE IF EXISTS tmp_duplicates` unconditionally. Any in-flight queries against this table will fail during a rebuild.

### `tmp_clusters`
Cluster membership derived from `tmp_duplicates`. Created by `clusterize.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `cluster_id` | Integer | Images in the same cluster share this value |
| `image_id` | UUID FK → images | |

Rebuilt by `clusterize.py` after each `rebuild_duplicates` run. Used by `GET /api/images/duplicates`.

---

## Concepts

### `concepts`
Top-level semantic concept (e.g. "Glam Metal", "Philosoraptor").

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String | |
| `embedding` | Vector(512) | **Deprecated** — centroid is now on `concept_image_sets` / `concept_text_sets` |

### `concept_image_sets`
A named group of reference images for a concept.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `concept_id` | Integer FK → concepts | |
| `name` | String | Set name |
| `directory` | String | Subdirectory under `CONCEPT_IMAGES_DIR` |
| `centroid_embedding` | Vector(512) | Mean embedding of all images in the set |

### `concept_images`
Individual reference images belonging to a `concept_image_set`.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `concept_image_set_id` | Integer FK → concept_image_sets | |
| `filename` | String NOT NULL | Filename within the set's directory |
| `embedding` | Vector(512) | CLIP embedding of this reference image |

### `concept_text_sets`
A named group of text phrases for a concept.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `concept_id` | Integer FK → concepts | |
| `name` | String | |
| `centroid_embedding` | Vector(512) | Mean embedding of all texts in the set |

### `concept_texts`
Individual text phrases belonging to a `concept_text_set`.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `concept_text_set_id` | Integer FK → concept_text_sets | |
| `name` | String | Short label |
| `text` | String NOT NULL | Full phrase used to generate the embedding |
| `embedding` | Vector(512) | CLIP text embedding |

---

## Search history

### `search_history`
One row per search request received by the backend.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `searched_at` | DateTime(tz) NOT NULL | |
| `query` | Text nullable | Free-text query string; null for tag-only searches |
| `client` | String(20) NOT NULL | `web`, `android`, `unknown` |
| `result_count` | Integer NOT NULL | |

Index: `(searched_at DESC)`.

### `search_history_tags`
Tags that were active as filters during a search.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `search_id` | UUID FK → search_history | CASCADE delete |
| `category` | String NOT NULL | Tag key |
| `value` | String NOT NULL | Tag value |

---

## Trends

### `feed_sources`
External sources scraped for trends data (e.g. chart pages, RSS feeds).

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK autoincrement | |
| `name` | String(255) NOT NULL | Display name |
| `url` | Text NOT NULL | URL of the feed |
| `selector` | Text NOT NULL | CSS/XPath selector used to extract entries |

### `trends_runs`
One row per execution of `trends_batch.py`.

| Column | Type | Notes |
|---|---|---|
| `run_id` | UUID PK | |
| `created_at` | DateTime(tz) NOT NULL | |
| `status` | String(20) NOT NULL | `started` \| `completed` \| `failed` |

### `trends_run_results`
Individual entries collected during a trends run.

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK autoincrement | |
| `run_id` | UUID FK → trends_runs | CASCADE delete |
| `source_id` | Integer FK → feed_sources | CASCADE delete |
| `label` | String(255) NOT NULL | Source-defined label (e.g. chart position) |
| `name` | String(255) NOT NULL | Entry name (e.g. band or album name) |
| `value` | Integer NOT NULL | Numeric metric (e.g. chart rank or play count) |

---

## Deprecation notes

- `concepts.embedding`: marked `# Deprecate` in `models.py`. The centroid embedding has moved to `concept_image_sets.centroid_embedding` and `concept_text_sets.centroid_embedding`. The column still exists in the DB but should not be written to by new code.
- `tmp_duplicates` and `tmp_clusters`: the `tmp_` prefix is intentional — these are transient materialized results, not persistent application data. Their structure may change without a migration.