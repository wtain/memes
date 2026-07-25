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

## 3. Non-Russian lemmatization (English, Spanish)

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

## 4. Accepted OCR-language cross-detection asymmetry (documented limitation, not planned work)

Not a deferred feature — a permanently accepted tradeoff, included here only for a
complete picture. Index-time lemmatization trusts each OCR row's own detected language
(`ru`/`en`/`es`); a Cyrillic row EasyOCR confidently but wrongly tags as non-Russian gets
lowercase-only treatment, while the same word in a search query still gets full
Russian lemmatization. Fixing this means fixing OCR language misdetection itself — a
different problem than search. Documented via code comment
(`docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md`), not
revisited here.

---

## Not on this list

Fuzzy/typo tolerance is no longer a leftover — it's being delivered by
`docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md`.
