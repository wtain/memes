"""P2-T11: failure injection against the two-schema fixture (the prod schema plays prod). Each test breaks the apply at one seam and asserts
the guarantee the design states: no partial group on prod, nothing outside the plan touched, the same command resumes and finishes, prod
equals dev after the resume, no row value in any output. Seams used: a database trigger that raises (mid-transaction), `apply._before_commit`
(inside the open prod transaction, after the digests matched), `KeyboardInterrupt`, `CommittedLog.append` (after the prod commit), the
server terminating the prod backend (a dropped tunnel as the client sees it), a held state-directory lock and a held prod advisory lock,
a files report that is stale or incomplete, a plan that changed between the dry run and the apply.

Not covered (needs a real network or role): a real ssh tunnel dying (the backend termination is its database-side image), a prod role that
cannot write (the fixture role is a superuser; the statement log test of test_promote_apply shows what the apply may issue).
"""
import json
import threading

import pytest

from sync import connect
from sync.config import exclusive_lock
from sync.promote import apply as apply_mod
from sync.promote import preconditions as pre_mod
from tests.integration.sync.test_promote_apply import (MANY, SENTINEL, digests, dev_only, put_files, rich, six_rich, tokens,  # noqa: F401
                                                       xmin)
from tests.integration.sync.test_promote_plan import (NOW, VEC2, args_for, command, settings_for, uid, world, write_files_report)  # noqa: F401

RUN = "20261115T120000Z-{:08x}"


def rid(n: int) -> str:
    return RUN.format(n)


def committed(folder) -> dict:
    return json.loads((folder / "committed.json").read_text())


def report(folder) -> dict:
    return json.loads((folder / "report.json").read_text())


def same_as_dev(world) -> bool:
    return tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))


def prod_state(side, numbers) -> dict:
    """Row versions of every table of the given images on prod: equal before and after means 'not touched'."""
    out = {}
    for n in numbers:
        for table, col in (("images", "id"), ("tags", "image_id"), ("image_descriptions", "image_id"), ("ocr_texts", "image_id"),
                           ("embeddings", "image_id"), ("meme_profiles", "image_id"), ("description_notes", "image_id")):
            out[(table, n)] = xmin(side, table, col, uid(n))
    return out


def raising_trigger(side, table, image_id, code="P0001"):
    """A prod trigger that raises on an insert of `table` for one image; returns the function that removes it."""
    side.x("create function sync_t_fail() returns trigger language plpgsql as "
           f"$$ begin if new.image_id = '{image_id}' then raise exception 'leak {SENTINEL}' using errcode = '{code}'; end if; return new; end $$")
    side.x(f"create trigger sync_t_fail before insert on {table} for each row execute function sync_t_fail()")

    def remove():
        side.x(f"drop trigger sync_t_fail on {table}")
        side.x("drop function sync_t_fail()")

    return remove


# --- a failure inside a batch transaction ------------------------------------------------------------------------------------------------

def test_a_failure_while_replacing_a_changed_image_leaves_prod_exactly_as_it_was_and_the_resume_finishes(world, command, tmp_path):
    six_rich(world, tmp_path)
    world.dev_side.x("update image_descriptions set text = 'a dog' where image_id = %s", (uid(2),))  # desc group (deleted first)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))  # tags group (the insert that fails)
    world.dev_side.x("insert into concepts (id, name) values (1, 'c')")
    before = prod_state(world.prod_side, range(1, MANY + 1))
    prod_digests = tokens(digests(world.prod))
    remove = raising_trigger(world.prod_side, "tags", uid(2))
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    finally:
        remove()
    assert code == 1 and "db_error:p0001" in err and SENTINEL not in out + err
    assert prod_state(world.prod_side, range(1, MANY + 1)) == before  # the deleted description rows are back: no partial group
    assert tokens(digests(world.prod)) == prod_digests
    assert world.prod_side.scalar("select count(*) from concepts") == 0  # the global sets come after the batches: not reached
    assert not (folder / "committed.json").exists()
    res = report(folder)["result"]["data"]["images"]
    assert res["failed"]["ids"] == [uid(2)] and res["failed"]["failure"] == "db_error:p0001"
    assert SENTINEL not in (folder / "report.json").read_text() + (folder / "report.md").read_text()

    code, out, err, folder = command("--apply", "--yes", run_id=rid(2))  # the same command, a new run id
    assert code == 0, (out, err)
    assert same_as_dev(world) and world.prod_side.scalar("select count(*) from concepts") == 1
    assert world.prod_side.scalar("select text from image_descriptions where image_id = %s", (uid(2),)) == "a dog"
    untouched = [n for n in range(1, MANY + 1) if n != 2]
    assert {k: v for k, v in prod_state(world.prod_side, untouched).items()} == {k: v for k, v in before.items() if k[1] != 2}


