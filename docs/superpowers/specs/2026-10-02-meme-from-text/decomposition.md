# Meme from text: decomposition and gap analysis

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

Step 1 breaks the target into capabilities. Step 2 maps each to the existing system (verified against `docs/data-flow.md`,
`CLAUDE.md`, `backend_api.md` and `environments/settings.yaml` on 2026-10-02). Step 3 lists the gaps that become projects.
Step 4 is the agent tool catalogue.

## 1. Capabilities

```
                      OFFLINE (batch)                                  ONLINE (request)
 ┌──────────────────────────────────────────────┐        ┌───────────────────────────────────────────────────┐
 │ C1 Taxonomy (axes, vocabularies, versions)   │◄──────►│ C5 Query understanding (text → query profile)     │
 │ C2 Meme profile extraction (image+text → axes)│        │ C6 Axis retrieval (per-axis candidate scores)     │
 │ C3 Axis embeddings (value → vector)          │        │ C7 Filtering and facets                           │
 │ C4 Profile store, staleness, human override  │        │ C8 Fusion and ranking, diversity, explanation     │
 └──────────────────────────────────────────────┘        │ C9 API endpoint                                   │
                                                          │ C10 Web UI (input, profile chips, results, facets)│
 Cross-cutting: C11 Evaluation (gold set, metrics)       └───────────────────────────────────────────────────┘
                C12 Operations (batch registry, run tracking, admin, statistics, settings per environment)
                C13 Agent layer (optional, wraps C5-C8 as tools)
```

## 2. Mapping to what exists

Legend: **Reuse** as is, **Extend** (change or add to existing code), **New**.

| Capability | What exists today | Verdict | Notes |
|---|---|---|---|
| C1 Taxonomy | Tag vocabulary for `ConceptTagger` (`batch/data/tagging/concepts.<profile>.yaml`, `tags.<profile>.yaml`), tag `category` field, YAML-per-environment convention | **New**, modelled on the YAML convention | Existing tags are concept-level (things, subjects). They are not situation/emotion/stance axes. Tags stay as a separate signal and can map into the `topic` axis |
| C2 Meme profile extraction | `build_image_descriptions` (multi-prompt, Ollama `llava`, failure tracking, incremental, run-tracked, `--limit`), `image-description-prompts.<env>.yaml`, description feedback, description notes, OCR text | **Extend** pattern, **New** job | Reuse the multi-prompt framework and its failure tracking. The current prompt set has one prompt, "What is shown in this image?" (visual content, not meaning). The new job needs structured JSON output with the taxonomy, using image + OCR text + existing description + note as input. Likely a new table, not new prompt keys, because the output is structured (see ADR-002) |
| C3 Axis embeddings | CLIP image embeddings (512-dim), description and note embeddings (`bge-large-en-v1.5`, 1024-dim, English only), OCR-text embeddings (multilingual MiniLM, 384-dim), pgvector HNSW indexes, per-embedding batch jobs | **Extend** pattern, **New** table(s) | Model choice is open (OQ-4). Cross-lingual query to English-profile matching needs either a multilingual model or query translation |
| C4 Profile store | `image_descriptions`, `image_description_feedback`, `description_notes`, `image_tags` with `source`, Alembic migrations, `Storage/models.py` | **New** tables, existing conventions | Needs: taxonomy version, model/prompt version, confidence per axis, `human_verified` flag, staleness. Human override mirrors the notes feature (a human value beats generated) |
| C5 Query understanding | Query tokenisation and lemmatisation (`rules/normalize.py`), language detection used in OCR and concept-vocabulary work, Ollama integration in `ai/` (`qwen2` configured as `ollama.model`) | **New** | No query-time LLM call exists anywhere today. Needs a structured-output prompt, validation, retry, timeout, caching and the fallback path. Reuse the `ai/` Ollama client and language detection |
| C6 Axis retrieval | pgvector kNN for `/similar` (image, description, description_note, description_all), `repository/` pattern, smart search lexical matching | **Extend** | `/similar` is image-to-image. Needs text-vector-to-axis-vector search. The query side of the repository is new, the index technology is reused |
| C7 Filtering and facets | `/api/images` facets and tag filters, `MemeSearchResponse.facets` | **Extend** | Add axis facets with counts over the candidate set |
| C8 Fusion and ranking | `repository/search_ranking.py`: source weights x tier weights summed per token, `search.ranking.*` in settings, missing-key-is-startup-error, relevance cursors, ADR `adr-2026-10-01-search-ranking-in-python.md` | **Extend** | Same style: weights in settings, scoring in Python, cursor embeds the score. The fusion inputs change from lemma hits to per-axis similarities plus the lexical score. Duplicate-cluster collapsing: `tmp_clusters` exists |
| C9 API | FastAPI routers, `shared/schemas/*.schema.json`, generated TS/Kotlin/Python types, `backend_api.md` | **Extend** (new router) | Follow the "adding a new endpoint" recipe in CLAUDE.md. Beware hand-written response models shadowing generated ones (CLAUDE.md gotcha) |
| C10 Web UI | `SearchPage`, `MemePage` (description notes and feedback UI), `ExplorePage`, facet components, `AdminBatchesPage` | **Extend** (new page, reuse components) | Reuse result grid, infinite scroll (windowed), facets. New: query box, profile chips editor, explanations, rating widget |
| C11 Evaluation | Nothing for relevance. There is `review-duplicates`, `suggest-tags` (Claude Code commands) and a statistics snapshot | **New** | Gold-set format, runner, baseline store, per-language report. Could be seeded from the user's own judgement through a small labelling UI or file |
| C12 Operations | `batch_registry.yaml`, run tracking, `run_wrapper`, `description_pipeline` driver, scheduler, `build_statistics`, three environment overlays | **Reuse** | New jobs register like existing ones. Decide whether profile extraction joins `description_pipeline` or is its own driver. Statistics: add coverage |
| C13 Agent layer | Nothing. The user named LangChain as a candidate | **New, optional** | Wraps C5-C8 tool functions. Only worth building after the gold set shows where the fixed pipeline fails (OQ-9) |

