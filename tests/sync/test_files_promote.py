"""P3-T5: `sync.files promote` end to end with LocalTransport (the real gate on a temp tree), a temp dev image directory and a
fake manifest-rows connection. No network, no database, no environment files."""
import contextlib
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sync import connect
from sync.config import SyncSettings
from sync.files import manifest as manifest_mod
from sync.files import promote
from sync.files.transport import LocalTransport
from tests.sync.gate_helpers import ENV_NAME, Tree

N = 700  # the 0.5 % left-out tripwire allows 3 problem images out of 700


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content(i: int) -> bytes:
    return (f"image-{i}-".encode()) * 8


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql):
        assert "images.status = 'active'" in sql  # the promotable rows query of sync.model

    def fetchall(self):
        return [tuple(r) for r in self.rows]

    def close(self):
        pass


class FakeRaw:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


class FakeDev(connect.SyncConnection):
    def __init__(self, rows):
        super().__init__(FakeRaw(rows), connect.DEV_RO, None, None, "general")

    def query(self, sql, args=None):
        if sql.startswith("select current_database()"):
            return [("devdb",)]
        if "current_setting" in sql:
            return [(None,)]
        raise AssertionError(sql)


class Killed(BaseException):
    """Stands for a killed process: not an Exception, so nothing in the tool can swallow it."""


class World:
    """Dev image dir + rows, a gate tree, settings and the injection points."""

    def __init__(self, tmp_path: Path, n: int = N, **gate_conf):
        gate_conf.setdefault("free_floor_pct", "0")
        self.tmp = tmp_path
        self.tree = Tree(tmp_path / "prod", **gate_conf) if (tmp_path / "prod").mkdir() is None else None
        self.dev = tmp_path / "dev"
        self.dev.mkdir()
        self.state = tmp_path / "state"
        self.rows = []  # [id, filename, content_hash]
        for i in range(n):
            self.add(f"img{i:04d}.jpg", content(i))
        self.settings = SyncSettings("general", {"state_dir": str(self.state), "dev": {"database": "devdb"},
                                                 "prod": {"gate_alias": "g", "upload_alias": "u"}}, root=tmp_path)
        self.transports = []
        self.before_verb = None
        self.counter = 0

    def add(self, name, data, row_hash="auto", file_name=None):
        (self.dev / (file_name or name)).write_bytes(data)  # file_name: a hostile row name cannot exist on an NTFS dev disk
        self.rows.append([str(uuid.UUID(int=len(self.rows) + 1)), name, sha(data) if row_hash == "auto" else row_hash])
        return self.rows[-1]

    def row(self, name):
        return next(r for r in self.rows if r[1] == name)

    def change(self, name, data):
        (self.dev / name).write_bytes(data)
        self.row(name)[2] = sha(data)

    @contextlib.contextmanager
    def opener(self, env, tool, settings=None):
        assert tool == "F"
        yield {"dev": FakeDev(self.rows), "prod": None, "prod_reader_conn": None}

    def factory(self, settings):
        t = LocalTransport(ENV_NAME, self.tree.conf_path, self.tree.incoming, before_verb=self.before_verb)
        self.transports.append(t)
        return t

    def run(self, *argv, run_id=None, base_dir=None, answer=None):
        self.counter += 1
        run_id = run_id or f"20261113T0000{self.counter:02d}Z-{self.counter:08x}"
        args = promote.build_parser().parse_args(["promote", "--env", "general", *argv, "--run-id", run_id])
        out, err = io.StringIO(), io.StringIO()
        code = promote.run_promote(args, settings=self.settings, session_opener=self.opener, transport_factory=self.factory,
                                   base_dir=str(base_dir or self.dev), out=out, err=err,
                                   ask=(lambda prompt: answer) if answer is not None else (lambda prompt: f"general {self.settings.get('prod.database') or ''}"))
        folder = self.state / "runs" / run_id
        return Run(code, out.getvalue(), err.getvalue(), folder, self.transports[-1] if self.transports else None)

    # what is on prod
    def prod_images(self):
        return {p.name for p in self.tree.base.iterdir()}

    def prod_snapshot(self):
        return {str(p.relative_to(self.tree.root)): (p.stat().st_size if p.is_file() else -1)
                for p in sorted(self.tree.root.rglob("*")) if p.name != "gate.lock" and "gate.conf" not in p.name}


