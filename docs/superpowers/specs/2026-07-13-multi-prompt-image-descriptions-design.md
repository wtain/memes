# Multi-prompt, configurable-model image descriptions

Status: done
Plan: docs/superpowers/plans/2026-07-13-multi-prompt-image-descriptions.md
Follow-ups: docs/superpowers/specs/2026-07-15-image-description-failure-tracking-and-context-size.md, docs/superpowers/specs/2026-07-16-image-description-embeddings-similarity.md

## Context

`batch/build_image_descriptions.py` currently runs a single hard-coded Ollama
model (`llava`) with a single hard-coded prompt ("What is shown in this
image?") against every image, storing one row per image in
`ollama_description`. It commits once at the end of the run, so a crash loses
all progress, and it has no progress/ETA reporting (unlike the OCR batch,
which uses `ProgressTracker` + `BatchCommitter` + `SimpleMetricsListener`).

We want to:
- Run multiple prompts per image, each independently configurable to use a
  different Ollama model (e.g. `qwen2.5vl:7b` instead of `llava`).
- Track which prompt and which model produced each stored description.
- Commit incrementally (every `settings.GENERAL.BATCH_SIZE` images) instead
  of once at the end, and report progress/ETA the same way the OCR batch
  does.
- Lay groundwork (schema only) for embedding descriptions later, without
  committing to an embedding model now.

Explicitly out of scope for this spec (see "Future work"): the frontend
`prompt : result` table, actually populating description embeddings, and
using those embeddings to link images together.

## Data model

Rename `OllamaDescription` → `ImageDescription` (table `ollama_description` →
`image_descriptions`) since it's no longer single-model-specific, and extend
it to track which prompt and model produced each row:

```python
class ImageDescription(Base):
    __tablename__ = "image_descriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    prompt_key = Column(String, nullable=False)   # matches a key in the prompts config file
    model_used = Column(String, nullable=False)   # actual model that generated this text (audit trail)
    text = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id", "prompt_key", name="uq_image_description_image_prompt"),
    )

    image = relationship("Image", back_populates="descriptions")
    embedding = relationship(
        "ImageDescriptionEmbedding", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )


class ImageDescriptionEmbedding(Base):
    __tablename__ = "image_description_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        index=True, unique=True,
    )
    embedding = Column(Vector(TEXT_EMBEDDING_DIM), index=True)
    created_at = Column(DateTime, server_default=func.now())

    description = relationship("ImageDescription", back_populates="embedding")
```

`TEXT_EMBEDDING_DIM` is a new constant (alongside the existing
`EMBEDDING_DIM = 512` for CLIP), set provisionally to `1024` — pgvector
requires a concrete dimension at column-creation time even though nothing
populates this column yet. `1024` matches both models recommended in
"Future work" below (`BAAI/bge-large-en-v1.5`, `mxbai-embed-large`). If the
eventually-chosen model differs, a small follow-up migration adjusts the
dimension — safe since the table is unpopulated until then.

The `(image_id, prompt_key)` unique constraint is what makes "fill only
missing pairs" reruns possible: one row per image per configured prompt.
`ImageDescriptionEmbedding` is created by this migration but nothing
populates it yet.

## Prompts config

New tracked config file per environment, following the existing
`rules.file` / `concepts.text_concepts_file` pattern (a path key in
`settings.<env>.yaml` pointing at a separate file under `batch/data/`):

```yaml
# environments/settings.general.yaml
image_descriptions:
  prompts_file: data/image-description-prompts.general.yaml
  model: qwen2.5vl:7b   # global default model, used when a prompt entry has no override
```

```yaml
# batch/data/image-description-prompts.general.yaml
- key: general_description
  prompt: "What is shown in this image?"
- key: humor_explanation
  prompt: "Explain the joke or meme reference in this image, if any."
  model: llava   # per-prompt override
```

New module `ai/image_description_prompts.py`:

```python
@dataclass
class PromptConfig:
    key: str
    prompt: str
    model: str | None = None


def load_prompts(path: str) -> list[PromptConfig]:
    """Raises on duplicate keys within the file."""


def resolve_model(prompt: PromptConfig, settings) -> str:
    return prompt.model or settings.get("image_descriptions.model")
```

This mirrors `batch/trends/resolution.py`'s `resolve_model` exactly, just
keyed by prompt instead of by trend source. The loader raises on duplicate
`prompt_key`s within a file — a silent duplicate would make one prompt's rows
unreachable via the unique-constraint fill-missing check.

`OllamaImageDescriber.describe()` in `ai/ollama.py` changes from a
hard-coded model/prompt to `describe(path: str, prompt: str, model: str) ->
str`.

## Batch script flow

Replaces the current `build_image_descriptions.py` for-loop with the
fill-missing-pairs + batched-commit + progress pattern used by
`extract_text_from_memes.py`.

**Incremental logic (always on — no more all-or-nothing `--incremental`
toggle):**
- Load prompts from config.
- For each `prompt_key`, query the set of `image_id`s that already have a row
  for that key → `existing: dict[str, set[UUID]]`.
- For each image, compute `missing = [p for p in prompts if image.id not in
  existing[p.key]]`. Images with `missing == []` are skipped entirely and not
  counted in total work (matches OCR's `tracker.skip()`).
- New `--reset` flag: deletes all `ImageDescription` rows before running, for
  cases where prompt text/model changed and a full regeneration is wanted.
  Replaces the old `--incremental`-off ("delete all") behavior.

**Per image:** loop over `missing` prompts, call `describer.describe(path,
prompt.prompt, resolve_model(prompt, settings))`, save via repository. If a
single prompt's Ollama call fails, log the error, increment
`error.model`, and continue with the image's remaining prompts (partial
fill) — the failed `(image, prompt)` pair stays missing and is retried on the
next run. After all missing prompts for an image have been attempted
(succeeded or failed), call `tracker.mark_done()` once — one unit of tracked
progress per image, regardless of how many prompts it required, matching the
OCR convention.

**Commit/metrics reuse:**
- `BatchCommitter` gains `save_description(image, prompt_key, model_used,
  text)` and `on_image_done(image)` (commits every
  `settings.GENERAL.BATCH_SIZE`). No separate per-pipeline status table is
  needed — the unique constraint is itself the "already done" check.
- `ProgressTracker(total=<images with missing work>, report_every=
  settings.GENERAL.PROGRESS_EVERY)` for `[done/~total] elapsed / avg / eta`
  reporting.
- `SimpleMetricsListener` counters: `saved`, `error.model`, `skipped.webp`,
  `skipped.no_work`.

## Migration & rename surface

`OllamaDescription` → `ImageDescription` touches more than
`Storage/models.py`:

- `Storage/models.py` — class + table rename, add `prompt_key`,
  `model_used`, unique constraint, new `ImageDescriptionEmbedding` table.
- `repository/ollama_descriptions.py` → renamed to
  `repository/image_descriptions.py`, class → `ImageDescriptionsRepository`,
  `save()` takes `prompt_key`/`model_used`, plus a new query for "image_ids
  that already have this prompt_key".
- `repository/images.py` — rename `self.description = aliased(...)`, rename
  `get_images_and_ollama_descriptions[_without_tags]` →
  `get_images_and_descriptions[_without_tags]`. Drop
  `get_all_images_without_description` (only caller is the script being
  rewritten).
- `Backend/app/repositories/diagnostics_repository.py` — import/class rename
  only; the exposed field is already `with_descriptions`, so
  `backend_api.md` does not change.
- `batch/build_tags_from_descriptions.py`, `batch/build_bow.py` —
  import/call renames only. Side effect: these consume one description row
  per image today; after this ships they'll see up to N rows per image (one
  per prompt), increasing tag/BOW coverage per image. This is an accepted,
  desired consequence, not a regression to guard against.
