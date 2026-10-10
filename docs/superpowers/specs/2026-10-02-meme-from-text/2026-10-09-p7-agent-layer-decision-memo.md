# P7: Agent layer — decision memo

Status: done
Decision: accepted on 2026-10-10: P7 is not built now; the deterministic pipeline stays (ADR-001, approach B). Reopen only under the conditions at the end of this memo.
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/requirements.md (OQ-9), decomposition.md (P7, `rerank_with_llm`), tracker.md (P7 "not started, gated")
Follow-ups: none

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Evidence: `p7-evidence/` (scripts and printed results, see its README).

## Question

OQ-9: "Does the agent layer (P7) justify itself after P3-P6 are measured? Candidates: query rewriting, multi-step refinement, asking a clarifying question." The standing answer was "Defer. Decide after the gold set shows where the pipeline fails." This memo is that decision, made from the gold set (60 queries, EN/RU/ES, 1,500-image corpus, 1,455 profiled), the production fused pipeline (P3 parser, P4 retrieval, tuned weights) and one probe of the only agent component with an observable upside, an LLM re-rank.

## Recommendation

**Do not build P7 now.** No agent component earns its cost on the evidence we have. Spend the effort on the things the evidence points at (below), and reopen P7 when one of the stated conditions holds. Concretely:

1. Keep the deterministic pipeline (ADR-001, approach B). The tool interfaces from P3/P4 already exist, so nothing is lost by waiting.
2. Pin and record the model runtime, then re-baseline (finding 5). Without that, no LLM-based change can be measured reliably.
3. Improve the data and the parse before adding a judge: profile quality (emotion and stance F1 are 0.375 and 0.278) and parse robustness are where the failures start.
4. Let real ratings accumulate (P5b) and export them with `export-ratings`; they are the cheapest way to grow the gold set and to measure any future judge on real usage.

## Evidence

All numbers are from `p7-evidence/results.txt`, produced on 2026-10-09 against the `general` database (read-only role), `qwen2.5:7b-instruct` as parser, the committed tuned weights. Method notes follow each finding; the caveats section lists what the numbers cannot say.

### 1. Where the pipeline stands

- nDCG@10 is 0.450 on today's run (stored baseline `fused`, 2026-10-08: 0.526; see finding 5 for why they differ). Latency is not the problem: retrieval p50 124 ms and p95 264 ms, parse p50 717 ms and p95 968 ms, embed 16 ms (stored baseline, uncontended GPU).
- For the product's use (a person picks one meme from the first results) the picture is better than nDCG@10 suggests. The first judged-relevant result is at rank 1 for 29 of 60 queries, within the top 3 for 49 (82%) and within the top 10 for 59 (98%). Judged-relevant memes per query: median 10 (min 4, max 19), so nDCG@10 mostly measures how many of the several good memes reach the top ten, not whether there is a good one.
- No language or difficulty class is an outlier: nDCG@10 is 0.455 / 0.444 / 0.451 for EN / RU / ES and 0.428 / 0.424 / 0.522 for easy / hard / ambiguous (ambiguous queries do *not* do worse, which weakens the case for clarifying questions).

### 2. Where it fails

- 15 of 60 queries score below 0.30. Attributed with the parser's own agreement with the expected profile (macro-F1 per query): **8 are parse-driven** (macro-F1 below 0.40), **5 are ranking-driven** (parse fine, most judged-relevant memes sit in the top 50 but not the top 10), **2 are neither** (relevant memes mostly outside the top 50).
- Across all 60 queries, parse macro-F1 and nDCG@10 are essentially uncorrelated (Pearson r = -0.03; the best third by parse quality has *lower* nDCG than the worst third: 0.412 against 0.465). So even a perfect parse would not obviously fix ranking, and the weak queries are a mix of causes.
- The data limits the ceiling: only 65% of the judged-relevant memes appear anywhere in the top 50 (43.5% in the top 10). Parse axis F1 against the hand-written expected profiles: topic 0.83, tone 0.50, emotion 0.375, stance 0.278. The emotion and stance vocabularies are where the system and the expected readings disagree most.

### 3. Headroom for an agent layer

An oracle that reorders the top N by the human grades gives the upper bound of any re-ranker over that window (nDCG@10; judged items only count, unjudged count as 0):

| Window N | 10 | 20 | 30 | 50 |
|---|---|---|---|---|
| Oracle nDCG@10 | 0.62 | 0.72 | 0.75 | 0.80 |
| Share of judged-relevant memes in the top N | 43.5% | 55% | 60% | 65% |

The ceiling is large (0.45 to 0.72 with a perfect judge over the top 20) and the per-language and per-difficulty ceilings are all within 0.69 to 0.83, so there is something a good enough judge could gain. Query rewriting and clarifying questions cannot be bounded this way: the parser already rewrites the message into an English situation phrase plus axis values, and the weak queries above show a mixed picture, not a rewriting gap.

### 4. Can a local LLM be that judge? (probe)

