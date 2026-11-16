"""P7-T1: the `sync.run` driver with fake step runners and a fake handshake (no database, no network, no environment files).

Covers ordering, `--stop-after`, failure stops, the combined plan hash, lock sharing, flag forwarding, the typed confirmation, the resume
message and redaction. The real modules on the two-schema fixture are in tests/integration/sync/test_run.py."""
import io
import json
import re

import pytest

from sync import connect, report
from sync.config import SyncSettings, exclusive_lock
from sync.run import cli
from sync.run.cli import StepResult

DB = "memes_general"
HS = [{"step": "H1", "ok": True, "detail": "ok"}, {"step": "H6", "ok": True, "detail": "ok"}]
SENT = "SENTINEL-TEXT-5c1e90"
H64 = "ab" * 32


def settings_for(tmp_path) -> SyncSettings:
    return SyncSettings("general", {"state_dir": str(tmp_path / "state"), "dev": {"database": "devdb"}, "prod": {"database": DB}}, root=tmp_path)


def doc_for(step, *, added=(), over=(), guards=(), detail="ok", result=None, pre_detail=None):
    tool, kind, name = {"pull": ("sync.pull", "pulled", "meme_saves"), "files": ("sync.files promote", "files", "images"),
                        "data": ("sync.promote", "images", "images"), "verify": ("sync.verify", "images", "images")}[step]
    entry = report.Entry(name, kind, added=report.IdSet(ids=list(added)), overwritten=report.Overwritten(ids=list(over)))
    section = report.Section(step, tool, entries=[entry], guards=list(guards))
    return report.Document(env="general", run_id="20261113T120000Z-00000001", tool=tool, dry=result is None, sections=[section],
                           handshake=[{"step": "H1", "ok": True, "detail": "ok"}],
                           preconditions=[{"id": "p1", "ok": True, "detail": pre_detail if pre_detail is not None else detail}], result=result)


class Fakes:
    """Step runners that record their calls and return canned documents."""

    def __init__(self, **plans):
        self.calls = []
        self.plans = plans  # step -> kwargs of doc_for
        self.fail = {}  # (step, apply) -> exit code
        self.results = {}  # step -> result dict of the apply document

    def runner(self, step):
        def run(ctx, run_id, apply):
            self.calls.append((step, run_id, apply))
            code = self.fail.get((step, apply), 0)
            if code and not apply:
                return StepResult(code, report.Document(env="general", run_id=run_id, tool="x", handshake=[{"step": "H3", "ok": False, "detail": "bad"}]))
            doc = doc_for(step, **self.plans.get(step, {}), result=self.results.get(step) if apply else None)
            return StepResult(code, doc)

        return run

    @property
    def runners(self):
        return {s: self.runner(s) for s in (*cli.CHILD_STEPS, "verify")}

    def steps(self, apply):
        return [s for s, _, a in self.calls if a is apply]


def drive(tmp_path, *argv, fakes=None, answer=None, handshake=None, run_id="20261113T120000Z-0000000a", **kw):
    fakes = fakes or Fakes()
    if "--apply" in argv and "--yes" in argv and "--plan-hash" not in argv and "--no-bind" not in argv:
        # --apply --yes needs the dry run's hash (review finding M1): bind it the way a person would, from a dry run of the same fakes
        import copy
        import secrets
        flags = [a for a in argv if a not in ("--apply", "--yes")]
        dry = drive(tmp_path, *flags, fakes=copy.deepcopy(fakes), handshake=handshake, run_id=f"20261113T110000Z-{secrets.token_hex(4)}", **kw)
        argv = (*argv, "--plan-hash", report_of(dry.folder)["plan_hash"] if dry.code == 0 else "0" * 64)  # a dry run that fails: nothing to bind
    argv = tuple(a for a in argv if a != "--no-bind")
    out, err = io.StringIO(), io.StringIO()
    args = cli.build_parser().parse_args(["--env", "general", *argv, "--run-id", run_id])
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answer if answer is not None else f"general {DB}"

    code = cli.run_driver(args, settings=settings_for(tmp_path), session_opener=object(), runners=fakes.runners,
                          handshake_fn=handshake or (lambda ctx, mode: (list(HS), DB)), out=out, err=err, ask=ask, **kw)
    folder = tmp_path / "state" / "runs" / run_id
    return type("R", (), {"code": code, "out": out.getvalue(), "err": err.getvalue(), "folder": folder, "fakes": fakes, "asked": asked})


