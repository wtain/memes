from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Mapping

from taxonomy.loader import load
from taxonomy.model import FREE_TEXT, AxisSpec, Taxonomy

SOURCES = ("model", "human")


@dataclass
class ValidationResult:
    profile: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_unit_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def _validate_free_text(name: str, spec: AxisSpec, profile: Mapping, out: dict, errors: list[str]) -> None:
    text = profile.get(name)
    if text is not None and not isinstance(text, str):
        errors.append(f"{name}: must be a string or null")
        text = None
    text = text.strip() if text else None
    if text and len(text) > spec.max_chars:
        errors.append(f"{name}: longer than {spec.max_chars} characters")
    out[name] = text or None

    confidence = profile.get(f"{name}Confidence")
    if confidence is not None and not _is_unit_number(confidence):
        errors.append(f"{name}Confidence: must be a number in 0..1")
        confidence = None
    out[f"{name}Confidence"] = float(confidence) if confidence is not None else None


def _validate_values(axis: str, spec: AxisSpec, items, tax: Taxonomy, errors: list[str]) -> list[dict]:
    if not isinstance(items, list):
        errors.append(f"{axis}: must be a list")
        return []
    best: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, Mapping):
            errors.append(f"{axis}: each value must be an object")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not tax.has_key(axis, key):
            errors.append(f"{axis}: unknown key {key!r}")
            continue
        confidence = item.get("confidence", 1.0)
        if not _is_unit_number(confidence):
            errors.append(f"{axis}: confidence for {key!r} must be a number in 0..1")
            continue
        source = item.get("source", "model")
        if source not in SOURCES:
            errors.append(f"{axis}: source for {key!r} must be one of {SOURCES}")
            continue
        entry = {"key": key, "confidence": float(confidence), "source": source}
        if key not in best or entry["confidence"] > best[key]["confidence"]:
            best[key] = entry
    values = sorted(best.values(), key=lambda v: (-v["confidence"], v["key"]))
    if spec.max_values is not None and len(values) > spec.max_values:
        errors.append(f"{axis}: {len(values)} values exceed the limit of {spec.max_values}")
    return values


def validate_profile(profile: Mapping, tax: Taxonomy | None = None) -> ValidationResult:
    """Validate and normalise a meme or query profile against the taxonomy.

    Only taxonomy-related fields are checked; other keys are ignored (the JSON schema covers them).
    """
    tax = tax or load()
    errors: list[str] = []
    out: dict[str, Any] = {}
    for name, spec in tax.axes.items():
        if spec.type == FREE_TEXT:
            _validate_free_text(name, spec, profile, out, errors)
        else:
            out[name] = _validate_values(name, spec, profile.get(name) or [], tax, errors)
    return ValidationResult(out, errors)
