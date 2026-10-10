# P7 decision memo: evidence

Scripts and printed results behind `../2026-10-09-p7-agent-layer-decision-memo.md`. These are one-off analysis scripts, not product code: they take their file paths from the command line and were run from the repository root with the read-only database role (`APP_ENV=general`, `DATABASE_URL` = the `DATABASE_URL_READONLY` of `environments/.env.general`, `PYTHONPATH=.`) and a local Ollama. Nothing here writes to a database. The raw rankings file (`p7_rank50.json`, 160 KB of database-local image ids) is not committed; the scripts regenerate it.

| Step | Script | What it does | Output |
|---|---|---|---|
| 1 | `p7_collect.py <out.json> <parse-model> 50` (uses `p7_pipeline.py`) | Runs the production fused pipeline on the reviewed gold queries, top 50 each, with long timeouts and the parser circuit breaker disabled (a busy GPU cannot degrade the run; latency only) | rankings and per-query meta |
| 2 | `p7_analyze.py <rankings.json> <rows.json>` | Where the judged-relevant memes land in the top N, oracle re-rank ceilings, per language and difficulty, weak-query list | `results.txt` (first block) |
| 3 | `p7_parse.py <scratch dir> <parse.json>`, then `p7_corr.py <rows.json> <parse.json>` | Parses all reviewed queries, scores them against the expected profiles, correlates parse quality with ranking quality, attributes weak queries | `results.txt` (second block) |
| 4 | `p7_rerank.py <rankings.json> <out.json> 20 10` | Pointwise LLM re-rank probe (`qwen2.5:7b-instruct`, top 20, 10 queries per language), scored on judged items only | `results.txt` (third block) |
| - | `p7_state.py` | Profile and vector state of the gold corpus (confound check: unchanged since 2026-10-04) | - |

`results.txt` was produced on 2026-10-09. The gold files, tuned weights and profile data it depends on are the ones committed at that date; rerunning on another day can differ for the reason given in the memo (finding 5).
