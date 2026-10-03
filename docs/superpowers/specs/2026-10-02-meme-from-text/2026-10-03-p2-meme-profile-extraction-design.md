# P2: Meme profile extraction — Design

Status: done
Plan: docs/superpowers/plans/2026-10-03-p2-meme-profile-extraction.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md (project P2)
Follow-ups: none yet (P4 retrieval and P5 UI consume the tables this spec creates)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Contract inherited from P1:
`docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md` (decisions 1-8, the table design in
decision 7, and the "Notes for P2 and P3" in its outcome addendum).
Requirements covered: FR-7, FR-8, FR-9 (storage side), FR-10, FR-11 (repository side), FR-12, FR-23 (counts only), NFR-5, NFR-8, OQ-3, OQ-7.

User decisions for this project (2026-10-03): the extractor is **text-first with a vision fallback**; the model is chosen by a **pilot
with the user's ratings**; the work covers **`metal` and `general` only** (`it` reports "not configured" and exits cleanly); two jobs
(extraction, embeddings) mirror the existing description jobs; one structured-output call per image covers every axis; the read/edit API
and UI belong to P5.

## Problem

P1 fixed the taxonomy, the profile schemas and the table design, but no meme has a profile. Retrieval (P4) and the UI (P5) need every
active image to carry a validated profile (`situation`, `emotion`, `stance`, `topic`, `tone`, a "use when" sentence) and a situation
vector, produced offline by the local Ollama, kept fresh when inputs change, and never overwritten once a human has verified it.

## Findings that shape the design (verified 2026-10-03)

Live counts through the read-only role (`ocr_readonly`), queried by the controller:

| Env | Active images | With OCR text | With Ollama description | Notes, feedback |
|---|---|---|---|---|
| metal | 18,088 | 17,634 | 7,482 (7,482 rows) | none |
| general | 32,328 | 31,453 | 32,295 (42,918 rows, about 1.3 per image) | none |
| it | 1,422 | 1,417 | 0 | none |

- In `general`, 99.9% of images already have a visual description, so the picture's content already exists as text. A text model can
  read OCR plus descriptions at a fraction of the cost of running the vision model over every image again (32k images).
- In `metal`, 7,482 of 18,088 images have descriptions and 17,634 have OCR text, so the text path will often run on OCR alone. The
  per-image text quality varies widely, which is what the pilot measures.
- No description feedback and no notes exist yet, so rejection handling and note priority cannot be exercised on real data. They are
  covered by tests, and the design must still be right for when they appear.
- Ollama on the workstation is version 0.35.0 (structured outputs with JSON-schema-constrained decoding are supported). Installed
  models: `qwen2:latest`, `qwen2.5vl:7b`, `llava:latest`, and `bge-large` (embeddings, P1).
- The description job caps client concurrency at 2 requests (VRAM headroom, measured 2026-08-01 on an RTX 3060 12 GB) and uses
  `ImageProcessingStatus` rows (`pipeline = "image_description:<key>"`, status `failed` only) for permanent failures. P2 reuses both.
- Reusable pieces: `tracked_run`/`finish_existing_run`/`record_stats`, `batch_registry.yaml`, `description_pipeline` steps with
  `needs`, `description_not_rejected()`, the OCR confidence/language filter used by `build_tags_from_ocr` (`OCR.CONFIDENCE_MIN`,
  `OCR.LANG_SCORE_MIN`), `has_unsupported_image_extension`, `OllamaEmbedder`, `taxonomy.load/validate_profile/vocabulary_prompt`.

## Decisions

### 1. Two jobs, joined to `description_pipeline`

| Job | Registry key / run kind | Does |
|---|---|---|
| `batch.build_meme_profiles` | `build_meme_profiles` | Selects images needing a profile, builds the input bundle, calls the LLM, validates, writes `meme_profiles` and `meme_profile_values` |
| `batch.build_meme_profile_embeddings` | `build_meme_profile_embeddings` | Embeds `situation_text` into `meme_profile_situation_embeddings` |

Both are run-tracked, `--env`, `--reset`, `--limit`, `--retry-failed` (extraction only; embeddings has no failure table, a failed embed is
simply retried on the next run), manual-trigger only (not in `scheduler.jobs`), admin-triggerable from `/admin/batches` through
`environments/batch_registry.yaml`, and `main(trigger, run_id, ...)` follows the existing contract.

