"""P2-T8 against the two-schema fixture: the verify phase at the end of an apply and `--verify-only`.

Only `ocrdb_test` (via `sync_pair`). Not covered: a real read-only role (the fixture's role is a superuser: read-only is proved by recording
every statement and by the explicit `READ ONLY` transactions) and the real handshake (replaced by a canned success, its steps are
checked in tests/sync/test_promote_verify.py).
"""
import json
import re

import pytest

from sync import connect
from sync.promote import apply as apply_mod
from sync.promote import cli, files_gate
from sync.promote import digest as dg
from tests.integration.sync.test_promote_apply import (SENTINEL, VEC2, command, dev_only, record, rich, rid,  # noqa: F401
                                                       six_rich, world)
from tests.integration.sync.test_promote_globals import absorb, close, committed_of, hashes, seed_concepts  # noqa: F401
from tests.integration.sync.test_promote_plan import both, uid, write_files_report  # noqa: F401

_ = (command, world, dev_only)


def verify_of(tmp_path, run_id, name="images"):
    doc = json.loads((tmp_path / "state" / "runs" / run_id / "report.json").read_text())
    return doc, doc["result"]["verify"][name]["verify"]


@pytest.fixture()
def applied(world, command, tmp_path, dev_only):
    """Seven rich images, concepts and absorptions on dev, applied to an empty prod: the run `rid(201)`."""
    dev_only(7)
    seed_concepts(world.dev_side)
    absorb(world.dev_side, 1, loser=21, closed=True)
    absorb(world.dev_side, 2, loser=22)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(201))
    assert code == 0, (out, err)
    return folder


# --- the verify at the end of an apply ----------------------------------------------------------------------------------------------

def test_an_apply_ends_with_a_clean_verify_of_every_batch_and_global_row(world, tmp_path, applied):
    doc, images = verify_of(tmp_path, rid(201))
    assert images["checked"] == 7 and images["differences"] == {}
    assert images["info"] == {"parity_added": 0, "parity_left_behind": 0, "parity_overwritten": 0, "parity_unchanged": 7}
    _, concepts = verify_of(tmp_path, rid(201), "concepts")
    assert concepts["checked"] == 3 and concepts["differences"] == {}
    _, absorptions = verify_of(tmp_path, rid(201), "popularity_absorptions")
    assert absorptions["checked"] == 3 and absorptions["info"]["parity_unchanged"] == 3
    assert set(doc["result"]["verify"]) >= {"images", "concepts", "concept_images", "popularity_absorptions"}
    assert "no differences" in (applied / "report.md").read_text() and not (applied / "dry").exists()


def test_a_difference_found_at_the_end_of_an_apply_is_exit_four_and_nothing_is_rolled_back(world, command, tmp_path, dev_only, monkeypatch):
    dev_only(4)
    original = apply_mod.verify_mod.verify_after_apply

    def tamper_then_verify(dev, prod, doc):
        world.prod_side.x("update tags set value = %s where image_id = %s", (SENTINEL, uid(2)))
        world.prod_side.x("delete from images where id = %s", (uid(3),))
        return original(dev, prod, doc)

    monkeypatch.setattr(apply_mod.verify_mod, "verify_after_apply", tamper_then_verify)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(202))
    assert code == 4, (out, err)
    assert "verification found 2 difference(s)" in err and "nothing was rolled back" in err and SENTINEL not in out + err
    assert world.prod_side.scalar("select count(*) from images") == 3  # the other three stay
    doc, images = verify_of(tmp_path, rid(202))
    assert images["differences"] == {"group_changed": {"count": 1, "ids": [uid(2)]}, "image_missing": {"count": 1, "ids": [uid(3)]}}
    assert len(committed_of(folder)["batches"][0]["ids"]) == 4
    assert SENTINEL not in (folder / "report.json").read_text() + (folder / "report.md").read_text()


def test_a_failed_batch_still_verifies_what_was_committed_and_the_exit_code_stays_one(world, command, tmp_path, dev_only, monkeypatch):
    dev_only(7)

    def boom(n, ids):
        if n == 2:
            raise RuntimeError("kill the batch")

    monkeypatch.setattr(apply_mod, "_before_commit", boom)
    code, out, err, folder = command("--apply", "--yes", "--batch-size", "3", run_id=rid(203))
    assert code == 1
    _, images = verify_of(tmp_path, rid(203))
    assert images["checked"] == 3 and images["differences"] == {}


