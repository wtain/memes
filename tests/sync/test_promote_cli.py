"""P2-T4: the command line, phase 0 checks on fake connections, the files gate and the redaction of failures (no database)."""
import datetime as dt
import io
import json

import pytest

from sync import connect
from sync.config import SyncSettings, exclusive_lock
from sync.promote import cli, files_gate
from sync.promote import preconditions as pre

NOW = dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SENTINEL = "SENTINEL-ROW-VALUE-93af"


def parse(*argv):
    return cli.build_parser().parse_args(["--env", "general", *argv])


# --- flags -----------------------------------------------------------------------------------------------------------------------

def test_every_flag_of_the_interface_is_accepted():
    a = parse("--apply", "--yes", "--plan-hash", "ab" * 32, "--first-seed", "--only", "tags,images", "--only", "embeddings",
              "--limit-images", "5", "--batch-size", "100", "--max-report-age", "90m", "--top", "7", "--run-id", "20261113T120000Z-0000000a",
              "--json", "-", "--allow-mass-change", "12", "--allow-shrink", "tags", "--allow-shrink", "embeddings", "--ignore-running-batches",
              "--allow-model-change")
    cli.validate_args(a)
    assert cli.only_tables(a) == ["tags", "images", "embeddings"] and a.allow_shrink == ["tags", "embeddings"]
    b = parse("--verify-only")
    cli.validate_args(b)
    assert b.verify_only


