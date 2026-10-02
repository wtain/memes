# P1: Taxonomy and data model — Design

Status: done
Plan: docs/superpowers/plans/2026-10-02-p1-taxonomy-and-data-model.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md (project P1)
Follow-ups: none yet (P2 meme profile extraction and P3 query understanding consume this spec)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Governing ADR: `adr/ADR-002-axis-taxonomy-and-representation.md`.
Requirements covered: FR-9 (representation), FR-12, OQ-1, OQ-2, OQ-4, OQ-8. User decisions for this project (2026-10-02): the five draft axes
are confirmed; vocabularies are drafted by Claude and reviewed by the user; the embedding model is provisional, served through
Ollama, and verified later by P6.

## Problem

Nothing in the system represents what a meme means as a reply (when to use it, what it expresses). Tags are concept keys such as
`тема:кринж` or `животное:кот`, and descriptions are visual prose. Before P2 can extract meme profiles and P3 can parse queries, both
need one agreed schema, controlled vocabularies, storage, versioning rules and an embedding approach. P1 fixes all of these so the
two projects can run in parallel against the same contract.

## Findings that shape the design (verified 2026-10-02)

- Tracked tag vocabularies are per-environment YAML (`batch/data/tagging/{concepts,tags}.<profile>.yaml`), Russian-keyed, with
  categories `слово` (138), `тема` (108), `животное` (23), `месяц`, `день недели`. They are concept-level and not reusable as axes,
  but the `тема:` keys can seed the `topic` vocabulary.
- Existing text vectors: `ImageDescriptionEmbedding` and `DescriptionNoteEmbedding` are 1024-dim (`bge-large-en-v1.5`, via
  sentence-transformers), `OcrTextEmbedding` is 384-dim (multilingual MiniLM). All use a pgvector HNSW cosine index.
- **The backend cannot embed text.** `Backend/requirements-backend.txt` forbids ML libraries (torch and similar). All text vectors are
  computed in batch jobs. The new flow must embed the query's `situation` phrase at request time, so the embedding model is served by
  the local Ollama and called over HTTP. Ollama on the dev workstation has `qwen2.5vl:7b`, `qwen2` and `llava` and **no embedding
  model**, so one must be pulled.
- Shared JSON schemas live in `shared/schemas/*.schema.json`, aggregated by `all.schema.json`, with TS, Kotlin and Python types generated
  (`documents/generation.md`). Tables live only in `Storage/models.py` with Alembic migrations.
- Config paths of the form `data/...` resolve against cwd and break when run from the repo root (CLAUDE.md gotcha), so the taxonomy
  loader resolves paths relative to its own module.

## Decisions

### 1. Axes (confirmed)

`situation` (free text), `emotion`, `stance`, `topic`, `tone` (controlled vocabularies). Visual subject and template identity stay in
existing tags and CLIP. Content sensitivity is expressed as values of `tone` (`crude`, `explicit`, `dark`), with no separate axis,
and filtering by those values is a normal axis filter (closes OQ-8: the default shows everything the library shows today, and the UI
may offer a "hide" toggle later).

Axis semantics differ by side (asymmetry, see README glossary):

| Axis | Meme profile | Query profile |
|---|---|---|
| `situation` | Situation in which the meme is a fitting reply | What is happening in the incoming message |
| `emotion` | Emotion the meme expresses | Emotion the user wants to convey back |
| `stance` | Communicative act the meme performs | Kind of reply wanted |
| `topic` | Domain the meme belongs to | Domain of the message |
| `tone` | Register of the meme | Register wanted |

### 2. One shared taxonomy, not per-environment

Vocabulary files are common to all environments (the schema types and facets must be identical, and the three corpora are the same
kind of content). Environment-specific differences are limited to ranking weights and thresholds in `settings*.yaml` (P4).
This differs from the tagging vocabularies deliberately, and `it` is not excluded.

### 3. Vocabulary files

Location: `batch/data/taxonomy/` (one file per controlled axis plus `taxonomy.yaml`).

```
batch/data/taxonomy/
  taxonomy.yaml        # version, axis list, cardinality limits
  emotion.yaml
  stance.yaml
  topic.yaml
  tone.yaml
  similarity.json      # generated, see decision 6
```

`taxonomy.yaml`:
```yaml
version: 1                 # integer, bumped on any change to keys, definitions or axes
axes:
  situation: {type: free_text, max_chars: 160}
  emotion:   {type: vocabulary, max_values: 3}
  stance:    {type: vocabulary, max_values: 2}
  topic:     {type: vocabulary, max_values: 3}
  tone:      {type: vocabulary, max_values: 2}
```

