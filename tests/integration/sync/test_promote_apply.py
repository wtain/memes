"""P2-T6 against the two-schema fixture: the apply phase of `sync.promote` for image batches (the prod schema plays prod).

Only `ocrdb_test` (via `sync_pair`). Not covered here, because the fixture has no roles, tunnel or capabilities endpoint: the identity
and role guard of the handshake (H1 to H10 is replaced by a canned success; its logic is unit tested in tests/sync/test_connect.py), a
real network and real server timing. The prod-side role of the fixture is a superuser, so "no write outside the registry" is proved by
recording every statement the apply issues (`record`), not by privileges.
"""
import datetime as dt
import json
import re
import uuid

import pytest

from sync import connect, registry
from sync.model import Klass
from sync.promote import apply as apply_mod
from sync.promote import digest as dg
from sync.promote import files_gate, preconditions as pre_mod
from tests.integration.sync.test_promote_plan import (NOW, VEC, VEC2, Side, args_for, command, entry, plan_of, settings_for, uid, world,
                                                     write_files_report)

VEC1024 = "[" + ",".join(["0.5"] * 1024) + "]"
VEC1024_B = "[" + ",".join(["0.125"] * 1024) + "]"
SENTINEL = "SENTINEL-TEXT-9f3a61"
STATE = "state"
_ = (command, world)  # fixtures


def rich(side: Side, n: int, *, text="hello world", batch_run=None, **kw) -> str:
    """An image with a row in every image-scoped class D table."""
    i = side.image(n, ocr=text, note=f"note {n}", remarks=f"remark {n}", **kw)
    side.x("update ocr_texts set bbox = %s::json where image_id = %s", (json.dumps([[0, 0, 1, 1]]), i))
    side.x("insert into ocr_lemmas (image_id, lemma) values (%s, 'cat'), (%s, 'sofa')", (i, i))
    d = uid(40_000 + n)
    side.x("insert into image_description_embeddings (image_description_id, embedding) values (%s, %s::vector)", (d, VEC1024))
    side.x("insert into description_lemmas (image_description_id, lemma) values (%s, 'cat')", (d,))
    side.x("insert into image_description_feedback (image_description_id, approved, created_at) values (%s, true, '2026-01-01')", (d,))
    side.x("insert into meme_profiles (image_id, taxonomy_version, extractor_model, extractor_prompt_version, inputs_fingerprint, "
           "situation_text, situation_confidence) values (%s, 3, 'x', 'v', 'f', 'a cat waits', 0.75)", (i,))
    side.x("insert into meme_profile_values (image_id, axis, value_key, confidence, source) values (%s, 'emotion', 'joy', 0.5, 'model')", (i,))
    side.x("insert into meme_profile_situation_embeddings (image_id, embedding, model_name, model_digest) values (%s, %s::vector, 'm1', 'd')",
           (i, VEC1024))
    side.x("insert into description_note_lemmas (image_id, lemma) values (%s, 'note')", (i,))
    side.x("insert into description_note_embeddings (description_note_id, embedding) values (%s, %s::vector)", (i, VEC1024))
    if batch_run:
        side.x("update images set ingestion_batch_id = %s where id = %s", (batch_run, i))
    return i


def rich_both(world, n, **kw):
    rich(world.dev_side, n, **kw)
    rich(world.prod_side, n, **kw)


def image_tables():
    return dg.image_scoped_tables()


def digests(conn, restrict="all"):
    return dg.fetch_image_digests(conn, image_tables(), restrict)


def tokens(d):
    return {i: {t: v.token for t, v in per.items()} for i, per in d.items()}


def xmin(side: Side, table: str, image_col: str, i: str) -> list:
    with side.raw.cursor() as cur:
        cur.execute(f'select xmin::text, ctid::text from "{table}" where "{image_col}" = %s order by 1, 2', (i,))
        return cur.fetchall()


def record(conn) -> list:
    log: list = []
    for name in ("query", "copy_in", "copy_out", "execute_values"):
        orig = getattr(conn, name)

        def wrap(sql, *a, _o=orig, **k):
            log.append(sql)
            return _o(sql, *a, **k)

        setattr(conn, name, wrap)
    return log


def run_dir(tmp_path, run_id):
    return tmp_path / STATE / "runs" / run_id


