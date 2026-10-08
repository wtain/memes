"""The vocabulary payload for the web UI (P5): pure conversion of the loaded taxonomy."""
from functools import lru_cache

from taxonomy import load
from taxonomy.model import FREE_TEXT, LANGUAGES, Taxonomy


def taxonomy_payload(tax: Taxonomy) -> dict:
    axes = []
    for name, spec in tax.axes.items():
        axis: dict = {"name": name, "type": spec.type}
        if spec.max_values is not None:
            axis["maxValues"] = spec.max_values
        if spec.max_chars is not None:
            axis["maxChars"] = spec.max_chars
        if spec.type != FREE_TEXT:
            axis["values"] = [
                {"key": value.key, "definition": value.definition,
                 "labels": {lang: value.labels[lang] for lang in LANGUAGES}}
                for value in tax.values[name].values()
            ]
        axes.append(axis)
    return {"version": tax.version, "axes": axes}


@lru_cache(maxsize=1)
def cached_taxonomy_payload() -> dict:
    return taxonomy_payload(load())
