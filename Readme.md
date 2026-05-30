
# Project Description

It's an AI-assisted semantic engine for memes.

# How it works

An application instance consist of database (postgres+pgvector, alembic), backend (FastAPI), frontend (vite+react+bootstrap), 
and batch processing.
Infrastructure: Docker+docker-compose

## Design

Filesystem <- (batches) <-> Database
                               ^ 
                               |
                              Backend
                                ^
                                |
                              Frontend

batches observe and traverse filesystem and sync it with the database, also working on refining the data in the database.
Backend serves data from the database to frontend.

What is missing now:
1. Caching (e.g. Redis under database for frequent queries)
2. Batches orchestration (e.g. Airflow or something simpler, or even self-crafted)
3. Android/iOS client
4. Upload flow
5. Agents/skills
6. User input (apart from upload flow and "excluded" flagging)
7. Ingestion: detect appropriate memes from an unsorted images collection

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
- environments
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
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0

set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082 --host 0.0.0.0

set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.it --port 8083 --host 0.0.0.0
```

# Frontend

Frontend uses npm+vite+bootstrap+react, lives in its folder.

# Batches (Current setup)

1. extract_text_from_memes (ingestion + OCR: files registered in database, OCR texts saved)
2. build_image_embeddings (CLIP embeddings: runs on registered files and recalculates embeddings)
3. rebuild_duplicates (based on embeddings, builds proximity relations)
4. clusterize (joins embeddings into clusters offline, so that backend can get them efficiently)
5. build_tags_from_ocr (builds tags from OCR texts)
6. build_concepts_embeddings (loads text and image concepts and builds CLIP embeddings, for image-set concepts builds centroids)
7. load_images_from_internet (loads images using SERP API)
8. trends_batch (builds trends for metal groups and genres)
9. build_image_descriptions (builds image Ollama descriptions)
10. build_tags_from_descriptions (builds tags from Ollama descriptions)
11. move_excluded (checks images for "excluded" flag and moves them into a separate subfolder "excluded" on the base path for images)
12. unregister_deleted_images (checks images in the database against base path and removes database records for the missing/moved images)

experimental package contains adhoc and in-development tools that are not used in main pipelines.

Batches usually clear all results and rebuild them, except of extract_text_from_memes which is pretending to be incremental.
rebuild_duplicates literally drops table and recreates it, then adds indexes required.

extract_text_from_memes -> build_image_embeddings -> rebuild_duplicates -> clusterize
                        -> build_tags_from_ocr
                        -> build_image_descriptions -> build_tags_from_descriptions

rebuild_duplicates + clusterize => might be a single batch
extract_text_from_memes => could be split into image registration (also removing non-existing) and OCR

For metal memes we can also source:
- Lyrics
- Album cover arts

# Environments

1. Metal memes (narrower scope for memes)
2. General memes (wide scope for memes, specific ones excluded, more diverse, but probably simples)
3. IT memes (IT-specific memes, could be easily identified and classified by keywords)

# Testing

The core of this system is "understanding" semantics of memes. testing strategy is still to be defined.

Initially it could be based on integration tests for the batches.

# Underlying mental model

## Glossary

### Meme

Meme is an image, that contains humorous ideas. It usually catches huge cultural context (or several contexts, e.g. a cartoon lore and office work).
Memes sometimes share templates, e.g. if a meme becomes popular it could be re-interpreted with the same template and additional context. It often goes into post-/meta-irony, which is multiple layers far from visual comprehension caught by CLIP and others.

### Entity

Something that exists in the real world, e.g. a metal band or a celebrity.

### Concept

An abstraction under entities. E.g., Poison and Cinderella would pertain to the same concept of Glam Metal (which doesn't have single visual representation).
Concepts could be organised into hierarchies, e.g. Glam Metal is a subset of Heavy Metal, and Heavy Metal is a subset of Music.

### Tags

Tags are artificial categories which help to divide memes into smaller and manageable subsets and enable navigation for a human.
Tags could be "genre:metal" or "lore:office".

## Purposes of this system

1. Identifying concepts
2. Relating concepts to tags
3. Similarity search and deduplication
4. Later: semantic search engine
5. Guardrails: community standards-awareness

