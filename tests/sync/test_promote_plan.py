"""P2-T5: the plan (no database): diff of two digest maps, floor, files gate, cap, guards and their lifts, global sets, --only,
plan hash and report content (redaction). Digest maps are fabricated with TableDigest."""
import json

import pytest

from sync import report
from sync.promote import digest as dg
from sync.promote import files_gate, plan as P

SENTINEL = "SENTINEL-TEXT-d41d8cd9"
TABLES = ["images", "image_extras", "ocr_texts", "embeddings", "tags", "image_descriptions"]


def iid(n):
    return f"00000000-0000-0000-0000-{n:012d}"


def D(count, tag="a", size=10):
    return dg.TableDigest(count, tag, tag, size)


def base_digest(tag="a"):
    return {"images": D(1, tag), "ocr_texts": D(1, tag), "embeddings": D(1, tag), "tags": D(2, tag), "image_descriptions": D(1, tag)}


def image(n, filename=None, content_hash="h"):
    return dg.DevImage(iid(n), filename or f"f{n}.jpg", f"{content_hash}{n}", f"2026-01-01 00:{n:02d}:00")


def make(dev_n, prod_n=None, dev=None, prod=None, **kw):
    """Images 1..dev_n on dev, 1..prod_n on prod (default dev_n), equal digests unless overridden per id."""
    prod_n = dev_n if prod_n is None else prod_n
    dev_d = {iid(n): base_digest() for n in range(1, dev_n + 1)}
    prod_d = {iid(n): base_digest() for n in range(1, prod_n + 1)}
    dev_d.update(dev or {})
    prod_d.update(prod or {})
    return P.PlanInput(env="general", dev_images=[image(n) for n in range(1, dev_n + 1)], prod_ids={iid(n) for n in range(1, prod_n + 1)},
                       dev_digests=dev_d, prod_digests=prod_d, tables=TABLES, manifest_hash="ab" * 32, **kw)


def entry(p, name="images"):
    return next(e for e in p.section.entries if e.name == name)


def test_equal_maps_are_unchanged():
    p = P.build_plan(make(10))
    e = entry(p)
    assert (e.added.n, e.overwritten.n, e.unchanged.n, e.left_behind.n) == (0, 0, 10, 0)
    assert p.tripped == []
    assert [g.id for g in p.guards][:3] == ["zero_images", "dev_emptied", "mass_change"]
    assert {g.id for g in p.guards} >= {f"row_regression:{t}" for t in TABLES}
    assert {g.id for g in p.guards} >= {"coverage_regression:" + t for t in ("embeddings", "tags", "image_descriptions")}


def test_added_changed_unchanged_left_behind_and_exact_groups():
    inp = make(10, 11, dev={iid(3): {**base_digest(), "tags": D(2, "z")}, iid(4): {**base_digest("q")}})
    inp.prod_ids.add(iid(99))  # prod-only
    p = P.build_plan(inp)
    e = entry(p)
    assert e.overwritten.groups[iid(3)] == ["tags"]
    assert e.overwritten.groups[iid(4)] == ["core", "ocr", "clip", "desc", "tags"]  # every table's tokens changed
    assert e.unchanged.n == 8 and e.left_behind.ids == [iid(11), iid(99)]
    p2 = P.build_plan(make(10, 9))
    assert entry(p2).added.ids == [iid(10)] and entry(p2).overwritten.n == 0


def test_a_removed_child_row_is_a_change_of_that_group():
    inp = make(10, dev={iid(2): {k: v for k, v in base_digest().items() if k != "tags"}})
    assert entry(P.build_plan(inp)).overwritten.groups == {iid(2): ["tags"]}


def test_completeness_floor_only_for_new_images():
    inp = make(10, 8, dev={iid(9): {k: v for k, v in base_digest().items() if k != "embeddings"},
                            iid(10): {k: v for k, v in base_digest().items() if k != "ocr_texts"},
                            iid(2): {k: v for k, v in base_digest().items() if k != "embeddings"}})
    p = P.build_plan(inp)
    e = entry(p)
    assert e.skipped["missing_required_table"].ids == [iid(9), iid(10)] and e.added.n == 0
    assert iid(2) in e.overwritten.groups  # an existing image is not held back by the floor
    assert p.new == [] and list(p.changed) == [iid(2)]


