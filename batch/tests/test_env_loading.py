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

_EXPECTED = {
    "metal": {
        "TAGGING_PROFILE": "metal",
        "RULES_FILE": "data/rules.json",
        "CLUSTER_SELECTION_METHOD": "eom",
    },
    "general": {
        "TAGGING_PROFILE": "general",
        "RULES_FILE": "data/rules.general.json",
        "CLUSTER_SELECTION_METHOD": "leaf",
    },
    "it": {
        "TAGGING_PROFILE": "it",
        "RULES_FILE": None,
        "CLUSTER_SELECTION_METHOD": "eom",
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
    return {
        "TAGGING_PROFILE": settings.TAGGING_PROFILE,
        "RULES_FILE": settings.get("RULES_FILE"),
        "CLUSTER_SELECTION_METHOD": settings.CLUSTER_SELECTION_METHOD,
        "DATABASE_URL": settings.DATABASE_URL,
    }


@pytest.mark.parametrize("name", ["metal", "general", "it"])
def test_dummy_batch_entrypoint_resolves_tracked_settings_per_environment(tmp_path, name):
    _write_fixture_env(tmp_path, name)

    result = _dummy_batch_entrypoint(name, tmp_path)

    expected = _EXPECTED[name]
    assert result["TAGGING_PROFILE"] == expected["TAGGING_PROFILE"]
    assert result["RULES_FILE"] == expected["RULES_FILE"]
    assert result["CLUSTER_SELECTION_METHOD"] == expected["CLUSTER_SELECTION_METHOD"]


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
