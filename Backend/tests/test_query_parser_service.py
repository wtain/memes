from parse import tool
from parse.config import ParseConfig
from parse.parser import QueryParser

import Backend.app.services.query_parser as service

CONFIG = ParseConfig(model="m", timeout_seconds=5.0, retry_min_seconds=1.5, max_concurrent=2, num_ctx=4096,
                     seed=42, keep_alive="30m", cache_size=10, max_input_chars=600, ollama_url="http://x")


def setup_function():
    service.get_query_parser.cache_clear()
    tool.set_parser(None)


def teardown_function():
    service.get_query_parser.cache_clear()
    tool.set_parser(None)


def test_returns_none_and_registers_nothing_when_parse_model_is_not_configured(monkeypatch):
    monkeypatch.setattr(service, "load_config", lambda get: None)

    assert service.get_query_parser() is None
    assert tool.get_parser() is None


def test_builds_one_parser_registers_it_with_the_tool_and_caches_it(monkeypatch):
    monkeypatch.setattr(service, "load_config", lambda get: CONFIG)

    parser = service.get_query_parser()

    assert isinstance(parser, QueryParser) and parser.model == "m"
    assert tool.get_parser() is parser
    assert service.get_query_parser() is parser
