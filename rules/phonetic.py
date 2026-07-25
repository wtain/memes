"""
Russian phonetic normalization, for erratives (deliberate internet-slang
misspellings like "превед", "аффтар") that don't share a spelling with
their canonical form but do sound alike.

Ported from the Russian Metaphone algorithm implemented by the `fonetika`
PyPI package (github.com/roddar92/russian_soundex, MIT), reimplemented
directly rather than taken as a dependency: fonetika's soundex module
unconditionally imports the unmaintained `pymorphy2` fork at import time,
which this project does not otherwise depend on (it uses pymorphy3).
Verified byte-for-byte against fonetika's reference output across a test
vocabulary of erratives, false-positive pairs, and general Russian words --
see docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md.

This module intentionally has no dependency on pymorphy3, settings, or the
DB layer -- callers (repository/ocr_lemmas.py) are responsible for gating
when a phonetic code is actually meaningful to compute or compare.
"""
import re

_CYRILLIC_WORD_RE = re.compile(r'^[а-яё]+$')

_CONSONANTS = 'бвгджзклмнпрстфхцчшщ'
_DEAF_VOWELS = 'аоыиэу'
_J_SEQ = r'^|ъ|ь'

_CONSONANT_VOWEL_MAP = [
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(я)'), r'\1а'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(ю)'), r'\1у'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(е)'), r'\1э'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(ё)'), r'\1о'),
]
_J_MAP = [
    (re.compile(r'(' + _J_SEQ + r')(я)'), 'jа'),
    (re.compile(r'(' + _J_SEQ + r')(ю)'), 'jу'),
    (re.compile(r'(' + _J_SEQ + r')(е)'), 'jэ'),
    (re.compile(r'(' + _J_SEQ + r')(ё)'), 'jо'),
]
_VOWEL_J_MAP = [
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(я)'), r'\1jа'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(ю)'), r'\1jу'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(е)'), r'\1jэ'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(ё)'), r'\1jо'),
]
_REMOVE_SIGNS = [
    (re.compile(r'й'), 'j'),
    (re.compile(r'[ъь]'), ''),
]
_II_ENDING = re.compile(r'и[еио]')
_REDUCE_REPEATED = re.compile(r'(\w)(\1)+')

_VOWEL_BUCKET = str.maketrans('аяоыиеёэюу', 'ААААИИИИУУ')
_DEVOICE = str.maketrans('бздвг', 'пстфк')
_VOICED_CONSONANTS = set('бздвг')
_SONORANTS_AND_VOWELS = set('лмнр' + 'аяоыиеёэюу')


def is_cyrillic_word(word: str) -> bool:
    """True if word consists entirely of lowercase Russian Cyrillic
    letters -- the only input russian_metaphone() is meaningful for."""
    return bool(_CYRILLIC_WORD_RE.match(word))


def _apply_rules(word, rules):
    for pattern, replacement in rules:
        word = pattern.sub(replacement, word)
    return word


def _devoice_terminal_consonants(word):
    result = []
    for i, letter in enumerate(word):
        if letter in _VOICED_CONSONANTS and (
            i == len(word) - 1 or word[i + 1].lower() not in _SONORANTS_AND_VOWELS
        ):
            letter = letter.translate(_DEVOICE)
        result.append(letter)
    return ''.join(result)


def russian_metaphone(word: str) -> str:
    """
    Reduces a Russian word to a phonetic code: words that sound alike
    (including erratives and their canonical spelling) reduce to the same
    code. Also collapses some genuinely distinct dictionary words (e.g.
    "кот"/"код") -- callers must gate on pymorphy3's is_known flag to avoid
    over-matching real vocabulary; see the design doc.
    """
    word = word.lower()
    word = _apply_rules(word, _CONSONANT_VOWEL_MAP)
    word = _apply_rules(word, _J_MAP + _VOWEL_J_MAP)
    word = _apply_rules(word, _REMOVE_SIGNS)
    word = _II_ENDING.sub('и', word)
    word = _REDUCE_REPEATED.sub(r'\1', word)
    word = word.translate(_VOWEL_BUCKET)
    word = _devoice_terminal_consonants(word)
    return word.upper()
