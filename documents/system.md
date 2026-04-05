# System Documentation

## Purpose

This system is introduced to build semantics of memes and track their relations.

It is represented as a knowledge base with memes, allowing fast search for memes by their semantics, as well as text detected and tags.

This could potentially serve multiple applications, apart from end-users who can search for memes or browse the catalogue. It could also be used for other images, not just memes.

We are less concerned about the precision, since non-relevant meme in the search results is not critical if the vast majority is relevant.

For PoC I have chosen memes about metal/music because:
1. Narrow lore (simpler than arbitrary images, or even arbitrary memes)
2. I already have a channel with the content (I already have memes library, some experience, opportunities to validate results)

## Description

### Entities

Image, meme, concept, template,...

#### Image

Sourced as a raw image (a file coming from Google Drive mounted locally, for production setup will be a separate volume). When registered in the database it gets its unique ID (GUID).

#### Meme

Considered to be the same as image in this system. Meme consists of:
1. Template
2. Lore
3. Entities (e.g. persons)

#### Concept

An abstraction that joins multiple entities.
Examples: Metal music

#### Entity

Something that exists in the real world and could be found on pictures.
Examples: James Hetfield, Lars Ulrich, Metallica band

#### Template

A common template used to create different memes under different lores and subjects. 
Examples: Drake, Two buttons, Two paths

#### Lore

??? How do I define it?

## Architecture

The system currently consists of:
1. Storage - where source images are stored
2. Database - where the data extracted from the images lives - OCR texts, tags, and so on
3. Backend - Serves the data from the database and storage, enabling search
4. Frontend - represents memes catalogue with search functionality
5. Batch - multiple batches, that get images from the storage and derive meta information - OCR texts, semantics and tags

### Batches

There are several layers of batches and different paths to derive semantic data from the raw images:

1. Image registration - (extract_text_from_memes.py) runs on raw images, takes them from the storage and registers in the database
2. OCR text detection - (extract_text_from_memes.py) runs on the images registered in the database and detects texts in multiple languages (EN, ES, RU)
3. Concept images sourcing - (load_images_from_internet.py) Loads images using search engine (SerpAPI) to download images for specific concepts (queries). Results then to be reviewed by a human.
4. Embed images - (build_image_embeddings.py) Uses LLM to build images embeddings. Runs on registered images
5. Embed concepts - (build_concept_embeddings.py) Runs on both image and text concepts and builds their embeddings. Before the run it clears all the previous data. Also runs reports with completeness metrics for the concepts against the images library
6. Tags from texts - (build_tags_from_ocr.py) Runs simple text rules and derives tags from OCR texts detected
7. Describe images - (build_image_descriptions.py) Runs against all registered images and produces descriptions

#### Layers

1. Raw data
2. Registered data
3. Extracted: OCR texts, embeddings, descriptions
4. Derived: tags (currently: from OCR only)

#### Paths

1. OCR Path - no hallucinations, but low tolerance to detection errors (high precision, low recall)
2. Embeddings path - only detects visual similarity, not reliable, hallucinations
3. Descriptions - massive hallucinations

The idea is to join all 3 signals to build a reliable system

## Quality strategy

Quality tracking is a challenge as long as we can't have ground-truth.
We can track:
1. Coverage - how much of the whole images base is covered by semantic metadata (tags and concepts).
2. Specific examples as test library - selected images and manually assigned labels (tags/concepts). Can't guarantee complete coverage.
3. Concepts proximity - if some concepts intersect (e.g. visually similar) but are semantically distant

## Technologies used

1. Python 3 (backend and batches)
2. TypeScript+React+Tailwind+Vite (Frontend)
3. ML (Llava for image descriptions, EasyOCR, OpenAI CLIP ViT-B-32 for image and text embeddings)
4. Database: PostgreSQL+pgvector + Alembic
5. Schema: JsonSchema
6. Infrastructure: Docker+Docker Compose
7. FastAPI (backend)

## Ideas

1. The majority of data is derriven so we easily drop it upon rerun. We can store "negative" tags that were removed by a human user, and then reapply them when tags are recreated. It could also be helpful for negative prompts. Be careful with image id which could also be reassigned if we wipe all images.
2. A batch to check each image existence? and add/remove respectively
3. Batching for ollama - save every e.g. 10 image descriptions and enable resume