def result_of(tmp_path, run_id):
    doc = json.loads((run_dir(tmp_path, run_id) / "report.json").read_text())
    return doc, doc.get("result", {}).get("data", {}).get("images")


RUN = "20261113T120000Z-{:08x}"


def rid(n: int) -> str:
    return RUN.format(n)


def put_files(tmp_path, rows, name="20261113T115000Z-0000000a", finished="2026-11-13T11:50:00Z"):
    """The one valid files report of the run folder: `rows` are (id, filename, content_hash) of the promotable set."""
    from sync.files.manifest import manifest_sha256

    for f in (tmp_path / STATE / "runs").glob("*/files-promoted.json"):
        f.unlink()
    folder = tmp_path / STATE / "runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files-promoted.json").write_text(json.dumps({
        "run_id": name, "env": "general", "finished_at": finished, "complete": True,
        "images": [{"id": i, "filename": f, "size": 1, "content_hash": h} for i, f, h in rows], "left_out": [],
        "manifest_sha256": manifest_sha256(rows)}))


def default_rows(numbers, renamed=None):
    renamed = renamed or {}
    return [(uid(n), renamed.get(n, f"img{n:04d}.jpg"), f"hash{n:04d}") for n in numbers]


@pytest.fixture()
def dev_only(world, tmp_path):
    """Seven rich images on dev only, an empty prod, a valid files report."""
    def make(count=7):
        for n in range(1, count + 1):
            rich(world.dev_side, n)
        write_files_report(tmp_path, count)
    return make


# --- first apply, idempotence, committed.json, report ---------------------------------------------------------------------------------

def test_first_apply_copies_every_table_and_prod_digests_equal_dev(world, command, tmp_path, dev_only):
    dev_only(7)
    world.dev_side.x("insert into batch_runs (run_id, kind, trigger, status) values (%s, 'k', 'manual', 'completed')", (str(uuid.uuid4()),))
    run = world.dev_side.scalar("select run_id::text from batch_runs")
    world.dev_side.x("update images set ingestion_batch_id = %s where id = %s", (run, uid(1)))
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(1))
    assert code == 0, (out, err)
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))
    for t in image_tables():
        assert world.prod_side.scalar(f'select count(*) from "{t}"') == world.dev_side.scalar(f'select count(*) from "{t}"'), t
        assert world.prod_side.scalar(f'select count(*) from "{t}"') > 0, t  # every table is populated
    assert world.prod_side.scalar("select count(*) from images where ingestion_batch_id is not null") == 0  # nullified
    assert world.prod_side.scalar("select bbox::text from ocr_texts where image_id = %s", (uid(1),)) == "[[0, 0, 1, 1]]"

    committed = json.loads((folder / "committed.json").read_text())
    assert committed["run_id"] == rid(1) and committed["env"] == "general"
    assert committed["globals"] == {"concepts": False, "absorptions": False}
    assert [b["n"] for b in committed["batches"]] == [1, 2, 3]
    assert [len(b["ids"]) for b in committed["batches"]] == [3, 3, 1]
    assert [i for b in committed["batches"] for i in b["ids"]] == [uid(n) for n in range(1, 8)]  # created_at, id order
    dev_groups = dg.image_group_hashes(digests(world.dev, "promotable")[uid(4)], dg.groups_for(image_tables()))
    assert committed["batches"][1]["digests"][uid(4)] == dev_groups
    assert set(dev_groups) == {"core", "ocr", "clip", "desc", "tags", "profile", "note"}
    assert all(b["renamed"] == [] and b["committed_at"].endswith("Z") for b in committed["batches"])

    doc, res = result_of(tmp_path, rid(1))
    assert doc["dry"] is False and (res["added"], res["overwritten"]) == (7, 0) and res["skipped_with_reason"] == {} and "failed" not in res
    assert not (folder / "dry").exists()
    assert "batch 3: 1 of 1 images committed" in out and "applied: 7 added, 0 overwritten in 3 batch(es)" in out


