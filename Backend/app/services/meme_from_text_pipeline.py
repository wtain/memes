"""Process-wide meme-from-text pipeline for the backend (P4 spec, decisions 2 and 7). Built lazily from the
tracked settings. None when the environment has no parser (`parse.model` unset, the `it` environment): the
endpoint then answers with the explicit unavailable response."""
from functools import lru_cache

from Backend.app.services.query_parser import get_query_parser
from config.settings import settings
from retrieval.config import load_config
from retrieval.embed import OllamaEmbedClient
from retrieval.pipeline import MemeFromTextPipeline
from retrieval.similarity import load_similarity


@lru_cache(maxsize=1)
def get_pipeline() -> MemeFromTextPipeline | None:
    parser = get_query_parser()
    if parser is None:
        return None
    config = load_config(settings.get)
    return MemeFromTextPipeline(
        config, parser, OllamaEmbedClient(config.ollama_url), load_similarity(),
        max_input_chars=int(settings.get("parse.max_input_chars")))