def report_of(folder):
    return json.loads((folder / "report.json").read_text())


def run_json(folder):
    return json.loads((folder / "run.json").read_text())


# --- the dry run ------------------------------------------------------------------------------------------------------------------

def test_dry_run_plans_every_step_in_order_and_writes_one_combined_report(tmp_path):
    f = Fakes(files={"added": [1, 2], "over": [3]}, data={"added": [2, 4], "over": [3, 5]})
    r = drive(tmp_path, fakes=f)
    assert r.code == 0, r.err
    assert [c[0] for c in f.calls] == ["pull", "files", "data", "verify"] and not any(c[2] for c in f.calls)
    assert {c[1] for c in f.calls} == {"20261113T120000Z-0000000a"}  # the children plan under the run's own id
    doc = report_of(r.folder)
    assert doc["tool"] == "sync.run" and doc["dry"] is True and (r.folder / "dry").exists()
    assert [s["name"] for s in doc["sections"]] == ["pull", "files", "data", "verify"]
    assert doc["dependency_note"] == report.DEPENDENCY_NOTE and doc["handshake"] == HS
    data = next(s for s in doc["sections"] if s["name"] == "data")["entries"][0]
    assert data["waiting_for_files"]["added"]["ids"] == [2] and data["waiting_for_files"]["overwritten"]["ids"] == [3]
    assert data["added"]["ids"] == [4] and data["overwritten"]["ids"] == [5]
    assert doc["apply_command"] == f"python -m sync.run --env general --apply --plan-hash {doc['plan_hash']}"
    assert f"plan hash: {doc['plan_hash']}" in r.out and "apply: python -m sync.run" in r.out
    assert (r.folder / "report.md").read_text().count("## Section ") == 4


def test_the_verify_section_is_the_drift_the_verify_runner_planned_and_run_json_plans_it(tmp_path):
    # P6 is built: the plan's verify section is the document of runners["verify"] (the drift), not an informational placeholder
    r = drive(tmp_path, fakes=Fakes(verify={"added": [7]}))
    verify = next(s for s in report_of(r.folder)["sections"] if s["name"] == "verify")
    assert verify["entries"][0]["added"]["ids"] == [7] and "checks" not in verify
    rj = run_json(r.folder)
    assert [s["name"] for s in rj["steps"]] == ["handshake", "pull", "files", "data", "verify"]
    assert [s["status"] for s in rj["steps"]] == ["planned"] * 5
    # a different drift is a different plan hash (the verify section joins the combined hash)
    other = report_of(drive(tmp_path, fakes=Fakes(verify={"added": [8]}), run_id="20261113T120000Z-0000000f").folder)
    assert other["plan_hash"] != report_of(r.folder)["plan_hash"]
    assert rj["dry"] is True and rj["run_id"] == "20261113T120000Z-0000000a" and rj["env"] == "general"
    assert rj["plan_hash"] == report_of(r.folder)["plan_hash"] and set(rj["steps"][0]) >= {"name", "status", "started_at", "finished_at", "exit_code"}


def test_combined_hash_is_the_hash_of_the_ordered_section_hashes_and_is_reproducible(tmp_path):
    r1 = drive(tmp_path, run_id="20261113T120000Z-00000001")
    r2 = drive(tmp_path, run_id="20261113T120000Z-00000002")
    d1, d2 = report_of(r1.folder), report_of(r2.folder)
    assert d1["plan_hash"] == d2["plan_hash"] and re.fullmatch(r"[0-9a-f]{64}", d1["plan_hash"])
    assert d1["plan_hash"] == report._sha([s["plan_hash"] for s in d1["sections"]])
    changed = Fakes(pull={"added": [9]})
    d3 = report_of(drive(tmp_path, fakes=changed, run_id="20261113T120000Z-00000003").folder)
    assert d3["plan_hash"] != d1["plan_hash"]
    assert [s["plan_hash"] for s in d3["sections"]][1:] == [s["plan_hash"] for s in d1["sections"]][1:]


