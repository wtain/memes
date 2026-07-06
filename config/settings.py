import os
from pathlib import Path

from dotenv import load_dotenv
from dynaconf import Dynaconf, Validator

_BASE_SETTINGS_FILE = "environments/settings.yaml"


def _build(name: str) -> Dynaconf:
    """Build a Dynaconf instance for `name`, without validating DATABASE_URL.

    Validation is deliberately not eager here: at module import time (see
    the module-level `settings` below), batch scripts haven't called
    load_env() yet, so DATABASE_URL isn't set — eagerly validating would
    raise before main() ever gets a chance to load it. load_env() validates
    explicitly once secrets are actually loaded. Backend gets its own
    fail-fast independently, via Storage/config.py's RuntimeError guard.

    merge_enabled=True is required by the nested tracked-YAML structure: a
    group like `bow:` is split across settings.yaml (common defaults) and
    settings.<name>.yaml (per-environment overrides). Without merge_enabled,
    Dynaconf's multi-file merge is shallow, so the per-environment file's
    `bow:` dict would replace the common file's `bow:` dict outright instead
    of merging key-by-key, silently dropping the common defaults. This is
    unrelated to the environments=True leak documented below — merge_enabled
    only deep-merges nested dict values within the two files already scoped
    to the active environment.
    """
    instance = Dynaconf(
        envvar_prefix=False,
        merge_enabled=True,
        settings_files=[_BASE_SETTINGS_FILE, f"environments/settings.{name}.yaml"],
    )
    instance.validators.register(Validator("DATABASE_URL", must_exist=True))
    return instance


class _SettingsProxy:
    """Delegates attribute/item access to the active Dynaconf instance.

    Dynaconf's reload()/configure() merge new data onto existing state
    rather than replacing it, so switching environments mid-process (e.g.
    metal -> it) left stale keys from the previous environment. Building a
    fresh Dynaconf instance per environment and swapping it in here avoids
    that entirely — every attribute access goes through the current
    instance, so callers holding `settings` (imported once, at module load
    time) transparently see the new environment's values after load_env().
    """

    def __init__(self, instance: Dynaconf):
        self._instance = instance

    def __getattr__(self, name):
        return getattr(self._instance, name)

    def __getitem__(self, key):
        return self._instance[key]


settings = _SettingsProxy(_build(os.environ.get("APP_ENV", "general")))


def load_env(name: str | None = None, base_dir: Path = Path("environments")) -> None:
    """Load <base_dir>/.env.<name> into the process, then rebuild settings.

    name defaults to the already-set APP_ENV. Raises if neither is available —
    silently falling back to whatever happens to already be in os.environ is
    the exact wrong-environment failure mode this replaces. base_dir is
    overridable so tests can point at a fixture directory instead of the
    real, gitignored environments/.env.* files.

    Each environment's tracked config lives in its own physical
    environments/settings.<name>.yaml file (layered on top of the common
    environments/settings.yaml), rather than Dynaconf's built-in
    environments=True/[env]-section merging — that mode merges every
    environment section found across all loaded files regardless of which
    one is active, which leaked general's settings into it whenever
    settings.general.yaml was also on disk. Loading only the two files that
    apply to the active environment avoids that.
    """
    name = name or os.environ.get("APP_ENV")
    if not name:
        raise RuntimeError(
            "No environment selected — pass --env {metal,general,it} or set APP_ENV"
        )
    load_dotenv(base_dir / f".env.{name}", override=True)
    instance = _build(name)
    instance.validators.validate()
    settings._instance = instance
