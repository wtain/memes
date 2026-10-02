# Meme from text: requirements

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

Priority: **M** must for first release, **S** should, **C** could (later). Each requirement is traceable to a user story (`US-*`)
and to a project (`P*`, see `tracker.md`).

## Functional requirements

### Input and query understanding

| ID | Requirement | Pri | Stories | Project |
|---|---|---|---|---|
| FR-1 | The web UI offers a text box for free text up to a documented maximum length (initial proposal: 1,000 characters). Longer input is rejected with a clear message, not silently truncated | M | US-1 | P5 |
| FR-2 | Input may be in English, Russian or Spanish, or mixed. Language is detected, not chosen by the user | M | US-1, US-7 | P3 |
| FR-3 | The system turns the input into a query profile: a value (or empty) for each taxonomy axis, produced by the local LLM as schema-validated structured output | M | US-1 | P3 |
| FR-4 | Invalid or partial LLM output is repaired or degraded gracefully (retry once, then fall back to the embedding-only path of FR-9). The request never fails because the LLM misbehaved | M | US-6 | P3 |
| FR-5 | The user can see the parsed profile and edit it (change, add or remove axis values), then re-run the search without a new LLM call | S | US-3 | P5 |
| FR-6 | The user can express hard constraints from the UI, independent of the text: axis filters (for example "emotion: sarcastic only"), and the existing tag filters | M | US-4 | P4, P5 |

### Meme-side enrichment

| ID | Requirement | Pri | Stories | Project |
|---|---|---|---|---|
| FR-7 | Every active image gets a meme profile through an offline batch job: values per axis, plus a short "when to use this" sentence in English. Inputs: image, OCR text, existing descriptions, human note, existing tags | M | US-8 | P2 |
| FR-8 | The extraction job is incremental, resumable, run-tracked, admin-triggerable from `/admin/batches`, supports `--limit`, and records failures per image without retrying forever (same conventions as `build_image_descriptions`) | M | US-8 | P2 |
| FR-9 | Free-text axis values (`situation`) are embedded per image, in one embedding model and space shared with query-side vectors, so similarity uses the same index technology as today (pgvector). Controlled-vocabulary axes use exact keys plus a precomputed key-to-key similarity instead of per-image vectors (ADR-002) | M | US-1 | P2, P4 |
| FR-10 | Human-rejected Ollama descriptions never feed the extraction. A human description note is treated as the strongest input. A feedback change marks the image's profile stale | M | US-8 | P2 |
| FR-11 | A curator can correct a meme profile in the UI (edit axis values), and a corrected profile is marked human-verified and is never overwritten by the batch job | S | US-9 | P2, P5 |
| FR-12 | The taxonomy is versioned. Each stored profile records the taxonomy version and the extraction model and prompt version, so a change marks affected profiles stale and re-extractable | M | US-8 | P1, P2 |

### Retrieval and ranking

| ID | Requirement | Pri | Stories | Project |
|---|---|---|---|---|
| FR-13 | Search returns a ranked page of memes, with pagination consistent with the existing cursor contract (`nextCursor` addresses the last returned item) | M | US-1 | P4 |
| FR-14 | Ranking fuses per-axis similarity between query profile and meme profile with configurable per-axis weights (`settings.yaml`), plus an optional lexical signal from existing smart search. Weights live in settings, never in code | M | US-1 | P4 |
| FR-15 | Axes absent from the query profile contribute nothing (no penalty). Axes absent from a meme profile score neutral, not as a mismatch | M | US-1 | P4 |
| FR-16 | Each result carries an explanation: per-axis score and the meme value matched, available to the UI | M | US-2 | P4, P5 |
| FR-17 | Results can be filtered by axis value (hard filter) and faceted: facet counts per axis value over the candidate set | S | US-4 | P4 |
| FR-18 | The user can mark a result as a good or bad match. The signal is stored for evaluation only (not used to retrain or re-rank in the first release) | C | US-5 | P6 |
| FR-19 | Diversity: the top page should not be dominated by near-duplicates of one meme. Duplicate clusters collapse to one representative by default | S | US-1 | P4 |
| FR-20 | Flagged images, non-active images and images without a meme profile are never returned by the axis path (images without a profile may still be reachable through the lexical path, labelled as such) | M | US-1 | P4 |

### API and operations

