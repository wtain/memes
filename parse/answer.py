"""Turns the model's raw answer into a validated query profile (P3 spec, decision 2). Pure."""
import json

from parse.schema import CONFIDENCE_LEVELS, LANGUAGES
from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile


class UnusableAnswer(ValueError):
    """The answer cannot be used (the message says why; it never contains the user's text)."""


def interpret_answer(raw, tax: Taxonomy, parser_version: str) -> dict:
    try:
        answer = json.loads(raw)
    except (TypeError, ValueError):
        raise UnusableAnswer("the answer is not valid JSON") from None
    if not isinstance(answer, dict):
        raise UnusableAnswer("the answer must be a JSON object")
    language = answer.get("language")
    if language not in LANGUAGES:
        raise UnusableAnswer(f"language must be one of {', '.join(LANGUAGES)}")

    candidate = {"situation": answer.get("situation")}
    for axis in tax.controlled_axes():
        items = answer.get(axis, [])
        if not isinstance(items, list):
            raise UnusableAnswer(f"{axis}: must be a list")
        converted = []
        for item in items:
            if not isinstance(item, dict):
                raise UnusableAnswer(f"{axis}: each value must be an object")
            level = item.get("confidence")
            if not (isinstance(level, str) and level in CONFIDENCE_LEVELS):
                raise UnusableAnswer(f"{axis}: confidence must be one of {', '.join(CONFIDENCE_LEVELS)}")
            converted.append({"key": item.get("key"), "confidence": CONFIDENCE_LEVELS[level], "source": "model"})
        candidate[axis] = converted

    result = validate_profile(candidate, tax)
    if result.errors:
        raise UnusableAnswer("; ".join(result.errors))
    profile = result.profile
    if not profile.get("situation") and not any(profile[axis] for axis in tax.controlled_axes()):
        raise UnusableAnswer("the answer has no situation and no values")
    return {
        "taxonomyVersion": tax.version,
        "language": language,
        "parserVersion": parser_version,
        "degraded": False,
        "situation": profile["situation"],
        "situationConfidence": None,
        **{axis: profile[axis] for axis in tax.controlled_axes()},
    }
