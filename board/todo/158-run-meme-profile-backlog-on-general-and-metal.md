# [P2] Run the meme profile backlog on general and metal (about 6.7 h and 3.8 h of Ollama time)

- **Priority:** P2
- **Area:** Meme-from-text epic, data (P2 follow-up)
- **Source:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md` (addendum "as-built, rollout and notes for P4 and P5")
- **Epic tracker:** `docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md`

## Problem

P2 is merged and the migration is applied to metal, general and it, but only 194 meme profiles exist
(a 200-image slice on `general`). `build_meme_profiles` and `build_meme_profile_embeddings` have not been
run over the real libraries. P4 (retrieval) and P6 (evaluation) need profiles for the whole active library.

## Estimated cost (measured on the real slice, concurrency 2, model loaded)

| Environment | Active images | Estimate |
|---|---|---|
| general | 32,328 | about 6.7 h (about 0.75 s per image) |
| metal | 18,088 | about 3.8 h |
| it | 1,422 | out of scope: both jobs exit with "not configured" |

About 4,800 images per hour; a `meme_profiles.max_per_run` of about 3,000 keeps one `description_pipeline` slice near 40 minutes.
The embeddings job afterwards is a few minutes per environment.

## When to run it (decision gate, answered by P6 on 2026-10-05)

The first 200 profiles were skewed (stance `mock` 70%, tone `absurd` 52% and `sarcastic` 30%), and any prompt change
marks every profile stale, so a full run before the prompt is settled would be paid for twice. P6's gold set
(`evaluation/gold/`, baselines in `evaluation/gold/baselines/`, spec addendum "result (2026-10-05)") answers it:
the current profiles carry real signal (`axis_match` over the profiles of the 1,500-image corpus: nDCG@10 0.339,
hit@10 0.867, against 0.006 / 0.05 for random and 0.259 / 0.75 for keyword-OR search), and the skew did not show up as
a retrieval failure. **Recommendation: run it** (the decision is the user's), `general` first in slices, `metal` after
`build_image_descriptions` (OCR-only profiles are the weakest: mean grade 0.25 for `ocr only`, n=12, against 0.73 for
`ocr+description`). If the prompt is tuned later, evaluate the change on the corpus first (re-profile the 1,500 images
in about 17 minutes with `--ids-file`, then `python -m evaluation run` and `compare`); a prompt change after the full
run costs the run again.

## Before the run

- Ollama is up and both models are present: `qwen2.5:7b-instruct` and `qwen2.5vl:7b`; `bge-large` for embeddings.
- `pip check` on `.venv311` is clean enough (CLAUDE.md gotcha: venv rot only shows on a fresh start).
- Start with a slice and read its stats: `python -m batch.build_meme_profiles --env general --limit 200` (set `PYTHONIOENCODING=utf-8`; `DATABASE_URL` for the environment must already be in the shell). Check `error.unavailable`, `error.invalid_output` and `skipped.*`.
- Remember that `--reset` deletes every model-generated profile and every failure row (human-verified profiles are kept).
- On `metal`, consider running `build_image_descriptions` first: only 7,482 of its 18,088 images have descriptions, and OCR-only profiles were the weakest in the pilot (mean rating 0.3-0.4 against about 1.0 with a description).

## How to run

- Detached, in slices (a single foreground tool call is killed after about 10 minutes): `--limit` per run, or set `meme_profiles.max_per_run` and trigger `description_pipeline`, or start a detached `Start-Process` (see `docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md`, recovery step 5).
- Then `python -m batch.build_meme_profile_embeddings --env <env>` (or the pipeline's second step).
- The jobs are resumable: selection is derived from the data, so re-triggering continues where the last run stopped; an Ollama outage stops the run without recording failures.

## Done when

- Coverage on general and metal is close to the number of active images minus images that cannot be profiled (undecodable files, repeatedly empty answers), read from the run stats' `coverage` snapshot or the read-only counts query.
- Every profile with a situation text has a situation embedding.
- The outcome (counts, failures, time per environment) is appended to the P2 spec addendum or the epic tracker.