`description_pipeline` gains two steps after `description_tags`: `meme_profiles` (`needs=("describe",)`) and `meme_profile_embeddings`
(`needs=("meme_profiles",)`). The extraction step is capped by `meme_profiles.max_per_run` (unset means unlimited), like the describe
step. Active images only, so new images join after `ingest_promote` and after their descriptions exist, as for the description jobs.

Splitting extraction from embedding follows the repo's own split (descriptions vs description embeddings): a model change re-embeds
without re-running the LLM, and an embedding outage never blocks extraction.

### 2. Settings (`meme_profiles.*` in `environments/settings.yaml`, per-environment overrides allowed)

| Key | Meaning | Initial value |
|---|---|---|
| `text_model` | Ollama model for the text path | set by the pilot |
| `vision_model` | Ollama model for the vision fallback | `qwen2.5vl:7b` (pilot may change) |
| `num_ctx` | context size | 8192 |
| `batch_size` | commit every N images | 50 |
| `max_per_run` | optional cap per `description_pipeline` run | unset |
| `concurrent_requests` | simultaneous Ollama requests | 2 |
| `min_text_chars` | below this many characters of usable text the image takes the vision path | 40 |
| `max_note_chars`, `max_description_chars`, `max_ocr_chars` | per-source truncation | 600, 800, 800 |
| `seed` | fixed Ollama seed (determinism, NFR-3) | 42 |

`environments/settings.it.yaml` does not define `meme_profiles.text_model`; both jobs check for it first and, when absent, print
"meme_profiles is not configured for this environment" and finish the run successfully with zero work (`it` per the user decision).
Unlike `image_descriptions.prompts_file` (which raises), this is a clean exit so that an admin click or a pipeline step on `it` is not a failure.

### 3. Input bundle and fingerprint

For each active image the bundle is assembled in this fixed order:

1. **Note**: the human `description_notes.text` when non-empty (highest priority; the strongest signal).
2. **Descriptions**: every `image_descriptions` row that passes `description_not_rejected`, ordered by `prompt_key`.
3. **OCR text**: `ocr_texts.text` rows ordered by `id`, filtered by `OCR.CONFIDENCE_MIN` and `OCR.LANG_SCORE_MIN` the same way
   `build_tags_from_ocr` does, joined with single spaces.

Each source is truncated to its `max_*_chars` at a word boundary. The bundle is `{"note": str|None, "descriptions": [[prompt_key, text]],
"ocr": str|None}`. `inputs_fingerprint` is the sha256 hex of the canonical JSON of the bundle (sorted keys, no whitespace) plus
`images.content_hash or ""` (so a replaced file with the same text still re-extracts on the vision path). Versions are **not** part of the
fingerprint (P1 compares them separately).

Path choice: **text** when the total length of note, descriptions and OCR is at least `min_text_chars`; otherwise **vision**, where the
model also receives the image file. An image with an unsupported extension (`has_unsupported_image_extension`) on the vision path is
skipped silently every run (counted as `skipped.unsupported_ext`), exactly as `build_image_descriptions` does; it simply has no profile.
An empty bundle on the text path cannot occur (it would be vision by the rule above).

### 4. Selecting work (staleness, resumability)

Every run recomputes bundles and fingerprints for **all** active images in chunks of 1,000 (three queries per chunk, text only; about
50 MB for `general`, seconds against hours of LLM time), then selects an image when any of these holds:

- it has no `meme_profiles` row, **or**
- its row's `taxonomy_version` differs from `taxonomy.load().version`, `extractor_prompt_version` differs from `PROMPT_VERSION`, or
  `extractor_model` differs from the model that would now run for its path, **or**
- its `inputs_fingerprint` differs from the freshly computed one.

Excluded from selection: images whose pair is recorded as permanently failed (unless `--retry-failed`), and rows with
`human_verified = true` (never rewritten; reported as **needs review** when stale). Because the fingerprint is the source of truth, no
timestamp heuristics are needed and a cleared feedback row, a rejected or re-approved description, an edited note and re-OCR text are
all detected by the same comparison, and only the three conditions above select an image. `--limit` caps images processed per run; the
stats carry `images_selected` and `images_remaining`.