Axis file (example, `stance.yaml`):
```yaml
values:
  mock:
    definition: "Ridicules or gently teases the situation or the person in it."
    labels: {en: Mock, ru: Подколоть, es: Burlarse}
  console:
    definition: "Offers comfort or sympathy to someone who is upset."
    labels: {en: Console, ru: Утешить, es: Consolar}
```

Rules, enforced by a lint (see Testing): keys are lowercase ASCII `snake_case` and unique within the axis, immutable once
released (rename is a delete plus an add with a version bump), every key has a non-empty English `definition` of at most 200
characters (definitions are embedded, see decision 6) and a label for `en`, `ru` and `es`. The `definition` is also the text shown to
the LLM as the choice list, so it is written as a plain usage description.

Initial sizes: `emotion` 26, `stance` 15, `tone` 10, `topic` 16. The draft values are in Appendix A and are reviewed by the user
before the files are committed.

Ownership of changes: a change to any vocabulary file must bump `version`. A committed `batch/data/taxonomy/taxonomy.lock`
(JSON: `version` and the sha256 of the five source files) is compared by a test with the current content. A content change with the same
`version`, or a changed `version` with a stale lock, fails with a message naming the one command that refreshes the lock
(`python -m taxonomy.lock`).

### 4. Loader package

A new top-level package `taxonomy/` (pure Python, depends on PyYAML only, no ML imports, so the backend can import it):
- `taxonomy.load()` returns an immutable `Taxonomy` (version, axes, values with definitions and labels, content hash).
- `taxonomy.validate_profile(profile_dict)` validates a profile (meme or query) against the loaded taxonomy: known keys only,
  cardinality limits, confidences in 0..1, `situation` length. Returns normalised data or a list of errors. P2 and P3 call it before
  storing or using LLM output.
- `taxonomy.vocabulary_prompt(axis)` renders the choice list (key plus definition) for prompts, so P2 and P3 use the same text.
- The vocabulary directory is resolved relative to the package, never to cwd, and the version comes only from `taxonomy.yaml`
  (no settings key, so all environments always agree).

Verify that PyYAML is in `Backend/requirements-backend.txt`'s closure; add it (a light, pure-Python package) if it is missing.

### 5. Profile JSON schema (shared)

New file `shared/schemas/memeprofile.schema.json` (camelCase, like the other shared schemas), added to `all.schema.json`, and types are
regenerated for TS, Kotlin and Python following `documents/generation.md` (CLAUDE.md warns that a schema change with no diff under
`Backend/app/types/generated/` means the step was skipped).

```json
{
  "$id": "memeprofile.schema.json",
  "title": "MemeProfile",
  "type": "object",
  "properties": {
    "taxonomyVersion": {"type": "integer"},
    "situation": {"type": ["string", "null"], "description": "Short English phrase"},
    "situationConfidence": {"type": ["number", "null"]},
    "emotion": {"type": "array", "items": {"$ref": "profilevalue.schema.json"}},
    "stance":  {"type": "array", "items": {"$ref": "profilevalue.schema.json"}},
    "topic":   {"type": "array", "items": {"$ref": "profilevalue.schema.json"}},
    "tone":    {"type": "array", "items": {"$ref": "profilevalue.schema.json"}},
    "useWhen": {"type": ["string", "null"], "description": "One English sentence, human-readable"},
    "humanVerified": {"type": "boolean"},
    "stale": {"type": "boolean"}
  },
  "required": ["taxonomyVersion", "emotion", "stance", "topic", "tone", "humanVerified", "stale"]
}
```
`profilevalue.schema.json`: `{key: string, confidence: number (0..1), source: "model" | "human"}`.

A second schema `queryprofile.schema.json` has the same axes (without `useWhen`, `humanVerified`, `stale`) plus `language`
(`en`/`ru`/`es`/`other`), `parserVersion` and `degraded` (boolean). It is used by P3 and P4.

Note for implementers (CLAUDE.md gotcha): if a router later hand-writes its own response model, it must declare these fields too, or
Pydantic silently drops them.

### 6. Embedding approach (provisional)

