"""Proof, on the pinned Caddy image, that the real edge Caddyfile writes neither the searched text nor a cookie into the container log,
also when the proxy fails (the http.log.error entry of the DEFAULT logger carries request>uri and request>headers unless filtered).
Needs a docker daemon and the pinned image; skipped otherwise. Throwaway container p5d-g-caddy-*, loopback port chosen by docker."""
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

PROD = Path(__file__).resolve().parents[2] / "deploy" / "prod"


def _pinned_image():
    text = (PROD / "edge" / "compose.yaml").read_text(encoding="utf-8")
    m = re.search(r"image:\s*(caddy:\S+)", text)
    return m.group(1) if m else None


def _docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


IMAGE = _pinned_image()
pytestmark = pytest.mark.skipif(not (IMAGE and _docker_ok()), reason="needs docker and the pinned caddy image")


def test_no_query_text_or_cookie_in_the_caddy_log_on_a_failed_proxy_request():
    name = "p5d-g-caddy-" + uuid.uuid4().hex[:8]
    caddyfile = PROD / "edge" / "Caddyfile"
    try:
        run = subprocess.run(
            ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8080",
             "-e", "ACME_EMAIL=ops@example.test", "-e", "METAL_HOST=http://:8080", "-e", "GENERAL_HOST=http://:8081", "-e", "IT_HOST=http://:8082",
             "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", IMAGE],
            capture_output=True, text=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if run.returncode != 0:
            pytest.skip("cannot start the pinned caddy image here: " + run.stderr[-200:])
        port = None
        for _ in range(30):
            out = subprocess.run(["docker", "port", name, "8080/tcp"], capture_output=True, text=True).stdout.strip()
            if out:
                port = out.splitlines()[0].rsplit(":", 1)[1]
                break
            time.sleep(0.3)
        assert port
        time.sleep(2)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/images?q=SEARCHSECRET",
                                     headers={"Cookie": "sess=COOKIESECRET", "Authorization": "Bearer AUTHSECRET", "Referer": "http://x/?q=REFSECRET"})
        try:
            urllib.request.urlopen(req, timeout=30)
            status = 200
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 502, "the upstream alias does not resolve, the proxy must fail"
        time.sleep(1)
        log = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        text = log.stdout + log.stderr
        assert '"level":"error"' in text, "the failing request must have produced an error entry (else the test proves nothing)"
        for secret in ("SEARCHSECRET", "COOKIESECRET", "AUTHSECRET", "REFSECRET"):
            assert secret not in text
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


# ------------------------------------------------------------------------------------------------------------------ O6b: metrics and file log

def _caddyfile():
    return (PROD / "edge" / "Caddyfile").read_text(encoding="utf-8")