def test_second_run_plans_nothing_and_writes_no_committed_json(world, command, tmp_path, dev_only):
    dev_only(5)
    assert command("--apply", "--yes", run_id=rid(2))[0] == 0
    code, out, err, folder = command("--apply", "--yes", run_id=rid(3))
    assert code == 0, (out, err)
    assert not (folder / "committed.json").exists()
    doc, res = result_of(tmp_path, rid(3))
    assert (res["added"], res["overwritten"]) == (0, 0)
    assert doc["sections"][0]["entries"][0]["unchanged"]["count"] == 5
    code, out, err, folder = command(run_id=rid(4))  # a dry run agrees
    entry_ = json.loads((folder / "report.json").read_text())["sections"][0]["entries"][0]
    assert code == 0 and entry_["added"]["count"] == 0 and entry_["overwritten"]["count"] == 0


def test_apply_report_and_console_hold_no_row_values(world, command, tmp_path, dev_only):
    dev_only(4)
    for table, col in (("ocr_texts", "text"), ("tags", "value"), ("image_descriptions", "text"), ("description_notes", "text"),
                       ("image_extras", "remarks"), ("meme_profiles", "situation_text")):
        world.dev_side.x(f'update "{table}" set "{col}" = %s', (SENTINEL,))
    code, out, err, folder = command("--apply", "--yes", run_id=rid(5))
    assert code == 0, (out, err)
    for name in ("report.json", "report.md", "committed.json"):
        assert SENTINEL not in (folder / name).read_text(), name
    assert SENTINEL not in out + err
    assert world.prod_side.scalar("select count(*) from tags where value = %s", (SENTINEL,)) == 4  # but the data did arrive


# --- changed images: only the changed groups are replaced ----------------------------------------------------------------------------------

MANY = 12  # enough images that a handful of changes stays under the 25 % mass-change guard


def six_rich(world, tmp_path):
    for n in range(1, MANY + 1):
        rich_both(world, n)
    write_files_report(tmp_path, MANY)


def test_only_the_changed_groups_are_replaced_and_everything_else_is_untouched(world, command, tmp_path):
    six_rich(world, tmp_path)
    p = world.prod_side
    before = {k: xmin(p, t, c, uid(n)) for k, (t, c, n) in {
        "desc2": ("image_descriptions", "image_id", 2), "tags2": ("tags", "image_id", 2), "ocr2": ("ocr_texts", "image_id", 2),
        "emb2": ("embeddings", "image_id", 2), "tags3": ("tags", "image_id", 3), "desc3": ("image_descriptions", "image_id", 3),
        "tags4": ("tags", "image_id", 4), "img2": ("images", "id", 2), "img3": ("images", "id", 3)}.items()}
    world.dev_side.x("update image_descriptions set text = 'a dog' where image_id = %s", (uid(2),))
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(3),))
    code, out, err, folder = command("--apply", "--yes", run_id=rid(6))
    assert code == 0, (out, err)
    after = lambda t, c, n: xmin(p, t, c, uid(n))
    assert after("image_descriptions", "image_id", 2) != before["desc2"]  # replaced
    assert p.scalar("select text from image_descriptions where image_id = %s", (uid(2),)) == "a dog"
    assert after("tags", "image_id", 3) != before["tags3"]
    assert p.scalar("select value from tags where image_id = %s", (uid(3),)) == "dog"
    for key, (t, c, n) in {"tags2": ("tags", "image_id", 2), "ocr2": ("ocr_texts", "image_id", 2), "emb2": ("embeddings", "image_id", 2),
                           "desc3": ("image_descriptions", "image_id", 3), "tags4": ("tags", "image_id", 4),
                           "img2": ("images", "id", 2), "img3": ("images", "id", 3)}.items():
        assert after(t, c, n) == before[key], key  # same row versions: not deleted, not rewritten
    # the whole desc group of image 2 was replaced (children included), nothing else of it
    assert p.scalar("select count(*) from image_description_embeddings") == MANY
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))
    doc, res = result_of(tmp_path, rid(6))
    assert (res["added"], res["overwritten"]) == (0, 2)
    groups = doc["sections"][0]["entries"][0]["overwritten"]["groups"]
    assert {g["id"]: g["groups"] for g in groups} == {uid(2): ["desc"], uid(3): ["tags"]}


