"""Grafana dashboards, datasource and compose of deploy/observability, plus the ports in environments/Environments.md. No Docker needed
(the compose validation is skipped without a daemon)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "deploy" / "observability"
DASHBOARDS = sorted((OBS / "dashboards").glob("*.json"))
ENVIRONMENTS_MD = ROOT / "environments" / "Environments.md"
GRAFANA_PORT, TUNNEL_PORT = 3300, 19090
EXPORTER_PREFIXES = ("node_", "pg_", "caddy_")
KNOWN_FUNCTIONS = {"sum", "rate", "increase", "histogram_quantile", "by", "le", "without", "avg", "max", "min", "irate", "on", "and", "or"}


def metrics_py_names() -> set[str]:
    """The metric names of Backend/app/observability/metrics.py after prometheus_client suffixing."""
    text = (ROOT / "Backend" / "app" / "observability" / "metrics.py").read_text(encoding="utf-8")
    names = set()
    for kind, name in re.findall(r'(Counter|Gauge|Histogram)\(\s*"([a-z_]+)"', text):
        if kind == "Counter":
            names.add(name if name.endswith("_total") else name + "_total")
        elif kind == "Histogram":
            names.update(name + s for s in ("_bucket", "_sum", "_count"))
        else:
            names.add(name)
    return names


def metric_names_in(expr: str) -> set[str]:
    expr = re.sub(r'"[^"]*"', '""', expr)          # label values
    expr = re.sub(r"\{[^}]*\}", "", expr)           # selectors
    expr = re.sub(r"\[[^\]]*\]", "", expr)          # ranges
    expr = re.sub(r"\b(by|without)\s*\([^)]*\)", "", expr)
    return {w for w in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", expr) if w not in KNOWN_FUNCTIONS}


def exprs(dash):
    for panel in dash["panels"]:
        for target in panel.get("targets", []):
            yield panel["title"], target["expr"]


def test_three_dashboards_exist():
    assert [p.stem for p in DASHBOARDS] == ["health", "performance", "usage"]


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_is_valid_json_with_panels_and_the_fixed_datasource(path):
    dash = json.loads(path.read_text(encoding="utf-8"))
    assert dash["uid"].startswith("memes-") and dash["title"] and dash["panels"]
    assert b"\r" not in path.read_bytes()
    for panel in dash["panels"]:
        assert panel["datasource"] == {"type": "prometheus", "uid": "memes-prometheus"}, panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "memes-prometheus"
    assert "${DS_" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_every_dashboard_has_the_env_variable_and_uses_it(path):
    dash = json.loads(path.read_text(encoding="utf-8"))
    env = [v for v in dash["templating"]["list"] if v["name"] == "env"]
    assert len(env) == 1 and env[0]["query"] == "metal,general,it"
    assert any('env="$env"' in e for _, e in exprs(dash))


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_every_expression_references_a_known_metric(path):
    known = metrics_py_names() | {"up"}
    assert {"http_requests_total", "http_request_duration_seconds_bucket", "rate_limited_total", "pageviews_total"} <= known
    for title, expr in exprs(json.loads(path.read_text(encoding="utf-8"))):
        names = metric_names_in(expr)
        assert names, (title, expr)
        for name in names:
            assert name in known or name.startswith(EXPORTER_PREFIXES), (title, name)


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_no_hard_coded_host_and_no_private_labels(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"https?://|\b\d{1,3}(\.\d{1,3}){3}\b|\.(com|net|org|io)\b|localhost", text)
    assert not re.search(r"(search_text|query_text|image_id|client_addr)", text)


def test_datasource_is_fixed_and_points_at_the_tunnel():
    ds = yaml.safe_load((OBS / "provisioning" / "datasources" / "prometheus.yml").read_text(encoding="utf-8"))["datasources"][0]
    assert ds["uid"] == "memes-prometheus" and ds["type"] == "prometheus"
    assert ds["url"] == f"http://host.docker.internal:{TUNNEL_PORT}"


def test_dashboard_provider_reads_the_mounted_directory():
    prov = yaml.safe_load((OBS / "provisioning" / "dashboards" / "dashboards.yml").read_text(encoding="utf-8"))["providers"][0]
    svc = yaml.safe_load((OBS / "compose.yaml").read_text(encoding="utf-8"))["services"]["grafana"]
    assert any(v.endswith(f":{prov['options']['path']}:ro") for v in svc["volumes"])


# ------------------------------------------------------------------------------------------------ compose guards

@pytest.fixture(scope="module")
def grafana():
    return yaml.safe_load((OBS / "compose.yaml").read_text(encoding="utf-8"))["services"]["grafana"]


def test_grafana_is_published_on_loopback_only(grafana):
    assert grafana["ports"] == [f"127.0.0.1:{GRAFANA_PORT}:3000"]
    assert "network_mode" not in grafana


def test_image_is_pinned_by_digest(grafana):
    assert re.fullmatch(r"grafana/grafana:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", grafana["image"])


def test_no_anonymous_access_and_password_comes_from_the_untracked_env(grafana):
    env = grafana["environment"]
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false" and env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert env["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GRAFANA_ADMIN_PASSWORD:?")
    assert "GF_INSTALL_PLUGINS" not in env
    assert "host.docker.internal:host-gateway" in grafana["extra_hosts"]
    assert grafana["mem_limit"] and grafana["pids_limit"]


def test_provisioning_mounts_are_read_only_and_data_is_a_named_volume(grafana):
    vols = grafana["volumes"]
    assert "grafana_data:/var/lib/grafana" in vols
    assert all(v.endswith(":ro") for v in vols if v.startswith("./"))


def test_env_file_is_ignored_and_the_example_holds_no_real_value():
    assert subprocess.run(["git", "check-ignore", "-q", "deploy/observability/.env"], cwd=ROOT).returncode == 0
    tracked = subprocess.run(["git", "ls-files", "deploy/observability"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert "deploy/observability/.env" not in tracked
    assert "change-me" in (OBS / ".env.example").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
def test_compose_config_validates():
    r = subprocess.run(["docker", "compose", "--env-file", str(OBS / ".env.example"), "-f", str(OBS / "compose.yaml"), "config", "-q"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "daemon" in r.stderr.lower():
        pytest.skip("no docker daemon")
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------------------------------------ ports

def test_ports_are_documented_once_and_do_not_collide():
    text = ENVIRONMENTS_MD.read_text(encoding="utf-8")
    for port in (GRAFANA_PORT, TUNNEL_PORT):
        rows = [line for line in text.splitlines() if line.startswith("|") and re.search(rf"\| {port} \|", line)]
        assert len(rows) == 1, port
    taken = {8081, 8082, 8083, 5173, 5174, 5175, 5432, 5434, 5436, 16432, 16434, 16436, 6432, 6434, 6436, 9090}
    assert not {GRAFANA_PORT, TUNNEL_PORT} & taken