def test_a_failure_in_batch_two_leaves_the_images_outside_the_plan_untouched(world, command, tmp_path, monkeypatch):
    six_rich(world, tmp_path)
    world.prod_side.image(900)  # on prod only: left behind, must stay byte for byte
    left_before = prod_state(world.prod_side, [900])
    quiet_before = prod_state(world.prod_side, (1, 2, 7, 8))
    for n in (3, 4, 5, 6):
        world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(n),))

    def boom(n, ids):
        if n == 2:
            raise RuntimeError(SENTINEL)

    monkeypatch.setattr(apply_mod, "_before_commit", boom)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "2", "--allow-mass-change", "4", run_id=rid(3))
    assert code == 1
    assert [b["ids"] for b in committed(folder)["batches"]] == [[uid(3), uid(4)]]
    assert world.prod_side.scalar("select count(*) from tags where value = 'dog'") == 2
    assert prod_state(world.prod_side, [900]) == left_before
    assert prod_state(world.prod_side, (1, 2, 7, 8)) == quiet_before  # unchanged images: no row version moved
    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: None)
    code, out, err, folder2 = command("--apply", "--yes", "--batch-size", "2", "--allow-mass-change", "2", run_id=rid(3))
    assert code == 0, (out, err)
    assert [b["ids"] for b in committed(folder2)["batches"]] == [[uid(3), uid(4)], [uid(5), uid(6)]]  # batch 1 kept, 2 appended
    assert prod_state(world.prod_side, (1, 2, 7, 8)) == quiet_before
    assert prod_state(world.prod_side, [900]) == left_before


# --- the process is interrupted ---------------------------------------------------------------------------------------------------------

def test_ctrl_c_inside_a_batch_rolls_that_batch_back_and_is_exit_one_with_the_committed_batches_listed(world, command, tmp_path, dev_only,
                                                                                                    monkeypatch):
    dev_only(7)

    def interrupt(n, ids):
        if n == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(apply_mod, "_before_commit", interrupt)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(4))
    assert code == 1 and "interrupted" in err and "1 batch(es) committed" in err
    assert world.prod_side.scalar("select count(*) from images") == 3 and world.prod_side.scalar("select count(*) from tags") == 3
    assert [b["n"] for b in committed(folder)["batches"]] == [1]
    assert report(folder)["result"]["data"]["images"]["failed"]["failure"] == "interrupted"
    assert "verify" not in report(folder)["result"]  # an interrupted run does not start the verify phase
    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: None)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(4))
    assert code == 0, (out, err) and same_as_dev(world)
    assert [b["n"] for b in committed(folder)["batches"]] == [1, 2, 3]


def test_a_crash_between_the_prod_commit_and_committed_json_is_repaired_by_running_again(world, command, tmp_path, dev_only, monkeypatch):
    """The documented window: the batch is committed on prod, the process dies before it is written to committed.json. The run is
    reported failed, the next run sees the images as unchanged (digests equal) and prod equals dev. What stays missing is the record:
    committed.json of that run never lists those ids; since M3 they are in `inflight.json` (written before the commit), reported as
    committed_unrecorded and covered by the verify and by --verify-only."""
    dev_only(7)
    real = apply_mod.CommittedLog.append

    def append(self, n, ids, digests_, renamed, at):
        if n == 2:
            raise OSError(f"disk full {SENTINEL}")
        return real(self, n, ids, digests_, renamed, at)

    monkeypatch.setattr(apply_mod.CommittedLog, "append", append)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(5))
    assert code == 1 and "record_failed_after_commit" in err and SENTINEL not in out + err
    entry = report(folder)["result"]["data"]["images"]
    # review finding M3: the batch IS on prod, so it is never "failed / rolled back"; it is named as committed but not recorded
    assert "ids" not in entry.get("failed", {}) or entry["failed"]["ids"] == []
    assert entry["committed_unrecorded"]["ids"] == [uid(4), uid(5), uid(6)]
    assert "COMMITTED BUT NOT RECORDED" in err and "rolled back" not in err
    assert world.prod_side.scalar("select count(*) from images") == 6  # batch 2 IS on prod
    assert [b["n"] for b in committed(folder)["batches"]] == [1]  # ... but not in committed.json
    assert json.loads((folder / "inflight.json").read_text())["ids"] == [uid(4), uid(5), uid(6)]  # the pre-commit intent record names them
    assert "verify" in report(folder)["result"] and report(folder)["result"]["verify"]["images"]["verify"]["checked"] == 6  # and the verify covered them
    code, out2, err2, _ = command("--verify-only", run_id=rid(9))
    assert code == 0 and "6 image(s)" in out2, (out2, err2)  # --verify-only includes the unrecorded batch
    monkeypatch.setattr(apply_mod.CommittedLog, "append", real)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(6))
    assert code == 0, (out, err)
    assert same_as_dev(world)
    assert [i for b in committed(folder)["batches"] for i in b["ids"]] == [uid(7)]  # only the image that was really missing


