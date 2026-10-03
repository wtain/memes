# P2 meme profile extraction: implementation review

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md`
Plan: `docs/superpowers/plans/2026-10-03-p2-meme-profile-extraction.md`
Pilot: `docs/superpowers/reviews/2026-10-03-p2-pilot-review.md`
Branch: `worktree-p2-profiles`, base `9e28fa9` (the plan commit). One review iteration (CLAUDE.md): every task was reviewed by a fresh reviewer and, where findings were Important, one fix round with a scoped re-review; then one whole-branch review on the most capable model, one fix wave for its Important findings, a scoped re-review of the code fixes and a docs pass.

## Verification evidence (final head)

| Check | Result |
|---|---|
| `tests/integration` (whole root, `ocrdb_test`) | 490 passed |
| `tests/taxonomy` | 83 passed |
| `tests/ai` | 66 passed |
| `batch/tests` | 261 passed |
| `Backend` pytest | 356 passed |
| Frontend | not run: P2 changes no frontend file |
| Migration on a scratch database built from the previous head's models | upgrade, downgrade, re-upgrade pass; `alembic check` prints "No new upgrade operations detected" |
| Live rollout (with the user's consent, controller only) | migration `d5a9f3c1e7b2` applied to metal, general and it; both jobs exit cleanly on `it`; a 200-image slice on `general` (run twice) saved 194 profiles, 6 empty answers became failures, 4 unsupported-extension images were skipped; embeddings job embedded 193 situations and a second run did nothing; nearest-neighbour query on the vectors looked sensible |

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | The migration applies cleanly to a scratch copy of each of metal, general and it, and downgrades; the three models match the P1 table design | **Met with deviation** | One faithful scratch schema (built from the models at the previous head, stamped `c4d8e1f2a5b7`) proved upgrade, downgrade, re-upgrade and a clean `alembic check`; the migration was then applied to the live metal, general and it databases with the user's consent. A separate scratch copy per environment was not made |
| 2 | Both jobs run on `general` in resumable slices, are run-tracked, appear in `/admin/batches`, honour `--limit` / `--reset` / `--retry-failed`, and `description_pipeline` runs both after the describe step | **Met with deviation** | Two real 200-image slices on `general` (second after `--reset`); registry entries and `main()` contract tested; pipeline step order and `needs` tested. Not done: an end-to-end `description_pipeline` run and a click in the admin UI (the user chose to defer the full run; the wiring has unit tests) |
| 3 | A stale profile is re-extracted; a human-verified profile is never rewritten and is reported as needing review; a rejected description re-extracts that image on the next run | Met | `tests/integration/test_build_meme_profiles.py` (fingerprint change after a rejected description, human-verified rows kept and counted in coverage, version and model staleness), `test_meme_profiles_repository.py` |
| 4 | On `it` both jobs exit successfully with "not configured" and zero work | Met | Unit tests plus a real run on `it` (exit code 0 for both jobs) |
| 5 | The pilot ran, the user's ratings are recorded, the chosen models are the defaults, throughput and a backlog estimate per environment are in the addendum | Met | Pilot review and spec addenda: `qwen2.5:7b-instruct` (mean rating 0.86 of 2), vision model unchanged; measured rollout throughput 0.75 s per image at concurrency 2 supersedes the serial pilot estimate |
| 6 | `coverage()` numbers are in the run stats; `docs/data-flow.md` and `CLAUDE.md` are updated; the full `tests/integration` root, `batch/tests`, `tests/ai` and Backend suites pass | Met | Stats carry a post-run `coverage` snapshot; docs updated in Task 6, the empty-answer fix and the docs pass; all roots green above |
| 7 | P4 and P5 can start from this spec and the P1 spec alone | Met after the fix wave | The whole-branch review found the spec out of date with the build; the as-built, rollout and "notes for P4 and P5" addendum fixes that (what P5 must pass to `save_human_profile`, why the backend cannot compute staleness yet, how to treat confidences) |

## Deviations from the plan and spec

1. `PROMPT_VERSION` is derived automatically from a hash of every static prompt part (instead of a pinned-hash test); a test perturbs each part.
2. Prompt-injection hardening beyond the plan: `_clean` removes any `meme_data` tag to a fixed point (case, whitespace and nested variants), and every static prompt string, including the retry message, is part of the version hash.
3. Unsupported-extension vision images are dropped before `--limit` is applied (the plan's version starved the run).
4. An all-empty answer is a failed extraction (found on real data: 3% of the first slice).
5. Failure rows carry a context `[fingerprint|prompt version|model]` and expire when it changes; run-level errors (connection, model missing) are not recorded and three in a row stop the run (the spec said failures are skipped until `--retry-failed`; the whole-branch review showed that could never recover through the pipeline or admin paths).
6. The pilot tool keys vision runs separately, hides error text on the blinded sheet and escapes every model-supplied value (the plan's single-key design mixed text and vision runs of the same model and could unblind the sheet).
7. OCR lines are ordered by bbox reading order, not by id (ids are random UUIDs); the plan already did this, the spec text did not.
8. The plan's scratch-schema helper needed `CREATE EXTENSION pg_trgm` (existing models carry trigram indexes); fixed in the plan.
9. The vision model stays `qwen2.5vl:7b` without a pilot winner, because it was the only vision candidate (its 6-of-10 valid runs reflected two undecodable files). Tasks ran in plan order; Task 7 and Task 8 were split between a subagent (code) and the controller with the user (live steps).

## What was fixed (review findings)

- Task 2: data-block escape could be bypassed with a nested closing tag; `PROMPT_VERSION` omitted several prompt strings; the version test only exercised dummy tuples.
- Task 4: unsupported-extension images starved `--limit`; uncounted `save_profile` False; vacuous assertion; missing tests.
- Task 7: blinded sheet could leak model names through error text; unescaped confidence; text and vision runs mixed under one key; non-atomic result writes.
- Real data: empty answers saved as profiles.
- Whole-branch review: failure rows never expired; dead Ollama could mark the backlog failed; `coverage()["failed"]` counted non-active images; a fragile exact-equality test; spec out of date; P5 handoff; rollout numbers; two documentation nits.

## What was not fixed (explained)

- Deferred minors judged acceptable by the whole-branch review: `from_validated` treats every list key as an axis (only controlled axes occur today); `ProfileMeta.human_verified` is ignored by the save methods (documented for P5); unvalidated source or duplicate keys surface as a database error at flush; no row lock on the human-verified check (single-job batch use); unterminated `<meme_data` and lookalike tags; structural prompt joiners outside the version hash; uncapped retry reason; `truncate_at_word` breaks only on a plain space; `_reading_key` does not catch `KeyError` for dict-shaped points; no test over 1,000 ids; `gather` without `return_exceptions` (a database error leaves siblings running against a closing session); `zip` truncation and no per-chunk database error handling in the embeddings job; stale temp file left by a failed atomic write; differing default seeds per pilot command; the OCR no-bbox tie-break by uuid string; an embeddings dimension mismatch after a future model change crashes outside the per-chunk try; `SITUATION_GUIDE` does not state the 160-character limit (adding it would stale the 194 real profiles for a retry-only gain).

## What was intentionally ignored

- Running the full backlog and an end-to-end `description_pipeline` run: deferred by the user until P6's gold set shows whether the prompt needs tuning (a prompt change marks every profile stale).
- The skew in the first 200 profiles (stance `mock` 70%): a prompt and gold-set question for P6, recorded for P4 in the addendum.
