"""Verbs of deploy/prod/memesync-gate.py against a temp directory tree (no ssh, no shell)."""
import ast
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time

import pytest

from tests.sync.gate_helpers import GATE_PATH, Tree, gate, make_tar, sha, work_names

POSIX = hasattr(os, "getuid")


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path)


def symlink_or_skip(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")


# ------------------------------------------------------------ handshake


def test_handshake_reports_environment_version_devices_free_space_and_caps(tree):
    r = tree.run("handshake")
    assert r.code == 0, r.err
    data = r.json()
    assert data["environment"] == "general" and data["version"] == gate.GATE_VERSION
    assert data["caps"] == {"max_member_mb": 200.0, "max_members": 5000, "max_batch_mb": 600.0, "free_floor_pct": 10.0,
                            "replace_day_min": 200, "replace_day_pct": 5.0}
    assert data["devices"]["base"] == data["devices"]["work"]
    assert data["free"]["files"]["total_bytes"] > 0


def test_handshake_exits_3_below_the_free_space_floor(tree):
    tree.write_conf(free_floor_pct="100")
    r = tree.run("handshake")
    assert r.code == 3 and "free_space_below_floor" in r.err


def test_handshake_exits_3_when_work_and_images_are_on_different_devices(tree, monkeypatch):
    real = gate.device_of
    monkeypatch.setattr(gate, "device_of", lambda d: 99 if d.path == str(tree.work) else real(d))
    r = tree.run("handshake")
    assert r.code == 3 and "device_mismatch" in r.err


def test_handshake_exits_3_for_a_missing_directory(tree):
    tree.write_conf(work_dir=tree.root / "nowhere")
    assert tree.run("handshake").code == 3


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_handshake_probe_needs_the_images_directory_readable_by_other(tree):
    tree.write_conf(probe_require_other_read="true")
    tree.base.chmod(0o700)
    assert tree.run("handshake").code == 3
    tree.base.chmod(0o755)
    assert tree.run("handshake").code == 0


# ------------------------------------------------------------ list and hash


def test_list_types_hidden_invalid_and_recorded_hashes(tree):
    tree.promote("l1", {"rec.jpg": b"recorded"})
    tree.put("plain.jpg", b"unrecorded")
    tree.put(".hidden", b"h")
    (tree.base / "subdir").mkdir()
    rows = {r[6]: r for r in tree.run("list").records()}
    assert rows["rec.jpg"][:2] == ["f", "8"] and rows["rec.jpg"][2] == sha(b"recorded") and rows["rec.jpg"][3] == "1"
    assert rows["plain.jpg"][0] == "f" and rows["plain.jpg"][2] == ""  # no record: not trusted
    assert rows[".hidden"][0] == "h" and rows["subdir"][0] == "d"


def test_list_does_not_trust_a_record_after_the_file_changed(tree):
    tree.promote("l2", {"a.jpg": b"old"})
    (tree.base / "a.jpg").write_bytes(b"newer!")
    assert tree.run("list").records()[0][2] == ""


@pytest.mark.skipif(not POSIX, reason="names with control characters need a POSIX filesystem")
def test_list_reports_tab_newline_and_non_utf8_names_as_invalid_entries(tree):
    for raw in (b"tab\tname", b"new\nline", b"bad\xffbyte"):
        with open(os.path.join(os.fsencode(str(tree.base)), raw), "wb") as fh:
            fh.write(b"1")
    rows = tree.run("list").records()
    assert len(rows) == 3 and all(r[0] == "invalid" and len(r) == 7 for r in rows)
    assert all("\t" not in r[6] and "\n" not in r[6] for r in rows)


def test_list_shows_a_symlink_as_a_link_not_a_file(tree, tmp_path):
    (tmp_path / "target.txt").write_text("t")
    symlink_or_skip(tree.base / "ln.jpg", tmp_path / "target.txt")
    assert tree.run("list").records()[0][0] == "l"


@pytest.mark.skipif(not POSIX, reason="hard links")
def test_list_reports_the_link_count(tree):
    tree.put("a.jpg")
    os.link(tree.base / "a.jpg", tree.base / "b.jpg")
    assert {r[6]: r[3] for r in tree.run("list").records()} == {"a.jpg": "2", "b.jpg": "2"}


def test_hash_all_and_named_rewrite_the_record(tree):
    tree.put("a.jpg", b"aa")
    tree.put("b.jpg", b"bb")
    r = tree.run("hash")
    assert r.code == 0 and {x[3]: x[2] for x in r.records()} == {"a.jpg": sha(b"aa"), "b.jpg": sha(b"bb")}
    assert {x[6]: x[2] for x in tree.run("list").records()} == {"a.jpg": sha(b"aa"), "b.jpg": sha(b"bb")}
    (tree.base / "a.jpg").write_bytes(b"changed")
    r = tree.run("hash --names", b"a.jpg\x00gone.jpg\x00")
    assert [x[0] for x in r.records()] == ["ok", "missing"]
    assert {x[6]: x[2] for x in tree.run("list").records()}["a.jpg"] == sha(b"changed")
    assert {x[6]: x[2] for x in tree.run("list").records()}["b.jpg"] == sha(b"bb")  # untouched by a named hash
    (tree.base / "b.jpg").unlink()
    tree.run("hash")
    assert "b.jpg" not in (tree.work / "record.tsv").read_text()  # a full hash forgets files that are gone


def test_a_torn_record_line_is_ignored(tree):
    good = "a.jpg\t%s\t1\t5\n" % sha(b"a")
    (tree.work / "record.tsv").write_bytes((good + "b.jpg\t%s\t1" % sha(b"b")).encode())
    w = gate.Dir.open_root(str(tree.work))
    try:
        assert list(gate.load_record(w)) == ["a.jpg"]
    finally:
        w.close()


def test_record_rewrite_is_atomic_when_the_replace_fails(tree, monkeypatch):
    tree.put("a.jpg", b"a")
    tree.run("hash")
    before = (tree.work / "record.tsv").read_bytes()
    tree.put("b.jpg", b"b")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(gate.os, "replace", boom)
    assert tree.run("hash").code == 1
    monkeypatch.undo()
    assert (tree.work / "record.tsv").read_bytes() == before
    assert sorted(p.name for p in tree.work.iterdir()) == ["gate.lock", "record.tsv"]  # no temp file left


@pytest.mark.skipif(not POSIX, reason="Windows cannot rename over a file another thread has open; the fallback is test-only")
def test_a_reader_never_sees_a_partial_record_while_it_is_rewritten(tree):
    tree.put("a.jpg", b"a")
    tree.run("hash")
    w = gate.Dir.open_root(str(tree.work))
    stop, bad = threading.Event(), []

    def reader():
        while not stop.is_set():
            try:
                text = (tree.work / "record.tsv").read_text()
            except OSError:
                continue  # the replace window on Windows
            if not text.endswith("\n") or text.count("\n") not in (1, 200):
                bad.append(text[:50])
    t = threading.Thread(target=reader)
    t.start()
    try:
        entries = {"n%d.jpg" % i: (sha(b"x"), 1, 1) for i in range(200)}
        for _ in range(30):
            gate.rewrite_record(w, entries)
            gate.rewrite_record(w, {"a.jpg": (sha(b"a"), 1, 1)})
    finally:
        stop.set()
        t.join()
        w.close()
    assert not bad


# ------------------------------------------------------------ commit, replacement cap, lock


def test_commit_new_names_replacements_and_identical_files(tree):
    assert tree.promote("c1", {"a.jpg": b"1", "b.jpg": b"2"}).json()["replaced"] == 0
    r = tree.promote("c2", {"a.jpg": b"1", "b.jpg": b"CHANGED", "c.jpg": b"3"})
    assert r.json() == {"id": "c2", "committed": 2, "replaced": 1, "identical": 1}
    assert (tree.base / "b.jpg").read_bytes() == b"CHANGED"
    assert json.loads((tree.work / "counters.json").read_text())["replaced"] == 1
    rows = {x[6]: x[2] for x in tree.run("list").records()}
    assert rows == {"a.jpg": sha(b"1"), "b.jpg": sha(b"CHANGED"), "c.jpg": sha(b"3")}  # all trusted via the record


def test_commit_never_publishes_over_something_that_is_not_a_regular_file(tree):
    (tree.base / "a.jpg").mkdir()
    r = tree.promote("c3", {"a.jpg": b"1", "z.jpg": b"2"})
    assert r.code == 1 and "destination_not_regular" in r.err
    assert not (tree.base / "z.jpg").exists()  # refused before any rename


def test_replace_cap_trips_before_any_change_and_new_names_are_not_capped(tree):
    tree.write_conf(replace_day_min="2", replace_day_pct="0")
    assert tree.promote("r0", {"n%d.jpg" % i: b"0" for i in range(10)}).code == 0  # ten new names, no cap
    assert tree.promote("r1", {"n0.jpg": b"1", "n1.jpg": b"1"}).code == 0           # two replaced: at the cap
    r = tree.promote("r2", {"n2.jpg": b"1", "n3.jpg": b"1", "fresh.jpg": b"new"})
    assert r.code == 3 and "replace_cap" in r.err
    assert (tree.base / "n2.jpg").read_bytes() == b"0" and not (tree.base / "fresh.jpg").exists()
    assert (tree.work / "r2").exists()  # kept: it can be committed once the owner lifts the cap in gate.conf
    tree.write_conf(replace_day_min="5", replace_day_pct="0")
    assert tree.run("commit r2").code == 0
    assert (tree.base / "fresh.jpg").read_bytes() == b"new"


def test_replace_cap_has_a_percentage_part(tree):
    tree.write_conf(replace_day_min="1", replace_day_pct="50")
    tree.promote("p0", {"a%d.jpg" % i: b"0" for i in range(4)})
    assert tree.promote("p1", {"a0.jpg": b"1", "a1.jpg": b"1"}).code == 0   # 50 % of 4
    assert tree.promote("p2", {"a2.jpg": b"1"}).code == 3


def test_the_replace_counter_starts_over_on_a_new_day(tree):
    (tree.work / "counters.json").write_text(json.dumps({"day": "1999-01-01", "replaced": 10 ** 6}))
    tree.write_conf(replace_day_min="1", replace_day_pct="0")
    tree.promote("d0", {"a.jpg": b"0"})
    assert tree.promote("d1", {"a.jpg": b"1"}).code == 0


def test_a_kill_between_renames_leaves_the_rest_of_the_batch_for_the_next_run(tree, monkeypatch):
    members = {"a.jpg": b"1", "b.jpg": b"2", "c.jpg": b"3"}
    assert tree.receive("k1", members).code == 0
    assert tree.run("verify k1", tree.verify_input(members)).code == 0
    real, calls = gate.Dir.replace_to, []

    def dying(self, name, other, new_name):
        if name == "c.jpg" and self.path.endswith("k1"):
            raise OSError(5, "killed")
        calls.append(name)
        return real(self, name, other, new_name)
    monkeypatch.setattr(gate.Dir, "replace_to", dying)
    assert tree.run("commit k1").code == 1
    monkeypatch.undo()
    assert sorted(p.name for p in tree.base.iterdir()) == ["a.jpg", "b.jpg"]
    rows = {x[6]: x[2] for x in tree.run("list").records()}
    assert rows == {"a.jpg": sha(b"1"), "b.jpg": sha(b"2")}  # recorded one by one, in the finally
    assert tree.run("commit k1").code == 0  # resumes: the two published names are identical, c.jpg moves
    assert (tree.base / "c.jpg").read_bytes() == b"3"


def test_concurrent_commits_are_serialised_by_the_lock(tree):
    batches = {}
    for i in range(4):
        members = {"p%d_%d.jpg" % (i, j): ("%d-%d" % (i, j)).encode() * 100 for j in range(20)}
        batches["w%d" % i] = members
        assert tree.receive("w%d" % i, members).code == 0
        assert tree.run("verify w%d" % i, tree.verify_input(members)).code == 0
    results = {}

    def go(bid):
        results[bid] = tree.run_subprocess("commit " + bid)
    threads = [threading.Thread(target=go, args=(b,)) for b in batches]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.code == 0 for r in results.values()), {b: r.err for b, r in results.items()}
    assert len(list(tree.base.iterdir())) == 80
    record = gate.Dir.open_root(str(tree.work))
    try:
        assert len(gate.load_record(record)) == 80  # no appended line was lost or interleaved
    finally:
        record.close()
    assert not [p for p in tree.work.iterdir() if p.name not in ("gate.lock", "record.tsv")]