## 3. Gaps (become projects)

1. **No meaning schema**: nothing today represents situation, emotion or stance. (P1)
2. **No structured, meaning-level meme enrichment**: existing descriptions are visual ("what is shown"), not "when would you use this". (P2)
3. **No query-time LLM and no query parsing**. (P3)
4. **No text-to-axis retrieval and fusion**. (P4)
5. **No UI for this flow**. (P5)
6. **No relevance evaluation at all**: today's ranking was tuned by reasoning and spot checks. (P6)
7. **Embedding space for three languages is unresolved**: the best existing text embeddings are English-only. (P1 decides, P6 measures)
8. **Taxonomy/profile versioning and staleness**: the existing jobs handle staleness per feature, not per schema version. (P1, P2)

Dependencies on existing work:
- Description tagging, notes and feedback (all done as of 2026-10-01, see `board/description-tagging-tracker.md`) are inputs to P2.
- Ingestion does not run description jobs (new images are described after promotion). P2 follows that rule: profile extraction covers
  active images only and joins after `ingest_promote`.
- Known gap that affects quality: the library has many images with no OCR text or no description. P2 must define behaviour for
  them (OQ-7).

## 4. Agent tool catalogue (draft)

All tools are pure functions of their inputs and the database state (read-only), with JSON-schema'd arguments and results. The
fixed pipeline (ADR-001, approach B) calls them in a fixed order. An agent calls them in an order it chooses. Names are
provisional.

| Tool | Input | Output | Notes |
|---|---|---|---|
| `parse_query` | text, optional language hint | query profile (axis values, confidence), detected language, parser version | The only tool that calls the LLM. Cached by normalised text |
| `get_taxonomy` | none | axes, vocabularies, taxonomy version | Lets an agent ground its own prompts and lets the UI render chips |
| `search_axis` | axis, value or vector, filters, k | candidate image ids with axis similarity | One tool per axis or one tool with an axis argument |
| `search_text` | text | candidate image ids with lexical score | Wraps today's smart search |
| `search_similar_to` | image id, mode | candidates | Wraps `/similar`. Useful for "more like this" refinement |
| `filter_candidates` | candidates, axis filters, tag filters | filtered candidates | Hard filters |
| `fuse` | per-signal candidate lists, weights | one ranked list with per-signal scores | Weights from settings unless overridden |
| `explain` | ranked list, query profile | per-result per-axis explanations | Derived from stored scores, no LLM |
| `get_meme_profile` | image id | meme profile, human_verified, confidence | For inspection and re-ranking logic |
| `rerank_with_llm` (optional, only in P7) | query text, top-N profiles | reordered top-N with reasons | The one place an LLM judges fit directly. Gated by evaluation |

## 5. Suggested build order

P1 → (P2 and P6 in parallel, P6 starting with the gold set because it also informs model choices in P2) → P3 → P4 → P5 → P7 if justified.
See `tracker.md` for the table with dependencies and status.
