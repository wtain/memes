"""P2-T7 against the two-schema fixture: the global sets of `sync.promote --apply` (concepts upsert, absorptions insert-or-close).

Only `ocrdb_test` (via `sync_pair`). The prod-side role of the fixture is a superuser, so "no write outside the registry, no delete of
a member" is proved by recording every statement the apply issues, not by privileges.
"""
import datetime as dt
import json
import re
import uuid

import pytest

from sync import registry
from sync.model import Klass
from sync.promote import apply as apply_mod
from sync.promote import digest as dg
from sync.promote import files_gate
from tests.integration.sync.test_promote_apply import (SENTINEL, VEC, VEC2, command, dev_only, record, result_of, rid, world)  # noqa: F401
from tests.integration.sync.test_promote_plan import Side, both, plan_of, settings_for, uid, write_files_report  # noqa: F401

_ = (command, world, dev_only)
CONCEPT_TABLES = ("concepts", "concept_image_sets", "concept_images", "concept_text_sets", "concept_texts")


def seed_concepts(side: Side, *, name="c") -> None:
    """Three concepts with one child in each of the four set tables (ids 1 to 3, children id 1 and 2)."""
    side.x("insert into concepts (id, name, embedding) values (1, %s, %s::vector), (2, %s, %s::vector), (3, %s, NULL)",
           (f"{name}1", VEC, f"{name}2", VEC, f"{name}3"))
    side.x("insert into concept_image_sets (id, concept_id, name, directory, centroid_embedding) values (1, 1, 's1', 'd1', %s::vector)", (VEC,))
    side.x("insert into concept_images (id, concept_image_set_id, filename, embedding) values (1, 1, 'f1.jpg', %s::vector)", (VEC,))
    side.x("insert into concept_text_sets (id, concept_id, name, centroid_embedding) values (1, 2, 't1', %s::vector)", (VEC,))
    side.x("insert into concept_texts (id, concept_text_set_id, name, text, embedding) values (1, 1, 'x', 'a text', %s::vector)", (VEC,))


def absorb(side: Side, n: int, loser, winner=1000, *, closed=False, created=0, source="cluster_review", weight=1) -> str:
    """One absorb row (id uid(60000 + n)) and, when `closed`, its reverse row (uid(61000 + n)); dev's own open-loser index is respected."""
    a, r = uid(60_000 + n), uid(61_000 + n)
    ts = dt.datetime(2026, 2, 1) + dt.timedelta(minutes=created or n)
    side.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, loser_filename, created_at) "
           "values (%s, 'absorb', %s, %s, %s, %s, %s, %s)", (a, source, uid(loser) if loser else None, uid(winner), weight, f"l{n}.jpg", ts))
    if closed:
        close(side, n, loser, winner, created=created)
    return a


def close(side: Side, n: int, loser, winner=1000, *, created=0, source="cluster_review", weight=1) -> None:
    a, r = uid(60_000 + n), uid(61_000 + n)
    ts = dt.datetime(2026, 2, 1) + dt.timedelta(minutes=(created or n) + 500)
    side.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, loser_filename, reverses_id, reason, "
           "created_at) values (%s, 'reverse', %s, %s, %s, %s, %s, %s, 'unflag', %s)",
           (r, source, uid(loser) if loser else None, uid(winner), weight, f"l{n}.jpg", a, ts))
    side.x("update popularity_absorptions set reversed_by_id = %s where id = %s", (r, a))


def hashes(conn, table, keys=None):
    return dg.fetch_global_hashes(conn, table, keys)


def committed_of(folder):
    return json.loads((folder / "committed.json").read_text())


def data_result(tmp_path, run_id, name):
    return result_of(tmp_path, run_id)[0].get("result", {}).get("data", {}).get(name)


# --- concepts: upsert, nothing deleted ----------------------------------------------------------------------------------------------

