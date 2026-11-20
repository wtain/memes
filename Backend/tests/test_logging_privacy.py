"""Request id, slow-request line, privacy of logs and metrics, and the O1 audit gaps (docs/superpowers/reviews/2026-11-19-logging-audit.md).

One test per gap, named after the report row. No database."""
import contextlib
import importlib.util
import logging
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

import Backend.app.main as main
import config.stage
from Backend.app import error_handlers
from Backend.app.api import bug_reports as bug_reports_module
from Backend.app.api import images as images_module
from Backend.app.api import meme_from_text
from Backend.app.api.images import get_image_service, router as images_router
from Backend.app.observability import logfiles
from Backend.app.observability.middleware import MetricsMiddleware, RequestIdMiddleware, SlowRequestMiddleware
from Backend.app.services import abuse, comics_pipeline_trigger, image_store, inbox_upload_service, ingestion_metrics, library_metrics, recut_files
from Backend.app.stage_policy import make_router
from Storage.db import get_async_db
from repository import popularity

SECRET = "SECRET-NEEDLE-9137"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def quiet(caplog):
    caplog.set_level(logging.INFO)
    httpx_logger = logging.getLogger("httpx")  # the test client itself logs the URL it requests
    httpx_level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    logfiles.reset_sampling()
    yield
    httpx_logger.setLevel(httpx_level)
    logfiles.reset_sampling()


def things_app(threshold_ms=False, with_request_id=True):  # threshold_ms False: no slow middleware; None: threshold from settings
    router = make_router(prefix="/things", tags=["things"])

    @router.get("/{thing_id}")
    async def thing(thing_id: str):
        return {"id": thing_id, "seen": logfiles.request_id_var.get()}

    @router.get("")
    async def boom(q: str = ""):
        raise RuntimeError(f"statement parameters: {q}")

    app = FastAPI()
    app.include_router(router, prefix="/api")
    if threshold_ms is not False:
        app.add_middleware(SlowRequestMiddleware, threshold_ms=threshold_ms)
    if with_request_id:
        app.add_middleware(RequestIdMiddleware)
    return app


# --- request id ---------------------------------------------------------------------------------------------------------------------

def test_request_id_is_server_generated_and_returned():
    client = TestClient(things_app())
    seen = set()
    for _ in range(3):
        response = client.get("/api/things/a", headers={"X-Request-ID": "client-chosen-id"})
        rid = response.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", rid) and rid != "client-chosen-id"
        assert response.json()["seen"] == rid  # the log context of the handler carries the same id
        seen.add(rid)
    assert len(seen) == 3
    assert logfiles.request_id_var.get() == "-"  # reset after the request


def test_request_id_filter_formats_the_id_and_a_dash_outside_a_request():
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "m", (), None)
    logfiles.REQUEST_ID_FILTER.filter(record)
    assert record.request_id == "-"
    token = logfiles.request_id_var.set("feedface")
    try:
        logfiles.REQUEST_ID_FILTER.filter(record)
    finally:
        logfiles.request_id_var.reset(token)
    assert record.request_id == "feedface"


def test_request_id_middleware_is_outermost_in_the_app():
    names = [m.cls.__name__ for m in main.app.user_middleware]
    assert names[0] == "RequestIdMiddleware" and names[1] == "SlowRequestMiddleware"
    assert "CORSMiddleware" in names and names.index("CORSMiddleware") > 1


# --- slow requests ------------------------------------------------------------------------------------------------------------------

def test_slow_request_line_has_template_not_query(caplog):
    client = TestClient(things_app(threshold_ms=0.0001))
    response = client.get(f"/api/things/{SECRET}?q={SECRET}")
    rid = response.headers["x-request-id"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("slow request")]
    assert len(lines) == 1
    assert lines[0].startswith("slow request route=/api/things/{thing_id} status=200 duration_ms=")
    assert lines[0].endswith(f"request_id={rid}")
    assert SECRET not in caplog.text


def test_slow_request_is_off_when_the_threshold_is_zero_or_not_reached(caplog):
    TestClient(things_app(threshold_ms=0)).get("/api/things/a")
    TestClient(things_app(threshold_ms=60_000)).get("/api/things/a")
    assert not [r for r in caplog.records if r.getMessage().startswith("slow request")]


def test_slow_request_threshold_comes_from_settings(monkeypatch, caplog):
    from Backend.app.observability import middleware
    monkeypatch.setattr(middleware, "slow_request_ms", lambda: 0.0001)
    TestClient(things_app(threshold_ms=None)).get("/api/things/a")
    assert [r for r in caplog.records if r.getMessage().startswith("slow request")]


