"""P2-T10 against the two-schema fixture: the first-seed mode of `sync.promote`.

What the fixture can show: H9 in both directions through the real handshake step (with `memes.state` set per session), the catalog
detection of the missing HNSW and GIN indexes (the prod schema's indexes are dropped by the fixture's superuser, a stand-in for the
owner's drop before a seed), the owner steps in the report and on the console, the mass-change guard being waived for a seed, resume
of a seed after a crash, and that the promote never touches `memes.state` or any DDL. Not here: the real owner role that drops and
rebuilds indexes and flips the database setting (P5, `deploy/prod/README.md`).
"""
import json
import re

import pytest

from sync import connect
from sync.promote import apply as apply_mod
from sync.promote import preconditions as pre_mod
from tests.integration.sync.test_connect_fixture import SHA, caps, dev_conn, prod_conn, settings as handshake_settings
from tests.integration.sync.test_promote_apply import (SENTINEL, digests, dev_only, image_tables, record, rich, rich_both, six_rich,  # noqa: F401
                                                       tokens)
from tests.integration.sync.test_promote_plan import command, uid, world, write_files_report  # noqa: F401

RUN = "20261114T120000Z-{:08x}"


def rid(n: int) -> str:
    return RUN.format(n)


def h9(pair, tmp_path, mode, state):
    """Only the H9 step of the real handshake, against a prod session whose `memes.state` is `state`."""
    results = connect.handshake("general", "P", mode, settings=handshake_settings(tmp_path), dev=dev_conn(pair),
                                prod=prod_conn(pair, state=state), capabilities=caps, git_sha=SHA, fail_fast=False, steps=("H9",))
    return results[0]


# --- H9: the state decides the mode --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,state,ok,detail", [
    ("first-seed", "seeding", True, "ok"),
    ("first-seed", "serving", False, "state_serving_want_seeding"),
    ("first-seed", None, False, "state_unset_want_seeding"),
    ("normal", "seeding", False, "state_seeding_want_serving"),
    ("normal", "serving", True, "ok"),
])
def test_h9_first_seed_needs_seeding_and_the_normal_mode_needs_serving(sync_pair, tmp_path, mode, state, ok, detail):
    sync_pair.reset()
    r = h9(sync_pair, tmp_path, mode, state)
    assert (r["step"], r["ok"], r["detail"]) == ("H9", ok, detail)


def test_a_refused_first_seed_writes_nothing_and_exits_two(world, tmp_path, monkeypatch):
    """The command with the real handshake against a serving prod: exit 2 before the first read of dev data or write of prod."""
    import io
    from sync.promote import cli
    from tests.integration.sync.test_promote_plan import NOW, args_for, settings_for

    for n in range(1, 4):
        rich(world.dev_side, n)
    write_files_report(tmp_path, 3)

    class Opener:
        def __init__(self, env, tool, mode="normal", settings=None):
            pass

        def __enter__(self):
            world.prod.query("SET default_transaction_read_only = off")
            world.prod.query("select set_config('memes.state', 'serving', false)")
            return {"dev": world.dev, "prod": world.prod, "prod_reader_conn": None}

        def __exit__(self, *exc):
            return None

    real = connect.handshake

    def only_h9(*a, **k):  # the fixture's role is a superuser and one server: H3 and H7 cannot pass here (see test_connect_fixture)
        return real(*a, **{**k, "steps": ("H9",)})

    monkeypatch.setattr(connect, "handshake", only_h9)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(args_for("--first-seed", "--apply", "--yes", "--run-id", rid(1)), settings=settings_for(tmp_path),
                           session_opener=Opener, capabilities=caps, git_sha=SHA, now=NOW, out=out, err=err)
    assert code == 2 and "H9" in err.getvalue() and "state_serving_want_seeding" in err.getvalue()
    assert world.prod_side.scalar("select count(*) from images") == 0
    assert not (tmp_path / "state" / "runs" / rid(1) / "committed.json").exists()


# --- index state in the report -------------------------------------------------------------------------------------------------------

@pytest.fixture()
def drop_prod_indexes(world):
    """Drops prod-schema indexes for a test and recreates them afterwards (the schemas live for the whole package)."""
    saved: list = []

    def drop(*names):
        for n in names:
            with world.prod_side.raw.cursor() as cur:
                cur.execute("select indexdef from pg_indexes where schemaname = current_schema() and indexname = %s", (n,))
                saved.append(cur.fetchone()[0])
            world.prod_side.x(f'drop index "{n}"')

    try:
        yield drop
    finally:
        for sql in saved:
            world.prod_side.x(sql)


def test_the_model_declares_hnsw_and_gin_indexes_on_class_d_tables():
    ix = pre_mod.rebuildable_indexes()
    assert "ix_embeddings_embedding_hnsw_cosine" in ix["embeddings"] and "ix_ocr_lemmas_lemma_trgm" in ix["ocr_lemmas"]
    assert all(names for names in ix.values())


