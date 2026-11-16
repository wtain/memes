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
