"""P6: `sync.verify` over the two-schema fixture (the dev schema plays dev, the prod schema plays prod, a LocalTransport on a temporary
gate tree plays the gate) and, for the file checks, the real gate file against a temporary directory tree.

Only `ocrdb_test` (via `sync_pair`). The handshake is replaced by a canned success (the fixture has no tunnel, roles, settings or capabilities
endpoint; its logic is unit tested in tests/sync/test_connect.py), like the promote, pull and run integration tests. The fixture's database role is
a superuser and `ocrdb_test` keeps its default PUBLIC grants, so S1 (grants) and S2 (roles) cannot be green here: the grants reader is replaced
by a canned answer in `rig`, and S2 is skipped by flag where a green report is the point. Not covered: a real read-only role (read-only is
proved by recording every statement of every connection), a real sshd, the HTTPS checks against a prod stack (unit tests with fakes).
"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import os

import pytest
from psycopg2.extensions import cursor as pg_cursor

from sync import connect
from sync.config import SyncSettings
from sync.files.transport import LocalTransport
from sync.pull import state as pull_state
from sync.verify import cli as verify_cli
from tests.integration.sync.test_promote_apply import command, dev_only, rich, rid, world  # noqa: F401
from tests.integration.sync.test_promote_plan import Side, uid
from tests.integration.sync.test_pull import imp, sh
from tests.sync.gate_helpers import ENV_NAME, Tree

_ = (command, dev_only, world)
SENT = "SENTINEL-VERIFY-TEXT-7b3f1c"
NOW = dt.datetime(2026, 11, 14, 12, 0, 0, tzinfo=dt.timezone.utc)
SHA = "abc1234def"
WRITE_WORDS = ("insert", "update", "delete", "truncate", "create", "drop", "alter", "grant", "revoke", "copy", "vacuum", "reindex")
READ_WORDS = ("select", "set", "begin", "commit", "with", "show")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LogCursor(pg_cursor):
    log: list = []

    def execute(self, query, vars=None):
        LogCursor.log.append(query if isinstance(query, str) else query.decode("utf-8", "replace"))
        return super().execute(query, vars)


class VRig:
    def __init__(self, pair, tmp_path, monkeypatch, *, verify_settings=None, api_url=""):
        self.pair, self.tmp = pair, tmp_path
        self.dev_side, self.prod_side = Side(pair.dev_conn()), Side(pair.prod_conn())
        (tmp_path / "prod").mkdir(exist_ok=True)
        self.tree = Tree(tmp_path / "prod", free_floor_pct="0")
        self.images = tmp_path / "dev-images"
        self.images.mkdir(exist_ok=True)
        self.state = tmp_path / "vstate"
        self.settings = SyncSettings("general", {
            "state_dir": str(self.state), "dev": {"database": "ocrdb_test"},
            "prod": {"database": "ocrdb_test", "reader_role": "memes_general_sync_r", "writer_role": "memes_general_sync_w",
                     "api_url": api_url, "gate_alias": "g", "upload_alias": "u", "ssh_alias": "a"},
            "verify": {"files": {"modes": []}, **(verify_settings or {})}}, root=tmp_path)
        self.transport = LocalTransport(ENV_NAME, self.tree.conf_path, self.tree.incoming)
        self.statements: list = []
        monkeypatch.setattr(connect, "handshake", lambda *a, **k: [{"step": "H1", "ok": True, "detail": "ok"}])
        monkeypatch.setattr(verify_cli, "gather_grants", lambda settings, prod, reader: {"writer": [], "reader": [], "app": [], "app_facts": None})
        LogCursor.log = self.statements

    def opener(self, env, tool, mode="normal", settings=None):
        pair = self.pair

        @contextlib.contextmanager
        def cm():
            conns = []

            def mk(raw_fn, kind, app_env=None):
                raw = raw_fn()
                raw.cursor_factory = LogCursor
                raw.autocommit = True
                c = connect.SyncConnection(raw, kind, None, None, app_env)
                conns.append(c)
                return c

            try:
                dev, prod = mk(pair.dev_conn, connect.DEV_RO, "general"), mk(pair.prod_conn, connect.PROD_WRITER_RO)
                reader = mk(pair.prod_conn, connect.PROD_READER)
                for c in (prod, reader):
                    c.query(f"SET memes.revision = '{SHA}'")  # session-level custom settings: what the prod database carries
                    c.query("SET memes.state = 'serving'")
                yield {"dev": dev, "prod": prod, "prod_reader_conn": reader}
            finally:
                for c in conns:
                    c.close()

        return cm()

    def verify(self, *argv, run_id, http=None, with_gate=True, base_dir=True, sink=None, plan_only=False, now=NOW, probe=None):
        out, err = io.StringIO(), io.StringIO()
        args = verify_cli.build_parser().parse_args(["--env", "general", *argv, "--run-id", run_id])
        if sink is not None:
            args.document_sink = sink
        code = verify_cli.run_verify(args, settings=self.settings, session_opener=self.opener, capabilities=lambda url: {"stage": "prod", "environment": "general"},
                                     transport=self.transport if with_gate else None, http=http, git_sha=SHA, now=now,
                                     base_dir=str(self.images) if base_dir else "", plan_only=plan_only, accounts_probe=probe, out=out, err=err)
        folder = self.state / "runs" / run_id
        return type("V", (), {"code": code, "out": out.getvalue(), "err": err.getvalue(), "folder": folder})

    # --- seeding ---------------------------------------------------------------------------------------------------------------------
    def image(self, n, data=None, *, dev=True, prod=True, put_prod_file=True, **kw):
        """Image n on the chosen sides with one content hash and, when `data` is given, its file on dev and (default) on the gate tree."""
        data = data if data is not None else f"image-{n}-".encode() * 8
        for flag, side in ((dev, self.dev_side), (prod, self.prod_side)):
            if flag:
                side.image(n, **kw)
                side.x("update images set content_hash = %s where id = %s", (sha(data), uid(n)))
        if dev:
            (self.images / f"img{n:04d}.jpg").write_bytes(data)
        if prod and put_prod_file:
            self.tree.put(f"img{n:04d}.jpg", data)
        return uid(n)

    def pull_state(self, finished_at):
        doc = pull_state.build_state(env="general", run_id="20261113T100000Z-0000000a", now=finished_at, snapshot_at="2026-11-13T10:00:00Z",
                                     snapshot="1:1:", ratings={"mode": "skip", "prod_count": 0, "max_updated_at": None, "keys_sha256": "0" * 64},
                                     prod_image_ids=0, tables={}, bug_reports=None)
        pull_state.write_state(self.state, doc)

    def facts(self, disk=None, containment=None):
        (self.tree.facts / "disk.json").write_text(json.dumps(disk if disk is not None else {"mounts": {"images": {"free_pct": 60}, "database": {"free_pct": 55}}}))
        (self.tree.facts / "containment.json").write_text(json.dumps(containment if containment is not None else GOOD_CONTAINMENT))

    def backup(self, age_h=2, size=5000, meta=None):
        dump = self.tree.backups / "classp-20261114.dump"
        dump.write_bytes(b"x" * size)
        (self.tree.backups / "classp-20261114.meta.json").write_text(json.dumps(meta if meta is not None else {"pg_restore_list": "ok", "entries": 7}))
        t = NOW.timestamp() - age_h * 3600
        os.utime(dump, (t, t))


GOOD_CONTAINMENT = {"read_only_root": True, "cap_drop": ["ALL"], "no_new_privileges": True, "user": "1000:1001", "base_path_read_only": True,
                    "published_ports": ["127.0.0.1:8000->8000"]}


@pytest.fixture()
def rig(sync_pair, tmp_path, monkeypatch):
    sync_pair.reset()
    r = VRig(sync_pair, tmp_path, monkeypatch)
    try:
        yield r
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


def vjson(folder):
    return json.loads((folder / "verify.json").read_text())


def by_id(doc):
    return {c["id"]: c for c in doc["checks"]}


def report_of(folder):
    return json.loads((folder / "report.json").read_text())


def drift(folder, name="images"):
    sec = next(s for s in report_of(folder)["sections"] if s["name"] == "verify")
    return next(e for e in sec["entries"] if e["name"] == name)


def groups_of(entry):
    return {g["id"]: g["groups"] for g in entry["overwritten"]["groups"]}


def green_rig(rig, n=4):
    """Equal images on both sides with files, facts, a fresh pull state: everything that can be green without a VPS is."""
    for i in range(1, n + 1):
        rig.image(i)
    rig.transport.hash_all()  # the gate records the hashes of the files the test put there (a real commit does that)
    rig.pull_state(NOW - dt.timedelta(hours=2))
    rig.facts()
    rig.backup()


# --- a clean state ------------------------------------------------------------------------------------------------------------------

def test_a_clean_state_is_green_and_writes_verify_json_and_a_dry_report(rig):
    green_rig(rig)
    run = rig.verify("--skip", "S2", run_id=rid(1))
    assert run.code == 0, (run.out, run.err)
    doc = vjson(run.folder)
    checks = by_id(doc)
    assert [c["id"] for c in doc["checks"]] == [c for c in verify_cli.CHECK_IDS if c in checks]  # report order
    bad = {i: c for i, c in checks.items() if c["status"] in ("warn", "error")}
    assert bad == {}, bad
    assert doc["env"] == "general" and doc["run_id"] == rid(1) and doc["ran_at"] == "2026-11-14T12:00:00Z"
    assert {i: c["skipped_reason"] for i, c in checks.items() if c["status"] == "skipped"} == {
        "S2": "skipped_by_flag", "F3": "deep_files_not_requested", "S3": "api_url_missing", "S4b": "account_probe_unavailable", "S7": "api_url_missing", "A4": "api_url_missing"}
    for cid in ("S0", "D1", "D2", "D3", "D4", "D5", "D6", "F1", "F2", "F4", "P1", "P2", "A1", "A2", "A3", "S1", "S4", "S5", "S6", "S8"):
        assert checks[cid]["status"] == "ok", (cid, checks[cid])
    assert doc["summary"]["exit_code"] == 0 and doc["summary"]["drift"]["images"] == {"added": 0, "overwritten": 0, "unchanged": 4, "left_behind": 0}
    assert (run.folder / "dry").exists() and (run.folder / "report.md").exists()
    assert drift(run.folder)["unchanged"]["count"] == 4 and f"verify {rid(1)}" in run.out
    # the trend store: growth metrics of this run are in verify.json
    assert doc["growth"]["metrics"]["meme_saves.rows"] == 0 and "meme_impressions.bytes" in doc["growth"]["metrics"]


def test_verify_issues_no_write_statement_on_either_side(rig):
    green_rig(rig, 3)
    del rig.statements[:]
    run = rig.verify("--skip", "S2", "--deep-files", run_id=rid(2))
    assert run.code == 0, (run.out, run.err)
    firsts = {s.strip().split(None, 1)[0].lower() for s in rig.statements if s.strip()}
    assert firsts <= set(READ_WORDS), firsts
    assert not [s for s in rig.statements if any(w in s.lower().split() for w in ("insert", "update", "delete", "truncate", "alter", "drop", "create"))
                and not s.lower().startswith(("select", "with"))]
    assert len(rig.statements) > 20 and any("default_transaction_read_only" in s for s in rig.statements)


# --- drift after a promote apply ----------------------------------------------------------------------------------------------------

def test_drift_categories_after_a_real_promote_apply(world, command, dev_only, tmp_path, monkeypatch):  # noqa: F811
    """A real `sync.promote --apply` copies seven images to prod; verify then shows no drift. A later change on dev is `overwritten` (with its
    groups), a new dev image `added`, an image flagged on dev afterwards `left_behind` (informational: exit stays 0 without the other two)."""
    dev_only(7)
    code, out, err, _ = command("--apply", "--yes", run_id=rid(100))
    assert code == 0, (out, err)
    r = VRig(world.pair, tmp_path, monkeypatch)
    try:
        r.settings.data["state_dir"] = str(tmp_path / "vstate")
        clean = r.verify("--skip", "S2,F1,F2,F3,F4", run_id=rid(101), with_gate=False)
        assert clean.code == 0, (clean.out, clean.err)
        c = by_id(vjson(clean.folder))
        assert c["D2"]["status"] == "ok" and c["D2"]["detail"].startswith("new=0,changed=0") and c["D3"]["status"] == "ok" and c["D4"]["status"] == "ok"
        assert {i: c[i]["skipped_reason"] for i in ("F1", "F2", "F3", "F4")} == {i: "skipped_by_flag" for i in ("F1", "F2", "F3", "F4")}

        world.dev_side.x("insert into image_extras (image_id, flagged) values (%s, true) on conflict (image_id) do update set flagged = true", (uid(7),))  # flagged on dev after promotion
        left = r.verify("--skip", "S2,F1,F2,F3,F4", run_id=rid(102), with_gate=False)
        assert left.code == 0, (left.out, left.err)  # left behind is informational (decision 27)
        e = drift(left.folder)
        assert e["left_behind"]["ids"] == [uid(7)] and e["added"]["count"] == 0 and e["overwritten"]["count"] == 0
        assert by_id(vjson(left.folder))["D2"]["detail"].startswith("new=0,changed=0,left_behind=1")

        world.dev_side.x("update tags set value = 'changed' where image_id = %s", (uid(2),))
        rich(world.dev_side, 8)  # a new image on dev only
        diff = r.verify("--skip", "S2,F1,F2,F3,F4", run_id=rid(103), with_gate=False)
        assert diff.code == 4
        e = drift(diff.folder)
        assert e["added"]["ids"] == [uid(8)] and e["overwritten"]["ids"] == [uid(2)] and groups_of(e) == {uid(2): ["tags"]}
        assert e["left_behind"]["ids"] == [uid(7)]
        assert by_id(vjson(diff.folder))["D2"]["status"] == "error" and "FAIL" in diff.out
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


def test_a_tampered_prod_digest_is_exit_4_and_a_deleted_row_changes_the_counts(rig):
    green_rig(rig)
    rig.prod_side.x("update tags set value = %s where image_id = %s", ("tampered", uid(2)))
    rig.prod_side.x("delete from ocr_texts where image_id = %s", (uid(3),))
    run = rig.verify("--skip", "S2", run_id=rid(3))
    assert run.code == 4
    c = by_id(vjson(run.folder))
    assert c["D2"]["status"] == "error" and "changed=2" in c["D2"]["detail"] and c["D3"]["status"] == "error" and "ocr_texts" in c["D3"]["detail"]
    e = drift(run.folder)
    assert groups_of(e) == {uid(2): ["tags"], uid(3): ["ocr"]}
    assert run.code == vjson(run.folder)["summary"]["exit_code"]


def test_a_dev_job_running_makes_the_digest_errors_warnings(rig):
    green_rig(rig, 2)
    rig.prod_side.x("update tags set value = 'x' where image_id = %s", (uid(1),))
    rig.dev_side.x("insert into batch_runs (run_id, kind, trigger, status) values (gen_random_uuid(), 'k', 'manual', 'started')")
    run = rig.verify("--skip", "S2", run_id=rid(4))
    c = by_id(vjson(run.folder))
    assert run.code == 0 and c["D2"]["status"] == "warn" and "unstable:dev_job_running" in c["D2"]["detail"]
    assert rig.verify("--skip", "S2", "--fail-on", "warn", run_id=rid(5)).code == 3  # only warnings, --fail-on warn


def test_a_class_x_row_on_prod_turns_d6_red(rig):
    green_rig(rig, 2)
    rig.prod_side.x("insert into batch_runs (run_id, kind, trigger, status) values (gen_random_uuid(), 'k', 'manual', 'completed')")
    rig.prod_side.x("select pg_stat_force_next_flush()")
    run = rig.verify("--skip", "S2", run_id=rid(6))
    c = by_id(vjson(run.folder))
    assert c["D6"]["status"] == "error" and "batch_runs" in c["D6"]["detail"] and run.code == 4


# --- P1: pull age -------------------------------------------------------------------------------------------------------------------

def test_a_stale_or_missing_pull_state_is_a_warning(rig):
    green_rig(rig, 2)
    rig.pull_state(NOW - dt.timedelta(days=8))
    stale = rig.verify("--skip", "S2", run_id=rid(7))
    p1 = by_id(vjson(stale.folder))["P1"]
    assert stale.code == 0 and p1["status"] == "warn" and "age_d=8.0" in p1["detail"] and "ratings=skip" in p1["detail"]
    assert rig.verify("--skip", "S2", "--fail-on", "warn", run_id=rid(8)).code == 3
    (rig.state / "pull_state.json").unlink()
    missing = by_id(vjson(rig.verify("--skip", "S2", run_id=rid(9)).folder))["P1"]
    assert missing["status"] == "warn" and missing["detail"] == "pull_state=first_pull"


# --- files: LocalTransport against the real gate -----------------------------------------------------------------------------------------

def test_file_drift_missing_changed_and_left_behind(rig):
    green_rig(rig, 4)
    (rig.tree.base / "img0001.jpg").unlink()  # missing on prod
    rig.tree.put("img0002.jpg", b"short")  # size changed
    rig.tree.put("stranger.jpg", b"x")  # prod only: left behind (informational)
    run = rig.verify("--skip", "S2", run_id=rid(10))
    c = by_id(vjson(run.folder))
    assert run.code == 4 and c["F1"]["status"] == "error" and c["F1"]["detail"].startswith("missing=1,size=1")
    assert c["F2"]["status"] == "ok" and c["F2"]["detail"] == "left_behind=1"
    e = drift(run.folder, "files")
    assert e["added"]["ids"] == [uid(1)] and e["overwritten"]["size_changed"]["ids"] == [uid(2)] and e["left_behind"]["names"] == ["stranger.jpg"]
    assert "[FAIL] F1" in run.out and "left behind on prod (removals deferred)" in run.out


def test_a_hard_link_on_prod_is_an_f4_anomaly_with_its_path(rig):
    green_rig(rig, 2)
    os.link(rig.tree.base / "img0001.jpg", rig.tree.base / "linked.jpg")
    run = rig.verify("--skip", "S2", run_id=rid(11))
    c = by_id(vjson(run.folder))
    assert run.code == 4 and c["F4"]["status"] == "error" and c["F4"]["detail"].startswith("anomalies=2") and "hardlinked=2" in c["F4"]["detail"]
    paths = {a["path"] for a in vjson(run.folder)["anomalies"]}
    assert paths == {"img0001.jpg", "linked.jpg"} and "anomaly hardlinked: linked.jpg" in run.out


def test_deep_files_rehashes_and_finds_a_same_size_tampered_file(rig):
    green_rig(rig, 3)
    assert rig.verify("--skip", "S2", "--deep-files", run_id=rid(12)).code == 0  # `hash` also fills the gate's record
    good = by_id(vjson(rig.state / "runs" / rid(12)))
    assert good["F3"]["status"] == "ok" and good["F1"]["status"] == "ok"
    original = (rig.tree.base / "img0002.jpg").read_bytes()
    rig.tree.put("img0002.jpg", b"Z" * len(original))  # same size, other bytes
    plain = rig.verify("--skip", "S2", run_id=rid(13))  # the record no longer matches mtime: the hash is untrusted, F1 only warns
    c = by_id(vjson(plain.folder))
    assert plain.code == 0 and c["F1"]["status"] == "warn" and "unverified=1" in c["F1"]["detail"] and c["F3"]["skipped_reason"] == "deep_files_not_requested"
    deep = rig.verify("--skip", "S2", "--deep-files", run_id=rid(14))
    c = by_id(vjson(deep.folder))
    assert deep.code == 4 and c["F3"]["status"] == "error" and "prod_hash=1" in c["F3"]["detail"] and c["F1"]["status"] == "error"
    assert drift(deep.folder, "files")["overwritten"]["hash_changed"]["ids"] == [uid(2)]


def test_a_dev_file_that_differs_from_its_content_hash_is_found_by_deep_files(rig):
    green_rig(rig, 2)
    (rig.images / "img0001.jpg").write_bytes(b"changed on dev")
    run = rig.verify("--skip", "S2", "--deep-files", run_id=rid(15))
    c = by_id(vjson(run.folder))
    assert c["F3"]["status"] == "error" and "dev_hash=1" in c["F3"]["detail"]


# --- gate facts: disk, containment, backup, bug reports ----------------------------------------------------------------------------------

def test_disk_containment_and_backup_facts_files(rig):
    green_rig(rig, 2)
    rig.facts(disk={"mounts": {"images": {"free_pct": 15}, "database": {"free_pct": 8}}},
              containment=dict(GOOD_CONTAINMENT, read_only_root=False, user="root"))
    rig.backup(age_h=40)
    run = rig.verify("--skip", "S2", run_id=rid(16))
    c = by_id(vjson(run.folder))
    assert c["A3"]["status"] == "error" and "error=database" in c["A3"]["detail"]
    assert c["S8"]["status"] == "error" and "read_only_root_off" in c["S8"]["detail"] and "runs_as_root" in c["S8"]["detail"]
    assert c["S6"]["status"] == "warn" and "too_old" in c["S6"]["detail"]
    assert run.code == 4


def test_missing_facts_files_and_no_backup_are_skipped_with_a_reason(rig):
    green_rig(rig, 2)
    (rig.tree.facts / "disk.json").unlink()
    (rig.tree.facts / "containment.json").unlink()
    for f in rig.tree.backups.iterdir():
        f.unlink()
    run = rig.verify("--skip", "S2", run_id=rid(17))
    c = by_id(vjson(run.folder))
    assert run.code == 0 and (c["A3"]["status"], c["A3"]["skipped_reason"]) == ("skipped", "gate_facts_missing")
    assert c["S8"]["skipped_reason"] == "gate_facts_missing" and c["S6"]["skipped_reason"] == "no_backup"


def test_bug_reports_waiting_on_prod(rig):
    green_rig(rig, 2)
    old = rig.tree.bug / "20260101T000000Z_11111111-1111-1111-1111-111111111111.txt"
    old.write_text("a bug report " + SENT)
    t = NOW.timestamp() - 3600
    os.utime(old, (t, t))
    rig.tree.write_conf(bug_max_total_mb="1", bug_max_file_mb="1")
    run = rig.verify("--skip", "S2", run_id=rid(18))
    c = by_id(vjson(run.folder))
    assert c["P2"]["status"] == "warn" and "waiting=1" in c["P2"]["detail"] and "oldest_h=" in c["P2"]["detail"]
    assert SENT not in (run.folder / "verify.json").read_text() + run.out


# --- A1: growth ---------------------------------------------------------------------------------------------------------------------

def history(state, run_id, at, rows, bytes_=10_000):
    folder = state / "runs" / run_id
    folder.mkdir(parents=True)
    metrics = {f"{t}.rows": 0 for t in ("meme_impressions", "search_history", "search_not_found", "meme_saves")}
    metrics["meme_impressions.rows"] = rows
    (folder / "verify.json").write_text(json.dumps({"run_id": run_id, "ran_at": at, "growth": {"metrics": metrics}}))


def test_a_table_that_grew_tenfold_is_a_growth_alarm_from_the_trend_store(sync_pair, tmp_path, monkeypatch):
    sync_pair.reset()
    r = VRig(sync_pair, tmp_path, monkeypatch, verify_settings={"growth": {"min_daily_rows": 100, "min_samples": 3}})
    try:
        green_rig(r, 2)
        for i, day in enumerate(("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12")):
            history(r.state, f"2026111{i}T000000Z-0000000{i}", f"{day}T12:00:00Z", rows=100 + 10 * i)  # +10 a day
        for n in range(1, 2001):
            imp(r.prod_side, n, 1)  # 2,000 rows by now
        run = r.verify("--skip", "S2", run_id=rid(20), now=dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc))
        a1 = by_id(vjson(run.folder))["A1"]
        assert a1["status"] == "warn" and "alarms=1" in a1["detail"] and run.code == 0
        t = vjson(run.folder)["growth"]["trend"]["meme_impressions.rows"]
        assert t["value"] == 2000 and t["status"] == "warn" and t["baseline_daily"] == 10.0 and t["daily"] > 1500
        assert vjson(run.folder)["growth"]["samples"] == 4
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


# --- --skip, redaction, errors ---------------------------------------------------------------------------------------------------------

def test_skip_lists_the_check_as_skipped_and_an_unknown_id_is_exit_2(rig):
    green_rig(rig, 2)
    run = rig.verify("--skip", "D2,D3", "--skip", "S2", run_id=rid(21))
    c = by_id(vjson(run.folder))
    assert (c["D2"]["status"], c["D2"]["skipped_reason"]) == ("skipped", "skipped_by_flag") and c["D3"]["skipped_reason"] == "skipped_by_flag"
    bad = rig.verify("--skip", "D9", run_id=rid(22))
    assert bad.code == 2 and "unknown checks" in bad.err


def test_no_row_text_reaches_any_output(rig):
    green_rig(rig, 2)
    rig.dev_side.x("update ocr_texts set text = %s where image_id = %s", (SENT, uid(1)))
    rig.prod_side.x("update ocr_texts set text = %s where image_id = %s", ("different " + SENT, uid(1)))
    rig.dev_side.x("insert into description_notes (image_id, text, updated_at) values (%s, %s, '2026-01-01')", (uid(2), SENT))
    sh(rig.prod_side, 1, query=SENT)
    run = rig.verify("--skip", "S2", "--json", str(rig.tmp / "copy.json"), run_id=rid(23))
    assert run.code == 4  # the ocr text differs: a digest difference
    blob = run.out + run.err + "".join(p.read_text() for p in rig.state.rglob("*") if p.is_file() and p.suffix in (".json", ".md"))
    blob += (rig.tmp / "copy.json").read_text()
    assert SENT not in blob and json.loads((rig.tmp / "copy.json").read_text())["run_id"] == rid(23)


def test_a_wrong_environment_handshake_is_exit_2_and_runs_no_check(sync_pair, tmp_path, monkeypatch):
    sync_pair.reset()
    r = VRig(sync_pair, tmp_path, monkeypatch)
    try:
        def bad(*a, **k):
            raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch", [{"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

        monkeypatch.setattr(connect, "handshake", bad)
        run = r.verify(run_id=rid(24))
        assert run.code == 2 and "H4" in run.err and not (run.folder / "verify.json").exists()
        doc = report_of(run.folder)
        assert doc["handshake"][0]["ok"] is False and doc["sections"] == []
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


def test_a_held_lock_is_exit_2(rig):
    from sync.config import exclusive_lock

    with exclusive_lock(rig.settings):
        run = rig.verify(run_id=rid(25))
    assert run.code == 2 and "lock" in run.err


# --- HTTPS checks with a fake http --------------------------------------------------------------------------------------------------------

def test_https_checks_against_a_fake_prod_site(sync_pair, tmp_path, monkeypatch):
    from sync.verify.net import HttpResult

    sync_pair.reset()
    r = VRig(sync_pair, tmp_path, monkeypatch, api_url="https://prod.example.test")
    try:
        green_rig(r, 2)
        caps = {"stage": "prod", "environment": "general", "features": {f: False for f in ("comics", "trends", "ratings", "statistics", "uploads", "history", "admin")}}
        calls = []

        def http(method, url, headers=None, timeout=10.0):
            calls.append((method, url))
            if url.endswith("/capabilities"):
                return HttpResult(200, {}, json.dumps(caps).encode())
            if url.endswith("/health"):
                return HttpResult(200, {}, b'{"backend": true, "database": true}')
            if "q=" in url:
                return HttpResult(422)
            return HttpResult(404)

        run = r.verify("--skip", "S2", run_id=rid(26), http=http)
        c = by_id(vjson(run.folder))
        assert run.code == 0, (run.out, c)
        assert [c[i]["status"] for i in ("S3", "S7", "A4")] == ["ok", "ok", "ok"]
        assert not any(m == "POST" and "bug-reports" in u for m, u in calls)  # no deep-net probe without --deep-net
        caps["features"]["ratings"] = True
        bad = r.verify("--skip", "S2", run_id=rid(27), http=http)
        assert by_id(vjson(bad.folder))["S3"]["status"] == "error" and bad.code == 4
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


# --- the planning part for sync.run ---------------------------------------------------------------------------------------------------------

def test_plan_only_hands_the_drift_section_to_the_sink_and_writes_nothing(rig):
    green_rig(rig, 3)
    rig.dev_side.x("update tags set value = 'x' where image_id = %s", (uid(1),))
    docs = []
    run = rig.verify(run_id=rid(30), sink=docs.append, plan_only=True)
    assert run.code == 0 and len(docs) == 1
    sec = docs[0].sections[0]
    assert sec.name == "verify" and sec.checks == []
    names = {e.name: e for e in sec.entries}
    assert names["images"].overwritten.ids == [uid(1)] and names["files"].unchanged.n == 3
    assert not (rig.state / "runs" / rid(30)).exists()
