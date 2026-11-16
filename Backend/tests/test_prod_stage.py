"""Prod behaviour of the backend (P5a-T3, P5a-T5 through the full app).

APP_STAGE is read once at import time and the route gate is built when the routers are imported, so each case runs
the real application in a child process (Backend/tests/prod_app_probe.py) with APP_STAGE=prod and a prepared
environment. Mocked everything: no database, no network, no real secrets.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "Backend" / "tests" / "prod_app_probe.py"
NOT_FOUND = {"detail": "Not Found"}

HOSTILE_ORIGIN = "https://memes.example"


def _symlink(link: Path, target: Path, directory: bool = False) -> bool:
    try:
        os.symlink(target, link, target_is_directory=directory)
        return True
    except (OSError, NotImplementedError):
        return False


def _run(env_name: str, base_path: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"APP_STAGE", "APP_ENV", "DATABASE_URL", "BASE_PATH"}}
    env.update({
        "APP_STAGE": "prod", "APP_ENV": env_name,
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
        "BASE_PATH": str(base_path),
        "CORS__ORIGINS": json.dumps([HOSTILE_ORIGIN]),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(PROBE)], cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=180)


def _probe(env_name: str, base_path: Path, extra_env: dict | None = None) -> dict:
    result = _run(env_name, base_path, extra_env)
    lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE_JSON=")]
    assert result.returncode == 0 and lines, f"probe failed:\n{result.stdout[-2000:]}\n{result.stderr[-4000:]}"
    return json.loads(lines[0][len("PROBE_JSON="):])


@pytest.fixture(scope="module")
def images_dir(tmp_path_factory):
    """BASE_PATH of the probe: an images directory next to a secret it must never serve."""
    root = tmp_path_factory.mktemp("prodroot")
    images = root / "images"
    images.mkdir()
    (root / "secret.txt").write_text("TOPSECRET")
    (images / "ok.jpg").write_bytes(b"OKBYTES")
    (images / "adir").mkdir()
    (images / "sub").mkdir()
    (images / "sub" / "inner.jpg").write_bytes(b"INNER")
    (images / "target-in.jpg").write_bytes(b"INSIDE")
    _symlink(images / "linkout.jpg", root / "secret.txt")
    _symlink(images / "linkin.jpg", images / "target-in.jpg")
    _symlink(images / "linkdir", root, directory=True)
    return images


# image id -> file name the fake database returns for it
HOSTILE_NAMES = {
    "n01": "../secret.txt", "n02": "..", "n03": ".", "n04": "%2e%2e/secret.txt", "n05": "..%2fsecret.txt",
    "n06": "/etc/passwd", "n07": "C:\\Windows\\win.ini", "n08": "a\u0000b.jpg", "n09": "sub/inner.jpg",
    "n10": "sub\\inner.jpg", "n11": "linkout.jpg", "n12": "linkin.jpg", "n13": "adir", "n14": "linkdir/../secret.txt",
    "n15": "linkdir/secret.txt", "n16": "%252e%252e%252fsecret.txt", "n17": "ok.jpg/../../secret.txt",
    "n18": "\u2025/secret.txt", "n19": "ok.jpg\u0000.txt", "good": "ok.jpg",
}
URLS = ["..%2fsecret.txt", "%2e%2e%2fsecret.txt", "%252e%252e%252fsecret.txt", "..%5csecret.txt", "..",
        "%2e%2e", "ok.jpg%00", "%2fetc%2fpasswd", "ok.jpg/", "ok.jpg/..%2fsecret.txt"]


@pytest.fixture(scope="module", params=["general", "metal", "it"])
def probe(request, images_dir):
    return request.param, _probe(request.param, images_dir, {
        "PROBE_NAMES": json.dumps(HOSTILE_NAMES), "PROBE_URLS": json.dumps(URLS)})


def test_stage_and_environment_are_what_the_probe_was_started_with(probe):
    env_name, out = probe
    assert out["stage_env"] == {"APP_STAGE": "prod", "APP_ENV": env_name}


def test_every_dev_only_route_answers_404_exactly_like_a_missing_path(probe):
    _, out = probe
    calls = out["dev_only_calls"]
    assert len(calls) == 69
    for key, result in calls.items():
        assert result["status"] == 404 and result["body"] == NOT_FOUND, key
        assert result["allow"] is None, key


def test_any_method_on_a_dev_only_path_answers_404_without_allow(probe):
    _, out = probe
    calls = out["dev_only_any_method_calls"]
    assert len(calls) > 100
    # the mixed routers' wrong-method cases that used to leak a 405, e.g. POST /api/history
    assert "POST /api/history" in calls and "POST /api/diagnostics/statistics" in calls
    for key, result in calls.items():
        assert result["status"] == 404 and result["body"] == NOT_FOUND, key
        assert result["allow"] is None, key


def test_fully_dev_only_routers_are_neither_imported_nor_mounted(probe):
    _, out = probe
    assert out["dev_only_modules_imported"] == []
    mounted = {tuple(row) for row in out["mounted_routes"]}
    for _, path in mounted:
        assert not path.startswith(("/api/admin", "/api/comics", "/api/trends", "/api/uploads", "/api/recut",
                                    "/api/ingestion", "/api/meme-from-text/ratings", "/api/meme-from-text/profiles"))
    # the mixed routers stay mounted and carry tombstones; every mounted route has a policy line
    from Backend.app.stage_policy import ROUTE_POLICY
    assert mounted <= set(ROUTE_POLICY)


def test_docs_and_openapi_are_off(probe):
    _, out = probe
    assert out["docs_urls"] == [None, None, None]
    for name in ("docs", "redoc", "openapi"):
        assert out["extra_calls"][name]["status"] == 404, name


def test_slashes_case_encoding_and_method_cases(probe):
    _, out = probe
    calls = out["extra_calls"]
    assert out["redirect_slashes"] is False
    for name in ("slash_literal", "slash_reader", "encoded_literal", "upper", "double_slash", "encoded_slash",
                 "unmounted_prefix", "unmounted_post", "unmounted_comics_by_image"):
        assert calls[name]["status"] == 404 and calls[name]["body"] == NOT_FOUND, name
    # HEAD and a plain OPTIONS are not routed by APIRoute; the Allow header lists only methods that exist
    assert calls["head_reader"]["status"] == 405 and calls["head_reader"]["allow"] == "GET"
    assert calls["options_plain"]["status"] == 405
    assert calls["wrong_method_reader"]["status"] == 405 and calls["wrong_method_reader"]["allow"] == "GET"


def test_cors_allows_only_the_configured_origin_without_credentials(probe):
    _, out = probe
    cors = out["cors"]
    assert cors["allowed_origin"]["status"] == 200 and cors["allowed_origin"]["acao"] == HOSTILE_ORIGIN
    assert cors["allowed_origin"]["acac"] is None
    for origin in ("localhost", "localhost_5174"):
        assert cors[origin]["status"] == 400 and cors[origin]["acao"] is None
    assert cors["put_preflight"]["status"] == 400          # PUT is dev only: not an allowed method on prod
    assert cors["simple_get"]["acao"] == HOSTILE_ORIGIN and cors["simple_get"]["acac"] is None
    assert out["settings"]["cors_allow_credentials"] is False


def test_health_has_a_fixed_detail(probe):
    _, out = probe
    assert out["health_ok"]["status"] == 200 and out["health_ok"]["body"] == {"status": "healthy"}
    assert out["health_fail"]["status"] == 503
    assert out["health_fail"]["body"] == {"detail": "Database unavailable"}
    assert "secret_user" not in json.dumps(out["health_fail"])


def test_capabilities_payload_on_prod(probe):
    env_name, out = probe
    assert out["capabilities"]["status"] == 200
    assert out["capabilities"]["body"] == {
        "stage": "prod", "environment": env_name,
        "features": {"memeFromText": False, "concepts": True, "trends": False, "comics": False, "ratings": False,
                     "statistics": False, "uploads": False, "history": False, "admin": False},
    }


def test_abuse_bounds_are_active_in_the_full_prod_app(probe):
    _, out = probe
    for name in ("oversize_q", "many_facets", "long_facet"):
        assert out["abuse"][name]["status"] == 422, name
    assert out["abuse"]["declared_body"]["status"] == 413


def test_overlay_switches_scheduler_and_parser_off(probe):
    _, out = probe
    assert out["settings"]["stage"] == "prod"
    assert out["settings"]["scheduler_enabled"] is False
    assert out["settings"]["parse_model"] == ""
    assert out["parser"] is True            # get_query_parser() is None on every environment


def test_lifespan_skips_the_scheduler_and_checks_the_environment_marker(probe):
    env_name, out = probe
    assert out["lifespan_match"] == {"started": True, "scheduler_calls": 0}
    for key in ("lifespan_wrong_marker", "lifespan_no_marker"):
        assert out[key]["started"] is False and out[key]["scheduler_calls"] == 0
        assert f"APP_ENV '{env_name}'" in out[key]["error"]


def test_hostile_image_file_names_are_never_served_through_the_prod_app(probe, images_dir):
    _, out = probe
    served = out["files_by_id"]
    assert served["good"]["status"] == 200 and served["good"]["body"] == "OKBYTES"
    for image_id, result in served.items():
        if image_id == "good":
            continue
        assert result["status"] == 404, (image_id, HOSTILE_NAMES[image_id], result)
        assert "TOPSECRET" not in result["body"] and "INNER" not in result["body"] and "INSIDE" not in result["body"]
    for raw, result in out["files_by_url"].items():
        assert result["status"] in (404, 405, 422), (raw, result)
        assert "TOPSECRET" not in result["body"]


def test_prod_refuses_to_start_without_app_env():
    env = {k: v for k, v in os.environ.items() if k not in {"APP_ENV", "APP_STAGE"}}
    env["APP_STAGE"] = "prod"
    result = subprocess.run([sys.executable, "-c", "import config.settings"], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0 and "APP_ENV" in result.stderr


def test_unknown_stage_value_is_refused():
    env = {k: v for k, v in os.environ.items() if k != "APP_STAGE"}
    env["APP_STAGE"] = "production"
    result = subprocess.run([sys.executable, "-c", "import config.stage"], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0 and "APP_STAGE" in result.stderr


def test_dev_stage_loads_no_prod_overlay():
    """APP_STAGE unset or dev: the prod overlay is not read (its keys are absent) and the scheduler setting is the dev one."""
    for stage in (None, "dev"):
        env = {k: v for k, v in os.environ.items() if k != "APP_STAGE"}
        env.update({"APP_ENV": "metal", "DATABASE_URL": "postgresql+asyncpg://t:t@localhost:5432/t"})
        if stage:
            env["APP_STAGE"] = stage
        code = ("from config.settings import settings; "
                "print(settings.get('stage'), settings.get('cors.origins'), settings.get('scheduler.enabled'), "
                "bool(settings.get('parse.model')))")
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True,
                                text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["None", "None", "True", "True"]