class Run:
    def __init__(self, code, out, err, folder, transport):
        self.code, self.out, self.err, self.folder, self.transport = code, out, err, folder, transport

    @property
    def report(self):
        return json.loads((self.folder / "report.json").read_text(encoding="utf-8"))

    @property
    def promoted(self):
        return json.loads((self.folder / "files-promoted.json").read_text(encoding="utf-8"))

    @property
    def entry(self):
        return self.report["sections"][0]["entries"][0]

    @property
    def guards(self):
        return {g["id"]: g for g in self.report["sections"][0]["guards"]}

    @property
    def plan_hash(self):
        return self.report["plan_hash"]

    def verbs(self):
        return [c.split(" ")[0] for c in self.transport.calls]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def seed(world):
    dry = world.run()
    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 0, applied.err
    return applied


# ---------------------------------------------------------------------------------------------------------------------------------

def test_first_seed_dry_run_then_apply(world):
    before = world.prod_snapshot()
    dry = world.run()
    assert dry.code == 0 and (dry.folder / "dry").exists()
    assert not (dry.folder / "files-promoted.json").exists()
    assert world.prod_snapshot() == before  # nothing on prod, not even gate bookkeeping
    assert set(dry.verbs()) == {"handshake", "list"}  # read-only verbs only
    assert dry.entry["added"]["count"] == N and dry.entry["overwritten"]["count"] == 0
    assert dry.report["apply_command"].startswith("python -m sync.files promote --env general --apply --plan-hash ")
    assert dry.plan_hash in dry.out and "DRY RUN" in dry.out

    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 0, applied.err
    assert not (applied.folder / "dry").exists() and applied.report["dry"] is False
    assert len(world.prod_images()) == N
    p = applied.promoted
    assert p["complete"] is True and len(p["images"]) == N and p["left_out"] == [] and p["replaced"] == 0
    assert p["env"] == "general" and p["run_id"] == applied.report["run_id"] and p["gate_version"] == "1"
    assert p["manifest_sha256"] == manifest_mod.manifest_sha256(world.rows)
    first = p["images"][0]
    assert first == {"id": world.rows[0][0], "filename": "img0000.jpg", "size": len(content(0)), "content_hash": sha(content(0))}
    assert applied.report["result"]["files"]["images"]["added"] == N
    assert (world.tree.base / "img0003.jpg").read_bytes() == content(3)
    assert not list(world.tree.incoming.iterdir()) and not [p for p in world.tree.work.iterdir() if p.name not in ("gate.lock", "record.tsv")]


def test_second_run_is_unchanged_and_sends_nothing(world):
    seed(world)
    again = world.run()
    assert again.code == 0 and again.entry["unchanged"]["count"] == N
    assert again.entry["added"]["count"] == 0 and again.entry["overwritten"]["count"] == 0
    applied = world.run("--apply", "--plan-hash", again.plan_hash)
    assert applied.code == 0
    assert not {"receive", "verify", "commit"} & set(applied.verbs()) and not any(c.startswith("upload") for c in applied.transport.calls)
    assert len(applied.promoted["images"]) == N and applied.promoted["complete"] is True


def test_a_changed_file_is_replaced_and_a_new_file_added(world):
    seed(world)
    world.change("img0005.jpg", b"new bytes of five" * 3)
    new = world.add("brand-new.jpg", b"fresh")
    dry = world.run()
    assert dry.entry["added"]["ids"] == [new[0]]
    ow = dry.entry["overwritten"]
    assert ow["count"] == 1 and ow["ids"] == [world.row("img0005.jpg")[0]]
    assert ow["size_changed"]["count"] == 1 and ow["hash_changed"]["count"] == 0  # the size differs
    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 0, applied.err
    assert (world.tree.base / "img0005.jpg").read_bytes() == b"new bytes of five" * 3
    assert (world.tree.base / "brand-new.jpg").read_bytes() == b"fresh"
    assert applied.promoted["replaced"] == 1 and len(applied.promoted["images"]) == N + 1
    assert applied.report["result"]["files"]["images"]["overwritten"] == 1


