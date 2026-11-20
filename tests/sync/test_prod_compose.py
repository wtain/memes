"""Guard tests of the production stacks under deploy/prod (P5d-T3 and P5d-T4). No docker, no database: the files are parsed as text and YAML.

What they pin: nothing but Caddy's 80/443 is published beyond the host loopback, the backend publishes no port, Postgres never joins the shared
edge network, every service carries the hardening keys, the images are pinned (digest for third-party images, a variable tag for ours), the env
examples hold keys only and cover every variable the compose files read, the Caddyfile keeps its log filter and limits, and pg_hba.conf keeps its
order (reject before allow, scram only, one project subnet per environment).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / "deploy" / "prod"
ENVS = ("metal", "general", "it")
# The environment table of the design (P5d): Postgres loopback port, project subnet, edge alias.
TABLE = {
    "metal": ("6432", "172.31.1.0/24", "api-metal"),
    "general": ("6434", "172.31.2.0/24", "api-general"),
    "it": ("6436", "172.31.3.0/24", "api-it"),
}


def load(path):
    return yaml.safe_load((PROD / path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stack():
    return load("compose.yaml")


@pytest.fixture(scope="module")
def edge():
    return load("edge/compose.yaml")


def all_services(stack, edge):
    for name, svc in stack["services"].items():
        yield "stack", name, svc
    for name, svc in edge["services"].items():
        yield "edge", name, svc


# ------------------------------------------------------------------------------------------------------------------ ports

def test_every_published_port_is_loopback_except_caddys_80_and_443(stack, edge):
    for which, name, svc in all_services(stack, edge):
        for port in svc.get("ports", []):
            text = str(port)
            if which == "edge" and name == "caddy":
                assert text in ("80:80", "443:443", "443:443/udp"), text
            else:
                assert text.startswith("127.0.0.1:"), f"{which}/{name} publishes {text} beyond the host loopback"


def test_the_backend_and_migrate_publish_nothing(stack):
    assert "ports" not in stack["services"]["backend"]
    assert "ports" not in stack["services"]["migrate"]
    assert "expose" not in stack["services"]["backend"]


def test_postgres_publishes_exactly_one_loopback_port(stack):
    assert stack["services"]["db"]["ports"] == ["127.0.0.1:${PG_PORT:?set PG_PORT}:5432"]


# --------------------------------------------------------------------------------------------------------------- networks

def test_db_is_never_on_the_edge_network(stack):
    db = stack["services"]["db"]
    assert list(db["networks"]) == ["internal"]
    assert "network_mode" not in db


def test_backend_is_on_the_project_network_and_the_shared_edge_with_its_alias(stack):
    nets = stack["services"]["backend"]["networks"]
    assert set(nets) == {"internal", "edge"}
    assert nets["edge"]["aliases"] == ["${EDGE_ALIAS:?set EDGE_ALIAS (api-<env>)}"]
    assert stack["networks"]["edge"] == {"name": "memes-edge", "external": True}
    assert "subnet" in stack["networks"]["internal"]["ipam"]["config"][0]


def test_migrate_has_no_network_and_is_an_ops_profile_job(stack):
    migrate = stack["services"]["migrate"]
    assert migrate["network_mode"] == "none"
    assert migrate["profiles"] == ["ops"]
    assert migrate["restart"] == "no"


def test_caddy_joins_only_the_edge_network(edge):
    assert edge["services"]["caddy"]["networks"] == ["edge"]
    assert edge["networks"]["edge"] == {"name": "memes-edge", "external": True}


# ------------------------------------------------------------------------------------------------------------- hardening

def test_every_service_has_the_hardening_keys(stack, edge):
    for which, name, svc in all_services(stack, edge):
        where = f"{which}/{name}"
        assert svc["read_only"] is True, where
        assert svc["cap_drop"] == ["ALL"], where
        assert "no-new-privileges:true" in svc["security_opt"], where
        for key in ("mem_limit", "cpus", "pids_limit"):
            assert svc.get(key), f"{where} lacks {key}"
        assert svc["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}, where
        assert any(str(t).startswith("/tmp") for t in svc.get("tmpfs", [])), f"{where} has no tmpfs /tmp"
        assert not svc.get("privileged"), where
        assert svc.get("pid") != "host" and svc.get("network_mode") not in ("host",), where


def test_added_capabilities_are_minimal(stack, edge):
    assert set(stack["services"]["db"]["cap_add"]) <= {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
    assert "cap_add" not in stack["services"]["backend"]
    assert "cap_add" not in stack["services"]["migrate"]
    assert edge["services"]["caddy"]["cap_add"] == ["NET_BIND_SERVICE"]


def test_backend_and_db_have_healthchecks(stack):
    assert "/health" in " ".join(stack["services"]["backend"]["healthcheck"]["test"])
    assert stack["services"]["db"]["healthcheck"]["test"]
    assert stack["services"]["backend"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_tmp_is_256m_for_the_services_that_spool(stack):
    assert "/tmp:size=256m" in stack["services"]["db"]["tmpfs"]
    for name in ("backend", "migrate"):  # nothing is executed from /tmp there; the spool is data only
        assert "/tmp:size=256m,noexec,nosuid" in stack["services"][name]["tmpfs"]


def test_backend_runs_as_a_dedicated_uid_with_the_environments_gid(stack):
    assert re.fullmatch(r"\$\{SVC_UID:-10001\}:\$\{SVC_GID:\?[^}]*\}", stack["services"]["backend"]["user"])


def test_images_are_mounted_read_only_and_bug_reports_read_write(stack):
    volumes = stack["services"]["backend"]["volumes"]
    images = [v for v in volumes if v.endswith(":/data/images:ro")]
    bugs = [v for v in volumes if ":/data/bug-reports" in v]
    assert len(images) == 1 and len(bugs) == 1 and not bugs[0].endswith(":ro")
    assert len(volumes) == 4, "the backend mounts images, bug reports, the log directory and the metrics volume"
    assert stack["services"]["backend"]["environment"]["BASE_PATH"] == "/data/images"


def test_backend_command_flags(stack):
    cmd = [str(c) for c in stack["services"]["backend"]["command"]]
    assert cmd[0] == "gunicorn"
    assert cmd[cmd.index("-c") + 1] == "/app/gunicorn.conf.py"
    assert cmd[cmd.index("-w") + 1] == "2"
    assert cmd[cmd.index("--log-level") + 1] == "warning"
    assert cmd[cmd.index("--forwarded-allow-ips") + 1] == "172.30.0.0/24"
    assert "--access-logfile" not in cmd and "--access-logformat" not in cmd  # no access log at all
    assert cmd[cmd.index("--bind") + 1] == "0.0.0.0:8000"


def test_backend_environment_is_prod_and_holds_no_literal_secret(stack):
    env = stack["services"]["backend"]["environment"]
    assert env["APP_STAGE"] == "prod"
    for key in ("DATABASE_URL", "APP_ENV"):
        assert env[key].startswith("${")  # always taken from the env file
    assert set(env) == {"APP_ENV", "APP_STAGE", "DATABASE_URL", "BASE_PATH", "BUG_REPORTS_PATH", "PROMETHEUS_MULTIPROC_DIR"}


def test_postgres_mounts_the_pg18_path_and_config_read_only(stack):
    volumes = stack["services"]["db"]["volumes"]
    assert any(v.endswith(":/var/lib/postgresql") for v in volumes)
    assert not any(v.endswith("/var/lib/postgresql/data") for v in volumes)  # the image refuses the pre-18 mount
    assert any(v.endswith(":/etc/memes:ro") for v in volumes)
    assert stack["services"]["db"]["shm_size"] == "256m"
    assert "config_file=/etc/memes/postgresql.conf" in stack["services"]["db"]["command"]


# ------------------------------------------------------------------------------------------------------------------ images

def test_third_party_images_are_pinned_by_digest(stack, edge):
    for image in (stack["services"]["db"]["image"], edge["services"]["caddy"]["image"]):
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image
    assert stack["services"]["db"]["image"].startswith("pgvector/pgvector:pg18")  # postgres:18 has no pgvector (P5d-T2)


def test_our_images_are_tagged_by_a_required_variable_never_latest(stack):
    for name in ("backend", "migrate"):
        image = stack["services"][name]["image"]
        assert re.fullmatch(r"memes-(backend|migrate):\$\{BACKEND_TAG:\?[^}]*\}", image), image
        assert "latest" not in image


# ------------------------------------------------------------------------------------------------------------------ metrics

def test_metrics_sidecar_is_unpublished_hardened_and_small(stack):
    svc = stack["services"]["metrics"]
    assert "ports" not in svc and "expose" not in svc
    assert svc["mem_limit"] == "64m"
    assert svc["command"] == ["python", "-m", "Backend.app.observability.exporter"]
    assert re.fullmatch(r"memes-backend:\$\{BACKEND_TAG:\?[^}]*\}", svc["image"])
    assert svc["volumes"] == ["metrics_mp:/metrics-mp:ro"]
    assert svc["environment"]["PROMETHEUS_MULTIPROC_DIR"] == "/metrics-mp"
    assert set(svc["networks"]) == {"edge"}
    assert svc["networks"]["edge"]["aliases"] == ["metrics-${MEMES_ENV:?set MEMES_ENV}"]
    assert "internal" not in svc["networks"]
    assert "metrics_mp" in stack["volumes"]


def test_nothing_publishes_the_exporter_port(stack, edge):
    for which, name, svc in all_services(stack, edge):
        for port in svc.get("ports", []):
            assert "9102" not in str(port), f"{which}/{name}"
    assert "9102" not in (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")


def test_backend_log_directory_is_required_and_matches_the_setting(stack):
    volumes = stack["services"]["backend"]["volumes"]
    logs = [v for v in volumes if v.endswith(":/var/log/memes")]
    assert len(logs) == 1 and logs[0].startswith("${LOGS_DIR:?") and logs[0].split("}", 1)[1].startswith("/backend:")
    prod = yaml.safe_load((ROOT / "environments" / "settings.prod.yaml").read_text(encoding="utf-8"))
    assert prod["observability"]["log_file"]["dir"] == "/var/log/memes"


def test_gunicorn_config_is_shipped_in_the_image():
    assert "COPY deploy/prod/gunicorn.conf.py ./gunicorn.conf.py" in (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
    assert "!deploy/prod/gunicorn.conf.py" in (ROOT / ".dockerignore").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
@pytest.mark.parametrize("env", ENVS)
def test_docker_compose_config_validates(env, tmp_path):
    values = {"MEMES_ENV": env, "PROJECT_SUBNET": TABLE[env][1], "PG_PORT": TABLE[env][0], "EDGE_ALIAS": TABLE[env][2], "SVC_GID": "1",
              "BACKEND_TAG": "x", "POSTGRES_PASSWORD": "x", "DATABASE_URL": "x", "DATABASE_URL_OWNER": "x", "IMAGES_DIR": "/tmp/i",
              "BUG_REPORTS_DIR": "/tmp/b", "PGDATA_DIR": "/tmp/p", "PGCONF_DIR": "/tmp/c", "LOGS_DIR": "/tmp/l"}
    envfile = tmp_path / "e.env"
    envfile.write_text(chr(10).join(f"{k}={v}" for k, v in values.items()) + chr(10), encoding="utf-8")
    done = subprocess.run(["docker", "compose", "-f", str(PROD / "compose.yaml"), "--env-file", str(envfile), "config", "-q"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --------------------------------------------------------------------------------------------------------- env examples

def example(name):
    return (PROD / "env" / name).read_text(encoding="utf-8")


def keys_of(text):
    return [line.split("=", 1)[0] for line in text.splitlines() if line.strip() and not line.startswith("#")]


@pytest.mark.parametrize("name", [f"{e}.prod.env.example" for e in ENVS] + ["edge.prod.env.example"])
def test_env_examples_hold_keys_without_values(name):
    for line in example(name).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=", line), f"{name}: {line!r} is not an empty KEY="
    assert "\r" not in example(name)


def _variables(path):
    text = (PROD / path).read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", text))
    optional = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):-", text))
    return required, optional


@pytest.mark.parametrize("env", ENVS)
def test_each_env_example_lists_every_variable_of_the_stack(env):
    required, optional = _variables("compose.yaml")
    keys = set(keys_of(example(f"{env}.prod.env.example")))
    assert required | optional <= keys
    assert len(keys_of(example(f"{env}.prod.env.example"))) == len(keys), "duplicate key"


def test_edge_example_lists_every_variable_of_the_edge_project():
    required, optional = _variables("edge/compose.yaml")
    assert required | optional <= set(keys_of(example("edge.prod.env.example")))


def test_env_example_comments_state_the_fixed_table_values():
    for env, (port, subnet, alias) in TABLE.items():
        text = example(f"{env}.prod.env.example")
        assert f"PG_PORT={port}" in text and subnet in text and alias in text and f"MEMES_ENV={env}" in text


def test_no_filled_env_file_is_tracked_here():
    leftovers = [p.name for p in (PROD / "env").iterdir() if not p.name.endswith(".example")]
    assert leftovers == []


# --------------------------------------------------------------------------------------------------------------- Caddyfile

def test_caddyfile_keeps_log_filter_headers_limits_and_404s():
    text = (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"request>uri delete", text) and re.search(r"request>headers delete", text)
    assert "log_append path {http.request.uri.path}" in text
    assert "Referrer-Policy no-referrer" in text and "X-Content-Type-Options nosniff" in text
    assert "Content-Security-Policy" in text and "sandbox" in text
    assert "max_size 11MB" in text and "max_size 64KB" in text and "max_header_size" in text
    for prefix in ("/api/admin*", "/api/uploads*", "/api/comics*", "/api/ingestion*", "/api/recut*", "/api/trends*"):
        assert prefix in text
    assert re.search(r"# basic_auth \{", text), "the basic-auth stanza stays in place, commented"
    assert not re.search(r"^\s*basic_auth", text, re.M)
    for env, (_, _, alias) in TABLE.items():
        assert f"import memes_site {env} {alias}" in text


def test_caddyfile_filters_the_default_logger_too():
    text = (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")
    # the global options block is the first brace block; its own `log` (no name) configures the default logger
    head = text.split("(quiet_log)", 1)[0]
    m = re.search(r"^	log \{(.*?)^	\}", head, re.M | re.S)
    assert m, "global options need a log block for the default logger (http.log.error entries carry the URI and headers)"
    for field in ("request>uri delete", "request>headers delete", "request>tls delete"):
        assert field in m.group(1)
    assert "format filter" in m.group(1)


def test_caddyfile_refuses_writes_and_framing():
    text = (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"@writes \{\s*path /api/\*\s*method PUT PATCH DELETE\s*\}", text)
    assert "X-Frame-Options DENY" in text and "frame-ancestors 'none'" in text
    assert "sandbox; default-src 'none'" in text


def test_caddyfile_holds_no_literal_hostname_or_secret():
    text = (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")
    assert "{$METAL_HOST}" in text and "{$GENERAL_HOST}" in text and "{$IT_HOST}" in text
    assert not re.search(r"\b[a-z0-9-]+\.(com|org|net|io|dev)\b", text)


# ------------------------------------------------------------------------------------------------------------------ Postgres

def render_hba(env):
    text = (PROD / "postgres" / "pg_hba.conf.template").read_text(encoding="utf-8")
    return text.replace("@ENV@", env).replace("@SUBNET@", TABLE[env][1])


def rules(env):
    out = []
    for line in render_hba(env).splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            out.append(line.split())
    return out


@pytest.mark.parametrize("env", ENVS)
def test_pg_hba_order_and_methods(env):
    text = render_hba(env)
    assert "@" not in text.replace("# ", "")
    rs = rules(env)
    db, owner, sync_r = f"memes_{env}", f"memes_{env}_owner", f"memes_{env}_sync_r"
    assert rs[0] == ["local", "all", "postgres", "peer"]
    assert rs[1] == ["local", db, owner, "scram-sha-256"]
    assert rs[2] == ["local", db, sync_r, "scram-sha-256"]
    assert rs[3] == ["host", "all", f"postgres,{owner}", "all", "reject"]
    service = rs[4:-1]
    assert [r[2] for r in service] == [f"memes_{env}_{k}" for k in ("app", "sync_w", "sync_r", "ro")]
    for r in service:
        assert r[:2] == ["host", db] and r[3] == TABLE[env][1] and r[4] == "scram-sha-256"
    assert rs[-1] == ["host", "all", "all", "all", "reject"]
    assert not any(r[-1] in ("trust", "password", "md5", "ident") for r in rs)
    assert [r for r in rs if r[0] == "local" and r[2] == f"memes_{env}_app"] == [], "the app role has no socket line"


def test_postgres_conf_pins_the_logging_and_auth_settings():
    text = (PROD / "postgres" / "postgresql.conf").read_text(encoding="utf-8")
    settings = dict(re.findall(r"^([a-z_]+)\s*=\s*(.+?)\s*(?:#.*)?$", text, re.M))
    assert settings["log_statement"] == "none"
    assert settings["log_min_error_statement"] == "panic"
    assert settings["log_min_duration_statement"] == "-1"
    assert settings["password_encryption"] == "scram-sha-256"
    assert settings["max_connections"] == "40"
    assert settings["hba_file"] == "'/etc/memes/pg_hba.conf'"
    assert settings["ssl"] == "off"


def test_provision_script_matches_the_environment_table_and_never_puts_a_password_in_argv():
    script = (PROD / "postgres" / "provision.sh").read_text(encoding="utf-8")
    for env, (_, subnet, _) in TABLE.items():
        assert re.search(rf"{env}\)\s*echo \"{re.escape(subnet)}\"", script), env
    assert "set -x" not in script
    assert not re.search(r"psql[^\n]*(-v\s+pw|PGPASSWORD|PASSWORD\s+'\$)", script)
    assert "\\\\set pw" in script and ":'\"'\"'pw'" in script  # the password travels on stdin as a psql variable
    assert "docker exec -i -u postgres" in script
    assert "memes.state" in script and "seeding" in script
    code = chr(10).join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "memes.state = '\"'\"'serving" not in code and "'serving'" not in code, "provisioning never flips the state to serving"
    order = [script.index(m) for m in ("1/5 database", "2/5 extensions", "3/5 roles and grants", "4/5 passwords", "5/5 markers")]
    assert order == sorted(order)


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32", reason="needs a POSIX bash (on Windows the bash on PATH may be WSL's)")
def test_provision_script_parses_and_renders_like_the_python_template(tmp_path):
    script = PROD / "postgres" / "provision.sh"
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    out = tmp_path / "conf"
    done = subprocess.run(["bash", str(script), "render", "general", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (out / "pg_hba.conf").read_text(encoding="utf-8") == render_hba("general")
    assert (out / "postgresql.conf").read_bytes() == (PROD / "postgres" / "postgresql.conf").read_bytes()


def _read_password_runner(value):
    script = (PROD / "postgres" / "provision.sh").read_text(encoding="utf-8")
    func = script[script.index("read_password() {"):script.index("set_password() {")]
    env = dict(os.environ, PW=value)
    return subprocess.run(["bash", "-c", func + "\nread_password PW label < /dev/null"], capture_output=True, text=True, env=env)


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32", reason="needs a POSIX bash (on Windows the bash on PATH may be WSL's)")
def test_read_password_rejects_values_with_a_newline_or_a_short_or_odd_value():
    good = "A" * 24 + "b+/=_.~-"
    ok = _read_password_runner(good)
    assert ok.returncode == 0 and ok.stdout == good
    for bad in (good + "\nALTER ROLE postgres PASSWORD 'x';", "\n" + good, good + "\r", "short", good + " x", good + "'"):
        r = _read_password_runner(bad)
        assert r.returncode != 0 and r.stdout == "", repr(bad)


def test_deploy_files_use_lf_line_endings():
    for path in list(PROD.rglob("*.yaml")) + list(PROD.rglob("*.sh")) + list(PROD.rglob("Caddyfile")) + list(PROD.rglob("*.example")):
        assert b"\r" not in path.read_bytes(), path
