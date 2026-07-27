# Image description embeddings + description-based similarity

Status: done
Plan: docs/superpowers/plans/2026-07-16-image-description-embeddings-similarity.md
Originates from: docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md

## Context

`ImageDescriptionEmbedding` was created as a schema-only placeholder in the
multi-prompt image descriptions work
(`docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md`)
— nothing populates it yet. Meanwhile, the existing "similar images" feature
(`GET /api/images/{image_id}/similar`,
`Backend/app/repositories/image_repository.py:126`) ranks by cosine distance
over CLIP `Embedding` rows (one per image, `EMBEDDING_DIM=512`) — a visual
similarity signal.

This spec covers actually populating `ImageDescriptionEmbedding` with text
embeddings of each `ImageDescription` row, and adding a second similarity
mode that ranks by those embeddings instead — a semantic/textual signal
(same joke or meme format, different image) rather than a visual one.

Deliberately out of scope (per discussion): frontend/Android UI for
choosing or displaying the new mode. This spec covers the batch job, the
repository/query layer, and the API contract change only.

## Embedding generation

New batch script `batch/build_image_description_embeddings.py`, kept
separate from `batch/build_image_descriptions.py` — same reasoning as OCR
and CLIP image embeddings already being two separate batch jobs: different
concern (fast local text embedding vs. slow remote-LLM generation),
different failure mode, no reason to couple them.

Reuses `ai/sbert.py`'s existing `SbertModel` class as-is (it already takes
`model_name` as a constructor parameter) — no new class needed, just a
second instance pointed at a different model:

```python
embedder = SbertModel(model_name="BAAI/bge-large-en-v1.5")
```

`BAAI/bge-large-en-v1.5` was chosen over the existing multilingual
`paraphrase-multilingual-MiniLM-L12-v2` (used elsewhere in `ai/sbert.py`)
because descriptions are English-only (the LLM prompts are English) and a
dedicated English STS model outperforms a multilingual one on pure
text-to-text semantic similarity. It is 1024-dimensional, matching the
`TEXT_EMBEDDING_DIM=1024` already reserved in `Storage/models.py`.

New repository `repository/image_description_embeddings.py`
(`ImageDescriptionEmbeddingsRepository`):

```python
class ImageDescriptionEmbeddingsRepository:
    def __init__(self, session):
        self.session = session

    async def get_descriptions_without_embedding(self):
        """Returns (id, text) pairs for ImageDescription rows with no
        ImageDescriptionEmbedding yet."""
        has_embedding = select(ImageDescriptionEmbedding.image_description_id).distinct().scalar_subquery()
        result = await self.session.execute(
            select(ImageDescription.id, ImageDescription.text)
            .where(ImageDescription.id.not_in(has_embedding))
        )
        return result.all()

    def save(self, description_id, embedding: list[float]) -> None:
        self.session.add(ImageDescriptionEmbedding(
            image_description_id=description_id, embedding=embedding,
        ))

    async def delete_all(self) -> None:
        await self.session.execute(delete(ImageDescriptionEmbedding))
```