def test_files_gate_classification_and_precedence():
    inp = make(10, 7, dev={iid(2): {**base_digest(), "tags": D(2, "z")}, iid(3): {**base_digest(), "tags": D(2, "z")}})
    inp.dev_images[7] = image(8, filename="renamed.jpg")
    files = files_gate.FilesReport("r", "general", "2026-11-13T11:00:00Z", False, "x", {
        iid(1): ("f1.jpg", "h1"), iid(2): ("f2.jpg", "h2"), iid(3): ("f3.jpg", "WRONG"), iid(8): ("f8.jpg", "h8"), iid(9): ("f9.jpg", "h9")},
        left_out={iid(10): "name_reserved_name"})
    inp.files = files
    e = entry(P.build_plan(inp))
    assert e.added.ids == [iid(9)]
    assert list(e.overwritten.groups) == [iid(2)]
    assert e.skipped["files_gate_miss"].ids == [iid(3), iid(8)]  # content hash differs; filename differs
    assert e.skipped["name_problem"].ids == [iid(10)]
    assert "missing_file" not in e.skipped


def test_limit_images_caps_in_created_order_and_drops_the_global_sets():
    inp = make(10, 4, limit_images=3, global_tables=["concepts"], dev_globals={"concepts": {1: ("h", None)}})
    p = P.build_plan(inp)
    e = entry(p)
    assert e.added.ids == [iid(5), iid(6), iid(7)] and e.skipped["capped_by_limit"].ids == [iid(8), iid(9), iid(10)]
    assert [x.name for x in p.section.entries] == ["images"] and p.section.flags == {"limit_images": 3}


def test_plan_without_image_tables_is_globals_only():
    inp = make(0)
    inp.tables = []
    inp.global_tables = ["concepts"]
    inp.dev_globals = {"concepts": {1: ("a", None), 2: ("b", None)}}
    inp.prod_globals = {"concepts": {2: ("x", None), 3: ("c", None)}}
    p = P.build_plan(inp)
    assert [x.name for x in p.section.entries] == ["concepts"] and p.section.guards == []
    c = entry(p, "concepts")
    assert (c.added.ids, c.overwritten.ids, c.unchanged.n, c.left_behind.ids) == ([1], [2], 0, [3])


def test_absorptions_insert_or_close():
    dev = {"a": ("h1", True), "r": ("h2", False), "same": ("h3", False), "closed_both": ("h4", True), "x": ("h5", None)}
    prod = {"a": ("h1o", False), "same": ("h3", False), "closed_both": ("h4", True), "prodonly": ("h6", False)}
    got = P._plan_global(dg.ABSORPTIONS, {k: v for k, v in dev.items() if k != "x"}, prod)
    assert got == {"added": ["r"], "overwritten": ["a"], "unchanged": 2, "left_behind": ["prodonly"]}
    # a row that differs on prod for another reason is not an overwrite for an absorption (the log is append-only)
    assert P._plan_global(dg.ABSORPTIONS, {"s": ("h", False)}, {"s": ("other", False)})["overwritten"] == []
    # concepts: a differing hash is an overwrite, nothing is ever pruned
    assert P._plan_global("concepts", {1: ("a", None)}, {1: ("b", None), 2: ("c", None)}) == {
        "added": [], "overwritten": [1], "unchanged": 0, "left_behind": [2]}


# --- guards ----------------------------------------------------------------------------------------------------------------------

def changed_inp(n_dev, n_changed, prod_n=None):
    return make(n_dev, prod_n, dev={iid(i): {**base_digest(), "tags": D(2, "z")} for i in range(1, n_changed + 1)})


def test_mass_change_boundary_and_exact_n():
    assert not [g for g in P.build_plan(changed_inp(40, 10)).tripped if g.id == "mass_change"]  # 25 % exactly: not above
    p = P.build_plan(changed_inp(40, 11))
    g = next(g for g in p.tripped if g.id == "mass_change")
    assert (g.value, g.limit, g.lifted_by) == (11, 10, "--allow-mass-change")
    ids = [g.id for g in p.tripped]
    assert P.effective_blocking(p, allow_mass_change=11, allow_shrink=["tags"]) == []
    assert P.effective_blocking(p, allow_mass_change=10, allow_shrink=["tags"]) == ["mass_change"]  # a wrong N lifts nothing
    assert P.effective_blocking(p, allow_mass_change=12, allow_shrink=["tags"]) == ["mass_change"]
    assert P.lift_problems(p, 12, ["tags"]) == ["allow_mass_change_wrong_n"]
    assert P.lift_problems(p, 11, ["tags"]) == []
    assert P.lift_problems(P.build_plan(make(10)), 5, []) == ["allow_mass_change_not_needed"]
    assert "coverage_regression:tags" not in ids  # tags rows still exist, only their digest changed