def test_stop_after_limits_the_steps(tmp_path):
    r = drive(tmp_path, "--stop-after", "files")
    assert r.fakes.steps(False) == ["pull", "files"]
    doc = report_of(r.folder)
    assert [s["name"] for s in doc["sections"]] == ["pull", "files"] and "--stop-after files" in doc["apply_command"]
    rj = run_json(r.folder)
    assert {s["name"]: s["status"] for s in rj["steps"]}["data"] == "skipped_with_reason"
    r = drive(tmp_path, "--stop-after", "handshake", run_id="20261113T120000Z-0000000b")
    assert r.code == 0 and r.fakes.calls == [] and "stopped after the handshake" in r.out


def test_dry_run_exits_zero_and_says_a_guard_would_refuse(tmp_path):
    guard = report.Guard("mass_change", True, 30, 20)
    r = drive(tmp_path, fakes=Fakes(data={"guards": [guard]}))
    assert r.code == 0 and "an apply would refuse (exit 3): data:mass_change" in r.out
    assert "guard mass_change would trip" in r.out


def test_a_failing_plan_step_exits_with_its_code_and_writes_a_failure_report(tmp_path):
    f = Fakes()
    f.fail[("files", False)] = 2
    r = drive(tmp_path, fakes=f)
    assert r.code == 2 and f.steps(False) == ["pull", "files"] and "step files could not plan" in r.err
    doc = report_of(r.folder)
    assert [s["name"] for s in doc["sections"]] == ["pull"] and doc["plan_hash"]
    assert {"step": "files.H3", "ok": False, "detail": "bad"} in doc["handshake"] and doc["apply_command"] is None


def test_handshake_failure_stops_before_any_step(tmp_path):
    def bad(ctx, mode):
        raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch", [{"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

    r = drive(tmp_path, "--apply", "--yes", handshake=bad)
    assert r.code == 2 and r.fakes.calls == [] and "H4" in r.err
    doc = report_of(r.folder)
    assert doc["sections"] == [] and doc["handshake"][0]["ok"] is False and doc["plan_hash"] is None
    assert run_json(r.folder)["steps"][0]["status"] == "failed"


def test_a_held_lock_is_exit_2_and_no_step_runs(tmp_path):
    settings = settings_for(tmp_path)
    with exclusive_lock(settings):
        r = drive(tmp_path)
    assert r.code == 2 and "lock" in r.err and r.fakes.calls == []


# --- the apply ------------------------------------------------------------------------------------------------------------------------

def dry_hash(tmp_path, *argv, fakes=None, run_id="20261113T110000Z-00000001"):
    return report_of(drive(tmp_path, *argv, fakes=fakes, run_id=run_id).folder)["plan_hash"]


def test_apply_replans_then_runs_each_step_once_under_one_run_id(tmp_path):
    h = dry_hash(tmp_path, fakes=Fakes(data={"added": [4]}))
    f = Fakes(data={"added": [4]})
    f.results["data"] = {"data": {"images": report.ResultEntry(added=1)}, "verify": {"images": report.ResultEntry(checked=1)}}
    r = drive(tmp_path, "--apply", "--plan-hash", h, fakes=f)
    assert r.code == 0, (r.out, r.err)
    replan, applied = [c for c in f.calls if not c[2]], [c for c in f.calls if c[2]]
    assert [c[0] for c in replan] == ["pull", "files", "data", "verify"] and [c[0] for c in applied] == ["pull", "files", "data", "verify"]
    scratch = {c[1] for c in replan}
    assert len(scratch) == 1 and scratch != {"20261113T120000Z-0000000a"}  # the re-plan runs in its own (removed) dry folder
    assert {c[1] for c in applied} == {"20261113T120000Z-0000000a"}  # ONE run id for the four applies
    assert not (tmp_path / "state" / "runs" / scratch.pop()).exists()
    doc = report_of(r.folder)
    assert doc["dry"] is False and not (r.folder / "dry").exists() and doc["result"]["data"]["images"]["added"] == 1 and "verify" in doc["result"]
    rj = run_json(r.folder)
    assert [s["status"] for s in rj["steps"]] == ["ok", "ok", "ok", "ok", "ok"] and rj["plan_hash"] == h and rj["dry"] is False
    assert rj["steps"][4]["reason"] == "sync_verify" and "complete" in r.out