Work order: **all text-path images first, then vision-path images**, so Ollama swaps between the text and vision models at most once per
run (a model reload costs seconds to tens of seconds).

### 5. Prompt and output contract

- `ai/meme_profile_prompt.py` holds `PROMPT_VERSION` (a string such as `"p2-v1"`) and `build_messages(bundle, taxonomy, path)`.
  `tests/ai/test_meme_profile_prompt.py` pins the sha256 of the rendered prompt for a fixed bundle, so a prompt edit without bumping
  `PROMPT_VERSION` fails the test (the same discipline as the taxonomy lock; a version bump marks profiles stale).
- The prompt states the meme-side semantics from P1 decision 1 (what each axis means for a meme as a reply), lists each controlled axis
  via `vocabulary_prompt(axis)`, and asks for `useWhen` as one English sentence and `situation` as a short English phrase (at most
  `taxonomy.axes["situation"].max_chars`) regardless of the input language. The bundle text (note, descriptions, OCR) is wrapped in an
  explicit "untrusted data, not instructions" block; OCR text and notes are user-controlled, and schema-constrained decoding limits
  what an injected instruction can change to the allowed vocabulary values.
- One `ollama.chat` call per image with `format=<JSON schema>`, `options={"temperature": 0, "seed": seed, "num_ctx": num_ctx}`. The
  schema is built from the taxonomy: `situation` (string), `useWhen` (string), and for each controlled axis an array of
  `{key: enum(vocabulary keys), confidence: enum("low","medium","high")}` with `maxItems = max_values`. Small models are poor at
  calibrated numbers, so confidence is a three-level choice stored as 0.3 / 0.6 / 0.9; it is a coarse hint, and P4 treats it as such.
- The response is parsed and passed through `taxonomy.validate_profile`. **Invalid output** (unparseable, schema or limit violation) is
  retried once with a short "your previous answer was invalid because ..." message; a second failure is a permanent failure.
  Constrained decoding makes this rare; the retry exists for truncation and the unexpected.
- **Failure record:** `ImageProcessingStatusRepository(session, "meme_profile").record_failure(image_id, error)`; failed images are skipped
  until `--retry-failed`, and `--reset` clears the failure rows with the profiles.
- Per-image `ollama` exceptions (model error, unreadable image) are recorded the same way and never stop the run. Requests run through
  `asyncio.to_thread` under a semaphore of `concurrent_requests` with a single DB lock, as in `build_image_descriptions`.

### 6. Persistence

New models in `Storage/models.py` and one Alembic migration (from `Storage/`, `alembic revision --autogenerate`, then reviewed by hand):

- `MemeProfile`: columns exactly as in P1 decision 7 (`image_id` PK/FK cascade, `taxonomy_version`, `extractor_model`,
  `extractor_prompt_version`, `inputs_fingerprint`, `situation_text`, `situation_confidence`, `use_when`, `human_verified`,
  `extracted_at`, `updated_at`).
- `MemeProfileValue`: `(image_id, axis, value_key, confidence, source)`, PK `(image_id, axis, value_key)`, index `(axis, value_key)`,
  `CHECK axis IN ('emotion','stance','topic','tone')`.
- `MemeProfileSituationEmbedding`: `image_id` PK/FK, `embedding Vector(PROFILE_EMBEDDING_DIM)`, `model_name`, `model_digest`, `created_at`,
  HNSW `vector_cosine_ops` index, as `ix_image_description_embeddings_embedding`.

A new `repository/meme_profiles.py` (async, repository pattern, no commits inside, per CLAUDE.md):

| Method | Purpose |
|---|---|
| `save_profile(image_id, profile, meta)` | Replace one image's profile and its values in the caller's transaction; deletes the situation embedding row when `situation_text` changed; refuses (returns False) when the row is `human_verified` |
| `save_human_profile(image_id, profile, meta)` | Write values with `source='human'`, set `human_verified`, used later by P5's endpoint |
| `get_profiles_meta(image_ids)` | version, model, prompt version, fingerprint and `human_verified` for the selection step |
| `delete_model_generated()` | `--reset`: delete every row that is not `human_verified` |
| `coverage()` | counts: active images, profiled, stale (any of the three rules), needs review (stale and human-verified), failed |
| `get_profiles_needing_embedding(model_name, model_digest)` | profiles with `situation_text` and no embedding row, or one from a different model or digest |
| `save_situation_embedding(image_id, vector, model_name, model_digest)` | upsert |

