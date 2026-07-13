"""
Integration tests for config/settings.py — proves environment selection,
the secrets overlay, and fast-fail validation all work end-to-end, without
touching the real, gitignored environments/.env.* files.
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
    "IMAGE_DESCRIPTIONS.MODEL": "llava",
    "IMAGE_DESCRIPTIONS.PROMPTS_FILE": None,
}

# Every tracked key this migration covers, resolved per environment. Built by
# layering each environment's committed settings.<name>.yaml overrides on top
# of _COMMON (mirroring exactly what config/settings.py itself does) — this
# is the nested-structure baseline proving the hierarchical-YAML restructuring
# resolves identically to the pre-restructure flat baseline (see
# docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md).
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
        "LEMMA_CLUSTERING.OUTPUT_FILE": "output/lemma_clusters.general.ru.yaml",
        "GENERAL.FRONTEND_ORIGIN": "http://localhost:5174",
        "IMAGE_DESCRIPTIONS.MODEL": "qwen2.5vl:7b",
        "IMAGE_DESCRIPTIONS.PROMPTS_FILE": "data/image-description-prompts.general.yaml",
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


@pytest.mark.parametrize("name", ["metal", "general", "it"])
def test_load_env_resolves_tracked_settings_per_environment(tmp_path, name):
    _write_fixture_env(tmp_path, name)

    load_env(name, base_dir=tmp_path)

    expected = _EXPECTED[name]
    for key, value in expected.items():
        assert settings.get(key) == value, f"{key} for {name}"


def test_load_env_secrets_overlay_wins_over_tracked_yaml(tmp_path):
    _write_fixture_env(tmp_path, "general", database_url="postgresql+asyncpg://fixture:fixture@localhost/fixture_db")

    load_env("general", base_dir=tmp_path)

    assert settings.DATABASE_URL == "postgresql+asyncpg://fixture:fixture@localhost/fixture_db"


def test_load_env_raises_if_database_url_missing(tmp_path, monkeypatch):
    # conftest.py sets DATABASE_URL via os.environ.setdefault for the whole
    # session — remove it here so this fixture .env file's omission is
    # actually visible to Dynaconf's os.environ overlay.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _write_fixture_env(tmp_path, "general", include_database_url=False)

    with pytest.raises(ValidationError):
        load_env("general", base_dir=tmp_path)


def test_load_env_raises_without_name_or_app_env(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)

    with pytest.raises(RuntimeError):
        load_env(None, base_dir=tmp_path)