def test_a_plan_that_changed_since_the_dry_run_is_exit_3_before_any_write(tmp_path):
    h = dry_hash(tmp_path)
    f = Fakes(pull={"added": [1]})  # the world changed
    r = drive(tmp_path, "--apply", "--plan-hash", h, fakes=f)
    assert r.code == 3 and "the plan changed since the dry run" in r.err
    assert not any(c[2] for c in f.calls) and r.asked == []
    assert f.steps(False) == ["pull", "files", "data", "verify"]
    assert drive(tmp_path, "--apply", "--plan-hash", "f" * 64, run_id="20261113T120000Z-0000000c").code == 3


def test_without_a_plan_hash_the_apply_still_confirms_and_runs(tmp_path):
    r = drive(tmp_path, "--apply")
    assert r.code == 0 and len(r.asked) == 1


def test_typed_confirmation_is_asked_once_with_env_and_the_prod_database_read_back(tmp_path):
    r = drive(tmp_path, "--apply")
    assert len(r.asked) == 1 and f"'general {DB}'" in r.asked[0]
    # the database name is what the handshake read back from the server, not a setting
    r = drive(tmp_path, "--apply", handshake=lambda ctx, mode: (list(HS), "memes_general_readback"), answer="general memes_general_readback",
              run_id="20261113T120000Z-0000000d")
    assert r.code == 0 and "'general memes_general_readback'" in r.asked[0]


@pytest.mark.parametrize("answer", ["", "general", "general devdb", "metal memes_general", "general memes_general extra", "yes"])
def test_a_wrong_confirmation_is_exit_3_and_writes_nothing(tmp_path, answer):
    r = drive(tmp_path, "--apply", answer=answer)
    assert r.code == 3 and "typed confirmation did not match" in r.err and not any(c[2] for c in r.fakes.calls)


def test_apply_yes_without_a_plan_hash_is_a_usage_error(tmp_path):
    with pytest.raises(cli.UsageProblem, match="--plan-hash"):
        cli.validate_args(cli.build_parser().parse_args(["--env", "general", "--apply", "--yes"]))
    cli.validate_args(cli.build_parser().parse_args(["--env", "general", "--apply", "--yes", "--plan-hash", "ab" * 32]))
    cli.validate_args(cli.build_parser().parse_args(["--env", "general", "--apply"]))  # the typed confirmation still binds without --yes


def test_yes_skips_only_the_confirmation(tmp_path):
    r = drive(tmp_path, "--apply", "--yes")
    assert r.code == 0 and r.asked == [] and r.fakes.steps(True) == ["pull", "files", "data", "verify"]
    bad = drive(tmp_path, "--apply", "--yes", "--plan-hash", "0" * 64, run_id="20261113T120000Z-0000000e")
    assert bad.code == 3 and bad.fakes.steps(True) == []  # --yes does not skip the hash check


def test_a_failing_step_stops_the_run_names_what_is_done_and_the_resume_command(tmp_path):
    f = Fakes()
    f.fail[("files", True)] = 1
    r = drive(tmp_path, "--apply", "--yes", "--allow-growth", fakes=f)
    assert r.code == 1 and f.steps(True) == ["pull", "files"]  # data never started
    assert "stopped at step files (exit 1)" in r.out and "completed: handshake, pull" in r.out and "not run: data, verify" in r.out
    assert "python -m sync.run --env general --allow-growth --apply" in r.out and "--plan-hash" in r.out.split("run the same command again")[1].split("\n")[0]
    rj = run_json(r.folder)
    assert {s["name"]: s["status"] for s in rj["steps"]} == {"handshake": "ok", "pull": "ok", "files": "failed", "data": "not_run", "verify": "not_run"}
    assert rj["steps"][2]["exit_code"] == 1
    assert report_of(r.folder)["dry"] is False


def test_the_exit_code_of_the_failing_child_is_the_exit_code_of_the_run(tmp_path):
    for code in (1, 3, 4):
        f = Fakes()
        f.fail[("data", True)] = code
        r = drive(tmp_path, "--apply", "--yes", fakes=f, run_id=f"20261113T12000{code}Z-0000000f")
        # exit 4 of the data step (promote's own verify phase) still runs sync.verify, so the owner gets the checks
        assert r.code == code and f.steps(True) == ["pull", "files", "data"] + (["verify"] if code == 4 else [])
        v = {s["name"]: s for s in run_json(r.folder)["steps"]}["verify"]
        assert v["status"] == ("failed" if code == 4 else "not_run")
        assert (v.get("reason") == "promote_verify_phase") is (code == 4)


