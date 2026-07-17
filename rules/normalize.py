import re

import pymorphy3

# Reddit watermarks OCR'd as "riSubredditName" (the slash is read as 'i').
# For any token matching this pattern we also emit the suffix as an extra lemma so that
# concepts like "metallica" still fire on "rimetallica". Safe to do unconditionally: any
# spurious suffix (e.g. "diculous" from "ridiculous") won't appear in the concept vocabulary.
_SUBREDDIT_OCR_RE = re.compile(r'^ri([a-zA-Z]{5,})$', re.IGNORECASE)


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer) -> str:
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()


_TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)

_JOINER_NORMALIZE = str.maketrans({
    "–": "-",   # en dash
    "—": "-",   # em dash
    "'": "'",   # right single quotation mark / smart apostrophe
})


def _normalize_joiners(text: str) -> str:
    return text.translate(_JOINER_NORMALIZE)


def tokenize(text: str) -> list[str]:
    # [^\W_] = letters and digits only; underscores treated as delimiters so that
    # social-media handles like "varg_vikernes" split into ["varg", "vikernes"].
    # A single '-' or "'" between two word-character runs stays part of the token
    # (compounds like "Санкт-Петербурга", contractions like "don't"); every other
    # occurrence of either character — with no word character immediately
    # following — still splits/strips as before. Em/en dashes and the curly
    # apostrophe are normalized to their ASCII counterparts first so there's one
    # canonical joiner per type.
    return _TOKEN_RE.findall(_normalize_joiners(text))


def normalize(text: str, morph: pymorphy3.MorphAnalyzer, min_length: int = 3) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set."""
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length or word.isdigit():
            continue
        lemma = lemmatize_word(word, morph)
        result.add(lemma)
        # r/subreddit OCR artifact: "r/Metallica" → "rimetallica" (slash read as 'i')
        m = _SUBREDDIT_OCR_RE.match(word)
        if m:
            suffix = m.group(1)
            if len(suffix) >= min_length:
                result.add(lemmatize_word(suffix, morph))
        # Trailing punctuation artifact: "SLAYER!!" → "slayerll" (!! read as ll)
        # Strip doubled trailing letter and emit the shorter form.
        if len(word) > min_length + 1 and word[-1].isalpha() and word[-1] == word[-2]:
            shorter = word[:-2]
            if len(shorter) >= min_length:
                result.add(lemmatize_word(shorter, morph))
    return result