# ADR 2026-07-16: Multi-prompt image descriptions — schema shape and failure tracking

STATUS: ACCEPTED

Full design: `docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md`,
`docs/superpowers/specs/2026-07-15-image-description-failure-tracking-and-context-size.md`

## Context

`batch/build_image_descriptions.py` ran one hard-coded Ollama model against one
hard-coded prompt per image, storing one row per image (`OllamaDescription`,
one description ever, no way to add a second prompt without losing the
first). A real production smoke test against the `general` environment (22k
images) then surfaced two more problems: failed `(image, prompt)` pairs
retried forever on every subsequent run at real Ollama cost, and the model's
context window (4096 tokens) was too small for some images.

## Decisions

1. **`ImageDescription` gets a `(image_id, prompt_key)` unique constraint,
   not a rename-only migration.** `OllamaDescription` → `ImageDescription`
   (no longer single-model-specific) with new `prompt_key` and `model_used`
   columns (`Storage/models.py`) — one row per image *per configured
   prompt*, not one row per image. This is what makes an incremental rerun
   able to compute "which `(image, prompt)` pairs are still missing"
   (`_images_missing_prompts` in `batch/build_image_descriptions.py`) instead
   of a coarser "does this image have any description yet" check. Existing
   rows were backfilled with `prompt_key='legacy'`, `model_used='llava'`
   rather than dropped, so historical descriptions stay queryable.

2. **Prompts live in a per-environment tracked YAML file
   (`image_descriptions.prompts_file`), not a DB table.** A DB-backed
   `ImageDescriptionPrompt` entity was the original idea, but prompts are
   reviewed/edited content, not runtime state — tracked YAML matches this
   repo's existing convention (`rules.file`, `concepts.text_concepts_file`)
   and needs no migration to add a prompt. Model resolution
   (`ai/image_description_prompts.py:resolve_model`) is per-prompt-override
   falling back to a global `image_descriptions.model` default, mirroring
   `batch/trends/resolution.py`'s existing `resolve_model` pattern exactly.

3. **Failure tracking reuses the existing `ImageProcessingStatus` table**
   (already used by the OCR pipeline), keyed as
   `f"image_description:{prompt.key}"`, rather than new columns on
   `ImageDescription` or a new table. No schema migration was needed for
   this — only new `pipeline` string values are written into a
   pre-existing table. The two tables now have different semantics:
   `ImageDescription`'s unique constraint is the sole "succeeded" signal;
   `ImageProcessingStatus` is only ever read for "has this pair failed
   before" (never `"done"`/`"processing"` rows) — see the comment on
   `_status_repos` in `batch/build_image_descriptions.py`.

4. **Two new non-committing repository methods, not reuse of the existing
   `mark_failed`/`mark_started`.** `ImageProcessingStatusRepository.mark_failed`
   and `.mark_started` call `session.commit()` internally (fine for OCR,
   which has no batched-commit concern of its own). Reusing them as-is here
   would force an immediate commit on every single Ollama failure, breaking
   `DescriptionBatchCommitter`'s batch-size-based commit cadence and
   violating this repo's "repositories don't commit" convention. Instead,
   `record_failure`, `get_image_ids_with_status`, and `delete_all`
   (`repository/image_procesing_status.py`) were added alongside the
   existing methods — untouched, still used by OCR — so a recorded failure
   rides on the same session/commit timing as everything else the batch
   script writes.

5. **Never auto-retry a failed pair by default; `--retry-failed` is an
   explicit one-shot override, not a standing policy.** OCR's own
   `should_process()` treats `"failed"` the same as "not done" and retries
   it every run — copying that verbatim would not have fixed the reported
   problem (wasted cost re-attempting pairs that will never succeed as
   configured, e.g. a WebP file mislabeled `.jpg` that Ollama's backend can
   never decode). `_images_missing_prompts(..., retry_failed: bool = False)`
   excludes pairs with a recorded failure unless the caller explicitly asks
   to retry them for that one run; nothing is written anywhere to make
   `--retry-failed` change future default behavior.

6. **`image_descriptions.num_ctx` (default `8192`, global-only — no
   per-prompt override) and `image_descriptions.batch_size` (default `50`,
   deliberately smaller than the shared `settings.GENERAL.BATCH_SIZE` of
   `100`) are new dedicated config keys**, not reuse of existing generic
   ones. Real observed per-image Ollama latency (35–70s against
   `qwen2.5vl:7b`) means a crash near the end of a 100-image run can lose
   close to an hour of already-paid-for inference — this pipeline gets its
   own smaller commit interval instead of sharing OCR's. `num_ctx` is
   global rather than per-prompt because context needs are a function of
   image/prompt size, not which prompt is configured, and Ollama's default
   4096 was found too small for some real images (`"exceeds the available
   context size"` errors).

7. **`ImageDescriptionEmbedding` is a placeholder table, not yet
   populated.** `TEXT_EMBEDDING_DIM = 1024` was chosen to match two
   candidate future text-embedding models (`BAAI/bge-large-en-v1.5`,
   `mxbai-embed-large`) — CLIP's text tower was explicitly rejected for this
   future use (image-to-image similarity via description text) since it's
   trained for image-text contrastive alignment, caps at 77 tokens, and
   generally underperforms dedicated text models on text-to-text semantic
   similarity. The table's PK is `image_description_id` itself (1:1 with
   `ImageDescription`, matching the existing `ImageMetrics` pattern) — an
   earlier draft with a separate `id` PK plus a redundant unique index was
   caught in review and fixed before merge.

## Consequences

- Adding a new prompt to a `prompts_file` backfills only that prompt across
  existing images on the next run — no full reprocessing, no code change.
- A permanently-unreadable image (bad format, corrupted file) costs exactly
  one wasted Ollama call, ever, per prompt — not one per batch run.
- `build_tags_from_descriptions.py` and `build_bow.py` now see up to N
  description rows per image (one per configured prompt) instead of one —
  an accepted, desired increase in tag/BOW coverage, not a regression.
- `image_description_embeddings` exists in the schema but does nothing yet;
  a future spec must pick an actual model and a batch job to populate it
  before any image-similarity feature can use it.
