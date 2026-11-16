"""P4-T6: the bug-report file step of `sync.pull`, with LocalTransport (the real gate on a temp tree) and a temp dev directory; plus the
SshTransport command construction for the three P4 verbs (injected runners, no process)."""
import hashlib
import json
import os
import tarfile
import uuid
from pathlib import Path

import pytest

from sync.config import SyncSettings
from sync.files.transport import GateFailure, GateReply, LocalTransport, SshTransport
from sync.pull import bugreports, state
from tests.sync.gate_helpers import ENV_NAME, Tree, make_tar, special

RUN = "20261113T120000Z-0000000a"
SENTINEL = b"SENTINEL-BUG-BODY-9f3a"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bug_name(i: int, ext: str = "txt") -> str:
    return f"20261112T1010{i % 60:02d}Z_{uuid.UUID(int=i + 1)}.{ext}"


class Killed(BaseException):
    pass


class World:
    def __init__(self, tmp_path: Path, **bug_conf):
        (tmp_path / "prod").mkdir()
        self.tree = Tree(tmp_path / "prod", free_floor_pct="0")
        self.dev = tmp_path / "dev-bug"
        self.state = tmp_path / "state"
        self.state.mkdir()
        conf = {"dev_dir": str(self.dev), **bug_conf}
        self.settings = SyncSettings("general", {"state_dir": str(self.state), "pull": {"bug_reports": conf}}, root=tmp_path)
        self.before_verb = None
        self.transport = LocalTransport(ENV_NAME, self.tree.conf_path, self.tree.incoming, before_verb=lambda line: self.before_verb and self.before_verb(line))

    def prod_file(self, name, data=b"hello", settled=True):
        p = self.tree.bug / name
        p.write_bytes(data)
        if settled:
            old = p.stat().st_mtime - 3600
            os.utime(p, (old, old))
        return p

    def run(self):
        return bugreports.run(self.settings, self.transport, self.state, RUN)

    def ledger(self):
        return json.loads((self.state / state.BUG_LEDGER_FILE).read_text()) if (self.state / state.BUG_LEDGER_FILE).exists() else {}

    def dev_files(self):
        return {p.name: p.read_bytes() for p in sorted(self.dev.iterdir())} if self.dev.exists() else {}

    def prod_names(self):
        return sorted(p.name for p in self.tree.bug.iterdir())

    def snapshot(self):
        """Everything a dry run must not change: dev dir, prod tree (names, sizes, mtimes), state dir."""
        def walk(root):
            return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}
        return walk(self.dev), walk(self.tree.bug), walk(self.state)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def seed(world, n=3):
    files = {bug_name(i): SENTINEL + b"%d" % i for i in range(n)}
    for name, data in files.items():
        world.prod_file(name, data)
    return files


# --- clean pull --------------------------------------------------------------------------------------------------------------------

def test_clean_pull_stores_verifies_records_then_deletes_on_prod(world):
    files = seed(world)
    assert world.run() == {"status": "done", "pulled": 3, "deleted_on_prod": 3, "failed": 0}
    assert world.dev_files() == files
    assert world.ledger() == {n: sha(d) for n, d in files.items()}
    assert world.prod_names() == []
    verbs = [c.split()[0] for c in world.transport.calls]
    assert verbs == ["pull-list", "pull-tar", "delete-verified"]
    assert not [p for p in world.dev.iterdir() if p.name.startswith(".")] and not [p for p in world.state.iterdir() if p.name.startswith(".")]


def test_rerun_is_a_noop(world):
    seed(world)
    world.run()
    before = world.snapshot()
    assert world.run() == {"status": "done", "pulled": 0, "deleted_on_prod": 0, "failed": 0}
    assert world.snapshot() == before
    assert [c.split()[0] for c in world.transport.calls[3:]] == ["pull-list"]


def test_unsettled_hidden_and_non_regular_prod_entries_are_not_pulled_and_not_deleted(world):
    world.prod_file(bug_name(0), b"young", settled=False)
    world.prod_file(".hidden", b"x")
    world.prod_file("random-name.txt", b"x")
    (world.tree.bug / "sub").mkdir()
    assert world.run() == {"status": "done", "pulled": 0, "deleted_on_prod": 0, "failed": 0}
    assert sorted(world.prod_names()) == sorted([bug_name(0), ".hidden", "random-name.txt", "sub"])


