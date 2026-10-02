# ADR-003: Retrieval and fusion of per-axis signals

Status: proposed
Date: 2026-10-02
Epic: docs/superpowers/specs/2026-10-02-meme-from-text/README.md
Related: docs/adr/adr-2026-10-01-search-ranking-in-python.md, docs/superpowers/specs/2026-10-01-search-result-ranking-design.md

## Context

Given a query profile and meme profiles (ADR-002), produce one ranked list with explanations, hard filters and facets, stable
pagination, and graceful behaviour when axes are missing on either side. The repository already ranks lexical hits in Python from
settings-driven weights, with a cursor that embeds the score.

## Decision (proposed)

1. **Candidate generation**: union of
   - top-K by `situation` vector similarity (pgvector, HNSW),
   - memes whose controlled-vocabulary values overlap the query's `emotion`, `stance`, `topic`, `tone` values (exact or near by vocabulary similarity),
   - optionally, lexical smart-search hits for the raw text (low weight, FR-14, OQ-6).
   K is a setting, sized from measurement.
2. **Per-axis score** in 0..1 for each candidate: vector similarity (converted from cosine distance) for `situation`, weighted
   vocabulary overlap for the others. Axis missing in the query: not scored. Axis missing or low confidence on the meme: neutral
   score (a configurable constant, not zero) so incomplete profiles are not buried (FR-15).
3. **Fusion**: weighted sum of per-axis scores, weights from `search.meme_from_text.*` in `environments/settings.yaml`
   (missing weight key is a startup error, as for `search.ranking.*`). A weighted sum is chosen over rank fusion (RRF) as the
   baseline because scores here are comparable (all normalised to 0..1) and explanations stay trivial (each axis contributes a
   visible amount). RRF stays an alternative to test in P6 if score scales prove unstable.
4. **Hard filters** (FR-6, FR-17) apply before ranking, as SQL conditions on the normalised axis-value tables. **Facets** are counted over
   the filtered candidate set.
5. **Diversity** (FR-19): collapse members of one duplicate cluster to the best-scoring representative on the first page, with the others
   reachable through the existing cluster view.
6. **Pagination**: the relevance cursor pattern already used by `/api/images?q=` (opaque cursor embedding score and id of the last
   item). Parse results are cached so page 2 does not re-parse.
7. **Explanation**: the response carries each result's per-axis scores and matched meme values, from the same computation, no
   second LLM call.
8. **Fallbacks**: parse failed or Ollama down → `situation` axis is replaced by a vector of the raw text (embedded with the same
   model, requires the translation question of ADR-002 to be settled) and the lexical path, flagged as degraded in the response.
9. **Optional re-rank** (P7 only): an LLM judging the top N against the query. Not part of the baseline and not adopted without
   evidence.

## Alternatives considered

- **All scoring in SQL**: faster for large candidate sets, but the repo already chose Python-side scoring for explainability and
  tuning (see ADR 2026-10-01). Candidate sets here are small (K of a few hundred).
- **Learning to rank**: needs far more labelled data than the gold set will have. Revisit if ratings (FR-18) accumulate.
- **Single combined score from one LLM judgement per (query, meme)**: highest potential fit quality, but cost and latency scale
  with candidates. Reserved for the optional re-rank.

## Consequences

- P4 implements candidate generation, scoring, fusion, filters, facets, the cursor and the endpoint, and exposes the stages as
  tools.
- P6's evaluation reads weights from settings, so tuning is a configuration change.
- The weights, K, the neutral score, and the lexical weight start with documented defaults and are tuned on the gold set, with the
  tuned values recorded in the P4/P6 spec, not guessed here.

## Revisit when

- Evaluation shows weighted sum loses to rank fusion on the gold set.
- Candidate generation misses acceptable memes (recall problem) rather than ranking them badly.