# --- --verify-only ----------------------------------------------------------------------------------------------------------------

def test_verify_only_against_the_newest_run_is_clean_and_leaves_no_apply_input_behind(world, command, tmp_path, applied):
    code, out, err, folder = command("--verify-only", run_id=rid(210))
    assert code == 0, (out, err)
    assert f"verified against run {rid(201)}" in out and "no differences" in out
    assert (folder / "dry").exists() and (folder / "report.json").exists() and not (folder / "committed.json").exists()
    doc, images = verify_of(tmp_path, rid(210))
    assert doc["dry"] is True and doc["apply_command"] is None and images["checked"] == 7
    assert doc["sections"][0]["name"] == "verify" and {e["name"] for e in doc["sections"][0]["entries"]} >= {"images", "concepts"}
    assert files_gate.candidates(tmp_path / "state" / "runs", folder) == [tmp_path / "state" / "runs" / "20261113T115000Z-0000000a" / "files-promoted.json"]
    # it is not an input of the next verify either: the newest committed.json is still the apply's
    code, out, err, folder = command("--verify-only", run_id=rid(211))
    assert code == 0 and f"verified against run {rid(201)}" in out


@pytest.mark.parametrize("what", ["tag_value", "tag_row_missing", "tag_row_extra", "embedding", "image_deleted", "note_cleared"])
def test_verify_only_detects_a_changed_prod_child_row_or_a_deleted_image(world, command, tmp_path, applied, what):
    p = world.prod_side
    if what == "tag_value":
        p.x("update tags set value = %s where image_id = %s", (SENTINEL, uid(3)))
    elif what == "tag_row_missing":
        p.x("delete from tags where image_id = %s", (uid(3),))
    elif what == "tag_row_extra":
        p.x("insert into tags (id, image_id, key, value, source) values (%s, %s, 'meme', 'extra', 'ocr')", (uid(99_001), uid(3)))
    elif what == "embedding":
        p.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, uid(3)))
    elif what == "image_deleted":
        p.x("delete from images where id = %s", (uid(3),))
    elif what == "note_cleared":
        p.x("delete from description_notes where image_id = %s", (uid(3),))
    code, out, err, folder = command("--verify-only", run_id=rid(212))
    assert code == 4, (out, err)
    assert "verification found" in err and SENTINEL not in out + err
    doc, images = verify_of(tmp_path, rid(212))
    diffs = {c: v["ids"] for c, v in images["differences"].items()}
    info = images["info"]
    if what == "image_deleted":
        assert diffs == {"image_missing": [uid(3)]}
    else:
        assert diffs == {"group_changed": [uid(3)]}
        want = {"tag_value": {"changed_group_tags": 1, "content_changed_tags": 1},
                "tag_row_missing": {"changed_group_tags": 1, "rows_missing_tags": 1},
                "tag_row_extra": {"changed_group_tags": 1, "rows_extra_tags": 1},
                "embedding": {"changed_group_clip": 1, "content_changed_embeddings": 1},
                "note_cleared": {"changed_group_note": 1, "rows_missing_description_notes": 1,
                                 "rows_missing_description_note_embeddings": 1}}[what]
        assert {k: v for k, v in info.items() if not k.startswith("parity_")} == want
    for name in ("report.json", "report.md"):
        assert SENTINEL not in (folder / name).read_text(), name
    assert (folder / "dry").exists() and not (folder / "committed.json").exists()


