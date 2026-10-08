# Evaluation gold data (meme from text, P6)

Design: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p6-evaluation-and-gold-set-design.md`.
Tool: `python -m evaluation --help`.

## Files

| File | What it is |
|---|---|
| `corpus.json` | The frozen evaluation corpus: 1,500 active `general` images (seed 7), one per duplicate cluster, flagged and unsupported-extension images excluded. Each entry has the image id, the `content_hash` at draw time and the input stratum. |
| `queries.json` | 60 test messages (20 EN, 20 RU, 20 ES) with tags (length, difficulty), an optional `pairId` for the six near-translated situations, and an `expectedProfile` (English situation plus taxonomy keys for emotion, stance, topic, tone: the reply wanted). Only queries with `reviewed: true` take part in runs. |
| `judgments.json` | Human grades per (query, image): 0 wrong, 1 acceptable, 2 great, with rater, date and round. |
| `baselines/<label>.json` | Stored results of a retriever run: metrics overall and per language, difficulty, length and input stratum, the gold-file hashes, the retriever config and the profile state of the corpus. No file names. |
| `parse/<label>.json` | Stored evaluations of the query parser (`python -m evaluation parse-eval`): language accuracy, degraded rate, per-axis agreement with the expected profiles, latency, and the parsed keys per query (no file names). |

Not committed (kept in `evaluation/work/`, gitignored): the candidate pools (`pools-r<N>.json`, they hold the private pool sources and library file names), the judging sheets and thumbnails, the corpus ids file for the P2 jobs.

## Rubric (the same text is at the top of every judging sheet)

Judge only: is this a good meme to send back as a reply to the message? The grade is about fit, not about how funny or well made the meme is. 2 = great reply (you would send it), 1 = acceptable (works, not ideal), 0 = wrong (does not fit, or you cannot tell what it means). Leave a meme ungraded (or pick "skip") to skip it. Ambiguous messages accept several kinds of reply.

Unjudged pairs count as grade 0 in the metrics (the standard pooling assumption). The report prints `judged@10`, the share of a retriever's top results (up to 10, of the results it returned) that has a judgment, so the cost of that assumption is visible; it divides by the number of results returned, not by 10.

## Validity

The image ids are database-local UUIDs of the `general` environment. The gold data is only meaningful against that database. `evaluation run` and `build-pools` warn when corpus images are missing, inactive or have a changed `content_hash`, and refuse when `APP_ENV` differs from the corpus environment. Absolute scores are optimistic compared with the full library (a 1,500-image sample); the gates are relative (beat the best baseline, do not regress).

## Rules for extending and running

- **Read-only database.** Database commands require `APP_ENV` and `DATABASE_URL` set to the read-only URL (role `ocr_readonly`) in the shell before starting, and refuse otherwise. The CLI never calls `config.settings.load_env()` (it would replace the read-only URL with the write-capable one). Profiling the corpus is done with the P2 jobs (`python -m batch.build_meme_profiles --env general --ids-file evaluation/work/corpus.ids`, then `python -m batch.build_meme_profile_embeddings --env general --ids-file evaluation/work/corpus.ids`), not with this CLI.
- **The corpus is frozen.** `build-corpus` refuses to overwrite an existing `corpus.json` without `--force`. Changing it invalidates every judgment and baseline.
- **New queries** are appended with new ids (`q061`, ...), marked `reviewed: false` until the user reviews them. Check them with `evaluation.gold.check_queries` and `lint_query_set` (the 60-query drafting rules; relax the quotas explicitly when growing the set).
- **New judging rounds** use `build-pools --round N` (writes `evaluation/work/pools-rN.json`, never overwrites a round without `--force`); already judged pairs are never asked again; `sheet --round N` renders the sheets; `import-judgments` merges the exports (a later grade for the same pair overwrites an earlier one and is reported). `build-pools` takes `--sources`, `--random` and `--depth`; a round that only judges the unjudged top ten of a parser uses `--sources parsed_axis_match --parse-model M --random 0 --depth 10 --per-query 10`.
- **Baselines** are compared only against baselines made with the same gold hashes (`compare` refuses otherwise). After judgments change, re-run the retrievers you want to compare.
- A prompt or model change for the P2 extractor is evaluated by re-profiling the corpus with `--reset --ids-file` (about 20 minutes), re-embedding, running the retrievers under a new label and comparing.

## Known deviations from the spec

- The judging sheet shows only the meme image (no input texts, not even behind a toggle), and thumbnails are 400 px instead of about 320 px.
- The `general` library has descriptions for almost every image, so the input strata `ocr only` (26) and `no usable text` (2) are tiny; their shortfall went to `ocr+description`. Per-stratum numbers for those two strata rest on very few images.

`tune-weights` collects the fused pipeline's candidates once and searches the fusion weights with cross-validation (`tuning/<label>.json`); `run --retriever fused --parse-model M` evaluates the production pipeline restricted to the corpus.

## Round 3 (P4) and the fused retriever

Round 3 pooled the unjudged members of the tuned `fused` pipeline's top ten (`build-pools --round 3 --sources fused --parse-model M --random 0 --depth 10`; 223 pairs, 216 judged, 7 repeats of the same images left unjudged). `python -m evaluation tune-weights --parse-model M --label L` collects the fused pipeline's candidates once and searches the fusion weights with cross-validation (`tuning/<label>.json`, refuses degraded profiles unless `--allow-degraded`); `run --retriever fused --parse-model M` evaluates the production pipeline restricted to the corpus. After a new judging round re-run every baseline with `--force` so all share one gold fingerprint (the stored `parsed_qwen2-latest` baseline predates round 3 and is kept only as history).