def test_rows_removed_on_dev_are_removed_on_prod_for_that_image(world, command, tmp_path):
    six_rich(world, tmp_path)
    d = world.dev_side
    d.x("delete from tags where image_id = %s", (uid(2),))
    d.x("delete from description_notes where image_id = %s", (uid(3),))  # cascades to the note embedding
    d.x("delete from image_extras where image_id = %s", (uid(4),))
    d.x("delete from image_descriptions where image_id = %s", (uid(5),))  # cascades to the description children
    code, out, err, folder = command("--apply", "--yes", "--allow-mass-change", "4", "--allow-shrink", "tags",
                                     "--allow-shrink", "image_descriptions", "--allow-shrink", "description_notes", run_id=rid(7))
    assert code == 0, (out, err)
    p = world.prod_side
    assert p.scalar("select count(*) from tags where image_id = %s", (uid(2),)) == 0
    assert p.scalar("select count(*) from description_notes where image_id = %s", (uid(3),)) == 0
    assert p.scalar("select count(*) from description_note_embeddings where description_note_id = %s", (uid(3),)) == 0
    assert p.scalar("select count(*) from image_extras where image_id = %s", (uid(4),)) == 0
    assert p.scalar("select count(*) from image_descriptions where image_id = %s", (uid(5),)) == 0
    assert p.scalar("select count(*) from description_lemmas where image_description_id = %s", (uid(40_005),)) == 0
    assert p.scalar("select count(*) from tags") == MANY - 1 and p.scalar("select count(*) from images") == MANY  # no image was removed
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))


def test_a_renamed_image_is_listed_in_committed_json_and_its_row_is_updated(world, command, tmp_path):
    six_rich(world, tmp_path)
    world.dev_side.x("update images set filename = 'renamed.jpg' where id = %s", (uid(2),))
    put_files(tmp_path, default_rows(range(1, MANY + 1), {2: "renamed.jpg"}))
    code, out, err, run = command("--apply", "--yes", run_id=rid(8))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select filename from images where id = %s", (uid(2),)) == "renamed.jpg"
    committed = json.loads((run / "committed.json").read_text())
    assert committed["batches"][0]["renamed"] == [{"id": uid(2), "old_filename": "img0002.jpg", "new_filename": "renamed.jpg"}]
    assert SENTINEL not in (run / "report.json").read_text() and "renamed.jpg" not in (run / "report.json").read_text()


def test_an_image_flagged_on_dev_after_its_promotion_is_left_behind_untouched(world, command, tmp_path):
    six_rich(world, tmp_path)
    before = xmin(world.prod_side, "images", "id", uid(2))
    world.dev_side.x("update image_extras set flagged = true where image_id = %s", (uid(2),))
    world.dev_side.x("update images set status = 'rejected' where id = %s", (uid(3),))
    put_files(tmp_path, default_rows([n for n in range(1, MANY + 1) if n not in (2, 3)]))
    code, out, err, folder = command("--apply", "--yes", run_id=rid(9))
    assert code == 0, (out, err)
    assert xmin(world.prod_side, "images", "id", uid(2)) == before  # not touched
    assert world.prod_side.scalar("select count(*) from images") == MANY
    assert world.prod_side.scalar("select status from images where id = %s", (uid(3),)) == "active"
    entry_ = json.loads((folder / "report.json").read_text())["sections"][0]["entries"][0]
    assert entry_["left_behind"]["ids"] == [uid(2), uid(3)] and entry_["added"]["count"] == 0
    assert not (folder / "committed.json").exists()


# --- failure and resume ---------------------------------------------------------------------------------------------------------------------

def test_a_failure_in_batch_two_rolls_that_batch_back_and_the_resume_completes(world, command, tmp_path, dev_only, monkeypatch):
    dev_only(7)

    def boom(n, ids):
        if n == 2:
            raise RuntimeError(f"kill the batch mid-way {SENTINEL}")

    monkeypatch.setattr(apply_mod, "_before_commit", boom)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(11))
    assert code == 1, (out, err)
    assert "unexpected:runtimeerror" in err and SENTINEL not in out + err
    p = world.prod_side
    assert p.scalar("select count(*) from images") == 3 and p.scalar("select count(*) from tags") == 3  # batch 1 final, batch 2 gone
    assert p.scalar("select count(*) from embeddings") == 3 and p.scalar("select count(*) from description_notes") == 3
    committed = json.loads((folder / "committed.json").read_text())
    assert [b["n"] for b in committed["batches"]] == [1]
    doc, res = result_of(tmp_path, rid(11))
    assert res["added"] == 3 and res["failed"]["ids"] == [uid(4), uid(5), uid(6)] and res["failed"]["failure"].startswith("unexpected")
    assert "committed.json" in err and "1 batch(es) committed" in err
    assert SENTINEL not in (folder / "report.json").read_text() + (folder / "report.md").read_text()

    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: None)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(11))  # the same run id: resume
    assert code == 0, (out, err)
    committed = json.loads((folder / "committed.json").read_text())
    assert [b["n"] for b in committed["batches"]] == [1, 2, 3]  # batch 1 kept, the others appended
    assert [i for b in committed["batches"] for i in b["ids"]] == [uid(n) for n in range(1, 8)]
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))
    assert not (folder / "dry").exists()


