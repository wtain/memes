"""P7-T2: `sync.run` over the REAL pull, files and promote modules on the two-schema fixture (the dev schema plays dev, the prod schema
plays prod, a LocalTransport on a temporary gate tree plays the gate and the upload account).

Only `ocrdb_test` (via `sync_pair`). The handshake is replaced by a canned success (the fixture has no tunnel, roles or capabilities
endpoint; its logic is unit tested in tests/sync/test_connect.py), exactly like the promote and pull integration tests; the driver's own
step 0 (`default_handshake`, with the banner and the read-back of the prod database name) still runs on top of it. Not covered here:
a real ssh tunnel (one tunnel for the whole run is wired in `run_driver` and only exercised by the unit tests' injected opener), a real
sshd and real network timing.
"""
import contextlib
import hashlib
import io
import json

import pytest

from sync import connect
from sync.config import SyncSettings
from sync.files.transport import LocalTransport
from sync.promote import apply as promote_apply
from sync.run import cli
from sync.verify import cli as verify_cli
from sync.verify import runtime as verify_rt
from tests.integration.sync.test_promote_plan import Side, uid
from tests.integration.sync.test_pull import imp, save, sh
from tests.sync.gate_helpers import ENV_NAME, Tree

RUN = "20261113T120000Z-{:08x}"
ANSWER = "general ocrdb_test"
SENT = "SENTINEL-RUN-TEXT-61d0e2"
REVISION = "abc1234def"  # git sha of the dev tree and `memes.revision` of the fixture's prod session (D1 of the verify step)


def rid(n: int) -> str:
    return RUN.format(n)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Rig:
    def __init__(self, pair, tmp_path, monkeypatch, **pull_and_promote):
        self.pair, self.tmp = pair, tmp_path
        self.dev_side, self.prod_side = Side(pair.dev_conn()), Side(pair.prod_conn())
        (tmp_path / "prod").mkdir()
        self.tree = Tree(tmp_path / "prod", free_floor_pct="0")
        self.images = tmp_path / "dev-images"
        self.images.mkdir()
        self.state = tmp_path / "state"
        self.settings = SyncSettings("general", {
            "state_dir": str(self.state), "dev": {"database": "ocrdb_test"},
            "prod": {"database": "ocrdb_test", "reader_role": "memes_general_sync_r", "writer_role": "memes_general_sync_w",
                     "api_url": "", "gate_alias": "g", "upload_alias": "u", "ssh_alias": "a"},
            "pull": {"bug_reports": {"dev_dir": str(tmp_path / "dev-bug")}}, "verify": {"files": {"modes": []}}, **pull_and_promote}, root=tmp_path)
        self.transport = LocalTransport(ENV_NAME, self.tree.conf_path, self.tree.incoming)
        self.opened = []
        monkeypatch.setattr(connect, "handshake", lambda *a, **k: [{"step": "H1", "ok": True, "detail": "ok"}])
        # P6 verify step: the fixture's role is a superuser and its database keeps the default PUBLIC grants, so S1 and S2 cannot be green here
        monkeypatch.setattr(verify_cli, "gather_grants", lambda settings, prod, reader: {"writer": [], "reader": [], "app": [], "app_facts": None})
        monkeypatch.setattr(verify_rt, "gather_s2", lambda conn, app_facts: {"public_connect": False, "trust_network": 0, "app_super": False, "app_create": False})

    # --- seeding -----------------------------------------------------------------------------------------------------------------
    def dev_image(self, n, text="hello world", note=None):
        i = self.dev_side.image(n, ocr=text, note=note)
        data = f"image-{n}-".encode() * 8
        (self.images / f"img{n:04d}.jpg").write_bytes(data)
        self.dev_side.x("update images set content_hash = %s where id = %s", (sha(data), i))
        return i

    def dev_images(self, count):
        for n in range(1, count + 1):
            self.dev_image(n)

    # --- connections: one opener for every tool, a fresh session per call like the real one ---------------------------------------
    def opener(self, env, tool, mode="normal", settings=None):
        self.opened.append((tool, mode))
        pair = self.pair

        @contextlib.contextmanager
        def cm():
            conns = []

            def mk(raw_fn, kind, app_env=None):
                raw = raw_fn()
                raw.autocommit = True
                c = connect.SyncConnection(raw, kind, None, None, app_env)
                conns.append(c)
                return c

            try:
                if tool == "L":
                    dev, prod = mk(pair.dev_conn, connect.DEV_RW, "general"), mk(pair.prod_conn, connect.PROD_READER)
                    prod.query("SET default_transaction_read_only = on")
                elif tool == "F":
                    dev, prod = mk(pair.dev_conn, connect.DEV_RO, "general"), None
                else:
                    dev, prod = mk(pair.dev_conn, connect.DEV_RO, "general"), mk(pair.prod_conn, connect.PROD_WRITER)
                reader = None
                if tool == "V":  # the verify tool also opens the class P reader
                    reader = mk(pair.prod_conn, connect.PROD_READER)
                    reader.query("SET default_transaction_read_only = on")
                if prod is not None:
                    prod.query(f"SET memes.revision = '{REVISION}'")
                yield {"dev": dev, "prod": prod, "prod_reader_conn": reader}
            finally:
                for c in conns:
                    c.close()

        return cm()

    def drive(self, *argv, run_id, answer=ANSWER, runners=None, ask=None):
        out, err = io.StringIO(), io.StringIO()
        args = cli.build_parser().parse_args(["--env", "general", *argv, "--run-id", run_id])
        self.asked = []

        def _ask(prompt):
            self.asked.append(prompt)
            return answer

        code = cli.run_driver(args, settings=self.settings, session_opener=self.opener, capabilities=lambda url: {"stage": "prod", "environment": "general"},
                              transport=self.transport, git_sha=REVISION,
                              transport_factory=lambda s: LocalTransport(ENV_NAME, self.tree.conf_path, self.tree.incoming),
                              base_dir=str(self.images), runners=runners, out=out, err=err, ask=ask or _ask)
        folder = self.state / "runs" / run_id
        return type("Run", (), {"code": code, "out": out.getvalue(), "err": err.getvalue(), "folder": folder})

    # --- what is where --------------------------------------------------------------------------------------------------------------
    def prod_images(self):
        return {p.name for p in self.tree.base.iterdir()}

    def prod_count(self, table="images"):
        return self.prod_side.scalar(f'select count(*) from "{table}"')

    def dev_count(self, table):
        return self.dev_side.scalar(f'select count(*) from "{table}"')