Batch script shape (mirrors `build_image_embeddings.py`'s simplicity, not
`build_image_descriptions.py`'s fill-missing-*pairs* complexity — this is
strictly 1:1, one embedding per description row, no per-prompt combinatorics
and no Ollama-style permanent-failure tracking needed, since local
sentence-transformer inference doesn't have the "some images permanently
fail" problem the LLM pipeline had):

```python
async def main(reset: bool):
    async with AsyncSessionLocal() as session:
        embeddings_repo = ImageDescriptionEmbeddingsRepository(session)
        if reset:
            print("Deleting all description embeddings...")
            await embeddings_repo.delete_all()
            await session.commit()

        rows = await embeddings_repo.get_descriptions_without_embedding()
        embedder = SbertModel(model_name="BAAI/bge-large-en-v1.5")

        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)
        for i, (description_id, text) in enumerate(rows):
            vector = embedder.embed_text(text)
            embeddings_repo.save(description_id, vector.tolist())
            tracker.mark_done()
            if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
                await session.commit()

        await session.commit()
    tracker.summary()
```

Reuses the shared `settings.GENERAL.BATCH_SIZE`/`PROGRESS_EVERY` (not a
dedicated config key like the LLM description pipeline needed) — local
embedding inference has none of the per-item latency/cost that motivated a
smaller, dedicated interval there.

## Repository/query design for description-based similarity

Added to the existing `ImageRepository`
(`Backend/app/repositories/image_repository.py`) — the established home for
every "rank images by embedding distance" query, alongside `get_similar`,
`get_duplicates`, `get_duplicates_precomputed`, `get_duplicates_clustered`.

**Same-prompt-only, best-match-across-shared-prompts:** comparisons are only
made between descriptions sharing the same `prompt_key` — comparing a
`general_description` embedding to a `humor_explanation` embedding would be
meaningless, since they're answers to different questions. When both images
have multiple prompts in common, the pair's distance is the *minimum* across
all shared prompt keys — two images only need one prompt key in common to
be comparable at all, and coverage doesn't need to match exactly between
source and candidate.

```python
async def get_similar_by_description(self, image_id: str, limit: int = 10):
    source_desc, source_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
    cand_desc, cand_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
    img, extras = aliased(Image), aliased(ImageExtras)

    result = await self.session.execute(
        select(
            cand_desc.image_id,
            func.min(source_emb.embedding.cosine_distance(cand_emb.embedding)).label("distance"),
            img.filename,
            extras.flagged,
        )
        .select_from(source_desc)
        .join(source_emb, source_emb.image_description_id == source_desc.id)
        .join(cand_desc, cand_desc.prompt_key == source_desc.prompt_key)
        .join(cand_emb, cand_emb.image_description_id == cand_desc.id)
        .join(img, img.id == cand_desc.image_id)
        .outerjoin(extras, extras.image_id == cand_desc.image_id)
        .where(source_desc.image_id == image_id, cand_desc.image_id != image_id)
        .group_by(cand_desc.image_id, img.filename, extras.flagged)
        .order_by("distance")
        .limit(limit)
    )
    return result.all()
```

**Why `LIMIT` correctly limits by image, not by prompt pair:** SQL evaluates
`GROUP BY` (and its aggregates) before `ORDER BY`/`LIMIT` — `GROUP BY
cand_desc.image_id` collapses the joined rowset (which can contain multiple
rows per candidate image, one per shared prompt key) down to exactly one row
per distinct candidate image *before* `ORDER BY`/`LIMIT` ever run. If the
source image has 2 prompts and 1,000 candidates share at least one of them,
the pre-aggregation join can produce up to 2,000 rows, but the query returns
at most 1,000 (one per candidate image) — `LIMIT 10` takes the top 10 of
those 1,000 image-rows, not 10 of the pre-grouped 2,000.

`img.filename` and `extras.flagged` are included in `GROUP BY` explicitly
(not relying on Postgres's primary-key functional-dependency exemption,
which only applies to a table's *own* declared primary key — `cand_desc`,
not `img`/`extras` — even though they're join-equal to it). Both are safe to
add: `ImageExtras.image_id` is itself a primary key (1:1 with `Image`, see
`Storage/models.py:305`), so grouping by it can't split a candidate's rows
further than grouping by `cand_desc.image_id` already does.

Existence check, used for the 404 contract below:

```python
async def has_description_embedding(self, image_id: str) -> bool:
    result = await self.session.execute(
        select(ImageDescriptionEmbedding.image_description_id)
        .join(ImageDescription, ImageDescription.id == ImageDescriptionEmbedding.image_description_id)
        .where(ImageDescription.image_id == image_id)
        .limit(1)
    )
    return result.first() is not None
```

## API layer

`GET /api/images/{image_id}/similar` gains `source: Literal["image",
"description"] = "image"` (default preserves current behavior for existing
clients — additive, not a breaking change).

`ImageService.get_similar` branches on `source`:

```python
async def get_similar(self, image_id: str, limit: int = 10, source: str = "image") -> MemeSearchResponse:
    if source == "description":
        if not await self.repo.has_description_embedding(image_id):
            raise HTTPException(status_code=404, detail="No description embedding found for this image")
        rows = await self.repo.get_similar_by_description(image_id, limit=limit)
    else:
        embedding = await self.repo.get_embedding(image_id)
        if embedding is None:
            raise HTTPException(status_code=404, detail="No embedding found for this image")
        rows = await self.repo.get_similar(image_id, embedding.tolist(), limit=limit)

    items = [
        Meme(
            id=str(iid), imageUrl=f"/api/images/{iid}", text=[], tags=[],
            originalFileName=fname, flagged=flagged if flagged is not None else False,
            cosineDistance=float(dist),
        )
        for iid, dist, fname, flagged in rows
    ]
    return MemeSearchResponse(items=items)
```

Both branches yield the same `(image_id, distance, filename, flagged)`-shaped
rows, so the `Meme`-building code is unchanged and shared. The response
schema (`Meme`/`MemeSearchResponse`, including `cosineDistance`) is
unchanged — the field is already generic; only what it measures differs per
mode. `router` layer (`Backend/app/api/images.py`) passes the new query
param through; `backend_api.md` is updated to document it.

**Mode-switch, not merge:** the two similarity signals are exposed as
alternate modes rather than blended into one ranked list. CLIP and text
embeddings live in unrelated vector spaces with incomparable raw distance
scales, so merging them soundly would require a score-fusion strategy (e.g.
reciprocal rank fusion) — real added complexity for a first version, and a
mode switch already handles partial description-embedding coverage
cleanly (an image simply isn't a candidate in `description` mode if it has
none, no special-casing needed). A blended/joined mode is possible future
work once real usage shows how the two compare qualitatively.

## Testing

- `tests/integration/test_image_description_embeddings_repository.py` (new)
  — `get_descriptions_without_embedding` (only returns descriptions lacking
  an embedding), `save`, `delete_all`.
- `tests/integration/test_backend_image_repository.py` (extend, following
  its existing `get_similar` test's style and `_unit_vector` helper) —
  `get_similar_by_description`: same-prompt-only matching (a shared-prompt
  candidate ranks by that prompt's distance; a candidate with only a
  non-shared prompt key is excluded entirely), best-match-across-shared-
  prompts (a candidate sharing 2 prompts is ranked by the closer of the
  two, not an average), self-exclusion, `flagged` passthrough. Also
  `has_description_embedding` (true/false cases).
- No dedicated test for `build_image_description_embeddings.py` itself,
  matching this repo's existing convention that thin batch-script
  orchestration (`build_image_embeddings.py` has none either) isn't
  separately tested — the logic that matters lives in, and is tested via,
  the repository.

## Future work (explicitly out of scope here)

- Frontend/Android UI for selecting or displaying the `source` mode.
- A blended/joined similarity mode (e.g. reciprocal rank fusion across both
  embedding spaces), once qualitative results from `description` mode alone
  are in.