def test_resume_runs_the_same_command_again_and_the_earlier_steps_are_idempotent(tmp_path):
    f = Fakes()
    f.fail[("data", True)] = 1
    first = drive(tmp_path, "--apply", "--yes", fakes=f, run_id="20261113T120000Z-00000010")
    assert first.code == 1
    f.fail.clear()
    second = drive(tmp_path, "--apply", "--yes", fakes=f, run_id="20261113T120000Z-00000011")
    assert second.code == 0 and f.steps(True) == ["pull", "files", "data", "pull", "files", "data", "verify"]
    assert {s["status"] for s in run_json(second.folder)["steps"]} == {"ok"}


def test_an_unexpected_exception_in_a_step_is_exit_1_without_its_message(tmp_path):
    f = Fakes()
    runners = f.runners

    def boom(ctx, run_id, apply):
        if apply:
            raise KeyError(SENT)
        return runners["data"](ctx, run_id, apply)

    out, err = io.StringIO(), io.StringIO()
    dry = cli.build_parser().parse_args(["--env", "general", "--run-id", "20261113T110000Z-00000012"])
    assert cli.run_driver(dry, settings=settings_for(tmp_path), session_opener=object(), runners={**runners, "files": boom},
                          handshake_fn=lambda ctx, mode: (list(HS), DB), out=io.StringIO(), err=io.StringIO()) == 0
    h = report_of(tmp_path / "state" / "runs" / "20261113T110000Z-00000012")["plan_hash"]
    args = cli.build_parser().parse_args(["--env", "general", "--apply", "--yes", "--plan-hash", h, "--run-id", "20261113T120000Z-00000012"])
    code = cli.run_driver(args, settings=settings_for(tmp_path), session_opener=object(), runners={**runners, "files": boom},
                          handshake_fn=lambda ctx, mode: (list(HS), DB), out=out, err=err)
    assert code == 1 and "KeyError" in err.getvalue() and SENT not in err.getvalue() + out.getvalue()


def test_apply_stop_after_runs_only_the_selected_steps(tmp_path):
    r = drive(tmp_path, "--apply", "--yes", "--stop-after", "pull")
    assert r.code == 0 and r.fakes.steps(True) == ["pull"] and r.fakes.steps(False) == ["pull"]


def test_verify_is_skipped_when_stop_after_ends_the_run_earlier(tmp_path):
    r = drive(tmp_path, "--apply", "--yes", "--stop-after", "files")
    v = {s["name"]: s for s in run_json(r.folder)["steps"]}["verify"]
    assert v["status"] == "skipped_with_reason" and v["reason"] == "stop_after" and "verify" not in r.fakes.steps(True)


def test_verify_step_maps_the_exit_code_of_sync_verify_to_the_run_json_status(tmp_path):
    ctx = cli.Ctx(env="general", settings=settings_for(tmp_path), args=None, out=io.StringIO(), err=io.StringIO(), opener=None)

    def runners(code):
        return {"verify": lambda c, rid, apply: StepResult(code)}

    rid = "20261113T120000Z-00000020"
    assert cli.verify_step(ctx, rid, runners(0), 0) == {"status": "ok", "exit_code": 0, "reason": "sync_verify"}
    assert cli.verify_step(ctx, rid, runners(4), 0) == {"status": "failed", "exit_code": 4, "reason": "verify_checks_failed"}
    assert cli.verify_step(ctx, rid, runners(2), 0) == {"status": "failed", "exit_code": 2, "reason": "verify_could_not_run"}
    assert cli.verify_step(ctx, rid, runners(0), 4) == {"status": "failed", "exit_code": 4, "reason": "promote_verify_phase"}

    def boom(c, r, apply):
        raise KeyError(SENT)

    out = cli.verify_step(ctx, rid, {"verify": boom}, 0)
    assert out == {"status": "failed", "exit_code": 1, "reason": "crashed"} and SENT not in ctx.err.getvalue()


# --- guards and lifting flags ------------------------------------------------------------------------------------------------------------