def _deleted_fields(text):
    """The set of fields each `format filter { ... fields { ... } }` block of `text` deletes, one set per block, in order."""
    out = []
    for block in re.findall(r"format filter \{.*?fields \{(.*?)\}\s*\}", text, re.S):
        lines = [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
        assert all(line.endswith(" delete") for line in lines), lines  # only deletions: no field is kept or rewritten
        out.append({line[:-len(" delete")] for line in lines})
    return out


REQUIRED_DELETIONS = {"request>uri", "request>headers", "request>tls", "request>remote_ip", "request>remote_port", "request>client_ip",
                      "resp_headers"}


def test_file_log_carries_the_same_filter_as_the_stdout_log_and_is_bounded():
    # Pure file parsing, runs without docker (the container test below proves it for real).
    text = _caddyfile()
    snippet = text[text.index("(quiet_log)"):text.index("(memes_site)")]
    file_part = snippet[snippet.index("log access_file"):]
    assert "output file /var/log/caddy/access.log" in file_part
    assert "roll_size 10MiB" in file_part and "roll_keep 5" in file_part
    # Three filters, parsed one by one: the default (error) logger of the global block, the snippet's stdout log and its file log.
    # Each deletes the same set, which holds every required field.
    assert len(_deleted_fields(text[:text.index("(quiet_log)")])) == 1
    assert len(_deleted_fields(snippet[snippet.index("output stderr"):snippet.index("log access_file")])) == 1
    assert len(_deleted_fields(file_part)) == 1
    sets = _deleted_fields(text)
    assert len(sets) == 3
    for deleted in sets:
        assert REQUIRED_DELETIONS <= deleted, REQUIRED_DELETIONS - deleted
        assert deleted == sets[0]
    # the id is read from the RESPONSE (server generated), never from the request
    assert "log_append request_id {http.response.header.X-Request-ID}" in snippet
    assert "http.request.header" not in snippet


def test_metrics_listener_is_internal_and_public_sites_refuse_metrics():
    text = _caddyfile()
    assert re.search(r"^http://:9180 \{\s*metrics\s*\}", text, re.M)
    global_block = text[:text.index("(quiet_log)")]
    assert re.search(r"^\tmetrics$", global_block, re.M)  # the global option (spec 5.4), not the per-server one
    assert not re.search(r"^\tservers \{[^}]*\bmetrics\b", global_block, re.M)
    assert "/metrics*" in re.search(r"@unmounted path ([^\n]*)", text).group(1)
    compose = (PROD / "edge" / "compose.yaml").read_text(encoding="utf-8")
    assert "9180" not in compose and "2019" not in compose  # never published, admin API not exposed
    assert "/var/log/caddy" in compose


def _wait_port(name):
    for _ in range(30):
        out = subprocess.run(["docker", "port", name, "8080/tcp"], capture_output=True, text=True).stdout.strip()
        if out:
            return out.splitlines()[0].rsplit(":", 1)[1]
        time.sleep(0.3)
    return None


@pytest.mark.skipif(not (IMAGE and _docker_ok()), reason="needs docker and the pinned caddy image")
def test_metrics_internal_only_and_file_log_holds_no_query_cookie_or_authorization():
    name = "o6b-caddy-" + uuid.uuid4().hex[:8]
    caddyfile = PROD / "edge" / "Caddyfile"
    try:
        run = subprocess.run(
            ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8080",
             "-e", "ACME_EMAIL=ops@example.test", "-e", "METAL_HOST=http://:8080", "-e", "GENERAL_HOST=http://:8081", "-e", "IT_HOST=http://:8082",
             "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", IMAGE],
            capture_output=True, text=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if run.returncode != 0:
            pytest.skip("cannot start the pinned caddy image here: " + run.stderr[-200:])
        port = _wait_port(name)
        assert port
        time.sleep(2)

        # the metrics listener answers inside the container (the edge network's view) ...
        inner = subprocess.run(["docker", "exec", name, "wget", "-qO-", "http://127.0.0.1:9180/metrics"], capture_output=True, text=True)
        assert inner.returncode == 0 and ("caddy_http_" in inner.stdout or "go_goroutines" in inner.stdout)
        # ... and is not published, and the public site refuses the path
        assert "9180" not in subprocess.run(["docker", "port", name], capture_output=True, text=True).stdout
        for path in ("/metrics", "/metrics?q=SEARCHSECRET"):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                         headers={"Cookie": "sess=COOKIESECRET", "Authorization": "Bearer AUTHSECRET"})
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(req, timeout=30)
            assert err.value.code == 404
        # a proxied request (502: the upstream alias does not resolve) with a client-sent id
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/images?q=SEARCHSECRET",
                                     headers={"Cookie": "sess=COOKIESECRET", "Authorization": "Bearer AUTHSECRET",
                                              "X-Request-ID": "CLIENTIDSECRET", "Referer": "http://x/?q=REFSECRET"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=30)
        time.sleep(1)
        log = subprocess.run(["docker", "exec", name, "cat", "/var/log/caddy/access.log"], capture_output=True, text=True).stdout
        assert '"path":"/api/images"' in log and '"path":"/metrics"' in log, "the file log must hold the requests (else the test proves nothing)"
        container_log = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        for text in (log, container_log.stdout + container_log.stderr):
            for secret in ("SEARCHSECRET", "COOKIESECRET", "AUTHSECRET", "REFSECRET", "CLIENTIDSECRET", "Cookie", "Authorization"):
                assert secret not in text
            for field in ("remote_ip", "remote_port", "client_ip", "resp_headers"):
                assert field not in text
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.mark.skipif(not (IMAGE and _docker_ok()), reason="needs docker and the pinned caddy image")
def test_response_headers_such_as_content_disposition_never_reach_the_logs():
    # A real upstream (`caddy respond` on the same image, alias api-metal on a throwaway network) answers like GET /api/images/<id>:
    # a Content-Disposition with a file name and the server-generated X-Request-ID. The response passes through the edge Caddy,
    # so the absence of the header in its logs is not vacuous.
    suffix = uuid.uuid4().hex[:8]
    network, upstream, edge = "o6b-net-" + suffix, "o6b-up-" + suffix, "o6b-edge-" + suffix
    caddyfile = PROD / "edge" / "Caddyfile"
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        if subprocess.run(["docker", "network", "create", network], capture_output=True).returncode != 0:
            pytest.skip("cannot create a docker network here")
        up = subprocess.run(
            ["docker", "run", "-d", "--name", upstream, "--network", network, "--network-alias", "api-metal", IMAGE,
             "caddy", "respond", "--listen", ":8000", "--status", "200",
             "--header", "Content-Disposition: inline; filename=UPLOADNAMESECRET.jpg",  # the flag is CSV-parsed: no quotes
             "--header", "X-Request-ID: 0123456789abcdef0123456789abcdef", "image-bytes"],
            capture_output=True, text=True, env=env)
        if up.returncode != 0:
            pytest.skip("cannot start the upstream container here: " + up.stderr[-200:])
        run = subprocess.run(
            ["docker", "run", "-d", "--name", edge, "--network", network, "-p", "127.0.0.1::8080",
             "-e", "ACME_EMAIL=ops@example.test", "-e", "METAL_HOST=http://:8080", "-e", "GENERAL_HOST=http://:8081", "-e", "IT_HOST=http://:8082",
             "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", IMAGE],
            capture_output=True, text=True, env=env)
        if run.returncode != 0:
            pytest.skip("cannot start the pinned caddy image here: " + run.stderr[-200:])
        port = _wait_port(edge)
        assert port
        response = None
        for _ in range(20):  # the upstream may still be starting
            try:
                response = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/images/abc?q=SEARCHSECRET", timeout=30)
                break
            except urllib.error.HTTPError:
                time.sleep(0.5)
        assert response is not None and response.status == 200
        assert "UPLOADNAMESECRET" in response.headers.get("Content-Disposition", ""), "the header must pass through (else this proves nothing)"
        time.sleep(1)
        file_log = subprocess.run(["docker", "exec", edge, "cat", "/var/log/caddy/access.log"], capture_output=True, text=True).stdout
        container = subprocess.run(["docker", "logs", edge], capture_output=True, text=True)
        assert '"path":"/api/images/abc"' in file_log and '"status":200' in file_log
        assert '"request_id":"0123456789abcdef0123456789abcdef"' in file_log  # appended from the response placeholder: survives
        for text in (file_log, container.stdout + container.stderr):
            for needle in ("UPLOADNAMESECRET", "Content-Disposition", "resp_headers", "SEARCHSECRET"):
                assert needle not in text
    finally:
        subprocess.run(["docker", "rm", "-f", edge, upstream], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
