def build_pipeline(parse_model):
    """Same as evaluation.__main__._build_pipeline, but patient: a long parse/embed timeout and no breaker, so a busy GPU
    cannot degrade the run (this changes latency only, never the ranking quality being measured)."""
    from config.settings import settings
    from parse.config import load_config as load_parse_config
    from parse.parser import QueryParser
    from retrieval.config import load_config
    from retrieval.embed import OllamaEmbedClient
    from retrieval.pipeline import MemeFromTextPipeline
    from retrieval.similarity import load_similarity
    from Storage.models import PROFILE_EMBEDDING_DIM

    overrides = {"parse.timeout_seconds": 120, "parse.retry_min_seconds": 5, "parser_breaker.failures": 100000,
                 "search.meme_from_text.parser_breaker.failures": 100000}

    def get(key, default=None):
        if key == "parse.model":
            return parse_model
        if key in overrides:
            return overrides[key]
        return settings.get(key, default)

    parser = QueryParser(load_parse_config(get))
    config = load_config(get)
    from dataclasses import replace
    config = replace(config, breaker_failures=100000, embed_timeout_seconds=120.0)
    return MemeFromTextPipeline(config, parser, OllamaEmbedClient(config.ollama_url, PROFILE_EMBEDDING_DIM),
                                load_similarity(), max_input_chars=int(settings.get("parse.max_input_chars")))
