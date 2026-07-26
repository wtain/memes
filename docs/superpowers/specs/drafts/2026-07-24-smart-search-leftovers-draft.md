# Smart Search — Leftovers

Status: Draft (backlog notes, not a design — no implementation plan should be written
directly from this file; each item needs its own brainstorming pass when picked up)

## Purpose

Smart search has gone through an original design
(`2026-07-21-smart-search-design.md`) and three hardening rounds, plus a fuzzy-matching
phase (`2026-07-24-smart-search-fuzzy-matching-design.md`). This document collects
everything still deliberately deferred, and — more usefully than a bare list — *why*
each one is deferred: the specific complexity, ambiguity, or challenge that makes it not
a quick follow-up, so a future pass through this backlog doesn't have to rediscover the
reasoning from scratch.

---

## 1. Ranking / relevance scoring (BM25, TF-IDF)

**What it would do:** order matching results by relevance instead of the current
boolean AND-of-lemmas retrieval (matches are unordered w.r.t. query relevance; the
`/api/images` and `/api/recommendations` endpoints sort by recency/seed, not by how well
a result matches the query).

**Why deferred:**

- **Schema gap.** `ocr_lemmas` is a flat `(image_id, lemma)` membership table — it
  answers "does this lemma occur," not "how often," and carries no document-length or
  corpus-wide term-frequency statistics. BM25 needs per-document term frequency,
  corpus-wide inverse document frequency, and document-length normalization — none of
  which exist today. This isn't a query change, it's a schema and index redesign.
- **Ongoing maintenance cost.** Corpus-wide IDF statistics drift every time images are
  added or the batch pipeline reindexes; a real implementation needs to decide whether
  IDF is precomputed and periodically refreshed (adds a maintenance job and a staleness
  window) or computed at query time (adds real per-query cost across a corpus that's
  already tens of thousands of images per environment).
- **Ambiguity about what "relevant" even means for this corpus.** Memes are short,
  mostly single-caption text. It's genuinely unclear whether BM25-style term-frequency
  weighting produces meaningfully better orderings than a much simpler heuristic (e.g.
  "how many distinct query lemmas matched" as a secondary sort key) for text this short.
  There's no golden test set today establishing what a "better" ordering looks like for
  this specific corpus, so picking BM25 specifically — as opposed to a cheaper heuristic
  — hasn't been validated as worth its complexity.
- **Interacts awkwardly with the current strict-AND model.** BM25 conventionally scores
  documents that partially match a multi-term query (OR-with-scoring), while this
  search deliberately keeps strict AND semantics (every query lemma must match). Whether
  to relax to OR-with-ranking, or keep strict AND and rank only within the AND'd result
  set, is itself an open design fork that needs its own brainstorming session before any
  implementation work.

## 2. Erratives normalization (превед→привет, кросавчег→красавчик, etc.) — DONE (mostly)

**Status: shipped.** See
`docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md`. The
trigram-similarity-can't-catch-this finding below (kept for the record) turned out
correct, but the conclusion that only a hand-curated dictionary could work was wrong —
a ported Russian Metaphone algorithm, gated on pymorphy3's `is_known` flag (to avoid
real dictionary words like `кот`/`код` cross-matching), handles the general case without
per-word curation. Along the way, a real bug was found and fixed in
`rules/normalize.py::lemmatize_word`: pymorphy3's own unknown-word guesser was
corrupting erratives' lemma before phonetic matching could even run (`превед` →
`преведа`).

**What's still open:** erratives that imitate a specific *inflected* surface form
whose dictionary lemma differs from that form (e.g. `жжот` evoking `жжёт`, which
lemmatizes to the infinitive `жечь` — a different surface form the errative never
imitated) aren't caught; fixing this needs matching against un-lemmatized surface
forms, a materially larger change. Also, an LLM-assisted dictionary-mining idea for
erratives that don't reduce to a clean phonetic rule at all remains an unimplemented
placeholder:
`docs/superpowers/specs/drafts/2026-07-25-erratives-llm-dictionary-mining-draft.md`.

<details>
<summary>Original why-deferred reasoning (kept for the record, now superseded)</summary>

This was *actively investigated* during the fuzzy-matching design
(`2026-07-24-smart-search-fuzzy-matching-design.md`) — not skipped for lack of time. The
obvious cheap approach (let trigram similarity catch erratives as a side effect of
generic fuzzy/typo tolerance) was empirically tested against the real database and
disproven:

| Pair | Trigram similarity |
|---|---|
| `превед` / `привет` | 0.17 |
| `кросавчег` / `красавчик` | 0.25 |
| `аффтар` / `автор` | 0.08 |
| `кот` / `код` (unrelated, for comparison) | 0.33 |

