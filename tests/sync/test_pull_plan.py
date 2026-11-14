"""P4-T1: the pure plan of `sync.pull` (no database): table categories, the ratings partition and ledger, guards, lifts, plan hash,
id-list truncation, the text-cap column list against the models, the SQL builders and the read-only discipline of the snapshot reader."""
import ast
import json
import re
from pathlib import Path

import pytest

from sync import report
from sync.pull import apply as apply_mod
from sync.pull import plan as pl
from sync.pull import snapshot as snap

U = [f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 40)]


def read(rows: dict, count=None, **kw) -> pl.TableRead:
    return pl.TableRead(len(rows) if count is None else count, {k: (h, 10) for k, h in rows.items()}, **kw)


def zero_inp(**over) -> pl.PlanInput:
    tables = {t: pl.TableRead(0) for t in pl.EXPECTED_TABLES}
    base = dict(env="general", mode="skip", prod=dict(tables), dev=dict(tables))
    base.update(over)
    return pl.PlanInput(**base)


def inp_with(prod_counts: dict, dev_counts: dict | None = None, **over) -> pl.PlanInput:
    prod = {t: pl.TableRead(prod_counts.get(t, 0)) for t in pl.EXPECTED_TABLES}
    dev = {t: pl.TableRead((dev_counts or {}).get(t, 0)) for t in pl.EXPECTED_TABLES}
    return pl.PlanInput(env="general", mode=over.pop("mode", "skip"), prod=prod, dev=dev, **over)


def previous(**counts) -> dict:
    return {"tables": {t: {"prod": n, "replaced": n} for t, n in counts.items()}}


# --- registry, constants, text cap ------------------------------------------------------------------------------------------------

def test_registry_and_constants_agree():
    assert pl.check_registry() == []
    assert pl.mirror_tables().index("search_history") < pl.mirror_tables().index("search_history_tags")
    assert pl.RATINGS not in pl.mirror_tables() and len(pl.mirror_tables()) == 5


def test_every_unbounded_text_column_of_the_six_tables_is_capped():
    import sqlalchemy as sa

    from Storage.models import Base

    unbounded = {}
    for t in pl.EXPECTED_TABLES:
        for c in Base.metadata.tables[t].columns:
            if isinstance(c.type, sa.Text) or (isinstance(c.type, sa.String) and c.type.length is None):
                unbounded.setdefault(t, set()).add(c.name)
    assert unbounded == {t: set(v) for t, v in pl.TEXT_COLUMNS.items()}


def test_plan_module_is_pure():
    allowed = set(__import__("sys").stdlib_module_names) | {"sync"}
    tree = ast.parse(Path(pl.__file__).read_text(encoding="utf-8"))
    roots = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    roots |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert roots <= allowed


# --- table categories ---------------------------------------------------------------------------------------------------------------

def test_table_categories_added_overwritten_discarded_unchanged():
    prod = read({U[0]: "a", U[1]: "b2", U[2]: "c"})
    dev = read({U[1]: "b", U[2]: "c", U[3]: "d"})
    e = pl.plan_table("meme_saves", prod, dev, 200_000)
    assert e.added.ids == [U[0]] and e.overwritten.ids == sorted([U[1], U[3]]) and e.unchanged.n == 1
    assert e.counts["differing"] == 1 and e.counts["dev_only_discarded"] == 1 and e.counts["prod"] == 3 and e.counts["dev"] == 3
    assert e.pulled and e.kind == "pulled"


def test_unread_table_has_counts_only_and_truncate_flag():
    e = pl.plan_table("meme_impressions", pl.TableRead(10**7, {}, False), read({1: "a"}), 200_000)
    assert e.added.n == 0 and e.counts["not_read"] == 1
    big = pl.TableRead(250_000, {i: ("h", 1) for i in range(250_000)})
    assert pl.plan_table("meme_saves", read({}), big, 200_000).counts["truncate"] == 1
    assert pl.plan_table("search_history", read({}), big, 200_000).counts["truncate"] == 0  # an FK parent is never truncated