@pytest.fixture()
def rig(sync_pair, tmp_path, monkeypatch):
    sync_pair.reset()
    r = Rig(sync_pair, tmp_path, monkeypatch)
    try:
        yield r
    finally:
        for side in (r.dev_side, r.prod_side):
            side.raw.close()


def report_of(folder):
    return json.loads((folder / "report.json").read_text())


def run_json(folder):
    return json.loads((folder / "run.json").read_text())


def section(doc, name):
    return next(s for s in doc["sections"] if s["name"] == name)


def images_entry(doc, name):
    return next(e for e in section(doc, name)["entries"] if e["name"] == "images")


# --- the dry run ---------------------------------------------------------------------------------------------------------------------

def test_dry_run_on_the_fixture_writes_one_combined_report_and_nothing_else(rig):
    rig.dev_images(6)
    run = rig.drive(run_id=rid(1))
    assert run.code == 0, (run.out, run.err)
    doc = report_of(run.folder)
    assert (run.folder / "dry").exists() and doc["dry"] is True and doc["tool"] == "sync.run"
    assert [s["name"] for s in doc["sections"]] == ["pull", "files", "data", "verify"]
    assert images_entry(doc, "files")["added"]["count"] == 6 and images_entry(doc, "files")["overwritten"]["count"] == 0
    data = images_entry(doc, "data")  # the dependency rule: the files are not on prod yet
    assert data["added"]["count"] == 0 and data["waiting_for_files"]["added"]["count"] == 6
    assert section(doc, "data")["totals"]["expected_added"]["count"] == 6 and doc["dependency_note"]
    assert doc["apply_command"] == f"python -m sync.run --env general --apply --plan-hash {doc['plan_hash']}"
    assert run.out.count("plan hash:") == 1 and "apply: python -m sync.run" in run.out
    rj = run_json(run.folder)
    assert rj["dry"] is True and [s["name"] for s in rj["steps"]] == ["handshake", "pull", "files", "data", "verify"]
    assert rj["plan_hash"] == doc["plan_hash"]
    # the verify section is the drift of prod against dev as of before the apply (P6), without any check, and part of the hash
    ver = images_entry(doc, "verify")
    assert ver["added"]["count"] == 6 and "checks" not in section(doc, "verify") and not (run.folder / "verify.json").exists()
    assert [e["name"] for e in section(doc, "verify")["entries"]][-1] == "files"
    # nothing was written on either side, and no file a consumer accepts
    assert rig.prod_count() == 0 and rig.prod_images() == set() and rig.dev_count("meme_saves") == 0
    assert not (rig.state / "pull_state.json").exists() and not list(rig.state.rglob("files-promoted.json"))
    assert not list(rig.state.rglob("committed.json"))
    assert sorted(p.name for p in (rig.state / "runs").iterdir()) == [rid(1)]


