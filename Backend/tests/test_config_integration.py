"""
Integration tests for config/settings.py — proves environment selection,
the secrets overlay, and fast-fail validation all work end-to-end, without
touching the real, gitignored environments/.env.* files.
"""
import pytest
from dynaconf.validator import ValidationError

from config.settings import load_env, settings

_EXPECTED = {
    "metal": {
        "TAGGING_PROFILE": "metal",
        "RULES_FILE": "data/rules.json",
        "FRONTEND_ORIGIN": "http://localhost:5173",
    },
    "general": {
        "TAGGING_PROFILE": "general",
        "RULES_FILE": "data/rules.general.json",
        "FRONTEND_ORIGIN": "http://localhost:5174",
    },
    "it": {
        "TAGGING_PROFILE": "it",
        "RULES_FILE": None,
        "FRONTEND_ORIGIN": "http://localhost:5175",
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
    assert settings.TAGGING_PROFILE == expected["TAGGING_PROFILE"]
    assert settings.get("RULES_FILE") == expected["RULES_FILE"]
    assert settings.FRONTEND_ORIGIN == expected["FRONTEND_ORIGIN"]


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
