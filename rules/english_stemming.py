"""
Lightweight English word-form normalization for search matching, via the
`snowballstemmer` package (rule-based Porter/Snowball stemming -- no ML
model, no dictionary, zero dependencies). Not a full lemmatizer: it
doesn't produce a real dictionary word ("batteries" -> "batteri") and
won't unify irregular forms a POS-aware lemmatizer would ("better" stays
"better", not "good") -- an accepted tradeoff over a heavier dependency
(spaCy/NLTK), matching how OCRLemma.lemma was never guaranteed to be a
real word for Russian either. See
docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.
"""
import re

import snowballstemmer

_stemmer = snowballstemmer.stemmer("english")

_LATIN_WORD_RE = re.compile(r'^[a-z]+$')


def stem_english_word(word: str) -> str:
    return _stemmer.stemWord(word.lower())


def is_latin_word(word: str) -> bool:
    """True if word consists entirely of lowercase Latin letters -- the
    only input stem_english_word() is meaningful for. Deliberately narrow
    (no hyphens/apostrophes/digits): compounds like "well-known" and
    contractions like "don't" fall through unstemmed for now -- see the
    design doc's disclosed limitations."""
    return bool(_LATIN_WORD_RE.match(word))
