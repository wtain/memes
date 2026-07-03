from wordfreq import zipf_frequency

from rules.normalize import tokenize

_MIN_ALPHA_TOKENS = 2
_ZIPF_KNOWN_THRESHOLD = 1.0  # below this, wordfreq effectively hasn't seen the word


def score(text: str, language: str) -> float | None:
    """
    Fraction of alphabetic tokens in `text` that are recognized words in
    `language`, per wordfreq's frequency data. Returns None if there are
    fewer than _MIN_ALPHA_TOKENS alphabetic tokens to judge from (too
    short/noisy to score reliably) rather than guessing.
    """
    tokens = [t for t in tokenize(text) if not t.isdigit()]
    if len(tokens) < _MIN_ALPHA_TOKENS:
        return None

    known = sum(1 for t in tokens if zipf_frequency(t.lower(), language) >= _ZIPF_KNOWN_THRESHOLD)
    return known / len(tokens)


def passes_language_filter(
    confidence: float | None,
    lang_score: float | None,
    confidence_min: float,
    lang_score_min: float,
) -> bool:
    """True if the row should be kept — used by build_bow.py and build_tags_from_ocr.py."""
    if confidence is not None and confidence < confidence_min:
        return False
    if lang_score is not None and lang_score < lang_score_min:
        return False
    return True