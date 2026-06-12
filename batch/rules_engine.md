# New Rules Engine Design

## Rationale

Current rules engine approach doesn't scale and has problems with adding new rules, its syntax is not convenient. 
A new rules engine is proposed.

## Details

There are words, concepts and tags. Words are sourced from OCR or potentially from Ollama descriptions. Word occurrence 
in text could signal specific topic, though there are ambiguous topics. E.g. word "salary" could go to "life" topic or 
"work" topic. Also due to imperfection of OCR and erratives (on-purpose misspellings) in memes lemmatisation not always 
working good, thus recognising the same word in different shapes as different words. It is proposed to introduce 
concepts, that would embrace multiple words with similar meaning into one concept, e.g. "salary" and "paycheck" are 
semantically the same, still being different words.
Then, when we have concepts extracted, we may have multiple different concepts. Now we are to extract meaningful tags 
to make memes be searchable via tags that extract the semantics. For that we consider having voting system - each 
concept could contribute to some tag (key and value, e.g. "topic:work"). Each concept could map to different 
topics/tags with different weight - e.g. "salary" maps to "topic:work" with weight 0.8 and to topic "life" with 
weight 0.2. Then tag weights are summed up and a threshold is applied to extract specific tag. There could also be 
negative weights, that would allow to fine-tune ambiguous words. Thresholds must also be different for different tags, 
that may be separate rule plane is to be introduced.

## Requirements

We need to develop a format for these rules (may be, different files/formats for different planes) and a rules engine 
(a new one) that would consume them and work as "text" -> bag of words (post-processed, error-corrected, lemmatised) -> 
tags. These rules files should be editable by a human. For bag of words logic see build_bow.py batch.
This new rules engine would be the core of a new batch for deriving tags from OCR and would change 
existing one.
We are still to define what is quality for this rules engine.

## Decisions

- **Voting is presence-based.** A concept contributes its weights once per text, no matter how many of its words
  occur or how many times. Meme texts are short; repetition must not push an ambiguous concept over a threshold.
- **Word matching is explicit aliases + opt-in fuzzy.** Each concept lists its words (lemmatized at load).
  Fuzzy matching (rapidfuzz) is enabled per word entry with its own threshold, only where it pays off.
  Deterministic by default — global fuzzy on short Russian lemmas produces false positives.
- **Concepts are flat.** A concept maps only to tag votes (possibly several tags). No concept→concept implication,
  no recursion. Shared semantics (e.g. all sex-related concepts also voting for `тема:секс`) is expressed by
  repeating the vote in each concept.
- **Rule files are YAML.** Comments are essential for documenting why a weight is 0.3 or why an errative is listed.
  PyYAML is already a dependency.
- **Quality = golden set + coverage metrics + version diffing** (see Quality section).

## Pipeline

Per meme text (OCR or description):

```
text
  → normalize: tokenize (\w+), drop words < min length, drop digits, lemmatize (pymorphy3)
  → lemma bag (set of lemmas)
  → concept matching: vocabulary entries matched against the lemma set → set of fired concepts
  → voting: each fired concept adds its weights to (tag_key, tag_value) accumulators
  → thresholding: emit tag if score ≥ threshold for that tag
  → tags
```

Normalization must be a shared module (e.g. `rules/normalize.py`) used identically by the engine,
`build_bow.py`, and the quality tooling — otherwise coverage stats drift from engine behavior.

## Rule planes / file formats

Two files per profile, living in `batch/data/tagging/` (new directory, to avoid confusion with the
embedding-based `text-concepts.*.json` which use "concept" for a different thing).

### 1. Concept plane — `concepts.<profile>.yaml`

One block per concept: its vocabulary (word plane) and its votes (vote plane). Adding a concept touches one place.

```yaml
зарплата:
  words: [зарплата, salary, paycheck, получка, зп]
  fuzzy:
    - word: зарплата          # catches OCR mangles like "зорплата"
      threshold: 85
  votes:
    тема:работа: 0.8
    тема:жизнь: 0.2

гей:
  words: [гей, пидор, пидорас, пидарас, пыдар, лесбиянка, педик]
  votes:
    тема:гей: 1.0
    тема:секс: 1.0

футбол:
  words: [футбол, гол, пенальти]
  votes:
    тема:спорт: 1.0
    тема:игры: -0.5         # negative vote: "игра" words near football are not about gaming
```

