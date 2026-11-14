"""P6 unit tests: the check model, every pure check function (thresholds, severities, skipped reasons), the growth trend, the HTTPS checks
with a fake http, the command line rules and the redaction guard. No database, no network, no files outside tmp_path.
The checks over real connections are in tests/integration/sync/test_verify.py."""
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from sync import connect, report
from sync.files import diff as diff_mod
from sync.files import manifest as manifest_mod
from sync.files.transport import GateFailure
from sync.promote import plan as plan_mod
from sync.verify import cli, data, growth, model, net
from sync.verify import files as vfiles
from sync.verify import runtime as rt
from sync.verify.model import ERROR, OK, SKIPPED, WARN, Check

SENT = "SENTINEL-UNIT-TEXT-91aa"
NOW = dt.datetime(2026, 11, 14, 12, 0, 0, tzinfo=dt.timezone.utc)


def at(days_ago=0.0):
    return NOW - dt.timedelta(days=days_ago)


# --- the model --------------------------------------------------------------------------------------------------------------------------

def test_a_check_has_the_documented_fields_and_validates_itself():
    c = model.ok("D2", "error", "new=0,changed=0", "python -m sync.run --env <env>")
    assert c.as_json() == {"id": "D2", "status": "ok", "severity": "error", "detail": "new=0,changed=0",
                           "fix": "python -m sync.run --env <env>", "skipped_reason": None}
    s = model.skipped("A3", "error", "gate_facts_missing")
    assert (s.status, s.skipped_reason, s.detail) == (SKIPPED, "gate_facts_missing", "")
    for bad in (lambda: Check("ZZ", OK, "error"), lambda: Check("D1", "maybe", "error"), lambda: Check("D1", OK, "loud"),
                lambda: Check("D1", SKIPPED, "error"),  # a skipped check needs a reason
                lambda: Check("D1", OK, "error", skipped_reason="x"),  # and only a skipped one has one
                lambda: Check("D1", OK, "error", fix="rm -rf $HOME`id`")):
        with pytest.raises(model.CheckError):
            bad()


def test_detail_is_reduced_to_code_like_tokens_so_row_text_cannot_get_through():
    for text in (SENT, f"text was {SENT}", "has space", "Upper", "a/b", "quote'd", "ok," + "é" * 5):
        assert Check("D1", WARN, "error", text).detail == report.REDACTED
    assert Check("D1", WARN, "error", "new=1,left_behind=2:x-y").detail == "new=1,left_behind=2:x-y"
    assert Check("D1", WARN, "error", "a," * 60).detail == ("a," * 60)[:80]  # a long list of codes is cut, not redacted


def test_fail_follows_the_severity_and_downgrade_only_touches_errors():
    assert model.fail("D2", "error", "x").status == ERROR and model.fail("P1", "warn", "x").status == WARN
    err = model.fail("D2", "error", "new=1")
    d = model.downgrade(err, "unstable:dev_job_running")
    assert d.status == WARN and d.detail == "new=1,unstable:dev_job_running" and d.severity == "error"
    assert model.downgrade(model.ok("D2", "error"), "x").status == OK


def test_exit_codes_clean_4_for_errors_and_3_only_for_warnings_with_fail_on_warn():
    clean = [model.ok("D1", "error"), model.skipped("A3", "error", "r")]
    warn = clean + [model.fail("P1", "warn", "stale")]
    error = warn + [model.fail("D2", "error", "new=1")]
    assert [model.exit_code(c) for c in (clean, warn, error)] == [0, 0, 4]
    assert [model.exit_code(c, True) for c in (clean, warn, error)] == [0, 3, 4]  # an error wins over --fail-on warn
    assert model.summarize(error) == {"ok": 1, "warn": 1, "error": 1, "skipped": 1}


def test_guarded_turns_failures_into_checks_without_a_message():
    def raiser(exc):
        def fn():
            raise exc
        return fn

    no_priv = connect.DbError("42501", kind="InsufficientPrivilege")
    assert model.guarded("S2", "error", raiser(no_priv))[0].skipped_reason == "no_privilege"
    other = model.guarded("S5", "error", raiser(connect.DbError("57014", kind="QueryCanceled")))[0]
    assert (other.status, other.detail) == (ERROR, "db_error:57014")
    gate = model.guarded("A3", "error", raiser(GateFailure("ssh_failed", f"message with {SENT}")))[0]
    assert gate.status == SKIPPED and gate.skipped_reason == "gate_ssh_failed"
    boom = model.guarded("S5", "warn", raiser(KeyError(SENT)))[0]
    assert (boom.status, boom.detail) == (WARN, "crashed:keyerror") and SENT not in json.dumps(boom.as_json())
    assert [c.id for c in model.guarded("D1", "error", lambda: [model.ok("D1", "error"), model.ok("D2", "error")])] == ["D1", "D2"]