# --- tamper, caps, hostile ---------------------------------------------------------------------------------------------------------

def test_sha_mismatch_between_list_and_tar_keeps_nothing_and_deletes_nothing(world):
    files = seed(world)
    victim = bug_name(1)

    def tamper(line):
        if line.startswith("pull-tar"):
            p = world.tree.bug / victim
            p.write_bytes(b"X" * len(files[victim]))  # same size, other bytes

    world.before_verb = tamper
    res = world.run()
    assert res == {"status": "partial", "pulled": 2, "deleted_on_prod": 2, "failed": 1}
    assert victim not in world.dev_files() and victim not in world.ledger()
    assert world.prod_names() == [victim]
    assert not [p for p in world.dev.iterdir() if p.name.startswith(".")]


def test_a_size_change_between_list_and_tar_fails_that_file_only(world):
    files = seed(world, 2)
    victim = bug_name(0)
    world.before_verb = lambda line: line.startswith("pull-tar") and (world.tree.bug / victim).write_bytes(b"much longer than before" * 9)
    res = world.run()
    assert res["pulled"] == 1 and res["failed"] == 1 and res["status"] == "partial"
    assert world.prod_names() == [victim]


def test_extension_size_and_count_caps(tmp_path):
    w = World(tmp_path, allowed_extensions=[".txt"], max_file_mb=0.0001, max_files=2, max_total_mb=100)  # 104 bytes per file
    ok = [bug_name(i) for i in range(5)]
    for n in ok:
        w.prod_file(n, b"abc")
    w.prod_file(bug_name(10, "png"), b"png")
    w.prod_file(bug_name(11), b"B" * 500)
    lists, checks = bugreports.plan_report(w.settings, w.transport)
    assert len(lists["to_fetch"]) == 2
    assert checks[0]["detail"] == "not_allowed=1,too_big=1,deferred=3"
    assert w.run() == {"status": "done", "pulled": 2, "deleted_on_prod": 2, "failed": 0}
    assert sorted(w.dev_files()) == ok[:2]
    assert sorted(w.prod_names()) == sorted(ok[2:] + [bug_name(10, "png"), bug_name(11)])
    assert w.run()["pulled"] == 2  # the deferred ones follow on the next runs
    assert w.run()["pulled"] == 1


def test_total_bytes_cap(tmp_path):
    w = World(tmp_path, max_total_mb=0.0001)  # about 104 bytes
    for i in range(3):
        w.prod_file(bug_name(i), b"x" * 60)
    assert w.run()["pulled"] == 1


def test_hostile_names_in_the_gate_list_are_dropped_by_the_client():
    raw = (b"f\t3\t" + sha(b"abc").encode() + b"\t" + bug_name(1).encode() + b"\x00"
           b"f\t3\t" + sha(b"abc").encode() + b"\t../../etc/passwd\x00"
           b"f\t3\t" + sha(b"abc").encode() + b"\t" + bug_name(1).encode() + b"\x00"  # duplicate
           b"f\t3\tnothex\t" + bug_name(2).encode() + b"\x00"
           b"f\t3\t" + sha(b"abc").encode() + b"\t" + bug_name(3).encode() + b"\n\x00"  # trailing newline: fullmatch, not `$`
           b"anomaly:hidden\t0\t\t.x\x00" + b"\xff\xfe\t\x00" + b"f\t-1\t" + sha(b"a").encode() + b"\t" + bug_name(4).encode() + b"\x00")
    files, anomalies = bugreports.parse_listing(raw)
    assert [f.name for f in files] == [bug_name(1)] and anomalies == 7