def test_verify_only_detects_global_rows_that_are_missing_or_changed_on_prod(world, command, tmp_path, applied):
    p = world.prod_side
    p.x("update concepts set name = %s where id = 1", (SENTINEL,))
    p.x("delete from concepts where id = 3")
    p.x("delete from popularity_absorptions where id = %s", (uid(60_002),))
    p.x("update popularity_absorptions set weight = 99 where id = %s", (uid(61_001),))
    code, out, err, folder = command("--verify-only", run_id=rid(213))
    assert code == 4, (out, err)
    assert "DIFFERENCE concepts rows_changed: 1 row(s)" in out and "DIFFERENCE concepts rows_missing: 1 row(s)" in out
    assert "DIFFERENCE popularity_absorptions rows_missing: 1 row(s)" in out and SENTINEL not in out + err
    _, concepts = verify_of(tmp_path, rid(213), "concepts")
    assert {c: v["ids"] for c, v in concepts["differences"].items()} == {"rows_changed": [1], "rows_missing": [3]}
    _, absorptions = verify_of(tmp_path, rid(213), "popularity_absorptions")
    assert {c: v["ids"] for c, v in absorptions["differences"].items()} == {"rows_changed": [uid(61_001)], "rows_missing": [uid(60_002)]}
    _, images = verify_of(tmp_path, rid(213))
    assert images["differences"] == {}  # the image part is intact
    assert SENTINEL not in (folder / "report.json").read_text()


def test_deleting_a_concept_on_prod_cascades_and_every_missing_row_is_reported(world, command, tmp_path, applied):
    world.prod_side.x("delete from concepts where id = 1")  # its image set and image go with it
    code, out, err, folder = command("--verify-only", run_id=rid(214))
    assert code == 4
    for table in ("concepts", "concept_image_sets", "concept_images"):
        assert verify_of(tmp_path, rid(214), table)[1]["differences"]["rows_missing"]["ids"] == [1]


def test_verify_only_is_read_only_on_both_sides(world, command, tmp_path, applied):
    world.prod_side.x("update tags set value = 'x' where image_id = %s", (uid(3),))  # so that a difference exists: the failing path is read only too
    prod_log, dev_log = record(world.prod), record(world.dev)
    code, out, err, folder = command("--verify-only", run_id=rid(215))
    assert code == 4
    assert prod_log and dev_log
    reads = ("SELECT", "BEGIN", "COMMIT", "ROLLBACK", "SET")
    for sql in prod_log + dev_log:
        assert sql.lstrip().upper().startswith(reads), sql[:60]
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|COPY|ANALYZE|VACUUM|DROP|ALTER|CREATE|GRANT|pg_advisory|setval)\b", sql, re.I), sql[:60]
    assert not [s for s in prod_log + dev_log if s.lstrip().upper().startswith("SET") and "default_transaction_read_only = on" not in s
                and s != "SET default_transaction_read_only = off"  # the fixture's opener resetting a session, not the tool
                and not re.match(r"SET (TimeZone|DateStyle|extra_float_digits|bytea_output|IntervalStyle|client_encoding) = ", s)]
    begins = [s for s in prod_log + dev_log if s.lstrip().upper().startswith("BEGIN")]
    assert begins and all("READ ONLY" in s for s in begins)
    assert world.prod_side.scalar("select value from tags where image_id = %s", (uid(3),)) == "x"  # untouched by the verify
    for table in ("images", "concepts", "popularity_absorptions"):
        assert world.prod_side.scalar(f'select count(*) from "{table}"') == world.dev_side.scalar(f'select count(*) from "{table}"')


def test_verify_only_uses_the_newest_committed_json_and_falls_back_to_an_older_one(world, command, tmp_path, dev_only):
    for n in range(1, 6):
        rich(world.dev_side, n)
    write_files_report(tmp_path, 5)
    assert command("--apply", "--yes", run_id=rid(220))[0] == 0
    for n in range(6, 9):
        rich(world.dev_side, n)
    write_files_report(tmp_path, 8)
    code, out, err, newer = command("--apply", "--yes", run_id=rid(221))
    assert code == 0, (out, err)
    code, out, err, _ = command("--verify-only", run_id=rid(222))
    assert code == 0 and f"verified against run {rid(221)}" in out
    _, images = verify_of(tmp_path, rid(222))
    assert images["checked"] == 3  # only the images that run committed
    (newer / "committed.json").unlink()
    code, out, err, _ = command("--verify-only", run_id=rid(223))
    assert code == 0 and f"verified against run {rid(220)}" in out
    assert verify_of(tmp_path, rid(223))[1]["checked"] == 5
    world.prod_side.x("update tags set value = 'x' where image_id = %s", (uid(1),))  # the older run's image
    assert command("--verify-only", run_id=rid(224))[0] == 4