- **Model**: an English text-embedding model served by Ollama, called through the Ollama embedding API. Candidates, in order of
  preference: `bge-large` (same model family as the existing sentence-transformers vectors; expected 1024-dim), `mxbai-embed-large`
  (1024-dim). Step 1 of the plan pulls the first candidate, records its exact name, digest and output dimension, and the column
  dimension below is set from the measured value (`PROFILE_EMBEDDING_DIM`, not assumed).
- **Independence**: the new vectors do not share a table with the existing `bge-large-en-v1.5` vectors. Ollama's quantised build
  may differ numerically from sentence-transformers, so mixing is forbidden. All new vectors (meme situations, vocabulary
  definitions, query situation phrases) come from this one model.
- **Normalisation**: vectors are L2-normalised before storage and before query use. Search uses cosine distance with an HNSW index,
  like the other embedding tables.
- **Reversibility**: `model_name` and `model_digest` are stored per row. Changing the model means a new migration (new dimension if
  needed) and a full re-embed job. A mismatch between a row's model and the configured model makes the row stale for embedding
  purposes.
- **Config**: `taxonomy.embedding_model` (Ollama model name) in `environments/settings.yaml`, `ollama.base_url` reuse if
  one exists, else add one. A startup check in P3/P4 fails fast with a clear message when the model is missing from Ollama (NFR-4:
  this affects only the structured path, search stays up).
- **Backend dependency**: the backend calls Ollama's HTTP API, so it needs a light HTTP client (`httpx` if already in the
  closure, otherwise the `ollama` client package). No ML library enters `requirements-backend.txt`. The exact choice is made in P3 and
  noted there.
- **P6 override**: P6 compares this choice with a multilingual model on the gold set. If the verdict is "switch", the reversibility
  rules above apply. The ADR-002 text is updated by P6, not here.

### 7. Tables

All tables are created by one Alembic migration in P2 (first consumer), and models live in `Storage/models.py`. The design is fixed here so P2 and
P3/P4 agree. Foreign keys cascade on image delete, like the other enrichment tables.

`meme_profiles` (one row per image):

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID PK, FK `images.id` | |
| `taxonomy_version` | Integer, not null | version used at extraction |
| `extractor_model` | String, not null | Ollama model name used |
| `extractor_prompt_version` | String, not null | version id of the extraction prompt (P2) |
| `inputs_fingerprint` | String(64), not null | sha256 over the extraction inputs: ordered OCR text, ids and text of non-rejected Ollama descriptions, note text |
| `situation_text` | Text, null | English phrase |
| `situation_confidence` | Float, null | |
| `use_when` | Text, null | one English sentence |
| `human_verified` | Boolean, not null, default false | |
| `extracted_at` | DateTime, server default now | |
| `updated_at` | DateTime, not null, server default now | set on human edits too |

`meme_profile_values` (controlled axes, many per image):

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID, FK `meme_profiles.image_id` | |
| `axis` | String(16), not null | `emotion`, `stance`, `topic`, `tone`. A check constraint limits it to these |
| `value_key` | String(48), not null | vocabulary key. Not a foreign key (vocabulary lives in YAML), validated in code |
| `confidence` | Float, not null | 0..1 |
| `source` | String(8), not null | `model` or `human` |

Primary key `(image_id, axis, value_key)`. Index `(axis, value_key)` serves filters and facet counts. Rows with a key no longer in
the taxonomy are tolerated by readers (ignored), and are removed when the profile is re-extracted.

`meme_profile_situation_embeddings` (one per image):

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID PK, FK `meme_profiles.image_id` | |
| `embedding` | `Vector(PROFILE_EMBEDDING_DIM)` | normalised |
| `model_name`, `model_digest` | String, not null | per decision 6 |
| `created_at` | DateTime, server default now | |

HNSW index with `vector_cosine_ops`, like `ix_image_description_embeddings_embedding`.

**Staleness (derived, no stored flag).** A profile is stale if any holds: `taxonomy_version` differs from the loaded taxonomy's;
`extractor_prompt_version` differs from the configured current one; `inputs_fingerprint` differs from a freshly computed one (covers
new or changed notes, rejected or approved descriptions, re-OCR). A stale `human_verified` profile is never rewritten by a job. It
is reported as "needs review" (P2 and P5 define the surfacing). The same derivation is exposed as `MemeProfile.stale` in the API.

**Inputs and rejected descriptions (FR-10).** The fingerprint is computed only over non-rejected descriptions, so a feedback change
alters it and marks the profile stale. Unlike tags there is no immediate deletion: the stale profile keeps serving until it is
re-extracted. Consequence: after rejecting a description, a profile built from that description remains searchable until the next P2 run. This is
a known, accepted latency, documented in `docs/data-flow.md` by P2.