def test_concepts_are_added_and_updated_and_a_concept_only_prod_has_is_left_behind(world, command, tmp_path):
    for n in range(1, 7):
        both(world, n)
    write_files_report(tmp_path, 6)
    seed_concepts(world.dev_side, name=SENTINEL)
    p = world.prod_side
    p.x("insert into concepts (id, name, embedding) values (2, 'old name', %s::vector)", (VEC2,))  # differs from dev's concept 2
    p.x("insert into concepts (id, name) values (9, 'prod only')")
    p.x("insert into concept_image_sets (id, concept_id, name) values (5, 9, 'prod only set')")
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(101))
    assert code == 0, (out, err)
    assert p.scalar("select count(*) from concepts") == 4 and p.scalar("select name from concepts where id = 2") == f"{SENTINEL}2"
    assert p.scalar("select embedding::text from concepts where id = 2") == VEC  # an update replaces every column
    assert p.scalar("select name from concepts where id = 9") == "prod only" and p.scalar("select count(*) from concept_image_sets") == 2
    for table in CONCEPT_TABLES:
        keys = list(hashes(world.dev, table))
        assert hashes(world.prod, table, keys) == hashes(world.dev, table, keys), table  # row for row equal to dev's
    assert p.scalar("select count(*) from concept_images") == 1 and p.scalar("select count(*) from concept_texts") == 1
    assert not [s for s in log if re.match(r"\s*(DELETE|TRUNCATE)", s, re.I)]  # nothing was deleted
    doc = json.loads((folder / "report.json").read_text())
    concepts = next(e for e in doc["sections"][0]["entries"] if e["name"] == "concepts")
    assert concepts["added"]["ids"] == [1, 3] and concepts["overwritten"]["ids"] == [2] and concepts["left_behind"]["ids"] == [9]
    res = doc["result"]["data"]
    assert (res["concepts"]["added"], res["concepts"]["overwritten"]) == (2, 1) and res["concept_images"]["added"] == 1
    assert "images" in res and res["images"]["added"] == 0
    c = committed_of(folder)
    assert c["globals"] == {"concepts": True, "absorptions": False} and c["batches"] == []
    assert set(c["global_rows"]) == set(CONCEPT_TABLES) and set(c["global_rows"]["concepts"]) == {"1", "2", "3"}
    assert set(c["global_tables"]) >= set(CONCEPT_TABLES) and "popularity_absorptions" in c["global_tables"]
    for name in ("report.json", "report.md", "committed.json"):
        assert SENTINEL not in (folder / name).read_text(), name
    assert SENTINEL not in out + err and "global set concepts: committed" in out and "global concepts: 2 added, 1 overwritten" in out
    assert "sequences moved" in out and "concepts" in out
    p.x("insert into concepts (name) values ('next')")
    assert p.scalar("select max(id) from concepts") == 10  # the sequence moved past prod's highest id (9)


def test_a_second_run_changes_nothing_and_writes_no_committed_json(world, command, tmp_path):
    for n in range(1, 5):
        both(world, n)
    write_files_report(tmp_path, 4)
    seed_concepts(world.dev_side)
    absorb(world.dev_side, 1, loser=2, closed=True)
    absorb(world.dev_side, 2, loser=3)
    assert command("--apply", "--yes", run_id=rid(102))[0] == 0
    before = {t: hashes(world.prod, t) for t in (*CONCEPT_TABLES, "popularity_absorptions")}
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(103))
    assert code == 0, (out, err)
    assert not (folder / "committed.json").exists()
    assert {t: hashes(world.prod, t) for t in before} == before
    assert not [s for s in log if re.match(r"\s*(INSERT|UPDATE|DELETE|COPY)", s, re.I)]
    doc = json.loads((folder / "report.json").read_text())
    assert set(doc["result"]["data"]) == {"images"} and "verify" not in doc["result"]
    assert next(e for e in doc["sections"][0]["entries"] if e["name"] == "concepts")["unchanged"]["count"] == 3


def test_only_concepts_needs_no_image_and_leaves_the_image_tables_alone(world, command, tmp_path):
    seed_concepts(world.dev_side)
    both(world, 1)
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", "--only", "concepts", run_id=rid(104))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from concepts") == 3 and world.prod_side.scalar("select count(*) from concept_images") == 0
    c = committed_of(folder)
    assert c["tables"] == [] and c["global_tables"] == ["concepts"] and set(c["global_rows"]) == {"concepts"} and c["globals"]["concepts"]
    touched = {m for sql in log for m in re.findall(r'(?:INTO|FROM|UPDATE|ANALYZE)\s+"?([a-z_]+)"?', sql) if m in registry.REGISTRY}
    assert touched <= {"concepts"}, touched


def test_a_concept_changed_on_dev_after_the_plan_is_deferred(world, tmp_path):
    seed_concepts(world.dev_side)
    p, inp, _ = plan_of(world, tmp_path, "--only", "concepts")
    world.dev_side.x("update concepts set name = 'changed after the plan' where id = 2")
    world.dev_side.x("delete from concepts where id = 3")  # gone: deferred as well
    outcome = apply_mod.run_apply(settings=settings_for(tmp_path), inp=inp, plan=p, dev=world.dev, prod=world.prod, folder=tmp_path / "run",
                                  run_id="20261113T120000Z-0000aaa1")
    assert outcome.ok and outcome.globals["concepts"].added == [1]
    assert outcome.globals["concepts"].skipped == {"deferred_changed_since_plan": [2, 3]}
    assert world.prod_side.scalar("select count(*) from concepts") == 1


