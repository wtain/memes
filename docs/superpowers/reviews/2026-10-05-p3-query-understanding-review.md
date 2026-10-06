# P3 query understanding: implementation review

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-05-p3-query-understanding-design.md`
Plan: `docs/superpowers/plans/2026-10-05-p3-query-understanding.md`
Process: subagent-driven (six code tasks plus a task 8 added after the measurement), a task review each, one final whole-branch review on the most capable model, one fix wave with one scoped re-review (a fix round for the Task 2 client as well), then the live steps by the controller and the user. One review iteration, as required by CLAUDE.md.

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `parse/` with the contract of decision 1, passing its tests; imports only stdlib and `taxonomy` (import guard) | Met | `parse/` modules `config`, `schema`, `answer`, `prompt`, `language`, `client`, `cache`, `parser`, `tool`; `tests/parse/test_pure_imports.py`; `tests/parse` 117 passed |
| 2 | The parser never raises, degrades in every failure case, never logs the text | Met | `QueryParser.parse` wraps everything (cancellation is not swallowed); tests for timeout, unavailable, bad response, invalid twice, empty input, unexpected exception, concurrency-gate timeout, and a caplog test with a marker string on every path |
| 3 | `Dockerfile.backend` copies the parser and the taxonomy and the image check passes | Met | `COPY parse`, `COPY taxonomy`, `COPY batch/data/taxonomy` plus the `.dockerignore` re-include rules; `tests/docker` 3 passed (real image build, `import parse` and `load()` inside the image) |
| 4 | `parse-eval` and `parsed_axis_match` run on the 60 reviewed queries for every candidate and the results are stored | Met with deviation | `evaluation/gold/parse/qwen25-7b.json`, `qwen2-latest.json`; baselines `parsed_qwen25-7b`, `parsed_qwen2-latest`; the larger model was not run (not needed, nothing pulled) |
| 5 | The chosen model satisfies: degraded at most 2%, language accuracy at least 95%, p50 at most 2.0 s and p95 at most 5.0 s, `parsed_axis_match` at least 0.9 times the gold-profile `axis_match`, every language at least 0.85 of English | Met with a caveat | degraded 0%, language accuracy 100%, p50 655 ms, p95 891 ms; `parsed_axis_match` nDCG@10 0.468 (hit@10 1.000, 99% judged after the round-2 judgments) against 0.295 for the gold-profile `axis_match` (a lower bound: only 47% of its top ten is judged), so the 0.9 criterion is met against the measured value but not rigorously settled (a round 3 judging the rest of the gold-profile top ten would settle it); language ratios 1.19 (es) and 1.08 (ru) |
| 6 | `parse.model` set in general and metal; `it` stays unconfigured and returns the degraded result | Met | `qwen2.5:7b-instruct` in both overlays, none in `it` (checked per `APP_ENV`); the tool returns a degraded result with fallback `unavailable` when no parser is registered (test) |
| 7 | The addendum records the numbers, the choice, OQ-3, proposed regression floors and the handoff notes for P4 | Met | spec addendum "result (2026-10-06)" |
| 8 | All test roots pass | Met | each root as its own command on the final branch: `tests/parse` 117, `tests/evaluation` 111, `tests/taxonomy` 83, `tests/ai` 66, `batch/tests` 268, whole `tests/integration/` 513, `Backend` 360, `tests/docker` 3 (real image build) |

## What was fixed (final review and fix wave)

- `parse.num_ctx` 4096 against 8192 for the same model in `meme_profiles` (Ollama would reload the model on every switch): aligned to 8192.
- A cold model could make the first parses degrade and bias the measurement: `QueryParser.warm_up` (never raises, caches nothing), used by `parse-eval` and `parsed_axis_match`.
- Nothing registered the parser in a running backend: `get_query_parser()` is now called in the `lifespan` (a failure does not stop the app).
- The semaphore bound to the first event loop (silent degradation under a second loop): created per running loop.
- The message-marker stripping removed too much text: tightened to `<\s*/?\s*message\b[^>]{0,20}>`.
- A log line on the tool's no-parser path; missing tests (a third parse waits for a slot, the profile item shape, version sensitivity for all ten prompt parts); unused imports and a flaky timing bound.
- Task 2 (fix round): `http.client.HTTPException` (IncompleteRead, BadStatusLine) escaped untyped from the client; the refused-connection test was tightened.

## What was not fixed (acceptable, explained)

- No hard `asyncio.wait_for` around the model call: a slot-releasing timeout would let abandoned requests pile up in Ollama; overruns are visible as `latency_ms` above the budget (a real total cap comes with an httpx swap).
- On Windows a closed local port takes about 2 s to fail (reported as `timeout` or `unavailable`); a circuit breaker is a P4 handoff note.
- `guess_language` (degraded results only) is stricter than the spec text (two Spanish function words, shorter list).
- A situation over 160 characters is rejected (retry) instead of truncated; the measured retry rate was 0 of 60.
- Identical concurrent queries are not deduplicated; a cache hit keeps the original `attempts` and `fallback`.
- Minor items: duplicate source names in `build-pools --sources` are collapsed silently; a forged message marker padded beyond 20 characters is not stripped (whitespace is collapsed before cleaning and the output is schema-constrained); the packaging test does not assert the position of the new `.dockerignore` lines.

## What was intentionally ignored

- The larger-model comparison (the installed models already passed; the user's choice was recorded and nothing was pulled).
- Per-query tuning of the prompt: the stance axis is the weakest (F1 0.28), but the retrieval result does not call for work now.

## Process incidents (recorded for the next project)

- Two implementer subagents wrote into the **main checkout** instead of their worktree (Task 5: stray untracked copies; Task 8: a commit on the spec branch). Both were recovered before the merge (cherry-pick with two conflicts resolved, stray files removed, the spec branch reset to its plan commit). Future dispatches should state the worktree path as the only allowed write location and the reviewer should check `git status` of the main checkout.
- Round-1 judgments had pooled the other retrievers, so a parser's own top ten was only 16% judged: a round 2 was needed before the retrieval criterion could be read at all.
