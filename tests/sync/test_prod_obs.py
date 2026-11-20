"""Guards of the memes-obs project (deploy/prod/obs): loopback-only Prometheus, pinned images, hardening, limits, scrape config.
Pure file parsing; the docker validation (compose config, promtool) is skipped without a daemon."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

OBS = Path(__file__).resolve().parents[2] / "deploy" / "prod" / "obs"


@pytest.fixture(scope="module")
def stack():
    return yaml.safe_load((OBS / "compose.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prom():
    return yaml.safe_load((OBS / "prometheus.yml").read_text(encoding="utf-8"))


def test_only_prometheus_publishes_a_port_and_only_on_loopback(stack):
    for name, svc in stack["services"].items():
        if name == "prometheus":
            assert svc["ports"] == ["127.0.0.1:9090:9090"]
        else:
            assert "ports" not in svc, name
        assert "expose" not in svc


def test_images_are_pinned_by_digest(stack):
    for name, svc in stack["services"].items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", svc["image"]), name


def test_services_follow_the_hardening_anchor_and_have_limits(stack):
    for name, svc in stack["services"].items():
        assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"], name
        assert svc["security_opt"] == ["no-new-privileges:true"], name
        assert svc["restart"] == "unless-stopped"
        assert svc["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}, name
        assert svc["mem_limit"] and svc["cpus"] and svc["pids_limit"], name
        assert "privileged" not in svc and svc.get("network_mode") is None and "pid" not in svc, name


def test_prometheus_flags_and_budget(stack):
    svc = stack["services"]["prometheus"]
    assert svc["mem_limit"] == "200m"
    for flag in ("--storage.tsdb.retention.time=30d", "--storage.tsdb.retention.size=2GB", "--web.listen-address=0.0.0.0:9090"):
        assert flag in svc["command"]
    assert "./prometheus.yml:/etc/prometheus/prometheus.yml:ro" in svc["volumes"]
    assert "edge" in svc["networks"]


def test_edge_network_is_the_external_shared_one(stack):
    assert stack["networks"]["edge"] == {"name": "memes-edge", "external": True}
    assert "node-exporter" in stack["services"]
    node = stack["services"]["node-exporter"]
    assert node["networks"] == ["obs"]
    assert all(v.endswith(":ro") or ":ro," in v for v in node["volumes"])


def test_prometheus_yml_names_the_four_jobs_and_targets(prom):
    assert prom["global"]["scrape_interval"] == "15s"
    jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
    assert set(jobs) == {"backend", "caddy", "postgres", "node"}
    backend = {(t, c["labels"]["env"]) for c in jobs["backend"]["static_configs"] for t in c["targets"]}
    assert backend == {(f"metrics-{e}:9102", e) for e in ("metal", "general", "it")}
    assert jobs["caddy"]["static_configs"][0]["targets"] == ["caddy:9180"]
    pg = {(t, c["labels"]["env"]) for c in jobs["postgres"]["static_configs"] for t in c["targets"]}
    assert pg == {(f"postgres-exporter-{e}:9187", e) for e in ("metal", "general", "it")}
    assert jobs["node"]["static_configs"][0]["targets"] == ["node-exporter:9100"]


def _docker_ok():
    return bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="needs docker")
def test_promtool_accepts_the_config_on_the_pinned_image(stack):
    image = stack["services"]["prometheus"]["image"]
    run = subprocess.run(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{OBS / 'prometheus.yml'}:/p.yml:ro",
                          image, "check", "config", "/p.yml"], capture_output=True, text=True, env={"MSYS_NO_PATHCONV": "1", **__import__("os").environ})
    assert run.returncode == 0, run.stdout + run.stderr