# --- the search text never reaches logs, log files or metrics -----------------------------------------------------------------------

def test_distinctive_search_text_absent_from_logs_and_metrics(tmp_path, caplog):
    service = AsyncMock()
    service.search.return_value = {"items": [], "hasNext": False, "facets": []}
    meme_service = AsyncMock()
    router = make_router(prefix="/things", tags=["things"])

    @router.get("")
    async def boom(q: str = ""):
        raise RuntimeError(f"(sqlalchemy.exc.DBAPIError) parameters: ('{q}',)")

    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.include_router(meme_from_text.router, prefix="/api")
    app.include_router(router, prefix="/api")
    app.add_exception_handler(RequestValidationError, error_handlers.validation_error_handler)
    app.dependency_overrides[get_image_service] = lambda: service
    app.dependency_overrides[get_async_db] = lambda: MagicMock()
    app.dependency_overrides[meme_from_text.get_meme_from_text_service] = lambda: meme_service
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SlowRequestMiddleware, threshold_ms=0.0001)  # every request also writes the slow line
    app.add_middleware(RequestIdMiddleware)
    client = TestClient(app, raise_server_exceptions=False)

    root = logging.getLogger()
    handlers = logfiles.install(str(tmp_path), "w0", 1_000_000, 2)
    assert handlers
    handlers[0].setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root.addHandler(handlers[0])
    try:
        ok = client.get(f"/api/images?q={SECRET}")
        bad = client.post("/api/meme-from-text/search", json={"text": [SECRET]})
        missing = client.get(f"/api/{SECRET}/nowhere?q={SECRET}")
        broken = client.get(f"/api/things?q={SECRET}")
    finally:
        root.removeHandler(handlers[0])
        handlers[0].close()
    assert (ok.status_code, bad.status_code, missing.status_code, broken.status_code) == (200, 422, 404, 500)
    assert broken.json() == {"detail": "Internal Server Error"} and "x-request-id" in broken.headers
    file_text = (tmp_path / "backend-w0.log").read_text(encoding="utf-8")
    metrics_text = generate_latest().decode()
    messages = " ".join(r.getMessage() + (r.exc_text or "") + str(r.exc_info) for r in caplog.records)
    for place, text in (("caplog", caplog.text), ("messages", messages), ("file", file_text), ("metrics", metrics_text),
                        ("422 body", bad.text)):
        assert SECRET not in text, place
    assert "unhandled exception type=RuntimeError route=/api/things" in file_text  # the failure itself is logged, by type
    assert "slow request route=" in file_text


# --- audit gaps (row names of the O1 report) -----------------------------------------------------------------------------------------

def test_gap_error_handlers_and_main_unhandled_exception_logs_type_route_request_id(caplog):
    client = TestClient(things_app(), raise_server_exceptions=False)
    response = client.get(f"/api/things?q={SECRET}")
    rid = response.headers["x-request-id"]
    assert response.status_code == 500
    lines = [r for r in caplog.records if r.getMessage().startswith("unhandled exception")]
    assert len(lines) == 1 and lines[0].exc_info is None
    assert lines[0].getMessage() == f"unhandled exception type=RuntimeError route=/api/things request_id={rid}"
    assert SECRET not in caplog.text


async def _run_raw(app, send):
    scope = {"type": "http", "method": "GET", "path": "/api/things", "raw_path": b"/api/things", "query_string": b"",
             "headers": [], "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1), "root_path": "",
             "http_version": "1.1"}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await RequestIdMiddleware(app)(scope, receive, send)


