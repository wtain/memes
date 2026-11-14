"""P4-T2 to T5 against the two-schema fixture: the dev schema plays dev (write-capable URL kind), the prod schema plays prod.

Only `ocrdb_test` (via `sync_pair`). Not covered here, because the fixture has no roles, tunnel or capabilities endpoint: the identity and role
guard of the handshake (H1 to H5 is replaced by a canned success; its logic is unit tested in tests/sync/test_connect.py and the dev
URL kind in tests/sync/test_pull_cli.py), a real network, real server timing and millions of rows. The prod side of the fixture is a superuser,
so "the pull never writes prod" is proved two ways: the prod session is put in `default_transaction_read_only` (any write would error) and every
statement issued on it is recorded and checked.
"""
import datetime as dt
import io
import json
import logging
import uuid

import pytest

from sync import connect
from sync.config import SyncSettings
from sync.pull import apply as apply_mod
from sync.pull import bugreports, cli, state
from sync.pull import plan as pl
from tests.integration.sync.test_promote_plan import Side, uid

NOW = dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SENT = "SENTINEL-TEXT-7d2b94"
SENT_DEV = "SENTINEL-DEV-TEXT-31ac08"
TS = "2026-01-01 10:00:00"
RUN = "20261113T120000Z-{:08x}"
ANSWER = "general ocrdb_test"
TABLES = list(pl.EXPECTED_TABLES)


def rid(n: int) -> str:
    return RUN.format(n)


def U(family: int, n: int) -> str:
    return uid(family * 1000 + n)


# --- seeding ---------------------------------------------------------------------------------------------------------------------

def sh(side: Side, n: int, query="find a cat", situation=None, client="web"):
    side.x("insert into search_history (id, query, situation, client, result_count, searched_at) values (%s,%s,%s,%s,3,%s)",
           (U(1, n), query, situation, client, TS))
    return U(1, n)


def tag(side: Side, n: int, search_n: int, value="cat"):
    side.x("insert into search_history_tags (id, search_id, category, value) values (%s,%s,'animal',%s)", (U(2, n), U(1, search_n), value))


def nf(side: Side, n: int, search_n: int):
    side.x("insert into search_not_found (id, search_history_id, created_at) values (%s,%s,%s)", (U(3, n), U(1, search_n), TS))


def save(side: Side, n: int, image_n: int = 1, search_n: int | None = None):
    side.x("insert into meme_saves (id, image_id, created_at, search_history_id) values (%s,%s,%s,%s)",
           (U(4, n), uid(image_n), TS, U(1, search_n) if search_n else None))


def imp(side: Side, n: int, image_n: int = 1, rank=1):
    side.x("insert into meme_impressions (id, image_id, shown_at, rank, surface) values (%s,%s,%s,%s,'search')", (n, uid(image_n), TS, rank))


def rating(side: Side, n: int, image_n: int, qh: str, grade=2, text=SENT, fallback="none"):
    side.x("insert into meme_text_ratings (id, query_hash, query_text, image_id, grade, rank, reading, parser_version, taxonomy_version, "
           "language, fallback, rated_at, updated_at) values (%s,%s,%s,%s,%s,1,'parsed','v1',3,'en',%s,%s,%s)",
           (U(5, n), qh, text, uid(image_n), grade, fallback, TS, TS))


def image(side: Side, n: int):
    side.image(n, desc=None, ocr_row=False, embedding=False, tags=())


def table_state(side: Side, table: str) -> tuple:
    """Row count and one hash of the whole-row text: equal on both sides when the tables are equal."""
    return (side.scalar(f'select count(*) from "{table}"'),
            side.scalar(f'select md5(coalesce(string_agg(t::text, \',\' order by t.id::text collate "C"), \'\')) from "{table}" t'))


def all_state(side: Side) -> dict:
    return {t: table_state(side, t) for t in TABLES}


def seed_prod(world, ratings: bool = False):
    p = world.prod_side
    for n in (1, 2):
        image(p, n)
    for n in (1, 2, 3):
        sh(p, n, query=f"query {n}", situation="situation text" if n == 1 else None)
    tag(p, 1, 1)
    tag(p, 2, 2, "dog")
    nf(p, 1, 3)
    for n in (1, 2, 3, 4):
        save(p, n, 1 if n % 2 else 2, search_n=1)
    for n in range(100, 106):
        imp(p, n, 1 + n % 2, rank=n - 99)
    if ratings:
        rating(p, 1, 1, "qh1")
        rating(p, 2, 2, "qh2", grade=1)


# --- harness ---------------------------------------------------------------------------------------------------------------------

def record(conn) -> list:
    log: list = []
    for name in ("query", "copy_in", "copy_out", "execute_values"):
        orig = getattr(conn, name)

        def wrap(sql, *a, _o=orig, **k):
            log.append(sql)
            return _o(sql, *a, **k)

        setattr(conn, name, wrap)
    return log


