# P4: Retrieval, fusion and API — Design

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md (project P4)
Follow-ups: none yet (P5, the web UI, consumes the endpoint defined here)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Inherited contracts: ADR-003 (candidate union, per-axis scores, weighted-sum fusion, filters, facets, diversity, cursor, explanations, fallbacks), P1 (`queryprofile`/`memeprofile` schemas, vocabulary similarity artifact), P2 (profile tables and the situation embeddings), P3 (`parse_query`, `QueryParser`, the degraded contract and its handoff notes), P6 (the gold set, the harness and the **re-based acceptance thresholds approved on 2026-10-07**).
Requirements covered: FR-6, FR-13 to FR-17, FR-19 to FR-22, NFR-1, OQ-6; it also provides the evidence for NFR-6 on the gold set.

User decisions for this project (2026-10-07): the **live backlog run (board task 158) stays separate and happens before P5 or rollout**; P4 is built and evaluated on the 1,500-image gold corpus (the pipeline takes a corpus restriction); the design below was approved section by section in chat.

## Problem

P2 can profile memes and P3 can turn a message into a query profile, but nothing yet ranks memes against a profile or exposes it. Today's search (`/api/images?q=`) is keyword AND-only (0.000 nDCG@10 on the gold set for full messages). P6 measured what a single-signal axis match over parsed profiles achieves (nDCG@10 0.468, hit@10 1.000 on the round-2 judgments, the best baseline), so P4 must beat that with a fused, explainable, filterable, paginated endpoint that stays fast and degrades gracefully when the local model is unavailable.

## Findings that shape the design (verified 2026-10-07)

- **Data volume today:** about 1,650 of 32,000 `general` images have meme profiles (the gold corpus plus an earlier slice); the full backlog is task 158. The pipeline must therefore accept an explicit eligible-image restriction (for evaluation) and must be correct, and fast enough, at 25,000 profiles (NFR-1) although only 1,650 exist.
- **Tables:** `meme_profiles` (`situation_text`, `use_when`, `human_verified`, staleness metadata), `meme_profile_values` (`image_id`, `axis`, `value_key`, `confidence`, `source`; index on `(axis, value_key)`), `meme_profile_situation_embeddings` (1024-dim `bge-large` vectors with `model_name` and `model_digest`, an HNSW cosine index). Duplicate clusters are `tmp_clusters` (`cluster_id`, `image_id`); flags are `image_extras.flagged`.
- **Existing search contract:** `GET /api/images?q=` returns `MemeSearchResponse` (`items: Meme[]`, `facets`, `nextCursor`, `hasNext`) with an opaque relevance cursor that embeds the last score and id; `Meme` has `id`, `imageUrl`, `originalFileName`, `text`, `tags`, `flagged`, `clusterId`, `descriptionNote`. Search ranking weights live under `search.ranking.*` in the tracked YAML with a startup error for a missing key.
- **Vocabulary similarity:** `batch/data/taxonomy/similarity.json` holds 25 curated near-synonym pairs (the automatic embedding neighbours were noise, P1 addendum), read at runtime.
- **Parser handoff (P3 addendum):** `get_query_parser()` is registered at startup; `warm_up()` should run as a background task; a degraded profile (`profile.degraded`) is the trigger for the embedding-only path; Windows takes about 2 s to fail when Ollama is down (a small circuit breaker is recommended); the cache is per process and a hit keeps the original `attempts`/`fallback`; in Docker `OLLAMA_HOST` must be set; `situation` is untrusted text.
- **Embedding calls:** the backend has no HTTP client dependency, so the query embedding uses `urllib` against Ollama's `/api/embed` in a worker thread, the same pattern as the P3 chat client.
- **Gold results to beat (round-2 judgments, 1,220 grades):** `parsed_axis_match` nDCG@10 0.468 / hit@10 1.000 (en 0.429, es 0.511, ru 0.463; by difficulty ambiguous 0.462, easy 0.530, hard 0.417; by length short 0.591, medium 0.378, long 0.525), `situation_embedding` on the raw message 0.252 (ru 0.052), `lexical_or` 0.196.

## Decisions

### 1. Package layout

