# P6: Evaluation and gold set — Design

Status: done
Plan: docs/superpowers/plans/2026-10-04-p6-evaluation-and-gold-set.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md (project P6)
Follow-ups: none yet (P3 query understanding and P4 retrieval plug into the interface this spec defines)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Inherited contracts: P1 (`2026-10-02-p1-taxonomy-and-data-model-design.md`) and P2 (`2026-10-03-p2-meme-profile-extraction-design.md`, including its as-built addendum and "Notes for P4 and P5").
Requirements covered: FR-18 (format compatibility only), FR-24, NFR-6, OQ-5; it also provides the evidence for OQ-3 and OQ-4 and the gate for board task 158 (the full profile backlog run).

User decisions for this project (2026-10-03): the first gold set has **60 test messages** (20 each in EN, RU, ES) **drafted by Claude and reviewed by the user**; measurement and judging happen inside a **frozen sample of about 1,500 `general` images**; the user grades **about 12 candidates per query (about 700 judgments, roughly 1-1.5 hours)** on a 0/1/2 scale in a blinded HTML sheet; the work is an **`evaluation/` package with committed gold data**, with four baseline retrievers and a stored baseline plus compare command.

## Problem

Nothing in the system can say whether a meme is a good reply to a message. Today's search ranking was tuned by reasoning and spot checks. P2 produced profiles whose quality is visibly modest (pilot mean rating 0.86 of 2; the first 200 profiles are dominated by a few values), and three open decisions wait for evidence: whether the extraction prompt needs tuning before the 6.7 h backlog run (board task 158), which embedding model to use for the situation vectors (OQ-4), and whether the local models are good enough for Russian and Spanish (OQ-3). P3 and P4 also need a target to build against and a regression gate (NFR-6). All of this needs one thing that does not exist: human-judged (message, meme) pairs and a harness that scores any retriever against them.

## Findings that shape the design (verified 2026-10-03)

- Human time is the scarce resource: the gold set is judgments by the user, so every design choice must make each judgment cheap and every judgment reusable.
- P3 (query parsing) and P4 (retrieval) do not exist yet. The harness therefore has a pluggable retriever interface, and the first baselines are simple retrievers built from signals that exist today.
- Profiles exist for only 194 `general` images; profiling the whole library is the 6.7 h backlog run. Judging pooled candidates from 32k images with the current prompt would be heavy, and a later prompt change would invalidate the profiles it measured. A frozen sample avoids both: about 1,500 images profile in roughly 20 minutes (0.75 s per image measured on 2026-10-03), and re-profiling after a prompt change takes the same time.
- **The production keyword search is AND-only:** `repository/ocr_lemmas.py::scored_image_matches` requires every normalised query lemma to match and returns an empty dict as soon as one lemma has no hit. A full natural-language message therefore matches almost nothing. The evaluation needs both a `lexical_and` row (what the product does today, expected to be near zero) and a `lexical_or` row (any-token scoring, useful as a pool source and as a floor).
- `meme_profile_situation_embeddings` already stores L2-normalised `bge-large` vectors per profile with model name and digest, and `OllamaEmbedder` can embed a query in the same space, so an embedding-only retriever (the ADR-001 fallback path) can be baselined now.
- The pilot already proved the labelling mechanics: a self-contained blinded HTML sheet with per-item ratings exported as JSON works in the user's browser (`batch/tools/meme_profile_pilot.py`, `render_sheet`).
- Duplicate memes exist (`tmp_clusters`); if one copy is judged acceptable its twin is too, so the corpus must hold one image per duplicate cluster or recall becomes ambiguous.
- Image ids are database-local UUIDs of the `general` environment; the gold data can reference them and be committed (no image bytes, no file names), while the corpus is only meaningful against that database.

## Decisions

### 1. Package and CLI

A new top-level package `evaluation/`:

