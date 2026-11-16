"""Storage/db.py reads its pool bounds from tracked settings (P5a-T7). Fresh interpreters: the settings object is built once at import."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = r"""
import json, logging
logging.basicConfig(level=logging.INFO)
import Storage.db as db
db.log_pool_bounds()
print(json.dumps({"pool": db.engine.pool.size(), "overflow": db.engine.pool._max_overflow, "max": db.max_connections()}))
"""


def _env(extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABASE__") and k not in ("APP_STAGE", "APP_ENV", "DATABASE_URL")}
    env.update(DATABASE_URL="postgresql+asyncpg://placeholder:placeholder@localhost/none", PYTHONPATH=str(ROOT), **extra)
    return env


def run(extra_env, code=PROBE):
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=_env(extra_env), capture_output=True, text=True, timeout=120)


def probe(extra_env):
    done = run(extra_env)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1]), done.stderr


@pytest.mark.parametrize("env", ["metal", "general", "it"])
def test_dev_keeps_ten_and_twenty(env):
    got, _ = probe({"APP_ENV": env})
    assert got == {"pool": 10, "overflow": 20, "max": 30}


@pytest.mark.parametrize("env", ["metal", "general", "it"])
def test_prod_uses_five_and_five_and_logs_the_ceiling_without_secrets(env):
    got, log = probe({"APP_ENV": env, "APP_STAGE": "prod"})
    assert got == {"pool": 5, "overflow": 5, "max": 10}
    assert "pool_size=5 max_overflow=5 max_connections_per_process=10 stage=prod" in log
    assert "placeholder" not in log


def test_a_batch_style_import_without_app_env_still_works():
    """Batch scripts import Storage.db before load_env(): APP_ENV is unset (defaults to general) and the import must not fail."""
    got, _ = probe({})
    assert got["max"] == 30


def test_a_bad_pool_value_fails_loudly_at_import():
    done = run({"DATABASE__POOL_SIZE": "0"}, code="import Storage.db")
    assert done.returncode != 0 and "database.pool_size" in done.stderr