| Module | Responsibility | Dependencies |
|---|---|---|
| `retrieval/config.py` | `RetrievalConfig` and `load_config(get)` for `search.meme_from_text.*`; a missing key is a startup error | stdlib |
| `retrieval/scoring.py` | Pure per-axis scoring (situation, controlled axes, lexical), the neutral rule, the similarity lookup | stdlib, `taxonomy` |
| `retrieval/fusion.py` | Weighted-sum fusion over the present axes, the score explanation, ranking with deterministic ties | stdlib |
| `retrieval/facets.py` | Facet counts over a candidate set, duplicate-cluster collapse | stdlib |
| `retrieval/cursor.py` | Opaque cursor encode and decode with a request fingerprint | stdlib |
| `retrieval/pipeline.py` | The orchestrating `MemeFromTextPipeline.search(request) -> SearchResult` with injected parser, embedder and repository; the degraded paths; the small result cache; the parser circuit breaker | stdlib, `parse`, `taxonomy`, `retrieval.*` |
| `retrieval/tools.py` | Typed tool schemas and functions (decision 8) | stdlib |
| `retrieval/embed.py` | `OllamaEmbedClient` over `urllib` (`/api/embed`) with typed errors, mirroring `parse/client.py` | stdlib |
| `repository/meme_retrieval.py` | SQL: eligible set, candidate generation, profile loading, duplicate clusters, facet inputs | SQLAlchemy |
| `repository/ocr_lemmas.py` (modify) | A shared `lexical_or_scores(session, text, restrict_ids=None)` extracted from the evaluation retriever so the product and the harness use one implementation | SQLAlchemy |
| `Backend/app/services/meme_from_text_service.py`, `Backend/app/api/meme_from_text.py` | Service and router; response model construction; request validation | FastAPI |
| `shared/schemas/*` (new) + generated TS, Kotlin, Python types | Request, response, result and axis-explanation schemas | - |

The pure modules (`config`, `scoring`, `fusion`, `facets`, `cursor`, `tools`, `embed`) import only the standard library, `taxonomy`, `parse` and `retrieval` (an AST guard test enforces it); they carry all logic and are unit-tested without a database or Ollama. `repository/meme_retrieval.py` returns plain Python values, never Pydantic models, and never commits (CLAUDE.md). `Dockerfile.backend` copies `retrieval/`.

### 2. Request, flow and caching

`POST /api/meme-from-text/search`, body (`memefromtextrequest.schema.json`): `text` (required, at most `parse.max_input_chars` after normalisation), `languageHint` (`en` | `ru` | `es` | `other`), `profile` (an optional edited query profile: the axes and `situation` only; validated with `taxonomy.validate_profile`; when present the message is not parsed), `filters` (`emotion`, `stance`, `topic`, `tone`: lists of vocabulary keys, plus `tags`: the existing key/value tag filters), `cursor`, `limit` (1 to 50, default 20).

Flow:
1. Resolve the query profile: the edited profile, or `parse_query` through the process-wide `QueryParser` (the P3 cache serves repeat requests). A parser circuit breaker skips the parse for `search.meme_from_text.parser_breaker.open_seconds` (default 30) after `failures` (default 3) consecutive `unavailable` or `timeout` fallbacks, answering degraded at once (it exists because a closed Ollama port costs about 2 s on Windows).
2. Embed the profile's `situation` with the profile embedding model (`taxonomy.embedding_model`) through `OllamaEmbedClient` (timeout `embed_timeout_seconds`, default 2.0); an LRU of the last 200 query vectors.
3. Generate candidates (decision 3), load their profile values, score and fuse (decision 4), collapse duplicate clusters, rank.
4. Build facets over the filtered candidate set; slice the page by the cursor; compute explanations only for the page.
5. Return the page with the profile used, the degraded flag and fallback reason, and timing metadata.

A small in-process result cache (bounded to 50 entries, TTL `cache_ttl_seconds`, default 120) keyed by the sha256 of the normalised request without the cursor (text or profile, filters, parser version, taxonomy version, weights hash) keeps paging stable and avoids repeating the SQL for page 2. Staleness is bounded by the TTL; a new profile or flag appears at the next miss.

### 3. Candidate generation (ADR-003 decision 1)

