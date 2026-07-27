# Smart Search

Status: done — superseded by the formal design this draft led to.
Follow-ups: docs/superpowers/specs/2026-07-21-smart-search-design.md

## Summary

Currently, text search is working in a naive way, simply comparing strings. A meme usually contains multiple strings, which form text, but different lines are recognised as independent captions by OCR engine.
For russian language different cases break naive string comparison.

For example, searching for "полиция" won't find "звоню в полицию", or "звоню в" won't find meme which has two texts one below another "звоню" and "в полицию".

## Goals (To be defined)

## Options/ideas

- Compare different text entries from image separately (Current approach)
- Join all string (take advantage of OCR coordinates) and compare (could join strings wrong, e.g. unrelated strings, or miss proper separator character); store joined string as well (separate table for derived strings?)
- Embed string and search query (requires running LLM on the backend, could be too slow without GPU or too expensive running cloud GPU on production) - should be fast enough - check CPU performance
- Bag of words (erratives make it harder; OCR could make mistakes itself)
- Lemmatisation, as in other parts of the system
- N-grams - store in a separate table? (like the all strings joined?)
- Use TF-IDF/BM25?
- Fuzzy search - levenshtein/trigram-similarity - native postgres support?
- Separate LLM to tackle erratives - offline phase, index build (превед → привет; кросавчег → красавчик)
- Use OCR confidence score
- Disable online search? Or increase delay - heavier search would take more time
- Caching (in future) - input embeddings (if used)

## Experiments (Add more if needed)

- Additional backend processing per query (per input character) - use CPU only - test performance