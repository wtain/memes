"""P2-T7 and T8 without a database: the global-set write plan of the absorptions (ordering, never reopen, never delete), the SQL builders
of the global sets, committed.json and its global part, the verify module's pure parts (loading, newest run, result block, summary),
the `steps` restriction of the handshake and the `--verify-only` command line up to the first connection."""
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from sync import connect, report
from sync.config import SyncSettings, exclusive_lock, mark_dry
from sync.promote import apply as A
from sync.promote import cli
from sync.promote import digest as dg
from sync.promote import verify as V
from tests.sync.test_connect import ENV, SHA, caps_ok, facts, good_pair, make_settings  # noqa: F401  (facts is a fixture)

RUN1, RUN2, RUN3 = "20261113T100000Z-0000000a", "20261113T110000Z-0000000b", "20261113T120000Z-0000000c"
SENTINEL = "SENTINEL-ROW-VALUE-77c1"
I1, I2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
COLS = [n for n, _ in A.global_columns(dg.ABSORPTIONS)]


# --- SQL builders of the global sets -------------------------------------------------------------------------------------------------

def test_global_columns_are_alphabetical_and_a_nullified_column_reads_null():
    assert COLS == sorted(COLS) and "batch_run_id" in COLS
    sql = A.global_select_sql(dg.ABSORPTIONS)
    assert 'NULL' in sql and 't."batch_run_id"' not in sql and "ANY(%s::uuid[])" in sql
    assert "ANY(%s::int4[])" in A.global_select_sql("concepts")
    assert 't."embedding"::text' in A.global_select_sql("concepts")  # a vector travels as text and is cast back on the insert
    assert "%s::vector" in A.global_template("concepts") and A.global_template("concepts").count("%s") == 3


def test_concepts_are_upserted_by_key_with_every_other_column_updated_and_nothing_is_deleted():
    sql = A.global_upsert_sql("concepts")
    assert sql.startswith('INSERT INTO "concepts" ("embedding", "id", "name") VALUES %s ON CONFLICT ("id") DO UPDATE SET ')
    assert '"name" = EXCLUDED."name"' in sql and '"embedding" = EXCLUDED."embedding"' in sql and '"id" = EXCLUDED' not in sql
    for table in dg.global_tables():
        text = A.global_upsert_sql(table) + A.global_select_sql(table)
        assert not re.search(r"\b(DELETE|TRUNCATE|DROP)\b", text, re.I)


def test_the_close_statement_only_touches_open_absorbs():
    sql = A.absorption_close_sql()
    assert "t.reversed_by_id IS NULL" in sql and "t.kind = 'absorb'" in sql and sql.startswith('UPDATE "popularity_absorptions"')
    assert not re.search(r"\b(DELETE|TRUNCATE|DROP)\b", sql + A.absorption_insert_sql(), re.I)
    assert "reversed_by_id" in A.absorption_insert_sql()


