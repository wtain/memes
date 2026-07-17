# `tokenize()` punctuation preservation for compound words

Date: 2026-07-17
Status: approved design, not yet implemented

## Problem

`rules/normalize.py`'s `tokenize()` splits on every non-alphanumeric character:

```python
def tokenize(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text, re.UNICODE)
```

This strips internal hyphens and apostrophes along with everything else. A
Russian compound proper noun like `"Санкт-Петербурга"` becomes two tokens,
`["Санкт", "Петербурга"]`, which are then lemmatized independently by
`lemmatize_word()` (pymorphy3) instead of as the single compound
`"санкт-петербург"`. The same loss happens for names like `"O'Brien"`.

## Motivation / uniformity goal

A separate, in-progress feature (Russian lemmatization in
`batch/trends_batch.py`, for GLiNER-extracted named entities) already made
the decision to preserve internal punctuation when lemmatizing entity
phrases, specifically so compound proper nouns don't get mangled. This spec
brings the same punctuation-preserving behavior to `tokenize()`, the shared
function backing the OCR pipeline, so `rules/normalize.py` has one
tokenization rule instead of two divergent ones.

**Cross-check note:** a sibling spec effort was investigating gating
`lemmatize_word()`/pymorphy3 by OCR row language (`en`/`es`/`ru`) — pymorphy3
is a Russian/Ukrainian-only analyzer currently applied unconditionally. As of
this writing that spec does not yet exist under `docs/superpowers/specs/`.
This spec's change is upstream of that one in the pipeline (`tokenize()` →
then, conditionally or not, `lemmatize_word()`) and does not depend on its
outcome, but the two should be cross-checked once both exist, since the
language-gating spec may change *when* `lemmatize_word()` is called on
`tokenize()`'s output.

## Call sites (investigated directly, not assumed)

`tokenize()` is imported by:

- `batch/build_bow.py` — tokenizes OCR text and image-description text to
  build per-language lemma frequency counts (the BOW vocabulary), and
  tokenizes rule keys / concept `words:` entries to compute which lemmas are
  already "covered" by the rules engine (`_build_json_rules_lemma_set`,
  `_build_concepts_lemma_set`) so `build_lemma_clusters` only clusters
  *unmatched* lemmas.
- `rules/lang_plausibility.py`'s `score()` — tokenizes OCR text and looks up
  each token in `wordfreq.zipf_frequency()` to score how "real" the detected
  language is; used by `build_bow.py` and `build_tags_from_ocr.py` to filter
  low-plausibility rows.
- `batch/tools/spot_check_*.py` — ad-hoc dev scripts, not part of the
  pipeline.

`rules/engine.py` (the **old** rules engine) has its own independent `\w+`
tokenization and is **not** affected by this change.

`rules/concept_tagger.py` (the **new**, not-yet-production rules engine) does
not call `tokenize()` for its own YAML `words:` entries — it only calls
`.split()` (whitespace) then lemmatizes each part directly. It does, however,
consume `tokenize()`'s output indirectly: `ConceptTagger.tag()` builds its
lemma bag via `rules.normalize.normalize()`, which calls `tokenize()`
internally. So changing `tokenize()` changes what OCR text lemma-bag entries
look like for concept matching, even though it doesn't change how concept
*word list* entries are lemmatized.

## Empirical validation

Tested directly against the installed `pymorphy3` and `wordfreq` in this
repo's `.venv311` (not assumed from documentation):

- `pymorphy3.MorphAnalyzer().parse("Санкт-Петербурга")` →
  `normal_form == "санкт-петербург"`, `score == 1.0`. Pymorphy3 has built-in
  support for hyphenated Russian compounds and declines them correctly as a
  single token — this is exactly the motivating case, confirmed fixed.
  Other compounds tested (`"интернет-магазина"` → `"интернет-магазин"`,
  `"бизнес-плана"` → `"бизнес-план"`) behave the same way.
- For Latin-script hyphenated/apostrophe'd words, pymorphy3's fallback simply
  lowercases the whole token unchanged (`"Static-X"` → `"static-x"`,
  `"O'Brien"` → `"o'brien"`) — no mangling, no crash.
- `wordfreq.zipf_frequency()` (used by `lang_plausibility.score()`) directly
  recognizes hyphenated compounds as dictionary entries in both English and
  Russian (`"santa-monica"`, `"well-known"`, `"mother-in-law"`,
  `"санкт-петербург"` all return normal zipf scores). This means the
  language-plausibility filter is **not** regressed by preserving hyphens —
  an initial concern that turned out not to apply.