def test_a_database_error_is_reduced_to_its_sqlstate(world, command, tmp_path, dev_only):
    dev_only(4)
    world.prod_side.x("create function sync_t_boom() returns trigger language plpgsql as "
                      f"$$ begin raise exception 'leak {SENTINEL}' using errcode = 'P0001'; end $$")
    world.prod_side.x("create trigger sync_t_boom before insert on tags for each row execute function sync_t_boom()")
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(12))
    finally:
        world.prod_side.x("drop trigger sync_t_boom on tags")
        world.prod_side.x("drop function sync_t_boom()")
    assert code == 1
    assert "db_error:p0001" in err and SENTINEL not in out + err
    assert world.prod_side.scalar("select count(*) from images") == 0
    assert not (folder / "committed.json").exists()
    assert SENTINEL not in (folder / "report.json").read_text()


def test_a_digest_mismatch_rolls_the_batch_back_and_stops(world, command, tmp_path, dev_only):
    dev_only(6)
    world.prod_side.x("create function sync_t_alter() returns trigger language plpgsql as "
                      "$$ begin if new.image_id = '%s' then new.value = new.value || 'x'; end if; return new; end $$" % uid(5))
    world.prod_side.x("create trigger sync_t_alter before insert on tags for each row execute function sync_t_alter()")
    try:
        code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(13))
    finally:
        world.prod_side.x("drop trigger sync_t_alter on tags")
        world.prod_side.x("drop function sync_t_alter()")
    assert code == 1 and "digest_mismatch" in err
    p = world.prod_side
    assert p.scalar("select count(*) from images") == 3 and p.scalar("select count(*) from tags") == 3  # batch 2 rolled back whole
    doc, res = result_of(tmp_path, rid(13))
    assert res["added"] == 3 and res["skipped_with_reason"]["digest_mismatch"]["ids"] == [uid(5)]
    assert res["failed"]["ids"] == [uid(4), uid(6)] and res["failed"]["failure"] == "digest_mismatch"
    assert [b["n"] for b in json.loads((folder / "committed.json").read_text())["batches"]] == [1]


def test_a_prod_advisory_lock_held_by_another_session_is_exit_two(world, command, tmp_path, dev_only, sync_pair):
    dev_only(3)
    other_raw = sync_pair.prod_conn()
    other_raw.autocommit = True
    try:
        assert pre_mod.try_prod_lock(connect.SyncConnection(other_raw, connect.PROD_WRITER))["ok"]
        code, out, err, folder = command("--apply", "--yes", run_id=rid(14))
    finally:
        other_raw.close()
    assert code == 2 and "prod_lock" in err
    assert world.prod_side.scalar("select count(*) from images") == 0
    assert not (folder / "committed.json").exists()


# --- apply-time checks of one batch ------------------------------------------------------------------------------------------------

def test_dev_changes_after_the_plan_are_deferred_not_copied(world, tmp_path, monkeypatch):
    from sync.promote import plan as plan_mod

    for n in range(1, 4):
        rich(world.dev_side, n)
    p, inp, _ = plan_of(world, tmp_path)
    inp.files = files_gate.FilesReport("r", "general", "2026-11-13T11:30:00Z", True, "x" * 64,
                                       images={uid(n): (f"img{n:04d}.jpg", f"hash{n:04d}") for n in (1, 2)}, left_out={})
    world.dev_side.x("update tags set value = 'changed after the plan' where image_id = %s", (uid(1),))  # 1: deferred
    # 3 is not in the files report: the gate holds it back. 2 goes through.
    outcome = apply_mod.run_apply(settings=settings_for(tmp_path), inp=inp, plan=p, dev=world.dev, prod=world.prod,
                                  folder=tmp_path / "run", run_id="20261113T120000Z-0000aaaa")
    assert outcome.ok and outcome.added == [uid(2)]
    assert outcome.skipped == {"deferred_changed_since_plan": [uid(1)], "files_gate_miss": [uid(3)]}
    assert world.prod_side.scalar("select count(*) from images") == 1