def test_the_dry_run_hash_is_reproducible(rig):
    rig.dev_images(4)
    h1 = report_of(rig.drive(run_id=rid(1)).folder)["plan_hash"]
    h2 = report_of(rig.drive(run_id=rid(2)).folder)["plan_hash"]
    assert h1 == h2
    rig.dev_image(5)
    assert report_of(rig.drive(run_id=rid(3)).folder)["plan_hash"] != h1


# --- first seed, then nothing to do ----------------------------------------------------------------------------------------------------

def test_first_run_seeds_everything_and_the_second_run_plans_nothing(rig):
    rig.dev_images(6)
    rig.dev_side.x("update ocr_texts set text = %s where image_id = %s", (SENT, uid(1)))
    dry = rig.drive("--first-seed", run_id=rid(1))
    assert dry.code == 0, (dry.out, dry.err)
    h = report_of(dry.folder)["plan_hash"]

    run = rig.drive("--first-seed", "--apply", "--plan-hash", h, run_id=rid(2))
    assert run.code == 0, (run.out, run.err)
    assert len(rig.asked) == 1 and "'general ocrdb_test'" in rig.asked[0]  # the typed confirmation, once, for the whole run
    assert ("R", "first-seed") in rig.opened and ("P", "first-seed") in rig.opened
    # prod now equals dev for the promoted set, files and rows
    assert rig.prod_count() == 6 and rig.prod_count("embeddings") == 6 and rig.prod_count("ocr_texts") == 6
    assert rig.prod_images() == {f"img{n:04d}.jpg" for n in range(1, 7)}
    assert (rig.tree.base / "img0003.jpg").read_bytes() == (rig.images / "img0003.jpg").read_bytes()
    # one run folder, one run id, every step's file in it
    assert not (run.folder / "dry").exists()
    promoted = json.loads((run.folder / "files-promoted.json").read_text())
    assert promoted["run_id"] == rid(2) and promoted["complete"] is True and len(promoted["images"]) == 6
    committed = json.loads((run.folder / "committed.json").read_text())
    assert committed["run_id"] == rid(2) and sum(len(b["ids"]) for b in committed["batches"]) == 6
    assert json.loads((rig.state / "pull_state.json").read_text())["run_id"] == rid(2)
    doc = report_of(run.folder)
    assert doc["dry"] is False and doc["tool"] == "sync.run" and doc["plan_hash"] == h
    assert doc["result"]["files"]["images"]["added"] == 6 and doc["result"]["data"]["images"]["added"] == 6
    assert doc["result"]["verify"]["images"]["verify"]["differences"] == {}  # promote's verify phase ran inside the data step
    rj = run_json(run.folder)
    assert [s["status"] for s in rj["steps"]] == ["ok"] * 5 and all(s["exit_code"] == 0 for s in rj["steps"][:4])
    assert rj["steps"][4]["reason"] == "sync_verify" and rj["flags"]["first_seed"] is True
    # step 4 is the whole sync.verify: verify.json in the run folder, the checks all clear, the drift empty (P6)
    vj = json.loads((run.folder / "verify.json").read_text())
    assert vj["run_id"] == rid(2) and vj["summary"]["error"] == 0 and vj["summary"]["exit_code"] == 0
    assert vj["summary"]["drift"]["images"] == {"added": 0, "overwritten": 0, "unchanged": 6, "left_behind": 0}
    assert {c["id"]: c["status"] for c in vj["checks"]}["D2"] == "ok" and (rig.state / "runs" / rid(2) / "dry").exists() is False
    assert [s["name"] for s in doc["sections"]][-1] == "verify" and images_entry(doc, "verify")["added"]["count"] == 6  # the plan: before the apply
    # redaction: the OCR sentinel reached prod and no report, state file or console
    assert rig.prod_side.scalar("select count(*) from ocr_texts where text = %s", (SENT,)) == 1
    for p in list(rig.state.rglob("*.json")) + list(rig.state.rglob("*.md")):
        assert SENT not in p.read_text(), p
    assert SENT not in run.out + run.err
    # the scratch dry folder of the re-plan is gone
    assert sorted(p.name for p in (rig.state / "runs").iterdir()) == [rid(1), rid(2)]

    # the second run plans nothing
    again = rig.drive(run_id=rid(3))
    assert again.code == 0, (again.out, again.err)
    d = report_of(again.folder)
    for name in ("files", "data"):
        e = images_entry(d, name)
        assert (e["added"]["count"], e["overwritten"]["count"], e["unchanged"]["count"]) == (0, 0, 6), name
    assert "waiting_for_files" not in images_entry(d, "data")
    assert all(e["added"]["count"] == 0 and e["overwritten"]["count"] == 0 for e in section(d, "pull")["entries"])
    h3 = d["plan_hash"]
    second = rig.drive("--apply", "--yes", "--plan-hash", h3, run_id=rid(4))
    assert second.code == 0, (second.out, second.err)
    assert rig.asked == []  # --yes
    assert not (second.folder / "committed.json").exists()  # nothing was promoted
    assert report_of(second.folder)["result"]["files"]["images"]["added"] == 0 and rig.prod_count() == 6