| ID | Requirement | Pri | Stories | Project |
|---|---|---|---|---|
| FR-21 | A backend endpoint accepts text (and optional filters or an edited profile) and returns a page of memes with the parsed profile and explanations. Documented in `backend_api.md`, with response schemas in `shared/schemas/` and generated types (TS, Kotlin, Python) | M | US-1 | P4 |
| FR-22 | Pipeline stages are exposed internally as typed, side-effect-free tools with JSON-schema'd inputs and outputs, so an agent layer can call the same code the endpoint uses | M | US-10 | P3, P4, P7 |
| FR-23 | An admin view shows profile coverage (images with and without a profile, stale, human-verified) and the last extraction runs | S | US-8 | P2, P5 |
| FR-24 | An offline evaluation command runs the gold set against the current pipeline and prints metrics and per-case diffs against a stored baseline | M | US-11 | P6 |

## Non-functional requirements

| ID | Requirement | Target (initial, to be confirmed by measurement) |
|---|---|---|
| NFR-1 | Query latency, parse plus retrieval, p50 / p95 on the reference workstation with the configured local model | 3 s / 8 s. Retrieval alone under 300 ms for 25k active images |
| NFR-2 | Privacy: query text goes only to the local Ollama and the local database. It is not sent to any external service. Query text is stored only if search history already stores it, with the same retention | Hard requirement |
| NFR-3 | Determinism: the same text and the same data version produce the same ranking (LLM called with temperature 0 and a fixed seed where Ollama supports it; parse result cached by normalised text) | Required for evaluation |
| NFR-4 | Availability: with Ollama down, search still works through the embedding-only path and the lexical path, and the UI says the structured path is degraded | Required |
| NFR-5 | Throughput of the offline job: bounded by Ollama. The design must give a measured images-per-hour figure and a backlog completion estimate per environment | Documented in P2 |
| NFR-6 | Quality: top-k relevance measured on the gold set (see `OQ-5` for metric and threshold). Releases must not regress the stored baseline | Set in P6 |
| NFR-7 | Three-environment parity: metal, general and it each have their own taxonomy overlay values and weights if needed. `it` may lack features that need data it does not have, and must say so explicitly (as `it` does for tagging today) | Per project |
| NFR-8 | Cost of storage: per-axis vectors for the active library fit in the existing database with an index strategy that keeps build time and size acceptable (measured in P2) | Documented in P2 |
| NFR-9 | Observability: each search logs parse time, LLM model and prompt version, fallback path used, retrieval time, and result count, without logging the text itself unless search history does | Required |
| NFR-10 | Accessibility and responsive behaviour of the new UI follow the existing frontend standards (tsc, eslint with 0 warnings, vitest) | Required |

## Open questions

Each question has an owner project and a default we proceed with unless told otherwise.

| ID | Question | Default for now | Resolved by |
|---|---|---|---|
| OQ-1 | The exact axes and vocabularies. Draft list in ADR-002 (`situation`, `emotion`, `stance`, `topic`, `tone`) | Use the draft | P1 |
| OQ-2 | Controlled vocabulary vs free text per axis, and how multilingual queries map to the vocabulary | Controlled vocabulary for `emotion`, `stance`, `tone`, free text for `situation`, `topic` | ADR-002 |
| OQ-3 | Which local model for parse and for extraction (vision-capable for extraction, text model for parse). The current Ollama models are `qwen2` (text) and `llava` (image descriptions). Is quality sufficient, particularly for Russian and Spanish | Evaluate in P3 and P2 with the gold set before committing | P3, P2, P6 |
| OQ-4 | One embedding model for all axis vectors and all three languages. Existing text embeddings are `bge-large-en-v1.5` (English only) and a multilingual MiniLM (384-dim, for OCR duplicates) | Candidate: a multilingual model, or translate the query profile to English and keep an English space. Decide in ADR-002 with a measured comparison | P1, P6 |
| OQ-5 | Metric and acceptance threshold (for example recall@10 of "acceptable" memes, nDCG@10) and the gold set size | 150-300 cases, three languages, graded relevance 0-2, nDCG@10 and recall@10 | P6 |
| OQ-6 | Should the first release include the lexical signal in fusion, or the axis path alone | Include it with a low default weight | ADR-003, P4 |
| OQ-7 | Do memes that lack readable text and have unclear meaning get a profile at all, or a "low confidence" flag | A confidence value per axis, low confidence axes are neutral in fusion | P2 |
| OQ-8 | Is NSFW/offensive content filtered or labelled, and is it shown in reply scenarios by default | Add a `tone` value and a filter. Default shows everything the library shows today. Confirm with the user | P1 |
| OQ-9 | Does the agent layer (P7) justify itself after P3-P6 are measured. Candidates: query rewriting, multi-step refinement, asking a clarifying question | Defer. Decide after the gold set shows where the pipeline fails | P7 |
