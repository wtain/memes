"""P4-T2/T4/T5 without a database: the command line, the state files, preconditions on fake connections, the lock, the failed-handshake
report, and the dev write-capable URL rule."""
import contextlib
import datetime as dt
import io
import json
import os

import pytest

from sync import connect, report
from sync.config import SyncSettings, exclusive_lock
from sync.pull import bugreports, cli, state

NOW = dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SENTINEL = "SENTINEL-RATING-TEXT-5c1e"


def parse(*argv):
    return cli.build_parser().parse_args(["--env", "general", *argv])


def settings(tmp_path, **extra):
    data = {"state_dir": str(tmp_path / "state"), "dev": {"database": "d"},
            "prod": {"database": "p", "reader_role": "r", "writer_role": "w", "local_port": 16434, "ssh_alias": "a", "api_url": "https://x.test"}}
    data.update(extra)
    return SyncSettings("general", data, root=tmp_path)


# --- flags -----------------------------------------------------------------------------------------------------------------------

def test_every_flag_of_the_interface_is_accepted_and_apply_flags_echo_the_plan_changers_and_lifts():
    a = parse("--apply", "--yes", "--plan-hash", "ab" * 32, "--allow-empty-source", "--allow-shrink", "meme_saves", "--allow-shrink", "search_history",
              "--allow-growth", "--skip-files", "--top", "7", "--run-id", "20261113T120000Z-0000000a", "--json", "-")
    cli.validate_args(a)
    assert cli.apply_flags(a) == ["--skip-files", "--allow-empty-source", "--allow-shrink", "meme_saves", "--allow-shrink", "search_history",
                                  "--allow-growth"]
    assert cli.apply_flags(parse("--top", "3", "--json", "-")) == []


