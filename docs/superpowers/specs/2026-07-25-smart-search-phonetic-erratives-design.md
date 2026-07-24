# Smart Search: Phonetic Erratives Normalization — Design

Status: Approved direction, written up in full after empirical validation.

## Problem

Russian internet slang ("erratives" — deliberate misspellings like `превед`,
`аффтар`, `кросавчег`, `жжот`) appears in meme OCR text and doesn't match its
canonical spelling. Neither exact match nor the existing trigram fuzzy
fallback (`docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md`)
catches this class of mismatch — it was explicitly deferred there and again
in `docs/superpowers/specs/drafts/2026-07-24-smart-search-leftovers-draft.md`.

## Why trigram similarity doesn't solve this (empirical finding)

Tested against the real `metal` database: erratives score 0.083–0.25 against
their canonical form, while unrelated short words score up to 0.33. The
ranges overlap — no trigram threshold separates "errative vs. its canonical
form" from "two unrelated short words." This is consistent with the
project's existing documented lesson in `batch/rules_engine.md`
("Deterministic by default — global fuzzy on short Russian lemmas produces
false positives").

## Why a phonetic-only approach doesn't solve this either (empirical finding)

Russian Soundex/Metaphone algorithms (tested via the `fonetika` PyPI library,
not adopted as a dependency — see below) do correctly collapse all tested
erratives to the same code as their canonical form. But they also collapse
genuinely distinct real words: `кот`/`код` → `КАТ`, `дом`/`дым` → `ДАМ`,
`стол`/`стал` → `СТАЛ`, `парта`/`порта` → `ПАРТА`. A combined
trigram+phonetic signal was also tried and empirically invalidated: the
erratives' trigram scores (0.083–0.25) overlap with the phonetic
false-positive pairs' trigram scores (0.143–0.333) — no threshold
discriminates between them on that axis either.

## The discriminating signal: `is_known`

pymorphy3's `morph.parse(word)[0].is_known` flags whether a word form was
found via genuine dictionary lookup vs. produced by pymorphy3's
unknown-word-guessing fallback. Tested against both failure classes:

- Erratives (`превед`, `аффтар`, `кросавчег`, `жжот`): all `is_known=False`.
- The phonetic false-positive pairs above (`кот`, `код`, `дом`, `дым`,
  `стол`, `стал`, `парта`, `порта`): all `is_known=True`.

This cleanly separates the two failure classes phonetic matching would
otherwise conflate. Caveat (disclosed and accepted): `is_known=False` also
fires for most modern slang/loanwords generally, not exclusively erratives —
the gate reads as "not a standard dictionary word," a broader category that
happens to contain erratives. This is fine: the goal is search recall for
non-dictionary tokens, and a slang word phonetically expanding to other
similar-sounding non-dictionary words is a much lower-stakes false positive
than two common, unrelated dictionary words silently merging.

Verified separately: checking `is_known` on the *already-lemmatized* lemma
string (what `matching_image_ids` has in hand) gives identical results to
checking it on the original raw query token, for every word tested above —
so this can be implemented entirely inside `repository/ocr_lemmas.py` using
the module's existing cached `_get_morph()` analyzer, without touching the
shared `rules/normalize.py` interface used by many other callers.

## Design

**Matching order per lemma**, inside `matching_image_ids`:

```
exact match
  → if empty and len(lemma) >= FUZZY_MIN_LEMMA_LENGTH:
      trigram fuzzy match (unchanged, existing behavior)
      → if lemma is Cyrillic
          and len(lemma) >= PHONETIC_MIN_LEMMA_LENGTH
          and is_known(lemma) is False:
            union in phonetic match
```

Phonetic matching is an *additional* signal unioned alongside trigram
(both fire independently once exact match fails and the length guard
passes), not a sequential "try trigram, then try phonetic" chain — the two
catch different error classes (character-level OCR noise vs. sound-alike
substitution) and there's no reason one should suppress the other.

Trigram's own gate (length only) is unchanged; it doesn't need the
`is_known` gate because its false-positive risk profile is already
acceptable without it (that's what the fuzzy-matching design already
shipped and validated).

**Guards, and why each is needed:**

- **Cyrillic check**: `russian_metaphone` is a Russian-specific
  transformation. Without this guard, every non-Russian query token would
  also get `is_known=False` (pymorphy3's dictionary is Russian-only) and
  spuriously attempt phonetic transformation on Latin-script text.
- **Length guard** (`PHONETIC_MIN_LEMMA_LENGTH`, new setting, default `5` —
  same starting value as `FUZZY_MIN_LEMMA_LENGTH`, independently tunable):
  even with `is_known` filtering out the specific real-word collisions found
  during testing, very short unknown tokens (2-3 characters — OCR noise
  fragments, foreign fragments) have fewer distinguishing phonemes and a
  higher chance of coincidental phonetic collision. Kept as a second,
  independent safety net rather than relying on `is_known` alone.
- **`is_known` gate**: the discriminating signal described above.

**Scope reduction**: phonetic matching queries `OCRLemma` only, not
`ImageTag` (unlike trigram, which unions both). Tags are assigned by the
rules/concept-tagging pipeline from a controlled vocabulary
(`rules/concept_tagger.py`, `tags.<env>.yaml`) — a tag value is essentially
never itself a literal errative string, so extending phonetic lookup to
`ImageTag` would add a write-path column + migration to a second table for
a code path that would almost never fire. If this assumption turns out
wrong in practice, extending it later is a small, isolated change.

## Algorithm: ported Russian Metaphone

Ported (not depended on) from the `fonetika` PyPI library
(`roddar92/russian_soundex`, MIT-licensed) — chosen over Soundex after
empirical testing showed Metaphone more precise (matching this project's
`RussianMetaphone` behavior with all its optional flags left at their
defaults: no phoneme-sequence reduction, no `-его/-ого` ending rewrite, no
"deafen all consonants" mode, no vowel-stripping). Not adopted as a runtime
dependency because `fonetika`'s `soundex.py` unconditionally imports
`pymorphy2` (the old, unmaintained fork) at module level even though the
core transform doesn't need it — this project already uses `pymorphy3`.

The ported implementation was verified byte-for-byte against the actual
`fonetika.metaphone.RussianMetaphone().transform()` reference output across
29 test words (all previously-tested erratives, false-positive pairs, and
general Russian vocabulary) — 0 mismatches.

Pipeline (default-flags `RussianMetaphone.transform`, reproduced from
`fonetika/metaphone.py` + `fonetika/ruleset.py` + `fonetika/config.py`):

1. Lowercase.
2. Consonant+iotated-vowel simplification: `<consonant>я/ю/е/ё` →
   `<consonant>а/у/э/о` (iotation doesn't apply directly after a consonant).
3. `j`-insertion: word-initial (or after `ъ`/`ь`) `я/ю/е/ё` → `jа/jу/jэ/jо`;
   after a vowel, `я/ю/е/ё` → `<vowel>jа/jу/jэ/jо`.
4. `й` → `j`; `ъ`/`ь` removed.
5. `и[еио]` → `и` (collapses these endings).
6. Collapse repeated adjacent identical letters (`жжот` → `жот` before
   further steps).
7. Vowel bucketing: all ten vowels collapse to one of three buckets —
   `а/я/о/ы` → `А`, `и/е/ё/э` → `И`, `у/ю` → `У`.
8. Terminal/pre-voiceless devoicing: `б/з/д/в/г` → `п/с/т/ф/к` when
   word-final or not followed by a sonorant (`л/м/н/р`) or a vowel (checked
   against the *original* vowel set, lowercased, even though the character
   being checked has already been vowel-bucketed to uppercase — this
   ordering detail is what made the initial port's first attempt
   mismatch on `дом`/`любовь`/`нравится` etc.; fixed and reverified).
9. Uppercase and return.

New module: `rules/phonetic.py`, exporting `russian_metaphone(word: str) -> str`.
Pure function, no dependencies beyond `re`. A short module comment credits
the algorithm reference (not copied code).

## Storage

New column on `OCRLemma`: `phonetic_code` (`String`, not null), with a plain
btree index (`ix_ocr_lemmas_phonetic_code`) — equality lookup only, no
trigram/GIN needed since phonetic matching is exact-code lookup, not
similarity search.

Computed once per row at write time in
`OCRLemmasSaver.add_lemmas()` (`repository/ocr_lemmas.py`), alongside the
existing `lemma` value — no per-query recomputation cost for the index
side. Note this denormalizes: every row sharing the same `lemma` string
computes the same `phonetic_code`, but `OCRLemma` already stores `lemma`
per-`(image_id, lemma)` row without a separate lemma dictionary table, so
this matches the existing schema shape rather than introducing a new kind
of redundancy.

**Migration**: one Alembic revision, chained from the current head
(`6fc209b37e8b_add_ocr_lemmas_trigram_index`):
1. Add `phonetic_code` as nullable.
2. Data migration: iterate existing rows in Python, compute
   `russian_metaphone(lemma)`, batched `UPDATE`.
3. Alter the column to `NOT NULL`.
4. Create the btree index.

This is done inside the migration itself (not "just rerun the batch job")
because `OCRLemmasSaver.add_lemmas()` upserts with
`ON CONFLICT DO NOTHING` — a rerun of `build_ocr_lemmas.py` against
already-indexed images would silently no-op on every existing row and never
backfill the new column.

## Settings

`environments/settings.yaml`, `search` domain group (alongside existing
`fuzzy_min_lemma_length` / `fuzzy_similarity_threshold`):

```yaml
search:
  fuzzy_min_lemma_length: 5
  fuzzy_similarity_threshold: 0.35
  phonetic_min_lemma_length: 5
```

## Testing

- `tests/rules/test_phonetic.py` (no DB, no I/O — matches this root's
  existing convention): unit tests for `russian_metaphone()` covering the
  full set of erratives and false-positive pairs already validated during
  design, asserting exact expected codes.
- `tests/integration/` (real Postgres): end-to-end `matching_image_ids()`
  cases —
  - an errative query (e.g. `превед`) matches an image whose OCR lemma is
    its canonical form, when the canonical form is only reachable via the
    phonetic path (not exact, not trigram).
  - a real dictionary word that happens to phonetically collide with
    another real word (e.g. `код`) does *not* pull in matches for the
    other word (`кот`), because `is_known=True` blocks the phonetic
    fallback for dictionary words.
  - a short (below `PHONETIC_MIN_LEMMA_LENGTH`) unknown token does not
    trigger phonetic matching.
  - a non-Cyrillic query token does not trigger phonetic matching.

## Out of scope (unchanged from prior drafts)

- LLM-assisted dictionary mining for erratives that don't reduce to a clean
  phonetic rule — placeholder already captured in
  `docs/superpowers/specs/drafts/2026-07-25-erratives-llm-dictionary-mining-draft.md`.
- BM25/ranking.
- Extending phonetic matching to `ImageTag` (see Scope reduction above).