def test_mass_change_does_not_apply_to_a_first_seed():
    inp = changed_inp(40, 30)
    inp.first_seed = True
    p = P.build_plan(inp)
    assert not any(g.id == "mass_change" and g.tripped for g in p.guards)
    assert p.section.flags == {"first_seed": True}


def test_dev_emptied_and_zero_image_guards_have_no_lift():
    p = P.build_plan(make(2, 5))
    assert [g.id for g in p.tripped] == ["dev_emptied"]
    assert P.effective_blocking(p, 0, TABLES) == ["dev_emptied"]
    p = P.build_plan(make(3, 6))
    assert not p.tripped  # exactly half is allowed
    p = P.build_plan(make(0, 5))
    assert {g.id for g in p.tripped} == {"zero_images", "dev_emptied"}
    assert [g.id for g in P.build_plan(make(0, 0)).tripped] == ["zero_images"]


def test_row_regression_tolerance_is_max_100_rows_or_two_percent():
    many = {k: v for k, v in base_digest().items()}
    inp = make(20, prod={iid(i): {**many, "tags": D(30, "p")} for i in range(1, 21)}, dev={iid(i): {**many, "tags": D(1, "d")} for i in range(1, 21)})
    p = P.build_plan(inp)
    g = next(g for g in p.guards if g.id == "row_regression:tags")
    assert (g.value, g.limit, g.tripped, g.lifted_by) == (580, 100, True, "--allow-shrink")
    assert P.effective_blocking(p, 20, ["tags"]) == []
    inp = make(20, prod={iid(i): {**many, "tags": D(30, "p")} for i in range(1, 21)}, dev={iid(i): {**many, "tags": D(25, "d")} for i in range(1, 21)})
    assert not next(g for g in P.build_plan(inp).guards if g.id == "row_regression:tags").tripped  # 100 rows fewer: at the tolerance
    big = make(20, prod={iid(i): {**many, "tags": D(1000, "p")} for i in range(1, 21)}, dev={iid(i): {**many, "tags": D(980, "d")} for i in range(1, 21)})
    g = next(g for g in P.build_plan(big).guards if g.id == "row_regression:tags")
    assert (g.value, g.limit, g.tripped) == (400, 400, False)  # 2 % of 20000 is 400


def test_coverage_regression_one_percent_of_images():
    no_tags = {k: v for k, v in base_digest().items() if k != "tags"}
    inp = make(200, dev={iid(1): no_tags, iid(2): no_tags})  # 2 of 200 lose all tags: exactly 1 % (limit 2.0)
    g = next(g for g in P.build_plan(inp).guards if g.id == "coverage_regression:tags")
    assert (g.value, g.limit, g.tripped) == (2, 2.0, False)
    inp = make(200, dev={iid(1): no_tags, iid(2): no_tags, iid(3): no_tags})
    p = P.build_plan(inp)
    assert next(g for g in p.guards if g.id == "coverage_regression:tags").tripped
    assert P.effective_blocking(p, None, ["tags"]) == []
    assert P.effective_blocking(p, None, ["embeddings"]) == ["coverage_regression:tags"]
    # a gain elsewhere does not hide the loss: images that gained rows count for nothing
    inp = make(200, dev={iid(1): no_tags, iid(2): no_tags, iid(3): no_tags}, prod={iid(i): no_tags for i in range(100, 150)})
    assert next(g for g in P.build_plan(inp).guards if g.id == "coverage_regression:tags").value == 3


def test_guards_ignore_images_new_to_prod_and_use_changed_plus_unchanged_only():
    inp = make(10, 5, dev={iid(i): {k: v for k, v in base_digest().items() if k != "tags"} for i in range(6, 11)})
    assert not P.build_plan(inp).tripped  # the five new images have no tags, but prod has nothing to lose there


# --- --only ----------------------------------------------------------------------------------------------------------------------