All sources are restricted to the **eligible set**: `images.status = 'active'`, not flagged (`image_extras.flagged IS NOT TRUE`), a `meme_profiles` row exists (FR-20), the hard filters hold (axis filters are `EXISTS` conditions on `meme_profile_values`, OR within an axis and AND across axes; tag filters reuse the existing tag-filter query), and, for evaluation, `image_id IN (restriction)`.
- **Situation source:** the top `candidates.situation_k` (default 300) eligible memes by cosine distance of `meme_profile_situation_embeddings` rows whose `model_name` equals the configured model, ordered by distance then image id. On the eligible sets that exist today an exact order is used; the plan verifies the installed pgvector version and sets `hnsw.iterative_scan` (or an equivalent) when filters would otherwise starve the index scan.
- **Axis source:** the top `candidates.axis_k` (default 300) eligible memes by the sum, over the query's values, of `query confidence x meme confidence` for exact keys and for the curated neighbours of each query key, computed in SQL by a grouped query on `meme_profile_values`.
- **Lexical source (optional, OQ-6, default weight low):** `lexical_or_scores(session, text, restrict_ids)`: per-token `scored_image_matches` scores summed over the message tokens, normalised by the maximum into 0..1; limited to the top `candidates.lexical_k` (default 100). Images with no profile may appear only here and are labelled `axisPath: false` (FR-20).
- The union is deduplicated and capped at `candidates.total_cap` (default 600) by best source rank.

### 4. Scoring and fusion (ADR-003 decisions 2 and 3)

All axis scores are in 0..1.
- **Situation:** `s_sit = clip((cos - floor) / (ceil - floor), 0, 1)` with `cos` the cosine similarity between the query vector and the meme's situation vector, `floor` and `ceil` settings (initial 0.45 and 0.85, from the P1 probe where unrelated pairs scored 0.44 to 0.58 and related pairs 0.66 to 0.84). A meme with no situation embedding scores the neutral constant on this axis.
- **Controlled axes (emotion, stance, topic, tone):** for the query values `Q = {(k, cq)}` and the meme values `M = {(m, cm)}`: `match(k) = max over m of sim(k, m) x cm`, where `sim = 1` for equal keys, the curated neighbour score for neighbours (when `similarity.use_neighbours` is true), else 0; `s_axis = sum(cq x match(k)) / sum(cq)`. A meme with **no value on a queried axis** scores `neutral_score` (default 0.3) on it (FR-15), not zero; a query axis with no values is not scored at all.
- **Lexical:** `s_lex` from decision 3, 0 when absent.
- **Fusion:** `score = sum over present axes P of w_a x s_a / sum over P of w_a`, where P is the set of query axes that have a value (situation counts when the profile has a situation) plus `lexical` when the lexical source is enabled and the message has tokens. Weights come from `search.meme_from_text.weights.{situation,emotion,stance,topic,tone,lexical}` (a missing key is a startup error, as `search.ranking.*`); initial documented defaults 0.40, 0.15, 0.10, 0.15, 0.10 and 0.10 are replaced by the tuned values (decision 10). The ranking order is `(-score, image_id)` so ties are deterministic (NFR-3).
- **Explanation (FR-16):** for each result and each present axis: `axis`, `score`, `weight` (the renormalised share), `contribution = weight x score`, `queryValues`, and `matched`: the pairs `(queryKey, memeKey, similarity, memeConfidence)` that produced the score (an empty list with `neutral: true` when the neutral rule applied); plus the meme's `situationText` and `useWhen`. The same computation yields the score and the explanation (no second pass, no LLM).

### 5. Filters, facets, diversity

- **Hard filters** are applied in SQL before ranking (decision 3). **Facets** (FR-17) are one `Facet` per axis (`name` = the axis, buckets of `{value, count}`) counted over the **filtered candidate set after duplicate collapse**, so a count says how many results the user would get by narrowing to that value; the existing tag facets are not included (they belong to `/api/images`).
- **Diversity (FR-19):** after scoring, each duplicate cluster (`tmp_clusters`) is collapsed to its best-scoring member (ties by image id); the representative carries `clusterId` and `clusterSize`, the others stay reachable through the existing cluster view. The collapse applies to every page so the ranking stays consistent across pages.