| Module | Responsibility | Dependencies |
|---|---|---|
| `evaluation/gold.py` | Dataclasses and strict validation for `corpus`, `queries`, `judgments`, `baseline` files; load and save with deterministic key order and LF newlines; content hashes | stdlib, `taxonomy` |
| `evaluation/metrics.py` | nDCG@k, recall@k, hit@k, judged-share@k; per-language and per-stratum aggregation | stdlib |
| `evaluation/pooling.py` | Build a blinded candidate pool per query from several ranked lists (round-robin, dedupe, random fill, seeded shuffle) | stdlib |
| `evaluation/runner.py` | Run a retriever over all queries, compute metrics, produce a result object | stdlib |
| `evaluation/report.py` | Markdown and JSON reports, baseline files, `compare` | stdlib |
| `evaluation/sheet.py` | Render the blinded HTML judging sheet; import its exported JSON | stdlib, Pillow (thumbnails) |
| `evaluation/retrievers/` | `base.py` (the `Retriever` protocol), `random_retriever.py`, `lexical.py`, `situation_embedding.py`, `axis_match.py` | SQLAlchemy async, `repository.*`, `ai.ollama_embed` |
| `evaluation/corpus.py` | Stratified frozen sample and its builder | SQLAlchemy async, `repository.meme_profile_inputs` |
| `evaluation/__main__.py` | CLI | all of the above |

The pure modules (`gold`, `metrics`, `pooling`, `runner`, `report`) import nothing outside the standard library and `taxonomy`, so they are unit-testable without a database. Retrievers and the corpus builder are the only parts that touch the database or Ollama.