A profile and its values are written in one transaction per image; commits are batched with a `DescriptionBatchCommitter`-style helper
(`meme_profiles.batch_size`). A crash loses at most one batch and the run resumes, because selection is derived from the data.

### 7. Embeddings job

Selects with `get_profiles_needing_embedding`, embeds `situation_text` in chunks through `OllamaEmbedder` (L2-normalised), and upserts.
It reads the configured model name once and its digest once per run (`OllamaEmbedder.model_digest()`, a clear error naming
`ollama pull` when absent). A row from a different model or digest is re-embedded, which implements P1's "reversible by re-embed".
Profiles with no `situation_text` get no vector (the profile is still searchable by its controlled axes). Stats: `images_selected`,
`embedded`, `skipped.no_situation`, `error.embed`.

### 8. Operations and visibility

- Run stats (both jobs): `images_selected`, `images_remaining`, `saved`, `error.model`, `error.invalid_output`, `skipped.unsupported_ext`,
  `skipped.failed`, `path.text`, `path.vision`, plus a `coverage` snapshot taken after the run. `/admin/batches` shows them with no new API.
- Docs: `docs/data-flow.md` section 4b rows change from "planned" to live and get their producer jobs; `CLAUDE.md` gets entries for both
  jobs in the batch list (kept in sync per its own rule); `docs/data-flow.md` section 4 mentions the two new `description_pipeline` steps.
- **Documented latency (from P1):** a description rejection changes the fingerprint, so the profile is re-extracted only on the next
  run and stays searchable until then (tags are deleted immediately; profiles are not).
- `backend_api.md` is not touched: P2 adds no endpoint. Reading and editing a profile over HTTP is P5's work.

### 9. Pilot (model choice, OQ-3), executed as the first plan task before the full run

1. Draw a stratified, fixed-seed sample of 40 active `general` images through the read-only role (controller only): 20 with OCR and
   descriptions, 10 with OCR only, 5 with descriptions only, 5 with no usable text (vision path).
2. Run candidate models over it with the production prompt and schema: text path candidates `qwen2:latest`, `qwen2.5vl:7b` (text only,
   no image) and one newer instruction-tuned ~7B text model pulled for the pilot (chosen in the plan; the user is asked before any
   download); vision path candidate `qwen2.5vl:7b` with the image.
3. Record per model: valid-output rate, share of keys inside the vocabulary (should be 100% with constrained decoding; this verifies
   the constraint works), run-to-run stability (two runs, same seed), seconds per image, VRAM behaviour at `concurrent_requests = 2`.
4. Produce a rating sheet (self-contained HTML, one row per image with a thumbnail, the inputs and each model's output side by side,
   model names shuffled and hidden) that exports the user's ratings as JSON (best / acceptable / poor per output). About 15 minutes of the
   user's time.
5. Decision rule: among models with a valid-output rate of at least 98%, take the highest mean rating (good = 2, acceptable = 1, poor = 0);
   ties go to the faster model. The winners become the `meme_profiles.text_model` and `vision_model` defaults, with the numbers, the sheet
   path and the rating summary recorded in this spec's addendum. P6's gold set re-checks the choice later (OQ-3 stays open there).
6. Use the measured seconds per image to size `max_per_run` so one `description_pipeline` slice finishes in under an hour, and to state
   a backlog estimate per environment in the addendum (NFR-5).

## Out of scope

- HTTP endpoints and UI for reading or editing a profile, facet counts and the admin coverage view (P5).
- Retrieval, scoring, fusion and filters (P4). Query parsing (P3). Gold set and metrics (P6).
- `it` (decision above). Chaining extraction into `ingest_auto_prep`: new images join after promotion, like descriptions.
- Per-axis prompts and an LLM re-rank of extractions. They become candidates only if the pilot or P6 shows the single-call prompt fails.
- Changing the taxonomy or the P1 table design (any change is a P1 follow-up).

