# System Architecture

Comprehensive guide to the Memes semantic search engine architecture, design decisions, and data flow.

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Core Components](#core-components)
4. [Data Flow](#data-flow)
5. [Technology Stack](#technology-stack)
6. [Data Models](#data-models)
7. [Processing Pipeline](#processing-pipeline)
8. [Database Schema](#database-schema)
9. [Batch Processing](#batch-processing)
10. [Quality Assurance](#quality-assurance)
11. [Performance Considerations](#performance-considerations)

---

## System Overview

The Memes system is a **semantic search engine for meme content** that combines multiple AI techniques to understand, organize, and search for memes.

### Core Concept

Instead of keyword-based search, the system understands memes through:
- **Visual Semantics**: CLIP embeddings for image-image and image-text similarity
- **Text Content**: OCR extraction for text detection within images
- **Semantic Understanding**: LLM-generated descriptions for conceptual matching
- **Automatic Categorization**: Rule-based and semantic tagging

### Design Philosophy

1. **Incremental Enrichment**: Raw images → registered → extracted → tagged → conceptualized
2. **Multiple Signal Paths**: Combine OCR, embeddings, and descriptions for reliability
3. **Offline Processing**: Batch jobs compute heavy operations; API serves pre-computed results
4. **Environment Isolation**: Support multiple independent instances (metal, general, IT)
5. **Precision over Recall**: Non-relevant results acceptable if majority are relevant

---

## Architecture Diagram

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        USER BROWSER                         │
│                                                              │
└────────────────────────┬──────────────────────────────────┘
                         │ HTTP/WebSocket
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (React)                         │
│  ├─ Search Interface                                       │
│  ├─ Image Browser                                          │
│  ├─ Similarity Viewer                                      │
│  └─ Tag Manager                                            │
│  Technology: React 19, Vite, Tailwind CSS, TypeScript     │
└────────────────────────┬──────────────────────────────────┘
                         │ REST API (JSON)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  BACKEND API (FastAPI)                      │
│  ├─ /api/images (search, similar, details)                │
│  ├─ /api/concepts (browse, associations)                  │
│  ├─ /api/tags (facets, aggregations)                      │
│  └─ Image file serving                                     │
│  Technology: FastAPI, async/await, Pydantic validation    │
└────────────────────────┬──────────────────────────────────┘
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
    ┌────────┐    ┌────────────┐    ┌──────────┐
    │Database│    │File Storage│    │AI Models │
    │Handler │    │            │    │(Cached)  │
    └────────┘    └────────────┘    └──────────┘
         │               │
         └───────┬───────┘
                 │ SQLAlchemy ORM
                 ▼
    ┌─────────────────────────┐
    │   PostgreSQL + pgvector │
    │  - Images & metadata    │
    │  - OCR text             │
    │  - Embeddings (vectors) │
    │  - Tags & concepts      │
    │  - Cluster relations    │
    └─────────────────────────┘
```

### Data Processing Pipeline

```
Filesystem (Image Files)
       │
       │ (batch/extract_text_from_memes.py)
       ├─→ Image Registration (GUID)
       ├─→ OCR Text Extraction (EasyOCR)
       │
       ▼
Database (Registered Images + OCR)
       │
       │ (batch/build_image_embeddings.py)
       ├─→ CLIP Embeddings Generation (ViT-B-32)
       │
       ▼
Database (Images + Embeddings)
       │
       │ (batch/rebuild_duplicates.py + clusterize.py)
       ├─→ Similarity Clustering (HDBSCAN)
       │
       ▼
Database (Images + Embeddings + Clusters)
       │
       ├─→ (batch/build_tags_from_ocr.py)
       │   └─→ Rule-based tagging from OCR
       │
       ├─→ (batch/build_image_descriptions.py)
       │   └─→ Ollama descriptions (if enabled)
       │
       └─→ (batch/build_tags_from_descriptions.py)
           └─→ Rule-based tagging from descriptions
       │
       ▼
Database (Fully Enriched Images)
       │
       │ (Backend API)
       │
       ▼
Frontend (Search & Browse)
```

---

## Core Components

### 1. Frontend (React Application)

**Location**: `Frontend/memes-frontend/`

**Purpose**: User interface for browsing and searching memes

**Key Features**:
- Image search with text query
- Similar image discovery
- Tag-based filtering with facets
- Concept browsing
- Duplicate image detection
- Image exclusion management

**Technology**:
- React 19+ (framework)
- Vite (build tool)
- Tailwind CSS (styling)
- TypeScript (type safety)
- React Router (navigation)

**Key Components**:
```
src/
├── components/          # Reusable React components
├── pages/               # Page-level components
├── hooks/               # Custom React hooks
├── services/            # API client layer
├── types/               # TypeScript types
└── styles/              # Tailwind CSS config
```

### 2. Backend API (FastAPI)

**Location**: `Backend/app/`

**Purpose**: RESTful API for data access and search

**Endpoints**:
- `GET /api/images` - Search with query, facets, pagination
- `GET /api/images/{id}` - Serve image file
- `GET /api/images/{id}/similar` - Find similar images
- `GET /api/concepts` - List concepts
- `GET /api/concepts/{id}/images` - Images for concept
- `PUT /api/images/{id}/mark_excluded` - Mark excluded

**Technology**:
- FastAPI 0.128+ (framework)
- SQLAlchemy 2.0+ (ORM, async)
- Pydantic (validation)
- asyncpg (async PostgreSQL)

**Structure**:
```
Backend/app/
├── main.py              # FastAPI app initialization
├── models/              # Response schemas (Pydantic)
├── routers/             # Endpoint handlers
├── dependencies.py      # Shared dependencies
└── config.py            # Configuration
```

### 3. Database Layer

**Technology**: PostgreSQL 14+ with pgvector extension

**Purpose**: Store all persistent data

**Key Tables**:
- `images` - Base image records with metadata
- `ocr_texts` - Extracted text from images
- `image_embeddings` - CLIP vector embeddings (1536-dim)
- `concept_embeddings` - Semantic concepts and their embeddings
- `tags` - Text tags applied to images
- `image_tags` - Junction table linking images to tags
- `duplicate_clusters` - Groups of similar/duplicate images
- `image_metrics` - Statistics and metadata

**Indexes**:
- Vector indexes (pgvector IVFFlat or HNSW) for similarity search
- B-tree indexes on frequently queried columns
- Composite indexes for common query patterns

### 4. Storage Layer

**Location**: Filesystem + `Storage/`

**Components**:
- `Storage/models.py` - SQLAlchemy ORM models
- `Storage/db.py` - Database connection management
- `Storage/alembic/` - Database migrations

**Design Pattern**: Repository pattern in `repository/` for data access

```python
# Example: repository/images.py
class ImageRepository:
    async def get_by_id(image_id: str) -> Image
    async def search(query: str, limit: int) -> List[Image]
    async def get_similar(image_id: str) -> List[Image]
```

### 5. Batch Processing System

**Location**: `batch/`

**Purpose**: Offline data enrichment and quality assurance

**Jobs**:
1. **extract_text_from_memes** - Image registration + OCR
2. **build_image_embeddings** - CLIP embeddings
3. **rebuild_duplicates** - Similarity clustering
4. **clusterize** - Optimize cluster storage
5. **build_tags_from_ocr** - Rule-based tagging
6. **build_image_descriptions** - LLM descriptions (Ollama)
7. **build_tags_from_descriptions** - Description-based tagging
8. **build_concept_embeddings** - Semantic concepts
9. **Utilities** - Move excluded, unregister deleted, trends analysis
10. **deduplicate_ocr_texts** - Remove duplicate OCR entries per image/language
11. **detect_file_duplicates** - Hash-based exact duplicate detection; marks duplicates as excluded

**Design**: Each job is independently runnable, idempotent where possible

### 6. AI/ML Integration

**Location**: `ai/`

**Components**:
- `ai/clip.py` - CLIP embeddings (OpenAI ViT-B-32)
- `ai/ollama.py` - Local LLM integration for descriptions
- `ai/yolo.py` - YOLOv8 object detection (experimental)

**Models**:
- **CLIP ViT-B-32**: 1536-dimensional embeddings
  - Trained on 400M image-text pairs
  - Excellent image-text alignment
  - Good generalization across domains

- **EasyOCR**: Multi-language text detection
  - Supports 80+ languages
  - Configurable for EN, ES, RU (current setup)

- **Ollama**: Local LLM for descriptions
  - Runs on-device (privacy preserving)
  - Optional: can be disabled for faster processing

### 7. Rule Engine

**Location**: `rules/`

**Purpose**: Deterministic tag derivation from text

**System**:
- JSON-based rule definitions in `batch/data/rules.json`
- Pattern matching on OCR and description text
- Tag generation with optional confidence scores

**Example**:
```json
{
  "rules": [
    {
      "pattern": "metallica|metal|rock",
      "tags": ["genre:metal", "music:rock"]
    }
  ]
}
```

### 8. Utility Modules

**embeddingutils/** - Vector operations
- Similarity computation
- Clustering utilities
- Centroid calculation

**graph/** - Graph algorithms
- Union-Find for cluster detection
- Component analysis

**shared/** - Common code
- Schemas (data validation)
- Utilities

**metrics/** - Monitoring
- Event listener for metrics collection

---

## Data Flow

### Search Request Flow

```
User enters query "metal band" in frontend
                │
                ▼
Frontend calls: GET /api/images?q=metal%20band&limit=20
                │
                ▼
Backend (FastAPI)
  1. Parse query parameters
  2. Call ImageRepository.search()
  3. Repository generates SQL with vector similarity
  4. PostgreSQL executes:
     - Full-text search on OCR text
     - Vector similarity on embeddings
     - Tag matching
     - Combine results with ranking
                │
                ▼
PostgreSQL
  1. Compute semantic similarity: ocr_text <-> "metal band"
  2. Rank by CLIP embedding distance
  3. Apply tag filters
  4. Return top 20 results
                │
                ▼
Backend formats response as JSON
  {
    "items": [
      {
        "id": "uuid-1",
        "imageUrl": "/api/images/uuid-1",
        "text": ["extracted text lines"],
        "tags": [{"name": "metal", "category": "genre"}]
      }
    ],
    "facets": [
      {"name": "tags", "buckets": [{"value": "metal", "count": 5}]}
    ],
    "nextCursor": "...",
    "hasNext": true
  }
                │
                ▼
Frontend receives JSON, renders image grid
```

### Batch Processing Flow

```
User places meme images in BASE_PATH directory
                │
                ▼
extract_text_from_memes.py (batch job 1)
  1. Scan BASE_PATH for new images
  2. Generate UUID for each
  3. Register in `images` table
  4. Run EasyOCR on each image
  5. Store text in `ocr_texts` table
                │
                ▼
build_image_embeddings.py (batch job 2)
  1. Load each registered image
  2. Generate CLIP embedding (1536 dims)
  3. Store in `image_embeddings` table
                │
                ▼
rebuild_duplicates.py (batch job 3)
  1. Compute pairwise similarities
  2. Identify near-duplicate clusters (threshold: 0.95+)
  3. Store in `duplicate_clusters` table
                │
                ▼
clusterize.py (batch job 4)
  1. Organize embeddings for fast similarity search
  2. Build vector indexes
                │
                ▼
Parallel tagging (batch jobs 5-7)
  ├─ build_tags_from_ocr.py
  │  Apply rules to OCR text → tags
  │
  ├─ build_image_descriptions.py
  │  Call Ollama → descriptions
  │
  └─ build_tags_from_descriptions.py
     Apply rules to descriptions → tags
                │
                ▼
All tags stored in `image_tags` table
                │
                ▼
Backend API can now serve fully enriched images
```

---

## Technology Stack

### Backend

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Framework** | FastAPI 0.128+ | REST API framework |
| **Async** | asyncio, asyncpg | Async database access |
| **ORM** | SQLAlchemy 2.0+ | Database abstraction |
| **Validation** | Pydantic 2.0+ | Request/response validation |
| **Database** | PostgreSQL 14+ | Primary data store |
| **Vector DB** | pgvector 0.4+ | Vector similarity search |
| **Server** | Uvicorn 0.40+ | ASGI server |

### Frontend

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Framework** | React 19+ | UI framework |
| **Build Tool** | Vite 7+ | Development and bundling |
| **Language** | TypeScript 5.9+ | Type safety |
| **Styling** | Tailwind CSS 4+ | Utility CSS |
| **Routing** | React Router 7+ | Client-side navigation |
| **State** | React hooks | State management |

### AI/ML

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Embeddings** | CLIP ViT-B-32 | Image-text semantic understanding |
| **OCR** | EasyOCR | Multi-language text detection |
| **LLM** | Ollama | Local image descriptions |
| **Object Detection** | YOLOv8 | Object identification (experimental) |
| **ML Framework** | PyTorch 2.5+ | Deep learning operations |

### Data Processing

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Data Analysis** | Pandas 2.3+ | Tabular data operations |
| **Numerical** | NumPy 2.2+ | Array operations |
| **ML** | Scikit-learn 1.8+ | Clustering, metrics |
| **Clustering** | HDBSCAN 0.8+ | Density-based clustering |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Containerization** | Docker | Application packaging |
| **Orchestration** | Docker Compose | Multi-container coordination |
| **Python** | 3.10+ | Runtime |
| **Database Migrations** | Alembic | Schema versioning |

---

## Data Models

### Core Entities

#### Image
```
{
  id: UUID                  # Unique identifier
  original_filename: str    # File name
  registered_at: DateTime   # Registration timestamp
  excluded: bool            # Exclusion flag
  width: int               # Image width
  height: int              # Image height
  file_size: int           # File size in bytes
}
```

#### OCR Text
```
{
  id: UUID
  image_id: UUID           # Foreign key to Image
  text: str                # Extracted text (all languages combined)
  language: str            # Detected language(s)
  confidence: float        # OCR confidence score
}
```

#### Image Embedding
```
{
  id: UUID
  image_id: UUID           # Foreign key to Image
  embedding: Vector(1536)  # CLIP embedding vector
  model: str              # Model name (e.g., "clip:vit-b-32")
  generated_at: DateTime
}
```

#### Tag
```
{
  id: UUID
  image_id: UUID           # Foreign key
  name: str                # Tag name (e.g., "metal")
  category: str            # Tag category (e.g., "genre")
  source: str              # Source ("ocr", "description", "user")
  confidence: float        # Tag confidence (0-1)
}
```

#### Concept
```
{
  id: UUID
  name: str                # Concept name
  description: str         # Human description
  embedding: Vector(1536)  # Semantic embedding
  type: str               # "text" or "image_set"
}
```

#### Image Concept
```
{
  image_id: UUID
  concept_id: UUID
  similarity: float        # Cosine similarity (0-1)
  rank: int               # Ranking within concept
}
```

---

## Processing Pipeline

### Initialization Pipeline (One-time)

```
1. extract_text_from_memes
   Input:  Filesystem images
   Output: Registered images + OCR text
   Time:   ~2-5 seconds per image

2. build_image_embeddings
   Input:  Registered images
   Output: CLIP embeddings (1536-dim vectors)
   Time:   ~0.5-2 seconds per image (CPU), faster with GPU

3. rebuild_duplicates
   Input:  Embeddings
   Output: Similarity-based clusters
   Time:   ~O(n²) but optimized, typically <1 minute for 1000 images

4. clusterize
   Input:  Duplicate clusters
   Output: Optimized index structure
   Time:   <1 minute

Total for 1000 images: ~30-60 minutes
```

### Enrichment Pipeline (After initialization)

```
5. build_tags_from_ocr
   Input:  OCR text + rule definitions
   Output: Tags derived from text
   Time:   ~10ms per image

6. build_image_descriptions
   Input:  Registered images
   Output: Ollama LLM descriptions
   Time:   ~5-15 seconds per image (local LLM)

7. build_tags_from_descriptions
   Input:  Descriptions + rules
   Output: Tags from descriptions
   Time:   ~10ms per image
```

### Quality Assurance Pipeline (Optional)

```
8. build_concept_embeddings
   Input:  Concept definitions (text + reference images)
   Output: Concept embeddings + mapping
   Time:   ~minutes depending on concept count

9. Trend analysis (metadata)
   Input:  Tags + temporal data
   Output: Trend statistics
```

---

## Database Schema

### High-Level Schema

```sql
-- Core tables
images
  ├─ id (UUID)
  ├─ original_filename
  ├─ registered_at
  ├─ excluded
  └─ dimensions, size

ocr_texts
  ├─ id (UUID)
  ├─ image_id (FK)
  ├─ text
  └─ confidence

image_embeddings
  ├─ id (UUID)
  ├─ image_id (FK)
  ├─ embedding (Vector 1536)
  └─ model

duplicate_clusters
  ├─ cluster_id (UUID)
  └─ members (image ids in cluster)

-- Tag system
tags
  ├─ id (UUID)
  ├─ name
  ├─ category
  └─ description

image_tags
  ├─ image_id (FK)
  ├─ tag_id (FK)
  ├─ source
  ├─ confidence
  └─ created_at

-- Concepts
concepts
  ├─ id (UUID)
  ├─ name
  ├─ description
  ├─ embedding (Vector 1536)
  └─ type

concept_images
  ├─ concept_id (FK)
  ├─ image_id (FK)
  └─ similarity
```

### Indexing Strategy

```sql
-- Vector similarity search
CREATE INDEX ON image_embeddings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);  -- Adjust based on dataset size

-- Full-text search on OCR
CREATE INDEX ON ocr_texts USING gin (to_tsvector('english', text));

-- Tag queries
CREATE INDEX ON image_tags (image_id, tag_id);
CREATE INDEX ON tags (name, category);

-- Temporal queries
CREATE INDEX ON images (registered_at DESC);
```

---

## Batch Processing

### Job Execution Model

- **Sequential**: One batch job runs at a time
- **Idempotent**: Most jobs can re-run without duplicating data
- **Exception**: `rebuild_duplicates` drops and recreates table
- **Resume-capable**: Can resume incomplete runs for long jobs

### Batch Job Template

```python
# Standard batch job structure
async def main():
    # 1. Initialize
    db = setup_database()
    config = load_config()

    # 2. Progress tracking
    total = count_items_to_process()

    # 3. Process with batching
    for batch in iter_in_batches(items, batch_size=32):
        results = await process_batch(batch)
        await db.save_results(results)
        log_progress(completed / total)

    # 4. Post-processing
    await rebuild_indexes()
    log_completion()

asyncio.run(main())
```

### Monitoring Batch Jobs

- **Logs**: Each job logs to stdout and optionally to files
- **Metrics**: Track items/sec, memory usage, estimated completion
- **Errors**: Batch jobs continue past individual failures but log them
- **Re-runs**: Can be safely re-run; will skip already-processed items

---

## Quality Assurance

### Testing Strategy

#### Test suites

| Suite | Location | Runner | Scope |
|-------|----------|--------|-------|
| **Backend** | `Backend/tests/` | `cd Backend && pytest` | FastAPI endpoints (mocked DB) |
| **Rules Engine** | `tests/rules/` | `pytest tests/rules/` | `RulesEngine` unit tests (no DB, no I/O) |

#### Rules Engine Tests

`tests/rules/test_engine.py` covers both public methods with parametrized cases:

- `TestGetTagsForText` — word-boundary regex matching (`\b…\b`, case-insensitive)
- `TestGetTagsForOCRText` — case-insensitive substring matching

Test data is fully externalised to `tests/rules/fixtures/`:

```
tests/rules/fixtures/
├── rules.json       # rule definitions used as test input
└── test_cases.json  # per-method cases: input + expected tags + unexpected tags
```

Each test case declares **positive signals** (tags that must appear) and **negative signals** (tags that must not appear). To add a scenario, append an entry to `test_cases.json` — no Python changes needed.

Behaviours covered: direct match, case-insensitive input, list-valued rules, single-hop and two-hop string reference chains, word-boundary blocking of partial matches (get_tags_for_text only), substring matching inside compound words (get_tags_for_ocr_text only), multi-rule input.

#### Planned additions

1. **Integration Tests**: Test batch jobs end-to-end
2. **Quality Metrics**:
   - **Coverage**: % of images with semantic metadata
   - **Precision**: Manual validation of tag accuracy
   - **Concept Coherence**: Embeddings similarity within concepts
   - **Duplicate Detection Accuracy**: False positive/negative rates
3. **Validation Examples**:
   - Selected images with ground-truth labels
   - Manual concept validation
   - Similarity search validation

### Quality Layers

```
Quality Path 1: OCR
├─ High precision (few errors)
├─ Low recall (may miss text)
└─ No hallucinations

Quality Path 2: Embeddings
├─ Good for similarity
├─ No hallucinations
└─ Detects visual similarity only

Quality Path 3: LLM Descriptions
├─ Captures semantic understanding
├─ Prone to hallucinations
└─ Rich but unreliable alone

Combined Result: Consensus across 3 paths → High confidence
```

---

## Performance Considerations

### Scalability Analysis

| Component | Current | Scaling Path |
|-----------|---------|--------------|
| **Images** | 1-10k | 100k+ with optimization |
| **Queries** | 100 QPS | 1000+ QPS with caching |
| **Embeddings** | In-memory | Vector DB (Pinecone/Weaviate) for >1M |
| **Batch Time** | Minutes-hours | Parallel processing, queue systems |

### Optimization Opportunities

1. **Caching Layer** (Redis)
   - Cache search results (30s TTL)
   - Cache image embeddings in memory
   - Cache concept associations

2. **Vector DB Specialized Systems**
   - Consider Pinecone, Weaviate, Milvus for scaling
   - pgvector suitable for <100k images

3. **Batch Job Optimization**
   - Parallelize across images
   - GPU acceleration for embeddings
   - Streaming processing for large datasets

4. **API Optimization**
   - Connection pooling (asyncpg)
   - Database query optimization
   - Pagination optimization
   - Response compression

### Benchmarks (Reference)

- Image registration: 2-5 sec/image
- CLIP embedding generation: 0.5-2 sec/image (CPU), 0.1-0.5 sec (GPU)
- ORC text extraction: 1-3 sec/image
- Duplicate detection: ~30-60 min for 1000 images
- Similarity search query: <100ms for 10k images

### Hardware Recommendations

**Minimum**:
- CPU: 4-core
- RAM: 8GB
- GPU: N/A (CPU-based, slow)

**Recommended**:
- CPU: 8+ core
- RAM: 16GB
- GPU: NVIDIA (CUDA 12.1+) for 5-10x speedup

**Production**:
- CPU: 16+ core
- RAM: 32GB+
- GPU: Multiple GPUs for parallel batch processing
- Storage: NVMe SSD for image I/O
- Database: Dedicated PostgreSQL server with index tuning

---

## Environment Isolation

Each environment runs independently:

```
Metal Environment
├─ Database: ocrdb_metal
├─ Images Path: /path/to/metal/memes
├─ Config: .env.metal
├─ Port: 8081
└─ Concepts: Metal-specific

General Environment
├─ Database: ocrdb_general
├─ Images Path: /path/to/general/memes
├─ Config: .env.general
├─ Port: 8082
└─ Concepts: General-purpose

IT Environment
├─ Database: ocrdb_it
├─ Images Path: /path/to/it/memes
├─ Config: .env.it
├─ Port: 8083
└─ Concepts: IT/DevOps specific
```

---

## Design Decisions & Rationale

### Why FastAPI?

- Async/await support (modern Python)
- Automatic OpenAPI documentation
- Pydantic validation (type-safe)
- Performance (near Node.js levels)
- Growing ecosystem

### Why PostgreSQL + pgvector?

- Relational data (images, tags, concepts)
- pgvector extension for embeddings
- ACID guarantees
- Full-text search capability
- Cost-effective (open-source)

### Why React + Vite?

- React: Rich ecosystem, large community
- Vite: Fast development experience, excellent HMR
- TypeScript: Catch bugs at compile time
- Tailwind: Rapid UI development

### Why CLIP for embeddings?

- Trained on 400M image-text pairs
- Good image-text alignment
- Generalizes well across domains
- Open-source and easy to use
- Smaller models (ViT-B-32) suitable for CPU

### Why Ollama for descriptions?

- Runs locally (privacy)
- No API costs
- Can be toggled off
- Good enough for tagging purposes

### Why Batch Processing?

- Decouples heavy computation from API
- Allows offline refinement
- Predictable resource usage
- Easier to scale horizontally

---

## DevOps & CI/CD

### Current Implementation

The project includes comprehensive GitHub Actions workflows:

**Testing Pipeline** (`backend-tests.yml`)
- Automated test execution on Python 3.10 and 3.11
- Runs on all PRs and pushes to main/develop
- 74 tests covering images and concepts endpoints
- Execution time: ~2 seconds

**Rules Engine Tests** (`pytest tests/rules/`)
- 15 parametrized unit tests, no external dependencies
- Covers `get_tags_for_text` and `get_tags_for_ocr_text`
- Test data driven from `tests/rules/fixtures/` JSON files

**Code Coverage** (`backend-coverage.yml`)
- Measures test coverage (target: ≥80%)
- Integration with Codecov
- Automatic PR comments with coverage reports
- HTML coverage reports preserved as artifacts

**Docker Build** (`backend-docker.yml`)
- Multi-stage Docker image building
- Automatic push to GitHub Container Registry
- Tag management (main, develop, release versions)
- Security scanning with Trivy
- Build cache optimization

**Release Management** (`release.yml`)
- Semantic versioning support (v1.0.0)
- Automatic changelog generation
- GitHub releases with Docker image references
- Supports pre-releases (alpha, beta)

See [CICD.md](./CICD.md) for complete DevOps documentation.

### Docker Image

Multi-stage Dockerfile optimized for production:
- Base: Python 3.11-slim (minimal image size)
- Build stage: Compile wheels, install build tools
- Final stage: Runtime only, non-root user
- Health checks included
- Security scanning enabled

### Deployment

Container-ready backend service:
```bash
docker pull ghcr.io/YOUR_ORG/memes/backend:main
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  ghcr.io/YOUR_ORG/memes/backend:main
```

---

## Future Improvements

1. **Caching Layer**: Redis for query results and embeddings
2. **Batch Orchestration**: Airflow or similar for scheduled runs
3. **Vector DB**: Move to specialized system for large-scale
4. **User Authentication**: Multi-user support with permissions
5. **Upload Flow**: User-uploaded images with moderation
6. **Mobile Apps**: Native Android/iOS clients
7. **Advanced Search**: Query language, boolean operations
8. **Real-time Updates**: WebSocket support for live search
9. **Monitoring**: Prometheus metrics, alerting
10. **Explainability**: Show why a result matched the query
11. **Frontend CI/CD**: React build, TypeScript checks
12. **Database Migrations**: Automated migration testing
13. **Performance Monitoring**: APM integration (DataDog, New Relic)
