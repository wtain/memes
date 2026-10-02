# Meme from text: user stories

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

Personas:
- **Searcher**: the person who owns a meme library and wants to answer a message with a meme.
- **Curator**: the same person in maintenance mode, improving the data (profiles, descriptions, tags).
- **Developer/Operator**: runs the batch jobs and the three environments, and tunes the pipeline.

Format: story, then acceptance criteria (AC) as checkable statements. Requirement IDs refer to `requirements.md`.

## Searcher

### US-1 Find a meme for a message
As a searcher, I paste a message and get memes that fit as a reply, so I do not have to guess keywords.
- AC1: Given the text "my manager just scheduled a meeting at 6 pm on Friday", the top 10 contains at least one meme a reviewer
  judges acceptable (gold set case). No keyword from the text needs to appear in the meme.
- AC2: The same holds for the same message in Russian and Spanish.
- AC3: Results appear within the latency budget (NFR-1), with a visible loading state while the LLM parses.
- AC4: Flagged and non-active images never appear. Pagination does not repeat or skip items.
- AC5: Empty or whitespace-only input does nothing and shows no error. Over-long input shows a limit message.
- FR-1, FR-2, FR-3, FR-13, FR-14, FR-20, FR-21.

### US-2 Understand why a meme was suggested
As a searcher, I see why each meme was suggested, so I can trust it and pick faster.
- AC1: Each result shows the matched axes with the meme's value (for example "emotion: resignation, stance: sarcasm").
- AC2: A result matched only through the lexical path says so.
- AC3: Explanations come from the same scores used for ranking, not from a second LLM call.
- FR-16.

### US-3 Correct how my text was understood
As a searcher, I can see the system's reading of my text and fix it, so a wrong reading does not ruin the results.
- AC1: The UI shows the parsed profile as editable chips per axis.
- AC2: Editing a chip and re-running search does not call the LLM again and returns in retrieval-only time.
- AC3: Removing all values from an axis excludes that axis from ranking.
- FR-5.

### US-4 Narrow by axis
As a searcher, I restrict results by an axis value ("only sarcastic"), so I get the tone I want.
- AC1: Selecting a facet value applies a hard filter and updates the result list and the other facet counts.
- AC2: Existing tag filters keep working together with axis filters.
- AC3: A filter that yields zero results says so and offers to remove it.
- FR-6, FR-17.

### US-5 Tell the system when a match was good or bad
As a searcher, I mark results good or bad, so quality can be measured over time.
- AC1: A one-click rating per result is stored with the query hash, the parse version, the meme id and the rank.
- AC2: Ratings do not change ranking in the first release.
- AC3: Ratings can be exported into the gold set format (P6).
- FR-18. Priority: could.

### US-6 Keep working when the LLM does not
As a searcher, I still get results when the local model is down or slow, so search is never blocked on it.
- AC1: With Ollama stopped, a search returns results from the embedding-only and lexical paths and shows a "limited understanding" notice.
- AC2: A malformed LLM response triggers one retry, then the same fallback, and is logged.
- AC3: A hard timeout (configurable) applies to the parse call.
- FR-4, NFR-4.

### US-7 Use my own language
As a searcher who writes in Russian, Spanish or English, I get relevant memes whatever language the meme text is in.
- AC1: The gold set has equal numbers of EN, RU and ES cases and reports metrics per language.
- AC2: Per-language quality does not fall below an agreed fraction of the English score (threshold set in P6).
- FR-2, NFR-6.

## Curator

### US-8 Enrich the library with meme profiles
As a curator, I run one job (or one admin button) that gives every active meme a profile, and I can see progress and gaps.
- AC1: The job processes only images without a current profile (or with a stale one), in resumable slices, with a per-run cap.
- AC2: `/admin/batches` shows runs with images selected, done, failed, remaining.
- AC3: A coverage view shows counts of images with profile, without profile, stale, human-verified.
- AC4: Changing the taxonomy version, or the extraction prompt version, marks existing profiles stale without deleting them.
- AC5: A rejected description never contributes. A note takes priority over generated descriptions.
- FR-7, FR-8, FR-10, FR-12, FR-23.

### US-9 Fix a wrong meme profile
As a curator, I fix a meme's axis values by hand, and the fix sticks.
- AC1: The meme page lets me edit each axis value.
- AC2: A human-verified profile is not overwritten by later batch runs, even after a taxonomy version bump (it is flagged for re-review instead).
- AC3: Edits take effect in search without waiting for the next batch (vectors for the changed axes are refreshed inline or in a fast job).
- FR-11. Priority: should.

## Developer/Operator

### US-10 Reuse the pipeline in an agent
As a developer, I can call each pipeline stage as a tool, so an agent can be added later without duplicating logic.
- AC1: Parse, per-axis search, filter, fuse and explain exist as functions with documented JSON-schema inputs and outputs, no hidden global state, no side effects.
- AC2: The HTTP endpoint is implemented by composing exactly those functions.
- AC3: Tool descriptions are good enough to hand to an LLM as a tool list (decomposition.md lists the catalogue).
- FR-22.

### US-11 Measure and tune
As a developer, I run one command and see how a change affects quality.
- AC1: The command runs all gold cases against the full pipeline with stored LLM parses (so retrieval tuning does not require re-calling the LLM) or live parses on demand.
- AC2: It prints nDCG@10 and recall@10 overall and per language, plus the cases that got worse versus the stored baseline.
- AC3: Axis weights and fusion parameters are read from settings, so tuning needs no code change.
- FR-14, FR-24.

### US-12 Operate it across three environments
As an operator, I deploy the feature to metal, general and it without a shared database or shared configuration.
- AC1: Each environment has its own profile data and, where needed, its own weights.
- AC2: An environment lacking a required model or vocabulary reports that clearly and disables the feature, instead of erroring at runtime.
- NFR-7.
