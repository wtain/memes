import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from rules.normalize import lemmatize_word, make_morph, tokenize
from rules.lang_plausibility import passes_language_filter
from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository
from repository.image_descriptions import ImageDescriptionsRepository

TEXT_SOURCE_OCR = "ocr"
TEXT_SOURCE_DESCRIPTIONS = "descriptions"


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
    return {lemmatize_word(w, morph) for w in words}


def _build_vocab_lemma_set(morph, path):
    """Load covered lemmas from a rules JSON or concepts YAML file."""
    if Path(path).suffix in (".yaml", ".yml"):
        return _build_concepts_lemma_set(morph, path)
    return _build_json_rules_lemma_set(morph, path)


def _build_json_rules_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_thresholds", None)
    covered = set()
    for rule_key in data:
        for word in tokenize(rule_key):
            covered.add(lemmatize_word(word, morph))
    return covered


def _build_concepts_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    covered = set()
    for _concept_name, cfg in (data or {}).items():
        for word in (cfg.get("words") or []):
            for token in tokenize(word):
                covered.add(lemmatize_word(token, morph))
        for fe in (cfg.get("fuzzy") or []):
            covered.add(lemmatize_word(fe["word"], morph))
    return covered


def _write_output(path, data):
    os.makedirs(Path(path).parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def main():
    text_source = settings.BOW.TEXT_SOURCE
    ocr_confidence_min = settings.OCR.CONFIDENCE_MIN
    ocr_lang_score_min = settings.OCR.LANG_SCORE_MIN
    min_word_length = settings.BOW.MIN_WORD_LENGTH
    min_frequency = settings.BOW.MIN_FREQUENCY
    output_file = settings.get("BOW.OUTPUT_FILE")
    ignore_file = settings.get("BOW.IGNORE_FILE")
    rules_file = settings.get("RULES.FILE")
    unmatched_file = settings.get("BOW.UNMATCHED_FILE")

    print(f"TEXT_SOURCE={text_source}")
    print(f"BOW_MIN_WORD_LENGTH={min_word_length}, BOW_MIN_FREQUENCY={min_frequency}")
    if text_source == TEXT_SOURCE_OCR:
        print(f"OCR_CONFIDENCE_MIN={ocr_confidence_min}")
        print(f"OCR_LANG_SCORE_MIN={ocr_lang_score_min}")
    print(f"BOW_OUTPUT_FILE={output_file}")

    morph = make_morph()
    metrics = SimpleMetricsListener()

    ignore_lemmas = set()
    if ignore_file:
        ignore_lemmas = _load_ignore_lemmas(morph, ignore_file)
        print(f"BOW_IGNORE_FILE={ignore_file} ({len(ignore_lemmas)} ignore lemmas loaded)")

    rules_lemmas = set()
    if rules_file:
        rules_lemmas = _build_vocab_lemma_set(morph, rules_file)
        print(f"RULES_FILE={rules_file} ({len(rules_lemmas)} lemmas covered by rules)")
        if not unmatched_file:
            raise ValueError("BOW_UNMATCHED_FILE must be set when RULES_FILE is provided")
        print(f"BOW_UNMATCHED_FILE={unmatched_file}")

    async with AsyncSessionLocal() as session:
        if text_source == TEXT_SOURCE_OCR:
            output = await _build_ocr_bow(
                session, morph, ocr_confidence_min, ocr_lang_score_min, min_word_length, min_frequency, metrics
            )
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


async def _build_ocr_bow(session, morph, confidence_min, lang_score_min, min_word_length, min_frequency, metrics):
    repo = OCRTextRepository(session)
    rows = await repo.get_all_texts_with_language()

    lang_counters = defaultdict(Counter)

    for text, confidence, language, lang_score in rows:
        metrics.increment("ocr.rows.total")
        if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
            if confidence is not None and confidence < confidence_min:
                metrics.increment("ocr.rows.skipped.low_confidence")
            else:
                metrics.increment("ocr.rows.skipped.low_lang_score")
            continue
        lang = language or "unknown"
        for word in tokenize(text):
            if len(word) < min_word_length or word.isdigit():
                continue
            lang_counters[lang][lemmatize_word(word, morph, lang)] += 1
        metrics.increment("ocr.rows.processed")

    output = {}
    for lang, counter in sorted(lang_counters.items()):
        filtered = _apply_min_frequency(counter, min_frequency, metrics)
        output[lang] = dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))
        print(f"Unique lemmas ({lang}): {len(output[lang])}")

    return output


async def _build_descriptions_bow(session, morph, min_word_length, min_frequency, metrics):
    repo = ImageDescriptionsRepository(session)
    texts = await repo.get_all_texts()

    counter = Counter()

    for text in texts:
        metrics.increment("descriptions.rows.total")
        for word in tokenize(text):
            if len(word) < min_word_length or word.isdigit():
                continue
            counter[lemmatize_word(word, morph)] += 1
        metrics.increment("descriptions.rows.processed")

    filtered = _apply_min_frequency(counter, min_frequency, metrics)
    output = dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))
    print(f"Unique lemmas: {len(output)}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())