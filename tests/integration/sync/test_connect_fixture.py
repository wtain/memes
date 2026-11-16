"""sync/connect.py against the two-schema fixture: the dev schema plays dev, the prod schema plays prod.

A real prod cannot be reproduced inside one database of one server, so these tests prove the SQL of each step and the
failure paths, and these two things are NOT reachable here (mock-tested in tests/sync/test_connect.py instead):
  * H3 passing: the fixture user is a superuser, so the role/privilege checks must (and do) fail on it;
  * H7 passing: both schemas live in one database of one server, so "same server" must (and does) trip.
Database-level settings (memes.environment/state/revision) are set per session with set_config on the prod connection.
"""
import uuid
from contextlib import nullcontext

import pytest

from sync import connect, doctor
from sync.config import SyncSettings
from sync.connect import DEV_RO, DEV_RW, PROD_WRITER, DevUrls, SyncConnection

ENV = "general"
SHA = "c" * 40


def settings(root):
    return SyncSettings(ENV, {
        "state_dir": "state/{env}", "dev": {"database": "ocrdb_test"},
        "prod": {"database": "ocrdb_test", "writer_role": "ocr", "reader_role": "ocr", "local_port": 16434,
                 "api_url": "http://127.0.0.1:1", "ssh_alias": "alias"}}, root)


@pytest.fixture
def pair(sync_pair):
    sync_pair.reset()
    yield sync_pair
    sync_pair.reset()
    assert sync_pair.public_snapshot() == sync_pair.public_before  # the public schema is never touched


def dev_conn(p, kind=DEV_RO):
    raw = p.dev_conn()
    raw.autocommit = True
    return SyncConnection(raw, kind, None, None, ENV)


def prod_conn(p, state="serving", revision=SHA, environment=ENV):
    raw = p.prod_conn()
    raw.autocommit = True
    conn = SyncConnection(raw, PROD_WRITER, connect.LOOPBACK, 16434)
    for name, value in (("memes.environment", environment), ("memes.state", state), ("memes.revision", revision)):
        if value is not None:
            conn.query("select set_config(%s, %s, false)", (name, value))
    return conn


def add_images(conn, ids):
    for i in ids:
        conn.query("insert into images (id, filename) values (%s, %s)", (str(i), f"{i}.jpg"))


caps = lambda url: {"stage": "prod", "environment": ENV}  # noqa: E731


def run_all(p, tmp_path, dev, prod, **kw):
    return connect.handshake(ENV, "P", settings=settings(tmp_path), dev=dev, prod=prod, capabilities=caps, git_sha=SHA,
                             fail_fast=False, **kw)


def test_identity_and_catalog_queries_run_on_a_real_server(pair):
    for conn in (dev_conn(pair), prod_conn(pair)):
        identity = connect._identity(conn)
        assert len(identity) == 5 and identity[2] and identity[3]
        facts = connect.gather_role_facts(conn, ENV)
        assert all(v["exists"] for v in facts["table_priv"].values())  # 46 registry tables + alembic_version
        assert len(facts["table_priv"]) == 47
        assert set(facts["col_priv"]["meme_text_ratings"]) >= {"image_id", "query_hash", "updated_at", "query_text"}
        assert connect._alembic(conn) == (pair.alembic_head,)
        conn.close()


def test_a_superuser_fails_the_role_check_and_the_same_server_trips_h7(pair, tmp_path):
    dev, prod = dev_conn(pair), prod_conn(pair)
    facts = connect.gather_role_facts(prod, ENV)
    problems = connect.evaluate_role(facts, "writer", "ocr")
    assert "role_attr_super" in problems and any(p.startswith("write_on_class_p_or_x") for p in problems)
    results = {r["step"]: r for r in run_all(pair, tmp_path, dev, prod)}
    assert {s for s, r in results.items() if not r["ok"]} == {"H3", "H7"}
    assert results["H7"]["detail"] == "same_server"
    assert "role_attr_super" in results["H3"]["detail"]
    for step in ("H1", "H2", "H4", "H5", "H8", "H9", "H10"):
        assert results[step]["ok"], results[step]


def test_h1_dev_database_name_memes_environment_and_read_only_session(pair, tmp_path):
    dev = dev_conn(pair)
    dev.query("select set_config('memes.environment', 'general', false)")
    prod = prod_conn(pair)
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev, prod)}
    assert r["H1"]["detail"] == "dev_has_memes_environment"
    dev2 = dev_conn(pair)
    s = settings(tmp_path)
    s.data["dev"]["database"] = "something_else"
    r = connect.handshake(ENV, "P", settings=s, dev=dev2, prod=prod, capabilities=caps, git_sha=SHA, fail_fast=False)
    assert r[0]["detail"] == "dev_db_mismatch"


