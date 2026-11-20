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


def _filter_block(text):
    m = re.search(r"format filter \{.*?fields \{(.*?)\}\s*\}", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def test_file_log_carries_the_same_filter_as_the_stdout_log_and_is_bounded():
    # Pure file parsing, runs without docker (the container test below proves it for real).
    text = _caddyfile()
    snippet = text[text.index("(quiet_log)"):text.index("(memes_site)")]
    stdout_filter = _filter_block(snippet[snippet.index("output stderr"):])
    file_part = snippet[snippet.index("log access_file"):]
    assert "output file /var/log/caddy/access.log" in file_part
    assert "roll_size 10MiB" in file_part and "roll_keep 5" in file_part
    assert _filter_block(file_part) == stdout_filter
    for deleted in ("request>uri delete", "request>headers delete", "request>tls delete",
                    "request>remote_ip delete", "request>remote_port delete", "request>client_ip delete"):
        assert deleted in stdout_filter
    # the default (error) logger of the global block and the stdout log of the snippet carry the same filter
    assert text.count("request>client_ip delete") == 3
    # the id is read from the RESPONSE (server generated), never from the request
    assert "log_append request_id {http.response.header.X-Request-ID}" in snippet
    assert "http.request.header" not in snippet


def test_metrics_listener_is_internal_and_public_sites_refuse_metrics():
    text = _caddyfile()
    assert re.search(r"^http://:9180 \{\s*metrics\s*\}", text, re.M)
    assert re.search(r"^\tservers \{\s*(#[^\n]*\n\s*)*metrics\b", text, re.M)
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
            for field in ("remote_ip", "remote_port", "client_ip"):
                assert field not in text
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