Erratives score *at or below* the similarity of unrelated short-word pairs, because
trigram similarity measures shared character substrings, not phonetics — and erratives
deliberately substitute characters (е↔и, а↔о, в↔ф) in ways that sound the same but break
substring overlap. There is no single similarity threshold that catches erratives
without also flooding short-word queries with false positives.

The original conclusion — "needs a hand-curated substitution dictionary, not an
algorithm" — is what the phonetic-erratives design superseded.
</details>

## 3. Non-Russian lemmatization (English, Spanish) — English: shipped

**Status: partially addressed — English shipped.** See
`docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md`.
It adds a lightweight rule-based *stemmer* (the `snowballstemmer` package — not a full
POS-aware lemmatizer like spaCy) that unifies English inflections for OCR text and search
queries (e.g. "cats"/"cat", "running"/"run"). It's wired in as a new query-time
*fallback* tier in `repository/ocr_lemmas.py::matching_image_ids` — tried only after
exact match already fails, the same layered pattern trigram and phonetic fallback
already use — rather than as a change to the primary lemma path, specifically to avoid
regressing Spanish-tagged content (Spanish is also Latin-script, so a query-time check
that can't tell English and Spanish apart must not unconditionally stem every
Latin-script token).

Spanish itself remains fully deferred; the original reasoning below still applies to it
unchanged. The English design's query-time fallback tier is deliberately structured so
that adding Spanish later means adding its own `language == "es"` index-time branch and
its own query-time fallback tier, following the same pattern, without needing to revisit
or rework the English implementation.

**What's still open (within the shipped English feature):**

- **Concept tagging now has an undecided vocab/text asymmetry — found during the
  final whole-branch review, after the design was written.** `rules/normalize.py`'s
  `lemmatize_word` is shared infrastructure, not search-matching-private:
  `batch/build_bow.py` and `rules/concept_tagger.py` (via `batch/build_tags_from_ocr.py`)
  also call it with each OCR row's own `language`, so `"en"`-tagged OCR text now gets
  stemmed there too — but `concept_tagger.py::_load_concepts` loads the concept
  **vocabulary** unstemmed (no `language` argument passed). After the next
  `build_tags_from_ocr` rebuild, this will silently shift some English concept tags
  (base-form vocab entries gain new matches against stemmed OCR text; inflected-form
  vocab entries lose matches they had before) — nobody decided this should happen, it's
  just a consequence of `lemmatize_word` being shared. `metal` being 93.8% English
  magnifies the effect. Deliberately left undecided rather than patched in: whether to
  also stem the concept vocabulary for symmetry needs its own brainstorming pass (does
  it actually improve tagging, or introduce new stem-collision false positives the way
  ungated phonetic matching did for search?) and empirical validation against real
  concept-tag counts before `build_tags_from_ocr`/`build_bow` are next rerun in any
  environment.
- **Hyphenated compounds and contractions aren't stemmed.** The design's
  `is_latin_word()` gate uses the matching regex `^[a-z]+$`, which only matches an
  unbroken sequence of plain lowercase letters — so tokens like "well-known" or "don't"
  (containing a hyphen or apostrophe) fall through without ever reaching the stemmer.
  This was a deliberate scope-narrowing for the first pass, explicitly flagged in the
  design itself as "take this on later" rather than a blocker.
- **Irregular forms a real lemmatizer would unify but a stemmer can't** (e.g.
  "better"/"good" staying as distinct, unrelated tokens) — an accepted tradeoff of
  choosing a lightweight stemmer over a heavier dependency like spaCy or NLTK, not
  expected to be revisited unless real search-quality complaints surface.

<details>
<summary>Original why-deferred reasoning (kept for the record; still applies to Spanish)</summary>

**What it would do:** true lemmatization (not just lowercasing) for English and Spanish
OCR text — e.g. unifying "cats"/"cat" the way Russian case declensions are already
unified.

**Why deferred:**

- **No lemmatizer dependency for these languages today.** `pymorphy3` (this project's
  lemmatization library) only ships Russian dictionaries here
  (`pymorphy3-dicts-ru`); there's no installed English or Spanish equivalent.
  `LEMMATIZABLE_LANGUAGES = {"ru"}` reflects what's actually available, not an arbitrary
  restriction.
- **Different tooling per language, not a config flag.** English lemmatization
  conventionally uses a different library entirely (spaCy, NLTK/WordNet, or a
  dedicated stemmer) — adding it isn't turning on a setting, it's a new dependency and a
  new code path in `rules/normalize.py` per language, meaningfully growing that module's
  maintenance surface.
