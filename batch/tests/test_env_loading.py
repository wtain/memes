"""
Integration test for a "dummy batch" script's entrypoint shape — proves the
--env flag -> config.settings.load_env() -> settings.* pattern every real
batch script's `if __name__ == "__main__":` block uses actually resolves the
right per-environment values end-to-end, without touching the real,
gitignored environments/.env.* files.
"""
import pytest
from dynaconf.validator import ValidationError

from config.settings import load_env, settings

_COMMON = {
    "RULES.LEMMATIZE": False,
    "RULES.TAGGING_DATA_DIR": None,
    "OCR.CONFIDENCE_MIN": 0.4,
    "OCR.LANG_SCORE_MIN": 0.3,
    "LEMMA_CLUSTERING.TEXT_SCOPE": "unmatched",
    "LEMMA_CLUSTERING.LANGUAGE": "all",
    "LEMMA_CLUSTERING.MIN_CLUSTER_SIZE": 2,
    "LEMMA_CLUSTERING.SELECTION_EPSILON": 0.0,
    "LEMMA_CLUSTERING.SELECTION_METHOD": "eom",
    "LEMMA_CLUSTERING.TEXT_EMBED_MODEL": "sbert",
    "LEMMA_CLUSTERING.OUTPUT_FILE": None,
    "LEMMA_CLUSTERING.MIN_SAMPLES": None,
    "OLLAMA.MODEL": "qwen2",
    "OLLAMA.ENABLED": True,
    "CONCEPTS.LOOKUP": False,
    "CONCEPTS.THRESHOLD": 0.2,
    "CONCEPTS.LIMIT": 50,
    "CONCEPTS.TEXT_CONCEPTS_FILE": None,
    "CONCEPTS.TEXT_CONCEPTS_TEMPLATES_FILE": None,
    "CONCEPTS.IMAGES_DIR": None,
    "CONCEPTS.MAPPING_FILE": None,
    "GENERAL.BATCH_SIZE": 100,
    "GENERAL.PROGRESS_EVERY": 10,
    "GENERAL.PROFILE": "general",
    "GENERAL.FRONTEND_ORIGIN": "http://localhost:5173",
    "GENERAL.TAGGING_PROFILE": None,
    "BOW.MIN_WORD_LENGTH": 3,
    "BOW.MIN_FREQUENCY": 2,
    "BOW.TEXT_SOURCE": "ocr",
    "BOW.OUTPUT_FILE": None,
    "BOW.UNMATCHED_FILE": None,
    "BOW.IGNORE_FILE": None,
    "RULES.FILE": None,
}

# Every tracked key this migration covers, resolved per environment — mirrors
# Backend/tests/test_config_integration.py's baseline (see
# docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md).
# Kept as an independent copy rather than a shared fixture: these two suites
# exercise the same config module from two different application entry
# points, and a shared module would obscure that either one is a complete,
# self-contained regression net on its own.
_EXPECTED = {
    "metal": {
        **_COMMON,
        "GENERAL.TAGGING_PROFILE": "metal",
        "RULES.FILE": "data/rules.json",
        "CONCEPTS.TEXT_CONCEPTS_FILE": "data/text-concepts.metal.json",
        "CONCEPTS.TEXT_CONCEPTS_TEMPLATES_FILE": "data/text-concepts.templates.metal.json",
        "CONCEPTS.IMAGES_DIR": "images",
    },
    "general": {
        **_COMMON,
        "GENERAL.TAGGING_PROFILE": "general",
        "RULES.FILE": "data/rules.general.json",
        "CONCEPTS.TEXT_CONCEPTS_FILE": "data/text-concepts.general.json",
        "CONCEPTS.TEXT_CONCEPTS_TEMPLATES_FILE": "data/text-concepts.templates.general.json",
        "CONCEPTS.IMAGES_DIR": "images-general",
        "CONCEPTS.MAPPING_FILE": "data/concepts-to-tags.general.json",
        "BOW.OUTPUT_FILE": "output/bow.general.json",
        "BOW.UNMATCHED_FILE": "output/bow.unmatched.general.json",
        "BOW.IGNORE_FILE": "data/ignore-words.general.json",
        "RULES.LEMMATIZE": True,
        "LEMMA_CLUSTERING.SELECTION_METHOD": "leaf",
        "GENERAL.FRONTEND_ORIGIN": "http://localhost:5174",
    },
    "it": {
        **_COMMON,
        "GENERAL.TAGGING_PROFILE": "it",
        "GENERAL.FRONTEND_ORIGIN": "http://localhost:5175",
    },
}


def _write_fixture_env(base_dir, name: str, *, database_url="postgresql+asyncpg://test:test@localhost/test", include_database_url=True):
    lines = [f"APP_ENV={name}"]
    if include_database_url:
        lines.append(f"DATABASE_URL={database_url}")
    lines.append("BASE_PATH=/tmp/test_images")
    (base_dir / f".env.{name}").write_text("\n".join(lines) + "\n")


def _dummy_batch_entrypoint(env_name: str, base_dir) -> dict:
    """Mimics a real batch script's `if __name__ == "__main__":` shape:
    --env flag -> load_env() -> read settings.* — without any real pipeline
    business logic, argparse, or asyncio.
    """
    load_env(env_name, base_dir=base_dir)
    result = {key: settings.get(key) for key in _COMMON}
    result["DATABASE_URL"] = settings.DATABASE_URL
    return result


@pytest.mark.parametrize("name", ["metal", "general", "it"])
def test_dummy_batch_entrypoint_resolves_tracked_settings_per_environment(tmp_path, name):
    _write_fixture_env(tmp_path, name)

    result = _dummy_batch_entrypoint(name, tmp_path)

    expected = _EXPECTED[name]
    for key, value in expected.items():
        assert result[key] == value, f"{key} for {name}"


def test_dummy_batch_entrypoint_secrets_overlay_wins_over_tracked_yaml(tmp_path):
    _write_fixture_env(tmp_path, "general", database_url="postgresql+asyncpg://fixture:fixture@localhost/fixture_db")

    result = _dummy_batch_entrypoint("general", tmp_path)

    assert result["DATABASE_URL"] == "postgresql+asyncpg://fixture:fixture@localhost/fixture_db"


def test_dummy_batch_entrypoint_raises_if_database_url_missing(tmp_path, monkeypatch):
    # conftest.py sets DATABASE_URL via os.environ.setdefault for the whole
    # session — remove it here so this fixture .env file's omission is
    # actually visible to Dynaconf's os.environ overlay.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _write_fixture_env(tmp_path, "general", include_database_url=False)

    with pytest.raises(ValidationError):
        _dummy_batch_entrypoint("general", tmp_path)


def test_dummy_batch_entrypoint_raises_without_env_or_app_env(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)

    with pytest.raises(RuntimeError):
        _dummy_batch_entrypoint(None, tmp_path)