- New Alembic migration (on top of, not editing,
  `1d0b68c811bc_added_ollama_descriptions_table.py`): rename table
  `ollama_description` → `image_descriptions`; add `prompt_key` (backfill
  existing rows with `'legacy'`) and `model_used` (backfill `'llava'`) as
  nullable, backfill, then set `NOT NULL`; add the unique constraint; create
  `image_description_embeddings`.
- `docs/schema.md` — update to reflect the renamed/extended table.

## Testing

- `batch/tests/` — new tests for `load_prompts()` (valid file, duplicate key
  raises, missing file) and `resolve_model()` (per-prompt override vs. global
  fallback), mirroring existing `batch/trends/resolution.py` test coverage.
- Repository-level test for the fill-missing-pairs query (e.g. 2 images × 2
  prompts, one pair already present → exactly 3 missing pairs returned).
- No Backend API test changes needed (no router changes).

## Future work (explicitly out of scope here)

- **Frontend table**: image detail page shows a `prompt : result` table —
  needs a new endpoint (or an extension of the existing image-detail
  endpoint) returning an image's `ImageDescription` rows.
- **Description embeddings**: populate `ImageDescriptionEmbedding` using an
  English STS-tuned text embedding model rather than CLIP's text tower —
  CLIP's text encoder is trained for image-text contrastive alignment, caps
  at 77 tokens, and generally underperforms dedicated text models on
  text-to-text semantic similarity, which is what this matching task is.
  Recommend `BAAI/bge-large-en-v1.5` or `mxbai-embed-large` via
  `sentence-transformers`, added alongside (not replacing) the existing
  multilingual `ai/sbert.py:SbertModel`. Requires choosing and setting
  `TEXT_EMBEDDING_DIM`.
- **Image-to-image linking via description embeddings**: a new batch/query
  step connecting images with semantically similar descriptions, built on
  top of the above.