def test_a_second_command_waits_then_exits_2_when_the_lock_stays_held(tree):
    tree.write_conf(lock_timeout_s="0.5")
    w = gate.Dir.open_root(str(tree.work))
    try:
        with gate.Lock(w, 5):
            r = tree.run_subprocess("sweep")
            assert r.code == 2 and "lock_busy" in r.err
            assert tree.run_subprocess("list").code == 0  # list is not a locked verb
    finally:
        w.close()
    assert tree.run_subprocess("sweep").code == 0


# ------------------------------------------------------------ receive: free space; sweep


def test_receive_refuses_when_the_batch_would_push_free_space_below_the_floor(tree):
    tree.write_conf(free_floor_pct="99.999")
    tree.upload("f1", make_tar({"a.jpg": b"x" * 1000}))
    r = tree.run("receive f1")
    assert r.code == 3 and "free_space_below_floor" in r.err
    assert work_names(tree) == []


def age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_sweep_removes_only_old_debris_and_never_the_record(tree):
    (tree.work / "old1").mkdir()
    (tree.work / "old1" / "x.jpg").write_bytes(b"x")
    (tree.work / "old1.list").write_text("x")
    (tree.work / "old2.tar").write_bytes(b"x")
    (tree.work / "new1").mkdir()
    (tree.work / "record.tsv.tmp-1-ab").write_text("x")
    (tree.work / "record.tsv").write_text("keep\n")
    (tree.work / "counters.json").write_text("{}")
    (tree.work / "unrelated.dat").write_text("x")
    (tree.incoming / "up_old.tar").write_bytes(b"x")
    (tree.incoming / "up_old.tar.part").write_bytes(b"x")
    (tree.incoming / "up_new.tar").write_bytes(b"x")
    for p in (tree.work / "old1", tree.work / "old1.list", tree.work / "old2.tar", tree.work / "record.tsv.tmp-1-ab", tree.work / "record.tsv",
              tree.work / "counters.json", tree.work / "unrelated.dat", tree.incoming / "up_old.tar", tree.incoming / "up_old.tar.part"):
        age(p, 7)
    age(tree.work / "new1", 5.9)
    (tree.base / "keep.jpg").write_bytes(b"1")
    age(tree.base / "keep.jpg", 100)
    r = tree.run("sweep")
    assert r.code == 0 and r.json() == {"removed": {"work": 4, "incoming": 2}}
    assert sorted(p.name for p in tree.work.iterdir()) == ["counters.json", "gate.lock", "new1", "record.tsv", "unrelated.dat"]
    assert [p.name for p in tree.incoming.iterdir()] == ["up_new.tar"]
    assert (tree.base / "keep.jpg").exists()