### 8. Vocabulary similarity artifact

For soft matching on controlled axes (ADR-003 scoring), `batch/build_taxonomy_similarity.py` embeds every vocabulary definition with the
model from decision 6, computes within-axis cosine similarity, and writes `batch/data/taxonomy/similarity.json`: for each key, the
neighbours above `taxonomy.similarity_threshold` (settings, initial 0.6, tuned in P4/P6) with their scores, plus `taxonomy_version`,
`model_name`, `model_digest` and the content hash of the vocabulary. The file is committed (small, human-reviewable, so a bad
neighbour pair is visible in a diff), and a test fails if its recorded hash or version no longer match the vocabulary files. A manual
override list in `taxonomy.yaml` (`similarity_overrides`) can add or remove pairs without re-running the model.
The backend reads the JSON, and never embeds definitions at request time.

### 9. Corpus validation of the vocabularies

Before the vocabularies are committed, check coverage on a sample, using the existing Ollama setup:
1. Draw a stratified sample of 100 active images from `general` (read-only DB role `DATABASE_URL_READONLY`, queries run by the
   controller, not by a subagent, per CLAUDE.md): mix of with/without OCR text, with/without Ollama description, text-heavy and
   image-only.
2. Have a local model (`qwen2.5vl:7b`) assign values from the draft vocabularies using a throwaway prompt (this is **not** the P2
   extractor, only a probe).
3. Report: share of memes with at least one value per axis, values never used (candidates to drop), values overused (candidates to
   split), and cases where the model wanted a value that does not exist (recorded as free-text suggestions).
4. The user reviews the report and the YAML, and the vocabularies are adjusted once. Further tuning is P6's job.

The sample check informs vocabulary content only. It does not substitute for the gold set and measures nothing about retrieval quality.

## Out of scope

- The extraction job, prompt and its Alembic migration (P2). Table design is fixed here, the migration is written with its first consumer.
- Query parsing and its prompt (P3). Retrieval, fusion and weights (P4). UI (P5). Gold set and metrics (P6).
- Choosing the final embedding model (P6 verifies).
- Localised labels beyond `en`, `ru`, `es`.

## Testing

All under the existing test roots, run separately per CLAUDE.md (no combined roots):
- `tests/taxonomy/` (new, pure unit, no DB): loader returns the expected structure, the vocabulary lint passes on the shipped files (keys
  unique, snake_case, definitions present and at most 200 characters, labels for en/ru/es, sizes within the documented ranges), `validate_profile` accepts
  valid and rejects unknown key, over-cardinality, out-of-range confidence, over-long situation, wrong axis type.
- Hash lock test: a change to vocabulary content without a `version` bump fails.
- Similarity artifact consistency test (hash and version match).
- Schema tests: `memeprofile.schema.json` and `queryprofile.schema.json` validate example fixtures. Generated type trees are in sync
  (`git diff` on generated types is empty after regeneration, as the CI gate checks for TS).
- The embedding probe (step 1 of the plan) is a script run once by hand and its output is recorded in this spec's addendum. It is not a CI test (needs Ollama).

## Acceptance criteria

1. `batch/data/taxonomy/` exists with `taxonomy.yaml` and the four vocabularies, reviewed and approved by the user.
2. The `taxonomy` package loads them with no ML imports (verified by importing it in a clean venv built from `Backend/requirements-backend.txt`).
3. `memeprofile` and `queryprofile` schemas exist, in `all.schema.json`, with TS, Kotlin and Python types regenerated and no CI diff.
4. The embedding model is pulled in Ollama, its name, digest and dimension are recorded in this spec's addendum, and `PROFILE_EMBEDDING_DIM`
   is defined from the measured value.
5. `similarity.json` is generated, reviewed for obviously wrong neighbours, and its consistency test passes.
6. The corpus sample report exists in `docs/superpowers/reviews/` and the vocabularies were adjusted once from it.
7. P2 and P3 can start from this spec alone: table design, schemas, loader API, versioning and staleness rules are complete and unambiguous.
8. `tracker.md` and ADR-002 are updated (P1 status, the Ollama-served embedding decision).

## Risks