@pytest.fixture()
def world(sync_pair):
    sync_pair.reset()
    dev_raw, prod_raw = sync_pair.dev_conn(), sync_pair.prod_conn()
    dev_side, prod_side = Side(dev_raw), Side(prod_raw)
    dev_c, prod_c = sync_pair.dev_conn(), sync_pair.prod_conn()
    dev_c.autocommit = prod_c.autocommit = True
    dev = connect.SyncConnection(dev_c, connect.DEV_RW, None, None, "general")
    prod = connect.SyncConnection(prod_c, connect.PROD_READER)
    try:
        yield type("World", (), {"dev_side": dev_side, "prod_side": prod_side, "dev": dev, "prod": prod, "pair": sync_pair})
    finally:
        for c in (dev_raw, prod_raw, dev_c, prod_c):
            c.close()


def pull_settings(tmp_path, **pull) -> SyncSettings:
    return SyncSettings("general", {"state_dir": str(tmp_path / "state"), "dev": {"database": "ocrdb_test"},
                                    "prod": {"database": "ocrdb_test", "reader_role": "memes_general_sync_r", "api_url": "https://x.test"},
                                    "pull": pull}, root=tmp_path)


@pytest.fixture()
def command(world, tmp_path, monkeypatch):
    def canned(*a, capabilities=None, **k):
        if capabilities is not None:
            capabilities("https://x.test")
        return [{"step": "H1", "ok": True, "detail": "ok"}]

    monkeypatch.setattr(connect, "handshake", canned)
    dev_log, prod_log = record(world.dev), record(world.prod)

    class Opener:
        def __init__(self, env, tool, mode="normal", settings=None):
            Opener.seen = (env, tool, mode)

        def __enter__(self):
            world.prod.query("SET default_transaction_read_only = on")  # prod is never written: any write would error
            return {"dev": world.dev, "prod": world.prod, "prod_reader_conn": None}

        def __exit__(self, *exc):
            return None

    def run(*argv, run_id, settings=None, answer=ANSWER, routes=False, **pull):
        out, err = io.StringIO(), io.StringIO()
        s = settings or pull_settings(tmp_path, **pull)
        args = cli.build_parser().parse_args(["--env", "general", *argv, "--run-id", run_id])
        dev_log.clear()
        prod_log.clear()
        code = cli.run_pull(args, settings=s, session_opener=Opener, capabilities=lambda url: {"stage": "prod", "environment": "general",
                            "features": {"ratings": routes}}, now=NOW, out=out, err=err, ask=lambda prompt: answer)
        return code, out.getvalue(), err.getvalue(), tmp_path / "state" / "runs" / run_id

    run.dev_log, run.prod_log = dev_log, prod_log
    return run


def report_of(folder):
    return json.loads((folder / "report.json").read_text())


def pull_entry(doc, table):
    return next(e for e in doc["sections"][0]["entries"] if e["name"] == table)


def guard_of(doc, gid):
    return next(g for g in doc["sections"][0]["guards"] if g["id"] == gid)


def state_of(tmp_path):
    return json.loads((tmp_path / "state" / state.STATE_FILE).read_text())


def dry_then_apply(command, *argv, n=1, **kw):
    code, out, err, folder = command(*argv, run_id=rid(n), **kw)
    assert code == 0, (out, err)
    h = report_of(folder)["plan_hash"]
    return command(*argv, "--apply", "--yes", "--plan-hash", h, run_id=rid(n + 1), **kw)


# --- first pull, idempotence, dev-made activity ---------------------------------------------------------------------------------