def test_the_apply_module_has_no_delete_of_a_global_set_member():
    src = Path(A.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")), flags=re.S)
    deletes = re.findall(r"DELETE FROM[^\n]{0,60}", code)
    assert deletes and all("_ident(table)" in d for d in deletes)  # only delete_sql (derived tables of a changed image)
    assert not re.search(r"DELETE\s+FROM\s+\"?(concepts|concept_|popularity)", code)


def test_global_sets_map_to_the_two_flags_of_committed_json():
    assert A.set_of("popularity_absorptions") == "absorptions"
    assert {A.set_of(t) for t in dg.global_tables() if t != "popularity_absorptions"} == {"concepts"}


# --- the absorption write plan, against a fake prod that enforces the constraints -----------------------------------------------------

def arow(id, kind="absorb", loser=None, rev_by=None, reverses=None, created="2026-01-01T00:00:00"):
    d = dict.fromkeys(COLS)
    d.update(id=id, kind=kind, source="cluster_review", loser_image_id=loser, winner_image_id="w", weight=1, created_at=created,
             reversed_by_id=rev_by, reverses_id=reverses, reason="unflag" if kind == "reverse" else None)
    return tuple(d[c] for c in COLS)


class FakeProd:
    """execute_values recorder that also plays the table: one open absorb per loser, a reverse row needs its absorb, an update needs its
    reverse row, a closed absorb is never touched."""

    def __init__(self, existing=()):
        self.calls, self.rows = [], {}
        ix = {c: i for i, c in enumerate(COLS)}
        self.ix = ix
        for r in existing:
            self.rows[r[ix["id"]]] = list(r)

    def _open_losers(self):
        ix = self.ix
        return [r[ix["loser_image_id"]] for r in self.rows.values()
                if r[ix["kind"]] == "absorb" and r[ix["reversed_by_id"]] is None and r[ix["loser_image_id"]] is not None]

    def execute_values(self, sql, rows, template=None, page_size=500):
        ix = self.ix
        if sql.startswith("INSERT"):
            self.calls.append(("insert", [r[ix["id"]] for r in rows]))
            for r in rows:
                assert r[ix["id"]] not in self.rows
                if r[ix["kind"]] == "reverse":
                    assert r[ix["reverses_id"]] in self.rows, "reverse row before its absorb"
                self.rows[r[ix["id"]]] = list(r)
                opens = self._open_losers()
                assert len(opens) == len(set(opens)), "two open absorbs of one loser"
        else:
            assert sql.startswith("UPDATE")
            self.calls.append(("close", [tuple(r) for r in rows]))
            for a, rev in rows:
                assert rev in self.rows, "close before the reverse row"
                if self.rows[a][ix["reversed_by_id"]] is None:
                    self.rows[a][ix["reversed_by_id"]] = rev


def state_of(*rows):
    ix = COLS.index("reversed_by_id")
    return {r[COLS.index("id")]: ("h", r[ix] is not None) for r in rows}


def test_new_absorbs_are_written_closed_ones_in_rounds_and_open_ones_last():
    dev = {r[COLS.index("id")]: r for r in [
        arow("A1", loser="L1"),                                                   # open
        arow("A2", loser="L2", rev_by="R2", created="2026-01-01T00:00:01"), arow("R2", "reverse", "L2", reverses="A2"),
        arow("A3a", loser="L3", rev_by="R3a", created="2026-01-01T00:00:02"), arow("R3a", "reverse", "L3", reverses="A3a"),
        arow("A3b", loser="L3", rev_by="R3b", created="2026-01-01T00:00:03"), arow("R3b", "reverse", "L3", reverses="A3b"),
        arow("A3c", loser="L3", created="2026-01-01T00:00:04"),                  # the single open absorb of L3
        arow("ADJ1", loser=None, rev_by="RADJ1", created="2026-01-01T00:00:05"), arow("RADJ1", "reverse", None, reverses="ADJ1"),
        arow("ADJ2", loser=None, rev_by="RADJ2", created="2026-01-01T00:00:06"), arow("RADJ2", "reverse", None, reverses="ADJ2"),
    ]}
    prod = FakeProd()
    written = A.write_absorptions(prod, dev, list(dev), {})
    assert written == set(dev)  # every row, absorbs and their reverse rows
    kinds = [c[0] for c in prod.calls]
    assert kinds == ["insert", "insert", "close", "insert", "insert", "close", "insert"]
    round0 = prod.calls[0][1]
    assert set(round0) == {"A2", "A3a", "ADJ1", "ADJ2"}  # one per loser (the loser-less adjustments are independent)
    assert prod.calls[3][1] == ["A3b"] and prod.calls[6][1] == ["A1", "A3c"]  # the open ones are last
    ix = COLS.index("reversed_by_id")
    assert [prod.rows[k][ix] for k in ("A1", "A3c")] == [None, None]
    assert prod.rows["A2"][ix] == "R2" and prod.rows["A3a"][ix] == "R3a" and prod.rows["A3b"][ix] == "R3b" and prod.rows["ADJ1"][ix] == "RADJ1"


def test_an_absorb_prod_has_open_and_dev_has_closed_gets_its_reverse_row_and_the_update():
    a_open = arow("A", loser="L")
    a_closed = arow("A", loser="L", rev_by="R")
    r = arow("R", "reverse", "L", reverses="A")
    prod = FakeProd([a_open])
    written = A.write_absorptions(prod, {"A": a_closed, "R": r}, ["A", "R"], state_of(a_open))
    assert written == {"A", "R"} and [c[0] for c in prod.calls] == ["insert", "close"]
    assert prod.calls[0][1] == ["R"] and prod.calls[1][1] == [("A", "R")]
    assert prod.rows["A"][COLS.index("reversed_by_id")] == "R"


def test_a_closed_absorb_on_prod_is_never_reopened_nor_re_pointed():
    closed_prod = arow("A", loser="L", rev_by="R")
    r = arow("R", "reverse", "L", reverses="A")
    prod = FakeProd([arow("A", loser="L"), r])  # state below says prod's A is closed
    prod.rows["A"][COLS.index("reversed_by_id")] = "R"
    # dev has the absorb open (dev never saw the reverse): not an overwrite, and even handed to the writer nothing happens
    assert A.write_absorptions(prod, {"A": arow("A", loser="L")}, ["A"], state_of(closed_prod, r)) == set()
    # dev closed it with another reverse row: prod's is closed already, so nothing is written either
    other = arow("R9", "reverse", "L", reverses="A")
    assert A.write_absorptions(prod, {"A": arow("A", loser="L", rev_by="R9"), "R9": other}, ["A"], state_of(closed_prod, r)) == set()
    assert prod.calls == [] and prod.rows["A"][COLS.index("reversed_by_id")] == "R"


def test_a_reverse_pointer_that_dev_does_not_hold_is_a_hard_error_and_a_lonely_reverse_row_is_refused():
    with pytest.raises(A.ApplyError, match="absorption_dangling_reverse"):
        A.write_absorptions(FakeProd(), {"A": arow("A", loser="L", rev_by="MISSING")}, ["A"], {})
    with pytest.raises(A.ApplyError, match="absorption_dangling_reverse"):  # the pointed-at row is not a reverse row of this absorb
        A.write_absorptions(FakeProd(), {"A": arow("A", loser="L", rev_by="R"), "R": arow("R", "reverse", "L", reverses="OTHER")}, ["A"], {})
    closed = arow("A", loser="L", rev_by="R")
    with pytest.raises(A.ApplyError, match="absorption_unclaimed_reverse"):
        A.write_absorptions(FakeProd([closed]), {"R": arow("R", "reverse", "L", reverses="A")}, ["R"], state_of(closed))


def test_rows_only_prod_has_are_not_in_the_write_plan():
    prod_only = arow("P1", loser="LX")
    prod = FakeProd([prod_only])
    new = arow("A", loser="L")
    written = A.write_absorptions(prod, {"A": new}, ["A"], state_of(prod_only))
    assert written == {"A"} and "P1" in prod.rows and prod.calls == [("insert", ["A"])]


# --- committed.json -------------------------------------------------------------------------------------------------------------------

def test_committed_json_records_global_rows_and_flips_the_set_flag(tmp_path):
    log = A.CommittedLog(tmp_path, RUN1, "general", [], ["concepts", "concept_images"])
    assert not (tmp_path / "committed.json").exists()
    log.record_global("concepts", {1: "h1", 2: "h2"})
    log.flip_global("concepts")
    doc = json.loads((tmp_path / "committed.json").read_text())
    assert doc["globals"] == {"concepts": True, "absorptions": False}
    assert doc["global_rows"] == {"concepts": {"1": "h1", "2": "h2"}} and doc["global_tables"] == ["concepts", "concept_images"]
    again = A.CommittedLog(tmp_path, RUN1, "general", [], ["concepts"])
    assert again.doc["global_rows"] == doc["global_rows"] and again.doc["globals"]["concepts"] is True
    again.record_global("concepts", {3: "h3"})
    assert json.loads((tmp_path / "committed.json").read_text())["global_rows"]["concepts"] == {"1": "h1", "2": "h2", "3": "h3"}
    assert V.has_committed(doc) and not V.has_committed({"batches": [{"ids": []}]})


# --- verify: loading and the newest run -----------------------------------------------------------------------------------------------

def committed(run_id=RUN1, env="general", ids=(I1,), tables=("images", "tags"), global_rows=None, **extra):
    doc = {"run_id": run_id, "env": env, "tables": list(tables), "globals": {"concepts": False, "absorptions": False},
           "batches": [{"n": 1, "committed_at": "2026-11-13T10:00:00Z", "ids": list(ids), "digests": {i: {"core": "a", "tags": "b"} for i in ids},
                        "renamed": []}] if ids else []}
    if global_rows:
        doc["global_rows"] = global_rows
    doc.update(extra)
    return doc


def put(tmp_path, run_id, doc=None, text=None, dry=False):
    folder = tmp_path / "runs" / run_id
    folder.mkdir(parents=True)
    if doc is not None or text is not None:
        (folder / "committed.json").write_text(text if text is not None else json.dumps(doc))
    if dry:
        mark_dry(folder)
    return folder


def test_parse_committed_accepts_what_the_apply_writes_and_refuses_other_shapes():
    assert V.parse_committed(committed(global_rows={"concepts": {"1": "h"}}, global_tables=["concepts"]))
    for bad in (None, [], {}, committed(tables=["meme_saves"]), committed(global_rows={"meme_saves": {}}), {**committed(), "batches": {}},
                {**committed(), "batches": [{"ids": [I1]}]}):
        assert V.parse_committed(bad) is None


def test_the_newest_committed_json_of_the_environment_wins_and_dry_empty_and_foreign_ones_are_passed_over(tmp_path):
    runs = tmp_path / "runs"
    assert V.newest_committed(runs, "general") is None
    put(tmp_path, RUN1, committed(RUN1))
    put(tmp_path, RUN2, committed(RUN2))
    put(tmp_path, RUN3, committed(RUN3), dry=True)  # a dry folder is never an input
    path, doc = V.newest_committed(runs, "general")
    assert doc["run_id"] == RUN2 and path.parent.name == RUN2
    assert V.newest_committed(runs, "general", skip=tmp_path / "runs" / RUN2)[1]["run_id"] == RUN1  # this run's own folder is skipped
    assert V.newest_committed(runs, "metal") is None
    put(tmp_path, "20261113T130000Z-0000000d", committed("20261113T130000Z-0000000d", ids=()))  # nothing committed in it
    put(tmp_path, "20261113T140000Z-0000000e")  # a folder without the file (a dry run of a no-op apply)
    assert V.newest_committed(runs, "general")[1]["run_id"] == RUN2


def test_an_unreadable_committed_json_is_a_verify_error_not_silently_skipped(tmp_path):
    put(tmp_path, RUN1, committed(RUN1))
    put(tmp_path, RUN2, text="{not json")
    with pytest.raises(V.VerifyError, match="committed_json_unreadable"):
        V.newest_committed(tmp_path / "runs", "general")


# --- verify: the result block and the console ----------------------------------------------------------------------------------------

def test_result_entries_carry_ids_codes_and_counts_only():
    res = V.VerifyResult(run_id=RUN1, checked_images=5, differences={"group_changed": [I2], "image_missing": [I1]},
                         info={"parity_added": 2, "changed_group_tags": 1}, global_checked={"concepts": 3},
                         global_differences={"concepts": {"rows_missing": [2]}}, global_info={"concepts": {"superseded_rows": 1}})
    assert not res.ok and res.difference_count == 3
    entries = V.result_entries(res)
    assert set(entries) == {"images", "concepts"} and entries["images"].checked == 5
    doc = report.Document(env="general", run_id=RUN1, tool="sync.promote", dry=False, result={"verify": entries})
    js = report.document_json(doc)["result"]["verify"]
    assert js["images"]["verify"]["differences"]["group_changed"] == {"count": 1, "ids": [I2]}
    assert js["concepts"]["verify"]["differences"]["rows_missing"]["ids"] == [2] and js["concepts"]["verify"]["info"] == {"superseded_rows": 1}
    md = report.render_markdown(doc)
    assert "verified 5: DIFFERENCES: group_changed 1, image_missing 1" in md and "verified 3: DIFFERENCES: rows_missing 1" in md
    text = "\n".join(V.summary(res))
    assert "DIFFERENCE image_missing: 1 image(s)" in text and "DIFFERENCE concepts rows_missing: 1 row(s)" in text
    clean = V.VerifyResult(run_id=RUN1, checked_images=2, global_checked={"concepts": 1})
    assert clean.ok and "no differences" in V.summary(clean)[0]
    assert not V.VerifyResult(error="db_error:42501").ok and "could not run" in V.summary(V.VerifyResult(error="x"))[0]


def test_a_result_entry_refuses_free_text_as_a_difference_code_and_negative_counts():
    with pytest.raises(report.ReportError):
        report.ResultEntry(checked=1, differences={f"the row says {SENTINEL}": [I1]})
    with pytest.raises(report.ReportError):
        report.ResultEntry(checked=1, info={"parity_added": -1})
    with pytest.raises(report.ReportError):
        report.ResultEntry(checked=1, differences={"group_changed": [SENTINEL]})
    plain = report.ResultEntry(added=1)
    assert "verify" not in report.document_json(report.Document(env="general", run_id=RUN1, tool="sync.promote", dry=False,
                                                                result={"data": {"images": plain}}))["result"]["data"]["images"]


def test_result_of_lists_every_global_table_the_apply_reached_and_the_verify_section():
    sec = report.Section(name="data", tool="sync.promote", entries=[
        report.Entry(name="images", kind="images"), report.Entry(name="concepts", kind="rows"),
        report.Entry(name="popularity_absorptions", kind="rows")])
    p = SimpleNamespace(section=sec)
    outcome = A.ApplyOutcome(added=[I1])
    outcome.globals["concepts"] = A.GlobalOutcome(added=[1, 2], overwritten=[3], skipped={"deferred_changed_since_plan": [4]})
    outcome.verify = V.VerifyResult(run_id=RUN1, checked_images=1, global_checked={"concepts": 3})
    res = cli.result_of(p, outcome)
    assert set(res["data"]) == {"images", "concepts"}  # the absorptions were not reached
    assert (res["data"]["concepts"].added, res["data"]["concepts"].overwritten) == (2, 1)
    assert res["data"]["concepts"].skipped == {"deferred_changed_since_plan": [4]}
    assert set(res["verify"]) == {"images", "concepts"}
    outcome.verify = V.VerifyResult(error="db_error:08006")
    assert "verify" not in cli.result_of(p, outcome)  # a verify that could not run is a failure, not a result


# --- the handshake steps of --verify-only ---------------------------------------------------------------------------------------------

def test_verify_only_runs_h1_to_h4_and_h10_with_the_read_only_prod_session(facts):
    d, p = good_pair()
    d.kind, p.kind = connect.DEV_RO, connect.PROD_WRITER_RO
    results = connect.handshake(ENV, "V", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA, steps=cli.VERIFY_STEPS)
    assert [r["step"] for r in results] == ["H1", "H2", "H3", "H4", "H10"] and all(r["ok"] for r in results)
    full = connect.handshake(ENV, "V", settings=make_settings(), dev=d, prod=p, capabilities=caps_ok, git_sha=SHA, fail_fast=False,
                             transport=SimpleNamespace(handshake=lambda: {"environment": ENV}))
    assert [r["step"] for r in full] == ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H10"]  # no restriction by default


# --- the --verify-only command line ---------------------------------------------------------------------------------------------------

def settings(tmp_path):
    return SyncSettings("general", {"state_dir": str(tmp_path / "state"), "dev": {"database": "d"}, "prod": {"database": "p"}}, root=tmp_path)


class Conn:
    def __init__(self):
        self.sql = []

    def query(self, sql, args=None):
        self.sql.append(sql)
        return []

    def scalar(self, sql, args=None):
        self.query(sql, args)


class Opener:
    def __init__(self, dev, prod):
        self.dev, self.prod, self.args = dev, prod, None

    def __call__(self, env, tool, mode="normal", settings=None):
        self.args = (env, tool, mode)
        return self

    def __enter__(self):
        return {"dev": self.dev, "prod": self.prod, "prod_reader_conn": None}

    def __exit__(self, *exc):
        return None


def args(*argv):
    return cli.build_parser().parse_args(["--env", "general", "--verify-only", *argv])


def seed_run(tmp_path, run_id=RUN1):
    folder = tmp_path / "state" / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "committed.json").write_text(json.dumps(committed(run_id)))