def test_dev_ro_session_is_read_only_and_errors_are_redacted(pair):
    ro = connect.dev_ro(ENV, dev_urls=DevUrls(ENV, "postgresql://x/rw", "postgresql://x/ro"), connector=lambda url, **kw: pair.dev_conn())
    with pytest.raises(connect.DbError) as info:
        ro.query("delete from batch_runs")
    assert info.value.sqlstate == "25006"
    ro.close()
    rw = dev_conn(pair, DEV_RW)
    secret = "needle-" + uuid.uuid4().hex
    image = uuid.uuid4()
    rw.query("insert into images (id, filename) values (%s, %s)", (str(image), secret))
    with pytest.raises(connect.DbError) as info:
        rw.query("insert into images (id, filename) values (%s, %s)", (str(image), secret))
    assert info.value.sqlstate == "23505" and info.value.constraint == "images_pkey" and info.value.table == "images"
    assert secret not in str(info.value) and str(image) not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_h8_id_overlap_against_real_tables(pair, tmp_path):
    ids = [uuid.uuid4() for _ in range(4)]
    dev, prod = dev_conn(pair, DEV_RW), prod_conn(pair)
    add_images(prod, ids)
    add_images(dev, ids[:2])  # exactly half is enough
    dev.kind = DEV_RO
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev, prod)}
    assert r["H8"] == {"step": "H8", "ok": True, "detail": "overlap=2/4"}
    dev2 = dev_conn(pair, DEV_RW)
    dev2.query("delete from images where id = %s", (str(ids[0]),))
    dev2.kind = DEV_RO
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev2, prod)}
    assert r["H8"]["ok"] is False and r["H8"]["detail"] == "overlap_low=1/4"


def test_h8_empty_prod_passes(pair, tmp_path):
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev_conn(pair), prod_conn(pair))}
    assert r["H8"]["detail"] == "prod_empty"


@pytest.mark.parametrize("kw,step,detail", [
    ({"state": "seeding"}, "H9", "state_seeding_want_serving"),
    ({"revision": "d" * 40}, "H10", "revision_mismatch"),
    ({"revision": None}, "H10", "revision_unset"),
    ({"environment": "metal"}, "H4", "prod_environment_mismatch"),
    ({"environment": None}, "H4", "prod_environment_mismatch"),
])
def test_database_settings_checks(pair, tmp_path, kw, step, detail):
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev_conn(pair), prod_conn(pair, **kw))}
    assert r[step]["ok"] is False and r[step]["detail"] == detail


def test_h10_alembic_version_difference(pair, tmp_path):
    # Shown through the helper with a stub for the other side: nothing may be written to any alembic_version table.
    dev, prod = dev_conn(pair), prod_conn(pair)
    real = prod.query

    def query(sql, args=None):
        return [("older_revision",)] if "alembic_version" in sql else real(sql, args)

    prod.query = query
    r = {x["step"]: x for x in run_all(pair, tmp_path, dev, prod)}
    assert r["H10"]["detail"] == "alembic_version_differs"


def test_doctor_runs_through_open_session_with_injected_connectors(pair, tmp_path):
    s = settings(tmp_path)
    secrets = {"SYNC_WRITER_PASSWORD": "pw", "SYNC_READER_PASSWORD": "pw"}

    def connector(*args, **kwargs):
        conn = pair.dev_conn() if args else pair.prod_conn()  # positional = the dev URL, keywords = prod host/port/user
        return conn

    def opener(env, tool, settings=None):
        return connect.open_session(env, tool, settings=settings, dev_urls=DevUrls(ENV, "postgresql://x/rw", "postgresql://x/ro"),
                                    secrets=secrets, open_tunnel_fn=lambda st: nullcontext(), connector=connector)

    results, text = doctor.run_doctor(ENV, settings=s, session_opener=opener, capabilities=caps, git_sha=SHA)
    by = {r["step"]: r for r in results}
    assert by["H1"]["ok"] and by["H2"]["ok"] and by["H5"]["ok"]
    assert by["H6"]["detail"] == "skipped:no_transport"
    assert not by["H3"]["ok"] and "reader:" in by["H3"]["detail"]  # superuser fixture role, both writer and reader are rejected
    assert not by["H4"]["ok"] and by["H4"]["detail"] == "prod_environment_mismatch"  # no memes.environment in a fresh session
    assert (tmp_path / "state" / ENV / "lock").exists()
    assert pair.public_snapshot() == pair.public_before
