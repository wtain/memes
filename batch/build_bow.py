import asyncio
import json
import os
import re
from collections import Counter, defaultdict

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


async def main():
    text_source = os.getenv("TEXT_SOURCE", TEXT_SOURCE_OCR)
    ocr_confidence_min = float(os.getenv("OCR_CONFIDENCE_MIN", "0.4"))
    min_word_length = int(os.getenv("BOW_MIN_WORD_LENGTH", "3"))
    min_frequency = int(os.getenv("BOW_MIN_FREQUENCY", "2"))
    output_file = os.getenv("BOW_OUTPUT_FILE")

    print(f"TEXT_SOURCE={text_source}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}, BOW_MIN_FREQUENCY={min_frequency}")
    if text_source == TEXT_SOURCE_OCR:
        print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}")
    print(f"BOW_OUTPUT_FILE={output_file}")

    morph = pymorphy3.MorphAnalyzer()
    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        if text_source == TEXT_SOURCE_OCR:
            output = await _build_ocr_bow(session, morph, ocr_confidence_min, min_word_length, min_frequency, metrics)
        elif text_source == TEXT_SOURCE_DESCRIPTIONS:
            output = await _build_descriptions_bow(session, morph, min_word_length, min_frequency, metrics)
        else:
            raise ValueError(f"Unknown TEXT_SOURCE: {text_source!r}. Expected 'ocr' or 'descriptions'.")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Written to {output_file}")
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