"""The `parse_query` tool (FR-22, P3 spec, decision 7): a typed, side-effect-free entry point (apart from the
cache and the local LLM call) that P4 and a later agent layer call. Pure: stdlib and parse/taxonomy only."""
from taxonomy.loader import load

from parse.parser import QueryParser, degraded_result, meta_to_dict
from parse.prompt import PARSER_VERSION
from parse.schema import LANGUAGES

PARSE_QUERY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "The message to understand"},
        "languageHint": {"type": "string", "enum": list(LANGUAGES),
                         "description": "Used only when the language cannot be guessed from the script"},
    },
    "required": ["text"],
}

PARSE_QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"$ref": "queryprofile.schema.json"},
        "meta": {
            "type": "object",
            "properties": {
                "model": {"type": "string"}, "parserVersion": {"type": "string"}, "latencyMs": {"type": "integer"},
                "cacheHit": {"type": "boolean"}, "attempts": {"type": "integer"},
                "fallback": {"type": "string",
                             "enum": ["none", "retry", "unavailable", "timeout", "invalid_output", "empty_input"]},
            },
            "required": ["model", "parserVersion", "latencyMs", "cacheHit", "attempts", "fallback"],
        },
    },
    "required": ["profile", "meta"],
}

_parser: QueryParser | None = None


def set_parser(parser: QueryParser | None) -> None:
    global _parser
    _parser = parser


def get_parser() -> QueryParser | None:
    return _parser


async def parse_query(arguments) -> dict:
    """Raises ValueError only for a malformed call; parse failures come back as a degraded profile."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("text is required and must be a string")
    hint = arguments.get("languageHint")
    if hint is not None and hint not in LANGUAGES:
        raise ValueError(f"languageHint must be one of {', '.join(LANGUAGES)}")
    parser = _parser
    if parser is None:
        result = degraded_result(text, hint, load(), "unavailable", model="", parser_version=PARSER_VERSION)
    else:
        result = await parser.parse(text, hint)
    return {"profile": result.profile, "meta": meta_to_dict(result.meta)}