def test_large_unchanged_list_is_count_only():
    rows = {i: "h" for i in range(1500)}
    e = pl.plan_table("meme_impressions", read(rows), read(rows), 200_000)
    assert e.unchanged.n == 1500 and e.unchanged.ids == []


# --- ratings ------------------------------------------------------------------------------------------------------------------------

def rr(i, h="h"):
    return pl.RatingRow(U[i], h, 5)


def test_ratings_partition_every_class():
    img = {n: U[20 + n] for n in range(8)}
    prod = {(img[0], "q"): rr(0), (img[1], "q"): rr(1, "new"), (img[2], "q"): rr(2), (img[3], "q"): rr(3)}
    dev = {(img[1], "q"): rr(11, "old"), (img[2], "q"): rr(2), (img[4], "q"): rr(4), (img[5], "q"): rr(5), (img[6], "q"): rr(6)}
    ledger = {pl.key_hash(img[4], "q"), pl.key_hash(img[5], "q")}
    prod_images = {img[0], img[1], img[2], img[4], img[6]}  # img[5] retired on prod; img[3] exists on prod only
    dev_images = {img[0], img[1], img[2], img[4], img[5], img[6]}  # img[3] unknown on dev
    part = pl.partition_ratings(prod, dev, ledger, prod_images, dev_images)
    assert part.insert == [(img[0], "q")] and part.update == [(img[1], "q")] and part.unchanged == [(img[2], "q")]
    assert part.skipped_unknown == [(img[3], "q")]
    assert part.delete == [(img[4], "q")]  # received earlier, prod no longer has it
    assert part.kept_retired == [(img[5], "q")]  # image absent from prod's images: kept even though the ledger has it
    assert part.kept_dev_made == [(img[6], "q")]  # never received from prod
    assert part.archived == part.update + part.delete


def test_dev_rating_survives_an_empty_prod_table_and_an_empty_ledger():
    dev = {(U[1], "q"): rr(1), (U[2], "q"): rr(2)}
    part = pl.partition_ratings({}, dev, set(), {U[1], U[2]}, {U[1], U[2]})
    assert part.delete == [] and len(part.kept_dev_made) == 2


def test_ratings_skip_mode_does_not_touch_the_table_and_mirror_lists_the_partition():
    inp = zero_inp()
    inp.prod[pl.RATINGS] = pl.TableRead(38)
    inp.dev[pl.RATINGS] = pl.TableRead(4)
    e = pl.plan_ratings(inp)[0]
    assert e.skipped["ratings_mode_skip"].n == 38 and e.added.n == 0 and e.overwritten.n == 0 and pl.plan_ratings(inp)[1] is None
    mirror = zero_inp(mode="mirror", prod_ratings={(U[1], "q"): rr(1)}, dev_ratings={(U[2], "q"): rr(2)}, prod_images=frozenset({U[1], U[2]}),
                      dev_images=frozenset({U[1], U[2]}), ledger=frozenset({pl.key_hash(U[2], "q")}))
    e, part = pl.plan_ratings(mirror)
    assert e.added.ids == [U[1]] and e.overwritten.ids == [U[2]] and e.counts["deleted"] == 1 and e.counts["archived"] == 1


def test_fingerprint_helpers():
    assert pl.key_line("i", "q") == "i|q" and len(pl.key_hash("i", "q")) == 64
    assert pl.keys_sha256(["b|1", "a|2"]) == pl.keys_sha256(["a|2", "b|1"]) != pl.keys_sha256(["a|2"])


# --- guards -------------------------------------------------------------------------------------------------------------------------

def guard(plan, gid):
    return next(g for g in plan.section.guards if g.id == gid)