async def test_gap_unhandled_exception_with_a_failing_send_logs_no_message(caplog):
    async def app(_scope, _receive, _send):
        raise RuntimeError(f"statement parameters: {SECRET}")

    async def gone(_message):
        raise OSError(f"client gone {SECRET}")

    await _run_raw(app, gone)  # must not raise: an escaping send error would chain the original, whose message the server logs
    assert any(r.getMessage().startswith("unhandled exception type=RuntimeError") for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert SECRET not in caplog.text


async def test_gap_client_disconnect_is_an_info_non_event(caplog):
    from starlette.requests import ClientDisconnect

    async def app(_scope, _receive, _send):
        raise ClientDisconnect(f"while reading {SECRET}")

    sent = []

    async def send(message):
        sent.append(message)

    await _run_raw(app, send)
    assert sent == []  # nobody is left to answer: no response is attempted
    records = [r for r in caplog.records if r.name == "Backend.app.observability.requests"]
    assert [(r.levelno, r.getMessage().split(" request_id=")[0]) for r in records] == [
        (logging.INFO, "client disconnected type=ClientDisconnect")]
    assert SECRET not in caplog.text


async def test_gap_library_metrics_and_ingestion_metrics_log_the_type_only(caplog):
    @contextlib.asynccontextmanager
    async def nested():
        yield

    session = MagicMock()
    session.begin_nested = nested

    async def failing():
        raise RuntimeError(f"INSERT ... parameters {SECRET}")

    assert await library_metrics._swallowed(session, "recut", failing) is None
    assert await ingestion_metrics._swallowed(session, "tier", failing) is None
    messages = [r.getMessage() for r in caplog.records]
    assert "library metrics failed (recut): RuntimeError" in messages
    assert "ingestion metrics failed (tier): RuntimeError" in messages
    assert SECRET not in caplog.text


def test_gap_images_get_image_404_bounds_and_escapes_the_client_id(caplog, monkeypatch):
    repo = MagicMock()
    repo.get_filename = AsyncMock(return_value=None)
    monkeypatch.setattr(images_module, "ImageRepository", lambda _db: repo)
    app = FastAPI()
    app.include_router(images_router, prefix="/api")
    app.dependency_overrides[get_async_db] = lambda: MagicMock()
    response = TestClient(app).get("/api/images/" + ("x%0Ay" * 200))
    assert response.status_code == 404
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("get_image 404")]
    assert lines == ["get_image 404: image not in DB"]  # the id is client path text: never logged
    # a file that is missing on disk: the database filename is not logged at all
    repo.get_filename = AsyncMock(return_value=f"{SECRET}.jpg")
    TestClient(app).get("/api/images/abc-id-needle")
    assert "get_image 404: image file not on disk" in caplog.text
    assert SECRET not in caplog.text and "abc-id-needle" not in caplog.text


def test_gap_image_store_refused_name_is_bounded_and_typed(caplog):
    assert image_store.image_exists("../" + "y" * 500 + "\nforged line " + SECRET) is False
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("image file name refused")]
    assert len(lines) == 1 and "forged" not in caplog.text and SECRET not in caplog.text


def test_gap_inbox_upload_service_write_error_logs_type_not_name_or_traceback(caplog, tmp_path):
    candidate = MagicMock()
    candidate.data = b"x"
    missing_staging = tmp_path / "no-such-staging"
    with pytest.raises(inbox_upload_service.InboxWriteError):
        inbox_upload_service._write_one(tmp_path, missing_staging, candidate, "d" * 64, "name\nforged " + SECRET + ".jpg")
    records = [r for r in caplog.records if "Could not write an upload" in r.getMessage()]
    assert len(records) == 1 and records[0].exc_info is None
    assert records[0].getMessage() == "Could not write an upload into the ingestion inbox (FileNotFoundError)"  # no client name
    assert "forged" not in caplog.text and SECRET not in caplog.text


async def test_gap_repository_popularity_guarded_logs_the_type_only(caplog):
    @contextlib.asynccontextmanager
    async def nested():
        yield

    session = MagicMock()
    session.begin_nested = nested

    async def failing():
        raise RuntimeError(f"INSERT INTO popularity_absorptions ... parameters {SECRET}")

    assert await popularity.guarded(session, "ingest_reject", failing) is None
    records = [r for r in caplog.records if r.getMessage().startswith("popularity recording failed")]
    assert [r.getMessage() for r in records] == ["popularity recording failed (ingest_reject): RuntimeError"]
    assert records[0].exc_info is None and SECRET not in caplog.text


async def test_gap_comics_pipeline_trigger_logs_the_type_only(caplog, monkeypatch):
    def boom():
        raise RuntimeError(f"dsn host {SECRET}")

    monkeypatch.setattr(comics_pipeline_trigger, "AsyncSessionLocal", boom)
    assert await comics_pipeline_trigger.start_comics_pipeline() is None
    assert any(r.getMessage().endswith("(RuntimeError)") and r.exc_info is None for r in caplog.records)
    assert SECRET not in caplog.text


