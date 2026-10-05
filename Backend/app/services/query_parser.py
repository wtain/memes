"""Process-wide query parser for the backend (P3 spec, decisions 5 and 7). Built lazily from the tracked settings
and registered with the `parse_query` tool. Returns None when `parse.model` is not configured (the `it`
environment), in which case the tool answers with the degraded result."""
from functools import lru_cache

from config.settings import settings
from parse import tool
from parse.config import load_config
from parse.parser import QueryParser


@lru_cache(maxsize=1)
def get_query_parser() -> QueryParser | None:
    config = load_config(settings.get)
    if config is None:
        return None
    parser = QueryParser(config)
    tool.set_parser(parser)
    return parser