def test_a_tripped_guard_without_its_lift_is_exit_3_before_any_write_and_the_lift_forwarded_passes(tmp_path):
    f = Fakes(pull={"guards": [report.Guard("empty_source", True, 5, 0, "--allow-empty-source")]},
              data={"guards": [report.Guard("mass_change", True, 30, 20)]})
    r = drive(tmp_path, "--apply", "--yes", fakes=f)
    assert r.code == 3 and "pull:empty_source" in r.err and "data:mass_change" in r.err and f.steps(True) == []
    r = drive(tmp_path, "--apply", "--yes", "--allow-empty-source", "--allow-mass-change", "29", fakes=f, run_id="20261113T120000Z-00000013")
    assert r.code == 3 and "pull:empty_source" not in r.err and "data:mass_change" in r.err  # a wrong N lifts nothing
    r = drive(tmp_path, "--apply", "--yes", "--allow-empty-source", "--allow-mass-change", "30", fakes=f, run_id="20261113T120000Z-00000014")
    assert r.code == 0 and f.steps(True) == ["pull", "files", "data", "verify"]


def test_a_tripped_files_guard_cannot_be_lifted(tmp_path):
    f = Fakes(files={"guards": [report.Guard("empty_manifest", True, 0, 1)]})
    r = drive(tmp_path, "--apply", "--yes", fakes=f)
    assert r.code == 3 and "files:empty_manifest" in r.err and f.steps(True) == []


def test_lift_flags_are_part_of_the_apply_command_but_not_of_the_plan_hash(tmp_path):
    plain = report_of(drive(tmp_path, run_id="20261113T120000Z-00000015").folder)
    lifted = report_of(drive(tmp_path, "--allow-growth", "--allow-shrink", "meme_saves", "--allow-model-change", "--ignore-running-batches",
                             run_id="20261113T120000Z-00000016").folder)
    assert plain["plan_hash"] == lifted["plan_hash"]
    cmd = lifted["apply_command"]
    for token in ("--allow-growth", "--allow-shrink meme_saves", "--allow-model-change", "--ignore-running-batches", "--apply --plan-hash"):
        assert token in cmd


