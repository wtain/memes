"""Process-wide meme-from-text pipeline for the backend (P4 spec, decisions 2 and 7). Built lazily from the
tracked settings. None when the environment has no parser (`parse.model` unset, the `it` environment): the
endpoint then answers with the explicit unavailable response.

A build failure (for example an invalid `search.meme_from_text` key) is logged once with the exception type name and
remembered: `get_pipeline()` then returns None, so the endpoint answers `available: false` instead of failing every
request with a 500. A misconfiguration therefore shows as `available: false` plus one startup warning; startup itself
never fails (the always-on environments must stay up)."""
import logging
from functools import lru_cache

from Backend.app.services.query_parser import get_query_parser
from config.settings import settings
from retrieval.config import load_config
from retrieval.embed import OllamaEmbedClient
from retrieval.pipeline import MemeFromTextPipeline
from retrieval.similarity import load_similarity
from Storage.models import PROFILE_EMBEDDING_DIM

logger = logging.getLogger(__name__)

_build_error = False


@lru_cache(maxsize=1)
def get_pipeline() -> MemeFromTextPipeline | None:
    global _build_error
    if _build_error:
        return None
    try:
        parser = get_query_parser()
        if parser is None:
            return None
        config = load_config(settings.get)
        return MemeFromTextPipeline(
            config, parser, OllamaEmbedClient(config.ollama_url, PROFILE_EMBEDDING_DIM), load_similarity(),
            max_input_chars=int(settings.get("parse.max_input_chars")))
    except Exception as error:
        _build_error = True
        logger.warning("meme-from-text pipeline could not be built, search stays unavailable: %s",
                       type(error).__name__)
        return None