def test_a_global_digest_mismatch_rolls_that_table_back_and_stops_the_phase(world, command, tmp_path):
    for n in range(1, 4):
        both(world, n)
    write_files_report(tmp_path, 3)
    seed_concepts(world.dev_side)
    world.prod_side.x("create function sync_t_alter() returns trigger language plpgsql as "
                      "$$ begin new.name = new.name || 'x'; return new; end $$")
    world.prod_side.x("create trigger sync_t_alter before insert on concepts for each row execute function sync_t_alter()")
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(105))
    finally:
        world.prod_side.x("drop trigger sync_t_alter on concepts")
        world.prod_side.x("drop function sync_t_alter()")
    assert code == 1 and "digest_mismatch" in err
    assert world.prod_side.scalar("select count(*) from concepts") == 0 and world.prod_side.scalar("select count(*) from concept_image_sets") == 0
    assert not (folder / "committed.json").exists()
    res = json.loads((folder / "report.json").read_text())["result"]["data"]
    assert res["concepts"]["failed"]["failure"] == "digest_mismatch" and "concept_image_sets" not in res


def test_a_database_error_in_a_global_table_is_reduced_to_its_sqlstate(world, command, tmp_path):
    seed_concepts(world.dev_side, name=SENTINEL)
    world.prod_side.x("create function sync_t_boom() returns trigger language plpgsql as "
                      f"$$ begin raise exception 'leak {SENTINEL}' using errcode = 'P0001'; end $$")
    world.prod_side.x("create trigger sync_t_boom before insert on concepts for each row execute function sync_t_boom()")
    try:
        code, out, err, folder = command("--apply", "--yes", "--only", "concepts", run_id=rid(106))
    finally:
        world.prod_side.x("drop trigger sync_t_boom on concepts")
        world.prod_side.x("drop function sync_t_boom()")
    assert code == 1 and "db_error:p0001" in err and SENTINEL not in out + err
    assert world.prod_side.scalar("select count(*) from concepts") == 0
    assert SENTINEL not in (folder / "report.json").read_text()


# --- absorptions: insert-or-close ---------------------------------------------------------------------------------------------------

def test_new_absorptions_are_inserted_closed_ones_with_their_reverse_row_and_equal_to_dev(world, command, tmp_path):
    d = world.dev_side
    absorb(d, 1, loser=21)                                  # open
    absorb(d, 2, loser=22, closed=True)                     # closed
    absorb(d, 3, loser=23, closed=True, created=3)          # L23: closed, closed again, then open
    absorb(d, 4, loser=23, closed=True, created=10)
    absorb(d, 5, loser=23, created=20)
    absorb(d, 6, loser=None, closed=True, source="adjustment", weight=-3)
    absorb(d, 7, loser=None, source="adjustment", weight=4)
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", "--only", "popularity_absorptions", run_id=rid(111))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from popularity_absorptions") == 11  # 7 absorbs and 4 reverse rows
    keys = list(hashes(world.dev, "popularity_absorptions"))
    assert hashes(world.prod, "popularity_absorptions", keys) == hashes(world.dev, "popularity_absorptions", keys)
    assert world.prod_side.scalar("select count(*) from popularity_absorptions where kind = 'absorb' and reversed_by_id is null") == 3
    assert world.prod_side.scalar("select count(*) from popularity_absorptions where batch_run_id is not null") == 0
    res = data_result(tmp_path, rid(111), "popularity_absorptions")
    assert res["added"] == 11 and res["overwritten"] == 0
    c = committed_of(folder)
    assert c["globals"]["absorptions"] is True and not c["globals"]["concepts"] and len(c["global_rows"]["popularity_absorptions"]) == 11
    assert not [s for s in log if re.match(r"\s*(DELETE|TRUNCATE)", s, re.I)]
    # a statement that updated anything but the one pointer of an open absorb would show here
    updates = [s for s in log if s.lstrip().upper().startswith("UPDATE")]
    assert updates and all("SET reversed_by_id" in s and "reversed_by_id IS NULL" in s for s in updates)