def test_a_dev_image_that_stopped_being_promotable_is_deferred(world, tmp_path):
    for n in range(1, 4):
        rich(world.dev_side, n)
    p, inp, _ = plan_of(world, tmp_path)
    inp.files = files_gate.FilesReport("r", "general", "2026-11-13T11:30:00Z", True, "x" * 64,
                                       images={uid(n): (f"img{n:04d}.jpg", f"hash{n:04d}") for n in (1, 2, 3)}, left_out={})
    world.dev_side.x("update images set status = 'rejected' where id = %s", (uid(2),))
    outcome = apply_mod.run_apply(settings=settings_for(tmp_path), inp=inp, plan=p, dev=world.dev, prod=world.prod,
                                  folder=tmp_path / "run", run_id="20261113T120000Z-0000aaab")
    assert outcome.added == [uid(1), uid(3)] and outcome.skipped == {"deferred_changed_since_plan": [uid(2)]}
    assert world.prod_side.scalar("select count(*) from images where id = %s", (uid(2),)) == 0


# --- flags -------------------------------------------------------------------------------------------------------------------------------------

def test_limit_images_and_batch_size(world, command, tmp_path, dev_only):
    dev_only(5)
    world.dev_side.x("insert into concepts (id, name) values (1, 'c')")
    code, out, err, folder = command("--apply", "--yes", "--limit-images", "3", "--batch-size", "2", run_id=rid(15))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from images") == 3
    assert [len(b["ids"]) for b in json.loads((folder / "committed.json").read_text())["batches"]] == [2, 1]
    assert world.prod_side.scalar("select count(*) from concepts") == 0  # the global sets are skipped with a cap
    doc, res = result_of(tmp_path, rid(15))
    assert res["added"] == 3
    assert doc["sections"][0]["entries"][0]["skipped_with_reason"]["capped_by_limit"]["ids"] == [uid(4), uid(5)]
    code, out, err, folder = command("--apply", "--yes", run_id=rid(16))  # the rest next time
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from images") == 5
    assert world.prod_side.scalar("select count(*) from concepts") == 1  # the uncapped run applies the global sets (T7)


def test_only_restricts_the_tables_that_are_written(world, command, tmp_path, dev_only):
    for n in range(1, MANY + 1):
        rich_both(world, n)
    write_files_report(tmp_path, MANY)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    world.dev_side.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, uid(3)))
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", "--only", "tags", run_id=rid(17))
    assert code == 0, (out, err)
    p = world.prod_side
    assert p.scalar("select value from tags where image_id = %s", (uid(2),)) == "dog"
    assert p.scalar("select embedding::text from embeddings where image_id = %s", (uid(3),)) == VEC  # out of scope: untouched
    touched = {m for sql in log for m in re.findall(r'(?:INTO|FROM|ANALYZE)\s+"?([a-z_]+)"?', sql) if m in registry.REGISTRY}
    assert touched <= {"images", "tags"}, touched
    assert json.loads((folder / "committed.json").read_text())["tables"] == ["images", "tags"]


def test_only_without_the_required_tables_refuses_while_a_new_image_is_planned_review_finding_m2(world, command, tmp_path, dev_only):
    for n in range(1, MANY + 1):
        rich_both(world, n)
    new = MANY + 1
    rich(world.dev_side, new)  # new on dev: --only tags would create its prod image without ocr_texts and embeddings
    write_files_report(tmp_path, new)
    before = world.prod_side.scalar("select count(*) from images")
    code, out, err, folder = command("--apply", "--yes", "--only", "tags", run_id=rid(18))
    assert code == 3 and "only_excludes_required_tables" in err, (out, err)
    assert world.prod_side.scalar("select count(*) from images") == before  # nothing was written
    code, out, err, folder = command("--apply", "--yes", "--only", "tags,embeddings,ocr_texts", run_id=rid(19))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from embeddings where image_id = %s", (uid(new),)) == 1
    assert world.prod_side.scalar("select count(*) from ocr_texts where image_id = %s", (uid(new),)) == 1


