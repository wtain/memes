
# Project Description

It's an AI-assisted semantic engine for memes.

# How it works

An application instance consist of database (postgres+pgvector), backend (FastAPI), frontend (vite+react), 
and batch processing.

## Database

Contains data about images, their embeddings, OCR text and tags. Additionally, it contains information about duplicates clusters.

## Batch

Batch reads images from the filesystem directory associated with the application instance and registers images in the database. 

For the registered images it also detects OCR texts, embeddings and Ollama descriptions. Texts and descriptions are used to derive tags using rules provided. 
Embeddings are used to connect images to concepts. 
Concepts are defined as images and texts, both embedded in the same embeddings space.

There are additional utility batches which detect duplicates clusters based on embeddings distance between images, move "marked" images to a separate directory ijn the filesystem, unregister images which were removed from the main folder, and other scripts.

## Backend

Backend serves both images and enables search by text descriptions and tags. It also shows duplicates.

# Repository structure

Single repo:
- Backend
- batch (sourcing, ingestion, trends,...)
- documents
- Frontend
- shared
- Storage


# Special Settings

## On Windows

```commandline
python -m pip install --upgrade pip
python -m pip install --upgrade certifi


python -m pip install --upgrade python-certifi-win32
```

# Run backend

```commandline
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file Storage/.env.metal --port 8081 --host 0.0.0.0

set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file Storage/.env.general --port 8082 --host 0.0.0.0
```