CLI (`python -m evaluation ...`): `build-corpus`, `build-pools`, `sheet`, `import-judgments`, `run`, `compare`, `list-baselines`. Database-touching commands require `DATABASE_URL` in the shell (the repo's batch convention); anything that only reads profiles and judgments needs only the read-only role (`DATABASE_URL_READONLY`) and refuses a write-capable URL by checking that the URL contains `ocr_readonly`, the guard used by the pilot tooling. The two commands that write (profile the corpus, via the P2 jobs) are not part of this CLI.

### 2. Gold data (committed under `evaluation/gold/`)

```
evaluation/gold/
  corpus.json          # frozen sample (decision 3)
  queries.json         # test messages with expected reply profiles (decision 4)
  judgments.json       # human grades (decision 6)
  baselines/<label>.json   # stored results (decision 8)
  README.md            # how to extend, rubric, rules
```

- `corpus.json`: `{"version": 1, "environment": "general", "seed": <int>, "created": "<date>", "images": [{"imageId", "contentHash", "stratum"}]}`. `contentHash` is `images.content_hash` (may be null for old rows; then `null`); the runner warns when an id is missing from the database or its hash changed.
- `queries.json`: `{"version": 1, "queries": [{"id": "q001", "language": "en|ru|es", "text": str, "tags": {"length": "short|medium|long", "difficulty": "easy|hard|ambiguous"}, "expectedProfile": {"situation": str, "emotion": [key], "stance": [key], "topic": [key], "tone": [key]}, "reviewed": bool}]}`. `expectedProfile` uses the P1 taxonomy keys (validated with `taxonomy.validate_profile`, `situation` in English) with the **query-side semantics from P1 decision 1** (what is happening in the message; the emotion, stance and tone of the reply wanted). It is the gold data for P3 (parse accuracy) and the input of the `axis_match` retriever.
- `judgments.json`: `{"version": 1, "judgments": [{"queryId", "imageId", "grade": 0|1|2, "rater": str, "date": str, "round": int}]}`; one entry per (query, image), later rounds may overwrite earlier ones for the same pair (the importer reports overwrites).
- Committed: the queries are synthetic text, the judgments are grades over database ids. Not committed (kept local): sheets, thumbnails, results containing library file names. The runner never writes file names.

### 3. The frozen corpus

`build-corpus` draws about 1,500 active `general` images with a fixed seed (stored in the file) using `MemeProfileInputsRepository` bundles to assign strata, like the pilot: `ocr+description` 900, `ocr only` 300, `description only` 150, `no usable text` 150 (shortfalls in a small stratum are redistributed to `ocr+description`, and the final counts are stored). Exclusions: flagged images, images with an unsupported or undecodable extension (the vision path cannot profile them), and every image but one per duplicate cluster (the representative is the lowest image id in the cluster; singletons are unaffected). The corpus is profiled with the P2 jobs restricted to it (decision 9) before any pool is built; the runner refuses to run when fewer than 95% of the corpus images have a profile and says how to fix it.

### 4. The queries

60 messages, 20 per language, drafted by Claude and reviewed by the user (each query has a `reviewed` flag; unreviewed queries are excluded from official runs). Drafting rules, all checkable by a lint in `evaluation/gold.py`:
- Per language: 5 short (at most 8 words), 10 medium, 5 long (two or three sentences); 7 easy, 8 hard (indirect, sarcastic or culturally specific), 5 ambiguous (several valid replies).
- Across the set: every `topic` key appears at least once; at least 8 distinct `stance` keys, 10 distinct `emotion` keys and 6 distinct `tone` keys appear in the expected profiles; most situations differ between the three languages (so a language gap cannot hide behind identical content), but at least 6 situations are written in all three languages as near-translations and share a `pairId`, to measure cross-language consistency.
- Written as an incoming message someone would actually receive, not as a description of a meme.
- Each query's `expectedProfile` is drafted together with the text and reviewed together with it.

### 5. Candidate pools

`build-pools` produces, per query, about 12 candidates from inside the corpus, deterministically:
- Sources: `lexical_or`, `situation_embedding`, `axis_match` (decision 7) and 3 random corpus images (seeded per query). Ranked sources contribute round-robin from rank 1 (skipping images already taken) until 9 distinct candidates are chosen; if a source returns fewer than its share (the keyword retriever often returns nothing), the other ranked sources fill the gap; the 3 random images are added last. The pool is then shuffled with a seed derived from the query id and presented with no source labels.
- The pool records, privately (not in the sheet), which sources proposed each candidate; this is stored in a local `pools.json` and is used only for the pooling-bias analysis in the report (decision 8).
- Pools are append-only across rounds: a later round for the same query adds new candidates and never re-asks about judged ones.

### 6. Judging

`sheet` writes one self-contained HTML file per language (about 240 judgments each): the incoming message, then for each candidate a thumbnail and its inputs hidden by default (the user judges the meme itself), with three controls: **2 great reply** (you would send it), **1 acceptable** (works, not ideal), **0 wrong** (does not fit, or you cannot tell what it means), and a "skip" that leaves it unjudged. Progress is kept in the page (browser storage, with a try/catch fallback), so the user can stop and resume; a button downloads `judgments-<language>.json`. `import-judgments` validates the file (known query and image ids, grade in 0/1/2), merges it into `judgments.json` (reports counts, skips and overwrites) and refuses a file whose query ids are not in the gold set. The sheet embeds thumbnails as base64 (about 320 px, JPEG) exactly like the pilot sheet, escapes every library-derived string, and never contains a retriever name or a profile value (so judging is not biased by the system's own labels).

Rubric (stated at the top of the sheet and in `evaluation/gold/README.md`): judge only "is this a good meme to send back as a reply to this message"; the grade is about fit, not about how funny or well-made the meme is; ambiguous messages accept several kinds of reply.

Unjudged pairs are treated as grade 0 by the metrics (the standard pooling assumption); the report prints how much of each top-10 was actually judged so the assumption's cost is visible.

### 7. Retrievers and the interface

```python
class Retriever(Protocol):
    name: str
    config: dict                      # recorded in the baseline
    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[UUID], k: int) -> list[tuple[UUID, float]]: ...
```

All results are restricted to `corpus_ids` and ordered by descending score with deterministic tie-breaks (image id). Baselines shipped:
- `random`: seeded shuffle of the corpus (the floor).
- `lexical_and`: today's behaviour, `scored_image_matches(session, query.text)`, restricted to the corpus (expected to be empty for most messages).
- `lexical_or`: for each normalised query token, `scored_image_matches(session, token)` and sum the scores per image over tokens (any-token semantics, same source and tier weights as production); restricted to the corpus.
- `situation_embedding`: embed the raw query text with the configured model (`taxonomy.embedding_model`, one batched call per run) and rank corpus profiles by cosine similarity to `meme_profile_situation_embeddings` (no translation: this is the embedding-only fallback and the cross-language probe for OQ-4).
- `axis_match`: uses the query's **gold** `expectedProfile` (no parsing): per axis with expected values, the share of expected values present on the image's profile, weighted by the stored confidence; the score is the mean over the axes that have expected values. A baseline, not the P4 fusion.

P4's pipeline later plugs in by implementing `Retriever` (a thin wrapper that parses with P3 and fuses). P3's parse accuracy is measured separately as agreement between its output and `expectedProfile`.

### 8. Metrics, runner, baselines

- Metrics per query over the top 10: **nDCG@10** (gain = grade, so 0/1/2, standard log2 discount), **recall@10** (positives are grade at least 1; the denominator is the number of judged positives for the query), **hit@10** (any grade at least 1 in the top 10, the criterion in user story US-1), and **judged@10** (share of the top 10 that was judged). Queries with no judged positive are excluded from nDCG and recall means and counted separately.
- Aggregates: overall, per language, per `tags.difficulty` and per `tags.length` of the query, and per corpus input stratum of the images in the top 10 (so the report shows whether OCR-only images are retrieved and judged worse); plus the per-language ratio to English (user story US-7).
- `run --retriever <name> [--label L]` evaluates all reviewed queries, prints a Markdown report and writes `baselines/<label>.json` containing: label, date, retriever name and config, the git commit if available, SHA-256 of `corpus.json`, `queries.json` and `judgments.json`, a summary of the corpus profile state (counts by `taxonomy_version`, `extractor_prompt_version`, `extractor_model`, and the embedding model and digest), overall and grouped metrics, and per-query metrics (no file names).
- `compare A B` prints metric deltas and the queries whose nDCG@10 fell by at least 0.1, with the query text; it refuses (unless `--force`) when the gold hashes differ, because scores against different judgments are not comparable (re-running a retriever to re-baseline is cheap).
- The report also shows a **pooling-bias note**: how many top-10 results of each retriever were proposed by each pool source, using the private pool records.
- Thresholds (OQ-5) are not guessed here: after the baseline run the addendum records the baseline numbers and proposes acceptance thresholds for P4 (the best baseline must be beaten on nDCG@10 and the per-language ratio must stay above a stated fraction), for the user to approve.

### 9. Profiling the corpus (a small P2 change)

The two P2 jobs gain an optional `--ids-file PATH` (a JSON list of UUIDs or a text file with one UUID per line) that restricts selection to those images: `build_meme_profiles` filters its candidates before staleness selection, `build_meme_profile_embeddings` filters its pending profiles. Everything else (fingerprints, failure context, `--reset`, `--limit`, run tracking) is unchanged; a unit test per job proves that images outside the file are never touched, and `--reset` combined with `--ids-file` deletes only the listed images' model-generated profiles and failure rows. `evaluation build-corpus --write-ids-file` writes the file for these jobs.

### 10. Experiments the harness enables (documented workflow, executed in the plan)

- **Prompt or model change** (gate for board task 158): change the prompt or `meme_profiles.text_model`, run `build_meme_profiles --ids-file corpus.ids --reset` (about 20 minutes, the corpus only), run `build_meme_profile_embeddings --ids-file corpus.ids`, run `evaluation run` for `axis_match` and `situation_embedding` under a new label, `compare` against the baseline. The baseline records the prompt version and model so a comparison names what changed.
- **Embedding model (OQ-4):** the `situation_embedding` baseline already gives the per-language gap for the English `bge-large` with raw RU/ES queries. A multilingual candidate (for example `bge-m3`, pulled only with the user's approval) is tested by temporarily setting `taxonomy.embedding_model`, re-embedding the corpus (minutes), re-running the retriever and comparing; the setting is restored afterwards unless the user decides to switch.
- **Profile quality without a parser:** `axis_match` with the gold expected profile isolates P2's profile quality from P3's parsing; if it scores near `random`, the profiles (not the retrieval design) are the problem.

## Out of scope

- The P3 parser, the P4 fusion and endpoint, and their retriever wrapper (they plug in later).
- The in-app labelling UI and rating capture (FR-18): the importer's format is the compatibility point; the UI belongs to P5.
- An LLM judge, learned rankers and online metrics.
- Judging the full library (the corpus is a sample; absolute scores are optimistic compared with the full library, relative scores are what the gate needs).
- Growing the gold set beyond the first 60 queries (the format and importer support later rounds).

## Testing

Each root as its own `pytest` command (CLAUDE.md); the whole `tests/integration/` root is run before merge if models or shared repositories change (they do not here, but the P2 job changes touch `batch/`):
- `tests/evaluation/` (pure, no database): `gold` validation (every required field, grade range, duplicate ids, language and tag enums, the drafting lint of decision 4, deterministic serialisation), `metrics` against hand-computed nDCG, recall, hit and judged-share examples (including no-positive queries and ties), `pooling` (deterministic, no duplicates, round-robin order, gap filling when one source is empty, random fill, shuffle seeded per query, source labels absent from the public pool), `runner` and `report` with a fake retriever (baseline contents, `compare` deltas, refusal on differing hashes, per-language ratio), sheet rendering (blinded: no retriever name or profile value in the HTML, escaping, export JSON shape), `import-judgments` (merge, overwrite report, unknown ids refused).
- `tests/integration/test_evaluation_retrievers.py` against `ocrdb_test`: `lexical_and` returns nothing for a multi-word message that matches no single image and works for a one-word query, `lexical_or` ranks an image matching more tokens higher and ignores images outside the corpus, `axis_match` ranks by agreement with the expected profile, `situation_embedding` with a fake embedder ranks by cosine and respects the corpus restriction; corpus builder strata, exclusions (flagged, unsupported extension, duplicate cluster) and determinism for a seed.
- `batch/tests` and `tests/integration` for the P2 `--ids-file` option (unit and DB-backed).

## Acceptance criteria

1. `evaluation/` exists with the CLI above; the pure modules import only the standard library and `taxonomy` (checked by an import-guard test, like `tests/taxonomy/test_core_imports.py`).
2. `evaluation/gold/` holds a frozen corpus of about 1,500 `general` images, all profiled (at least 95%), 60 reviewed queries meeting the drafting lint, and about 700 judgments imported from the user's sheets.
3. Baselines for `random`, `lexical_and`, `lexical_or`, `situation_embedding` and `axis_match` are stored with the gold hashes and the profile-state summary, and the Markdown report shows overall, per-language, per-stratum and per-difficulty metrics, judged@10 and the pooling-bias note.
4. `compare` works between two baselines and refuses mismatched gold hashes.
5. The P2 jobs accept `--ids-file` and leave other images untouched (tests).
6. The addendum records the baseline numbers, answers the two decision gates with evidence (does the prompt need tuning before task 158; how large the raw RU/ES gap is for the English embedding) and proposes P4's acceptance thresholds for the user's approval.
7. All test roots pass (`tests/evaluation`, `tests/integration` whole root, `batch/tests`, `tests/ai`, `tests/taxonomy`, Backend).
8. P3 and P4 can start from this spec alone (the interface, gold formats and baseline numbers are unambiguous).

## Risks

| Risk | Mitigation |
|---|---|
| About 700 judgments is a lot of human time | Per-language sheets with progress saving; the user can stop after one language and still get a per-language baseline; pools capped at about 12 |
| Gold set is biased to the author's taste and to what the pooling retrievers can find | Random pool members, the judged@10 and pooling-bias note in every report, two raters possible later (the rater is recorded), later rounds append pools |
| Synthetic queries are unlike real messages | Drafting rules (indirect, long, ambiguous cases), user review of every query, `pairId` near-translations to test cross-language consistency, real messages and FR-18 ratings grow the set later |
| A 1,500-image sample flatters absolute scores | Stated everywhere; gates are relative (beat the best baseline, do not regress); revisit with a larger corpus once the backlog is profiled |
| Profiles from the current prompt are skewed, so `axis_match` may look bad for the wrong reason | That is exactly the signal the prompt gate needs; the experiment loop re-profiles the corpus in about 20 minutes after a prompt change |
| Duplicate memes make "acceptable" ambiguous | One image per duplicate cluster in the corpus |
| Scores change whenever judgments grow | Baselines store the gold hashes; `compare` refuses mismatches; re-baselining is cheap |
| The corpus references database-local ids | Content hashes and a drift warning; the gold data is only valid for the `general` database it was drawn from, documented in the README |

## Plan outline (for writing-plans)

1. `evaluation/gold.py` and `metrics.py` with their tests (the contract everything else uses).
2. `pooling.py`, `runner.py`, `report.py` with their tests (all pure).
3. Retrievers (`random`, `lexical`, `situation_embedding`, `axis_match`) with integration tests.
4. The P2 `--ids-file` option on both jobs, with tests.
5. Corpus builder and `build-corpus`; draw the corpus (controller, read-only role) and profile it (controller, live write to `general` with consent).
6. Draft the 60 queries and expected profiles; the user reviews them.
7. `build-pools`, `sheet`, `import-judgments` with tests; the user judges the sheets (human step).
8. Baseline runs for all retrievers, the experiment workflow checks, the addendum with the numbers, the two gate answers and proposed thresholds; docs (`CLAUDE.md`, epic tracker); review report.

## Addendum: result (2026-10-05)

### What was built and drawn

- `evaluation/` package with the CLI (`build-corpus`, `build-pools`, `sheet`, `import-judgments`, `run`, `compare`, `list-baselines`), the five baseline retrievers, the metrics, baselines with gold hashes and the blinded sheet; `--ids-file` on the two P2 jobs. Tests: `tests/evaluation` 90, whole `tests/integration` 509, `batch/tests` 268, `tests/taxonomy` 83, `tests/ai` 66.
- Corpus: 1,500 `general` images (seed 7). Strata: 1,322 `ocr+description`, 150 `description only`, 26 `ocr only`, 2 `no usable text`. The library has descriptions for almost every image, so the two weak strata are tiny (all available images were taken, the shortfall went to `ocr+description`); per-stratum numbers for them rest on very few images. Profiling the corpus took 17 min 11 s (0.7 s per image), 1,446 saved, 45 empty answers; 1,455 of 1,500 profiled (97%), 1,454 embedded, no drift (0 missing, 0 inactive, 0 hash changed), one embedding digest.
- Queries: 60 reviewed (20 per language, six near-translated pairs, drafting lint clean). Judgments: 718 from one rater (en 238, ru 240, es 240; two English candidates skipped). Grades 0/1/2: en 120/72/46, ru 145/65/30, es 144/62/34. Every query has at least one positive.

### Baselines (nDCG@10 / hit@10, 60 queries, `evaluation/gold/baselines/`)

| retriever | nDCG@10 | recall@10 | hit@10 | judged@10 | en | es | ru |
|---|---|---|---|---|---|---|---|
| random | 0.006 | 0.010 | 0.050 | 0.015 | 0.000 | 0.018 | 0.000 |
| lexical_and (production search today) | 0.000 | 0.000 | 0.000 | - | 0.000 | 0.000 | 0.000 |
| lexical_or | 0.259 | 0.234 | 0.750 | 0.340 | 0.203 | 0.254 | 0.319 |
| situation_embedding (raw query, English bge-large) | 0.330 | 0.296 | 0.750 | 0.267 | 0.525 | 0.384 | 0.081 |
| axis_match (gold expected profile, no parsing) | 0.339 | 0.314 | 0.867 | 0.322 | 0.349 | 0.312 | 0.354 |

Per difficulty (nDCG@10): axis_match ambiguous 0.421, easy 0.350, hard 0.277; situation_embedding 0.335 / 0.303 / 0.350; lexical_or 0.417 / 0.216 / 0.197. Per length: axis_match short 0.402, medium 0.323, long 0.307; lexical_or short 0.290, medium 0.295, long 0.154.

**How to read the numbers.** `judged@10` is only 0.27 to 0.34: about two thirds of every retriever's top ten was never shown to the judge and counts as grade 0 (pooling bias note in each report: unpooled top results 407 of 600 for axis_match, 438 of 600 for situation_embedding, 392 of 576 for lexical_or). All scores are therefore lower bounds, and a retriever whose top ten differs from the three pool sources is penalised the most. Absolute values are also optimistic relative to the full library (a 1,500-image sample); only the ordering and the gaps are used for decisions.

### Gate answers

**(a) Does the prompt need tuning before the full backlog run (board task 158)?** No, the evidence does not block it. With the gold expected profile (so no parsing), `axis_match` over the current profiles reaches hit@10 0.867 and beats the keyword-OR search (0.259 / 0.75) and random (0.006 / 0.05) by a wide margin, and the production search returns nothing for a full message (0.000). The skew seen in the first 200 profiles (stance `mock` 70%) did not show up as a retrieval failure. Weak spots: `hard` queries (0.277 against 0.350 for easy), and OCR-only memes (mean grade of judged results 0.25 for `ocr only`, n=12, against 0.73 for `ocr+description`, n=168, in `axis_match`), consistent with the P2 pilot but resting on very few images. Recommendation: run the `general` backlog in slices (first `--limit 200`, then about 3,000 per slice), run `build_image_descriptions` before profiling `metal` (OCR-only profiles are the weakest), and treat prompt tuning as a later experiment evaluated on the corpus with the experiment loop of decision 10 (a prompt change re-profiles the corpus in about 17 minutes and `compare` shows the effect). The cost of being wrong: a later prompt change marks every profile stale and the backlog is paid for a second time.

**(b) How large is the raw RU/ES gap for the English embedding (OQ-4)?** Large for Russian: `situation_embedding` nDCG@10 is 0.525 in English, 0.384 in Spanish (ratio 0.73) and 0.081 in Russian (ratio 0.15; hit@10 0.40 against 1.00). English-only bge-large cannot be fed raw Russian messages. `axis_match`, which works on the English profile of the query, is language-robust (ratio es 0.89, ru 1.01), so the design route of ADR-001 (the P3 parser turns the message into an English situation text and axis values, then retrieval runs in English) is supported, provided the P3 parse quality holds; P3 is measured separately as agreement with `expectedProfile`. `lexical_or` is relatively strongest in Russian (0.319, ratio 1.57), because OCR text of the corpus memes matches Russian words directly. The comparison with a multilingual embedding model (for example `bge-m3`) was not run: it needs a model pull, which waits for the user's approval; the harness supports it (set `taxonomy.embedding_model`, `build_meme_profile_embeddings --ids-file --reset`, run `situation_embedding` under a new label, `compare`).

### Proposed acceptance thresholds for P4 (for the user's approval, not a decision)

On the same gold data (hashes in the baselines), evaluated after a second judging round that pools P4's own top ten, so that its unjudged results are not scored as zero:
1. Overall nDCG@10 at least 0.37 (the best baseline 0.339 plus a 0.03 margin) and hit@10 at least 0.87 (the best baseline).
2. Per language nDCG@10 at least 0.85 of English and at least 0.30 in every language.
3. No query class (difficulty, length) below the best baseline's score for that class (difficulty: ambiguous 0.421, easy 0.350, hard 0.350; length: short 0.402, medium 0.326, long 0.329 taking the best baseline per class).
4. P3 parse agreement with `expectedProfile` is reported separately (per axis, per language) and gates P3, not P4.

### Deviations from this spec

- Pools are stored per round (`evaluation/work/pools-r<N>.json`, never overwritten without `--force`) and `run` merges the private sources of all rounds, instead of a single `pools.json` (final review finding: append-only pools).
- The judging sheet shows only the meme image (inputs are not shown, not even behind a toggle) and thumbnails are 400 px instead of about 320 px; transparent images are composited onto white; a "skip (ungraded)" control was added.
- `judged@10` divides by the number of results returned (up to 10), not by 10.
- `build-corpus` does not create the output directory (found when drawing the corpus; the directory was created by hand).
- Corpus drift and environment checks (spec decision 2) and embedding coverage/digest warnings were added after the final review.
- The multilingual embedding experiment (OQ-4) was not run (needs a model pull and the user's approval).