| Risk | Mitigation |
|---|---|
| Ollama's embedding build gives poor vectors for short phrases | Step 1 probe with 10-20 hand-picked phrase pairs before committing. Fall back to `mxbai-embed-large`. P6 verifies on the gold set |
| Vocabulary too coarse or too fine | Sample check, the version bump process, and P6 results drive changes. Keys are immutable and renames are deliberate |
| Dimension mismatch if the model changes later | `model_name`/`model_digest` per row, a new migration and a full re-embed, documented in decision 6 |
| Backend gains a new runtime dependency on Ollama | Isolated to the structured path, NFR-4 fallback keeps search working, startup check reports clearly |
| Stale-but-served profile after a description rejection | Documented latency (decision 7). P2 may add a cheap "mark stale" hook if the user dislikes it |
| Lock file and hash discipline is annoying | The test message says exactly what to run to refresh the hash. Revisit if it causes friction |

## Plan outline (for writing-plans)

1. Embedding probe: pull the candidate model, record name/digest/dimension, run the phrase-pair sanity probe, set `PROFILE_EMBEDDING_DIM`.
2. Write `taxonomy/` package, `taxonomy.yaml`, draft vocabularies (Appendix A) and the lint tests (TDD).
3. Corpus sample check, report in `docs/superpowers/reviews/`, user review, adjust vocabularies, bump `version` as needed.
4. JSON schemas, `all.schema.json`, regenerate TS, Kotlin and Python types, verify no CI diff.
5. `build_taxonomy_similarity.py`, generate and review `similarity.json`, consistency test.
6. Documentation: `docs/data-flow.md` signal-matrix rows for the new signals (marked planned), `CLAUDE.md` batch list entry for the new script, update ADR-002 and `tracker.md`.

## Appendix A: draft vocabularies (for user review)

Keys only here. Definitions and RU/ES labels are written in the YAML files during step 2 and reviewed with them.

- **emotion (26)**: `joy`, `amusement`, `pride`, `relief`, `gratitude`, `love`, `excitement`, `smugness`, `hope`, `anger`, `frustration`, `annoyance`,
  `disgust`, `contempt`, `sadness`, `disappointment`, `resignation`, `exhaustion`, `anxiety`, `fear`, `embarrassment`, `confusion`,
  `shock`, `boredom`, `jealousy`, `indifference`
- **stance (15)**: `agree`, `disagree`, `refuse`, `mock`, `console`, `celebrate`, `congratulate`, `deflect`, `warn`, `complain`, `apologize`,
  `thank`, `brag`, `relate` (me too), `demand`
- **tone (10)**: `wholesome`, `sarcastic`, `absurd`, `deadpan`, `dramatic`, `cynical`, `self_deprecating`, `dark`, `crude`, `explicit`
- **topic (16)**: `work`, `study`, `family`, `romance`, `friends`, `money`, `health`, `technology`, `gaming`, `food`, `sleep`, `politics`,
  `animals`, `travel`, `holidays`, `weather`. Step 3 cross-checks against the existing `тема:` tag keys and adds or merges where the library clearly needs it.

## Addendum: embedding probe result

Recorded 2026-10-02. Model `bge-large` (Ollama `bge-large:latest`), digest `b3d71c92805938e2c9d78e6f35e82d7cc1bd77e05b47dd2ab3016240f4021c15`, dimension `1024`, pairs passed `4/4`
(`python -m batch.tools.probe_embedding_model`; pair similarity 0.661-0.841 against closest-unrelated 0.436-0.579). `PROFILE_EMBEDDING_DIM = 1024` in `Storage/models.py`,
`taxonomy.embedding_model` in `environments/settings.yaml`.

## Addendum: outcome and deviations (2026-10-02)

- Vocabularies were adjusted once after the corpus coverage review (`docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md`): topic gained `music`, `religion`, `horror` (19 keys). Nothing else changed.
- Similarity (decision 8) deviates: embedding neighbours were noise at every usable threshold (examples: agree~disagree 0.70, joy~sadness 0.65, annoyance~jealousy 0.71, while joy~amusement was missing), so 25 curated pairs in `batch/data/taxonomy/similarity_overrides.yaml` replace them and the threshold is 0.9. The overrides file sits outside the taxonomy version and lock: editing it needs only a regenerated `similarity.json` (no version bump, no lock refresh, no re-extraction). Taxonomy version is 2.
- Generated Pydantic classes are named `Schema` in each module (repo convention). Backend code should import `memeprofile.Schema` etc., not the package-level `RootModel` wrappers.
- `tsc -b` for the regenerated TypeScript types is verified by the controller in the final verification.
