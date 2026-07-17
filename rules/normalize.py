import re

import pymorphy3

# Reddit watermarks OCR'd as "riSubredditName" (the slash is read as 'i').
# For any token matching this pattern we also emit the suffix as an extra lemma so that
# concepts like "metallica" still fire on "rimetallica". Safe to do unconditionally: any
# spurious suffix (e.g. "diculous" from "ridiculous") won't appear in the concept vocabulary.
_SUBREDDIT_OCR_RE = re.compile(r'^ri([a-zA-Z]{5,})$', re.IGNORECASE)

# Languages pymorphy3 can meaningfully lemmatize — only Russian dictionaries are
# installed (pymorphy3-dicts-ru). There is no pymorphy3-dicts-uk in any
# requirements file today; add "uk" here if that ever changes.
LEMMATIZABLE_LANGUAGES = frozenset({"ru"})


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    language=None (default): unchanged legacy behavior — always call morph.parse(),
    relying on pymorphy3's own script-based fallback (real RU dictionary lookup for
    Cyrillic, LatinAnalyzer passthrough-lowercase for Latin script). Used by callers
    with no per-word language signal (concept/rules vocabulary loading, dev tools,
    tests).

    language is a string not in LEMMATIZABLE_LANGUAGES (including "unknown" for
    NULL/undetected OCR rows): pymorphy3 is skipped entirely; returns word.lower().
    Rows known, or assumed, not to be Russian never reach an analyzer that was never
    designed for them.

    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization.

    Note for callers outside this module (e.g. trends_batch's lemmatize_phrase): the
    None-means-"run pymorphy3 anyway" default here is a per-call fallback for callers
    with no language signal at all. A caller that already knows its own language ahead
    of time doesn't rely on this default — it simply never calls this function for
    non-lemmatizable content in the first place.
    """
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()


_TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)

_JOINER_NORMALIZE = str.maketrans({
    "–": "-",   # en dash
    "—": "-",   # em dash
    "’": "'",   # right single quotation mark / smart apostrophe
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


def normalize(
    text: str,
    morph: pymorphy3.MorphAnalyzer,
    min_length: int = 3,
    language: str | None = None,
) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set."""
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length or word.isdigit():
            continue
        lemma = lemmatize_word(word, morph, language)
        result.add(lemma)
        # r/subreddit OCR artifact: "r/Metallica" → "rimetallica" (slash read as 'i')
        m = _SUBREDDIT_OCR_RE.match(word)
        if m:
            suffix = m.group(1)
            if len(suffix) >= min_length:
                result.add(lemmatize_word(suffix, morph, language))
        # Trailing punctuation artifact: "SLAYER!!" → "slayerll" (!! read as ll)
        # Strip doubled trailing letter and emit the shorter form.
        if len(word) > min_length + 1 and word[-1].isalpha() and word[-1] == word[-2]:
            shorter = word[:-2]
            if len(shorter) >= min_length:
                result.add(lemmatize_word(shorter, morph, language))
    return result