- **Digit-containing compounds join too, and this was checked, not just the
  letter-only case above.** `[^\W_]` (the token-body character class) matches
  digits as well as letters, so `"covid-19"`, `"top-10"`, `"gta-5"` all
  tokenize as one joined token under the new regex, not two (confirmed:
  `_TOKEN_RE.findall("covid-19") == ["covid-19"]`). This means
  `build_bow.py`'s `word.isdigit()` filter, which previously dropped the pure
  `"19"` half after a hyphen-split, no longer gets the chance to — the whole
  compound survives as one BOW/lemma entry going forward. Checked whether
  this hurts `lang_plausibility.score()`: `zipf_frequency("covid-19", "en")`
  returns `3.85` and `zipf_frequency("top-10", "en")` returns `5.12` — both
  are recognized dictionary entries in `wordfreq`'s own corpus, so these
  specific common cases score identically to before, not worse. A truly
  obscure alphanumeric compound could still come back unrecognized, but
  that's the same class of risk the letter-only compound case above already
  accepts (e.g. an obscure two-word place name) — not a new failure mode
  introduced by including digits in the joiner's character class.

## Design

### Regex change

Replace the tokenizing regex so a single hyphen or apostrophe between two
runs of word characters is treated as part of the token, while every other
character (including underscore, which must keep splitting deliberately —
see below) still splits/strips exactly as before:

```python
_TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)

def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalize_joiners(text))
```

`[^\W_]` = "word character, excluding underscore" (unchanged from today).
The optional trailing group `(?:['-][^\W_]+)*` allows the token to continue
across a `'` or `-` only when a word character immediately follows — a
joiner with punctuation, whitespace, or end-of-string on either side is
never consumed, so it still acts as a separator there.

Underscore is deliberately **not** added as a joiner: `_SUBREDDIT`-style
handles like `"varg_vikernes"` must keep splitting into `["varg",
"vikernes"]` per the existing comment in the file, and this is unaffected —
underscore remains excluded by `[^\W_]` and is never treated as a joiner
character.

### Unicode joiner normalization

Rather than adding em-dash, en-dash, and the curly/smart apostrophe as
additional alternatives inside the regex character class, normalize them to
their plain ASCII counterparts as a preprocessing step, so the regex only
ever has to know about one canonical joiner per type:

```python
_JOINER_NORMALIZE = str.maketrans({
    "–": "-",   # en dash  –
    "—": "-",   # em dash  —
    "’": "'",   # right single quotation mark / smart apostrophe  ’
})

def _normalize_joiners(text: str) -> str:
    return text.translate(_JOINER_NORMALIZE)
```

This is applied only inside `tokenize()`, transiently, on the text passed in
— it does not mutate or persist anywhere (OCR text in the database is
untouched; only the token stream produced from it changes).

**Left single quotation mark (`‘`, `'`) is intentionally excluded** from
this table. It's the mirror-image *opening* quote character and, unlike
`’`, is not also overloaded as an apostrophe in any common typographic
convention — it should never behave as a word-internal joiner, so it's left
alone and continues to split tokens as any other punctuation does today.

### Why normalizing curly apostrophe to `'` is safe despite its dual role

`’` is genuinely ambiguous in real text — it's used both as an
apostrophe (`"don't"`) and as a closing single quotation mark
(`'quoted text'`). Normalizing it unconditionally to `'` and letting the same
joiner rule apply to both roles turns out to be safe in practice, because the
regex only treats `'` as a joiner when a word character *immediately*
follows it with no space. A closing quote is essentially always followed by
whitespace, punctuation, or end-of-string (`'quoted'`, next char is a space
or period) — so it naturally falls through to being a plain separator, same
as before. Verified directly:

```
"YOLO'"            -> tokens=['YOLO']            # trailing quote dropped, not joined
"'sup"             -> tokens=['sup']              # leading quote dropped, not joined
"it's a 'quote' test" -> tokens=["it's", 'a', 'quote', 'test']   # both roles resolved correctly
"don't"            -> tokens=["don't"]            # contraction preserved as one token (new, intended)
```

## Edge case behavior (verified against the actual regex + normalization)

