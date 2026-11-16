"""Guard tests of the tracked prod overlays environments/settings.<env>.prod.yaml (P5d-T6). No database.

Each overlay is loaded the way the production container loads it (APP_STAGE=prod, APP_ENV=<env>, in a fresh interpreter because the stage
is read once at import), the existing `validate_*_settings` functions run against the merged result, and the overlay is checked for
secrets and for keys the code does not read.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ENVS = ("metal", "general", "it")
SECRET_KEY = re.compile(r"password|passwd|secret|token|api_?key|database_url|dsn|private", re.I)
SECRET_VALUE = re.compile(r"://[^/\s@]*:[^/\s@]*@|-----BEGIN|\bsk-[A-Za-z0-9]{10,}")

_PROBE = r"""
import json
from config.settings import settings
from repository.meme_impressions import validate_impression_settings
from repository.search_outcomes import validate_outcome_settings
from repository.search_ranking import validate_search_settings
validate_impression_settings()
validate_outcome_settings()
validate_search_settings()
out = {k: settings.get(k) for k in (
    "stage", "scheduler.enabled", "cors.origins", "cors.allow_credentials", "parse.model",
    "popularity.impressions.enabled", "popularity.search_outcomes.enabled", "search.meme_from_text.query_log.enabled")}
out["cors.origins"] = list(out["cors.origins"] or [])
print(json.dumps(out))
"""


def overlay_path(env):
    return ROOT / "environments" / f"settings.{env}.prod.yaml"


def load_yaml(env):
    return yaml.safe_load(overlay_path(env).read_text(encoding="utf-8"))


def run_probe(env):
    child_env = {k: v for k, v in os.environ.items() if k not in ("APP_STAGE", "APP_ENV", "DATABASE_URL", "CORS__ORIGINS")}
    child_env.update(APP_STAGE="prod", APP_ENV=env, DATABASE_URL="postgresql+asyncpg://placeholder:placeholder@localhost/none", PYTHONPATH=str(ROOT))
    done = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=child_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def walk(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, path + (str(index),))
    else:
        yield path, node


@pytest.fixture(params=ENVS)
def env(request):
    return request.param


def test_overlay_loads_with_prod_stage_and_the_validators_pass(env):
    got = run_probe(env)
    assert got["stage"] == "prod"
    assert got["scheduler.enabled"] is False
    assert got["cors.allow_credentials"] is False
    assert got["popularity.impressions.enabled"] is True
    assert got["popularity.search_outcomes.enabled"] is True
    assert got["search.meme_from_text.query_log.enabled"] is True


def test_cors_origins_are_only_this_environments_https_placeholder_host(env):
    got = run_probe(env)
    assert got["cors.origins"] == [f"https://{env}.example.invalid"]
    for origin in got["cors.origins"]:
        assert origin.startswith("https://") and "localhost" not in origin and "127.0.0.1" not in origin


def test_the_parser_is_off_where_it_could_exist(env):
    got = run_probe(env)
    assert not got["parse.model"]  # "" or absent (it has no parser at all)
    if env in ("metal", "general"):
        assert load_yaml(env)["parse"]["model"] == ""


def test_overlay_holds_no_secret(env):
    text = overlay_path(env).read_text(encoding="utf-8")
    for path, value in walk(yaml.safe_load(text)):
        assert not any(SECRET_KEY.search(part) for part in path), f"secret-looking key {'.'.join(path)}"
        assert not SECRET_VALUE.search(str(value)), f"secret-looking value at {'.'.join(path)}"
    assert not re.search(r"^\s*[A-Z][A-Z0-9_]+\s*:", text, re.M), "environment-style key in a tracked overlay"


def test_overlay_uses_only_keys_the_code_reads(env):
    """No invented keys: every dotted path is one of the groups the backend reads (settings.prod.yaml's own, plus the three the overlay
    re-asserts). Keys of the design that no code reads yet (abuse.*, history.record_browse, bug_reports.*, database.pool_size) are
    deliberately absent until P5a-T6/T7 add the readers."""
    allowed = {
        "cors.origins", "cors.allow_credentials", "popularity.impressions.enabled", "popularity.search_outcomes.enabled",
        "search.meme_from_text.query_log.enabled", "parse.model",
    }
    paths = {".".join(path[:-1] if path[-1].isdigit() else path) for path, _ in walk(load_yaml(env))}
    assert paths <= allowed, sorted(paths - allowed)


def test_placeholder_hostnames_are_the_reserved_invalid_tld(env):
    text = overlay_path(env).read_text(encoding="utf-8")
    hosts = re.findall(r"https?://([^\s/\"']+)", text)
    assert hosts and all(h.endswith(".invalid") for h in hosts), hosts