### 6. Pagination

The cursor is the existing pattern: base64 JSON of `{s: score rounded to 6 decimals, i: image id, f: request fingerprint}`; the page contains the next `limit` results with `(-score, id)` after the cursor; `nextCursor` addresses the last returned item and `hasNext` is true when more candidates remain. A cursor whose fingerprint does not match the request restarts at page 1 (as the existing relevance cursor does). Pages are cut from the cached ranked list of decision 2.

### 7. Degraded paths and availability (FR-4, NFR-4)

- **Parse degraded and no edited profile:** the situation axis uses the embedding of the **raw message** (the English-only embedding is weak for Russian, which the response says through `fallback`), the controlled axes are absent, and the lexical source is on; `degraded: true` and `fallback` carry the parser's reason (`unavailable`, `timeout`, `invalid_output`, `empty_input`, or `breaker_open`).
- **Embedding also unavailable:** lexical only (`fallback` appends `embed_unavailable`); with no lexical hits the page is empty with `degraded: true`. The endpoint never returns an error because a model failed; only a malformed request is a 4xx (422).
- **`it`** (no profiles, `parse.model` unset): the response is the explicit "not available" answer: HTTP 200 with `degraded: true`, `fallback: "unavailable"`, no items, and `available: false` in the metadata, so the UI can say so.
- **Startup:** the backend `lifespan` already builds the parser; it additionally builds the pipeline and starts a background task that calls `parser.warm_up()` and one embedding call at startup and every `warm_interval_minutes` (default 20, below the 30-minute `keep_alive`), never raising.

### 8. Tools (FR-22)

`retrieval/tools.py` exposes typed, side-effect-free (apart from the local model calls and caches) functions with JSON-schema'd inputs and outputs: `generate_candidates`, `score_candidates`, `fuse_and_rank`, `filter_and_facet`, `explain_results`, and the orchestrating `search_memes_from_text`; they take explicit data and injected services, so an agent layer can call the same code the endpoint uses. Schemas are plain dictionaries like P3's; the request, result and response shapes that cross the API are shared JSON schemas with generated types.

### 9. Public API and schemas

New schemas in `shared/schemas/` (and `all.schema.json`), with TS, Kotlin and Python types regenerated (CLAUDE.md procedure, `documents/generation.md`) and no diff in CI: `memefromtextrequest`, `memefromtextresult` (`meme: Meme`, `score`, `axisPath`, `explanation`, `clusterId`, `clusterSize`), `axisexplanation`, `memefromtextresponse` (`items`, `facets: Facet[]`, `nextCursor`, `hasNext`, `profile: QueryProfile`, `degraded`, `fallback`, `meta: {available, parseMs, embedMs, retrievalMs, candidates, cacheHit, parserVersion, model}`). `backend_api.md` documents the endpoint (signature, parameters, response, behaviours, the degraded contract). A router that hand-writes response models must declare every field (CLAUDE.md gotcha): the router uses the generated models.

### 10. Evaluation, tuning and acceptance (extends the P6 harness)

- **`fused` retriever** (`evaluation/retrievers/fused.py`): runs `MemeFromTextPipeline` with the gold corpus as the eligible restriction, the same parser and embedder, and the weights in settings; `python -m evaluation run --retriever fused --parse-model M --label L` stores a baseline; `build-pools --sources fused` pools its top ten for a judging round.
- **`python -m evaluation tune-weights`:** searches the six weights (a coarse simplex grid, then coordinate refinement) maximising mean nDCG@10 on the judged gold queries, with leave-queries-out cross-validation (five folds, stratified by language) to estimate the overfit, and prints the recommended weights, the cross-validated score and the per-fold spread. Reviewing and committing the weights to `settings.yaml` is a human step. Tuning happens **before** the round-3 judging, and the final claims are made on the round-3 judgments.
- **Round 3:** the unjudged members of the tuned `fused` top ten are judged by the user (the thresholds were approved on that condition; the expected size is a few hundred pairs, less than round 2 because the parsed top ten is already judged).
- **Acceptance numbers (approved 2026-10-07, re-based):** overall nDCG@10 at least **0.50** and hit@10 at least **0.95**; every language nDCG@10 at least 0.85 of English and at least 0.30; no query class below the best baseline's score on that class (difficulty: ambiguous 0.462, easy 0.530, hard 0.417; length: short 0.591, medium 0.378, long 0.525, the `parsed_axis_match` values); no regression of the stored baselines of the other retrievers' code paths.
- **Latency (NFR-1):** retrieval alone (candidates, scoring, fusion, facets) p95 under 300 ms and with the parse (p50 3 s / p95 8 s end to end) measured on the gold corpus with the warm model; the 25,000-profile case is covered by a synthetic load check in the test database (decision 11).

