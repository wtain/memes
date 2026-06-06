
# Memes - AI-Powered Semantic Meme Search Engine

An intelligent system for organizing, searching, and understanding memes through AI-powered semantic analysis. Uses embeddings, OCR, LLM descriptions, and clustering to build a searchable knowledge base.

## Quick Start

### Prerequisites

- **Python**: 3.11
- **Node.js**: 18+
- **PostgreSQL**: 14+ with pgvector extension
- **Git**: For cloning and version control

### One-Command Setup (Docker)

```bash
# Clone the repository
git clone <repository-url>
cd memes

# Start all services (requires Docker & Docker Compose)
docker-compose up -d
```

Services available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8081/api
- **PostgreSQL**: localhost:5432

### Manual Setup (Windows/macOS/Linux)

For detailed setup instructions, see **[SETUP.md](./SETUP.md)**.

Quick steps:
```bash
# Backend
cd Backend
pip install -r requirements.txt
set WATCHFILES_FORCE_POLLING=1
uvicorn app.main:app --reload --env-file ../environments/.env.metal

# Frontend (new terminal)
cd Frontend/memes-frontend
npm install
npm run dev

# Batch processing
cd batch
pip install -r requirements.txt
python extract_text_from_memes.py  # Start with image registration
```

## What It Does

An application instance consists of:
- **Database**: PostgreSQL + pgvector for embeddings and similarity search
- **Backend**: FastAPI REST API serving meme data with search capabilities
- **Frontend**: React + Vite single-page application for browsing and searching
- **Batch Processing**: Python jobs that enrich images with OCR, embeddings, descriptions, and tags

### Architecture Overview

```
Filesystem (source images)
    ↓ (batches monitor & process)
    ↓
Database (PostgreSQL + pgvector)
    ↓ (Backend API)
    ↓
Frontend (React)
    ↓
User Interface
```

**Data Flow**:
1. **Batch jobs** scan filesystem, register new images, extract OCR text, generate CLIP embeddings
2. **Database** stores all metadata: images, embeddings, OCR text, tags, concepts, duplicates
3. **Backend API** provides search, filtering, similarity, and concept endpoints
4. **Frontend** offers user interface for browsing, searching, and managing memes

## Key Features

- **Semantic Search**: Find similar memes using CLIP embeddings (image + text similarity)
- **OCR Text Extraction**: Automatically detect text within images
- **AI Descriptions**: Generate descriptions using local Ollama LLM integration
- **Automatic Tagging**: Derive tags from OCR text and descriptions using rule engine
- **Duplicate Detection**: Find near-duplicate images using embedding clustering
- **Concept Organization**: Group related memes by semantic concepts
- **Multi-Environment Support**: Separate instances for metal memes, general memes, IT memes
- **Trend Analysis**: Track trends within specific domains

## What's Missing (Roadmap)

1. Caching (e.g. Redis for frequent queries)
2. Batch orchestration (Airflow or similar for scheduled processing)
3. Mobile clients (Android/iOS)
4. User upload flow
5. Agent/skill system for advanced querying
6. User authentication and multi-user support
7. Intelligent meme ingestion from unsorted image collections

## Project Structure

```
memes/
├── Backend/                 # FastAPI application
│   ├── app/                # Route handlers and endpoints
│   ├── tests/              # Unit and integration tests
│   ├── requirements-test.txt
│   └── pytest.ini
├── Frontend/memes-frontend/ # React + Vite web application
├── batch/                  # Data processing jobs
│   ├── extract_text_from_memes.py
│   ├── build_image_embeddings.py
│   ├── rebuild_duplicates.py
│   ├── clusterize.py
│   └── ... (more batch jobs)
├── Storage/               # Database models and migrations
│   ├── models.py          # SQLAlchemy ORM
│   └── alembic/           # Database migrations
├── repository/            # Data access layer (DAO pattern)
├── ai/                    # AI integrations (CLIP, Ollama, YOLOv8)
├── embeddingutils/        # Embedding utilities
├── shared/                # Shared utilities and schemas
├── environments/          # Environment-specific configs
│   ├── .env.metal
│   ├── .env.general
│   └── .env.it
├── documents/             # Project documentation
└── Readme.md
```

