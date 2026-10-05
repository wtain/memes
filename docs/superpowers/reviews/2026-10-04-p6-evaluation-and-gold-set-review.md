# P6 evaluation and gold set: implementation review

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p6-evaluation-and-gold-set-design.md`
Plan: `docs/superpowers/plans/2026-10-04-p6-evaluation-and-gold-set.md`
Process: subagent-driven (seven code tasks, a task review each, one final whole-branch review on the most capable model, one fix wave with one scoped re-review), then the live steps by the controller and the user. One review iteration, as required by CLAUDE.md.

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `evaluation/` with the CLI; pure modules import only stdlib and `taxonomy` (import guard) | Met | `evaluation/__main__.py` commands `build-corpus`, `build-pools`, `sheet`, `import-judgments`, `run`, `compare`, `list-baselines`; `tests/evaluation/test_pure_imports.py` (AST guard) passes; `retrievers/__init__.py` is empty |
| 2 | Corpus of about 1,500 images, at least 95% profiled; 60 reviewed queries meeting the lint; about 700 judgments | Met | `corpus.json` 1,500 images, 1,455 profiled (97%); `queries.json` 60, all `reviewed: true`, `check_queries` and `lint_query_set` empty; `judgments.json` 718 |
| 3 | Baselines for random, lexical_and, lexical_or, situation_embedding, axis_match with gold hashes and profile state; report with overall, per-language, per-stratum, per-difficulty, judged@10 and pooling-bias note | Met | `evaluation/gold/baselines/*.json` (five files); reports in `evaluation/work/reports/` (local) |
| 4 | `compare` works and refuses mismatched gold hashes | Met | live `compare random axis_match`; refusal covered by `test_report.py` and `test_cli.py` (exit 1, names the differing key) |
| 5 | P2 jobs accept `--ids-file` and leave other images untouched | Met | unit and integration tests for both jobs, including `--reset` scoped to the listed images; the real run selected 1,491 images from the ids file (the corpus minus profiles that were already current); that no image outside the file was written was not separately queried |
| 6 | Addendum records the baseline numbers, answers the two gates with evidence, proposes P4 thresholds | Met with deviation | spec addendum "result (2026-10-05)"; the multilingual-embedding comparison (OQ-4) was not run (needs a model pull and the user's approval); the gap for the English embedding is measured |
| 7 | All test roots pass | Met | `tests/evaluation` 90, whole `tests/integration` 509, `batch/tests` 268, `tests/taxonomy` 83, `tests/ai` 66, `Backend` 356, each its own command |
| 8 | P3 and P4 can start from this spec alone | Met | interface (`Retriever` protocol), gold formats, baseline numbers and proposed thresholds are in the spec and `evaluation/gold/README.md` |

## What was fixed (final review and the fix wave, commit 90f6daa)

- `build-pools` overwrote `pools.json` (lost the private sources of earlier rounds, breaking the pooling-bias note and the append-only rule): pools are now per round (`pools-r<N>.json`), refused without `--force`, and `run` merges all rounds.
- Corpus drift check (missing, inactive, hash changed) and an APP_ENV versus corpus environment refusal, both missing from the spec's decision 2.
- `parse_*` raised `KeyError` on malformed gold files: now `GoldError` naming the entry.
- `compare` missed an exact 0.1 drop because of float noise: the delta is rounded.
- `build-corpus` defaults ignored `--gold-dir`.
- Thumbnails turned transparency black: composited onto white.
- The sheet had no skip control: added.
- Embedding coverage and stale digest warnings; empty ids file rejected; under-delivery and failed-thumbnail warnings.

## What was not fixed (acceptable, explained)

- The judging sheet shows only the meme image (inputs not shown at all, spec says hidden by default) and thumbnails are 400 px (spec: about 320 px): blinding is stricter than the spec and 400 px is more legible.
- `judged@10` divides by the number of results returned, not by 10 (spec wording ambiguous; documented).
- Commit `e7824ee` carries `Co-Authored-By: Claude Haiku 4.5` instead of Sonnet 5.5 (history not rewritten).
- Minor deferred items: `report.py` imports the private `gold._read`/`_write`; `git_commit()` uses the current directory; `Pool.sources` is a mutable dict; the random retriever has no id tie-break (its scores strictly decrease); `\w+` tokenisation of the OR retriever splits hyphens and apostrophes; `MAX_IDS` is checked after de-duplication; `select_corpus` under-delivers silently when `ocr+description` cannot cover a shortfall (the CLI now warns); `build-corpus` does not create the output directory (found live, directory created by hand); `<!--` is not neutralised in the sheet's metadata block (only the operator's rater name goes there).
- No handler-level test for the database commands (`build-corpus`, `build-pools`, `run`); they were exercised live against the read-only role instead (corpus draw, 720-candidate pools, five baseline runs).

## What was intentionally ignored

- The sheet's JavaScript was not exercised by an automated browser test; it was exercised by the user judging 718 pairs and exporting three files that imported without a single refusal.

## Findings of the live run worth knowing

- The `general` library has descriptions for almost every image, so the strata `ocr only` (26) and `no usable text` (2) are tiny.
- Two thirds of every retriever's top ten was never judged (`judged@10` 0.27 to 0.34), so all scores are lower bounds; the proposed P4 thresholds therefore require a second judging round that pools P4's own results.
- The English bge-large embedding fails for raw Russian messages (nDCG ratio to English 0.15, Spanish 0.73), while the profile route (`axis_match`, English profile) is language-robust (ru 1.01, es 0.89).