def test_a_change_on_dev_after_the_dry_run_makes_the_apply_refuse_before_any_write(rig):
    rig.dev_images(4)
    h = report_of(rig.drive(run_id=rid(1)).folder)["plan_hash"]
    rig.dev_image(5)  # the world changed since the dry run
    run = rig.drive("--apply", "--plan-hash", h, run_id=rid(2))
    assert run.code == 3 and "the plan changed since the dry run" in run.err and rig.asked == []
    assert rig.prod_count() == 0 and rig.prod_images() == set() and not list(rig.state.rglob("files-promoted.json"))


def test_a_wrong_typed_confirmation_writes_nothing(rig):
    rig.dev_images(3)
    run = rig.drive("--apply", answer="general ocrdb", run_id=rid(1))
    assert run.code == 3 and "typed confirmation did not match" in run.err
    assert rig.prod_count() == 0 and rig.prod_images() == set()


# --- reader activity, changed files and rows, left behind ------------------------------------------------------------------------------------

def test_pull_files_and_data_each_do_their_part_in_one_run(rig):
    rig.dev_images(6)
    assert rig.drive("--first-seed", "--apply", "--yes", run_id=rid(1)).code == 0
    # readers were active on prod; dev re-embedded image 2 (new bytes + note), image 6 was flagged on dev afterwards
    sh(rig.prod_side, 1, query="a query")
    save(rig.prod_side, 1, 1, search_n=1)
    imp(rig.prod_side, 100, 2)
    data = b"new bytes for image two"
    (rig.images / "img0002.jpg").write_bytes(data)
    rig.dev_side.x("update images set content_hash = %s where id = %s", (sha(data), uid(2)))
    rig.dev_side.x("insert into description_notes (image_id, text, updated_at) values (%s, 'a note', '2026-01-02')", (uid(2),))
    rig.dev_side.x("insert into image_extras (image_id, flagged) values (%s, true)", (uid(6),))

    dry = rig.drive(run_id=rid(2))
    assert dry.code == 0, (dry.out, dry.err)
    d = report_of(dry.folder)
    pull = {e["name"]: e for e in section(d, "pull")["entries"]}
    assert pull["meme_saves"]["added"]["count"] == 1 and pull["meme_impressions"]["added"]["count"] == 1 and pull["search_history"]["added"]["count"] == 1
    files = images_entry(d, "files")
    assert files["overwritten"]["ids"] == [uid(2)] and files["overwritten"]["size_changed"]["ids"] == [uid(2)]
    assert files["left_behind"]["names"] == ["img0006.jpg"]  # the flagged image's file stays on prod
    data_e = images_entry(d, "data")
    assert data_e["waiting_for_files"]["overwritten"]["ids"] == [uid(2)]  # image 2's rows wait for its replaced file
    assert data_e["left_behind"]["ids"] == [uid(6)]

    run = rig.drive("--apply", "--plan-hash", d["plan_hash"], run_id=rid(3))
    assert run.code == 0, (run.out, run.err)
    assert rig.dev_count("meme_saves") == 1 and rig.dev_count("meme_impressions") == 1  # pulled
    assert (rig.tree.base / "img0002.jpg").read_bytes() == data  # replaced on prod
    assert (rig.tree.base / "img0006.jpg").exists() and rig.prod_count() == 6  # left behind, never deleted (decision 27)
    assert rig.prod_side.scalar("select count(*) from description_notes where image_id = %s", (uid(2),)) == 1
    res = report_of(run.folder)["result"]
    assert res["files"]["images"]["overwritten"] == 1 and res["data"]["images"]["overwritten"] == 1 and res["pull"]["meme_saves"]["added"] == 1