For detailed architectural overview, see **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

## Technology Stack

| Layer | Technologies |
|-------|--------------|
| **Backend** | FastAPI, SQLAlchemy, async/await, Pydantic |
| **Frontend** | React 19+, Vite, TypeScript, Tailwind CSS |
| **Database** | PostgreSQL 14+, pgvector extension, Alembic migrations |
| **AI/ML** | CLIP ViT-B-32 (embeddings), Ollama (LLM), EasyOCR, YOLOv8 |
| **Data Processing** | Pandas, NumPy, Scikit-learn, PyTorch |
| **Infrastructure** | Docker, Docker Compose, Python 3.10+ |

## Documentation

### User & Developer Guides
- **[SETUP.md](./SETUP.md)** - Installation and setup instructions (OS-specific)
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** - System design, data flow, and components
- **[backend_api.md](./backend_api.md)** - API endpoint documentation
- **[documents/system.md](./documents/system.md)** - System requirements and design considerations

### Operations & CI/CD
- **[CICD.md](./CICD.md)** - GitHub Actions pipelines, testing, Docker builds, and releases
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** - Contribution guidelines (coming soon)

## Database

Contains all persistent data about the system:
- **Images**: Image files with metadata and GUIDs
- **OCR Texts**: Extracted text from images (multi-language)
- **Embeddings**: CLIP vector embeddings for images and concepts
- **Tags**: Auto-generated and user-provided tags with categories
- **Concepts**: Semantic concepts (entities and abstractions)
- **Duplicate Clusters**: Groups of similar/duplicate images
- **Metrics**: Processing statistics and metadata