# --- growth trend (A1) ----------------------------------------------------------------------------------------------------------------------

CFG = growth.config(lambda path, default: default)


def hist(*rows, metric="meme_impressions.rows"):
    return [{"run_id": f"r{i}", "at": at(len(rows) - i), "metrics": {metric: v}, "facts": {}} for i, v in enumerate(rows)]


def test_growth_alarm_is_five_times_the_trailing_median_above_the_floor():
    cfg = dict(CFG, min_daily_rows=100)
    quiet = growth.evaluate("meme_impressions.rows", hist(1000, 1010, 1020, 1030), NOW, 1040, cfg)
    assert quiet["status"] == "ok" and quiet["baseline_daily"] == 10.0
    burst = growth.evaluate("meme_impressions.rows", hist(1000, 1010, 1020, 1030), NOW, 2030, cfg)
    assert burst["status"] == "warn" and burst["daily"] == 1000.0
    # at exactly five times the median it is not yet an alarm; above the floor and over 5x it is
    assert growth.evaluate("meme_impressions.rows", hist(0, 1000, 2000, 3000), NOW, 8000, cfg)["status"] == "ok"
    assert growth.evaluate("meme_impressions.rows", hist(0, 1000, 2000, 3000), NOW, 8100, cfg)["status"] == "warn"
    # below the floor a tenfold jump is not an alarm
    assert growth.evaluate("meme_impressions.rows", hist(10, 11, 12, 13), NOW, 140, dict(CFG))["status"] == "ok"


def test_growth_is_building_until_min_samples_and_the_hard_ceiling_is_always_an_error():
    assert growth.evaluate("m.rows", hist(1, 2), NOW, 10 ** 6, CFG)["status"] == "building"
    assert growth.evaluate("m.rows", [], NOW, 5, CFG) == {"value": 5, "daily": None, "baseline_daily": None, "status": "building"}
    assert growth.evaluate("m.rows", [], NOW, CFG["hard_rows"] + 1, CFG)["status"] == "error"
    assert growth.evaluate("m.bytes", [], NOW, CFG["hard_bytes"] + 1, CFG)["status"] == "error"


def test_two_runs_a_minute_apart_do_not_blow_a_rate_up_and_the_window_is_seven_days():
    h = [{"run_id": "a", "at": NOW - dt.timedelta(minutes=1), "metrics": {"m.rows": 0}, "facts": {}}]
    assert growth.evaluate("m.rows", h, NOW, 10, CFG)["daily"] == 240.0  # 10 rows per 1/24 day, not per minute
    old = [{"run_id": f"o{i}", "at": at(20 - i), "metrics": {"m.rows": 1000 * i}, "facts": {}} for i in range(4)]  # a big old rate, outside the window
    recent = [{"run_id": f"n{i}", "at": at(3 - i), "metrics": {"m.rows": 5000 + 10 * i}, "facts": {}} for i in range(4)]
    ev = growth.evaluate("m.rows", old + recent, NOW, 5035, dict(CFG, min_daily_rows=0))
    assert ev["baseline_daily"] == 10.0  # the median of the trailing 7 days only


def test_load_history_reads_the_last_30_verify_json_files_and_ignores_the_rest(tmp_path):
    for i in range(35):
        folder = tmp_path / "runs" / f"2026111{i % 10}T0000{i:02d}Z-{i:08x}"
        folder.mkdir(parents=True)
        (folder / "verify.json").write_text(json.dumps({"run_id": f"r{i:02d}", "ran_at": f"2026-10-{(i % 28) + 1:02d}T00:00:00Z",
                                                        "growth": {"metrics": {"a.rows": i, "bad": "x"}, "facts": {"backup_size": 5}}}))
    (tmp_path / "runs" / "junk").mkdir()
    (tmp_path / "runs" / "junk" / "verify.json").write_text("{not json")
    (tmp_path / "runs" / "norun").mkdir()
    (tmp_path / "runs" / "norun" / "verify.json").write_text(json.dumps({"ran_at": "garbage"}))
    h = growth.load_history(tmp_path)
    assert len(h) == 30 and h == sorted(h, key=lambda s: (s["at"], s["run_id"])) and all(set(s["metrics"]) == {"a.rows"} for s in h)
    assert growth.load_history(tmp_path, exclude_run="r34") != h and growth.load_history(tmp_path / "missing") == []