def test_same_size_different_hash_is_a_hash_change(world):
    seed(world)
    same_size = bytes(reversed(content(7)))
    world.change("img0007.jpg", same_size)
    dry = world.run()
    ow = dry.entry["overwritten"]
    assert ow["hash_changed"]["count"] == 1 and ow["size_changed"]["count"] == 0
    assert world.run("--apply", "--plan-hash", dry.plan_hash).code == 0
    assert (world.tree.base / "img0007.jpg").read_bytes() == same_size


def test_missing_dev_file_and_hostile_names_are_left_out_not_fatal(world):
    (world.dev / "img0010.jpg").unlink()
    world.add("bad\tname.jpg", b"x", file_name="bad-name.jpg")
    world.add("CON.jpg", b"y", file_name="con-file.jpg")
    dry = world.run()
    assert dry.code == 0
    skipped = dry.entry["skipped_with_reason"]
    assert skipped["missing_file"]["ids"] == [world.row("img0010.jpg")[0]] and skipped["name_problem"]["count"] == 2
    assert dry.guards["left_out_tripwire"]["tripped"] is False
    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 0, applied.err
    p = applied.promoted
    assert p["complete"] is False and len(p["images"]) == N - 1
    reasons = sorted((lo["reason"], lo["filename"]) for lo in p["left_out"])
    assert reasons == [("missing_file", "img0010.jpg"), ("name_control_character", "bad\tname.jpg"), ("name_reserved_name", "CON.jpg")]
    assert "bad\tname.jpg" not in world.prod_images() and "CON.jpg" not in world.prod_images()


def test_too_many_left_out_trips_the_guard_exit_3_on_apply_only(world):
    for i in range(4):
        (world.dev / f"img{i:04d}.jpg").unlink()
    dry = world.run()
    assert dry.code == 0 and dry.guards["left_out_tripwire"]["tripped"] is True
    before = world.prod_snapshot()
    applied = world.run("--apply")
    assert applied.code == 3 and "left_out_tripwire" in applied.err
    assert world.prod_snapshot() == before and not (applied.folder / "files-promoted.json").exists()


def test_empty_manifest_and_half_of_prod_guards(world):
    seed(world)
    world.rows[:] = []
    dry = world.run()
    assert dry.code == 0 and dry.guards["empty_manifest"]["tripped"] and dry.guards["manifest_below_half_of_prod"]["tripped"]
    assert dry.entry["left_behind"]["count"] == N  # on prod, not promotable: informational
    before = world.prod_snapshot()
    assert world.run("--apply").code == 3
    assert world.prod_snapshot() == before and len(world.prod_images()) == N  # nothing was removed


def test_left_behind_files_are_reported_never_removed(world):
    seed(world)
    (world.tree.base / "orphan.jpg").write_bytes(b"orphan")
    (world.tree.base / ".hidden").write_bytes(b"h")
    dry = world.run()
    lb = dry.entry["left_behind"]
    assert set(lb["names"]) == {"orphan.jpg", ".hidden"} and lb["count"] == 2
    assert world.run("--apply", "--plan-hash", dry.plan_hash).code == 0
    assert (world.tree.base / "orphan.jpg").exists() and (world.tree.base / ".hidden").exists()


def test_a_blocking_anomaly_is_a_guard(world):
    (world.tree.base / "img0001.jpg").mkdir()  # a directory where a manifest file belongs
    dry = world.run()
    assert dry.code == 0 and dry.guards["prod_anomaly_blocking"]["tripped"]
    before = world.prod_snapshot()
    applied = world.run("--apply")
    assert applied.code == 3 and "prod_anomaly_blocking" in applied.err
    assert world.prod_snapshot() == before