def test_ctrl_c_between_the_prod_commit_and_the_record_is_committed_unrecorded_and_the_same_run_id_adopts_it(world, command, tmp_path,
                                                                                                              dev_only, monkeypatch):
    dev_only(7)
    real = apply_mod.CommittedLog.append

    def append(self, n, ids, digests_, renamed, at):
        if n == 2:
            raise KeyboardInterrupt
        return real(self, n, ids, digests_, renamed, at)

    monkeypatch.setattr(apply_mod.CommittedLog, "append", append)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(7))
    assert code == 1 and "COMMITTED BUT NOT RECORDED" in err
    entry = report(folder)["result"]["data"]["images"]
    assert entry["committed_unrecorded"]["ids"] == [uid(4), uid(5), uid(6)] and not entry.get("failed", {}).get("ids")
    assert world.prod_side.scalar("select count(*) from images") == 6 and (folder / "inflight.json").is_file()
    monkeypatch.setattr(apply_mod.CommittedLog, "append", real)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(7))  # the same run id resumes
    assert code == 0, (out, err) and same_as_dev(world)
    assert not (folder / "inflight.json").exists()
    assert sorted(i for b in committed(folder)["batches"] for i in b["ids"]) == [uid(n) for n in range(1, 8)]


def test_a_dropped_connection_mid_batch_rolls_back_on_the_server_and_a_fresh_session_resumes(world, tmp_path, dev_only, monkeypatch):
    """The tunnel drop as the database sees it: the prod backend is terminated while the batch transaction is open."""
    import io

    from sync.promote import cli
    dev_only(7)
    pair = world.pair

    def opener_factory():
        class Fresh:
            def __init__(self, env, tool, mode="normal", settings=None):
                self.conns = []

            def __enter__(self):
                d, p = pair.dev_conn(), pair.prod_conn()
                d.autocommit = p.autocommit = True
                self.conns = [d, p]
                self.dev = connect.SyncConnection(d, connect.DEV_RO, None, None, "general")
                self.prod = connect.SyncConnection(p, connect.PROD_WRITER)
                Fresh.last = self
                return {"dev": self.dev, "prod": self.prod, "prod_reader_conn": None}

            def __exit__(self, *exc):
                for c in self.conns:
                    c.close()

        return Fresh

    Fresh = opener_factory()
    monkeypatch.setattr(connect, "handshake", lambda *a, **k: [{"step": "H1", "ok": True, "detail": "ok"}])

    def run(run_id):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_promote(args_for("--apply", "--yes", "--batch-size", "3", "--run-id", run_id), settings=settings_for(tmp_path),
                               session_opener=Fresh, now=NOW, out=out, err=err)
        return code, out.getvalue(), err.getvalue(), tmp_path / "state" / "runs" / run_id

    def cut(n, ids):
        if n == 2:
            pid = Fresh.last.prod.scalar("select pg_backend_pid()")
            admin = pair.prod_conn()
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute("select pg_terminate_backend(%s)", (pid,))
            admin.close()

    monkeypatch.setattr(apply_mod, "_before_commit", cut)
    code, out, err, folder = run(rid(7))
    assert code == 1, (out, err)
    assert SENTINEL not in out + err
    check = pair.prod_conn()
    check.autocommit = True
    with check.cursor() as cur:
        cur.execute("select count(*) from images")
        assert cur.fetchone()[0] == 3  # the open transaction of batch 2 died with its session
        cur.execute("select pg_try_advisory_lock(%s)", (pre_mod.PROMOTE_LOCK_KEY,))
        assert cur.fetchone()[0] is True  # the prod advisory lock went with the dead session
        cur.execute("select pg_advisory_unlock(%s)", (pre_mod.PROMOTE_LOCK_KEY,))
    check.close()
    assert [b["n"] for b in committed(folder)["batches"]] == [1]
    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: None)
    code, out, err, folder = run(rid(8))
    assert code == 0, (out, err)
    assert same_as_dev(world)