def test_gap_recut_files_logs_the_type_only(caplog, monkeypatch):
    def failing(_path):
        raise PermissionError(f"cannot remove {SECRET}")

    monkeypatch.setattr(recut_files.os, "remove", failing)
    recut_files.remove_quietly("/data/images/a.jpg")
    records = [r for r in caplog.records if "could not remove" in r.getMessage()]
    assert len(records) == 1 and records[0].exc_info is None and SECRET not in caplog.text


async def test_gap_main_health_logs_the_type_not_the_message(caplog):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError(f"password authentication failed {SECRET}"))
    with pytest.raises(HTTPException) as caught:
        await main.health_check(db)
    assert caught.value.status_code == 503
    assert any(r.getMessage() == "health check: database unavailable type=RuntimeError" for r in caplog.records)
    assert SECRET not in caplog.text


def test_gap_middleware_and_abuse_rejections_are_sampled_by_scope(caplog):
    for _ in range(50):
        abuse.count_refusal("saves")
    abuse.count_refusal("body_limit")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request refused")]
    assert sorted(lines) == ["request refused by limit scope=body_limit", "request refused by limit scope=saves"]


def test_gap_stage_policy_denial_is_sampled_and_logs_the_template_only(monkeypatch, caplog):
    monkeypatch.setattr(config.stage, "STAGE", "prod")
    router = make_router(prefix="/images", tags=["images"])

    @router.get("/flagged")
    async def flagged():
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    for _ in range(5):
        assert client.get("/api/images/flagged?q=" + SECRET).status_code == 404
        assert client.post("/api/images/flagged").status_code == 404
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stage policy denied")]
    assert lines == ["stage policy denied route=/api/images/flagged"]  # both paths share the template, sampled to one line
    assert SECRET not in caplog.text


def test_gap_bug_reports_receipt_logs_outcome_and_size_only(monkeypatch, caplog):
    monkeypatch.setattr(bug_reports_module, "enforce", lambda *_a: None)
    monkeypatch.setattr(bug_reports_module, "bug_report_dir_caps", lambda: (100, 10**9))
    monkeypatch.setattr(bug_reports_module, "bug_report_dir_has_room", lambda *_a: True)
    monkeypatch.setattr(bug_reports_module, "save_bug_report", lambda *_a: None)
    app = FastAPI()
    app.include_router(bug_reports_module.router, prefix="/api")
    client = TestClient(app)
    assert client.post("/api/bug-reports", files={"file": (f"{SECRET}.txt", b"hello " + SECRET.encode())}).status_code == 200
    assert client.post("/api/bug-reports", files={"file": (f"{SECRET}.exe", b"zz")}).status_code == 422
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("bug report received")]
    assert lines == ["bug report received outcome=saved size_bytes=%d" % (6 + len(SECRET)),
                     "bug report received outcome=bad_extension size_bytes=0"]
    assert SECRET not in caplog.text


async def test_gap_main_startup_and_shutdown_lines(monkeypatch, caplog):
    async def nothing(*_a):
        return []

    monkeypatch.setattr(main, "_configure_logging", lambda: None)
    monkeypatch.setattr(main, "get_query_parser", lambda: None)
    monkeypatch.setattr(main, "get_pipeline", lambda: None)
    monkeypatch.setattr(main, "start_scheduler", nothing)
    monkeypatch.setattr(main, "stop_scheduler", nothing)
    async with main.lifespan(main.app):
        pass
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("backend starting stage=dev pid=") and "slow_request_ms=" in m for m in messages)
    assert any(m.startswith("backend stopping pid=") for m in messages)


def test_gap_gunicorn_conf_assigns_the_lowest_free_slot_and_sets_the_env(monkeypatch):
    spec = importlib.util.spec_from_file_location("memes_gunicorn_conf", ROOT / "deploy" / "prod" / "gunicorn.conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    class Worker:
        pass

    server = MagicMock()
    server.num_workers = 2
    server.WORKERS = {}
    first, second, third = Worker(), Worker(), Worker()
    conf.pre_fork(server, first)
    server.WORKERS[101] = first
    conf.pre_fork(server, second)
    server.WORKERS[102] = second
    assert (first.memes_slot, second.memes_slot) == ("w0", "w1")
    del server.WORKERS[101]  # worker 0 died: its replacement takes the free slot, so the files stay at two
    conf.pre_fork(server, third)
    assert third.memes_slot == "w0"
    monkeypatch.delenv("MEMES_LOG_SLOT", raising=False)
    conf.post_fork(server, third)
    assert os.environ["MEMES_LOG_SLOT"] == "w0"
    monkeypatch.delenv("MEMES_LOG_SLOT", raising=False)