- **Unvalidated payoff for this corpus.** Russian's case/declension system makes exact
  substring matching fail often (the whole motivation for the original smart search
  spec). English meme text is frequently already in base form or slang that a formal
  lemmatizer handles poorly anyway — whether the same investment pays off proportionally
  for English/Spanish text hasn't been checked against real corpus data the way the
  Russian case was.

</details>

## 4. Accepted OCR-language cross-detection asymmetry (documented limitation, not planned work)

Not a deferred feature — a permanently accepted tradeoff, included here only for a
complete picture. Index-time lemmatization trusts each OCR row's own detected language
(`ru`/`en`/`es`); a Cyrillic row EasyOCR confidently but wrongly tags as non-Russian gets
lowercase-only treatment, while the same word in a search query still gets full
Russian lemmatization. Fixing this means fixing OCR language misdetection itself — a
different problem than search. Documented via code comment
(`docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md`), not
revisited here.

## 5. Fallback-tier short-circuiting makes results corpus-dependent

**What it would do:** Today, `repository/ocr_lemmas.py::matching_image_ids` tries an
exact match for each query lemma first; only if that returns *zero* results does it fall
through to the fuzzier fallback tiers (trigram-similarity matching, phonetic matching for
Russian "erratives" — deliberate phonetic misspellings like `превед` for `привет` — and,
once `docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md` is
implemented, English stemming too). The fuzzy tiers are unioned together when more than
one of them runs (e.g. trigram and phonetic results are combined, not tried one after
another until one succeeds) — but the boundary between "exact match found something" and
"exact match found nothing, so try the fuzzy tiers" is a hard all-or-nothing gate: if
exact match finds even one result for a query lemma, none of the fuzzy tiers run at all
for that lemma, no matter how many additional legitimate results a fuzzier tier might
have surfaced.

The idea floated here (not committed to, just raised for the record) is to stop
short-circuiting: run every tier unconditionally for every query and merge/union all of
their results together, rather than gating the fuzzy tiers behind "did exact match come
up empty."

**Why deferred:**

- **Depends on ranking existing first.** The main reason today's exact-match tier is
  allowed to fully suppress the fuzzy tiers is that there's no relevance ranking yet —
  see item 1 above, "Ranking / relevance scoring." Without ranking, merging all tiers
  unconditionally would mix an exact match for one meme with a fuzzy/phonetic/stemmed
  match for a much less relevant meme into one undifferentiated result set, with no
  signal to tell the two apart or sort one above the other. A ranking signal (once item 1
  is implemented) would let exact matches naturally surface above fuzzy matches within a
  single merged result set, making the current tier gate unnecessary as a stand-in for
  quality — but until ranking exists, removing the gate would make result quality worse,
  not better. This idea can't really be tackled in isolation from item 1; it would need
  to be designed alongside it, or after it.
- **The corpus-dependency problem this is meant to fix.** The short-circuit means
  whether a query benefits from fuzzy matching at all depends entirely on whether some
  *unrelated* image happens to satisfy exact match first — not on any property of the
  query itself. Concretely: if a new meme gets added to the corpus that happens to
  satisfy exact match for a given query, the result set can silently *shrink* to just
  that one exact hit, even though before that meme was added, the same query surfaced
  several results via the fuzzy tiers. In other words, the quality and composition of
  today's search results depend on incidental corpus contents rather than being
  consistently well-defined by the tiers' own individual quality — an easy thing to
  overlook when trying to explain why a query's result set changed between two points in
  time.

This is a general architectural concern about the whole fallback-chain design, not
specific to any one fuzzy tier — it applies equally to the existing trigram and phonetic
tiers and to the not-yet-implemented English stemming tier. The stemming design doc
above discusses this same short-circuiting behavior briefly from its own narrower
angle (in its "Known, disclosed limitations" section); this item is the general version
of that concern, covering all fallback tiers, not just stemming.

## 6. Progressive / asynchronous search (open, not fleshed out)

**What it would do:** Return the fast exact-match tier's results to the user
immediately, then keep computing the heavier fuzzy tiers (trigram, phonetic, and
eventually English stemming) in the background, refining/updating the visible result set
as each tier finishes — instead of making the user wait for every tier to finish before
seeing anything.

**Why deferred:** This is a related but distinct idea from item 5 above, raised in the
same discussion but not fleshed out at all beyond the basic concept. It's recorded here
purely so a future brainstorming pass doesn't have to rediscover that it was raised.
Open questions that would need their own dedicated design work include: what this
implies for the API contract (streaming responses? client-side polling? websockets?),
and what it implies for the frontend UX (partial results appearing first, then updating
in place as later tiers complete). None of these have been thought through yet — this
item is a placeholder to pick up later, not a proposal to build from.

---

## Not on this list

Fuzzy/typo tolerance is no longer a leftover — it's being delivered by
`docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md`.