A pointwise re-rank of the top 20 with the already-loaded `qwen2.5:7b-instruct` (prompt: message plus the candidate's situation, use-when and axis values, answer 0/1/2), on 30 gold queries (10 per language), scored on judged candidates only (unjudged ones dropped from both lists so the pooling bias cannot favour either side):

- Judged-only nDCG@10: baseline 0.508, LLM re-rank 0.537 (+0.028, standard error 0.012), oracle 0.74. The LLM re-rank is better on 19 queries, worse on 6, equal on 4 (29 queries with at least 3 judged candidates): a small, consistent gain that captures about 12% of the available headroom.
- Agreement with the human grades is poor: exact 44% over 322 judged candidates and 55% on relevant versus not relevant. Of the 218 candidates it scored "not a fit", the owner had graded 112 (51%) acceptable or great; of the 98 it scored "great", only 31 (32%) were great and 34 (35%) were graded wrong (the confusion matrix is in `p7-evidence/results.txt`). It is a weak judge.
- Cost: 20 model calls per search; about 1 s per call under the contended GPU of the run, so roughly 20 s per query serially. Even at 0.3 to 0.5 s per call on an idle GPU that is 6 to 10 s added to a pipeline whose total is now about 1 s.

### 5. Measurement is noisy (a finding in its own right)

Between the stored baseline (2026-10-08) and today the same code, weights, data (profiles and vectors unchanged since 2026-10-04) and model digest (`845dbda0ea48`) produced different rankings for 25 of 60 queries and a lower nDCG@10 (0.526 to 0.450). The cause is the parser: its structured reading of the same message changed for some messages (for example "Guess who got the promotion!" was read as amusement/mock/sarcastic on 2026-10-05, as jealousy/complain today). Inside one session the parser is self-consistent (5 of 5 identical readings per message), the aggregate parse quality is stable (macro-F1 0.497 today against 0.498 stored), and Ollama is at 0.35.0. Single queries flip, and the ranking follows. Practical consequence: a difference of a few hundredths of nDCG@10 between two runs on different days is not evidence, and an LLM layer on top would add one more source of this variation.

## Assessment of each candidate

| Candidate | Evidence for | Evidence against | Verdict |
|---|---|---|---|
| Query rewriting | 8 of 15 weak queries are parse-driven | The parser already rewrites; parse quality barely correlates with ranking (r = -0.03); parse readings drift with the runtime | Not justified. Cheaper levers: parse prompt and model choice, pinning the runtime, the editable reading in the UI (P5) |
| Multi-step refinement | none measured | The UI already lets the owner edit the reading and re-run retrieval only (P5); no gold-set signal for a model-driven loop | Not justified |
| Clarifying question | none | Ambiguous queries are not worse than the rest (nDCG@10 0.522 against 0.428 and 0.424) | Not justified |
| LLM re-rank | Oracle ceiling 0.72 over the top 20 | The 7B judge gains +0.03 of the +0.23 available, agrees with humans 44% of the time, and costs seconds per query | Not justified now. The only candidate worth revisiting, with a stronger judge or a trained one |

## Caveats (what these numbers cannot say)

- Judged items beyond rank 10 come from other retrievers' pools plus round 3 (the fused top 10), so ranks 11 to 50 are mostly unjudged. The oracle ceilings count only judged items, so they are *lower* bounds on true headroom; the probe's judged-only scoring avoids the bias but shrinks each list (some queries had only 2 to 5 judged candidates in the top 20).
- 60 queries and a single rater: a difference below about 0.03 nDCG@10 is within the noise shown in finding 5. The probe's +0.028 has a standard error of 0.012 on 29 queries, but it was measured under one runtime state.
- The probe used one prompt, one model and no tuning. A better prompt, a larger model or fine-tuning could do better; this memo only says the obvious cheap version does not pay.
- The probe and the headroom analysis ran with long timeouts and the circuit breaker disabled (the GPU was shared with other work during the run, which made the stock 5 s parse time out). That changes latency only; the rankings measured are the production pipeline's.
- Expected profiles are the owner's readings of the messages, not ground truth about what a good parse is.

## What would change the recommendation (reopen conditions)

Reopen P7 (starting with LLM re-rank) when any of these holds:

1. A judge with at least about 70% binary agreement with the owner's grades on a held-out sample (a larger local model, or one fitted on exported ratings) and a measured gain of at least 0.05 nDCG@10 on judged-only scoring across two runs.
2. Enough real ratings (P5b) to evaluate on real usage instead of the 60 gold queries (suggested: 300 or more rated pairs across at least 50 distinct messages).
3. A product need that retrieval cannot meet, such as multi-turn conversation (the current scope is one message in, ranked memes out).

## Cheaper next steps this memo recommends instead

1. Pin the runtime for evaluation (Ollama version in the evaluation README and in each stored baseline), re-run the baselines and the parse evaluation, and add a "same-session reproducibility" check to the evaluation harness.
2. Profile and vocabulary quality: review the emotion and stance disagreements between the parser and the expected profiles (F1 0.375 and 0.278); some may be vocabulary or definition issues rather than model errors. The P5c curator tools now make corrections possible.
3. A deeper judging round for ranks 11 to 30 of the weakest 15 queries (about 15 x 20 pairs) would turn the lower-bound headroom into a measured number before any further investment.
4. Complete the `general` profile backlog (1,640 of 32,328 profiled): the page and every later experiment need real coverage.
