# P4 retrieval, fusion and API: review report

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-07-p4-retrieval-fusion-and-api-design.md` (status done; its addendum holds the results, decisions and handoff notes).
Plan: `docs/superpowers/plans/2026-10-07-p4-retrieval-fusion-and-api.md`.
Branch: `p4-retrieval-fusion-and-api` (forked from the plan commit `68f5fdc`).

## Review process

Subagent-driven: one implementer per task, a task review (spec compliance and code quality) after each, scoped re-reviews of every fix round, one whole-branch review on the most capable model, one final fix wave with its own scoped re-review, then the live steps run by the controller. Every task review and re-review ended with all findings addressed. Nothing in the process ran against the live databases except the controller (read-only role).

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Package, repository, service, endpoint exist as in decisions 1 to 9; pure modules import only stdlib, `taxonomy`, `parse`, `retrieval` (AST test); settings keys exist, a missing key is a startup error | Met. `tests/retrieval/test_pure_imports.py`, `load_config` tests, `search.meme_from_text.*` keys. |
| 2 | Ranked, filtered, faceted, paginated, explained page with the profile; never fails because a model failed; excludes flagged, inactive and profile-less images on the axis path | Met. Pipeline, router and repository tests; degraded smoke test against a closed Ollama port. |
| 3 | Schemas, generated types, `backend_api.md` updated with no CI diff | Met. Four schemas; Python, TypeScript and Kotlin trees regenerated (only real changes committed); `backend_api.md` aligned to the behaviour in the final fix wave. |
| 4 | `fused` on the gold corpus, tuned weights in `settings.yaml` with the cross-validated estimate recorded, round-3 judgments imported | Met. `tune-1` (CV 0.386, in-sample 0.398 against 0.355 for the former defaults), 216 round-3 judgments imported, 0 overwritten. |
| 5 | Approved thresholds hold | Met: nDCG@10 0.526, hit@10 1.000; languages 1.000 / 1.049 / 0.934 of English, all above 0.30; every difficulty and length class above the best baseline. |
| 6 | Retrieval p95 under 300 ms (gold corpus and 25,000-profile synthetic check); end-to-end p50 3 s / p95 8 s | Met: gold corpus p95 234 ms; synthetic p50 107 / p95 132 ms; stage-percentile sum about 0.83 s / 1.2 s. |
| 7 | Addendum, tracker, `CLAUDE.md`, review report | Met. |
| 8 | All test roots pass | Met on the final tree (2026-10-08, each root its own command): `tests/retrieval` 133, `tests/evaluation` 133, `tests/parse` 117, `tests/taxonomy` 83, `tests/ai` 66, `batch/tests` 268, `tests/rules` 144, `Backend` 390, whole `tests/integration` 539 (test database), `tests/docker` 4. |

## What was fixed

- Per-task review rounds: cache and breaker behaviour (degraded results cached 10 s, language hint in the cache key), tool validation, the schema-race and guard strength of the load check, the HNSW settings error handling (no permanent "unsupported" from a transient error), non-finite vector values, tuning objective parity with production (empty ranking when nothing is scorable), cross-validation over unjudged folds, `--folds` validation, a public `config` property.
- Whole-branch review (four Important findings, all fixed): a zero weight on a fallback axis ordered degraded results by id; the latency evidence could not be produced (lexical source outside every measurement, `retrievalMs` incomplete, `FusedRetriever` dropped the meta); database errors could log the user's lemmas through uvicorn; `tune-weights` could tune on degraded profiles. Also fixed: request size bounds, `backend_api.md` mismatches, digest check for `fused`, pipeline build failure handling, embedding dimension check, test gaps, coarse simplex start for the tuning.
- Found by profiling on this machine: a 44 ms stall for requests over 16,384 bytes over the IPv6 loopback (task 8b): compact vector literals, array parameters, one round trip for the HNSW settings; results identical on 36 fixed queries.
- Found on the real data: the lexical source cost 250 ms at p50 and over a second at p95 per request for +0.01 nDCG@10, so it became a fallback (`lexical.only_when_degraded`), plus a 20-token cap; cold models degraded the first evaluation queries, so the evaluation commands now warm both models.

## What was not fixed (acceptable deviations, explained)

- Decision 8: only `search_memes_from_text`, `score_candidates` and `explain_results` are exposed as tools; the finer-grained tool functions of the spec text are covered by `prepare` and `rank`.
- "Digest mismatch at startup logged and counted" is not implemented at startup (the evaluation commands check the digest).
- `warm_task.cancel()` at shutdown is not awaited; a cancelled in-flight warm-up call cannot stop its worker thread, so a graceful shutdown may wait up to the warm timeout. Low impact.
- The parser circuit breaker is soft at the half-open edge (a burst may make more than one probe call) and bypasses the P3 parse cache; documented in the handoff notes.
- Test and style nits recorded in the ledger (vestigial helper branch in a test, unbounded `score` in the schema, `--images 0` in the load script, over-long line).

## What was intentionally ignored

- Round-3 judging left 7 of 223 pairs unjudged (repeats of the same images); fused judged@10 is 0.988, so the effect is negligible.
- The stored `parsed_qwen2-latest` baseline predates round 3 and was not re-run (history only).
- No optimisation of the lexical source itself: after becoming a fallback it is off the healthy path, and the 20-token cap bounds the degraded path.

## Live and operational notes

- Measured on `general` (pgvector 0.8.2, stored embedding model name equal to the configured `bge-large`, 1,638 of 1,640 profiles embedded); only about 5% of the library is profiled, so the profile backlog (board task 158) must run before P5 or rollout.
- The always-on environments will keep `qwen2.5:7b` and `bge-large` loaded after merge (warm-up every 20 minutes); there is no opt-out setting yet.
- Weights were tuned and the final figures measured on the same 60 queries (mildly optimistic).
