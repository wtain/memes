import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymorphy3

from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository
from repository.ollama_descriptions import OllamaDescriptionsRepository

TEXT_SOURCE_OCR = "ocr"
TEXT_SOURCE_DESCRIPTIONS = "descriptions"


def _lemmatize_word(morph, word):
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()


def _tokenize(text):
    return re.findall(r'\w+', text)


def _apply_min_frequency(counter_or_dict, min_frequency, metrics):
    result = {}
    for lemma, count in counter_or_dict.items():
        if count >= min_frequency:
            result[lemma] = count
        else:
            metrics.increment("lemmas.filtered_out")
    return result


def _filter_lemmas(output, text_source, exclude_set):
    if text_source == TEXT_SOURCE_OCR:
        return {
            lang: {lemma: count for lemma, count in lang_output.items() if lemma not in exclude_set}
            for lang, lang_output in output.items()
        }
    return {lemma: count for lemma, count in output.items() if lemma not in exclude_set}


def _count_lemmas(output, text_source):
    if text_source == TEXT_SOURCE_OCR:
        return sum(len(v) for v in output.values())
    return len(output)


def _load_ignore_lemmas(morph, path):
    with open(path, encoding="utf-8") as f:
        words = json.load(f)
    return {_lemmatize_word(morph, w) for w in words}


def _build_rules_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_thresholds", None)
    covered = set()
    for rule_key in data:
        for word in _tokenize(rule_key):
            covered.add(_lemmatize_word(morph, word))
    return covered


def _write_output(path, data):
    os.makedirs(Path(path).parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def main():
    text_source = os.getenv("TEXT_SOURCE", TEXT_SOURCE_OCR)
    ocr_confidence_min = float(os.getenv("OCR_CONFIDENCE_MIN", "0.4"))
    min_word_length = int(os.getenv("BOW_MIN_WORD_LENGTH", "3"))
    min_frequency = int(os.getenv("BOW_MIN_FREQUENCY", "2"))
    output_file = os.getenv("BOW_OUTPUT_FILE")
    ignore_file = os.getenv("BOW_IGNORE_FILE")
    rules_file = os.getenv("RULES_FILE")
    unmatched_file = os.getenv("BOW_UNMATCHED_FILE")

    print(f"TEXT_SOURCE={text_source}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}, BOW_MIN_FREQUENCY={min_frequency}")
    if text_source == TEXT_SOURCE_OCR:
        print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}")
    print(f"BOW_OUTPUT_FILE={output_file}")

    morph = pymorphy3.MorphAnalyzer()
    metrics = SimpleMetricsListener()

    ignore_lemmas = set()
    if ignore_file:
        ignore_lemmas = _load_ignore_lemmas(morph, ignore_file)
        print(f"BOW_IGNORE_FILE={ignore_file} ({len(ignore_lemmas)} ignore lemmas loaded)")

    rules_lemmas = set()
    if rules_file:
        rules_lemmas = _build_rules_lemma_set(morph, rules_file)
        print(f"RULES_FILE={rules_file} ({len(rules_lemmas)} lemmas covered by rules)")
        if not unmatched_file:
            raise ValueError("BOW_UNMATCHED_FILE must be set when RULES_FILE is provided")
        print(f"BOW_UNMATCHED_FILE={unmatched_file}")

    async with AsyncSessionLocal() as session:
        if text_source == TEXT_SOURCE_OCR:
            output = await _build_ocr_bow(session, morph, ocr_confidence_min, min_word_length, min_frequency, metrics)
        elif text_source == TEXT_SOURCE_DESCRIPTIONS:
            output = await _build_descriptions_bow(session, morph, min_word_length, min_frequency, metrics)
        else:
            raise ValueError(f"Unknown TEXT_SOURCE: {text_source!r}. Expected 'ocr' or 'descriptions'.")

    if ignore_lemmas:
        before = _count_lemmas(output, text_source)
        output = _filter_lemmas(output, text_source, ignore_lemmas)
        print(f"Ignore filter: removed {before - _count_lemmas(output, text_source)} lemmas")

    _write_output(output_file, output)
    print(f"Written to {output_file}")

    if rules_lemmas:
        unmatched = _filter_lemmas(output, text_source, rules_lemmas)
        total = _count_lemmas(output, text_source)
        remaining = _count_lemmas(unmatched, text_source)
        print(f"Rules coverage: {total - remaining}/{total} lemmas matched, {remaining} unmatched")
        _write_output(unmatched_file, unmatched)
        print(f"Unmatched written to {unmatched_file}")

    metrics.print()


async def _build_ocr_bow(session, morph, confidence_min, min_word_length, min_frequency, metrics):
    repo = OCRTextRepository(session)
    rows = await repo.get_all_texts_with_language()

    lang_counters = defaultdict(Counter)

    for text, confidence, language in rows:
        metrics.increment("ocr.rows.total")
        if confidence is not None and confidence < confidence_min:
            metrics.increment("ocr.rows.skipped.low_confidence")
            continue
        lang = language or "unknown"
        for word in _tokenize(text):
            if len(word) < min_word_length:
                continue
            lang_counters[lang][_lemmatize_word(morph, word)] += 1
        metrics.increment("ocr.rows.processed")

    output = {}
    for lang, counter in sorted(lang_counters.items()):
        filtered = _apply_min_frequency(counter, min_frequency, metrics)
        output[lang] = dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))
        print(f"Unique lemmas ({lang}): {len(output[lang])}")

    return output


async def _build_descriptions_bow(session, morph, min_word_length, min_frequency, metrics):
    repo = OllamaDescriptionsRepository(session)
    texts = await repo.get_all_texts()

    counter = Counter()

    for text in texts:
        metrics.increment("descriptions.rows.total")
        for word in _tokenize(text):
            if len(word) < min_word_length:
                continue
            counter[_lemmatize_word(morph, word)] += 1
        metrics.increment("descriptions.rows.processed")

    filtered = _apply_min_frequency(counter, min_frequency, metrics)
    output = dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))
    print(f"Unique lemmas: {len(output)}")
    return output


if __name__ == "__main__":
    asyncio.run(main())