| Input | Tokens | Notes |
|---|---|---|
| `Санкт-Петербурга` | `['Санкт-Петербурга']` | motivating case |
| `Санкт--Петербурга` (double hyphen) | `['Санкт', 'Петербурга']` | second `-` isn't immediately followed by a word char inside the joiner group on the first pass; graceful degradation to a split, not an error |
| `-leading` | `['leading']` | leading hyphen not consumed |
| `trailing-` | `['trailing']` | trailing hyphen not consumed |
| `O'Brien` | `["O'Brien"]` | motivating case for apostrophe |
| `rock-n'-roll` (mixed joiners, no valid word char after `-roll`'s leading `'`) | `['rock-n', 'roll']` | ambiguous punctuation cluster; degrades to a split rather than one garbled token |
| `AWESOME—NOT` (em dash, no surrounding spaces) | `['AWESOME-NOT']` | see **Accepted risk** below |
| `well–known` (en dash) | `['well-known']` | intended |
| `varg_vikernes` | `['varg', 'vikernes']` | underscore still splits — deliberately unchanged |
| `Guns'n'Roses` | `["Guns'n'Roses"]` | single token; see call-site check below |
| `covid-19` | `['covid-19']` | digits count as word characters too — joins same as a letter-only compound; see Empirical validation |
| `top-10` | `['top-10']` | same; both this and `covid-19` are recognized `wordfreq` entries, so plausibility scoring is unaffected for these common cases |

## Known behavior changes and accepted risks

**`static-x` concept phrase match regresses (low risk, not-yet-production).**
`batch/data/tagging/concepts.metal.yaml`'s `static-x` concept lists
`"static x"` (two words) as a matchable phrase, relying on today's
`tokenize()` splitting OCR text `"Static-X"` into two separate tokens
`["static", "x"]` so the two-word phrase check passes. After this change,
`"Static-X"` tokenizes to the single token `"static-x"`, and the phrase
`"static x"` no longer matches it. This is the **only** confirmed regression
found across `rules.json`, `concepts.metal.yaml`, and `tags.metal.yaml` —
every other punctuated string in that data (`"alissa white-gluz"`,
`"proto-punk"`, `"Guns'n'Roses"`) exists only as a tag *output value*, never
as a `words:` match entry, so it isn't affected. `rules/concept_tagger.py`
(the engine that owns this data) is explicitly **not yet wired into the main
pipeline** per the project's architecture docs, so this has no production
impact today. Recommended follow-up (out of scope for this spec, tracked
here for whoever picks it up): add `"static-x"` as an explicit alternate
spelling in that concept's `words:` list.

**Em/en-dash used as a rhetorical separator with no surrounding whitespace
merges into one token.** Meme captions sometimes use an em-dash as a
stylistic pause/contrast with tight kerning and no spaces (e.g.
`"AWESOME—NOT"`). Normalizing dash characters to `-` before tokenizing means
this merges into a single token `"awesome-not"` instead of two separate
lemmas `"awesome"` and `"not"`. This was raised as a concern during design
and the decision was made to accept it: the failure mode is bounded (a
missed/misshapen lemma, not data corruption or a crash — same class of risk
the plain-hyphen case already carries for any human-typed hyphen used as a
separator), and it symmetrically enables the intended dash-as-compound-joiner
behavior for genuine cases like `"well–known"`. No mitigation is proposed;
documented here as an intentional trade-off, not an oversight.

**Contractions now tokenize as one word.** `"don't"` was previously
`["don", "t"]`; it's now `["don't"]`. This is an intended side effect of
adding `'` as a joiner, not a bug — it's arguably more correct (matches how
`wordfreq` and `pymorphy3` already treat these strings), but note it here
since it changes BOW vocabulary content for any English/Spanish OCR text
containing contractions.

## Downstream effects (inherited automatically, no code changes needed there)

Because all three real call sites (`build_bow.py`, `lang_plausibility.py`,
and transitively `rules/normalize.normalize()` used by
`rules/concept_tagger.py`) call `tokenize()` rather than reimplementing
tokenization, they all pick up the new behavior for free from this one
change:

- `build_bow.py`'s OCR/description BOW vocabulary will contain hyphenated
  and apostrophe'd compounds as single lemmas going forward, changing the
  shape (not necessarily the size) of `bow.<lang>.<env>.json` output on the
  next full rebuild.
- `build_bow.py`'s rule/concept coverage sets (`_build_json_rules_lemma_set`,
  `_build_concepts_lemma_set`) will likewise tokenize any hyphenated/
  apostrophe'd rule keys or concept words as single lemmas rather than
  splitting them — consistent with how `concept_tagger.py`'s own
  `_load_concepts` already lemmatizes such entries as whole units via
  `.split()` (whitespace only). This closes a pre-existing inconsistency
  between the two code paths rather than introducing a new one.
- `lang_plausibility.score()`'s known/unknown word ratio is unaffected in
  aggregate (per the `wordfreq` testing above) but will score hyphenated
  compounds as single lookups instead of two independent ones.

## Testing considerations (for whoever implements this — not written here)

This spec is design-only; no code or tests are written as part of it. For the
implementation step, `tests/rules/test_engine.py` and
`tests/rules/test_concept_tagger.py` don't exercise `rules/normalize.tokenize()`
directly (they go through `RulesEngine`, which has its own separate `\w+`
regex, or through `ConceptTagger`). A new `tests/rules/test_normalize.py`
(doesn't currently exist) is the natural home for direct unit tests of
`tokenize()`, covering at minimum every row in the edge-case table above.
`tests/rules/test_lang_plausibility.py`'s existing tests should continue to
pass unchanged (verified the specific words/sentences it uses don't contain
joiners that would change tokenization).

## Out of scope

- Any change to `batch/trends_batch.py` or its (separately, concurrently
  designed) entity-lemmatization behavior.
- The `lemmatize_word()`/pymorphy3 language-gating decision (sibling spec,
  not yet written as of this document).
- Extending joiner characters beyond `-` and `'` (e.g. non-breaking hyphen,
  minus sign, low quotation marks) — not part of the motivating cases, and
  deliberately left as plain separators to avoid scope creep.
- Fixing the `static-x` concept's `words:` entry — flagged above as a
  follow-up, not performed here.
- Any implementation, i.e. actually editing `rules/normalize.py`. This
  document is the design only.