Semantics:

- `words`: matched exactly against the text's lemma set (entries are themselves lemmatized at load,
  so inflected forms can be listed as-is).
- Multi-word entries (phrases, e.g. `офисный планктон`) match when **all** their lemmas are present in the text.
- `fuzzy`: optional; each entry matches via rapidfuzz ratio against text lemmas with its own `score_cutoff`.
- A word may appear in several concepts (homonyms, e.g. "очко"); both concepts fire. The loader reports
  duplicates so accidental ones are visible.

### 2. Tag plane — `tags.<profile>.yaml`

Registry of all tags with thresholds. Doubles as validation: a vote referencing a tag not declared here
is a load error (catches typos — impossible with the current format).

```yaml
defaults:
  threshold: 1.0

tags:
  тема:работа: {}                    # uses default threshold
  тема:жизнь:
    threshold: 1.5                   # noisy tag, demand stronger evidence
  тема:секс: {}
  тема:гей: {}
  тема:спорт: {}
  тема:игры: {}
```

Threshold resolution: per tag value → per tag key (optional `тема: {threshold: ...}` entry) → global default.

## Engine API

```python
engine = ConceptTagger.load("batch/data/tagging", profile="general")
result = engine.tag(text)

result.tags             # [("тема", "работа"), ...]
result.trace            # explainability: word → concept → (tag, score) chain
```

`trace` is required, not optional: weight tuning and golden-set failure analysis are impossible without
seeing *why* a tag fired or missed the threshold.

Scoring: weights are plain-summed (negative votes simply subtract; a strongly negative weight acts as a veto).
Final score compared against the tag's threshold with ≥.

## Quality

Three mechanisms, all part of the design:

1. **Golden set** (`batch/data/tagging/golden.<profile>.yaml`): hand-labeled sample (~100–300 memes) of
   `{image, expected tags, optionally forbidden tags}`. A script (`eval_rules.py`) runs the engine over the
   golden set's OCR texts and reports per-tag precision/recall/F1. Serves as the regression test for rule edits.
2. **Coverage metrics**: % of memes receiving ≥1 tag; % of corpus lemma *mass* (occurrence-weighted, not just
   unique lemmas) covered by the vocabulary. `build_bow.py`'s unmatched report is adapted to read the new
   vocabulary; the unmatched file remains the worklist for extending rules.
3. **Version diffing** (`diff_rules.py`): runs two rule versions over the full corpus and reports which images
   gained/lost which tags, with per-tag summary counts and sample images. Human-reviews a rule change's blast
   radius before committing it.

## Migration

1. One-off converter seeds the new YAML from `rules.general.json`: alias chains become `words` lists of the
   target concept; tag dicts become votes with weight 1.0; all tags get threshold 1.0 (with all weights 1.0 and
   thresholds 1.0 the new engine reproduces current binary behavior exactly — a clean baseline for the diff tool).
2. New batch (rework of `build_tags_from_ocr.py`) uses `ConceptTagger`; same `TagsSaver` integration.
   The engine is text-source-agnostic; the descriptions batch migrates the same way afterwards.
3. Old `rules/engine.py` and `rules.general.json` are retired once the diff between old and new output is reviewed.

## Proposed defaults (open to revision)

- Negative weights: plain summation, no separate veto primitive.
- Single mixed-language vocabulary (Russian + English lemmas don't collide); no per-language rule scoping.
- Homonym words allowed in multiple concepts, reported at load time.
- New files live under `batch/data/tagging/`; profile suffix convention (`.general`, `.metal`) is kept.
- Tag emission source stays `"OCR"` in the tags table (no schema change).
- `ignore-words.general.json` stays as-is (it serves `build_bow.py`, not the engine — the engine ignores
  unknown lemmas by construction).