def test_check_a1_levels():
    mk = lambda **m: {"samples": 5, "trend": {k: {"status": v} for k, v in m.items()}}  # noqa: E731
    assert model.skipped("A1", "warn", "no_metrics").skipped_reason == "no_metrics" and rt.check_a1(mk()).skipped_reason == "no_metrics"
    assert rt.check_a1(mk(a="ok", b="building")).status == OK
    assert "building" in rt.check_a1(mk(a="building")).detail
    warn = rt.check_a1(mk(a="ok", b="warn"))
    assert warn.status == WARN and "alarms=1" in warn.detail
    assert rt.check_a1(mk(a="error", b="warn")).status == ERROR


# --- data checks (D1 to D6) -----------------------------------------------------------------------------------------------------------------

def facts(alembic=("head1",), major=16, revision="abc", ext=(("pg_trgm", "1.6"), ("vector", "0.8.0"))):
    return {"alembic": list(alembic), "major": major, "extensions": [list(e) for e in ext], "revision": revision}


def test_d1_compares_schema_postgres_extensions_and_revision():
    assert data.check_d1(facts(), facts(), "abc").status == OK
    assert data.check_d1(facts(), facts(revision="ABC "), " abc").status == OK
    c = data.check_d1(facts(), facts(alembic=("head2",), major=17, ext=(("vector", "0.9"),), revision=None), "abc")
    assert c.status == ERROR and c.detail == "alembic_differs,major_differs,ext_differs,revision_unset"
    assert data.check_d1(facts(), facts(revision="def"), "abc").detail == "revision_mismatch"
    assert data.check_d1(facts(), facts(), None).detail == "dev_git_sha_unknown"


def plan_with(added=(), over=None, left=(), unchanged=(), floor=(), globals_=None):
    e = report.Entry("images", "images", added=report.IdSet(ids=list(added)), overwritten=report.Overwritten(ids=[], groups=over or {}),
                     unchanged=report.IdSet(ids=list(unchanged)), left_behind=report.LeftBehind(ids=list(left)),
                     skipped={"missing_required_table": report.IdSet(ids=list(floor))} if floor else {})
    return SimpleNamespace(section=SimpleNamespace(entries=[e]), left_behind=list(left), globals=globals_ if globals_ is not None else {})


def test_d2_new_or_changed_is_an_error_left_behind_is_informational_and_the_floor_is_only_counted():
    assert data.check_d2(plan_with(unchanged=[1, 2])).status == OK
    left = data.check_d2(plan_with(left=[3, 4], unchanged=[1]))
    assert left.status == OK and left.detail == "new=0,changed=0,left_behind=2"  # decision 27: never an error
    assert data.check_d2(plan_with(floor=[9])).status == OK and "floor_skipped=1" in data.check_d2(plan_with(floor=[9])).detail
    new, changed = data.check_d2(plan_with(added=[5])), data.check_d2(plan_with(over={6: ["tags", "ocr"]}))
    assert (new.status, new.detail) == (ERROR, "new=1,changed=0,left_behind=0") and changed.detail.startswith("new=0,changed=1")
    unstable = data.check_d2(plan_with(added=[5]), unstable=True)
    assert unstable.status == WARN and "unstable:dev_job_running" in unstable.detail


def test_d3_row_counts_per_table():
    assert data.check_d3({"tags": 4, "ocr_texts": 2}, {"tags": 4, "ocr_texts": 2}).status == OK
    c = data.check_d3({"tags": 4, "ocr_texts": 2}, {"tags": 3, "ocr_texts": 2})
    assert c.status == ERROR and c.detail == "tables=1,tags"
    assert data.check_d3({"tags": 4}, {}, unstable=True).status == WARN


def test_d4_global_sets_missing_or_differing_are_errors_left_behind_is_not():
    g = {"concepts": {"added": [], "overwritten": [], "left_behind": [1, 2], "unchanged": 3}}
    assert data.check_d4(plan_with(globals_=g)).detail == "missing=0,differing=0,left_behind=2" and data.check_d4(plan_with(globals_=g)).status == OK
    g["concepts"]["added"], g["concepts"]["overwritten"] = [7], [8]
    c = data.check_d4(plan_with(globals_=g))
    assert c.status == ERROR and c.detail == "missing=1,differing=1,left_behind=2"
    assert data.check_d4(plan_with(globals_={})).skipped_reason == "no_global_tables"


