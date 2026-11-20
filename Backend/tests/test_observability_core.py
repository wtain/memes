"""Observability core (O2): request metrics middleware, label bounds, multiprocess exporter, switch-off behaviour. No database."""
import asyncio
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
import signal
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.images import get_image_service, router as images_router
from Backend.app.observability import exporter, metrics
from Backend.app.observability.middleware import MetricsMiddleware
from Storage.db import get_async_db

ROOT = str(Path(__file__).resolve().parents[2])


def build_app():
    service = AsyncMock()
    service.search.return_value = {"items": [], "hasNext": False, "facets": []}
    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.dependency_overrides[get_image_service] = lambda: service
    app.dependency_overrides[get_async_db] = lambda: MagicMock()  # never a real session
    app.add_middleware(MetricsMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def request_samples():
    return [s for family in metrics.HTTP_REQUESTS.collect() for s in family.samples if s.name == "http_requests_total"]


def test_route_label_is_template_and_unmatched_is_one_series():
    client = build_app()
    before = {tuple(sorted(s.labels.items())) for s in request_samples()}
    for _ in range(500):
        client.get("/nothing/" + uuid.uuid4().hex)
    new = {tuple(sorted(s.labels.items())) for s in request_samples()} - before
    assert len(new) <= 1
    unmatched = [s for s in request_samples() if s.labels["route"] == "unmatched"]
    assert len(unmatched) == 1 and unmatched[0].labels == {"route": "unmatched", "method": "GET", "status_class": "4xx"}
    assert unmatched[0].value >= 500
    client.get(f"/api/images/{uuid.uuid4()}")
    assert any(s.labels["route"].startswith("/api/images/{") for s in request_samples())


def test_status_class_and_methods_bounded():
    assert [metrics.status_class(c) for c in (101, 204, 301, 404, 503)] == ["1xx", "2xx", "3xx", "4xx", "5xx"]
    assert metrics.status_class(0) == "other" and metrics.status_class(999) == "other"
    assert metrics.method_label("GET") == "GET"
    assert metrics.method_label("PROPFIND") == "OTHER" and metrics.method_label(None) == "OTHER"
    build_app().request("PURGE", "/api/images")
    methods = {s.labels["method"] for s in request_samples()}
    assert "PURGE" not in methods and "OTHER" in methods


def test_metrics_failure_never_changes_a_response(monkeypatch):
    class Broken:
        def labels(self, *args, **kwargs):
            raise RuntimeError("metrics are down")

        def inc(self, *args):
            raise RuntimeError("metrics are down")

        dec = inc

    client = build_app()
    plain = client.get("/api/images")
    for name in ("HTTP_REQUESTS", "HTTP_DURATION", "HTTP_IN_FLIGHT"):
        monkeypatch.setattr(metrics, name, Broken())
    broken = client.get("/api/images")
    assert broken.status_code == plain.status_code == 200
    assert broken.json() == plain.json()


def run_python(code, env_extra=None):
    env = {**os.environ, "PYTHONPATH": ROOT, **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, cwd=ROOT, capture_output=True, text=True,
                          timeout=120)


def test_disabled_adds_no_middleware_and_no_import():
    result = run_python("""
        import os, sys
        os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
        os.environ.setdefault("BASE_PATH", "/tmp/test_images")
        os.environ.setdefault("APP_ENV", "general")
        from Backend.app.main import app
        from Backend.app.observability import enabled
        assert enabled() is False
        assert not any(m.cls.__name__ == "MetricsMiddleware" for m in app.user_middleware)
        assert "prometheus_client" not in sys.modules
        assert "Backend.app.observability.metrics" not in sys.modules
        print("ok")
    """)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr[-2000:]


def scrape(directory) -> str:
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(directory)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(exporter.app({"type": "http", "method": "GET", "path": "/metrics"}, None, send))
    assert sent[0]["status"] == 200
    return sent[1]["body"].decode()


def sample_value(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    return None


CHILD = """
    import time
    from Backend.app.observability import metrics
    metrics.HTTP_REQUESTS.labels("/api/x", "GET", "2xx").inc({n})
    metrics.HTTP_IN_FLIGHT.inc()
    import os
    print("ready", os.getpid(), flush=True)
    time.sleep(60)
"""
SERIES = 'http_requests_total{method="GET",route="/api/x",status_class="2xx"}'


def stop(child, pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    if child.poll() is None:
        child.kill()
    child.wait()


def test_two_processes_sum_in_exporter(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    env = {**os.environ, "PYTHONPATH": ROOT, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}
    children = [subprocess.Popen([sys.executable, "-c", textwrap.dedent(CHILD.format(n=n))], env=env, cwd=ROOT,
                                 stdout=subprocess.PIPE, text=True) for n in (3, 4)]
    try:
        pids = []
        for child in children:
            ready, pid = child.stdout.readline().split()
            assert ready == "ready"
            pids.append(int(pid))  # not child.pid: on Windows the venv launcher is a separate process from the interpreter
        text = scrape(tmp_path)
        assert sample_value(text, SERIES) == 7.0
        assert sample_value(text, "http_requests_in_flight") == 2.0
        # a worker killed mid-request: its in-flight contribution goes once the master marks it dead; its counts stay
        from prometheus_client import multiprocess
        stop(children[0], pids[0])
        multiprocess.mark_process_dead(pids[0])
        text = scrape(tmp_path)
        assert sample_value(text, "http_requests_in_flight") == 1.0
        assert sample_value(text, SERIES) == 7.0
        stop(children[1], pids[1])
        multiprocess.mark_process_dead(pids[1])
        assert sample_value(scrape(tmp_path), "http_requests_in_flight") in (0.0, None)
    finally:
        for child, pid in zip(children, pids):
            stop(child, pid)
            child.stdout.close()


def test_exporter_answers_404_elsewhere_and_500_when_unconfigured(monkeypatch):
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(exporter.app({"type": "http", "method": "GET", "path": "/other"}, None, send))
    assert sent[0]["status"] == 404
    sent.clear()
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    asyncio.run(exporter.app({"type": "http", "method": "GET", "path": "/metrics"}, None, send))
    assert sent[0]["status"] == 500


def test_no_query_or_id_in_any_label(tmp_path):
    client = build_app()
    image_id = str(uuid.uuid4())
    client.get("/api/images", params={"q": "SECRET-TEXT-42"})
    client.get(f"/api/images/{image_id}")
    client.get("/api/images/by-secret-SECRET-TEXT-42")
    labels = " ".join(str(s.labels) for family in (metrics.HTTP_REQUESTS.collect() + metrics.HTTP_DURATION.collect())
                      for s in family.samples)
    assert "SECRET-TEXT-42" not in labels and image_id not in labels
    # and in the multiprocess exporter output of a real child process serving the same requests
    result = run_python(f"""
        from unittest.mock import AsyncMock
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from Storage.db import get_async_db
        from Backend.app.api.images import get_image_service, router
        from Backend.app.observability.middleware import MetricsMiddleware
        service = AsyncMock()
        service.search.return_value = {{"items": [], "hasNext": False, "facets": []}}
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_image_service] = lambda: service
        app.dependency_overrides[get_async_db] = lambda: MagicMock()
        app.add_middleware(MetricsMiddleware)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/api/images", params={{"q": "SECRET-TEXT-42"}})
        client.get("/api/images/{image_id}")
    """, {"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)})
    assert result.returncode == 0, result.stderr[-2000:]
    text = scrape(tmp_path)
    assert "http_requests_total" in text
    assert "SECRET-TEXT-42" not in text and image_id not in text