def test_a_close_on_dev_reaches_prod_as_a_reverse_row_and_an_update(world, command, tmp_path):
    for side in (world.dev_side, world.prod_side):
        absorb(side, 1, loser=21)
        absorb(side, 2, loser=22)
    close(world.dev_side, 1, loser=21)  # dev undoes the first absorb after the shared promote
    code, out, err, folder = command("--apply", "--yes", "--only", "popularity_absorptions", run_id=rid(112))
    assert code == 0, (out, err)
    p = world.prod_side
    assert p.scalar("select reversed_by_id::text from popularity_absorptions where id = %s", (uid(60_001),)) == uid(61_001)
    assert p.scalar("select reversed_by_id from popularity_absorptions where id = %s", (uid(60_002),)) is None
    assert p.scalar("select count(*) from popularity_absorptions") == 3
    keys = list(hashes(world.dev, "popularity_absorptions"))
    assert hashes(world.prod, "popularity_absorptions", keys) == hashes(world.dev, "popularity_absorptions", keys)
    res = data_result(tmp_path, rid(112), "popularity_absorptions")
    assert (res["added"], res["overwritten"]) == (1, 1)
    entry = next(e for e in json.loads((folder / "report.json").read_text())["sections"][0]["entries"] if e["name"] == "popularity_absorptions")
    assert entry["overwritten"]["ids"] == [uid(60_001)] and entry["added"]["ids"] == [uid(61_001)]


def test_a_closed_absorb_is_never_reopened_and_a_row_only_prod_has_is_never_touched(world, command, tmp_path):
    absorb(world.dev_side, 1, loser=21)                     # dev: open
    absorb(world.prod_side, 1, loser=21, closed=True)       # prod: closed by a reverse row dev never had
    absorb(world.prod_side, 9, loser=29, closed=True)       # only prod has it
    before = hashes(world.prod, "popularity_absorptions")
    log = record(world.prod)
    code, out, err, folder = command("--apply", "--yes", "--only", "popularity_absorptions", run_id=rid(113))
    assert code == 0, (out, err)
    assert hashes(world.prod, "popularity_absorptions") == before
    assert not [s for s in log if re.match(r"\s*(INSERT|UPDATE|DELETE|COPY)", s, re.I)]
    entry = next(e for e in json.loads((folder / "report.json").read_text())["sections"][0]["entries"] if e["name"] == "popularity_absorptions")
    assert entry["left_behind"]["count"] == 3 and entry["overwritten"]["count"] == 0 and entry["unchanged"]["count"] == 1
    assert not (folder / "committed.json").exists()


def test_an_open_absorb_of_a_loser_that_prod_has_open_under_another_row_fails_without_a_partial_write(world, command, tmp_path):
    absorb(world.dev_side, 1, loser=21)                     # dev: A1 open on loser 21
    absorb(world.prod_side, 2, loser=21)                    # prod: another row, open, same loser (dev lacks it)
    absorb(world.dev_side, 3, loser=22)
    code, out, err, folder = command("--apply", "--yes", "--only", "popularity_absorptions", run_id=rid(114))
    assert code == 1 and "db_error:23505" in err  # uq_popularity_open_loser: surfaced, the table rolled back whole
    assert world.prod_side.scalar("select count(*) from popularity_absorptions") == 1
    assert not (folder / "committed.json").exists()


def test_the_globals_apply_names_no_class_p_or_x_table_and_deletes_nothing(world, command, tmp_path):
    for n in range(1, 5):
        both(world, n)
    write_files_report(tmp_path, 4)
    seed_concepts(world.dev_side)
    absorb(world.dev_side, 1, loser=21, closed=True)
    absorb(world.dev_side, 2, loser=22)
    world.prod_side.x("insert into concepts (id, name) values (9, 'prod only')")
    prod_log, dev_log = record(world.prod), record(world.dev)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(115))
    assert code == 0, (out, err)
    forbidden = [n for k in (Klass.P, Klass.X) for n in registry.names(k)] + ["alembic_version"]
    for sql in prod_log + [s for s in dev_log if s.startswith("COPY")]:
        for name in forbidden:
            assert not re.search(rf'\b{name}\b', sql), (name, sql[:80])
    for sql in prod_log:
        assert sql.lstrip().upper().startswith(("SELECT", "BEGIN", "COMMIT", "ROLLBACK", "SET", "INSERT INTO", "UPDATE", "ANALYZE")), sql[:60]
        assert not re.search(r"\b(TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|VACUUM|DELETE)\b", sql, re.I), sql[:60]
    assert all(s.lstrip().upper().startswith(("SELECT", "BEGIN", "COMMIT", "SET", "COPY (SELECT")) for s in dev_log)
    assert world.prod_side.scalar("select count(*) from concepts where id = 9") == 1


# --- flags --------------------------------------------------------------------------------------------------------------------------

def test_limit_images_skips_the_global_sets_until_the_uncapped_run(world, command, tmp_path, dev_only):
    dev_only(5)
    seed_concepts(world.dev_side)
    code, out, err, folder = command("--apply", "--yes", "--limit-images", "2", run_id=rid(116))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from concepts") == 0
    assert "global_tables" not in committed_of(folder) and not committed_of(folder)["globals"]["concepts"]
    code, out, err, folder = command("--apply", "--yes", run_id=rid(117))
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from concepts") == 3 and committed_of(folder)["globals"]["concepts"]