def test_hostile_tar_members_are_not_stored(world):
    files = seed(world, 3)

    class Evil(LocalTransport):
        def _run_to_file(self, line, stdin, dest, timeout):
            names = [n for n in stdin.decode().split("\x00") if n]
            members = [(special("../evil.txt", tarfile.REGTYPE), b"x"), (special(names[0], tarfile.SYMTYPE, "/etc/passwd"), None),
                       (special("not-listed.txt", tarfile.REGTYPE), b"x"), (names[1], files[names[1]] + b"+"), (names[0], files[names[0]]), (names[2], files[names[2]])]
            Path(dest).write_bytes(make_tar(members))
            return GateReply(0, b"", "")

    world.transport = Evil(ENV_NAME, world.tree.conf_path, world.tree.incoming)
    res = world.run()
    assert res == {"status": "partial", "pulled": 1, "deleted_on_prod": 1, "failed": 2}  # 1st: a link (its later regular copy is refused), 2nd: wrong size, 3rd fine
    assert list(world.dev_files()) == [sorted(files)[2]]
    assert "evil.txt" not in "".join(p.name for p in world.dev.parent.rglob("*"))
    assert world.prod_names() != []


def test_name_collision_with_a_different_dev_file_never_overwrites_it(world):
    world.dev.mkdir()
    name = bug_name(0)
    (world.dev / name).write_bytes(b"dev's own file")
    world.prod_file(name, b"prod's report")
    assert world.run() == {"status": "done", "pulled": 1, "deleted_on_prod": 1, "failed": 0}
    files = world.dev_files()
    assert files[name] == b"dev's own file" and files[name.replace(".txt", "_1.txt")] == b"prod's report"
    assert world.prod_names() == []


def test_same_file_already_on_dev_counts_as_received(world):
    world.dev.mkdir()
    name = bug_name(0)
    (world.dev / name).write_bytes(b"same")
    world.prod_file(name, b"same")
    assert world.run()["pulled"] == 1
    assert list(world.dev_files()) == [name] and world.prod_names() == []


# --- crash and resume --------------------------------------------------------------------------------------------------------------

def test_crash_between_dev_write_and_ledger_resumes_without_duplicate_or_loss(world, monkeypatch):
    files = seed(world, 2)
    real = bugreports.write_ledger
    monkeypatch.setattr(bugreports, "write_ledger", lambda *a: (_ for _ in ()).throw(Killed()))
    with pytest.raises(Killed):
        world.run()
    monkeypatch.setattr(bugreports, "write_ledger", real)
    assert world.ledger() == {} and len(world.dev_files()) == 1 and len(world.prod_names()) == 2  # nothing deleted on prod
    assert not [p for p in world.dev.iterdir() if p.name.startswith(".")]
    assert world.run() == {"status": "done", "pulled": 2, "deleted_on_prod": 2, "failed": 0}
    assert world.dev_files() == files and world.prod_names() == []


def test_crash_between_ledger_and_prod_delete_deletes_without_a_second_download(world):
    files = seed(world, 2)

    def kill(line):
        if line.startswith("delete-verified"):
            raise Killed()

    world.before_verb = kill
    with pytest.raises(Killed):
        world.run()
    assert len(world.ledger()) == 2 and len(world.prod_names()) == 2
    world.before_verb = None
    world.transport.calls.clear()
    assert world.run() == {"status": "done", "pulled": 0, "deleted_on_prod": 2, "failed": 0}
    assert [c.split()[0] for c in world.transport.calls] == ["pull-list", "delete-verified"]  # no pull-tar
    assert world.dev_files() == files and world.prod_names() == []


def _killed_before_delete(world, n):
    files = seed(world, n)

    def kill(line):
        if line.startswith("delete-verified"):
            raise Killed()

    world.before_verb = kill
    with pytest.raises(Killed):
        world.run()
    world.before_verb = None
    assert len(world.ledger()) == n and len(world.prod_names()) == n
    return files


def test_a_ledger_entry_without_its_dev_file_pulls_it_again_before_anything_is_deleted_on_prod(world):
    """Review finding L4: the ledger says "received" but the dev file was removed before the resume."""
    files = _killed_before_delete(world, 2)
    sorted(world.dev.iterdir())[0].unlink()
    world.transport.calls.clear()
    assert world.run() == {"status": "done", "pulled": 1, "deleted_on_prod": 2, "failed": 0}
    verbs = [c.split()[0] for c in world.transport.calls]
    assert verbs.index("pull-tar") < verbs.index("delete-verified")
    assert world.dev_files() == files and world.prod_names() == []