def test_empty_source_guard_and_its_lift():
    p = pl.build_plan(inp_with({}, {"meme_saves": 3}))
    assert guard(p, "empty_source").tripped and pl.effective_blocking(p) == ["empty_source"]
    assert pl.effective_blocking(p, allow_empty_source=True) == []
    assert not guard(pl.build_plan(inp_with({"meme_saves": 1}, {"meme_saves": 3})), "empty_source").tripped
    assert not guard(pl.build_plan(inp_with({}, {})), "empty_source").tripped  # both empty: nothing to wipe
    # dev ratings do not count in skip mode: the table is not touched
    assert not guard(pl.build_plan(inp_with({}, {pl.RATINGS: 9})), "empty_source").tripped
    assert guard(pl.build_plan(inp_with({}, {pl.RATINGS: 9}, mode="mirror")), "empty_source").tripped


def test_shrink_guards_append_only_any_drop_lossy_over_half_first_pull_none():
    prev = previous(meme_impressions=100, meme_saves=10, search_history=100, search_history_tags=100, search_not_found=10, meme_text_ratings=10)
    p = pl.build_plan(inp_with({"meme_impressions": 99, "meme_saves": 10, "search_history": 60, "search_history_tags": 49,
                                "search_not_found": 10, pl.RATINGS: 2}, previous=prev, mode="mirror"))
    tripped = {g.id for g in p.tripped}
    assert tripped == {"shrink:meme_impressions", "shrink:search_history_tags", "shrink:meme_text_ratings"}
    assert pl.effective_blocking(p, allow_shrink=["meme_impressions", "search_history_tags"]) == ["shrink:meme_text_ratings"]
    assert not any(g.tripped for g in pl.build_plan(inp_with({"meme_impressions": 0, "meme_saves": 1}, previous=None)).section.guards
                   if g.id.startswith("shrink"))
    # ratings in skip mode: the table is not written, so a drop of prod's ratings is no refusal
    skip = pl.build_plan(inp_with({pl.RATINGS: 1}, previous=previous(meme_text_ratings=10), mode="skip"))
    assert not guard(skip, "shrink:meme_text_ratings").tripped


def test_growth_needs_both_the_factor_and_the_minimum_absolute_growth():
    lim = pl.Limits(max_growth_factor=5, min_growth_rows=100_000)
    prev = previous(meme_saves=10, meme_impressions=100_000)
    small = pl.build_plan(inp_with({"meme_saves": 1000}, previous=prev, limits=lim))  # 100x but only 990 rows
    assert not guard(small, "growth:meme_saves").tripped
    big = pl.build_plan(inp_with({"meme_impressions": 700_000, "meme_saves": 10}, previous=prev, limits=lim))
    assert guard(big, "growth:meme_impressions").tripped and pl.effective_blocking(big, allow_growth=True) == []
    first = pl.build_plan(inp_with({"meme_impressions": 10**6}, previous=None, limits=lim))
    assert not guard(first, "growth:meme_impressions").tripped


def test_row_ceiling_guard_is_lifted_by_allow_growth():
    p = pl.build_plan(inp_with({"meme_saves": 11}, limits=pl.Limits(max_rows_per_pull=10)))
    assert guard(p, "row_ceiling:meme_saves").tripped and pl.effective_blocking(p) == ["row_ceiling:meme_saves"]
    assert pl.effective_blocking(p, allow_growth=True) == []


def test_lift_problems_name_useless_flags():
    p = pl.build_plan(inp_with({"meme_saves": 1}))
    assert pl.lift_problems(p, True, ["meme_saves"], True) == ["allow_empty_source_not_needed", "allow_growth_not_needed",
                                                                 "allow_shrink_not_needed:meme_saves"]


# --- plan hash and report -----------------------------------------------------------------------------------------------------------

