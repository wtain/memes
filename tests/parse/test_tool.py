import json
from pathlib import Path

import pytest

from parse import tool
from parse.parser import QueryParser
from taxonomy import load
from tests.parse.test_parser import BASE, VALID, FakeClient

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "shared" / "schemas" / "queryprofile.schema.json"
TAX = load()


@pytest.fixture(autouse=True)
def _reset_parser():
    tool.set_parser(None)
    yield
    tool.set_parser(None)


def _assert_matches_queryprofile(profile):
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert set(schema["required"]) <= set(profile)
    assert profile["language"] in schema["properties"]["language"]["enum"]
    for axis in ("emotion", "stance", "topic", "tone"):
        assert isinstance(profile[axis], list)


@pytest.mark.asyncio
async def test_parse_query_returns_profile_and_camel_case_meta():
    tool.set_parser(QueryParser(BASE, client=FakeClient(VALID), tax=TAX))

    output = await tool.parse_query({"text": "My friend got promoted!", "languageHint": "en"})

    assert set(output) == {"profile", "meta"}
    _assert_matches_queryprofile(output["profile"])
    assert set(output["meta"]) == {"model", "parserVersion", "latencyMs", "cacheHit", "attempts", "fallback"}
    assert output["meta"]["fallback"] == "none" and output["profile"]["degraded"] is False


@pytest.mark.asyncio
async def test_parse_query_without_a_configured_parser_degrades():
    output = await tool.parse_query({"text": "Привет, как дела?"})

    _assert_matches_queryprofile(output["profile"])
    assert output["profile"]["degraded"] is True and output["profile"]["language"] == "ru"
    assert output["meta"]["fallback"] == "unavailable" and output["meta"]["model"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [None, "text", {}, {"text": 5}, {"text": "hi", "languageHint": "fr"}])
async def test_a_malformed_call_raises_value_error(arguments):
    with pytest.raises(ValueError):
        await tool.parse_query(arguments)


def test_the_schema_dictionaries_describe_the_call():
    assert tool.PARSE_QUERY_INPUT_SCHEMA["required"] == ["text"]
    assert tool.PARSE_QUERY_INPUT_SCHEMA["properties"]["languageHint"]["enum"] == ["en", "ru", "es", "other"]
    assert tool.PARSE_QUERY_OUTPUT_SCHEMA["required"] == ["profile", "meta"]
    assert set(tool.PARSE_QUERY_OUTPUT_SCHEMA["properties"]["meta"]["required"]) == {
        "model", "parserVersion", "latencyMs", "cacheHit", "attempts", "fallback"}


def test_get_parser_returns_what_was_set():
    assert tool.get_parser() is None
    parser = QueryParser(BASE, client=FakeClient(), tax=TAX)
    tool.set_parser(parser)
    assert tool.get_parser() is parser
