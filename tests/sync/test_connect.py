"""sync/connect.py and sync/doctor.py without a database: mock connections, a mock capabilities server, pure role evaluation."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import psycopg2
import pytest

from sync import connect, doctor, registry
from sync.config import ConfigError, PreconditionError, SyncSettings
from sync.connect import (DEV_RO, DEV_RW, PROD_READER, PROD_WRITER, PROD_WRITER_RO, DevUrls, SyncConnection)
from sync.model import Klass
from sync.report import Document

ENV = "general"
SHA = "a" * 40
HEAD = "rev123"


def make_settings(**over):
    data = {"dev": {"database": "devdb"},
            "prod": {"database": "memes_general", "writer_role": "memes_general_sync_w", "reader_role": "memes_general_sync_r",
                     "local_port": 16434, "api_url": "http://127.0.0.1:1"}}
    data.update(over)
    return SyncSettings(ENV, data)


class FakeConn(SyncConnection):
    """A connection answering the handshake queries from a state dict."""

    def __init__(self, kind, host=None, port=None, app_env=None, **state):
        super().__init__(None, kind, host, port, app_env)
        self.state = {"db": "x", "settings": {}, "identity": ("1.1.1.1", 5432, "t0", 1), "system_id": "sys1", "alembic": ["rev1"],
                      "image_ids": [], "dev_overlap": 0, **state}
        self.log = []

    def query(self, sql, args=None):
        s = self.state
        self.log.append(sql)
        if sql.startswith("select current_database()"):
            return [(s["db"],)]
        if "current_setting" in sql:
            return [(s["settings"].get(args[0]),)]
        if "inet_server_addr" in sql:
            return [s["identity"]]
        if "pg_control_system" in sql:
            if s["system_id"] == "denied":
                raise connect.DbError("42501")
            return [(s["system_id"],)]
        if "alembic_version" in sql:
            return [(v,) for v in s["alembic"]]
        if "select id::text from images" in sql:
            return [(i,) for i in s["image_ids"]]
        if "count(*) from images" in sql:
            return [(s["dev_overlap"],)]
        raise AssertionError(sql)


def good_pair(**prod_state):
    dev = FakeConn(DEV_RO, app_env=ENV, db="devdb", dev_overlap=2)
    state = dict(db="memes_general", identity=("10.0.0.2", 5432, "t1", 7), system_id="sys2",
                 settings={"memes.environment": ENV, "memes.state": "serving", "memes.revision": SHA},
                 image_ids=["i1", "i2", "i3"])
    state.update(prod_state)
    prod = FakeConn(PROD_WRITER, connect.LOOPBACK, 16434, **state)
    return dev, prod


def good_facts(role):
    d, p, x = (registry.names(k) for k in (Klass.D, Klass.P, Klass.X))
    flags = dict.fromkeys(("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"), False)
    tp = {t: {"exists": True, **flags} for t in d + p + x + ["alembic_version"]}
    tp["alembic_version"]["SELECT"] = True
    if role == "writer":
        for t in d:
            tp[t].update(SELECT=True, INSERT=True, UPDATE=True, DELETE=True)
        cols = {"meme_text_ratings": {"image_id": True, "query_hash": True, "updated_at": True, "query_text": False}, "images": {"id": False}}
    else:
        for t in p:
            tp[t]["SELECT"] = True
        cols = {"meme_text_ratings": {"query_text": False}, "images": {"id": True, "filename": False}}
    return {"user": f"memes_general_sync_{'w' if role == 'writer' else 'r'}",
            "attrs": dict.fromkeys(connect._ATTRS, False), "member_of": [], "owns_images": False, "table_priv": tp, "col_priv": cols}


@pytest.fixture
def facts(monkeypatch):
    def fake(conn, env):
        return good_facts("reader" if conn.kind == PROD_READER else "writer")

    monkeypatch.setattr(connect, "gather_role_facts", fake)


def caps_ok(_url):
    return {"stage": "prod", "environment": ENV}


def run(tool="P", dev=None, prod=None, mode="normal", **kw):
    d, p = good_pair()
    kw.setdefault("capabilities", caps_ok)
    kw.setdefault("git_sha", SHA)
    return connect.handshake(ENV, tool, mode, settings=make_settings(), dev=dev or d, prod=prod or p, **kw)


# ----------------------------------------------------------------------------------------------------- errors

def test_db_error_keeps_only_sqlstate_constraint_and_table():
    exc = psycopg2.errors.UniqueViolation("duplicate key value violates unique constraint (secret-row-value)")
    exc_diag = SimpleNamespace(constraint_name="uq_x", table_name="tags")
    err = connect.describe_db_error(SimpleNamespace(pgcode="23505", diag=exc_diag))
    assert str(err).endswith("sqlstate=23505 constraint=uq_x table=tags")
    assert "secret" not in str(connect.describe_db_error(exc))


def test_guard_db_does_not_chain_the_original_message():
    with pytest.raises(connect.DbError) as info:
        with connect.guard_db():
            raise psycopg2.OperationalError("password=hunter2 failed")
    assert "hunter2" not in str(info.value) and info.value.__suppress_context__


# ---------------------------------------------------------------------------------------------- factories (H1 kind)

class FakeRaw:
    def __init__(self):
        self.autocommit, self.executed = False, []

    def cursor(self):
        raw = self

        class C:
            description = None

            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, args=None): raw.executed.append(sql)

        return C()

    def close(self): pass


def test_dev_ro_refuses_the_write_capable_url_and_sets_read_only():
    calls = []
    raw_conn = FakeRaw()
    ok = connect.dev_ro(ENV, dev_urls=DevUrls(ENV, "postgresql+asyncpg://a/rw", "postgresql://a/ro"),
                        connector=lambda url: (calls.append(url), raw_conn)[1])
    assert calls == ["postgresql://a/ro"] and raw_conn.autocommit and "SET default_transaction_read_only = on" in raw_conn.executed
    assert ok.kind == DEV_RO and ok.app_env == ENV
    with pytest.raises(PreconditionError, match="dev_url_kind_wrong"):
        connect.dev_ro(ENV, dev_urls=DevUrls(ENV, "postgresql://a/x", "postgresql://a/x"), connector=lambda u: FakeRaw())


def test_dev_rw_refuses_the_read_only_url_and_never_uses_it():
    seen = []
    conn = connect.dev_rw(ENV, dev_urls=DevUrls(ENV, "postgresql+asyncpg://a/rw", "postgresql://a/ro"),
                          connector=lambda url: (seen.append(url), FakeRaw())[1])
    assert seen == ["postgresql://a/rw"] and conn.kind == DEV_RW
    with pytest.raises(PreconditionError, match="dev_url_kind_wrong"):
        connect.dev_rw(ENV, dev_urls=DevUrls(ENV, "postgresql://a/same", "postgresql://a/same"), connector=lambda u: FakeRaw())
    with pytest.raises(PreconditionError, match="dev_rw_url_missing"):
        connect.dev_rw(ENV, dev_urls=DevUrls(ENV, None, "postgresql://a/ro"), connector=lambda u: FakeRaw())


def test_prod_factories_use_loopback_port_role_and_password_from_the_dict():
    seen = {}
    secrets = {"SYNC_WRITER_PASSWORD": "w-pass", "SYNC_READER_PASSWORD": "r-pass"}

    def connector(**kw):
        seen[kw["user"]] = kw
        return FakeRaw()

    s = make_settings()
    w = connect.prod_writer(s, secrets, connector=connector)
    r = connect.prod_reader(s, secrets, connector=connector)
    ro = connect.prod_writer_readonly(s, secrets, connector=connector)
    assert (w.kind, r.kind, ro.kind) == (PROD_WRITER, PROD_READER, PROD_WRITER_RO)
    assert seen["memes_general_sync_w"]["host"] == "127.0.0.1" and seen["memes_general_sync_w"]["port"] == 16434
    assert seen["memes_general_sync_w"]["password"] == "w-pass" and seen["memes_general_sync_r"]["password"] == "r-pass"
    assert seen["memes_general_sync_w"]["dbname"] == "memes_general"
    with pytest.raises(ConfigError):
        connect.prod_writer(s, {"SYNC_READER_PASSWORD": "x"}, connector=connector)


# ---------------------------------------------------------------------------------------------- capabilities (H5)

@pytest.fixture
def caps_server():
    box = {"body": json.dumps({"stage": "prod", "environment": ENV, "features": {}}), "status": 200}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            box["path"] = self.path
            self.send_response(box["status"])
            self.end_headers()
            self.wfile.write(box["body"].encode())

        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    box["url"] = f"http://127.0.0.1:{srv.server_port}"
    yield box
    srv.shutdown()


def test_capabilities_client_reads_the_public_endpoint(caps_server):
    data = connect.fetch_capabilities(caps_server["url"])
    assert data["environment"] == ENV and caps_server["path"] == "/api/diagnostics/capabilities"


@pytest.mark.parametrize("body,status,code", [("nope", 200, "not_json"), ("[]", 200, "payload_shape"), ("{}", 404, "http_404"),
                                              ('{"stage": 1, "environment": "x"}', 200, "payload_shape")])
def test_capabilities_client_failure_codes(caps_server, body, status, code):
    caps_server.update(body=body, status=status)
    with pytest.raises(connect.CapabilitiesError) as info:
        connect.fetch_capabilities(caps_server["url"])
    assert info.value.code == code


def test_capabilities_client_refuses_plain_http_off_loopback_and_unreachable():
    with pytest.raises(connect.CapabilitiesError) as info:
        connect.fetch_capabilities("http://example.com")
    assert info.value.code == "api_url_not_https"
    with pytest.raises(connect.CapabilitiesError) as info:
        connect.fetch_capabilities("http://127.0.0.1:1", timeout=1)
    assert info.value.code == "unreachable"


# ------------------------------------------------------------------------------------------------ role evaluation

def test_good_roles_have_no_problems():
    assert connect.evaluate_role(good_facts("writer"), "writer", "memes_general_sync_w") == []
    assert connect.evaluate_role(good_facts("reader"), "reader", "memes_general_sync_r") == []


def test_writer_problems_are_found():
    f = good_facts("writer")
    f["user"] = "someone"
    f["attrs"]["rolsuper"] = f["attrs"]["rolbypassrls"] = True
    f["member_of"] = ["memes_general_app"]
    f["owns_images"] = True
    f["table_priv"]["meme_saves"]["INSERT"] = True
    f["table_priv"]["tmp_clusters"]["DELETE"] = True
    f["table_priv"]["meme_text_ratings"]["SELECT"] = True
    f["table_priv"]["images"]["DELETE"] = False
    f["col_priv"]["meme_text_ratings"]["query_text"] = True
    got = connect.evaluate_role(f, "writer", "memes_general_sync_w")
    assert {"wrong_current_user", "role_attr_super", "role_attr_bypassrls", "member_of_app_role", "member_of_table_owner",
            "write_on_class_p_or_x=2", "select_on_class_p_or_x=1", "missing_write_on_class_d=1", "ratings_column_select_wrong"} == set(got)


def test_writer_without_the_ratings_column_grant_fails():
    f = good_facts("writer")
    f["col_priv"]["meme_text_ratings"]["updated_at"] = False
    assert "ratings_column_select_wrong" in connect.evaluate_role(f, "writer", "memes_general_sync_w")


def test_reader_problems_are_found():
    f = good_facts("reader")
    f["table_priv"]["images"]["SELECT"] = True
    f["table_priv"]["meme_saves"]["INSERT"] = True
    f["table_priv"]["meme_impressions"]["SELECT"] = False
    f["col_priv"]["images"]["filename"] = True
    f["table_priv"]["alembic_version"]["SELECT"] = False
    got = connect.evaluate_role(f, "reader", "memes_general_sync_r")
    assert {"select_on_class_d_or_x=1", "write_privilege=1", "missing_select_on_class_p=1", "images_column_select_wrong",
            "alembic_version_unreadable"} == set(got)


# ------------------------------------------------------------------------------------------------------- handshake

def test_full_handshake_for_promote_passes_all_applicable_steps(facts):
    results = run("P")
    assert [r["step"] for r in results] == ["H1", "H2", "H3", "H4", "H5", "H7", "H8", "H9", "H10"]  # no H6 for P
    assert all(r["ok"] for r in results)
    Document(env=ENV, run_id="20261112T101010Z-0a1b2c3d", tool="sync.promote", handshake=results)  # accepted by the report module


@pytest.mark.parametrize("tool,steps", [
    ("F", ["H1", "H6"]),
    ("L", ["H1", "H2", "H3", "H4", "H5", "H6"]),
    ("V", ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H10"]),
    ("R", ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]),
    ("D", ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]),
])
def test_steps_per_tool(facts, tool, steps):
    d, p = good_pair()
    kinds = {"L": (DEV_RW, PROD_READER), "V": (DEV_RO, PROD_WRITER_RO)}.get(tool)
    if kinds:
        d.kind, p.kind = kinds
    reader = FakeConn(PROD_READER, connect.LOOPBACK, 16434)
    transport = SimpleNamespace(handshake=lambda: {"environment": ENV})
    results = connect.handshake(ENV, tool, settings=make_settings(), dev=d, prod=None if tool == "F" else p,
                                prod_reader_conn=reader, capabilities=caps_ok, transport=transport, git_sha=SHA)
    assert [r["step"] for r in results] == steps and all(r["ok"] for r in results)


def fails(step, **kw):
    with pytest.raises(PreconditionError) as info:
        run(**kw)
    err = info.value
    assert isinstance(err, connect.HandshakeError) and err.exit_code == 2 and f"handshake {step} failed" in str(err)
    assert err.results[-1]["step"] == step and not err.results[-1]["ok"]
    return err.results[-1]["detail"]


def test_h1_failures(facts):
    d, p = good_pair()
    d.app_env = "metal"
    assert fails("H1", dev=d) == "app_env_mismatch"
    d, _ = good_pair()
    d.kind = DEV_RW
    assert fails("H1", dev=d) == "dev_url_kind_wrong"
    d, _ = good_pair()
    d.state["db"] = "other"
    assert fails("H1", dev=d) == "dev_db_mismatch"
    d, _ = good_pair()
    d.state["settings"]["memes.environment"] = "general"
    assert fails("H1", dev=d) == "dev_has_memes_environment"


def test_h1_dev_database_unset_is_a_precondition_failure(facts):
    d, p = good_pair()
    results = connect.handshake(ENV, "P", settings=make_settings(dev={"database": ""}), dev=d, prod=p, capabilities=caps_ok,
                                git_sha=SHA, fail_fast=False)
    assert results[0] == {"step": "H1", "ok": False, "detail": "config_missing"}


def test_h2_requires_the_tunnel_port_and_database(facts):
    _, p = good_pair()
    p.port = 5432
    assert fails("H2", prod=p) == "not_via_tunnel"
    _, p = good_pair()
    p.state["db"] = "memes_it"
    assert fails("H2", prod=p) == "prod_db_mismatch"


def test_h3_wrong_connection_kind_and_role_problems(monkeypatch, facts):
    _, p = good_pair()
    p.kind = PROD_READER
    assert "prod_connection_kind_wrong" in fails("H3", prod=p)
    monkeypatch.setattr(connect, "gather_role_facts", lambda c, e: {**good_facts("writer"), "attrs": {**dict.fromkeys(connect._ATTRS, False), "rolsuper": True}})
    assert fails("H3") == "role_attr_super"


def test_h4_h5_failures(facts):
    _, p = good_pair()
    p.state["settings"]["memes.environment"] = "metal"
    assert fails("H4", prod=p) == "prod_environment_mismatch"
    assert fails("H5", capabilities=lambda u: {"stage": "dev", "environment": ENV}) == "stage_not_prod"
    assert fails("H5", capabilities=lambda u: {"stage": "prod", "environment": "it"}) == "environment_mismatch"

    def boom(url):
        raise connect.CapabilitiesError("unreachable")

    assert fails("H5", capabilities=boom) == "capabilities:unreachable"


def test_h6_gate_through_the_transport_protocol(facts):
    d, p = good_pair()
    ok = connect.handshake(ENV, "L", settings=make_settings(), dev=_rw(d), prod=_reader(p),
                           capabilities=caps_ok, transport=SimpleNamespace(handshake=lambda: {"environment": ENV}), git_sha=SHA)
    assert ok[-1] == {"step": "H6", "ok": True, "detail": "ok"}
    with pytest.raises(PreconditionError, match="gate_environment_mismatch"):
        connect.handshake(ENV, "L", settings=make_settings(), dev=_rw(d), prod=_reader(p), capabilities=caps_ok,
                          transport=SimpleNamespace(handshake=lambda: {"environment": "it"}))
    with pytest.raises(PreconditionError, match="transport_missing"):
        connect.handshake(ENV, "L", settings=make_settings(), dev=_rw(d), prod=_reader(p), capabilities=caps_ok)


def _rw(d):
    d.kind = DEV_RW
    return d


def _reader(p):
    p.kind = PROD_READER
    return p


def test_h7_same_server(facts):
    d, p = good_pair()
    p.state["identity"], p.state["system_id"] = d.state["identity"], d.state["system_id"]
    assert fails("H7", dev=d, prod=p) == "same_server"
    # differing in just one fact is enough
    d, p = good_pair()
    p.state["identity"], p.state["system_id"] = d.state["identity"], d.state["system_id"]
    p.state["identity"] = (*d.state["identity"][:3], 99)
    assert connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA)


def test_h7_tolerates_a_denied_system_identifier(facts):
    d, p = good_pair()
    d.state["system_id"] = p.state["system_id"] = "denied"
    assert connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA)


def test_h8_overlap(facts):
    d, p = good_pair()
    d.state["dev_overlap"] = 1  # 1 of 3 prod ids exist in dev
    assert fails("H8", dev=d, prod=p).startswith("overlap_low=1/3")
    d.state["dev_overlap"] = 2  # 2 of 3 is more than half
    assert any(r["detail"] == "overlap=2/3" for r in connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p,
                                                                      capabilities=caps_ok, git_sha=SHA))
    d, p = good_pair(image_ids=[])
    assert [r for r in run(dev=d, prod=p) if r["step"] == "H8"][0]["detail"] == "prod_empty"
    d, p = good_pair()
    d.state["dev_overlap"] = 0
    assert fails("H8", dev=d, prod=p).startswith("overlap_low=0/3")  # another environment's data: overlap 0


def test_h9_state_per_mode(facts):
    _, p = good_pair()
    p.state["settings"]["memes.state"] = "seeding"
    assert fails("H9", prod=p) == "state_seeding_want_serving"
    assert run(prod=p, mode="first-seed")
    _, p2 = good_pair()
    assert fails("H9", prod=p2, mode="first-seed") == "state_serving_want_seeding"
    _, p3 = good_pair()
    p3.state["settings"].pop("memes.state")
    assert fails("H9", prod=p3) == "state_unset_want_serving"


def test_h10_release(facts):
    d, p = good_pair()
    p.state["alembic"] = ["rev2"]
    assert fails("H10", dev=d, prod=p) == "alembic_version_differs"
    d, p = good_pair()
    p.state["settings"]["memes.revision"] = "b" * 40
    assert fails("H10", dev=d, prod=p) == "revision_mismatch"
    d, p = good_pair()
    del p.state["settings"]["memes.revision"]
    assert fails("H10", dev=d, prod=p) == "revision_unset"
    d, p = good_pair()
    assert fails("H10", dev=d, prod=p, git_sha="") == "dev_git_sha_unknown"


def test_fail_fast_off_runs_every_step_and_reports_all_failures(facts):
    d, p = good_pair()
    p.state["settings"]["memes.environment"] = "metal"
    p.state["settings"]["memes.state"] = "seeding"
    results = connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA, fail_fast=False)
    assert [r["step"] for r in results if not r["ok"]] == ["H4", "H9"]


def test_database_error_inside_a_step_is_recorded_as_a_code(facts):
    d, p = good_pair()
    orig = p.query

    def query(sql, args=None):
        if "alembic_version" in sql:
            raise connect.DbError("42P01", table="alembic_version")
        return orig(sql, args)

    p.query = query
    assert fails("H10", dev=d, prod=p) == "db_error:42p01"
    results = connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA, fail_fast=False)
    assert [r for r in results if r["step"] == "H10"][0]["detail"].startswith("db_error:")


def test_details_are_codes_only(facts):
    d, p = good_pair()
    p.state["settings"]["memes.environment"] = "metal"
    results = connect.handshake(ENV, "P", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA, fail_fast=False)
    Document(env=ENV, run_id="20261112T101010Z-0a1b2c3d", tool="sync.doctor", handshake=results)
    import re
    assert all(re.fullmatch(r"[a-z0-9_.:=,/-]{0,80}", r["detail"]) for r in results)


# ------------------------------------------------------------------------------------------------------- doctor

def test_doctor_runs_all_steps_without_stopping_and_exits_by_result(facts, capsys):
    d, p = good_pair()
    reader = FakeConn(PROD_READER, connect.LOOPBACK, 16434)
    sessions = {"dev": d, "prod": p, "prod_reader_conn": reader}

    from contextlib import contextmanager

    @contextmanager
    def opener(env, tool, settings=None):
        assert tool == "D"
        yield sessions

    results, text = doctor.run_doctor(ENV, settings=make_settings(), session_opener=opener, capabilities=caps_ok, git_sha=SHA, lock=False)
    assert all(r["ok"] for r in results) and [r["step"] for r in results][5] == "H6" and results[5]["detail"] == "skipped:no_transport"
    assert text.startswith("dev general (devdb @ localhost, read-only) -> prod memes_general as memes_general_sync_w")


def test_doctor_main_exit_codes(monkeypatch, capsys):
    bad = [{"step": "H1", "ok": False, "detail": "dev_db_mismatch"}]
    monkeypatch.setattr(doctor, "run_doctor", lambda env: (bad, "banner"))
    assert doctor.main(["--env", ENV, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["handshake"] == bad
    monkeypatch.setattr(doctor, "run_doctor", lambda env: ([{"step": "H1", "ok": True, "detail": "ok"}], "banner"))
    assert doctor.main(["--env", ENV]) == 0
    monkeypatch.setattr(doctor, "run_doctor", lambda env: (_ for _ in ()).throw(ConfigError("settings missing")))
    assert doctor.main(["--env", ENV]) == 2
    assert "settings missing" in capsys.readouterr().err