def test_flag_rules(tmp_path):
    parser = cli.build_parser()
    for argv, text in ((["--plan-hash", H64], "needs --apply"), (["--yes"], "needs --apply"), (["--apply", "--plan-hash", "zz"], "hex hash"),
                       (["--top", "0"], "--top"), (["--allow-shrink", "no_such_table"], "neither class D nor class P"),
                       (["--allow-mass-change", "-1"], "negative")):
        r = drive(tmp_path, *argv, run_id="20261113T120000Z-00000017")
        assert r.code == 2 and text in r.err, (argv, r.err)
    bad = parser.parse_args(["--env", "general", "--run-id", "x"])
    with pytest.raises(cli.UsageProblem):
        cli.validate_args(bad)
    for flag in ("--retire", "--prod-url", "--ack-revision", "--i-have-pulled", "--deep"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--env", "general", flag])


# --- default runners: lock sharing and flag forwarding ----------------------------------------------------------------------------------

def test_children_run_under_the_parents_lock_and_get_their_own_flags(tmp_path, monkeypatch):
    seen = {}

    def fake(step):
        def run(args, **kw):
            seen[step] = (args, kw)
            return 0

        return run

    monkeypatch.setattr(cli.pull_cli, "run_pull", fake("pull"))
    monkeypatch.setattr(cli.files_cli, "run_promote", fake("files"))
    monkeypatch.setattr(cli.promote_cli, "run_promote", fake("data"))
    monkeypatch.setattr(cli.verify_cli, "run_verify", fake("verify"))
    out, err = io.StringIO(), io.StringIO()
    argv = ["--env", "general", "--apply", "--yes", "--first-seed", "--allow-mass-change", "30", "--allow-shrink", "images",
            "--allow-shrink", "meme_saves", "--allow-model-change", "--ignore-running-batches", "--allow-empty-source", "--allow-growth",
            "--skip-files", "--run-id", "20261113T120000Z-00000018"]
    args = cli.build_parser().parse_args(argv)
    ctx = cli.Ctx(env="general", settings=settings_for(tmp_path), args=args, out=out, err=err, opener="OPENER", capabilities="CAPS",
                  transport="T", transport_factory="TF", git_sha="abc", base_dir="BASE")
    for step, runner in cli.DEFAULT_RUNNERS.items():
        assert runner(ctx, "20261113T120000Z-00000018", True).code == 0
    va, vkw = seen["verify"]  # P6: the library entry, the run's id, no lock of its own, the whole report (not the planning part) in an apply
    assert vkw["lock"] is False and vkw["plan_only"] is False and vkw["session_opener"] == "OPENER" and vkw["transport"] == "T"
    assert va.run_id == "20261113T120000Z-00000018" and va.env == "general" and callable(va.document_sink)
    for step in ("pull", "files", "data"):
        a, kw = seen[step]
        assert kw["lock"] is False and kw["session_opener"] == "OPENER" and callable(kw["ask"]) and a.run_id == "20261113T120000Z-00000018"
        assert a.apply is True and a.yes is True and a.plan_hash is None and callable(a.document_sink)
    pull, files, data = (seen[s][0] for s in ("pull", "files", "data"))
    assert (pull.skip_files, pull.allow_empty_source, pull.allow_growth, pull.allow_shrink) == (True, True, True, ["meme_saves"])
    assert (data.first_seed, data.allow_mass_change, data.allow_shrink, data.allow_model_change, data.ignore_running_batches) == (
        True, 30, ["images"], True, True)
    assert not hasattr(files, "first_seed") and files.command == "promote" and files.deep is False
    assert seen["pull"][1]["transport"] is None  # --skip-files: no bug reports
    assert seen["files"][1]["transport_factory"] == "TF" and seen["files"][1]["base_dir"] == "BASE"
    assert seen["data"][1]["capabilities"] == "CAPS" and seen["data"][1]["git_sha"] == "abc"


def test_dry_children_get_no_apply_flags_and_keep_quiet(tmp_path, monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        kw["out"].write("child summary\n")
        return 0

    monkeypatch.setattr(cli.promote_cli, "run_promote", run)
    out = io.StringIO()
    args = cli.build_parser().parse_args(["--env", "general"])
    ctx = cli.Ctx(env="general", settings=settings_for(tmp_path), args=args, out=out, err=io.StringIO(), opener=None)
    cli.run_data_step(ctx, "20261113T120000Z-00000019", False)
    assert seen["args"].apply is False and seen["args"].yes is False and out.getvalue() == ""
    cli.run_data_step(ctx, "20261113T120000Z-00000019", True)
    assert out.getvalue() == "[data] child summary\n"


def test_the_real_children_and_the_driver_share_one_lock_not_two(tmp_path):
    """A child that took its own lock inside the parent's would fail: the OS lock is not re-entrant. The driver's default runners pass lock=False
    (above), so this is the other half: the lock really is held for the whole run."""
    settings = settings_for(tmp_path)
    holders = []

    def runner(ctx, run_id, apply):
        try:
            with exclusive_lock(settings):
                holders.append("second holder got in")
        except Exception as exc:
            holders.append(type(exc).__name__)
        return StepResult(0, doc_for("pull"))

    out = io.StringIO()
    args = cli.build_parser().parse_args(["--env", "general", "--stop-after", "pull", "--run-id", "20261113T120000Z-0000001a"])
    code = cli.run_driver(args, settings=settings, session_opener=object(), runners={"pull": runner},
                          handshake_fn=lambda ctx, mode: (list(HS), DB), out=out, err=io.StringIO())
    assert code == 0 and holders == ["LockHeldError"]
    with exclusive_lock(settings):  # released afterwards
        pass


# --- redaction ----------------------------------------------------------------------------------------------------------------------------

def test_no_free_text_reaches_the_report_run_json_or_stdout(tmp_path):
    f = Fakes(pull={"pre_detail": SENT}, data={"added": [4]})
    r = drive(tmp_path, "--apply", "--yes", fakes=f)
    assert r.code == 0
    for name in ("report.json", "report.md", "run.json"):
        assert SENT not in (r.folder / name).read_text(), name
    assert SENT not in r.out + r.err
    assert "[redacted]" in (r.folder / "report.json").read_text()
    with pytest.raises(report.ReportError):
        report.Entry("images", "images", added=report.IdSet(ids=[SENT]))


def test_json_dash_prints_the_combined_report_instead_of_the_summary(tmp_path):
    r = drive(tmp_path, "--json", "-")
    doc = json.loads(r.out[r.out.index("{"):])
    assert doc["tool"] == "sync.run" and [s["name"] for s in doc["sections"]] == ["pull", "files", "data", "verify"]
    p = tmp_path / "copy.json"
    drive(tmp_path, "--json", str(p), run_id="20261113T120000Z-0000001b")
    assert json.loads(p.read_text())["tool"] == "sync.run"