def test_d5_is_informational_and_d6_flags_every_class_x_table_with_rows():
    d5 = data.check_d5(2, {"tags": 3, "ocr_texts": 0})
    assert (d5.status, d5.severity, d5.detail) == (OK, "info", "left_behind_images=2,rows=3,tables=1")
    assert data.check_d6({"comics": 0, "batch_runs": 0}).status == OK
    d6 = data.check_d6({"comics": 0, "batch_runs": 4, "tmp_clusters": 1})
    assert d6.status == ERROR and d6.detail == "tables=2,batch_runs,tmp_clusters"


def test_data_checks_honour_skip_and_never_raise():
    f = data.DataFacts(plan=plan_with(unchanged=[1]), schema_dev=facts(), schema_prod=facts(), x_rows={})
    out = {c.id: c for c in data.data_checks(f, "abc", {"D3", "D6"})}
    assert out["D3"].skipped_reason == out["D6"].skipped_reason == "skipped_by_flag" and out["D1"].status == OK and out["D5"].severity == "info"
    broken = data.DataFacts(plan=None, schema_dev=facts(), schema_prod=facts(), x_rows={})
    out = {c.id: c for c in data.data_checks(broken, "abc", set())}
    assert out["D2"].detail == "crashed:attributeerror" and out["D1"].status == OK


# --- file checks (F1 to F4), bug reports --------------------------------------------------------------------------------------------------

def mf(files):
    m = manifest_mod.Manifest()
    for i, (name, size, sha) in enumerate(files):
        m.entries[name] = manifest_mod.ManifestEntry(str(i), name, size, sha)
    return m


def remote(*entries):
    return [diff_mod.RemoteEntry(*e) for e in entries]


def test_f1_f2_over_the_diff():
    m = mf([("a.jpg", 5, "a" * 64), ("b.jpg", 5, "b" * 64), ("c.jpg", 5, "c" * 64), ("d.jpg", 5, "d" * 64)])
    r = remote(("f", 5, "a" * 64, 1, 0, "644", "a.jpg"), ("f", 9, "b" * 64, 1, 0, "644", "b.jpg"), ("f", 5, "x" * 64, 1, 0, "644", "c.jpg"),
               ("f", 5, "", 1, 0, "644", "d.jpg"), ("f", 5, "", 1, 0, "644", "extra.jpg"))
    d = diff_mod.diff_manifest(m, r)
    f1 = vfiles.check_f1(d, m)
    assert f1.status == ERROR and f1.detail == "missing=0,size=1,hash=1,unverified=1,left_out=0"
    assert vfiles.check_f2(d).detail == "left_behind=1" and vfiles.check_f2(d).status == OK and vfiles.check_f2(d).severity == "info"
    only_unverified = diff_mod.diff_manifest(mf([("d.jpg", 5, "d" * 64)]), remote(("f", 5, "", 1, 0, "644", "d.jpg")))
    assert vfiles.check_f1(only_unverified, m).status == WARN
    assert vfiles.check_f1(diff_mod.diff_manifest(m, remote()), m).detail.startswith("missing=4")


def test_f3_needs_deep_files_and_counts_prod_and_dev_problems():
    m = mf([("a.jpg", 5, "a" * 64)])
    ff = vfiles.FileFacts(manifest=m, diff=diff_mod.diff_manifest(m, remote(("f", 5, "a" * 64, 1, 0, "644", "a.jpg"))), remote=[])
    assert vfiles.check_f3(ff).skipped_reason == "deep_files_not_requested"
    ff.deep = True
    assert vfiles.check_f3(ff).status == OK
    ff.dev_hash_mismatch, ff.deep_missing = 1, 2
    c = vfiles.check_f3(ff)
    assert c.status == ERROR and "dev_hash=1" in c.detail and "prod_missing=2" in c.detail


def test_f4_classifies_anomalies_by_type_links_mode_and_owner():
    r = remote(("f", 1, "", 1, 1000, "644", "ok.jpg"), ("l", 1, "", 1, 1000, "777", "sym.jpg"), ("f", 1, "", 2, 1000, "644", "hard.jpg"),
               ("f", 1, "", 1, 1000, "666", "mode.jpg"), ("f", 1, "", 1, 0, "644", "owner.jpg"), ("d", 1, "", 1, 1000, "755", "sub"))
    an = vfiles.classify_remote(r, ("644",), 1000)
    assert dict((p, why) for p, why in an) == {"sym.jpg": "not_regular", "hard.jpg": "hardlinked", "mode.jpg": "mode", "owner.jpg": "owner", "sub": "not_regular"}
    assert vfiles.classify_remote(r, None, None) == [("hard.jpg", "hardlinked"), ("sub", "not_regular"), ("sym.jpg", "not_regular")]
    c = vfiles.check_f4(an)
    assert c.status == ERROR and c.detail.startswith("anomalies=5") and "not_regular=2" in c.detail
    assert vfiles.check_f4([]).status == OK
    hostile = vfiles.classify_remote([diff_mod.RemoteEntry("invalid", 1, "", 1, 0, "644", "bad\nname", True)], ("644",), None)
    assert hostile[0][1] == "invalid_name" and "\n" not in hostile[0][0]  # the path is made report-safe