def test_a_later_promote_that_replaced_an_image_is_superseded_not_a_difference(world, command, tmp_path, dev_only):
    dev_only(6)
    assert command("--apply", "--yes", run_id=rid(230))[0] == 0
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    code, out, err, later = command("--apply", "--yes", run_id=rid(231))
    assert code == 0, (out, err)
    (later / "committed.json").unlink()  # verify the first run against what prod holds now
    code, out, err, _ = command("--verify-only", run_id=rid(232))
    assert code == 0, (out, err)
    _, images = verify_of(tmp_path, rid(232))
    assert images["differences"] == {} and images["info"]["superseded_groups"] == 1
    world.prod_side.x("update tags set value = 'neither dev nor the record' where image_id = %s", (uid(2),))
    assert command("--verify-only", run_id=rid(233))[0] == 4  # prod now equals neither


def test_left_behind_and_dev_changes_are_information_not_errors(world, command, tmp_path, applied):
    world.dev_side.x("update image_extras set flagged = true where image_id = %s", (uid(2),))  # flagged on dev after the promote
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(3),))  # changed on dev since the copy
    world.dev_side.x("update concepts set name = 'renamed on dev' where id = 1")
    code, out, err, folder = command("--verify-only", run_id=rid(240))
    assert code == 0, (out, err)
    doc, images = verify_of(tmp_path, rid(240))
    assert images["differences"] == {} and images["info"]["parity_left_behind"] == 1 and images["info"]["parity_overwritten"] == 1
    entries = {e["name"]: e for e in doc["sections"][0]["entries"]}
    assert entries["images"]["left_behind"]["ids"] == [uid(2)] and entries["images"]["overwritten"]["ids"] == [uid(3)]
    assert entries["concepts"]["overwritten"]["ids"] == [1]
    assert "parity_left_behind 1" in out


def test_only_the_tables_of_the_run_are_compared(world, command, tmp_path):
    six_rich(world, tmp_path)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    code, out, err, folder = command("--apply", "--yes", "--only", "tags", run_id=rid(250))
    assert code == 0, (out, err)
    assert committed_of(folder)["tables"] == ["images", "tags"]
    assert set(next(iter(committed_of(folder)["batches"][0]["digests"].values()))) == {"core", "tags"}
    world.prod_side.x("update embeddings set embedding = %s::vector where image_id = %s", ("[" + ",".join(["0.9"] * 512) + "]", uid(2)))
    assert command("--verify-only", run_id=rid(251))[0] == 0  # outside the tables of that run
    world.prod_side.x("update tags set value = 'x' where image_id = %s", (uid(2),))
    code, out, err, _ = command("--verify-only", run_id=rid(252))
    assert code == 4
    assert verify_of(tmp_path, rid(252))[1]["differences"]["group_changed"]["ids"] == [uid(2)]


def test_verify_only_runs_the_reduced_handshake_with_the_read_only_tool(world, command, tmp_path, applied, monkeypatch):
    seen = {}

    def spy(env, tool, mode="normal", **kw):
        seen.update(tool=tool, steps=kw.get("steps"), mode=mode)
        return [{"step": "H1", "ok": True, "detail": "ok"}]

    monkeypatch.setattr(connect, "handshake", spy)
    assert command("--verify-only", run_id=rid(260))[0] == 0
    assert seen == {"tool": "V", "steps": cli.VERIFY_STEPS, "mode": "normal"}
    doc = json.loads((tmp_path / "state" / "runs" / rid(260) / "report.json").read_text())
    assert doc["handshake"] == [{"step": "H1", "ok": True, "detail": "ok"}]


def test_verify_only_without_a_committed_json_is_exit_two(world, command, tmp_path):
    both(world, 1)
    code, out, err, folder = command("--verify-only", run_id=rid(270))
    assert code == 2 and "no committed.json" in err


def test_the_digests_a_verify_compares_are_the_recorded_snapshot_digests(world, tmp_path, applied):
    c = committed_of(applied)
    ids = [i for b in c["batches"] for i in b["ids"]]
    prod = dg.fetch_image_digests(world.prod, c["tables"], "ids", ids)
    groups = dg.groups_for(c["tables"])
    recorded = {i: h for b in c["batches"] for i, h in b["digests"].items()}
    assert {i: dg.image_group_hashes(prod[i], groups) for i in ids} == recorded