For schema details, see [ARCHITECTURE.md](./ARCHITECTURE.md#database-schema).

## Environments

The project supports three independent meme environments:

1. **Metal Memes** (metal music culture)
   - Port: 8081
   - Config: `environments/.env.metal`
   - Focus: Heavy metal, rock music, subcultures

2. **General Memes** (broad internet culture)
   - Port: 8082
   - Config: `environments/.env.general`
   - Focus: Wide variety of memes with some exclusions

3. **IT Memes** (software development culture)
   - Port: 8083
   - Config: `environments/.env.it`
   - Focus: Programming, DevOps, tech industry humor

Each environment has its own database, image directory, and configuration.

### Running Backend for Each Environment

**Windows**:
```cmd
# Metal environment (port 8081)
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0

# General environment (port 8082)
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082 --host 0.0.0.0

# IT environment (port 8083)
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.it --port 8083 --host 0.0.0.0
```

**macOS/Linux**:
```bash
# Metal environment (port 8081)
export WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0

# General environment (port 8082)
export WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082 --host 0.0.0.0

# IT environment (port 8083)
export WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.it --port 8083 --host 0.0.0.0
```

## Batch Processing Jobs (Current Setup)

**Recommended execution order**:

```
extract_text_from_memes → build_image_embeddings → rebuild_duplicates → clusterize
                       → build_tags_from_ocr
                       → build_image_descriptions → build_tags_from_descriptions

Optional data maintenance (run as needed):
  detect_file_duplicates
  deduplicate_ocr_texts
  move_excluded
  unregister_deleted_images
```

**Detailed job list**:

1. **extract_text_from_memes** - Image registration + OCR text extraction
   - Registers new image files in database with unique ID (GUID)
   - Extracts text using EasyOCR (EN, ES, RU languages)
   - Pretends to be incremental (can handle new images)

2. **build_image_embeddings** - Generate CLIP embeddings
   - Runs on all registered images
   - Generates 1536-dimensional CLIP ViT-B-32 vectors
   - Recalculates on each run (overwrites previous)

3. **rebuild_duplicates** - Build similarity-based clusters
   - Detects near-duplicate images based on embedding distance
   - Creates proximity relations between similar images
   - **Important**: Drops and recreates table on each run

4. **clusterize** - Optimize cluster queries
   - Joins embeddings into clusters for efficient backend queries
   - Builds vector indexes for fast similarity search

5. **build_tags_from_ocr** - Rule-based tag generation from OCR text
   - Applies pattern rules to extracted text
   - Generates categorical tags for navigation
   - Env vars: `RULES_FILE` (required), `RULES_LEMMATIZE` (default `false`), `RULES_FUZZY_THRESHOLD` (default `80`)

6. **build_image_descriptions** - Generate AI descriptions (requires Ollama)
   - Calls local Ollama LLM to generate image descriptions
   - Optional: can be disabled if Ollama not available
   - Takes 5-15 seconds per image

7. **build_tags_from_descriptions** - Rule-based tag generation from descriptions
   - Applies pattern rules to LLM descriptions
   - Fills in semantic understanding-based tags

8. **build_concept_embeddings** - Build semantic concept embeddings
   - Loads text and image concepts
   - Builds CLIP embeddings for concepts
   - For image-set concepts, calculates centroids
   - Runs reports on concept completeness

9. **load_images_from_internet** - Fetch images via SERP API
   - Uses SerpAPI to download images for specific concept queries
   - Results require human review before use

10. **trends_batch** - Analyze trends
    - Tracks trends within domains (e.g., metal groups, genres)
    - Environment-specific (metal, general, IT)

11. **move_excluded** - Archive excluded images
    - Checks images flagged as "excluded"
    - Moves them to separate "excluded" subdirectory

12. **unregister_deleted_images** - Clean up deleted images
    - Checks database against filesystem
    - Removes database records for missing/moved images

13. **detect_file_duplicates** - Detect and exclude byte-identical image files
    - Computes SHA-256 hash for every registered image (cached in `images.content_hash`)
    - Groups identical files into clusters using Union-Find
    - Confirms byte identity within each cluster before acting
    - Keeps the oldest image in each cluster; marks the rest as excluded
    - Idempotent: skips images with a cached hash; re-run safe

14. **deduplicate_ocr_texts** - Remove duplicate OCR text entries
    - Traverses all registered images in the database
    - Removes duplicate OCR rows per (image, language) where the same text was detected more than once
    - Keeps the highest-confidence occurrence; ties broken by oldest entry
    - Idempotent (safe to re-run)

15. **detect_entities_and_tag** - Detect animals and objects in images using YOLOv8
    - Runs YOLOv8 object detection on every registered image (skips `.webp`)
    - Tags detected animals under `key=animal` and objects under `key=object` (source: `YOLO`)
    - Wipes all `YOLO` tags before each run (full rebuild)
    - Requires `BASE_PATH` env var pointing to the image directory

16. **tag_images_from_concepts** - Assign concept-derived tags to top-matching images
    - Reads a JSON mapping file (`CONCEPT_MAPPING_FILE`) that maps concept names to `{key, value}` tag definitions
    - Iterates all concepts in the DB; concepts with no mapping entry are skipped (counted in metrics)
    - For each mapped concept, fetches top-matching images via cosine similarity on concept embeddings
    - Tags those images with the mapped `{key, value}` (source: `CONCEPT`); wipes all `CONCEPT` tags before each run
    - Configurable via env vars: `CONCEPT_MAPPING_FILE` (required), `CONCEPT_THRESHOLD` (default `0.2`), `CONCEPT_LIMIT` (default `50`)
    - Mapping format: `{"Concept Name": {"key": "genre", "value": "glam_metal", "threshold": 0.15, "limit": 30}, ...}` (`threshold` and `limit` are optional per-concept overrides)

17. **build_bow** - Build a bag-of-words frequency report from text data
    - Lemmatizes all words via pymorphy3 (same tokenization as the rules engine)
    - Source controlled by `TEXT_SOURCE` env var: `ocr` (default) or `descriptions`
    - OCR mode: output is split per detected language `{"ru": {"кот": 145, ...}, "en": {...}}`
    - Descriptions mode: flat `{"cat": 30, ...}` (no language field on descriptions)
    - Configurable via env vars: `BOW_OUTPUT_FILE` (required), `TEXT_SOURCE` (default `ocr`), `BOW_MIN_WORD_LENGTH` (default `3`), `BOW_MIN_FREQUENCY` (default `2`), `OCR_CONFIDENCE_MIN` (default `0.4`, OCR only)
    - Use the output to identify the most frequent lemmas not yet covered by rules

**Batch job notes**:
- Most jobs clear and rebuild all results (idempotent)
- Exception: `extract_text_from_memes` is pseudo-incremental
- `rebuild_duplicates` literally drops table and recreates with indexes
- `experimental/` package contains adhoc tools not in main pipeline

**Potential refactoring considerations**:
- `rebuild_duplicates` + `clusterize` => might be merged into a single batch
- `extract_text_from_memes` => could be split into image registration (also removing non-existing) and OCR

**For metal memes, can also source**:
- Lyrics from band metadata
- Album cover artwork

See **[SETUP.md](./SETUP.md#running-batch-jobs)** for detailed running instructions.

## Glossary

### Meme
An image containing humorous or cultural ideas. Memes often combine visual templates with cultural context, sometimes spanning multiple layers of irony.

### Entity
Real-world objects that appear in images (e.g., metal bands, celebrities).

### Concept
An abstract grouping of related entities (e.g., "Glam Metal" as a concept encompassing multiple bands).

### Tag
Human-readable categorization for navigation (e.g., `genre:metal`, `emotion:panic`).

### Embedding
Vector representation of an image/text in semantic space for similarity search.

## Underlying Mental Model

### Purposes of this System

1. **Identifying concepts**: Group memes by semantic meaning
2. **Relating concepts to tags**: Map concepts to navigable categories
3. **Similarity search and deduplication**: Find visually and semantically similar memes, detect near-duplicates
4. **Semantic search engine**: Enable natural language search over meme database
5. **Guardrails: community standards-awareness**: Respect community guidelines and content policies

## Testing

The core of this system is understanding semantics of memes—this requires careful validation.

### Running Tests

**Backend tests** (FastAPI endpoints):
```bash
cd Backend
pytest
```

**Rules engine tests** (unit tests, no DB required):
```bash
# from project root
pytest tests/rules/
```

Test data is externalised under `tests/rules/fixtures/`:
- `rules.json` — rule definitions used as test input; optional `_thresholds` dict sets per-rule fuzzy match thresholds
- `test_cases.json` — input texts with expected and unexpected tag assertions for `get_tags_for_text`, `get_tags_for_ocr_text`, and `get_tags_for_ocr_text_lemmatized`

**Lemmatized OCR matching** (`RULES_LEMMATIZE=true`): OCR text is word-tokenized and each token is reduced to its pymorphy3 normal form; rule keys are pre-lemmatized at engine init. Matching uses `rapidfuzz.fuzz.ratio` to tolerate OCR character errors. Requires `pymorphy3` and `rapidFuzz`; lemmatized tests are skipped automatically if either package is absent.

**Testing Strategy**: Under active development. Planned approaches:
- Integration tests for batch jobs
- Quality metrics (coverage, precision, concept coherence)
- Validation with manually-labeled test images
- Duplicate detection accuracy measurements

See [SETUP.md](./SETUP.md) for running tests:
```bash
cd Backend
pytest  # Run unit and integration tests
```

See [ARCHITECTURE.md](./ARCHITECTURE.md#quality-assurance) for detailed quality assurance strategy.

## Special Settings

### Windows

If you encounter certificate or file-watching issues:

```cmd
python -m pip install --upgrade pip
python -m pip install --upgrade certifi
python -m pip install --upgrade python-certifi-win32
```

For development, always set:
```cmd
set WATCHFILES_FORCE_POLLING=1
```

See [SETUP.md](./SETUP.md#windows) for full Windows setup guide.

## Contributing

We welcome contributions! Please see our guidelines:
- Fork the repository
- Follow the [ARCHITECTURE.md](./ARCHITECTURE.md) design patterns
- Add tests for new features
- Submit a pull request

## License

[Add your license here]

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