def pull_list(*names, anomaly=False):
    out = b"".join(f"f\t{size}\t{'a' * 64}\t{n}".encode() + b"\x00" for n, size in names)
    return out + (b"anomaly:name\t1\t\tweird\x00" if anomaly else b"")


UUID = "11111111-2222-3333-4444-555555555555"


def test_p2_and_a2_bug_reports():
    f = vfiles.bug_facts(pull_list((f"20261113T120000Z_{UUID}.txt", 100), (f"20261110T000000Z_{UUID.replace('1', '2')}.txt", 50)), NOW)
    assert f["files"] == 2 and f["bytes"] == 150 and f["anomalies"] == 0 and f["oldest_age_h"] == 108.0
    assert vfiles.check_p2(f).status == OK and vfiles.check_p2(f).severity == "info"
    old = dict(f, oldest_age_h=24 * 8)
    assert vfiles.check_p2(old).status == WARN and vfiles.check_p2(dict(f, anomalies=1)).status == WARN
    assert vfiles.bug_facts(pull_list(anomaly=True), NOW)["anomalies"] == 1
    assert vfiles.check_a2(f, 200, 10 ** 6).status == OK
    assert vfiles.check_a2(dict(f, files=160), 200, 10 ** 6).status == WARN  # 80 % of the file cap
    assert vfiles.check_a2(dict(f, bytes=10 ** 6), 200, 10 ** 6).status == ERROR  # at the cap
    assert vfiles.check_a2(dict(f, files=159), 200, 10 ** 6).status == OK


# --- runtime checks -------------------------------------------------------------------------------------------------------------------------

def test_p1_pull_freshness_is_a_warning_never_an_error():
    doc = {"finished_at": "2026-11-13T12:00:00Z", "run_id": "20261113T120000Z-0000000a", "ratings": {"mode": "skip"}, "bug_reports": {"status": "done"}}
    ok_ = rt.check_p1(doc, "found", NOW, 7)
    assert ok_.status == OK and ok_.detail == "age_d=1.0,ratings=skip,bug=done,run=20261113t120000z-0000000a"
    stale = rt.check_p1(dict(doc, finished_at="2026-11-01T12:00:00Z"), "found", NOW, 7)
    assert stale.status == WARN and stale.severity == "warn" and "age_d=13.0" in stale.detail and stale.detail.endswith("stale")
    assert rt.check_p1(None, "first_pull", NOW).detail == "pull_state=first_pull"
    assert rt.check_p1(dict(doc, finished_at="never"), "found", NOW).detail == "finished_at_unreadable"
    assert rt.check_p1(dict(doc, finished_at="2026-11-07T12:00:00Z"), "found", NOW, 7).status == OK  # exactly 7 days is not stale yet


def test_a3_disk_thresholds_per_mount_and_unusable_facts():
    mounts = lambda **m: {"mounts": {k: {"free_pct": v} for k, v in m.items()}}  # noqa: E731
    assert rt.check_a3(mounts(images=60, db=45)).status == OK
    assert rt.check_a3(mounts(images=60, db=20)).status == OK  # 20 % free is the warn boundary: warn below it
    warn = rt.check_a3(mounts(images=19.9, db=80))
    assert warn.status == WARN and warn.detail == "mounts=2,min_free_pct=19.9,low=images"
    err = rt.check_a3(mounts(images=9.9, logs=15))
    assert err.status == ERROR and err.detail.endswith("error=images")
    assert rt.check_a3({"images": {"free_pct": 50}}).status == OK  # the flat shape of the gate test too
    assert rt.check_a3({"x": {"free_bytes": 9, "total_bytes": 100}}).status == ERROR  # 9 % free, computed
    assert rt.check_a3({"x": {"free_bytes": 10, "total_bytes": 100}}).status == WARN  # exactly 10 % is the error boundary: error below it
    for bad in (None, {}, {"mounts": {}}, {"x": "full"}, {"x": {"nothing": 1}}, []):
        assert rt.check_a3(bad).skipped_reason == "facts_unusable"
    assert rt.check_a3(mounts(images=15), warn_pct=10, error_pct=5).status == OK  # thresholds come from sync.verify.disk.*