def test_dry_run_changes_nothing_and_reports_the_plan(world, command, tmp_path):
    seed_prod(world)
    before = all_state(world.dev_side)
    code, out, err, folder = command(run_id=rid(1))
    assert code == 0, (out, err)
    assert (folder / "dry").exists() and (folder / "report.json").exists() and (folder / "report.md").exists()
    doc = report_of(folder)
    assert doc["dry"] is True and "--apply --plan-hash " + doc["plan_hash"] in doc["apply_command"]
    sec = doc["sections"][0]
    assert sec["name"] == "pull" and sec["tool"] == "sync.pull" and sec["flags"]["ratings_mode"] == "skip"
    assert pull_entry(doc, "meme_impressions")["added"]["count"] == 6 and pull_entry(doc, "meme_saves")["added"]["ids"] == sorted(U(4, n) for n in (1, 2, 3, 4))
    assert pull_entry(doc, "meme_text_ratings")["skipped_with_reason"]["ratings_mode_skip"]["count"] == 0
    assert all_state(world.dev_side) == before and not (tmp_path / "state" / state.STATE_FILE).exists()
    assert not (tmp_path / "state" / state.LEDGER_FILE).exists()
    assert all(not s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE", "COPY", "LOCK")) for s in command.dev_log)
    assert "plan hash:" in out and "[pull]" in out


def test_first_pull_into_an_empty_dev_mirror_makes_dev_equal_prod_and_writes_both_state_stages(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    stage_one = {}
    real = bugreports.run

    def spy(settings, transport, directory, run_id):
        stage_one.update(json.loads((tmp_path / "state" / state.STATE_FILE).read_text()))  # the file exists BEFORE the bug-report step
        return real(settings, transport, directory, run_id)

    monkeypatch.setattr(bugreports, "run", spy)
    code, out, err, folder = dry_then_apply(command)
    assert code == 0, (out, err)
    for t in TABLES[:2] + TABLES[3:]:
        assert table_state(world.dev_side, t) == table_state(world.prod_side, t), t
    assert table_state(world.dev_side, "meme_saves")[0] == 4 and table_state(world.dev_side, "meme_impressions")[0] == 6
    st = state_of(tmp_path)
    assert list(st) == ["env", "run_id", "finished_at", "snapshot_at", "snapshot", "applied", "ratings", "prod_image_ids", "tables", "bug_reports"]
    assert st["env"] == "general" and st["run_id"] == rid(2) and st["applied"] is True and st["prod_image_ids"] == 2
    assert st["tables"]["meme_saves"] == {"prod": 4, "replaced": 4} and st["tables"]["meme_text_ratings"] == {"prod": 0, "replaced": 0}
    assert st["ratings"]["mode"] == "skip" and st["ratings"]["prod_count"] == 0 and st["snapshot"] and st["snapshot_at"].endswith("Z")
    assert st["bug_reports"] == {"status": "skipped", "pulled": 0, "deleted_on_prod": 0, "failed": 0}
    assert stage_one["bug_reports"]["status"] == "skipped" and stage_one["run_id"] == rid(2)
    doc = report_of(folder)
    assert doc["dry"] is False and not (folder / "dry").exists()
    assert doc["result"]["pull"]["meme_saves"]["added"] == 4
    assert "applied: 16 rows written to dev in 6 tables" in out


def test_a_repeated_pull_is_idempotent(world, command, tmp_path):
    seed_prod(world)
    assert dry_then_apply(command)[0] == 0
    first = all_state(world.dev_side)
    code, out, err, folder = command(run_id=rid(3))
    assert code == 0
    doc = report_of(folder)
    for t in ("meme_saves", "meme_impressions", "search_history", "search_history_tags", "search_not_found"):
        e = pull_entry(doc, t)
        assert e["added"]["count"] == 0 and e["overwritten"]["count"] == 0 and e["unchanged"]["count"] == e["counts"]["prod"], t
    assert dry_then_apply(command, n=4)[0] == 0
    assert all_state(world.dev_side) == first


def test_dev_made_rows_are_replaced_and_the_report_counts_them_separately(world, command, tmp_path):
    seed_prod(world)
    assert dry_then_apply(command)[0] == 0
    d = world.dev_side
    sh(d, 90, query="made on dev")
    tag(d, 90, 90)
    save(d, 90)
    imp(d, 900)
    nf(d, 90, 90)
    d.x("update meme_saves set image_id = %s where id = %s", (uid(2), U(4, 1)))  # a prod row changed on dev
    code, out, err, folder = command(run_id=rid(3))
    doc = report_of(folder)
    assert code == 0 and pull_entry(doc, "search_history")["counts"]["dev_only_discarded"] == 1
    assert pull_entry(doc, "meme_saves")["overwritten"]["ids"] == sorted([U(4, 1), U(4, 90)])
    assert pull_entry(doc, "meme_saves")["counts"]["differing"] == 1
    assert dry_then_apply(command, n=4)[0] == 0
    for t in TABLES[:2] + TABLES[3:]:
        assert table_state(d, t) == table_state(world.prod_side, t), t
    assert d.scalar("select count(*) from search_history where query = 'made on dev'") == 0


# --- ratings ---------------------------------------------------------------------------------------------------------------------

def test_ratings_skip_leaves_dev_ratings_alone_and_still_computes_the_fingerprint(world, command, tmp_path):
    seed_prod(world, ratings=True)
    image(world.dev_side, 1)
    image(world.dev_side, 2)
    rating(world.dev_side, 50, 1, "qdev", text=SENT_DEV)
    before = table_state(world.dev_side, "meme_text_ratings")
    code, out, err, folder = dry_then_apply(command, routes=False)
    assert code == 0, (out, err)
    assert table_state(world.dev_side, "meme_text_ratings") == before  # the owner's dev rating is untouched
    st = state_of(tmp_path)
    lines = [f"{uid(1)}|qh1", f"{uid(2)}|qh2"]
    assert st["ratings"] == {"mode": "skip", "prod_count": 2, "max_updated_at": "2026-01-01T10:00:00.000000", "keys_sha256": pl.keys_sha256(lines)}
    assert st["tables"]["meme_text_ratings"] == {"prod": 2, "replaced": 0}
    doc = report_of(folder)
    assert pull_entry(doc, "meme_text_ratings")["skipped_with_reason"]["ratings_mode_skip"]["count"] == 2
    assert not (tmp_path / "state" / state.LEDGER_FILE).exists() and not (tmp_path / "state" / state.ARCHIVE_FILE).exists()
    assert any(p["id"] == "ratings_consistency" and p["detail"] == "warn_rows_but_routes_off" for p in doc["preconditions"])


def seed_ratings_world(world, tmp_path):
    p, d = world.prod_side, world.dev_side
    for n in (1, 2, 5, 6):
        image(p, n)
        image(d, n)
    image(d, 3)  # dev only: absent from prod's images (retired there)
    image(p, 4)  # prod only: unknown on dev
    rating(p, 1, 1, "qa")  # A: new on prod -> inserted
    rating(p, 2, 2, "qc", grade=2, text=SENT)  # C: differs on dev -> updated, dev row archived
    rating(d, 12, 2, "qc", grade=0, text=SENT_DEV)
    rating(p, 3, 4, "qb")  # B: image unknown on dev -> skipped
    rating(p, 4, 6, "qh")  # H: equal on both sides
    rating(d, 4, 6, "qh")
    rating(d, 15, 5, "qe", text=SENT_DEV)  # E: received earlier (ledger), prod no longer has it -> deleted, archived
    rating(d, 16, 5, "qd", text=SENT_DEV)  # D: made on dev -> kept
    rating(d, 17, 3, "qf", text=SENT_DEV)  # F: in the ledger but the image is retired on prod -> kept
    rating(d, 18, 3, "qg", text=SENT_DEV)  # G: image absent on prod -> kept
    state.write_ledger(tmp_path / "state", {pl.key_hash(uid(5), "qe"), pl.key_hash(uid(3), "qf")}, "general", NOW)


def test_ratings_mirror_is_ledger_guarded_archives_text_and_replaces_dev_rows(world, command, tmp_path, caplog):
    seed_ratings_world(world, tmp_path)
    p, d = world.prod_side, world.dev_side
    caplog.set_level(logging.DEBUG)
    code, out, err, folder = command(run_id=rid(1), ratings_mode="mirror", routes=True)
    assert code == 0, (out, err)
    e = pull_entry(report_of(folder), "meme_text_ratings")
    assert e["added"]["ids"] == [U(5, 1)] and e["overwritten"]["ids"] == sorted([U(5, 2), U(5, 15)])
    assert e["skipped_with_reason"]["skipped_unknown_image"]["ids"] == [U(5, 3)] and e["unchanged"]["count"] == 1
    assert sorted(e["left_behind"]["ids"]) == sorted([U(5, 16), U(5, 17), U(5, 18)])
    assert e["counts"]["archived"] == 2 and e["counts"]["deleted"] == 1 and e["counts"]["updated"] == 1 and e["counts"]["inserted"] == 1
    code, out, err, folder = dry_then_apply(command, n=2, ratings_mode="mirror", routes=True)
    assert code == 0, (out, err)
    keys = {(r[0], r[1]) for r in _rows(d, "select image_id::text, query_hash from meme_text_ratings")}
    assert keys == {(uid(1), "qa"), (uid(2), "qc"), (uid(6), "qh"), (uid(5), "qd"), (uid(3), "qf"), (uid(3), "qg")}
    assert d.scalar("select grade from meme_text_ratings where query_hash = 'qc'") == 2
    assert d.scalar("select id::text from meme_text_ratings where query_hash = 'qc'") == U(5, 2)  # prod wins, its id too
    assert d.scalar("select count(*) from meme_text_ratings where query_hash = 'qb'") == 0  # skipped-unknown: not copied
    archive = [json.loads(x) for x in (tmp_path / "state" / state.ARCHIVE_FILE).read_text().splitlines()]
    assert sorted((a["reason"], a["row"]["query_hash"], a["row"]["query_text"]) for a in archive) == [
        ("deleted", "qe", SENT_DEV), ("overwritten", "qc", SENT_DEV)]
    assert all(a["run_id"] == rid(3) for a in archive)
    ledger = state.load_ledger(tmp_path / "state")
    assert ledger == frozenset({pl.key_hash(uid(3), "qf"), pl.key_hash(uid(1), "qa"), pl.key_hash(uid(2), "qc"), pl.key_hash(uid(6), "qh")})
    st = state_of(tmp_path)
    assert st["ratings"]["mode"] == "mirror" and st["ratings"]["prod_count"] == 4 and st["tables"]["meme_text_ratings"] == {"prod": 4, "replaced": 2}
    assert st["ratings"]["keys_sha256"] == pl.keys_sha256([f"{uid(1)}|qa", f"{uid(2)}|qc", f"{uid(4)}|qb", f"{uid(6)}|qh"])
    # the rating text is only in the archive: never in a report, the summary, the logs or any other state file
    texts = [out, err, caplog.text] + [f.read_text(errors="replace") for f in (tmp_path / "state").rglob("*") if f.is_file() and f.name != state.ARCHIVE_FILE]
    assert not any(SENT in t or SENT_DEV in t for t in texts)
    # a second mirror pull changes nothing and archives nothing more
    n_lines = len(archive)
    assert dry_then_apply(command, n=5, ratings_mode="mirror", routes=True)[0] == 0
    assert len((tmp_path / "state" / state.ARCHIVE_FILE).read_text().splitlines()) == n_lines


def _rows(side, sql):
    with side.raw.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_a_prod_forget_is_mirrored_after_the_ledger_knows_the_key_and_the_row_is_archived(world, command, tmp_path):
    p, d = world.prod_side, world.dev_side
    image(p, 1)
    image(d, 1)
    rating(p, 1, 1, "qa")
    assert dry_then_apply(command, ratings_mode="mirror", routes=True)[0] == 0
    assert d.scalar("select count(*) from meme_text_ratings") == 1
    p.x("delete from meme_text_ratings")  # "Forget text" on prod
    # prod's ratings went from 1 to 0 (more than half of a lossy table) and nothing else is on prod: both guards refuse, then both lifts apply
    code, out, err, folder = command("--apply", "--yes", run_id=rid(3), ratings_mode="mirror", routes=True)
    assert code == 3 and "shrink:meme_text_ratings" in err and "empty_source" in err
    assert d.scalar("select count(*) from meme_text_ratings") == 1
    code, out, err, folder = command("--apply", "--yes", "--allow-shrink", "meme_text_ratings", "--allow-empty-source", run_id=rid(4),
                                     ratings_mode="mirror", routes=True)
    assert code == 0, (out, err)
    assert d.scalar("select count(*) from meme_text_ratings") == 0
    archive = [json.loads(x) for x in (tmp_path / "state" / state.ARCHIVE_FILE).read_text().splitlines()]
    assert [a["reason"] for a in archive] == ["deleted"] and archive[0]["row"]["query_text"] == SENT


def test_a_dev_rating_survives_a_pull_with_an_empty_prod_ratings_table_in_mirror_mode_without_a_ledger(world, command, tmp_path):
    image(world.dev_side, 1)
    image(world.prod_side, 1)
    rating(world.dev_side, 7, 1, "qdev", text=SENT_DEV)
    sh(world.prod_side, 1)
    assert dry_then_apply(command, ratings_mode="mirror", routes=True)[0] == 0
    assert world.dev_side.scalar("select count(*) from meme_text_ratings") == 1  # never received from prod: never deleted
    assert not (tmp_path / "state" / state.ARCHIVE_FILE).exists()


# --- guards ----------------------------------------------------------------------------------------------------------------------

def assert_guard_flow(command, world, tmp_path, gid, lift, n, *extra, **settings_kw):
    """Dry run exits 0 with the guard tripped; the apply without the lift exits 3 and writes nothing; with the lift it succeeds."""
    before = all_state(world.dev_side)
    state_before = (tmp_path / "state" / state.STATE_FILE).read_bytes() if (tmp_path / "state" / state.STATE_FILE).exists() else None
    code, out, err, folder = command(*extra, run_id=rid(n), **settings_kw)
    assert code == 0, (out, err)
    doc = report_of(folder)
    assert guard_of(doc, gid)["tripped"] is True and f"guard {gid} would trip" in out and "an apply would refuse (exit 3)" in out
    h = doc["plan_hash"]
    code, out, err, _ = command(*extra, "--apply", "--yes", "--plan-hash", h, run_id=rid(n + 1), **settings_kw)
    assert code == 3 and gid in err, (out, err)
    assert all_state(world.dev_side) == before
    after = (tmp_path / "state" / state.STATE_FILE).read_bytes() if (tmp_path / "state" / state.STATE_FILE).exists() else None
    assert after == state_before
    code, out, err, _ = command(*extra, *lift, "--apply", "--yes", "--plan-hash", h, run_id=rid(n + 2), **settings_kw)
    assert code == 0, (out, err)


def test_empty_source_guard(world, command, tmp_path):
    for n in (1, 2):
        save(world.dev_side, n, 1)
    sh(world.dev_side, 1)
    assert_guard_flow(command, world, tmp_path, "empty_source", ["--allow-empty-source"], 1)
    assert world.dev_side.scalar("select count(*) from meme_saves") == 0


def test_shrink_guard_for_an_append_only_and_a_lossy_table(world, command, tmp_path):
    seed_prod(world)
    assert dry_then_apply(command)[0] == 0
    world.prod_side.x("delete from meme_saves where id = %s", (U(4, 4),))  # an append-only table shrank by one: refused
    assert_guard_flow(command, world, tmp_path, "shrink:meme_saves", ["--allow-shrink", "meme_saves"], 3)
    # search_history: a drop of at most half is a count, more than half is a refusal
    world.prod_side.x("delete from search_not_found")
    world.prod_side.x("delete from search_history where id <> %s", (U(1, 1),))
    assert_guard_flow(command, world, tmp_path, "shrink:search_history", ["--allow-shrink", "search_history", "--allow-shrink", "search_not_found",
                                                                       "--allow-shrink", "search_history_tags"], 6)


def test_a_lost_state_file_makes_the_next_pull_a_first_pull_with_no_shrink_baseline(world, command, tmp_path):
    seed_prod(world)
    assert dry_then_apply(command)[0] == 0
    (tmp_path / "state" / state.STATE_FILE).unlink()
    world.prod_side.x("delete from meme_saves")
    code, out, err, folder = command(run_id=rid(3))
    assert code == 0 and "first pull" in out and "shrink:meme_saves" not in out
    assert dry_then_apply(command, n=4)[0] == 0 and world.dev_side.scalar("select count(*) from meme_saves") == 0


def test_growth_guard(world, command, tmp_path):
    seed_prod(world)
    state.write_state(tmp_path / "state", state.build_state(
        env="general", run_id=rid(9), now=NOW, snapshot_at="2026-11-13T11:00:00Z", snapshot="1:1:",
        ratings={"mode": "skip", "prod_count": 0, "max_updated_at": None, "keys_sha256": "0" * 64}, prod_image_ids=2,
        tables={"meme_saves": {"prod": 1, "replaced": 1}, "meme_impressions": {"prod": 6, "replaced": 6}}))
    assert_guard_flow(command, world, tmp_path, "growth:meme_saves", ["--allow-growth"], 1, max_growth_factor=2, min_growth_rows=2)


def test_row_ceiling_guard_reads_only_the_count_and_the_apply_still_verifies_with_an_aggregate_hash(world, command, tmp_path):
    seed_prod(world)
    assert_guard_flow(command, world, tmp_path, "row_ceiling:meme_impressions", ["--allow-growth"], 1, max_rows_per_pull=5)
    assert table_state(world.dev_side, "meme_impressions") == table_state(world.prod_side, "meme_impressions")
    doc = report_of(tmp_path / "state" / "runs" / rid(1))
    assert pull_entry(doc, "meme_impressions")["counts"]["not_read"] == 1 and pull_entry(doc, "meme_impressions")["added"]["count"] == 0


# --- truncate, text cap, setval --------------------------------------------------------------------------------------------------

def test_truncate_is_used_above_the_threshold_for_fk_free_tables_only(world, command, tmp_path):
    seed_prod(world)
    for n in range(10, 16):
        save(world.dev_side, n)
    for n in range(10, 16):
        sh(world.dev_side, n)
    code, out, err, folder = dry_then_apply(command, truncate_threshold=3)
    assert code == 0, (out, err)
    statements = [s for s in command.dev_log if s.startswith("TRUNCATE")]
    assert statements == ['TRUNCATE TABLE "meme_saves"'] and "truncated: meme_saves" in out
    assert any(s.startswith('DELETE FROM "search_history"') for s in command.dev_log)  # an FK parent is deleted, never truncated
    assert table_state(world.dev_side, "meme_saves") == table_state(world.prod_side, "meme_saves")


def test_the_text_cap_cuts_free_text_and_is_counted(world, command, tmp_path):
    sh(world.prod_side, 1, query="q" * 50, situation="s" * 30)
    tag(world.prod_side, 1, 1, "v" * 25)
    code, out, err, folder = command(run_id=rid(1), text_cap=10)
    assert code == 0 and "text cap: 3 value(s) would be cut" in out
    assert pull_entry(report_of(folder), "search_history")["counts"]["truncated_values"] == 2
    assert dry_then_apply(command, n=2, text_cap=10)[0] == 0
    assert world.dev_side.scalar("select query from search_history") == "q" * 10 and world.dev_side.scalar("select situation from search_history") == "s" * 10
    assert world.dev_side.scalar("select value from search_history_tags") == "v" * 10


def test_setval_moves_the_dev_sequence_past_the_copied_ids_and_resets_it_for_an_empty_table(world, command, tmp_path):
    seed_prod(world)
    assert dry_then_apply(command)[0] == 0
    world.dev_side.x("insert into meme_impressions (image_id, rank, surface) values (%s, 1, 'search')", (uid(1),))
    assert world.dev_side.scalar("select max(id) from meme_impressions") == 106
    world.prod_side.x("delete from meme_impressions")
    code, out, err, folder = command("--apply", "--yes", "--allow-shrink", "meme_impressions", run_id=rid(3))
    assert code == 0, (out, err)
    world.dev_side.x("insert into meme_impressions (image_id, rank, surface) values (%s, 1, 'search')", (uid(1),))
    assert world.dev_side.scalar("select min(id) from meme_impressions") == 1


# --- report, hash, redaction, safety ---------------------------------------------------------------------------------------------

def test_plan_hash_is_deterministic_survives_prod_activity_and_refuses_a_changed_structure(world, command, tmp_path):
    seed_prod(world)
    h1 = report_of(command(run_id=rid(1))[3])["plan_hash"]
    h2 = report_of(command(run_id=rid(2))[3])["plan_hash"]
    assert h1 == h2 and len(h1) == 64
    before = all_state(world.dev_side)
    # a changed flag (here --skip-files) is a different plan: the apply refuses before any write
    code, out, err, folder = command("--skip-files", "--apply", "--yes", "--plan-hash", h1, run_id=rid(3))
    assert code == 3 and "the plan changed since the dry run" in err and all_state(world.dev_side) == before
    assert not (tmp_path / "state" / state.STATE_FILE).exists()
    # readers are active on prod between the dry run and the apply: rows change, appear and go; the hash does not move (owner decision A)
    world.prod_side.x("update meme_saves set image_id = %s where id = %s", (uid(2), U(4, 1)))
    save(world.prod_side, 9, 1)
    imp(world.prod_side, 200, 1)
    h3 = report_of(command(run_id=rid(4))[3])["plan_hash"]
    assert h3 == h1
    code, out, err, _ = command("--apply", "--yes", "--plan-hash", h1, run_id=rid(5))
    assert code == 0, (out, err)
    assert all_state(world.dev_side) == all_state(world.prod_side)  # and the apply copied what prod holds NOW
    code, out, err, _ = command("--apply", "--yes", "--plan-hash", "f" * 64, run_id=rid(6))
    assert code == 3


def test_typed_confirmation_is_env_and_dev_database(world, command, tmp_path):
    seed_prod(world)
    before = all_state(world.dev_side)
    code, out, err, _ = command("--apply", run_id=rid(1), answer="general memes_general")  # the prod database name is not what confirms a write to dev
    assert code == 3 and "typed confirmation did not match" in err and all_state(world.dev_side) == before
    assert command("--apply", run_id=rid(2), answer=ANSWER)[0] == 0


def test_no_text_reaches_report_summary_logs_or_state_through_any_source(world, command, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    p, d = world.prod_side, world.dev_side
    sh(p, 1, query=SENT + "-query", situation=SENT + "-situation")
    tag(p, 1, 1, SENT + "-tag")
    sh(d, 50, query=SENT_DEV + "-query")
    tag(d, 50, 50, SENT_DEV + "-tag")
    image(p, 1)
    image(d, 1)
    rating(p, 1, 1, "qa", text=SENT + "-rating")
    rating(d, 2, 1, "qb", text=SENT_DEV + "-rating")
    outputs = []
    for mode in ("skip", "mirror"):
        for i, argv in enumerate(([], ["--apply", "--yes"]), 1):
            code, out, err, folder = command(*argv, run_id=rid(10 * (mode == "mirror") + i), ratings_mode=mode, routes=True, text_cap=12)
            assert code == 0, (out, err)
            outputs += [out, err, (folder / "report.json").read_text(), (folder / "report.md").read_text()]
    code, out, err, folder = command("--json", "-", run_id=rid(30))
    outputs += [out, err]
    texts = outputs + [caplog.text] + [f.read_text(errors="replace") for f in (tmp_path / "state").rglob("*")
                                         if f.is_file() and f.name != state.ARCHIVE_FILE]
    assert not any("SENTINEL" in t for t in texts)
    assert not any(s for s in command.dev_log + command.prod_log if "SENTINEL" in s)  # the text is not even in a statement


def test_the_pull_never_writes_prod_every_prod_statement_is_a_read(world, command, tmp_path):
    seed_prod(world, ratings=True)
    prod_before = all_state(world.prod_side)
    image(world.dev_side, 1)
    image(world.dev_side, 2)
    seen = []
    for argv, kw in (([], {}), (["--apply", "--yes"], {"ratings_mode": "mirror"}), (["--apply", "--yes"], {})):
        code, out, err, _ = command(*argv, run_id=rid(len(seen) + 1), **kw)
        assert code == 0, (out, err)
        seen += command.prod_log
    allowed = ("select", "begin", "commit", "set ", "copy (select")
    bad = [s for s in seen if not s.lstrip().lower().startswith(allowed)]
    assert bad == [] and seen
    assert all_state(world.prod_side) == prod_before


def test_the_pull_reads_one_consistent_snapshot_even_while_prod_is_written(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    real = world.prod.copy_out
    calls = []

    def writer_during_read(sql, fileobj):
        if not calls:  # a reader saves a meme on prod, committed, after the snapshot was taken and before the first COPY
            world.prod_side.x("insert into meme_saves (id, image_id, created_at) values (%s, %s, %s)", (U(4, 99), uid(1), TS))
        calls.append(sql)
        return real(sql, fileobj)

    monkeypatch.setattr(world.prod, "copy_out", writer_during_read)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    assert code == 0, (out, err)  # the in-transaction digest check compares dev with the same snapshot, so it passes
    assert world.dev_side.scalar("select count(*) from meme_saves where id = %s", (U(4, 99),)) == 0  # not in the snapshot: not torn in
    assert world.prod_side.scalar("select count(*) from meme_saves") == 5 and state_of(tmp_path)["tables"]["meme_saves"]["prod"] == 4


# --- failure behaviour -----------------------------------------------------------------------------------------------------------

def test_a_failure_inside_the_dev_transaction_rolls_every_table_back(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    sh(world.dev_side, 70, query="dev only")
    before = all_state(world.dev_side)

    def boom(inp, dev, prod):
        raise apply_mod.ApplyError("digest_mismatch")

    monkeypatch.setattr(apply_mod, "_verify_simple", boom)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    assert code == 1 and "rolled back" in err and "digest_mismatch" in err
    assert all_state(world.dev_side) == before and not (tmp_path / "state" / state.STATE_FILE).exists()
    doc = report_of(folder)
    assert doc["result"]["pull"]["meme_saves"]["failed"]["failure"] == "digest_mismatch"
    assert world.dev_side.scalar("select count(*) from search_history where query = 'dev only'") == 1


def test_a_database_error_in_the_copy_is_reduced_to_its_code_and_rolls_back(world, command, tmp_path):
    seed_prod(world)
    before = all_state(world.dev_side)
    world.dev_side.x("alter table meme_saves add constraint ck_tmp_pull check (image_id <> %s)" % ("'" + uid(2) + "'"))
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    finally:
        world.dev_side.x("alter table meme_saves drop constraint ck_tmp_pull")
    assert code == 1 and "db_error:23514" in err and SENT not in err
    assert all_state(world.dev_side) == before


def test_a_dev_lock_timeout_is_a_failed_run_that_changes_nothing(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    before = all_state(world.dev_side)
    monkeypatch.setattr(apply_mod, "LOCK_TIMEOUT", "300ms")
    blocker = world.pair.dev_conn()
    try:
        with blocker.cursor() as cur:  # an open dev writer: its row lock conflicts with SHARE ROW EXCLUSIVE
            cur.execute("insert into meme_saves (id, image_id) values (%s, %s)", (U(4, 77), uid(1)))
        code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    finally:
        blocker.rollback()
        blocker.close()
    assert code == 1 and "dev_lock_timeout" in err and all_state(world.dev_side) == before
    assert not (tmp_path / "state" / state.STATE_FILE).exists()


def test_the_bug_report_step_failing_keeps_the_tables_and_the_state_and_exits_one(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    monkeypatch.setattr(bugreports, "run", lambda *a: {"status": "partial", "pulled": 2, "deleted_on_prod": 1, "failed": 1})
    code, out, err, folder = dry_then_apply(command)
    assert code == 1 and "bug report" in err
    assert state_of(tmp_path)["bug_reports"] == {"status": "partial", "pulled": 2, "deleted_on_prod": 1, "failed": 1}
    assert table_state(world.dev_side, "meme_saves") == table_state(world.prod_side, "meme_saves")


def test_skip_files_does_not_call_the_bug_report_step(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    monkeypatch.setattr(bugreports, "run", lambda *a: pytest.fail("the bug-report step must not run with --skip-files"))
    code, out, err, folder = dry_then_apply(command, "--skip-files")
    assert code == 0 and state_of(tmp_path)["bug_reports"]["status"] == "skipped"
    assert report_of(folder)["sections"][0]["flags"]["skip_files"] is True


# --- preconditions ---------------------------------------------------------------------------------------------------------------

def test_a_column_mismatch_refuses_with_exit_two_and_an_alembic_difference_only_warns(world, command, tmp_path):
    seed_prod(world)
    before = all_state(world.dev_side)
    world.dev_side.x("alter table meme_saves add column extra_col text")
    try:
        code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    finally:
        world.dev_side.x("alter table meme_saves drop column extra_col")
    assert code == 2 and "column_compat" in err and all_state(world.dev_side) == before
    pre = report_of(folder)["preconditions"]
    assert any(p["id"] == "column_compat" and not p["ok"] and "meme_saves" in p["detail"] for p in pre)
    world.dev_side.x("update alembic_version set version_num = 'zzzz'")
    try:
        code, out, err, folder = command(run_id=rid(2))
    finally:
        world.dev_side.x("update alembic_version set version_num = %s", (world.pair.alembic_head,))
    assert code == 0 and any(p["id"] == "alembic_version" and p["ok"] and p["detail"] == "warn_differs" for p in report_of(folder)["preconditions"])


def test_a_handshake_mismatch_exits_two_and_nothing_on_dev_changes(world, command, tmp_path, monkeypatch):
    seed_prod(world)
    before = all_state(world.dev_side)

    def refuse(*a, **k):
        raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch", [{"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

    monkeypatch.setattr(connect, "handshake", refuse)
    code, out, err, folder = command("--apply", "--yes", run_id=rid(1))
    assert code == 2 and "prod_environment_mismatch" in err and all_state(world.dev_side) == before
    assert report_of(folder)["handshake"][0]["ok"] is False


def test_the_fixture_left_the_public_schema_alone(sync_pair, world, command):
    seed_prod(world)
    assert command("--apply", "--yes", run_id=rid(1))[0] == 0
    assert sync_pair.public_snapshot() == sync_pair.public_before