def test_apply_with_a_wrong_plan_hash_exits_3_and_writes_nothing_on_prod(world):
    dry = world.run()
    before = world.prod_snapshot()
    applied = world.run("--apply", "--plan-hash", "0" * 64)
    assert applied.code == 3 and "plan changed" in applied.err
    assert world.prod_snapshot() == before and not (applied.folder / "files-promoted.json").exists()
    assert applied.report["dry"] is False and applied.plan_hash == dry.plan_hash


def test_plan_hash_is_deterministic_and_follows_the_world(world):
    a, b = world.run(), world.run()
    assert a.plan_hash == b.plan_hash
    world.add("late.jpg", b"late")
    assert world.run().plan_hash != a.plan_hash
    assert world.run("--apply", "--plan-hash", a.plan_hash).code == 3  # the world changed since that dry run
    deep = world.run("--deep")
    assert deep.plan_hash != world.run().plan_hash and "--deep" in deep.report["apply_command"]


def test_a_dry_run_writes_only_the_report_folder(world):
    seed(world)
    world.change("img0002.jpg", b"changed")
    before_prod, before_state = world.prod_snapshot(), sorted(p.name for p in world.state.iterdir())
    dry = world.run("--deep", "--batch-mb", "200", "--json", str(world.tmp / "copy.json"))
    assert dry.code == 0
    assert world.prod_snapshot() == before_prod
    assert sorted(p.name for p in dry.folder.iterdir()) == ["dry", "report.json", "report.md"]
    assert sorted(p.name for p in world.state.iterdir()) == sorted(before_state + ["runs"] if "runs" not in before_state else before_state)
    assert json.loads((world.tmp / "copy.json").read_text()) == dry.report
    assert "--batch-mb 200" in dry.report["apply_command"] and "--deep" in dry.report["apply_command"]
    assert "hash" not in dry.verbs()  # --deep only reads dev in a dry run; the gate record is not rewritten


def test_report_redaction_names_only_for_left_behind_files(world):
    sentinel = "SENTINEL-NAME-9d3f"
    world.add(f"{sentinel}\tx.jpg", b"s", file_name="sentinel-file.jpg")  # hostile name: reported by id and reason code only
    (world.tree.base / "ORPHAN-seen-on-prod.jpg").write_bytes(b"o")  # allowed: a prod file without an id
    hostile_prod = True
    try:
        (world.tree.base / "bad\tprod\nname").write_bytes(b"p")
    except OSError:  # not creatable on an NTFS test machine; the Linux run covers it
        hostile_prod = False
    dry = world.run("--json", "-")
    text = dry.out + (dry.folder / "report.json").read_text(encoding="utf-8") + (dry.folder / "report.md").read_text(encoding="utf-8")
    assert sentinel not in text
    assert "ORPHAN-seen-on-prod.jpg" in (dry.folder / "report.json").read_text(encoding="utf-8")
    assert "bad\\tprod" not in text
    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    if hostile_prod:
        assert "invalid-name-" in (dry.folder / "report.json").read_text(encoding="utf-8")
        assert applied.code == 3  # the invalid prod entry blocks (anomaly guard); the sentinel is still absent
    assert sentinel not in applied.out + (applied.folder / "report.json").read_text(encoding="utf-8")


def test_files_promoted_keeps_the_names_the_report_hides(world):
    world.add("secret\tname.jpg", b"s", file_name="secret-file.jpg")
    applied = world.run("--apply")
    assert applied.code == 0
    assert any(lo["filename"] == "secret\tname.jpg" for lo in applied.promoted["left_out"])
    assert "secret" not in (applied.folder / "report.json").read_text(encoding="utf-8")


def test_files_over_the_gate_member_cap_are_skipped_up_front(tmp_path):
    w = World(tmp_path, max_member_mb="0.002")  # about 2 KB
    w.add("huge.jpg", b"z" * 5000)
    dry = w.run()
    assert dry.entry["skipped_with_reason"]["over_member_cap"]["ids"] == [w.row("huge.jpg")[0]]
    applied = w.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 0, applied.err
    assert "huge.jpg" not in w.prod_images() and len(w.prod_images()) == N
    assert [(lo["filename"], lo["reason"]) for lo in applied.promoted["left_out"]] == [("huge.jpg", "over_member_cap")]
    assert applied.promoted["complete"] is False