## Testing

All under the existing roots, each run as its own `pytest` command (CLAUDE.md):

- `tests/ai/test_meme_profile_prompt.py` (no Ollama): prompt contains every vocabulary key and the untrusted-data wrapper, the pinned
  prompt hash, the schema built from the shipped taxonomy has the right enums and `maxItems`, and `confidence` maps low/medium/high to
  0.3/0.6/0.9.
- `tests/ai/test_meme_profile_extractor.py` (Ollama monkeypatched): valid response accepted; invalid then valid is retried once; invalid
  twice is a failure; the extractor passes `format`, `temperature 0` and the seed; the vision path passes the image.
- `batch/tests/test_build_meme_profiles_main.py` and `test_build_meme_profile_embeddings_main.py` (mocked repositories, as the existing
  `*_main` tests): bundle assembly order, fingerprint stability and change, rejected description changes the fingerprint, note priority,
  path choice by `min_text_chars`, text-before-vision ordering, `--limit`, `--reset`, `--retry-failed`, `skipped.unsupported_ext`,
  the "not configured" clean exit, the `description_pipeline` step wiring and `needs`.
- `tests/integration/test_meme_profiles_repository.py` against `ocrdb_test` (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`):
  `save_profile` replaces values atomically, refuses a human-verified row, drops the embedding when `situation_text` changes,
  `delete_model_generated` keeps human-verified rows, `coverage()` counts, `get_profiles_needing_embedding` for a model change, the
  cascade on image delete, the `axis` check constraint, and the migration upgrade/downgrade. Per CLAUDE.md the **whole**
  `tests/integration/` root is run before merging because the shared models file and a new migration change.
- A schema/model consistency test: the `axis` check-constraint values equal the taxonomy's controlled axes.

## Acceptance criteria

1. The migration applies cleanly to a scratch copy of each of `metal`, `general` and `it` and downgrades; the three models match the P1 table design.
2. `build_meme_profiles` and `build_meme_profile_embeddings` run on `general` in resumable slices, are run-tracked, appear in `/admin/batches`,
   honour `--limit`/`--reset`/`--retry-failed`, and `description_pipeline` runs both after the describe step.
3. A stale profile (taxonomy version, prompt version, model or fingerprint differs) is re-extracted; a `human_verified` profile is never
   rewritten and is reported as needs review; a rejected description re-extracts that image on the next run.
4. On `it` both jobs exit successfully with "not configured" and zero work.
5. The pilot ran, the user's ratings are recorded, the chosen models are the settings defaults, and measured throughput and a backlog
   estimate per environment are in the addendum.
6. `coverage()` numbers are available in the run stats; `docs/data-flow.md` and `CLAUDE.md` are updated; the full `tests/integration/` root, `batch/tests`,
   `tests/ai` and `Backend` suites pass.
7. P4 and P5 can start from this spec and the P1 spec alone (tables, repository API, selection and staleness semantics are unambiguous).

## Risks

| Risk | Mitigation |
|---|---|
| Text-only extraction from OCR alone is weak where descriptions are missing (`metal`) | The pilot stratifies by input richness, the rating sheet shows the inputs, low-signal images get `low` confidences. `min_text_chars` is tunable per environment |
| A small model labels everything "amusement/relate" (probe showed 86% / 46%) | Expected for a meme library; P4 weights by value frequency. The pilot sheet makes systematic bias visible |
| Constrained decoding is slow or unsupported on a model | The pilot measures it; a model that cannot do structured output is not a candidate |
| Model reloads between text and vision models dominate runtime | Text-path images first, then vision, at most one swap per run; `max_per_run` slices |
| A prompt change silently leaves old profiles in place | `PROMPT_VERSION` pinned by a hash test; a version bump marks profiles stale |
| Prompt injection through OCR text or notes | Untrusted-data wrapper plus schema-constrained output limits the effect to allowed vocabulary values |
| Migration against a live database (board task 143, no migration process for prod) | Apply to scratch copies first (acceptance 1); the tables are new and empty, the migration has no data rewrite |
| ~32k images at several seconds each is long on one GPU shared with the describe job | Per-run cap and resumable slices; the pipeline runs steps sequentially, so extraction never competes with describe inside one run |

## Plan outline (for writing-plans)

1. Models, migration and repository, with the integration tests (schema first, nothing else depends on the LLM).
2. Taxonomy-driven schema and prompt module, extractor class, their tests (Ollama mocked).
3. Bundle assembly and fingerprint, selection logic, the `build_meme_profiles` job and its main tests; registry entry; `it` clean exit.
4. Pilot: sample, runner, rating sheet; the user rates; decision recorded; settings defaults set (the user is asked before any model pull).
5. `build_meme_profile_embeddings` job, registry entry, tests.
6. `description_pipeline` steps and tests; docs (`data-flow.md`, `CLAUDE.md`); run on `general` in slices with measured throughput recorded in the addendum;
   full test roots; review report.

## Addendum: pilot result (2026-10-03)

Full numbers, caveats and the rating breakdown are in `docs/superpowers/reviews/2026-10-03-p2-pilot-review.md`.

- **Sample and method:** 40 `general` images (20 ocr+description, 10 ocr only, 5 description only, 5 vision path); candidates `qwen2:latest`, `qwen2.5vl:7b` (text only), `qwen2.5:7b-instruct` (pulled for the pilot with the user's approval) on the text path and `qwen2.5vl:7b` with the image on the vision path; production prompt and schema, `temperature 0`, `seed 42`, two runs each; blinded sheet rated by the user (best / acceptable / poor).
- **Result:** every text model produced valid output on 100% of runs; mean ratings (0-2): `qwen2.5:7b-instruct` 0.86, `qwen2:latest` 0.77, `qwen2.5vl:7b` text-only 0.66. **Decision: `meme_profiles.text_model` is `qwen2.5:7b-instruct`** for `metal` and `general` (set in their overlay files). **`vision_model` stays `qwen2.5vl:7b`**: it was the only vision candidate, and its 6-of-10 valid runs reflect two undecodable image files, not the model.
- **Finding for later projects:** quality is modest (the winner got 8 best, 14 acceptable, 13 poor of 35) and weakest on OCR-only images (mean 0.3-0.4 against about 1.0 with a description). `metal` has descriptions for only 7,482 of 18,088 active images, so running the description job there first would likely help its profiles more than a model change. P4 should treat profile values as a coarse, confidence-weighted signal; P6's gold set re-checks the model choice (OQ-3 stays open there).
- **Throughput (NFR-5):** per-call means include model-load time because the pilot alternated models per image; the winner's median is 2.0 s and its mean 3.6 s. Estimated backlog for all active images: `general` (32,328) about 13.5-24.2 h at concurrency 2 (18.0-32.3 h serial), `metal` (18,088) about 7.5-13.6 h at concurrency 2 (10.0-18.1 h serial). `meme_profiles.max_per_run` of about 1,000 would keep a `description_pipeline` slice under an hour at the pessimistic mean; the setting is left unset. Concurrency 2 and VRAM behaviour were not measured by the (serial) pilot; the first rollout slice measures them.
- **Decision-rule note:** the pilot tool keys vision runs separately (`<model>@vision`) because `qwen2.5vl:7b` is both a text candidate and the vision model, and it computes the text winner and the vision winner separately.

## Addendum: as-built, rollout and notes for P4 and P5 (2026-10-03)

### As built, deviating from the decisions above

- **Decision 5:** `PROMPT_VERSION` is derived automatically: `p2-` plus the first 10 hex characters of a sha256 over every static prompt part (system prompt, situation guide, user template, axis guide, confidence levels, axis-line template, axis-guide fallback, the data labels, the no-text text, the image line and the retry template). It replaces the pinned-hash test and the example `"p2-v1"`. Outside the hash: the response-schema structure, the extractor's parse rules (including the empty-answer rule) and the vocabulary rendering in `taxonomy/prompt.py` (covered by `taxonomy_version`).
- **Decision 3:** OCR lines are ordered by reading order (polygon corner, top to bottom then left to right; rows without a usable bbox after them in id order), not "by id" (ids are random UUIDs).
- **Decision 6:** the repository method is `get_meta` (the spec said `get_profiles_meta`); `save_profile` returns False for a human-verified row.
- `situation_confidence` is always NULL in P2 (the model is not asked for it); axis confidences are the coarse levels 0.3 / 0.6 / 0.9.
- **Decision 7:** the embeddings job never emits `skipped.no_situation` (selection already excludes profiles without a situation); its stats are `images_selected`, `images_remaining`, `embedded`, `error.embed`.
- **Decision 4 additions made after review:** vision-path images with an unsupported extension are dropped from the work list before `--limit` is applied (counted `skipped.unsupported_ext`). An all-empty answer (no situation, use-when or values) is a failed extraction (retried once, then recorded as failed). Every failure row now carries its context `[fingerprint(16 chars)|prompt version|model]` and is skipped only while that context is unchanged, so a new note, a new description, a prompt-version change or a model change makes the image eligible again automatically (legacy rows without a context are retried once; `--retry-failed` still overrides). Run-level errors (httpx transport errors, `ConnectionError`, an Ollama 404 model-missing response) are NOT recorded as failures, and three in a row stop the run with an error, so an outage cannot mark the backlog failed. This replaces the literal "failed images are skipped unless --retry-failed" in decision 4.
- The pilot tool keys vision runs separately (`<model>@vision`) and computes the text winner and the vision winner separately; the blinded sheet never shows error text.

### Rollout (general, 2026-10-03)

- The migration (`d5a9f3c1e7b2`) is applied to metal, general and it (tables empty, readable by the read-only role); both jobs exit cleanly on `it`.
- First slice on `general`: 200 images in 2m30s, about 0.75 s per image wall time at concurrency 2 with the model loaded (this replaces the serial pilot estimate). That is about 6.7 h for all of `general` (32,328 images) and 3.8 h for `metal` (18,088), roughly 4,800 images per hour. A `meme_profiles.max_per_run` of about 3,000 keeps a `description_pipeline` slice near 40 minutes (the pilot addendum's "about 1,000" is superseded). VRAM at concurrency 2 was not recorded (`ollama ps` was not captured).
- Result of the slice: 194 saved, 6 empty answers (3%) which are now recorded as failures, 4 unsupported-extension images skipped. Distribution measured on the first 200-profile slice (before the empty-answer fix, so 6 of those 200 were empty and the shares are of 200): stance `mock` 70%, tone `absurd` 52% and `sarcastic` 30%, emotion `amusement` 26%, topic `work` 31%.
- The embeddings job embedded 193 situations (`bge-large`, digest `b3d71c92...`), a second run did nothing, and a nearest-neighbour query on the stored vectors returned sensible neighbours.
- By the user's decision the full backlog run and an end-to-end `description_pipeline` run were NOT done (wiring is covered by unit tests). The full run is left to be triggered later (admin button or CLI), preferably after P6's gold set shows whether the prompt needs tuning, because a prompt change marks every profile stale.
- Before the full run: confirm Ollama is up and both models are present (`qwen2.5:7b-instruct`, `qwen2.5vl:7b`), run `pip check` on `.venv311` (CLAUDE.md gotcha), run a `--limit` slice first and read its stats, remember `--reset` also clears every failure row, and on `metal` consider running `build_image_descriptions` first (OCR-only profiles are the weakest).

### Notes for P4

- Treat `situation_confidence` as absent and axis confidences as coarse levels.
- Weight by value frequency (the sample is dominated by `mock`, `absurd`, `sarcastic`).
- Images that failed (empty answers, undecodable files) have no profile.

### Notes for P5

- The backend cannot compute "stale" by itself yet: fingerprint and model staleness need `batch/utils/meme_profile_bundle.py`, which imports `rules.lang_plausibility` (it pulls in `wordfreq`, which is not in `Backend/requirements-backend.txt`). Options: move the bundle and fingerprint logic into a backend-importable module (`passes_language_filter` is four lines), or expose a stored stale set from the job.
- When P5 verifies a profile it must pass the existing row's meta (read it with `get_meta` first) to `save_human_profile`; a placeholder `inputs_fingerprint` or `extractor_model` would make every human-verified profile show as needing review at the next run.
- `save_profile` and `save_human_profile` ignore `ProfileMeta.human_verified` (the flag comes from which method is called).