def test_a_ledger_entry_whose_dev_file_changed_is_verified_again_before_prod_deletes_it(world):
    files = _killed_before_delete(world, 2)
    sorted(world.dev.iterdir())[0].write_bytes(b"tampered")
    world.transport.calls.clear()
    result = world.run()
    verbs = [c.split()[0] for c in world.transport.calls]
    assert "pull-tar" in verbs and verbs.index("pull-tar") < verbs.index("delete-verified")  # the changed file was fetched again first
    # the verified copy was published next to the tampered file (never clobbering it) before prod deleted its own
    assert result["status"] == "done" and world.prod_names() == []
    assert sorted(world.dev_files().values())[-1:] != [] and set(files.values()) <= set(world.dev_files().values())


def test_lost_ledger_costs_only_a_redownload(world):
    files = seed(world, 1)
    world.before_verb = lambda line: (_ for _ in ()).throw(Killed()) if line.startswith("delete-verified") else None
    with pytest.raises(Killed):
        world.run()
    (world.state / state.BUG_LEDGER_FILE).unlink()
    world.before_verb = None
    assert world.run()["pulled"] == 1
    assert world.dev_files() == files  # no _1 duplicate


def test_a_file_replaced_on_prod_after_the_ledger_is_a_new_file(world):
    seed(world, 1)
    world.before_verb = lambda line: (_ for _ in ()).throw(Killed()) if line.startswith("delete-verified") else None
    with pytest.raises(Killed):
        world.run()
    world.before_verb = None
    world.prod_file(bug_name(0), b"a different report with the same name")
    assert world.run()["pulled"] == 1
    assert len(world.dev_files()) == 2


def test_delete_verified_is_refused_when_prod_changed_after_the_tar(world):
    files = seed(world, 2)
    victim = bug_name(0)
    world.before_verb = lambda line: line.startswith("delete-verified") and world.prod_file(victim, b"replaced after the pull")
    res = world.run()
    assert res == {"status": "partial", "pulled": 2, "deleted_on_prod": 1, "failed": 1}
    assert world.prod_names() == [victim] and (world.tree.bug / victim).read_bytes() == b"replaced after the pull"
    assert world.dev_files() == files


def test_a_gate_failure_after_the_commit_is_recorded_as_partial(world):
    seed(world, 1)
    world.before_verb = lambda line: (_ for _ in ()).throw(GateFailure("ssh_failed")) if line.startswith("pull-list") else None
    assert world.run() == {"status": "partial", "pulled": 0, "deleted_on_prod": 0, "failed": 1}


def test_a_failing_pull_tar_fails_the_batch_and_deletes_nothing(world):
    seed(world, 2)
    world.tree.write_conf(free_floor_pct="0", bug_max_total_mb="0.0000001")
    res = world.run()
    assert res["status"] == "partial" and res["pulled"] == 0 and res["failed"] == 2 and len(world.prod_names()) == 2


# --- dry run -----------------------------------------------------------------------------------------------------------------------

def test_dry_run_changes_nothing_and_the_report_holds_ids_only(world):
    files = seed(world, 3)
    world.prod_file("secret-sentinel-name.txt", SENTINEL)
    before = world.snapshot()
    lists, checks = bugreports.plan_report(world.settings, world.transport)
    assert world.snapshot() == before and not world.dev.exists()
    ids = sorted(str(uuid.UUID(int=i + 1)) for i in range(3))
    assert lists == {"to_fetch": ids, "to_delete_on_prod_after_verified_pull": ids}
    assert checks == [{"id": "bug_reports_listing", "status": "attention", "severity": "info", "detail": "anomalies=1"}]
    from sync import report
    sec = report.Section(name="pull", tool="pull", lists=lists, checks=checks)
    doc = report.Document(env="general", run_id=RUN, tool="pull", dry=True, sections=[sec])
    blob = report.render_json(doc) + report.render_markdown(doc) + report.render_summary(doc)
    for needle in (SENTINEL.decode(), "secret-sentinel-name", "20261112T", ".txt"):
        assert needle not in blob
    assert [c.split()[0] for c in world.transport.calls] == ["pull-list"]