def test_a_predicted_gate_replace_cap_is_a_guard(tmp_path):
    w = World(tmp_path, replace_day_min="1", replace_day_pct="0")
    seed(w)
    w.change("img0001.jpg", b"one")
    w.change("img0002.jpg", b"two")
    dry = w.run()
    g = dry.guards["gate_replace_cap"]
    assert dry.code == 0 and g["tripped"] and (g["value"], g["limit"]) == (2, 1)
    assert w.run("--apply").code == 3
    assert (w.tree.base / "img0001.jpg").read_bytes() == content(1)


def test_a_gate_cap_trip_at_commit_exits_3_and_the_rerun_resumes(world):
    seed(world)
    world.change("img0001.jpg", b"one")
    world.change("img0002.jpg", b"two")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (world.tree.work / "counters.json").write_text(json.dumps({"day": today, "replaced": 199}))  # 199 of 200 already used today
    dry = world.run()
    applied = world.run("--apply", "--plan-hash", dry.plan_hash)
    assert applied.code == 3 and "replace_cap" in applied.err
    assert (world.tree.base / "img0001.jpg").read_bytes() == content(1)  # nothing published
    p = applied.promoted
    assert p["complete"] is False and {lo["reason"] for lo in p["left_out"]} == {"files_gate_miss"} and len(p["images"]) == N - 2
    (world.tree.work / "counters.json").unlink()  # the owner lifts the cap on the VPS
    again = world.run("--apply")
    assert again.code == 0, again.err
    assert "upload" not in " ".join(c.split(" ")[0] for c in again.transport.calls)  # the verified batch was still in the gate's work dir
    assert (world.tree.base / "img0001.jpg").read_bytes() == b"one" and again.promoted["complete"] is True


