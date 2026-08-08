"""
Fixed, curated equivalence tables for text canonization -- narrow, hand-maintained lists
rather than a general phonetic/fuzzy system. See
docs/superpowers/specs/2026-08-08-search-canonization-design.md.
"""

# British -> American spelling. Only the British form needs a key; the American form already
# passes through unchanged (it's what index/query text canonicalizes toward). Covers only
# base/dictionary forms -- inflected forms not listed here (e.g. "categorising") are not
# specially handled; the existing Snowball stemmer may separately unify some of them once
# canonicalized, but this isn't guaranteed. Deliberately a fixed list, not a suffix rule --
# see design doc's Out-of-scope section for why (the "prise"/"prize" collision risk).
SPELLING_VARIANTS: dict[str, str] = {
    # -ise/-ize (and -yse/-yze) verbs
    "realise": "realize", "organise": "organize", "recognise": "recognize",
    "categorise": "categorize", "initialise": "initialize", "customise": "customize",
    "analyse": "analyze", "paralyse": "paralyze", "finalise": "finalize",
    "characterise": "characterize", "apologise": "apologize", "criticise": "criticize",
    "emphasise": "emphasize", "memorise": "memorize", "minimise": "minimize",
    "maximise": "maximize", "optimise": "optimize", "summarise": "summarize",
    "standardise": "standardize", "specialise": "specialize", "familiarise": "familiarize",
    "prioritise": "prioritize", "capitalise": "capitalize", "symbolise": "symbolize",
    "sympathise": "sympathize", "utilise": "utilize",
    # -isation/-ization nouns
    "realisation": "realization", "organisation": "organization",
    "categorisation": "categorization", "initialisation": "initialization",
    "customisation": "customization", "optimisation": "optimization",
    "standardisation": "standardization", "specialisation": "specialization",
    "minimisation": "minimization", "maximisation": "maximization",
    "summarisation": "summarization", "prioritisation": "prioritization",
    "capitalisation": "capitalization", "utilisation": "utilization",
    # -our/-or
    "colour": "color", "favour": "favor", "favourite": "favorite", "humour": "humor",
    "flavour": "flavor", "honour": "honor", "neighbour": "neighbor",
    "behaviour": "behavior", "colourful": "colorful",
    # -re/-er
    "centre": "center", "theatre": "theater", "litre": "liter", "fibre": "fiber",
    "metre": "meter",
}

# Negative ("n't") contractions -> their expansion, keyed on the apostrophe-stripped lowercase
# form so both "don't" and "dont" (OCR frequently drops apostrophes) hit the same entry.
# Each value is lemmatized word-by-word and added to the result set like any other word,
# subject to the caller's own min_length filter -- so e.g. "don't"/"dont" both contribute
# {"not"} (the same lemma set literal "do not" text already produces today, since "do" is
# below the default min_length), closing the equivalence via the existing AND-of-lemmas
# matching with no new machinery. Only "n't" forms are covered -- other contractions ("it's",
# "that's", "let's", "I'm") are semantically ambiguous ("it's" = "it is" or "it has"?) and
# don't reduce as cleanly; out of scope for this narrow pass.
CONTRACTION_EXPANSIONS: dict[str, list[str]] = {
    "dont": ["do", "not"],
    "cant": ["can", "not"],
    "wont": ["will", "not"],
    "isnt": ["is", "not"],
    "arent": ["are", "not"],
    "wasnt": ["was", "not"],
    "werent": ["were", "not"],
    "doesnt": ["does", "not"],
    "didnt": ["did", "not"],
    "hasnt": ["has", "not"],
    "havent": ["have", "not"],
    "hadnt": ["had", "not"],
    "wouldnt": ["would", "not"],
    "couldnt": ["could", "not"],
    "shouldnt": ["should", "not"],
}