def test_s8_containment_facts():
    good = {"read_only_root": True, "cap_drop": ["ALL"], "no_new_privileges": True, "user": "1000:1001", "base_path_read_only": True,
            "published_ports": ["127.0.0.1:8000->8000"]}
    assert rt.check_s8(good).status == OK
    for key, code in (("read_only_root", "read_only_root_off"), ("no_new_privileges", "no_new_privileges_off"),
                      ("base_path_read_only", "base_path_read_only_off")):
        assert code in rt.check_s8(dict(good, **{key: False})).detail
    c = rt.check_s8(dict(good, cap_drop=["NET_RAW"], user="root", published_ports=["0.0.0.0:8000->8000"]))
    assert c.status == ERROR and {"cap_drop_all_missing", "runs_as_root", "port_not_loopback"} <= set(c.detail.split(","))
    assert "read_only_root_missing" in rt.check_s8({k: v for k, v in good.items() if k != "read_only_root"}).detail
    assert rt.check_s8({"user": "0:0", **{k: v for k, v in good.items() if k != "user"}}).detail == "runs_as_root"
    for bad in (None, {}, "x"):
        assert rt.check_s8(bad).skipped_reason == "facts_unusable"


def test_s6_backup_age_size_and_restorability():
    info = {"name": "x.dump", "size": 1000, "mtime": NOW.timestamp() - 3600, "meta": {"pg_restore_list": "ok"}}
    assert rt.check_s6(info, NOW).status == OK and rt.check_s6(None, NOW).skipped_reason == "no_backup"
    assert "too_old" in rt.check_s6(dict(info, mtime=NOW.timestamp() - 37 * 3600), NOW, 36).detail
    assert rt.check_s6(dict(info, mtime=NOW.timestamp() - 35 * 3600), NOW, 36).status == OK
    assert "empty" in rt.check_s6(dict(info, size=0), NOW).detail
    assert "restore_list_not_ok" in rt.check_s6(dict(info, meta={"pg_restore_list": "failed"}), NOW).detail
    assert "restore_check_missing" in rt.check_s6(dict(info, meta=None), NOW).detail
    shrunk = rt.check_s6(dict(info, size=400), NOW, prev={"size": 1000, "rows": 100}, rows=100)
    assert shrunk.status == WARN and "smaller_than_rows_lost" in shrunk.detail
    assert rt.check_s6(dict(info, size=400), NOW, prev={"size": 1000, "rows": 100}, rows=40).status == OK  # it lost the rows it shrank by
    assert rt.check_s6(dict(info, mtime=None), NOW).status == WARN


def test_s5_connections_and_indexes():
    idx = {"i_hnsw": True, "i_gin": True}
    assert rt.check_s5(10, 100, idx, idx).status == OK
    assert rt.check_s5(80, 100, idx, idx).status == WARN and rt.check_s5(95, 100, idx, idx).status == ERROR
    missing = rt.check_s5(10, 100, idx, {"i_gin": True})
    assert missing.status == ERROR and "indexes_missing=1" in missing.detail
    assert rt.check_s5(10, 100, idx, {"i_gin": True}, seeding=True).status == OK  # while `seeding` the missing indexes are expected
    assert "indexes_invalid=1" in rt.check_s5(10, 100, idx, {"i_hnsw": False, "i_gin": True}, seeding=True).detail


def test_s4_s4b_gate():
    reply = {"version": "1", "devices": {"base": 7, "work": 7, "incoming": 7}}
    assert rt.check_s4(reply, "1").status == OK
    assert rt.check_s4(dict(reply, version="2"), "1").detail == "gate_version_differs"
    assert rt.check_s4({"version": "1", "devices": {"base": 1, "work": 2}}, "1").detail == "device_mismatch"
    assert rt.check_s4b(None).skipped_reason == "account_probe_unavailable"
    good = {"upload_refuses_verb": True, "upload_refuses_shell": True, "upload_chrooted": True, "verb_account_no_sftp": True}
    assert rt.check_s4b(good).status == OK
    assert rt.check_s4b(dict(good, upload_chrooted=False)).status == ERROR


