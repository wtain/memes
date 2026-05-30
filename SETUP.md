# Setup Guide

This guide covers installing and running the Memes semantic search engine on your system.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (Docker)](#quick-start-docker)
3. [Manual Setup](#manual-setup)
   - [Database Setup](#database-setup)
   - [Backend Setup](#backend-setup)
   - [Frontend Setup](#frontend-setup)
   - [Batch Jobs Setup](#batch-jobs-setup)
4. [Environment Configuration](#environment-configuration)
5. [Running Services](#running-services)
6. [Running Batch Jobs](#running-batch-jobs)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

- **CPU**: Quad-core or better (for batch processing)
- **RAM**: 8GB minimum (16GB recommended for CLIP and Ollama)
- **Disk**: 50GB+ (depends on image library size)
- **Network**: For downloading AI models on first run

### Required Software

- **Python**: 3.10 or 3.11 (3.11 recommended)
- **PostgreSQL**: 14 or newer with pgvector extension
- **Node.js**: 18 or newer (for frontend)
- **Git**: For cloning the repository
- **Docker** (optional): For containerized setup

### Optional Dependencies

- **Ollama**: For AI image descriptions (install from https://ollama.ai)
- **CUDA 12.1**: For GPU-accelerated embeddings (requires compatible NVIDIA GPU)

---

## Quick Start (Docker)

If you have Docker and Docker Compose installed, you can start all services at once:

```bash
# Clone repository
git clone <repository-url>
cd memes

# Start services (PostgreSQL, Backend, Frontend)
docker-compose up -d

# Wait for services to be ready (~30 seconds)
docker-compose logs -f

# Access services
# Frontend: http://localhost:5173
# Backend API: http://localhost:8081/api
# PostgreSQL: localhost:5432 (user: ocr, pass: ocr)
```

To stop services:
```bash
docker-compose down
```

---

## Manual Setup

### Database Setup

#### 1. Install PostgreSQL

**Windows**:
- Download from https://www.postgresql.org/download/windows/
- Install with default settings, remember the password for `postgres` user
- During installation, select pgvector extension if available

**macOS** (Homebrew):
```bash
brew install postgresql@14
brew services start postgresql@14
```

**Linux** (Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install postgresql-14 postgresql-contrib-14
sudo systemctl start postgresql
```

#### 2. Create Database and User

```bash
# Connect to PostgreSQL as admin
psql -U postgres

# Inside psql shell, run:
CREATE USER ocr WITH PASSWORD 'ocr';
CREATE DATABASE ocrdb OWNER ocr;
ALTER USER ocr CREATEDB;
\q  # Exit psql
```

#### 3. Install pgvector Extension

```bash
# Ubuntu/Debian
sudo apt-get install postgresql-14-pgvector

# macOS (if using Homebrew)
brew install pgvector

# Windows: Download pre-built binary from https://github.com/pgvector/pgvector
```

Then enable in database:
```bash
psql -U ocr -d ocrdb -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

#### 4. Initialize Database Schema

```bash
# From project root
cd Backend
python -m alembic upgrade head
cd ..
```

---

### Backend Setup

#### 1. Create Python Virtual Environment

**Windows**:
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**macOS/Linux**:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

#### 2. Install Dependencies

```bash
# Upgrade pip and tools
python -m pip install --upgrade pip setuptools wheel

# Windows-specific (optional, for certificate issues)
python -m pip install --upgrade certifi python-certifi-win32

# Install requirements
pip install -r requirements.txt
pip install -r Backend/requirements-test.txt  # For testing
```

#### 3. Configure Environment

Copy and edit environment file for your target environment:

```bash
# Choose one of: metal, general, or it
cp environments/.env.metal .env
```

Edit `.env` with your settings:
```env
DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb
BASE_PATH=/path/to/meme/images
FRONTEND_ORIGIN=http://localhost:5173
ALTERNATIVE_FRONTEND_ORIGIN=http://192.168.1.x:5173  # Optional
VITE_BACKEND_API_URL=http://localhost:8081
```

**Important**: Update `BASE_PATH` to your local meme images directory.

#### 4. Run Backend Server

**Windows**:
```cmd
set WATCHFILES_FORCE_POLLING=1
cd Backend
uvicorn app.main:app --reload --reload-dir app --env-file ..\.env --port 8081 --host 0.0.0.0
```

**macOS/Linux**:
```bash
export WATCHFILES_FORCE_POLLING=1
cd Backend
uvicorn app.main:app --reload --reload-dir app --env-file ../.env --port 8081 --host 0.0.0.0
```

Backend will be available at `http://localhost:8081`

API documentation available at `http://localhost:8081/docs` (Swagger UI)

---

### Frontend Setup

#### 1. Install Dependencies

```bash
cd Frontend/memes-frontend
npm install
# or using pnpm
pnpm install
```

#### 2. Configure Build

Environment-specific configuration is handled automatically. The frontend reads `VITE_BACKEND_API_URL` from:
- `.env.metal`, `.env.general`, or `.env.it`

Copy environment file:
```bash
cp ../../environments/.env.metal .env.local
```

#### 3. Run Development Server

```bash
# From Frontend/memes-frontend directory
npm run dev
# Output: VITE v7.x.x  ready in xxx ms
# ➜  Local:   http://localhost:5173/
```

**For different environments**:
- Metal memes: `npm run dev` (port 5173)
- General memes: `npm run dev-gen` (port 5174)
- IT memes: `npm run dev-it` (port 5175)

#### 4. Build for Production

```bash
npm run build
npm run preview
```

---

### Batch Jobs Setup

#### 1. Install Ollama (Optional, for descriptions)

Download from https://ollama.ai

```bash
# After installation, pull the model
ollama pull llava  # For image descriptions

# Start Ollama service (runs on localhost:11434)
ollama serve
```

#### 2. Configure Batch Environment

```bash
# Copy batch environment
cp environments/.env.metal batch/.env
```

Edit `batch/.env` with same DATABASE_URL and BASE_PATH as backend.

#### 3. Set Image Directory

Create your meme images directory:
```bash
mkdir -p /path/to/meme/images
# Copy meme images here
```

Update `BASE_PATH` in `.env` files to point to this directory.

---

## Environment Configuration

### Database URL Format

```
postgresql+asyncpg://username:password@host:port/database
```

Example:
```
postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb
```

### Environment Variables

**Required**:
- `DATABASE_URL`: PostgreSQL connection string
- `BASE_PATH`: Filesystem path to meme images directory

**Frontend**:
- `FRONTEND_ORIGIN`: Frontend URL for CORS (e.g., http://localhost:5173)
- `ALTERNATIVE_FRONTEND_ORIGIN`: Secondary frontend URL (optional)
- `VITE_BACKEND_API_URL`: Backend API URL (e.g., http://localhost:8081)

**Batch Jobs**:
- `RULES_FILE`: Path to tag rules JSON (default: `batch/data/rules.json`)
- `TEXT_CONCEPTS_FILE`: Text concepts file (environment-specific)
- `CONCEPT_IMAGES_DIR`: Directory for concept images

**Optional**:
- `WATCHFILES_FORCE_POLLING`: Set to `1` on Windows for file watching
- `OLLAMA_BASE_URL`: Ollama server URL (default: http://localhost:11434)

### Environment-Specific Configs

Each environment has its own `.env` file in `environments/`:

**Metal** (`.env.metal`)
- Focus: Heavy metal and music culture memes
- Database: `ocrdb_metal`

**General** (`.env.general`)
- Focus: Broad internet memes
- Database: `ocrdb_general`

**IT** (`.env.it`)
- Focus: Software development and tech memes
- Database: `ocrdb_it`

To switch environments, update which `.env` file you're using.

---

## Running Services

### Terminal 1: Database (if not using Docker)
```bash
# PostgreSQL should auto-start, verify:
psql -U ocr -d ocrdb -c "SELECT 1"
```

### Terminal 2: Backend
```bash
cd Backend
# Windows
set WATCHFILES_FORCE_POLLING=1
uvicorn app.main:app --reload --env-file ..\.env --port 8081 --host 0.0.0.0

# macOS/Linux
export WATCHFILES_FORCE_POLLING=1
uvicorn app.main:app --reload --env-file ../.env --port 8081 --host 0.0.0.0
```

### Terminal 3: Frontend
```bash
cd Frontend/memes-frontend
npm run dev
```

### Optional: Terminal 4: Ollama (for image descriptions)
```bash
ollama serve
```

All services should now be running:
- **Frontend**: http://localhost:5173
- **Backend**: http://localhost:8081
- **API Docs**: http://localhost:8081/docs

---

## Running Batch Jobs

### Initial Setup Pipeline

Run these jobs once to initialize your database:

```bash
# 1. Register images and extract OCR text
python batch/extract_text_from_memes.py

# 2. Generate CLIP embeddings
python batch/build_image_embeddings.py

# 3. Detect duplicate clusters
python batch/rebuild_duplicates.py

# 4. Optimize for queries
python batch/clusterize.py
```

### Tag Generation

After initial setup, generate tags:

```bash
# From OCR text
python batch/build_tags_from_ocr.py

# From AI descriptions (requires Ollama)
python batch/build_image_descriptions.py
python batch/build_tags_from_descriptions.py
```

### Periodic Batch Jobs

Run these periodically to keep data updated:

```bash
# Check for new/deleted images
python batch/extract_text_from_memes.py

# Update embeddings
python batch/build_image_embeddings.py

# Rebuild duplicate detection
python batch/rebuild_duplicates.py && python batch/clusterize.py
```

### Other Utilities

```bash
# Build concept embeddings
python batch/build_concept_embeddings.py

# Load images from internet (requires SERP API key)
python batch/load_images_from_internet.py

# Archive excluded images
python batch/move_excluded.py

# Clean up database for deleted files
python batch/unregister_deleted_images.py
```

---

## Troubleshooting

### PostgreSQL Connection Issues

**Error**: `psycopg2.OperationalError: could not connect to server`

Solution:
```bash
# Check PostgreSQL is running
# Windows: Look for PostgreSQL service in Services
# macOS: brew services list | grep postgresql
# Linux: sudo systemctl status postgresql

# Test connection
psql -U ocr -d ocrdb
```

**Error**: `pgvector extension not found`

Solution:
```bash
# Install pgvector extension
psql -U ocr -d ocrdb -c "CREATE EXTENSION vector;"
```

### Backend Startup Issues

**Error**: `ModuleNotFoundError: No module named...`

Solution:
```bash
# Activate virtual environment
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

**Error**: `WATCHFILES_FORCE_POLLING not set`

Solution (Windows only):
```cmd
set WATCHFILES_FORCE_POLLING=1
```

### Frontend Issues

**Error**: `Command not found: npm`

Solution:
```bash
# Install Node.js from https://nodejs.org/
# Verify installation
node --version
npm --version
```

**Error**: `Port 5173 already in use`

Solution:
```bash
# Use alternative port
npm run dev -- --port 5174
```

### Image Loading Issues

**Symptoms**: Images show as 404 or don't load

Solution:
1. Verify `BASE_PATH` points to correct directory
2. Check images exist in that directory
3. Ensure user running services has read permissions
4. Restart batch jobs: `python batch/extract_text_from_memes.py`

### Embedding Generation Issues

**Error**: `CUDA out of memory`

Solution:
```bash
# Use CPU instead (slower but works on any machine)
# Set in batch job before running:
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
```

### Ollama Connection Issues

**Error**: `Connection refused to localhost:11434`

Solution:
```bash
# Start Ollama in separate terminal
ollama serve

# Verify it's running
curl http://localhost:11434/api/tags
```

---

## Continuous Integration & Deployment

The project includes automated CI/CD pipelines using GitHub Actions:

- **Tests**: Automatically run on every push and pull request
- **Code Coverage**: Track coverage and identify gaps
- **Docker Builds**: Build and publish container images
- **Releases**: Automated release management with semantic versioning

See [CICD.md](./CICD.md) for complete CI/CD documentation including:
- Workflow descriptions
- Release process
- Docker image usage
- Troubleshooting

## Next Steps

1. **Load Images**: Copy meme images to your `BASE_PATH` directory
2. **Run Initial Batch**: Follow "Initial Setup Pipeline" above
3. **Browse Frontend**: Open http://localhost:5173
4. **Search**: Use the search bar to find memes by text, tags, or similarity
5. **Manage**: Mark unwanted images as excluded
6. **Monitor**: Check logs in Backend and Batch terminals for errors
7. **Learn CI/CD**: Review [CICD.md](./CICD.md) for automated testing and deployment

---

## Performance Tips

- **First batch run** will take time (downloading models, processing images)
- **Enable GPU**: Install CUDA and use GPU-accelerated PyTorch for 5-10x speedup
- **Increase workers**: Batch jobs support parallel processing
- **Monitor resources**: Watch RAM/CPU during embedding generation
- **Database indexes**: Already created by migrations; no action needed

---

## Getting Help

- Check logs in terminal where services are running
- Review `documents/system.md` for architecture details
- See `backend_api.md` for API documentation
- Open an issue on GitHub with error messages and logs