def test_only_adds_parents_and_children():
    assert P.resolve_only(["tags"]) == (["images", "tags"], [])
    img, glb = P.resolve_only(["description_lemmas"])
    assert img == ["images", "image_descriptions", "description_lemmas"] and glb == []
    img, _ = P.resolve_only(["image_descriptions"])  # replacing the parent cascades into its children
    assert img == ["images", "image_descriptions", "description_lemmas", "image_description_embeddings", "image_description_feedback"]
    img, _ = P.resolve_only(["description_note_embeddings"])
    assert img == ["images", "description_notes", "description_note_embeddings"]
    assert P.resolve_only(["concept_images"]) == ([], ["concepts", "concept_image_sets", "concept_images"])
    assert P.resolve_only(None)[1][0] == "concepts" and len(P.resolve_only(None)[0]) == 16
    with pytest.raises(P.PlanError):
        P.resolve_only(["meme_saves"])


# --- hash and report -------------------------------------------------------------------------------------------------------------

def test_plan_hash_is_deterministic_and_sees_groups_ids_flags_and_manifest():
    inp = changed_inp(10, 2)
    h = P.build_plan(inp).section.plan_hash()
    assert h == P.build_plan(changed_inp(10, 2)).section.plan_hash()
    inp2 = changed_inp(10, 2)
    inp2.dev_digests[iid(1)] = {**base_digest(), "tags": D(2, "z"), "embeddings": D(1, "z")}  # one more group on one image
    assert P.build_plan(inp2).section.plan_hash() != h
    assert P.build_plan(changed_inp(10, 3)).section.plan_hash() != h
    inp3 = changed_inp(10, 2)
    inp3.manifest_hash = "cd" * 32
    assert P.build_plan(inp3).section.plan_hash() != h
    inp4 = changed_inp(10, 2)
    inp4.first_seed = True
    assert P.build_plan(inp4).section.plan_hash() != h
    inp5 = changed_inp(10, 2)
    inp5.batch_size = 7  # a measurement of the apply, not the plan
    assert P.build_plan(inp5).section.plan_hash() == h


def test_report_content_completeness_and_redaction(tmp_path):
    inp = changed_inp(10, 3, 9)
    inp.dev_images[0] = dg.DevImage(iid(1), SENTINEL + ".jpg", SENTINEL, "2026-01-01 00:01:00")
    p = P.build_plan(inp)
    doc = report.Document(env="general", run_id="20261113T120000Z-00000001", tool=P.TOOL, dry=True, sections=[p.section],
                          handshake=[{"step": "H1", "ok": True, "detail": "ok"}], preconditions=[{"id": "files_report", "ok": True, "detail": "absent"}])
    doc.apply_command = report.apply_command(P.TOOL, "general", doc.plan_hash, ["--first-seed"])
    assert doc.apply_command == f"python -m sync.promote --env general --first-seed --apply --plan-hash {doc.plan_hash}"
    out = report.write_report(doc, tmp_path, top_n=2)
    js = json.loads((tmp_path / "report.json").read_text())
    sec = js["sections"][0]
    assert sec["name"] == "data" and sec["entries"][0]["name"] == "images" and sec["plan_hash"] == p.section.plan_hash()
    e = sec["entries"][0]
    assert e["added"]["ids"] == [iid(10)] and len(e["overwritten"]["groups"]) == 3 and e["overwritten"]["group_counts"] == {"tags": 3}
    assert {g["id"] for g in sec["guards"]} >= {"zero_images", "dev_emptied", "mass_change", "row_regression:tags"}
    assert set(sec["table_rows"]) == set(TABLES) and sec["table_rows"]["tags"]["overwritten_rows"] == 6
    assert all(g["lifted_by"] in (None, "--allow-mass-change", "--allow-shrink") for g in sec["guards"])
    md = (tmp_path / "report.md").read_text()
    assert "rows per table" in md and "left behind on prod (removals deferred)" in md and "tags 3" in md
    everything = (tmp_path / "report.json").read_text() + md + out["summary"]
    assert SENTINEL not in everything  # filenames and hashes never reach the report


def test_left_behind_is_informational_and_listed_in_full_in_json(tmp_path):
    inp = make(10, 12)
    p = P.build_plan(inp)
    assert not p.tripped and entry(p).left_behind.ids == [iid(11), iid(12)]
    assert p.section.totals()["left_behind"]["count"] == 2