def test_a_first_seed_dry_run_names_the_tables_whose_indexes_the_owner_rebuilds(world, command, tmp_path, dev_only, drop_prod_indexes):
    dev_only(4)
    drop_prod_indexes("ix_embeddings_embedding_hnsw_cosine", "ix_ocr_lemmas_lemma_trgm")
    code, out, err, folder = command("--first-seed", run_id=rid(2))
    assert code == 0, (out, err)
    doc = json.loads((folder / "report.json").read_text())
    pre = {p["id"]: p for p in doc["preconditions"]}
    assert pre["rebuild_indexes:embeddings"] == {"id": "rebuild_indexes:embeddings", "ok": True, "detail": "missing=1"}
    assert pre["rebuild_indexes:ocr_lemmas"]["detail"] == "missing=1" and "rebuild_indexes" not in pre
    assert "owner step: rebuild the HNSW and GIN indexes of " in out and "embeddings" in out and "ocr_lemmas" in out
    assert "ALTER DATABASE ocrdb_test SET memes.state = 'serving'" in out and (folder / "dry").exists()
    assert "--first-seed --apply --plan-hash " in doc["apply_command"]
    assert world.prod_side.scalar("select count(*) from images") == 0


def test_a_first_seed_with_all_indexes_present_says_so_and_a_normal_run_reads_no_index_state(world, command, tmp_path, dev_only):
    dev_only(3)
    code, out, err, folder = command("--first-seed", run_id=rid(3))
    assert code == 0, (out, err)
    pre = {p["id"]: p for p in json.loads((folder / "report.json").read_text())["preconditions"]}
    assert pre["rebuild_indexes"]["detail"] == "all_present" and "the HNSW and GIN indexes are all present" in out
    _, out2, _, folder2 = command(run_id=rid(4))
    assert not any(p["id"].startswith("rebuild_indexes") for p in json.loads((folder2 / "report.json").read_text())["preconditions"])
    assert "owner step:" not in out2


# --- the seed itself ----------------------------------------------------------------------------------------------------------------------

def test_a_first_seed_loads_everything_by_batches_and_never_touches_state_or_ddl(world, command, tmp_path, dev_only, drop_prod_indexes):
    dev_only(7)
    drop_prod_indexes("ix_embeddings_embedding_hnsw_cosine")
    log = record(world.prod)
    code, out, err, folder = command("--first-seed", "--apply", "--yes", "--batch-size", "3", run_id=rid(5))
    assert code == 0, (out, err)
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))
    assert [len(b["ids"]) for b in json.loads((folder / "committed.json").read_text())["batches"]] == [3, 3, 1]
    assert "owner step: rebuild the HNSW and GIN indexes of embeddings" in out and "SET memes.state = 'serving'" in out
    assert not [s[:80] for s in log if re.search(r"memes\.state|set_config|\b(ALTER|CREATE|DROP|REINDEX)\b", s, re.I)]
    sec = json.loads((folder / "report.json").read_text())["sections"][0]
    assert sec["flags"].get("first_seed") is True


def test_the_mass_change_guard_does_not_apply_to_a_seed_but_does_to_a_normal_promote(world, command, tmp_path):
    six_rich(world, tmp_path)  # 12 images on both sides, equal
    for n in range(1, 8):  # 7 of 12 change on dev: above 25 %
        world.dev_side.x("update image_descriptions set text = 'a dog' where image_id = %s", (uid(n),))
    code, out, err, folder = command("--apply", "--yes", run_id=rid(6))
    assert code == 3 and "mass_change" in err
    assert world.prod_side.scalar("select count(*) from image_descriptions where text = 'a dog'") == 0
    code, out, err, folder = command("--first-seed", "--apply", "--yes", run_id=rid(7))  # a resumed seed: the same state of affairs
    assert code == 0, (out, err)
    assert world.prod_side.scalar("select count(*) from image_descriptions where text = 'a dog'") == 7
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))


def test_a_seed_interrupted_in_batch_two_resumes_with_the_same_command_and_only_loads_what_is_missing(world, command, tmp_path, dev_only,
                                                                                                    monkeypatch):
    dev_only(7)

    def boom(n, ids):
        if n == 2:
            raise RuntimeError(f"killed {SENTINEL}")

    monkeypatch.setattr(apply_mod, "_before_commit", boom)
    code, out, err, folder = command("--first-seed", "--apply", "--yes", "--batch-size", "3", run_id=rid(8))
    assert code == 1 and SENTINEL not in out + err
    assert "owner step, last: ALTER DATABASE" in out  # the steps are printed also after a partial run: the state still stays 'seeding'
    assert world.prod_side.scalar("select count(*) from images") == 3
    monkeypatch.setattr(apply_mod, "_before_commit", lambda n, ids: None)
    code, out, err, folder2 = command("--first-seed", "--apply", "--yes", "--batch-size", "3", run_id=rid(9))
    assert code == 0, (out, err)
    assert sum(len(b["ids"]) for b in json.loads((folder2 / "committed.json").read_text())["batches"]) == 4  # not the three of batch 1
    assert tokens(digests(world.prod)) == tokens(digests(world.dev, "promotable"))
    # and a third run, still in seeding mode, plans nothing
    code, out, err, folder3 = command("--first-seed", "--apply", "--yes", run_id=rid(10))
    assert code == 0 and not (folder3 / "committed.json").exists()
