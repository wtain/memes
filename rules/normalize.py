import re

import pymorphy3


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer) -> str:
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()


def tokenize(text: str) -> list[str]:
    return re.findall(r'\w+', text)


def normalize(text: str, morph: pymorphy3.MorphAnalyzer, min_length: int = 3) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set."""
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length or word.isdigit():
            continue
        result.add(lemmatize_word(word, morph))
    return result