def test_sweep_age_is_configurable(tree):
    (tree.incoming / "u.tar").write_bytes(b"x")
    age(tree.incoming / "u.tar", 2)
    assert tree.run("sweep").json()["removed"]["incoming"] == 0
    tree.write_conf(sweep_age_hours="1")
    assert tree.run("sweep").json()["removed"]["incoming"] == 1


def test_sweep_does_not_follow_a_symlink_planted_in_incoming(tree, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.jpg").write_bytes(b"p")
    symlink_or_skip(tree.incoming / "evil", victim)
    if os.utime in os.supports_follow_symlinks:
        t = time.time() - 10 * 3600
        os.utime(tree.incoming / "evil", (t, t), follow_symlinks=False)
    tree.run("sweep")
    assert (victim / "precious.jpg").exists()


# ------------------------------------------------------------ bug reports (P4 verbs)

BUG = "20261112T101010Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ab"


def bug_file(tree, name, data=b"report", hours=1):
    p = tree.bug / name
    p.write_bytes(data)
    age(p, hours)
    return p


def test_pull_list_settled_files_anomalies_and_the_age_rule(tree):
    bug_file(tree, BUG + ".txt", b"hello")
    bug_file(tree, "20261112T101011Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ac.png", b"young", hours=0)  # not settled: silent
    bug_file(tree, "random-name.txt")
    bug_file(tree, ".hiddenfile")
    bug_file(tree, "20261112T101012Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ad.log", b"x" * (11 * 1024 * 1024))
    (tree.bug / "sub").mkdir()
    age(tree.bug / "sub", 1)
    rows = {r[3]: r for r in tree.run("pull-list").records()}
    assert rows[BUG + ".txt"][:3] == ["f", "5", sha(b"hello")]
    assert rows["random-name.txt"][0] == "anomaly:name"
    assert rows[".hiddenfile"][0] == "anomaly:hidden"
    assert rows["sub"][0].startswith("anomaly:not_regular")
    assert rows["20261112T101012Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ad.log"][0] == "anomaly:too_big"
    assert not any("0123456789ac" in k for k in rows)


def test_pull_tar_returns_the_named_files_and_nothing_else(tree):
    bug_file(tree, BUG + ".txt", b"hello")
    other = "20261112T101011Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ac.json"
    bug_file(tree, other, b"{}")
    r = tree.run("pull-tar", (BUG + ".txt\x00").encode())
    assert r.code == 0
    with tarfile.open(fileobj=io.BytesIO(r.out), mode="r:") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == [BUG + ".txt"] and members[0].isreg()
        assert tar.extractfile(members[0]).read() == b"hello"


@pytest.mark.parametrize("name", ["../x", "a/b.txt", BUG.upper() + ".txt", BUG + ".TXT", BUG + ".toolongextension", "x" * 300, BUG, ".hidden", "..\\x", "a\nb.txt",
                                  BUG + ".txt\n", "20261112T101010Z_xyz.txt"])
def test_pull_tar_names_are_validated_against_the_bug_report_pattern(tree, name):
    r = tree.run("pull-tar", name.encode() + b"\x00")
    assert r.code == 2 and r.out == b""


def test_pull_tar_refuses_a_missing_name_a_link_and_the_caps_before_writing(tree, tmp_path):
    assert tree.run("pull-tar", (BUG + ".txt\x00").encode()).code == 1
    (tmp_path / "t").write_text("t")
    symlink_or_skip(tree.bug / (BUG + ".txt"), tmp_path / "t")
    r = tree.run("pull-tar", (BUG + ".txt\x00").encode())
    assert r.code == 1 and r.out == b""
    os.unlink(tree.bug / (BUG + ".txt"))
    bug_file(tree, BUG + ".txt", b"x" * 2000)
    tree.write_conf(bug_max_file_mb="0.001")
    r = tree.run("pull-tar", (BUG + ".txt\x00").encode())
    assert r.code == 3 and r.out == b""


def test_delete_verified_deletes_only_what_still_matches(tree):
    a, b, c = BUG + ".txt", "20261112T101011Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ac.txt", "20261112T101012Z_0f0f0f0f-aaaa-bbbb-cccc-0123456789ad.txt"
    bug_file(tree, a, b"AAA")
    bug_file(tree, b, b"replaced after the pull")
    stdin = b"".join(x.encode() + b"\x00" for pair in ((sha(b"AAA"), a), (sha(b"what was pulled"), b), (sha(b"gone"), c)) for x in pair)
    r = tree.run("delete-verified", stdin)
    assert r.code == 1 and [x for x in r.records()] == [["deleted", a], ["mismatch", b], ["missing", c]]
    assert not (tree.bug / a).exists() and (tree.bug / b).read_bytes() == b"replaced after the pull"


@pytest.mark.parametrize("stdin", [b"nothex\x00" + (BUG + ".txt").encode() + b"\x00", ("0" * 64 + "\x00../x\x00").encode(), ("0" * 64).encode() + b"\x00", (("0" * 64 + "\x00" + BUG + ".txt\x00") * 2).encode()])
def test_delete_verified_rejects_malformed_input(tree, stdin):
    bug_file(tree, BUG + ".txt")
    assert tree.run("delete-verified", stdin).code == 2
    assert (tree.bug / (BUG + ".txt")).exists()


def test_delete_verified_does_not_follow_a_symlink(tree, tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("secret")
    symlink_or_skip(tree.bug / (BUG + ".txt"), target)
    r = tree.run("delete-verified", (sha(b"secret") + "\x00" + BUG + ".txt\x00").encode())
    assert r.code == 1 and target.exists()


# ------------------------------------------------------------ P5 verbs


def test_fetch_backup_prints_header_then_the_newest_dump(tree):
    (tree.backups / "classp-20261110.dump").write_bytes(b"OLD")
    (tree.backups / "classp-20261111.dump").write_bytes(b"NEWDUMPBYTES")
    (tree.backups / "classp-20261111.meta.json").write_text(json.dumps({"pg_restore_list": "ok", "entries": 7}))
    (tree.backups / "notes.txt").write_text("ignored")
    os.utime(tree.backups / "classp-20261110.dump", (1, 1))
    r = tree.run("fetch-backup")
    head, _, body = r.out.partition(b"\n")
    meta = json.loads(head)
    assert r.code == 0 and meta["name"] == "classp-20261111.dump" and meta["size"] == 12 and meta["meta"]["entries"] == 7 and body == b"NEWDUMPBYTES"
    only = tree.run("fetch-backup --meta")
    assert only.out.count(b"\n") == 1 and only.code == 0


def test_fetch_backup_without_a_dump_exits_3(tree):
    assert tree.run("fetch-backup").code == 3


def test_disk_and_containment_print_the_facts_files(tree):
    (tree.facts / "disk.json").write_text('{"images": {"free_pct": 40}}')
    (tree.facts / "containment.json").write_text('{"read_only_root": true}\n')
    assert json.loads(tree.run("disk").out) == {"images": {"free_pct": 40}}
    assert json.loads(tree.run("containment").out) == {"read_only_root": True}
    (tree.facts / "disk.json").unlink()
    assert tree.run("disk").code == 3


# ------------------------------------------------------------ subprocess smoke test of the real entry point


def test_the_script_runs_as_a_process_and_never_uses_a_shell():
    assert tree_free_smoke() == 0
    source = GATE_PATH.read_text(encoding="utf-8")
    tree_ast = ast.parse(source)
    imported = {a.name.split(".")[0] for n in ast.walk(tree_ast) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree_ast) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names) and not imported & {"subprocess", "pty", "shlex", "pwd", "grp"}
    called = {n.attr for n in ast.walk(tree_ast) if isinstance(n, ast.Attribute)}
    assert not called & {"system", "popen", "spawnl", "execv", "execvp", "rmtree", "copytree", "walk", "copy", "copyfile", "move", "chown"}
    assert source.startswith("#!/usr/bin/env python3")


def tree_free_smoke():
    return subprocess.run([sys.executable, str(GATE_PATH)], capture_output=True, timeout=30).returncode - 2  # usage error is exit 2