def test_plan_hash_covers_the_structure_only_not_prod_rows():
    """Owner decision A: tables in scope, ratings mode, flags and guard states; never prod ids, row digests or counts."""
    def build(h="b2", **kw):
        inp = zero_inp(**kw)
        inp.prod["meme_saves"] = read({U[0]: "a", U[1]: h})
        inp.dev["meme_saves"] = read({U[1]: "b"})
        return pl.build_plan(inp)

    a, b = build(), build()
    assert a.section.plan_hash() == b.section.plan_hash()
    assert build(h="b3").section.plan_hash() == a.section.plan_hash()  # a prod row whose content changed: same plan
    c = build()
    c.section.estimate = {"duration_s": 99.0, "wire_bytes": 1}
    assert c.section.plan_hash() == a.section.plan_hash()
    # prod rows come and go (readers are active every minute): the hash does not move
    inp = zero_inp()
    inp.prod["meme_saves"] = read({U[0]: "a"})
    assert pl.build_plan(inp).section.plan_hash() == a.section.plan_hash()
    inp = zero_inp()
    inp.prod["meme_saves"] = read({U[0]: "a", U[1]: "x", "ab12": "y"})
    inp.prod["meme_impressions"] = read({i: "h" for i in range(1, 20)})
    assert pl.build_plan(inp).section.plan_hash() == a.section.plan_hash()
    # the bug-report lists are not hashed either
    c.section.lists = {"to_fetch": [U[0]]}
    c.section.checks = [{"id": "bug_reports", "status": "attention", "severity": "info", "detail": "anomalies=1"}]
    assert c.section.plan_hash() == a.section.plan_hash()


def test_plan_hash_changes_with_a_flag_a_mode_or_a_guard_state():
    def build(**kw):
        return pl.build_plan(zero_inp(**kw)).section

    base = build().plan_hash()
    assert build(skip_files=True).plan_hash() != base
    assert build(mode="mirror").plan_hash() != base
    flagged = build()
    flagged.flags["bug_reports"] = "off"  # the cli sets it from the transport and --skip-files
    assert flagged.plan_hash() != base
    cut = build()
    cut.flags["text_cap"] = 7
    assert cut.plan_hash() != base
    # a guard that trips (here: the row ceiling) is a different plan; the guard value alone is not
    tripped = pl.build_plan(inp_with({"meme_saves": 11}, limits=pl.Limits(max_rows_per_pull=10))).section
    quiet = pl.build_plan(inp_with({"meme_saves": 11}, limits=pl.Limits(max_rows_per_pull=50))).section
    assert tripped.plan_hash() != quiet.plan_hash()
    assert pl.build_plan(inp_with({"meme_saves": 11})).section.plan_hash() == pl.build_plan(inp_with({"meme_saves": 12})).section.plan_hash()
    # a table out of scope is a different plan
    fewer = build()
    fewer.entries = fewer.entries[:-1]
    assert fewer.plan_hash() != base


def test_apply_command_carries_the_lifts_and_report_ids_are_cut_above_max_ids():
    inp = zero_inp()
    inp.prod["meme_impressions"] = read({i: "h" for i in range(1, 30)})
    p = pl.build_plan(inp)
    doc = report.Document(env="general", run_id="20261113T120000Z-00000001", tool="sync.pull", sections=[p.section])
    doc.apply_command = report.apply_command("sync.pull", "general", doc.plan_hash, ["--allow-shrink", "meme_saves", "--allow-growth"])
    assert doc.apply_command.startswith("python -m sync.pull --env general --allow-shrink meme_saves --allow-growth --apply --plan-hash ")
    entry = next(e for e in report.document_json(doc, max_ids=10)["sections"][0]["entries"] if e["name"] == "meme_impressions")
    assert entry["added"]["ids"] is None and entry["added"]["ids_range"] == {"count": 29, "min": 1, "max": 29}
    assert next(e for e in report.document_json(doc, max_ids=100)["sections"][0]["entries"] if e["name"] == "meme_impressions")["added"]["ids"][0] == 1
    assert "counts" in report.document_json(doc)["sections"][0]["entries"][0]


# --- SQL builders and the read-only discipline --------------------------------------------------------------------------------------

