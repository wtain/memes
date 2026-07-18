# Smart Search

Status: Draft

## Summary

Currently, text search is working in a naive way, simply comparing strings. A meme usually contains multiple strings, which form text, but different lines are recognised as independent captions by OCR engine.
For russian language different cases break naive string comparison.

For example, searching for "полиция" won't find "звоню в полицию", or "звоню в" won't find meme which has two texts one below another "звоню" and "в полицию".

## Options

- Compare different text entries from image separately (Current approach)
- Join all string and compare (could join strings wrong, e.g. unrelated strings, or miss proper separator character)
- Embed string and search query (requires running LLM on the backend, could be too slow without GPU or too expensive running cloud GPU on production)
- Bag of words (erratives make it harder; OCR could make mistakes itself)
- Lemmatisation, as in other parts of the system