def role_facts(**over):
    base = {"attrs": {"super": False, "createrole": False, "createdb": False, "replication": False, "bypassrls": False},
            "schema_create": False, "table_priv": {}}
    from sync import registry
    from sync.model import Klass

    for t in registry.names():
        k = registry.get(t).klass
        base["table_priv"][t] = {"exists": True, "SELECT": k is not Klass.X, "INSERT": k is Klass.P, "UPDATE": k is Klass.P,
                                 "DELETE": k is Klass.P, "TRUNCATE": False}
    base["table_priv"]["alembic_version"] = {"exists": True, "SELECT": False, "INSERT": False, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
    base.update(over)
    return base


def test_s1_backend_app_grants_and_the_check_roll_up():
    assert rt.evaluate_app(role_facts()) == []
    f = role_facts()
    f["table_priv"]["images"]["INSERT"] = True  # a class D table the backend can write
    f["table_priv"]["meme_saves"]["DELETE"] = False
    f["table_priv"]["comics"]["SELECT"] = True
    f["table_priv"]["alembic_version"]["SELECT"] = True
    f["attrs"]["super"] = True
    p = rt.evaluate_app(dict(f, schema_create=True))
    assert {"role_attr_super", "schema_create", "write_on_class_d=1", "missing_write_on_class_p=1", "privilege_on_class_x=1", "alembic_version_access"} <= set(p)
    assert rt.check_s1([], [], []).status == OK and rt.check_s1([], None, []).detail == "roles=writer.app"
    bad = rt.check_s1(["write_on_class_p_or_x=2"], [], [])
    assert bad.status == ERROR and bad.detail.startswith("roles_bad=writer,first=write_on_class_p_or_x") and "2" in bad.detail
    assert rt.check_s1(None, None, None).skipped_reason == "no_connections"


def test_s2_roles_pg_hba():
    clean = {"public_connect": False, "trust_network": 0, "app_super": False, "app_create": False}
    assert rt.check_s2(clean).status == OK and rt.check_s2(dict(clean, trust_network=None)).detail == "pg_hba=unreadable"
    c = rt.check_s2({"public_connect": True, "trust_network": 2, "app_super": True, "app_create": True})
    assert c.status == ERROR and c.detail == "public_connect,trust_network=2,app_superuser,app_can_create"


# --- HTTPS checks (S3, S7, A4) with a fake http ---------------------------------------------------------------------------------------------

def fake_http(routes, default=404):
    def http(method, url, headers=None, timeout=10.0):
        for key, value in routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        return net.HttpResult(default)

    return http


FEATURES_OFF = {f: False for f in net.STAGE_FEATURES}


def test_s3_health_and_capabilities():
    health = net.HttpResult(200, {}, b'{"backend": true, "database": true}')
    caps = {"stage": "prod", "environment": "general", "features": FEATURES_OFF}
    assert net.check_s3(health, caps, "general").status == OK
    bad = net.check_s3(net.HttpResult(200, {}, b"asyncpg.exceptions.ConnectionDoesNotExistError at 10.0.0.5"), dict(caps, stage="dev"), "general")
    assert bad.status == ERROR and {"health_leaks_driver", "stage_not_prod"} <= set(bad.detail.split(","))
    assert "environment_mismatch" in net.check_s3(health, dict(caps, environment="metal"), "general").detail
    on = net.check_s3(health, dict(caps, features=dict(FEATURES_OFF, comics=True, admin=True)), "general")
    assert on.detail == "features_on=admin.comics"
    assert "features_absent" in net.check_s3(health, {"stage": "prod", "environment": "general"}, "general").detail
    assert "capabilities_unavailable" in net.check_s3(health, None, "general").detail and "health_http_500" in net.check_s3(net.HttpResult(500), caps, "general").detail
    assert net.check_s3(None, None, "general").skipped_reason == "api_unreachable"


def test_s7_every_sampled_route_must_answer_404():
    all_404 = {rid: 404 for rid in net.STAGE_ROUTES}
    assert net.check_s7(all_404).status == OK
    c = net.check_s7(dict(all_404, docs=200, uploads=405))
    assert c.status == ERROR and c.detail == "not_404=2,docs:200.uploads:405"
    assert net.check_s7({rid: None for rid in net.STAGE_ROUTES}).skipped_reason == "api_unreachable" and net.check_s7({}).skipped_reason == "api_unreachable"
    http = fake_http({})
    assert set(net.gather_s7(http, "https://x.test")) == set(net.STAGE_ROUTES) and "ratings" not in net.gather_s7(http, "https://x.test", ("ratings",))
    down = fake_http({"": net.NetUnavailable("unreachable")})
    assert set(net.gather_s7(down, "https://x.test").values()) == {None}


def test_a4_abuse_caps_oversize_q_and_with_deep_net_the_declared_length_and_a_rate_limit():
    assert net.check_a4({"q_status": 422}).status == OK
    assert net.check_a4({"q_status": 200}).detail == "oversize_q_200" and net.check_a4({"q_status": None}).skipped_reason == "api_unreachable"
    deep_ok = {"q_status": 422, "declared_status": 413, "burst": {"sent": 40, "limited": True}}
    assert net.check_a4(deep_ok).status == OK
    bad = net.check_a4({"q_status": 422, "declared_status": 200, "burst": {"sent": 40, "limited": False}})
    assert bad.status == ERROR and bad.detail == "declared_length_accepted,no_rate_limit_seen"
    limited = fake_http({"/health": net.HttpResult(200, {"x-ratelimit-remaining": "0"}), "q=": net.HttpResult(422), "bug-reports": net.HttpResult(413)})
    got = net.gather_a4(limited, "https://x.test", deep=True, bug_cap_bytes=10, burst=3)
    assert got == {"q_status": 422, "declared_status": 413, "burst": {"sent": 3, "limited": True}}
    plain = net.gather_a4(limited, "https://x.test", deep=False, bug_cap_bytes=10)
    assert set(plain) == {"q_status"}


# --- the command line ----------------------------------------------------------------------------------------------------------------------

def test_flags_and_validation():
    p = cli.build_parser()
    a = p.parse_args(["--env", "general", "--skip", "D2,D3", "--skip", "S2", "--fail-on", "warn", "--deep-files", "--deep-net", "--top", "5",
                      "--json", "-", "--run-id", "20261114T120000Z-0000000a"])
    cli.validate_args(a)
    assert cli.skipped_ids(a) == {"D2", "D3", "S2"} and a.fail_on == "warn" and a.deep_files and a.deep_net and a.json_path == "-"
    for argv, text in ((["--skip", "D9"], "unknown checks: D9"), (["--top", "0"], "--top"), (["--run-id", "x"], "--run-id")):
        with pytest.raises(cli.UsageProblem, match=text):
            cli.validate_args(p.parse_args(["--env", "general", *argv]))
    for flag in ("--apply", "--yes", "--plan-hash", "--retire", "--prod-url"):
        with pytest.raises(SystemExit):
            p.parse_args(["--env", "general", flag])
    assert set(model.CHECK_IDS) >= {"S0", "D1", "D6", "F1", "F4", "P1", "P2", "A1", "A4", "S1", "S8"}


def test_the_collector_records_skips_and_orders_by_the_design():
    cx = cli.Collector({"D2"})
    cx.run("S1", "error", lambda: model.ok("S1", "error"))
    cx.run("D2", "error", lambda: pytest.fail("a skipped check must not run"))
    cx.run("D1", "error", lambda: (_ for _ in ()).throw(RuntimeError(SENT)))
    cx.mark_skipped(("F1", "D2"), "error", "no_gate_transport")
    ordered = {c.id: c for c in cx.ordered}
    assert [c.id for c in cx.ordered] == ["D1", "D2", "F1", "S1"]
    assert ordered["D2"].skipped_reason == "skipped_by_flag" and ordered["F1"].skipped_reason == "no_gate_transport"
    assert ordered["D1"].detail == "crashed:runtimeerror" and SENT not in json.dumps([c.as_json() for c in cx.ordered])


def test_verify_json_carries_only_codes_counts_and_anomaly_paths():
    checks = [model.ok("S0", "error", "handshake_steps=9"), model.fail("P1", "warn", "age_d=9.0,stale")]
    e = report.Entry("images", "images", added=report.IdSet(ids=[1, 2]))
    ff = SimpleNamespace(anomalies=[("linked.jpg", "hardlinked")])
    doc = cli._verify_json("general", "20261114T120000Z-0000000a", NOW, checks, {"samples": 0, "metrics": {}, "trend": {}, "facts": {}}, [e], ff, 0,
                           [{"step": "H1", "ok": True, "detail": f"ok {SENT}"}])
    assert set(doc) >= {"env", "ran_at", "run_id", "checks", "growth", "summary"} and doc["ran_at"] == "2026-11-14T12:00:00Z"
    assert doc["summary"] == {"ok": 1, "warn": 1, "error": 0, "skipped": 0, "exit_code": 0, "drift": {"images": {"added": 2, "overwritten": 0, "unchanged": 0, "left_behind": 0}}}
    assert doc["anomalies"] == [{"path": "linked.jpg", "reason": "hardlinked"}] and SENT not in json.dumps(doc)
    assert set(doc["checks"][0]) == {"id", "status", "severity", "detail", "fix", "skipped_reason"}


def test_plan_helpers_are_the_promote_plan_over_the_whole_set():
    # the drift is computed by P2's own planner with no files gate: a new image is `added`, not `waiting` or `files_gate_miss`
    assert plan_mod.ENTRY == "images" and data.SECTION == "verify" and data.TOOL == "sync.verify"