# --- a failure in the data step and the resume --------------------------------------------------------------------------------------------

def test_a_failure_in_the_data_step_leaves_the_earlier_steps_committed_and_the_rerun_resumes(sync_pair, tmp_path, monkeypatch):
    sync_pair.reset()
    rig = Rig(sync_pair, tmp_path, monkeypatch, promote={"batch_size": 3})
    try:
        rig.dev_images(6)

        def boom(n, ids):
            if n == 2:
                raise RuntimeError(f"kill the batch mid-way {SENT}")

        monkeypatch.setattr(promote_apply, "_before_commit", boom)
        first = rig.drive("--first-seed", "--apply", "--yes", run_id=rid(1))
        assert first.code == 1, (first.out, first.err)
        assert "stopped at step data (exit 1)" in first.out and "completed: handshake, pull, files" in first.out and "not run: verify" in first.out
        assert "python -m sync.run --env general --first-seed --apply" in first.out
        # the files step committed: every file is on prod; the data step committed batch 1 only
        assert len(rig.prod_images()) == 6 and rig.prod_count() == 3
        assert json.loads((first.folder / "files-promoted.json").read_text())["complete"] is True
        assert [b["n"] for b in json.loads((first.folder / "committed.json").read_text())["batches"]] == [1]
        assert (rig.state / "pull_state.json").exists()  # the pull before it is done too
        rj = run_json(first.folder)
        assert {s["name"]: s["status"] for s in rj["steps"]} == {"handshake": "ok", "pull": "ok", "files": "ok", "data": "failed", "verify": "not_run"}
        assert rj["steps"][3]["exit_code"] == 1
        everything = first.out + first.err + "".join(p.read_text() for p in rig.state.rglob("*.json"))
        assert SENT not in everything

        monkeypatch.setattr(promote_apply, "_before_commit", lambda n, ids: None)
        second = rig.drive("--first-seed", "--apply", "--yes", run_id=rid(2))  # the same command again
        assert second.code == 0, (second.out, second.err)
        assert rig.prod_count() == 6 and rig.prod_count("embeddings") == 6
        committed = json.loads((second.folder / "committed.json").read_text())
        assert sum(len(b["ids"]) for b in committed["batches"]) == 3  # only what was missing
        assert report_of(second.folder)["result"]["files"]["images"]["added"] == 0  # the files step found nothing to send
        assert {s["status"] for s in run_json(second.folder)["steps"]} == {"ok"}
    finally:
        for side in (rig.dev_side, rig.prod_side):
            side.raw.close()


def test_a_dev_running_batch_stops_the_plan_before_any_step_writes(rig):
    rig.dev_images(3)
    rig.dev_side.x("insert into batch_runs (run_id, kind, trigger, status) values (gen_random_uuid(), 'k', 'manual', 'started')")
    run = rig.drive("--apply", "--yes", run_id=rid(1))
    assert run.code == 2 and "step data could not plan" in run.err
    assert rig.prod_count() == 0 and rig.prod_images() == set()
    doc = report_of(run.folder)
    assert any(p["id"] == "data.running_batches" and not p["ok"] for p in doc["preconditions"])
    ok = rig.drive("--apply", "--yes", "--ignore-running-batches", run_id=rid(2))  # the lift is forwarded to the step that owns it
    assert ok.code == 0, (ok.out, ok.err)
    assert rig.prod_count() == 3


def test_a_failing_check_of_the_verify_step_is_exit_4_after_everything_else_was_applied(rig):
    rig.dev_images(3)
    assert rig.drive("--first-seed", "--apply", "--yes", run_id=rid(1)).code == 0
    rig.prod_side.x("insert into batch_runs (run_id, kind, trigger, status) values (gen_random_uuid(), 'k', 'manual', 'completed')")  # class X on prod
    rig.prod_side.x("select pg_stat_force_next_flush()")
    run = rig.drive("--apply", "--yes", run_id=rid(2))
    assert run.code == 4, (run.out, run.err)
    rj = run_json(run.folder)
    verify = {s["name"]: s for s in rj["steps"]}["verify"]
    assert verify["status"] == "failed" and verify["exit_code"] == 4 and verify["reason"] == "verify_checks_failed"
    assert {s["status"] for s in rj["steps"][:4]} == {"ok"}
    vj = json.loads((run.folder / "verify.json").read_text())
    assert {c["id"]: c["status"] for c in vj["checks"]}["D6"] == "error"
    assert "stopped at step verify (exit 4)" in run.out