### 11. Synthetic load check (test database only)

A script (`tests/load/`, not part of the normal pytest roots) fills `ocrdb_test` with 25,000 synthetic images, profiles, values and random normalised situation vectors, runs the candidate SQL, scoring and fusion for 50 query profiles and prints the p50 and p95 of the retrieval stage. It never touches a live database (the test URL only) and is run by the controller; its result goes into the addendum.

### 12. Settings (`environments/settings.yaml`, common file; absent behaviour for `it` is the `parse.model` opt-out)

`search.meme_from_text`: `weights.*` (six keys), `candidates.situation_k: 300`, `candidates.axis_k: 300`, `candidates.lexical_k: 100`, `candidates.total_cap: 600`, `neutral_score: 0.3`, `situation.floor: 0.45`, `situation.ceil: 0.85`, `similarity.use_neighbours: true`, `lexical.enabled: true`, `cache_ttl_seconds: 120`, `embed_timeout_seconds: 2.0`, `warm_interval_minutes: 20`, `parser_breaker.failures: 3`, `parser_breaker.open_seconds: 30`, `page.default_limit: 20`, `page.max_limit: 50`. A missing key is a startup error.

## Out of scope

- The web UI, the chips editor, rating capture (P5), any LLM re-rank, a learned ranker, query rewriting (P7).
- Running the profile backlog (board task 158, separate by decision).
- Changing the existing `/api/images` endpoints.
- A persistent search or parse cache, per-user personalisation, and multi-turn refinement.

## Testing

Each root as its own `pytest` command (CLAUDE.md); the **whole** `tests/integration/` root runs because shared code changes (`repository/ocr_lemmas.py`, the evaluation retriever now delegates to it).
- `tests/retrieval/` (pure): axis scoring against hand-computed examples (exact match, neighbour match, confidence weighting, neutral rule, absent query axis), the situation floor/ceiling rescaling, fusion renormalisation over present axes (FR-15), deterministic tie order, explanation contents and that contributions sum to the score, facet counts after collapse, duplicate collapse (best member wins, ties by id), cursor round trip and the fingerprint-mismatch restart, settings loader (every key, a missing key is an error), the circuit breaker (opens after N failures, closes after the interval), the result cache (hit, TTL expiry, key changes with filters, text, weights, parser version), the degraded paths (parse degraded, embed failure, both, empty page), the AST import guard, hostile and very long text, and that the text is never logged.
- `tests/retrieval/test_embed_client.py`: the embed client against a local `http.server` (success, timeout, 404, 500, malformed body, connection refused, the typed errors).
- `tests/integration/test_meme_retrieval_repository.py` (`ocrdb_test`): the eligible set (inactive, flagged and profile-less images excluded; a corpus restriction), axis filters (OR within an axis, AND across), tag filters, the situation source ordering and the model-name restriction, the axis source with neighbours, duplicate clusters, `lexical_or_scores` ranking and restriction, and that nothing in the repository commits.
- `Backend/tests/`: the endpoint with a fake pipeline (request validation, 422 cases, response shape against the generated model, `it` answer, cursor behaviour), the `lifespan` wiring (pipeline and warm-up task built, a failure does not stop startup).
- `tests/evaluation/`: the `fused` retriever with a fake pipeline, `tune-weights` on a tiny synthetic gold set (the grid, the cross-validation split, deterministic output), the new `build-pools --sources fused` option.
- Generated types: the TS, Kotlin and Python trees are regenerated and a diff check shows nothing stale; `backend_api.md` mentions the endpoint.
- `tests/docker/`: the image imports `retrieval`.