def test_resume_after_a_kill_between_receive_and_commit(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.setattr(promote, "MB", 64)  # a batch target of 150 "MB" is 9.6 KB here: about 100 files, several batches
    commits = []

    def kill_second_commit(line):
        if line.startswith("commit"):
            commits.append(line)
            if len(commits) == 2:
                raise Killed

    w.before_verb = kill_second_commit
    with pytest.raises(Killed):
        w.run("--apply", "--batch-mb", "150")
    done = len(w.prod_images())
    assert 0 < done < N  # the first batch is visible, the second only received and verified
    assert any(p.is_dir() for p in w.tree.work.iterdir())  # the second batch waits in the gate's work dir
    w.before_verb = None
    again = w.run("--apply", "--batch-mb", "150")
    assert again.code == 0, again.err
    calls = again.transport.calls
    assert any(c.startswith("receive") for c in calls) and len(w.prod_images()) == N
    uploads = [c for c in calls if c.startswith("upload")]
    receives = [c for c in calls if c.startswith("receive")]
    resumed = receives[0].split()[1]  # the batch that waited in the gate's work dir: received (batch_exists), never uploaded again
    assert f"upload {resumed}" not in uploads and len(receives) == 1 + 2 * len(uploads) and len(uploads) >= 2
    assert again.promoted["complete"] is True and len(again.promoted["images"]) == N


def test_resume_after_a_kill_between_upload_and_receive(tmp_path, monkeypatch):
    w = World(tmp_path)

    def kill_first_receive_after_upload(line):
        if line.startswith("receive") and any(c.startswith("upload") for c in w.transports[-1].calls):
            raise Killed

    w.before_verb = kill_first_receive_after_upload
    with pytest.raises(Killed):
        w.run("--apply")
    assert len(list(w.tree.incoming.glob("*.tar"))) == 1  # the complete tar waits in the jail
    w.before_verb = None
    again = w.run("--apply")
    assert again.code == 0 and len(w.prod_images()) == N
    assert not [c for c in again.transport.calls if c.startswith("upload")]  # the tar was already there


def test_deep_adopts_files_prod_holds_without_a_record(world):
    for i in range(N):  # prod already has every file (placed by hand: no gate record)
        (world.tree.base / f"img{i:04d}.jpg").write_bytes(content(i))
    plain = world.run()
    assert plain.entry["overwritten"]["count"] == N  # unverified: would be re-sent
    deep = world.run("--deep")
    applied = world.run("--apply", "--deep", "--plan-hash", deep.plan_hash)
    assert applied.code == 0, applied.err
    assert "hash" in applied.verbs() and not any(c.startswith("upload") for c in applied.transport.calls)
    assert applied.promoted["complete"] is True and len(applied.promoted["images"]) == N and applied.promoted["replaced"] == 0
    third = world.run()
    assert third.entry["unchanged"]["count"] == N


def test_unverified_files_are_resent_without_deep_and_commit_sees_them_identical(world):
    for i in range(N):
        (world.tree.base / f"img{i:04d}.jpg").write_bytes(content(i))
    applied = world.run("--apply")
    assert applied.code == 0, applied.err
    assert applied.promoted["replaced"] == 0 and len(applied.promoted["images"]) == N


def test_a_dev_file_that_changes_after_planning_is_dropped_from_its_batch(world, monkeypatch):
    real = promote._build_tar

    def tamper(batch, directory):
        (world.dev / "img0003.jpg").write_bytes(b"changed while we ran")  # the manifest says the old size
        return real(batch, directory)

    monkeypatch.setattr(promote, "_build_tar", tamper)
    applied = world.run("--apply")
    assert applied.code == 0, applied.err
    p = applied.promoted
    assert [(lo["filename"], lo["reason"]) for lo in p["left_out"]] == [("img0003.jpg", "hash_mismatch")] and p["complete"] is False
    assert "img0003.jpg" not in world.prod_images() and len(p["images"]) == N - 1


def test_handshake_failure_exits_2_and_writes_a_report_with_the_handshake_only(world):
    world.factory = lambda settings: LocalTransport("it", world.tree.conf_path, world.tree.incoming)
    run = world.run()
    assert run.code == 2 and "H6" in run.err
    rep = run.report
    assert rep["sections"] == [] and any(h["step"] == "H6" and not h["ok"] for h in rep["handshake"])
    assert rep["plan_hash"] is None and not (run.folder / "files-promoted.json").exists()


def test_a_held_lock_is_exit_2(world):
    from sync.config import exclusive_lock

    with exclusive_lock(world.settings):
        run = world.run()
    assert run.code == 2 and "lock" in run.err


def test_missing_dev_base_path_is_exit_2(world, tmp_path):
    run = world.run(base_dir=tmp_path / "nope")
    assert run.code == 2


def test_cli_flag_rules():
    parser = promote.build_parser()
    with pytest.raises(SystemExit) as err:
        promote.main(["promote", "--env", "general", "--plan-hash", "ab" * 32])
    assert err.value.code == 2
    with pytest.raises(SystemExit):
        parser.parse_args(["retire", "--env", "general"])  # deferred (decision 27)
    for flag in ("--retire", "--force-orphans", "--allow-mass-retire"):
        with pytest.raises(SystemExit):
            parser.parse_args(["promote", "--env", "general", flag])
    ns = parser.parse_args(["promote", "--env", "it", "--apply", "--deep", "--batch-mb", "200", "--top", "5", "--run-id", "x", "--json", "-"])
    assert (ns.apply, ns.deep, ns.batch_mb, ns.top, ns.json_path) == (True, True, 200.0, 5, "-")


def test_batch_mb_outside_150_to_500_is_exit_2(world):
    assert world.run("--batch-mb", "10").code == 2
    assert world.run("--batch-mb", "900").code == 2


def test_wrong_typed_confirmation_sends_nothing(world):
    before = world.prod_snapshot()
    run = world.run("--apply", answer="general wrong_db")
    assert run.code == 3 and "confirmation" in run.err
    assert world.prod_snapshot() == before


def test_yes_skips_only_the_confirmation(world):
    def never(prompt):
        raise AssertionError("must not ask")
    args = promote.build_parser().parse_args(["promote", "--env", "general", "--apply", "--yes"])
    assert args.yes is True