def test_verify_only_takes_the_state_lock(tmp_path):
    s = settings(tmp_path)
    seed_run(tmp_path)
    with exclusive_lock(s):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_promote(args("--run-id", RUN3), settings=s, session_opener=lambda *a, **k: pytest.fail("no connection"), out=out, err=err)
    assert code == 2 and "another sync command holds the lock" in err.getvalue()


def test_verify_only_handshake_failure_is_exit_two_with_a_report_and_no_dev_read(tmp_path, monkeypatch):
    seed_run(tmp_path)
    seen = {}

    def refuse(env, tool, mode="normal", **kw):
        seen.update(tool=tool, steps=kw.get("steps"), mode=mode)
        raise connect.HandshakeError("handshake H4 failed: prod_environment_mismatch",
                                     [{"step": "H1", "ok": True, "detail": "ok"}, {"step": "H4", "ok": False, "detail": "prod_environment_mismatch"}])

    monkeypatch.setattr(connect, "handshake", refuse)
    dev, prod = Conn(), Conn()
    opener = Opener(dev, prod)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_promote(args("--run-id", RUN3), settings=settings(tmp_path), session_opener=opener, out=out, err=err)
    assert code == 2 and "H4" in err.getvalue()
    assert opener.args == ("general", "V", "normal") and seen["tool"] == "V" and seen["steps"] == cli.VERIFY_STEPS
    assert dev.sql == [] and prod.sql == ["SET default_transaction_read_only = on"]
    doc = json.loads((tmp_path / "state" / "runs" / RUN3 / "report.json").read_text())
    assert [h["ok"] for h in doc["handshake"]] == [True, False] and doc["dry"] is True
    assert not (tmp_path / "state" / "runs" / RUN3 / "committed.json").exists()  # a verify-only folder is never an apply input