## Acceptance criteria

1. The `retrieval/` package, the repository, the service and the endpoint exist as in decisions 1 to 9; the pure modules import only stdlib, `taxonomy`, `parse` and `retrieval`; the settings keys exist and a missing key is a startup error.
2. The endpoint returns a ranked, filtered, faceted, paginated, explained page with the parsed profile, never fails because a model failed (degraded paths of decision 7), and excludes flagged, inactive and profile-less images on the axis path.
3. `shared/schemas`, the generated types and `backend_api.md` are updated with no CI diff.
4. `fused` runs on the gold corpus, the tuned weights are in `settings.yaml` with the cross-validated estimate recorded, and the round-3 judgments are imported.
5. The approved thresholds hold on the final judgments: overall nDCG@10 at least 0.50 and hit@10 at least 0.95, every language at least 0.85 of English and at least 0.30, no query class below the best baseline's score on that class.
6. Retrieval alone p95 is under 300 ms on the gold corpus and in the 25,000-profile synthetic check; end-to-end p50 3 s / p95 8 s with the warm parser.
7. The addendum records the tuned weights, the cross-validated and final numbers, the latency measurements and the handoff notes for P5; the tracker, `CLAUDE.md` and the review report are updated.
8. All test roots pass (`tests/retrieval`, `tests/evaluation`, `tests/parse`, `tests/taxonomy`, `tests/ai`, `batch/tests`, the whole `tests/integration`, `Backend`, `tests/docker`).

## Risks

| Risk | Mitigation |
|---|---|
| The fused ranking does not beat the single-signal parsed axis match | The thresholds are the gate; the tuning uses cross-validation; the retriever interface lets the individual signals be re-measured; RRF is the documented alternative (ADR-003) |
| The 60-query gold set overfits the weights | Leave-queries-out cross-validation, tuning before round 3, final numbers on round-3 judgments, the spread across folds is reported |
| Judged coverage biases the comparison toward whatever was pooled | A round 3 pools the tuned retriever's own top ten; reports print `judged@10`; the thresholds are evaluated on the final judgments |
| HNSW with filters returns too few rows | Exact ordering on small eligible sets, `hnsw.iterative_scan` verified in the plan, the synthetic load check |
| Retrieval is slow at 25,000 profiles | Candidate caps, indexed SQL, Python scoring on at most 600 candidates, the synthetic check, the TTL cache |
| Ollama down makes every search slow (a closed port takes about 2 s on Windows) | The parser circuit breaker, embed timeout 2 s, lexical fallback |
| A mismatched embedding model or digest mixes vectors | Rows are filtered by `model_name`; a digest mismatch at startup is logged and counted; re-embedding is the P2 job |
| Only 1,650 of 32,000 memes are profiled until task 158 | The user's decision (separate run before P5/rollout); the response and the UI show coverage; the pipeline works with any eligible set |
| Shared code change breaks other consumers (`repository/ocr_lemmas.py`) | The extraction keeps behaviour identical (the evaluation retriever delegates), the whole `tests/integration/` root runs |
| A router with hand-written models drops fields (CLAUDE.md gotcha) | The router uses the generated models; a test compares the response against the schema |

## Plan outline (for writing-plans)

1. `retrieval/` pure core: config, scoring, fusion, facets and collapse, cursor, with their tests.
2. `retrieval/embed.py` client with the local-server tests; the shared `lexical_or_scores` extraction (and the evaluation retriever delegating to it).
3. `repository/meme_retrieval.py` with integration tests.
4. `retrieval/pipeline.py` (flow, cache, circuit breaker, degraded paths) and `retrieval/tools.py`, with fake-service tests.
5. Settings, the new shared schemas, generated types, the service, the router, the `lifespan` wiring and warm-up task, `backend_api.md`, the Dockerfile copy and docker check.
6. Evaluation extension: the `fused` retriever, `tune-weights`, `build-pools --sources fused`.
7. The synthetic load check (test database only).
8. Controller steps: run `fused` with the defaults, tune the weights, review and commit them, build the round-3 pools, the user judges, import, final baseline and threshold check, the latency measurements, then the addendum, tracker, `CLAUDE.md` and review report.