# --- after the commit ------------------------------------------------------------------------------------------------------------------

def test_prod_changed_by_someone_else_after_the_commit_is_exit_four_and_the_next_run_repairs_it(world, command, tmp_path, dev_only, monkeypatch):
    dev_only(5)
    original = apply_mod.verify_mod.verify_after_apply
    done = []

    def tamper(dev, prod, doc):
        if not done:
            done.append(1)
            world.prod_side.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, uid(2)))
        return original(dev, prod, doc)

    monkeypatch.setattr(apply_mod.verify_mod, "verify_after_apply", tamper)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(9))
    assert code == 4 and "nothing was rolled back" in err
    assert world.prod_side.scalar("select count(*) from images") == 5
    code, out, err, folder = command("--verify-only", run_id=rid(10))
    assert code == 4  # verify-only against the same committed.json still sees it
    # the prod row differs from dev: the next promote sees image 2 as changed (clip) and overwrites exactly that group
    code, out, err, folder = command("--apply", "--yes", run_id=rid(11))
    assert code == 0, (out, err)
    groups = report(folder)["sections"][0]["entries"][0]["overwritten"]["groups"]
    assert {g["id"]: g["groups"] for g in groups} == {uid(2): ["clip"]}
    assert same_as_dev(world)
    assert command("--verify-only", run_id=rid(12))[0] == 0


def test_a_failure_in_the_global_phase_keeps_the_committed_images_and_the_resume_finishes_the_globals(world, command, tmp_path, dev_only):
    dev_only(4)
    world.dev_side.x("insert into concepts (id, name) values (1, 'c1'), (2, 'c2')")
    world.prod_side.x("create function sync_t_fail() returns trigger language plpgsql as "
                      f"$$ begin raise exception 'leak {SENTINEL}' using errcode = 'P0001'; end $$")
    world.prod_side.x("create trigger sync_t_fail before insert on concepts for each row execute function sync_t_fail()")
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(13))
    finally:
        world.prod_side.x("drop trigger sync_t_fail on concepts")
        world.prod_side.x("drop function sync_t_fail()")
    assert code == 1 and "db_error:p0001" in err and SENTINEL not in out + err
    assert world.prod_side.scalar("select count(*) from images") == 4 and world.prod_side.scalar("select count(*) from concepts") == 0
    assert committed(folder)["globals"]["concepts"] is False
    code, out, err, folder = command("--apply", "--yes", run_id=rid(14))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from concepts") == 2 and committed(folder)["globals"]["concepts"] is True
    assert same_as_dev(world)


# --- files uploaded, rows not committed; the gate ----------------------------------------------------------------------------------------

def test_files_on_prod_without_rows_are_completed_by_the_next_run_with_the_same_files_report(world, command, tmp_path, dev_only):
    dev_only(4)
    remove = raising_trigger(world.prod_side, "embeddings", uid(1))  # batch 1 fails whole: files are 'on prod', no row is
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(15))
    finally:
        remove()
    assert code == 1 and world.prod_side.scalar("select count(*) from images") == 0 and not (folder / "committed.json").exists()
    assert (tmp_path / "state" / "runs" / "20261113T115000Z-0000000a" / "files-promoted.json").exists()  # P3's report is still valid
    code, out, err, folder = command("--apply", "--yes", run_id=rid(16))
    assert code == 0, (out, err) and same_as_dev(world)


def test_a_files_report_that_does_not_cover_the_promotable_set_is_refused_before_any_write(world, command, tmp_path, dev_only):
    dev_only(5)
    put_files(tmp_path, [(uid(n), f"img{n:04d}.jpg", f"hash{n:04d}") for n in (1, 2, 4, 5)])  # the manifest still names 5 on dev: hash differs
    code, out, err, folder = command("--apply", "--yes", run_id=rid(17))
    assert code == 2 and "files_report" in err  # a report that does not cover the promotable set (manifest hash) is a precondition
    assert world.prod_side.scalar("select count(*) from images") == 0
    assert not (folder / "committed.json").exists()


