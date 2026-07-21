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
    keep_digit_tokens: bool = False,
) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set.

    keep_digit_tokens=True keeps a pure-digit token (still subject to
    min_length) as a literal lemma instead of dropping it — used by search
    indexing/matching, where numeric queries (years, model numbers) should
    still be findable. Default False preserves the original tag/concept-
    vocabulary behavior for every other caller.
    """
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length:
            continue
        if word.isdigit():
            if keep_digit_tokens:
                result.add(word)
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


def lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str:
    """Lemmatize each whitespace-delimited chunk of text, preserving
    internal punctuation (e.g. hyphens in compound names) and word order.

    Does not accept a language parameter: always performs real Russian lemmatization
    via lemmatize_word(). Callers are responsible for ensuring the input text is
    already known to be Russian; this function does not do its own language gating.
    Unlike lemmatize_word() (which has language-aware fallbacks) or normalize()
    (which accepts an optional language parameter), lemmatize_phrase() is designed for
    callers in specialized contexts (e.g., trends_batch) that have already determined
    language at a higher level and invoke this function only for Russian content.

    Uses simple whitespace splitting (text.split()) rather than tokenize() to preserve
    ordered, readable phrases as single units. tokenize() is designed for bag-of-words
    extraction where word order and internal punctuation do not matter — it would break
    compound proper nouns like "Санкт-Петербург" at their internal hyphens and lose
    the multi-word structure. lemmatize_phrase() keeps each whitespace-delimited token
    whole, so compound names and hyphenated expressions remain intact and readable."""
    return " ".join(lemmatize_word(chunk, morph) for chunk in text.split())