@pytest.mark.parametrize("argv", [
    ["--plan-hash", "ab" * 32], ["--yes"], ["--apply", "--plan-hash", "nothex"], ["--top", "0"], ["--allow-shrink", "images"],
    ["--allow-shrink", "embeddings"], ["--run-id", "x"],
    ["--apply", "--yes"],  # review finding M1: --yes never replaces the dry-run binding
])
def test_bad_flags_are_exit_two(argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_pull(parse(*argv), settings=SyncSettings("general", {}), out=out, err=err, lock=False)
    assert code == 2 and err.getvalue().startswith("sync: ")


@pytest.mark.parametrize("flag", ["--prod-url", "--i-have-pulled", "--max-pull-age", "--allow-mass-retire", "--retire", "--ratings-mode", "--first-seed"])
def test_flags_that_do_not_exist_stay_nonexistent(flag):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--env", "general", flag])


def test_env_is_required_and_checked():
    for argv in ([], ["--env", "prod"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(argv)


def test_tracked_settings_are_validated_exit_two(tmp_path):
    for bad in ({"pull": {"ratings_mode": "merge"}}, {"pull": {"text_cap": -1}}, {"pull": {"max_growth_factor": "five"}}):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_pull(parse(), settings=settings(tmp_path, **bad), out=out, err=err, lock=False)
        assert code == 2 and "sync.pull" in err.getvalue()


def test_the_shipped_tracked_pull_settings_load():
    from sync.config import load_settings

    s = load_settings("general")
    assert cli.mode_from(s) == "skip"
    lim = cli.limits_from(s)
    assert (lim.max_growth_factor, lim.min_growth_rows, lim.truncate_threshold, lim.text_cap) == (5, 100000, 200000, 2000) and lim.max_rows_per_pull > 100000
    assert s.get("pull.bug_reports.allowed_extensions") == [".txt", ".json", ".png", ".jpg", ".log", ".zip"]


# --- state files -----------------------------------------------------------------------------------------------------------------

def a_state(**over):
    doc = state.build_state(env="general", run_id="20261113T120000Z-0000000a", now=NOW, snapshot_at="2026-11-13T11:59:00Z", snapshot="10:20:",
                            ratings={"mode": "skip", "prod_count": 3, "max_updated_at": None, "keys_sha256": "ab" * 32}, prod_image_ids=12,
                            tables={"meme_saves": {"prod": 4, "replaced": 4}})
    doc.update(over)
    return doc


def test_state_has_exactly_the_fields_of_the_interface_in_order():
    doc = a_state()
    assert list(doc) == ["env", "run_id", "finished_at", "snapshot_at", "snapshot", "applied", "ratings", "prod_image_ids", "tables", "bug_reports"]
    assert list(doc["ratings"]) == ["mode", "prod_count", "max_updated_at", "keys_sha256"]
    assert doc["bug_reports"] == {"status": "skipped", "pulled": 0, "deleted_on_prod": 0, "failed": 0}
    assert doc["applied"] is True and doc["finished_at"] == "2026-11-13T12:00:00Z"
    assert state.state_problems(doc) == []


def test_load_state_treats_a_missing_unreadable_or_foreign_file_as_a_first_pull(tmp_path):
    assert state.load_state(tmp_path, "general") == (None, state.FIRST_PULL)
    (tmp_path / state.STATE_FILE).write_text("{not json")
    assert state.load_state(tmp_path, "general") == (None, state.UNREADABLE)
    (tmp_path / state.STATE_FILE).write_text(json.dumps({"env": "general"}))
    assert state.load_state(tmp_path, "general")[1] == state.UNREADABLE
    state.write_state(tmp_path, a_state(env="metal"))
    assert state.load_state(tmp_path, "general") == (None, state.FOREIGN)
    state.write_state(tmp_path, a_state())
    doc, status = state.load_state(tmp_path, "general")
    assert status == state.FOUND and doc["tables"]["meme_saves"]["prod"] == 4


def test_state_is_written_atomically_in_two_stages_and_leaves_no_temp_file(tmp_path):
    doc = a_state()
    state.write_state(tmp_path, doc)
    first = json.loads((tmp_path / state.STATE_FILE).read_text())
    assert first["bug_reports"]["status"] == "skipped"
    doc["bug_reports"] = {"status": "done", "pulled": 2, "deleted_on_prod": 2, "failed": 0}
    state.write_state(tmp_path, doc)
    assert json.loads((tmp_path / state.STATE_FILE).read_text())["bug_reports"]["pulled"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [state.STATE_FILE]


def test_ledger_roundtrip_and_a_lost_ledger_is_empty(tmp_path):
    assert state.load_ledger(tmp_path) == frozenset()
    state.write_ledger(tmp_path, {"b" * 64, "a" * 64}, "general", NOW)
    assert state.load_ledger(tmp_path) == frozenset({"a" * 64, "b" * 64})
    (tmp_path / state.LEDGER_FILE).write_text("garbage")
    assert state.load_ledger(tmp_path) == frozenset()


def test_archive_appends_json_lines_with_the_text_and_restricts_permissions(tmp_path):
    row = {"id": "i1", "query_text": SENTINEL, "grade": 2}
    assert state.append_archive(tmp_path, [], "20261113T120000Z-0000000a", {}, NOW) == 0 and not (tmp_path / state.ARCHIVE_FILE).exists()
    assert state.append_archive(tmp_path, [row], "20261113T120000Z-0000000a", {"i1": "deleted"}, NOW) == 1
    assert state.append_archive(tmp_path, [dict(row, id="i2")], "20261113T120000Z-0000000b", {}, NOW) == 1
    lines = (tmp_path / state.ARCHIVE_FILE).read_text().splitlines()
    docs = [json.loads(x) for x in lines]
    assert [d["reason"] for d in docs] == ["deleted", "overwritten"] and docs[0]["row"]["query_text"] == SENTINEL
    assert docs[0]["run_id"] == "20261113T120000Z-0000000a"
    if os.name != "nt":
        assert (tmp_path / state.ARCHIVE_FILE).stat().st_mode & 0o777 == 0o600


# --- preconditions on fake connections -------------------------------------------------------------------------------------------

class Conn:
    def __init__(self, answers=None, kind=connect.DEV_RW, app_env="general"):
        self.answers, self.sql, self.kind, self.app_env = answers or {}, [], kind, app_env
        self.host = self.port = None

    def query(self, sql, args=None):
        self.sql.append(sql)
        for key, rows in self.answers.items():
            if key in sql:
                return rows(args) if callable(rows) else rows
        return []

    def scalar(self, sql, args=None):
        rows = self.query(sql, args)
        return rows[0][0] if rows else None


def signature(drop=None, extra=None):
    from sync.pull import snapshot as snap

    rows = [(t, c, "text", "text") for t in cli.plan_mod.EXPECTED_TABLES for c in snap.columns(t)] + [("images", "id", "uuid", "uuid")]
    if drop:
        rows = [r for r in rows if (r[0], r[1]) != drop]
    return rows + ([extra] if extra else [])


def test_column_compat_equal_differing_and_not_the_model():
    key = "information_schema.columns"
    ok = cli.column_compat(Conn({key: signature()}), Conn({key: signature()}))
    assert ok["ok"] and ok["detail"] == "ok"
    differ = cli.column_compat(Conn({key: signature()}), Conn({key: signature(extra=("meme_saves", "x", "text", "text"))}))
    assert not differ["ok"] and "dev_prod_differ=meme_saves" in differ["detail"] and "prod_not_model=meme_saves" in differ["detail"]
    typed = cli.column_compat(Conn({key: signature()}), Conn({key: [(*r[:2], "integer", "int4") if r[:2] == ("meme_impressions", "rank") else r for r in signature()]}))
    assert not typed["ok"] and "dev_prod_differ=meme_impressions" in typed["detail"]  # a type difference is a mismatch too
    no_id = cli.column_compat(Conn({key: signature()}), Conn({key: signature(drop=("images", "id"))}))
    assert not no_id["ok"] and "prod_images_id_unreadable" in no_id["detail"]


def test_alembic_and_running_batches_are_warnings_never_failures():
    same = cli.alembic_note(Conn({"alembic_version": [("a",)]}), Conn({"alembic_version": [("a",)]}))
    diff = cli.alembic_note(Conn({"alembic_version": [("b",)]}), Conn({"alembic_version": [("a",)]}))
    assert same["detail"] == "equal" and diff["ok"] and diff["detail"] == "warn_differs"
    assert cli.running_batches(Conn({"batch_runs": [(2,)]})) == {"id": "running_batches", "ok": True, "detail": "warn_running=2"}
    assert cli.running_batches(Conn({"batch_runs": [(0,)]}))["detail"] == "none"


@pytest.mark.parametrize("payload,mode,count,detail", [
    ({"features": {"ratings": False}}, "skip", 0, "ok_skip"),
    ({"features": {"ratings": False}}, "skip", 5, "warn_rows_but_routes_off"),
    ({"features": {"ratings": False}}, "mirror", 0, "warn_mirror_but_routes_off"),
    ({"features": {"ratings": True}}, "skip", 0, "warn_routes_on_mode_skip"),
    ({"features": {"ratings": True}}, "mirror", 3, "ok_mirror"),
    (None, "skip", 0, "warn_routes_unknown"),
])
def test_ratings_consistency_only_warns(payload, mode, count, detail):
    r = cli.ratings_consistency(payload, mode, count)
    assert r["ok"] and r["detail"] == detail


# --- lock, handshake failure, dev url kind -------------------------------------------------------------------------------------------

def test_a_held_lock_is_exit_two_with_a_report_and_no_connection(tmp_path):
    s = settings(tmp_path)

    def opener(*a, **k):
        raise AssertionError("must not connect")

    with exclusive_lock(s):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_pull(parse("--run-id", "20261113T120000Z-00000001"), settings=s, session_opener=opener, out=out, err=err)
    assert code == 2 and "another sync command holds the lock" in err.getvalue()
    doc = json.loads((tmp_path / "state" / "runs" / "20261113T120000Z-00000001" / "report.json").read_text())
    assert doc["preconditions"] == [{"id": "lock", "ok": False, "detail": "held"}]


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


def test_a_failed_handshake_writes_a_report_with_the_step_and_changes_nothing(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch", [{"step": "H1", "ok": True, "detail": "ok"},
                                                                                         {"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

    monkeypatch.setattr(connect, "handshake", refuse)
    dev, prod = Conn(), Conn(kind=connect.PROD_READER)
    opener = Opener(dev, prod)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_pull(parse("--apply", "--yes", "--run-id", "20261113T120000Z-00000002"), settings=settings(tmp_path), session_opener=opener, out=out, err=err)
    assert code == 2 and opener.args == ("general", "L", "normal") and "prod_environment_mismatch" in err.getvalue()
    doc = json.loads((tmp_path / "state" / "runs" / "20261113T120000Z-00000002" / "report.json").read_text())
    assert [h["ok"] for h in doc["handshake"]] == [True, False] and doc["sections"] == [] and doc["plan_hash"] is None
    assert not (tmp_path / "state" / state.STATE_FILE).exists()
    assert dev.sql == [] and prod.sql == []  # nothing was read or written after the failed step


def test_the_pull_handshake_runs_h1_to_h5_and_h6_only_with_a_transport():
    assert cli.handshake_steps(None) == ("H1", "H2", "H3", "H4", "H5") and cli.handshake_steps(object())[-1] == "H6"


def test_the_pull_refuses_the_read_only_dev_url_and_the_handshake_checks_the_kind(tmp_path):
    s = settings(tmp_path)
    same = connect.DevUrls("general", rw="postgresql://u:p@h/d", ro="postgresql://u:p@h/d")
    with pytest.raises(connect.PreconditionError, match="dev_url_kind_wrong"):
        with connect.open_session("general", "L", settings=s, dev_urls=same, secrets={"SYNC_READER_PASSWORD": "x"},
                                  open_tunnel_fn=lambda settings: contextlib.nullcontext(), connector=lambda *a, **k: None):
            pass
    ro_conn = Conn({"current_database": [("d",)]}, kind=connect.DEV_RO)
    with pytest.raises(connect.HandshakeError) as exc:
        connect.handshake("general", "L", settings=SyncSettings("general", {"dev": {"database": "d"}}), dev=ro_conn, steps=("H1",))
    assert "dev_url_kind_wrong" in str(exc.value)


# --- the bug-report hook ------------------------------------------------------------------------------------------------------------

def test_the_bug_report_hook_reports_skipped_until_t6():
    assert bugreports.plan_lists(None) == {}
    assert bugreports.run(None, None, None, "20261113T120000Z-0000000a") == {"status": "skipped", "pulled": 0, "deleted_on_prod": 0, "failed": 0}