def test_plan_lists_without_a_transport_is_empty_and_resume_files_are_listed_for_deletion(world):
    assert bugreports.plan_report(world.settings, None) == ({}, [])
    seed(world, 1)
    world.before_verb = lambda line: (_ for _ in ()).throw(Killed()) if line.startswith("delete-verified") else None
    with pytest.raises(Killed):
        world.run()
    world.before_verb = None
    lists = bugreports.plan_lists(world.settings, world.transport)
    assert lists["to_fetch"] == [] and len(lists["to_delete_on_prod_after_verified_pull"]) == 1


def test_an_unknown_dev_directory_is_a_precondition_failure_of_the_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("BUG_REPORTS_PATH", raising=False)
    monkeypatch.delenv("BASE_PATH", raising=False)
    w = World(tmp_path)
    w.settings = SyncSettings("general", {"state_dir": str(w.state)}, root=tmp_path)
    w.prod_file(bug_name(0))
    from sync.config import PreconditionError
    with pytest.raises(PreconditionError):
        bugreports.plan_report(w.settings, w.transport)


def test_dev_dir_follows_the_backend_rule(monkeypatch):
    s = SyncSettings("general", {})
    monkeypatch.delenv("BUG_REPORTS_PATH", raising=False)
    monkeypatch.setenv("BASE_PATH", "/images")
    assert bugreports.dev_dir(s) == Path("/images/bug_reports")
    monkeypatch.setenv("BUG_REPORTS_PATH", "/elsewhere")
    assert bugreports.dev_dir(s) == Path("/elsewhere")


def test_the_module_never_decodes_or_executes_bodies():
    src = Path(bugreports.__file__).read_text(encoding="utf-8")
    for banned in ("extractall", "extract(", "eval(", "exec(", "json.loads(fh", "subprocess", "import zipfile", "PIL"):
        assert banned not in src
    assert "must sanitise" in bugreports.__doc__.lower()


# --- SshTransport command construction --------------------------------------------------------------------------------------------

def test_ssh_transport_builds_the_three_pull_verbs_and_streams_pull_tar_to_a_file(tmp_path):
    seen = []

    def runner(command, stdin, timeout):
        seen.append((command, stdin, timeout))
        return 0, b"out", b""

    def file_runner(command, stdin, dest, timeout):
        seen.append((command, stdin, dest, timeout))
        Path(dest).write_bytes(b"TAR")
        return 0, b""

    t = SshTransport("ssh", "sftp", "gate", "up", runner=runner, file_runner=file_runner)
    assert t.pull_list().out == b"out"
    assert seen[0][0][-2:] == ["gate", "pull-list"] and seen[0][1] == b""
    dest = tmp_path / "x.tar"
    r = t.pull_tar(["a.txt", "b.txt"], dest)
    assert r.ok and dest.read_bytes() == b"TAR"
    assert seen[1][0][-2:] == ["gate", "pull-tar"] and seen[1][1] == b"a.txt\x00b.txt\x00" and seen[1][2] == str(dest)
    t.delete_verified([("a" * 64, "n1.txt"), ("b" * 64, "n2.txt")])
    assert seen[2][0][-2:] == ["gate", "delete-verified"] and seen[2][1] == ("a" * 64).encode() + b"\x00n1.txt\x00" + ("b" * 64).encode() + b"\x00n2.txt\x00"
    for cmd in (s[0] for s in seen):
        assert cmd[0] == "ssh" and "-T" in cmd and "BatchMode=yes" in cmd


def test_ssh_transport_still_refuses_unknown_verbs_and_maps_exit_255(tmp_path):
    t = SshTransport("ssh", "sftp", "gate", "up", runner=lambda *a: (0, b"", b""), file_runner=lambda *a: (255, b"Connection refused\n"))
    for line in ("retire", "restore 2026-11-14", "pull-tar; id", "rm"):  # retire/restore are deferred (decision 27): not on the list
        with pytest.raises(GateFailure):
            t.verb_command(line)
    # P6 extended the list deliberately by exactly the three read-only verbs of sync.verify; the transport only ever sends `fetch-backup --meta`
    for line in ("fetch-backup --meta", "disk", "containment"):
        assert t.verb_command(line)[-2:] == ["gate", line]
    with pytest.raises(GateFailure) as exc:
        t.pull_tar(["a.txt"], tmp_path / "x.tar")
    assert exc.value.code == "ssh_failed"