# --- what the apply may and may not touch -------------------------------------------------------------------------------------------------

def test_the_apply_issues_no_statement_on_class_p_or_x_and_never_deletes_an_image(world, command, tmp_path):
    six_rich(world, tmp_path)
    new = MANY + 1
    rich(world.dev_side, new)
    world.dev_side.x("update image_descriptions set text = 'a dog' where image_id = %s", (uid(2),))
    world.dev_side.x("update images set popularity = 9, filename = 'n.jpg' where id = %s", (uid(3),))
    world.dev_side.x("delete from tags where image_id = %s", (uid(4),))
    put_files(tmp_path, default_rows(range(1, new + 1), {3: "n.jpg"}))
    prod_log, dev_log = record(world.prod), record(world.dev)
    code, out, err, folder = command("--apply", "--yes", "--allow-shrink", "tags", run_id=rid(18))
    assert code == 0, (out, err)
    forbidden = [n for k in (Klass.P, Klass.X) for n in registry.names(k)] + ["alembic_version"]
    # prod: nothing at all names a class P or X table; dev: the precondition reads may (batch_runs), the copies and snapshots may not
    for sql in prod_log + [s for s in dev_log if s.startswith("COPY")]:
        for name in forbidden:
            assert not re.search(rf'\b{name}\b', sql), (name, sql[:80])
    d_tables = set(registry.names(Klass.D))
    allowed_verbs = ("SELECT", "BEGIN", "COMMIT", "ROLLBACK", "SET", "INSERT INTO", "DELETE FROM", "COPY", "ANALYZE")
    for sql in prod_log:
        assert sql.lstrip().upper().startswith(allowed_verbs), sql[:60]
        assert not re.search(r"\b(TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|VACUUM)\b", sql, re.I), sql[:60]
        if sql.lstrip().upper().startswith("DELETE"):
            target = re.match(r'\s*DELETE FROM\s+"?([a-z_]+)"?', sql, re.I).group(1)
            assert target in d_tables and target != "images", sql[:60]
        if sql.lstrip().upper().startswith("INSERT"):
            assert re.match(r'\s*INSERT INTO "?images"?', sql) and "ON CONFLICT" in sql
    assert not any(re.search(r"DELETE\s+FROM\s+\"?images\"?\b", s, re.I) for s in prod_log)
    assert all(s.lstrip().upper().startswith(("SELECT", "BEGIN", "COMMIT", "SET", "COPY (SELECT")) for s in dev_log)
    assert any(s.startswith("DELETE FROM") for s in prod_log) and any(s.startswith("COPY") for s in prod_log)
    assert world.prod_side.scalar("select count(*) from images") == new


# --- setval ---------------------------------------------------------------------------------------------------------------------------------

def test_setval_moves_the_sequence_past_max_id_and_never_lowers_it(world):
    p = world.prod_side
    p.x("insert into concepts (id, name) values (1, 'a'), (2, 'b'), (5, 'c')")
    p.x("insert into concept_image_sets (id, concept_id) values (7, 1)")
    done = apply_mod.setval_tables(world.prod)
    assert done == {"concept_image_sets": 7, "concepts": 5}
    p.x("insert into concepts (name) values ('next')")
    assert p.scalar("select max(id) from concepts") == 6  # the default insert no longer collides
    p.x("select setval(pg_get_serial_sequence('concepts', 'id'), 100)")
    assert apply_mod.setval_tables(world.prod, ["concepts"]) == {}  # 6 is below the sequence: left alone
    assert p.scalar("select last_value from concepts_id_seq") == 100
    assert apply_mod.setval_tables(world.prod, ["meme_impressions", "tags", "concept_texts"]) == {}  # class P, no sequence, empty


def test_the_end_phase_runs_setval_for_the_selected_global_tables(world, command, tmp_path, dev_only):
    dev_only(2)
    world.prod_side.x("insert into concepts (id, name) values (3, 'x')")
    code, out, err, folder = command("--apply", "--yes", run_id=rid(19))
    assert code == 0, (out, err)
    assert "sequences moved: concepts" in out