def test_a_stale_files_report_is_refused_before_any_write(world, command, tmp_path, dev_only):
    dev_only(3)
    for f in (tmp_path / "state" / "runs").glob("*/files-promoted.json"):
        f.unlink()
    write_files_report(tmp_path, 3, finished="2026-11-13T09:00:00Z")  # three hours before NOW
    code, out, err, folder = command("--apply", "--yes", run_id=rid(18))
    assert code == 2 and "files_report" in err
    assert world.prod_side.scalar("select count(*) from images") == 0
    code, out, err, folder = command("--apply", "--yes", "--max-report-age", "4h", run_id=rid(19))
    assert code == 0, (out, err)


def test_a_dev_change_after_the_files_report_invalidates_it(world, command, tmp_path, dev_only):
    dev_only(3)
    world.dev_side.x("update images set content_hash = 'rewritten' where id = %s", (uid(2),))  # e.g. fix_image_formats ran after P3
    code, out, err, folder = command("--apply", "--yes", run_id=rid(20))
    assert code == 2 and "files_report" in err and world.prod_side.scalar("select count(*) from images") == 0


# --- contention and a changed plan -----------------------------------------------------------------------------------------------------

def test_the_state_directory_lock_held_by_another_command_is_exit_two_and_writes_nothing(world, command, tmp_path, dev_only):
    dev_only(3)
    with exclusive_lock(settings_for(tmp_path)):
        code, out, err, folder = command("--apply", "--yes", run_id=rid(21))
    assert code == 2 and "another sync command holds the lock" in err
    assert world.prod_side.scalar("select count(*) from images") == 0 and not (folder / "committed.json").exists()
    assert command("--apply", "--yes", run_id=rid(22))[0] == 0  # released


def test_the_prod_advisory_lock_is_released_after_a_failed_apply(world, command, tmp_path, dev_only, monkeypatch, sync_pair):
    dev_only(4)
    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: (_ for _ in ()).throw(RuntimeError("x")))
    assert command("--apply", "--yes", run_id=rid(23))[0] == 1
    other = sync_pair.prod_conn()
    other.autocommit = True
    try:
        res = pre_mod.try_prod_lock(connect.SyncConnection(other, connect.PROD_WRITER))
        assert res["ok"], res  # nobody holds it any more
    finally:
        other.close()


def test_a_change_between_the_dry_run_and_the_apply_is_exit_three_before_any_write(world, command, tmp_path):
    six_rich(world, tmp_path)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    code, out, err, folder = command(run_id=rid(24))
    plan_hash = report(folder)["plan_hash"]
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(3),))  # the world moved after the owner read the report
    before = prod_state(world.prod_side, range(1, MANY + 1))
    code, out, err, folder = command("--apply", "--yes", "--plan-hash", plan_hash, run_id=rid(25))
    assert code == 3 and "the plan changed since the dry run" in err
    assert prod_state(world.prod_side, range(1, MANY + 1)) == before and not (folder / "committed.json").exists()
    code, out, err, folder = command("--apply", "--yes", "--plan-hash", plan_hash, run_id=rid(25))  # still refused, deterministic
    assert code == 3
    assert report(command(run_id=rid(26))[3])["plan_hash"] != plan_hash


def test_a_second_apply_racing_the_first_cannot_start_while_the_prod_lock_is_held(world, command, tmp_path, dev_only, sync_pair):
    """Two promotes of one environment: the first holds the prod advisory lock from another process (another workstation's session)."""
    dev_only(3)
    other = sync_pair.prod_conn()
    other.autocommit = True
    gate = threading.Event()
    try:
        assert pre_mod.try_prod_lock(connect.SyncConnection(other, connect.PROD_WRITER))["ok"]
        gate.set()
        code, out, err, folder = command("--apply", "--yes", run_id=rid(26))
    finally:
        other.close()
    assert gate.is_set() and code == 2 and "prod_lock" in err
    assert world.prod_side.scalar("select count(*) from images") == 0


# --- the record on disk -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("content", ["{ not json", json.dumps({"run_id": "someone-else", "env": "general", "batches": []}),
                                     json.dumps({"run_id": rid(27), "env": "metal", "batches": []})])
def test_an_unreadable_or_foreign_committed_json_stops_the_run_before_the_first_prod_write(world, command, tmp_path, dev_only, content):
    dev_only(3)
    folder = tmp_path / "state" / "runs" / rid(27)
    folder.mkdir(parents=True)
    (folder / "committed.json").write_text(content)
    code, out, err, _ = command("--apply", "--yes", run_id=rid(27))
    assert code == 1 and ("committed_json_unreadable" in err or "committed_json_foreign" in err)
    assert world.prod_side.scalar("select count(*) from images") == 0
    assert (folder / "committed.json").read_text() == content  # not overwritten