@pytest.mark.parametrize("argv", [
    ["--plan-hash", "ab" * 32], ["--yes"], ["--apply", "--plan-hash", "nothex"], ["--verify-only", "--apply"],
    ["--limit-images", "0"], ["--batch-size", "0"], ["--top", "0"], ["--allow-mass-change", "-1"], ["--allow-shrink", "meme_saves"],
    ["--only", "meme_saves"], ["--only", ","], ["--run-id", "x"], ["--max-report-age", "soon"], ["--max-report-age", "0h"],
    ["--first-seed", "--verify-only"],
])
def test_bad_flags_are_exit_two(argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(parse(*argv), settings=SyncSettings("general", {}), out=out, err=err, lock=False)
    assert code == 2 and err.getvalue().startswith("sync: ")


@pytest.mark.parametrize("flag", ["--retire", "--allow-mass-retire", "--skip-files-check", "--ack-revision", "--i-have-pulled", "--allow-replace",
                                  "--prod-url", "--max-pull-age", "--force-orphans"])
def test_flags_that_do_not_exist_stay_nonexistent(flag):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--env", "general", flag])


def test_env_is_required_and_checked():
    for argv in ([], ["--env", "prod"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(argv)


def test_parse_age():
    assert cli.parse_age("2h") == 7200 and cli.parse_age("90m") == 5400 and cli.parse_age("7200") == 7200 and cli.parse_age("1d") == 86400
    with pytest.raises(cli.UsageProblem):
        cli.parse_age("2 hours")


def test_apply_command_flags_carry_plan_changing_flags_and_lifts_but_not_secrets_or_output_flags():
    a = parse("--first-seed", "--only", "tags,images", "--limit-images", "5", "--allow-mass-change", "9", "--allow-shrink", "tags",
              "--ignore-running-batches", "--max-report-age", "90m", "--top", "3", "--json", "-", "--allow-model-change")
    assert cli.apply_flags(a) == ["--first-seed", "--only", "tags,images", "--limit-images", "5", "--max-report-age", "90m",
                                  "--allow-mass-change", "9", "--allow-shrink", "tags", "--allow-model-change", "--ignore-running-batches"]
    assert cli.apply_flags(parse()) == []


def test_verify_only_is_not_built_and_exits_one_before_any_connection(tmp_path):
    def opener(*a, **k):
        raise AssertionError("must not connect")

    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(parse("--verify-only"), settings=SyncSettings("general", {"state_dir": str(tmp_path)}, root=tmp_path),
                           session_opener=opener, out=out, err=err)
    assert code == 1 and "not built yet" in err.getvalue()


# --- handshake failure, lock, failed preconditions: a report is still written ------------------------------------------------------

class Conn:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.sql = []

    def query(self, sql, args=None):
        self.sql.append(sql)
        for key, rows in self.answers.items():
            if key in sql:
                return rows(args) if callable(rows) else rows
        return []

    def scalar(self, sql, args=None):
        rows = self.query(sql, args)
        return rows[0][0] if rows else None


class Opener:
    def __init__(self, dev, prod):
        self.dev, self.prod, self.args = dev, prod, None

    def __call__(self, env, tool, mode="normal", settings=None):
        self.args = (env, tool, mode)
        return self

    def __enter__(self):
        return {"dev": self.dev, "prod": self.prod, "prod_reader_conn": None}

    def __exit__(self, *exc):
        return None


def settings(tmp_path):
    return SyncSettings("general", {"state_dir": str(tmp_path / "state"), "dev": {"database": "d"}, "prod": {"database": "p"}}, root=tmp_path)


def test_handshake_failure_writes_a_report_and_exits_two(tmp_path, monkeypatch):
    def failing(*a, **k):
        raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch",
                                     [{"step": "H1", "ok": True, "detail": "ok"}, {"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

    monkeypatch.setattr(connect, "handshake", failing)
    opener = Opener(Conn(), Conn())
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(parse("--run-id", "20261113T120000Z-0000000b"), settings=settings(tmp_path), session_opener=opener, out=out, err=err)
    assert code == 2 and "H4" in err.getvalue()
    assert opener.args == ("general", "P", "normal")
    doc = json.loads((tmp_path / "state" / "runs" / "20261113T120000Z-0000000b" / "report.json").read_text())
    assert [h["ok"] for h in doc["handshake"]] == [True, False] and doc["sections"] == [] and doc["plan_hash"] is None
    assert "FAILED: H4" in out.getvalue()
    # a dry run asks the prod session to be read only before anything else
    assert opener.prod.sql[0] == "SET default_transaction_read_only = on"


def test_first_seed_mode_reaches_the_handshake(tmp_path, monkeypatch):
    seen = {}

    def spy(env, tool, mode="normal", **k):
        seen["args"] = (tool, mode)
        raise connect.HandshakeError("x", [{"step": "H9", "ok": False, "detail": "state_serving_want_seeding"}])

    monkeypatch.setattr(connect, "handshake", spy)
    opener = Opener(Conn(), Conn())
    cli.run_promote(parse("--first-seed", "--run-id", "20261113T120000Z-0000000c"), settings=settings(tmp_path), session_opener=opener,
                    out=io.StringIO(), err=io.StringIO())
    assert seen["args"] == ("P", "first-seed") and opener.args[2] == "first-seed"


def test_held_state_lock_is_exit_two_with_a_report(tmp_path):
    s = settings(tmp_path)
    with exclusive_lock(s):
        # a second holder in the same process: the OS lock is per file descriptor on Windows and POSIX flock semantics differ,
        # so go through the real function the command uses
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_promote(parse("--run-id", "20261113T120000Z-0000000d"), settings=s, session_opener=lambda *a, **k: pytest.fail("no connection"),
                               out=out, err=err)
    assert code == 2 and "another sync command holds the lock" in err.getvalue()
    doc = json.loads((tmp_path / "state" / "runs" / "20261113T120000Z-0000000d" / "report.json").read_text())
    assert doc["preconditions"] == [{"id": "lock", "ok": False, "detail": "held"}]


def test_failed_precondition_is_exit_two_and_names_it_in_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(connect, "handshake", lambda *a, **k: [{"step": "H1", "ok": True, "detail": "ok"}])
    monkeypatch.setattr(pre, "session_settings", lambda d, p: pre.result("session_settings", True, "ok"))
    monkeypatch.setattr(pre, "schema_parity", lambda d, p: pre.result("schema_parity", False, "server_major_differs"))
    monkeypatch.setattr(pre, "column_parity", lambda d, p: pre.result("column_parity", True, "ok"))
    monkeypatch.setattr(pre, "model_parity", lambda d, p: pre.result("model_parity", True, "ok"))
    monkeypatch.setattr(pre, "running_batches", lambda d, ignore=False: pre.result("running_batches", True, "ok"))
    monkeypatch.setattr(pre, "try_prod_lock", lambda p: pre.result("prod_lock", True, "other_sync_sessions=0"))
    released = []
    monkeypatch.setattr(pre, "release_prod_lock", lambda p: released.append(True))
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(parse("--run-id", "20261113T120000Z-0000000e"), settings=settings(tmp_path), session_opener=Opener(Conn(), Conn()),
                           out=out, err=err)
    assert code == 2 and "schema_parity" in err.getvalue()
    doc = json.loads((tmp_path / "state" / "runs" / "20261113T120000Z-0000000e" / "report.json").read_text())
    assert [p["id"] for p in doc["preconditions"] if not p["ok"]] == ["schema_parity"] and doc["sections"] == []
    assert released == [True]  # the prod advisory lock is released on the way out


def test_a_database_error_does_not_leak_row_content(tmp_path, monkeypatch):
    class Boom(Exception):
        pgcode = "23505"

        class diag:
            constraint_name = "uq_x"
            table_name = "tags"

        def __str__(self):
            return f"duplicate key value violates unique constraint: Key (value)=({SENTINEL}) already exists"

    import psycopg2

    class PgBoom(psycopg2.Error):
        pgcode = "23505"
        diag = Boom.diag

        def __str__(self):
            return f"DETAIL: Key (value)=({SENTINEL})"

    monkeypatch.setattr(connect, "handshake", lambda *a, **k: [])

    def raise_pg(args):
        raise PgBoom()

    class Raw:
        def cursor(self):
            raise PgBoom()

    dev = connect.SyncConnection(Raw(), connect.DEV_RO)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(parse("--run-id", "20261113T120000Z-0000000f"), settings=settings(tmp_path), session_opener=Opener(dev, Conn()),
                           out=out, err=err)
    assert code == 1
    assert SENTINEL not in out.getvalue() + err.getvalue() and "sqlstate=23505" in err.getvalue()
    for p in (tmp_path / "state").rglob("*"):
        if p.is_file():
            assert SENTINEL not in p.read_text(errors="ignore")


# --- phase 0 on fake connections -------------------------------------------------------------------------------------------------

def test_result_detail_is_cut_to_the_report_limit():
    assert len(pre.result("x", False, "a" * 200)["detail"]) == 80


def test_running_batches_checks():
    dev = Conn({"batch_runs": [(0,)]})
    assert pre.running_batches(dev) == {"id": "running_batches", "ok": True, "detail": "ok"}
    dev = Conn({"batch_runs": [(2,)]})
    assert pre.running_batches(dev) == {"id": "running_batches", "ok": False, "detail": "running=2"}
    assert pre.running_batches(dev, ignore=True) == {"id": "running_batches", "ok": True, "detail": "ignored=2"}
    assert "kind <> 'ingestion'" in dev.sql[0]


def test_required_tables_config():
    assert pre.required_tables_config(["embeddings", "ocr_texts"])["ok"]
    r = pre.required_tables_config(["embeddings", "concepts", "nope"])
    assert not r["ok"] and r["detail"] == "unknown=concepts,nope"


def test_schema_parity_on_fake_connections():
    def conn(version=160004, ext=(("pg_trgm", "1.6"), ("vector", "0.8.0")), enc="UTF8", dims=(("embeddings", "embedding", 512),)):
        return Conn({"server_version_num": [(version,)], "pg_extension": [list(e) for e in ext], "server_encoding": [(enc,)],
                     "atttypmod": [list(d) for d in dims]})

    assert pre.schema_parity(conn(), conn())["ok"]
    assert pre.schema_parity(conn(), conn(version=170002))["detail"] == "server_major_differs"
    assert pre.schema_parity(conn(), conn(version=160009))["ok"]  # minor versions may differ
    assert pre.schema_parity(conn(), conn(ext=(("pg_trgm", "1.6"), ("vector", "0.7.0"))))["detail"] == "extension_versions_differ"
    assert pre.schema_parity(conn(), conn(ext=(("vector", "0.8.0"),)))["ok"] is False
    assert pre.schema_parity(conn(), conn(enc="SQL_ASCII"))["detail"] == "server_encoding_not_utf8"
    assert pre.schema_parity(conn(), conn(dims=(("embeddings", "embedding", 1536),)))["detail"] == "vector_dimensions_differ"


def test_column_parity_compares_both_sides_and_the_model():
    model = {"images": ["id", "filename"], **{t: ["id"] for t in pre.registry.names(pre.Klass.D) if t != "images"}}
    cols = [(t, c, "text", "text", "YES") for t, cs in model.items() for c in cs]
    same = Conn({"information_schema.columns": cols})
    assert pre.column_parity(same, Conn({"information_schema.columns": cols}), model)["ok"]
    extra = Conn({"information_schema.columns": cols + [("tags", "extra", "text", "text", "YES")]})
    r = pre.column_parity(same, extra, model)
    assert not r["ok"] and "dev_prod_differ=tags" in r["detail"] and "prod_not_model=tags" in r["detail"]
    nullable = Conn({"information_schema.columns": [(t, c, "text", "text", "NO") if t == "images" else (t, c, "text", "text", "YES") for t, c, *_ in cols]})
    assert pre.column_parity(same, nullable, model)["detail"].startswith("dev_prod_differ=images")


def test_model_parity_rules():
    def conn(pairs, taxonomy):
        return Conn({"model_name": [list(p) for p in pairs], "taxonomy_version": [(t,) for t in taxonomy]})

    assert pre.model_parity(conn([("m", "d")], [3]), conn([("m", "d")], [3]))["ok"]
    assert pre.model_parity(conn([("m", "d")], [3]), conn([], []))["ok"]  # an empty prod takes any single pair
    assert pre.model_parity(conn([], []), conn([("m", "d")], [3]))["ok"]  # differences from prod are not a precondition
    assert pre.model_parity(conn([("m", "d")], [3]), conn([("m", "OTHER")], [4]))["ok"]
    assert pre.model_parity(conn([("m", "d"), ("m2", "d")], [3]), conn([], []))["detail"] == "dev_model_pairs=2"
    assert pre.model_parity(conn([("m", "d")], [3, 4]), conn([], []))["detail"] == "dev_taxonomy_versions=2"
    assert pre.model_parity(conn([("m", "d"), ("m2", "d")], [3, 4]), conn([], []))["detail"] == "dev_model_pairs=2,dev_taxonomy_versions=2"


def test_model_pair_change_is_informational_and_names_what_changed():
    def conn(pairs, taxonomy):
        return Conn({"model_name": [list(p) for p in pairs], "taxonomy_version": [(t,) for t in taxonomy]})

    def detail(dev, prod):
        r = pre.model_pair_change(dev, prod)
        assert r["id"] == "model_pair_change" and r["ok"]
        return r["detail"]

    assert detail(conn([("m", "d")], [3]), conn([("m", "d")], [3])) == "profile_embedding=same,taxonomy=same"
    assert detail(conn([("m", "d")], [3]), conn([("m", "OTHER")], [3])) == "profile_embedding=changed,taxonomy=same"
    assert detail(conn([("m", "d")], [3]), conn([("m", "d")], [4])) == "profile_embedding=same,taxonomy=changed"
    assert detail(conn([("m", "d")], [3]), conn([("n", "d")], [4])) == "profile_embedding=changed,taxonomy=changed"
    assert detail(conn([("m", "d")], [3]), conn([], [])) == "profile_embedding=same,taxonomy=same"  # empty prod: nothing to overwrite
    assert detail(conn([], []), conn([("m", "d")], [3])) == "profile_embedding=same,taxonomy=same"  # empty dev: the coverage guards speak
    assert pre.model_changed([pre.result("model_pair_change", True, "profile_embedding=changed,taxonomy=same")])
    assert not pre.model_changed([pre.result("model_pair_change", True, "profile_embedding=same,taxonomy=same")])
    assert not pre.model_changed([])


def test_prod_lock_results():
    got = Conn({"pg_try_advisory_lock": [(True,)], "pg_stat_activity": [(1,)]})
    assert pre.try_prod_lock(got) == {"id": "prod_lock", "ok": True, "detail": "other_sync_sessions=1"}
    busy = Conn({"pg_try_advisory_lock": [(False,)], "pg_stat_activity": [(0,)]})
    assert pre.try_prod_lock(busy) == {"id": "prod_lock", "ok": False, "detail": "held,other_sync_sessions=0"}
    assert pre.PROMOTE_LOCK_KEY < 2 ** 63


# --- files gate --------------------------------------------------------------------------------------------------------------------

def files_doc(**kw):
    base = {"run_id": "20261113T110000Z-aaaaaaaa", "env": "general", "finished_at": "2026-11-13T11:30:00Z", "complete": True,
            "images": [{"id": "1", "filename": "a.jpg", "size": 1, "content_hash": "h1"}],
            "left_out": [{"id": "2", "filename": "b.jpg", "reason": "name_trailing_dot"}, {"id": "3", "filename": "c", "reason": "weird"}],
            "manifest_sha256": "m" * 64}
    base.update(kw)
    return base


def test_files_gate_check_statuses():
    r = files_gate.parse(files_doc())
    ok = lambda **kw: files_gate.check(files_gate.parse(files_doc(**kw)), "general", NOW, 7200, "m" * 64)
    assert ok() == "valid"
    assert ok(env="metal") == "env_mismatch"
    assert ok(finished_at="2026-11-13T09:59:59Z") == "stale"
    assert ok(finished_at="2026-11-13T10:00:00Z") == "valid"  # exactly 2 h old
    assert ok(finished_at="2026-11-13T13:00:00Z") == "stale"  # from the future
    assert ok(manifest_sha256="x" * 64) == "manifest_mismatch"
    assert ok(finished_at="yesterday") == "malformed"
    assert files_gate.check(None, "general", NOW, 7200, "m" * 64) == "absent"
    assert files_gate.check(None, "general", NOW, 7200, "m" * 64, status="malformed") == "malformed"
    assert r.left_out == {"2": "name_problem", "3": "files_gate_miss"}


def test_files_gate_ignores_dry_run_folders_and_takes_the_newest(tmp_path):
    runs = tmp_path / "runs"
    for name, finished, dry in (("20261113T100000Z-00000001", "2026-11-13T10:00:00Z", False), ("20261113T110000Z-00000002", "2026-11-13T11:00:00Z", False),
                                ("20261113T115000Z-00000003", "2026-11-13T11:50:00Z", True)):
        f = runs / name
        f.mkdir(parents=True)
        (f / "files-promoted.json").write_text(json.dumps(files_doc(run_id=name, finished_at=finished)))
        if dry:
            (f / "dry").write_text("")
    got, status = files_gate.load_newest(runs)
    assert status == "valid" and got.run_id == "20261113T110000Z-00000002"
    own = runs / "20261113T100000Z-00000001"
    assert files_gate.load_newest(runs, own)[0].run_id == "20261113T100000Z-00000001"  # this run's own folder first
    assert files_gate.load_newest(tmp_path / "nothing") == (None, "absent")
    (runs / "20261113T110000Z-00000002" / "files-promoted.json").write_text("{not json")
    (runs / "20261113T100000Z-00000001" / "files-promoted.json").write_text(json.dumps({"nonsense": 1}))
    assert files_gate.load_newest(runs) == (None, "malformed")


def test_files_gate_classify_compares_id_filename_and_hash():
    class Row:
        def __init__(self, id, filename, content_hash):
            self.id, self.filename, self.content_hash = id, filename, content_hash

    rep = files_gate.parse(files_doc(images=[{"id": str(i), "filename": f"{i}.jpg", "size": 1, "content_hash": f"h{i}"} for i in (1, 2, 4)],
                                     left_out=[{"id": "5", "filename": "5.jpg", "reason": "missing_file"},
                                               {"id": "6", "filename": "6.jpg", "reason": "name_reserved"},
                                               {"id": "7", "filename": "7.jpg", "reason": "over_member_cap"}]))
    rows = [Row("1", "1.jpg", "h1"), Row("2", "2.jpg", "CHANGED"), Row("3", "3.jpg", "h3"), Row("4", "other.jpg", "h4"), Row("5", "5.jpg", "h5"),
            Row("6", "6.jpg", "h6"), Row("7", "7.jpg", "h7"), Row("8", "8.jpg", None)]
    assert files_gate.classify(rep, rows) == {"files_gate_miss": ["2", "3", "4", "8"],
                                              "missing_file": ["5"], "name_problem": ["6"], "over_member_cap": ["7"]}
    assert files_gate.classify(rep, [Row("1", "1.jpg", None)]) == {}  # no dev content hash: the filename decides
