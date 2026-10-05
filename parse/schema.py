"""Ollama response schema for the query parser (P3 spec, decision 1). Pure."""
from taxonomy.model import Taxonomy

LANGUAGES = ("en", "ru", "es", "other")
CONFIDENCE_LEVELS = {"low": 0.3, "medium": 0.6, "high": 0.9}


def build_response_schema(tax: Taxonomy) -> dict:
    """JSON schema for structured output, built from the taxonomy. maxLength is deliberately omitted on
    `situation` (constrained decoding does not reliably honour it, P2's finding); validate_profile enforces it."""
    properties = {"language": {"type": "string", "enum": list(LANGUAGES)}, "situation": {"type": "string"}}
    for axis in tax.controlled_axes():
        properties[axis] = {
            "type": "array",
            "maxItems": tax.axes[axis].max_values,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": list(tax.values[axis])},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
                },
                "required": ["key", "confidence"],
            },
        }
    return {"type": "object", "properties": properties, "required": list(properties)}