def test_row_expr_cuts_free_text_and_uses_alphabetical_columns():
    expr = snap.row_expr("search_history", 123)
    assert 'left("query", 123)' in expr and 'left("situation", 123)' in expr and 'left("kind"' not in expr
    assert expr.index('"client"') < expr.index('"degraded"') < expr.index('"id"')
    assert 'left("query_text", 9)' in snap.select_list(pl.RATINGS, 9) and "left(" not in snap.select_list("meme_saves", 9)
    assert snap.key_expr("meme_impressions") == '"id"' and snap.key_expr("meme_saves") == '"id"::text'


def test_identifiers_are_checked_and_only_class_p_tables_are_readable():
    with pytest.raises(snap.SnapshotError):
        snap.table_ident("images")
    with pytest.raises(snap.SnapshotError):
        snap.ident('x"; drop table y; --')


class Recorder:
    def __init__(self, answers=()):
        self.sql, self.answers = [], list(answers)

    def query(self, sql, args=None):
        self.sql.append(sql)
        return self.answers.pop(0) if self.answers else []

    def scalar(self, sql, args=None):
        rows = self.query(sql, args)
        return rows[0][0] if rows else 0


def test_snapshot_reader_issues_selects_only():
    conn = Recorder([[(3,)], [(0,)], [("a", "h", 1)]])
    t = snap.read_table(conn, "search_history", 2000, 100, with_truncated=True)
    assert t.count == 3 and t.rows == {"a": ("h", 1)}
    conn.sql += [snap.hash_sql("meme_text_ratings", 5), snap.digest_sql("meme_saves", 5), snap.count_sql("meme_saves")]
    snap.ratings_fingerprint(Recorder([[(0, None)], []]), "skip")
    assert all(re.match(r"^\s*select ", s, re.I) for s in conn.sql), conn.sql
    over = snap.read_table(Recorder([[(10**7,)]]), "meme_saves", 2000, 100)
    assert not over.read and over.count == 10**7 and over.rows == {}


def test_fingerprint_is_the_sorted_key_hash_and_never_carries_text():
    conn = Recorder([[(2, "2026-11-13T10:00:00.000000")], [(U[2], "bb"), (U[1], "aa")]])
    fp = snap.ratings_fingerprint(conn, "skip")
    assert fp == {"mode": "skip", "prod_count": 2, "max_updated_at": "2026-11-13T10:00:00.000000",
                  "keys_sha256": pl.keys_sha256([f"{U[1]}|aa", f"{U[2]}|bb"])}


def test_apply_sql_shapes():
    assert apply_mod.lock_sql(["search_history_tags", "search_history"]) == 'LOCK TABLE "search_history_tags", "search_history" IN SHARE ROW EXCLUSIVE MODE'
    assert apply_mod.delete_order() == ["search_history_tags", "search_not_found", "search_history", "meme_saves", "meme_impressions"][:0] or \
        apply_mod.delete_order()[-1] != "search_history_tags"
    order = apply_mod.delete_order()
    assert order.index("search_history_tags") < order.index("search_history")  # children first
    assert apply_mod.copy_in_sql("meme_saves").startswith('COPY "meme_saves" (') and apply_mod.copy_in_sql("meme_saves").endswith("(FORMAT binary)")
    assert "left(" in apply_mod.copy_out_sql("search_history", 7) and apply_mod.copy_out_sql("meme_saves", 7, " WHERE x").endswith("TO STDOUT (FORMAT binary)")
    assert apply_mod.uuid_array([U[1]]) == "'{" + U[1] + "}'::uuid[]"
    with pytest.raises(apply_mod.ApplyError):
        apply_mod.uuid_array(["1'; drop table x; --"])


def test_the_apply_module_never_names_a_prod_write():
    """Static check: the only statements issued through the `prod` connection in apply.py are the COPY OUT streams and the digest reads."""
    src = Path(apply_mod.__file__).read_text(encoding="utf-8")
    prod_calls = re.findall(r"prod\.(\w+)\(", src)
    assert set(prod_calls) <= {"copy_out"}
    assert not re.search(r"prod\.query\(", src)
