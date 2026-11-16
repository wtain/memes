"""The dry-run report (sync/report.py; interfaces.md section k). No database, no network."""
import copy
import json
from pathlib import Path

import pytest

from sync import report as R

RUN_ID = "20261113T101500Z-0a1b2c3d"
SENTINEL = "SENTINEL secret \"text\" `x` ünï ключ"


def images_entry(**kw):
    base = dict(
        name="images", kind="images",
        added=R.IdSet(ids=[30, 10, 20], bytes=300),
        overwritten=R.Overwritten(groups={7: ["ocr", "core"], 5: ["tags"], 9: ["clip", "ocr"]}, bytes=90),
        unchanged=R.IdSet(count=1200),
        skipped={"missing_file": R.IdSet(ids=[4, 3]), "name_problem": R.IdSet(ids=[8])},
        left_behind=R.LeftBehind(ids=[99, 98]),
    )
    base.update(kw)
    return R.Entry(**base)


def data_section(**kw):
    base = dict(
        name="data", tool="sync.promote", entries=[images_entry(), R.Entry(name="tags", added=R.IdSet(ids=[1, 2]))],
        guards=[R.Guard("mass_change", False, 0.1, 0.25, "--allow-mass-change"), R.Guard("zero_images", False, 3, 1)],
        flags={"first_seed": False, "only": "images"}, manifest_hash="ab" * 16,
        estimate={"duration_s": 12.5, "wire_bytes": 1000},
    )
    base.update(kw)
    return R.Section(**base)


def files_section():
    return R.Section(
        name="files", tool="sync.files promote",
        entries=[R.Entry(name="meme_files", kind="files", added=R.IdSet(ids=[10, 20], bytes=5000),
                         overwritten=R.Overwritten(ids=[7], size_changed=[7], hash_changed=[7]),
                         left_behind=R.LeftBehind(names=["orphan one.jpg", "b.png"]))],
        guards=[R.Guard("batch_mb", False, 1, 64)],
    )


def doc(sections=None, dry=True, **kw):
    sections = sections or [data_section()]
    d = R.Document(env="general", run_id=RUN_ID, tool="sync.promote", dry=dry, sections=sections,
                   handshake=[{"step": "H1", "ok": True, "detail": "stage=serving"}],
                   preconditions=[{"id": "C1", "ok": True, "detail": ""}], **kw)
    d.apply_command = R.apply_command("sync.promote", "general", d.plan_hash)
    return d


# --- plan hash ---------------------------------------------------------------------------------------------------------------

def test_hash_is_deterministic_under_id_and_entry_reordering():
    a = data_section()
    b = data_section(entries=[
        R.Entry(name="tags", added=R.IdSet(ids=[2, 1])),
        images_entry(added=R.IdSet(ids=[20, 30, 10], bytes=300),
                     overwritten=R.Overwritten(groups={9: ["ocr", "clip"], 5: ["tags"], 7: ["core", "ocr"]}, bytes=90),
                     skipped={"name_problem": R.IdSet(ids=[8]), "missing_file": R.IdSet(ids=[3, 4])},
                     left_behind=R.LeftBehind(ids=[98, 99])),
    ], guards=[R.Guard("zero_images", False, 3, 1), R.Guard("mass_change", False, 0.1, 0.25, "--allow-mass-change")])
    assert a.plan_hash() == b.plan_hash()


def test_hash_ignores_volatile_fields():
    base = data_section()
    changed = data_section(estimate={"duration_s": 999, "wire_bytes": 1})
    changed.entries[0].added.bytes = 1
    changed.entries[0].overwritten.bytes = 5
    assert base.plan_hash() == changed.plan_hash()
    d1, d2 = doc(), doc(generated_at="2020-01-01T00:00:00Z")
    d2.run_id = "20300101T000000Z-ffffffff"
    assert d1.plan_hash == d2.plan_hash


def test_hash_changes_when_an_overwritten_group_changes():
    other = data_section()
    other.entries[0].overwritten.groups[5] = ["tags", "note"]
    assert other.plan_hash() != data_section().plan_hash()


@pytest.mark.parametrize("mutate", [
    lambda s: s.entries[0].added.ids.append(31),
    lambda s: s.entries[0].skipped.pop("name_problem"),
    lambda s: s.entries[0].left_behind.ids.append(1),
    lambda s: s.guards[0].__setattr__("tripped", True),
    lambda s: s.flags.__setitem__("first_seed", True),
    lambda s: setattr(s, "manifest_hash", "cd" * 16),
    lambda s: setattr(s.entries[0].unchanged, "count", 1),
])
def test_hash_changes_with_plan_content(mutate):
    s = data_section()
    mutate(s)
    assert s.plan_hash() != data_section().plan_hash()


def test_combined_hash_is_hash_of_ordered_section_hashes():
    s1, s2 = files_section(), data_section()
    ab = R.combined_plan_hash([s1, s2])
    ba = R.combined_plan_hash([s2, s1])
    assert ab != ba
    assert ab == R._sha([s1.plan_hash(), s2.plan_hash()])
    assert R.combined_plan_hash([s2]) == s2.plan_hash()
    assert R.combined_plan_hash([]) is None


def test_apply_command_exact():
    assert R.apply_command("sync.run", "general", "3fa9" + "0" * 8) == "python -m sync.run --env general --apply --plan-hash 3fa9" + "0" * 8
    cmd = R.apply_command("sync.promote", "it", "ab" * 8, ["--only", "images,tags", "--first-seed"])
    assert cmd == f"python -m sync.promote --env it --only images,tags --first-seed --apply --plan-hash {'ab' * 8}"
    assert R.apply_command("sync.files promote", "metal", "ab" * 8).startswith("python -m sync.files promote --env metal")
    assert R.apply_command("sync.promote", "it", None) is None
    assert R.apply_command("sync.verify", "it", "ab" * 8) is None
    with pytest.raises(R.ReportError):
        R.apply_command("sync.promote", "it", "ab" * 8, ["; rm -rf /"])
    with pytest.raises(R.ReportError):
        R.apply_command("sync.promote", "prodx", "ab" * 8)


# --- json ----------------------------------------------------------------------------------------------------------------------

def test_report_json_is_complete():
    d = json.loads(R.render_json(doc()))
    assert d["env"] == "general" and d["dry"] is True and d["plan_hash"] == d["sections"][0]["plan_hash"]
    assert d["apply_command"].endswith("--apply --plan-hash " + d["plan_hash"])
    assert "result" not in d
    e = d["sections"][0]["entries"][0]
    assert e["added"]["ids"] == [10, 20, 30] and e["added"]["bytes"] == 300
    assert e["overwritten"]["group_counts"] == {"core": 1, "ocr": 2, "clip": 1, "tags": 1}
    assert e["overwritten"]["groups"][0] == {"id": 5, "groups": ["tags"]}
    assert e["skipped_with_reason"]["missing_file"]["ids"] == [3, 4]
    assert e["left_behind"]["ids"] == [98, 99]
    assert e["unchanged"]["count"] == 1200 and "ids" not in e["unchanged"]
    assert [g["id"] for g in d["sections"][0]["guards"]] == ["mass_change", "zero_images"]
    assert d["sections"][0]["totals"]["added"] == {"count": 5, "bytes": 300}


def test_unchanged_ids_only_for_small_sections():
    small = R.Section(name="data", tool="sync.promote", entries=[R.Entry(name="t", unchanged=R.IdSet(ids=[1, 2, 3]))])
    big = R.Section(name="data", tool="sync.promote", entries=[R.Entry(name="t", unchanged=R.IdSet(ids=list(range(1000))))])
    assert json.loads(R.render_json(doc([small])))["sections"][0]["entries"][0]["unchanged"]["ids"] == [1, 2, 3]
    assert "ids" not in json.loads(R.render_json(doc([big])))["sections"][0]["entries"][0]["unchanged"]


def test_max_ids_cut_applies_to_large_pulled_tables_only():
    pulled = R.Entry(name="meme_impressions", kind="pulled", pulled=True, added=R.IdSet(ids=list(range(1, 501))))
    plain = R.Entry(name="tags", added=R.IdSet(ids=list(range(1, 501))))
    sec = R.Section(name="pull", tool="sync.pull", entries=[pulled, plain])
    d = json.loads(R.render_json(R.Document(env="it", run_id=RUN_ID, tool="sync.pull", sections=[sec]), max_ids=100))
    p, q = d["sections"][0]["entries"]
    assert p["added"]["ids"] is None and p["added"]["ids_range"] == {"count": 500, "min": 1, "max": 500}
    assert p["added"]["count"] == 500
    assert len(q["added"]["ids"]) == 500
    # at the limit nothing is cut; the hash always covers the full list
    d2 = json.loads(R.render_json(R.Document(env="it", run_id=RUN_ID, tool="sync.pull", sections=[sec]), max_ids=500))
    assert len(d2["sections"][0]["entries"][0]["added"]["ids"]) == 500
    assert d["sections"][0]["plan_hash"] == d2["sections"][0]["plan_hash"]


def test_pull_lists_and_verify_checks():
    sec = R.Section(name="pull", tool="sync.pull", lists={"to_fetch": ["ab12", "cd34"], "to_delete_on_prod_after_verified_pull": ["ab12"]},
                    checks=[{"id": "D1", "status": "warn", "severity": "warn", "detail": "drift=3"}])
    d = json.loads(R.render_json(R.Document(env="it", run_id=RUN_ID, tool="sync.pull", sections=[sec])))
    assert d["sections"][0]["lists"]["to_fetch"] == {"count": 2, "ids": ["ab12", "cd34"]}
    assert d["sections"][0]["checks"][0]["detail"] == "drift=3"


# --- markdown / summary --------------------------------------------------------------------------------------------------------

def test_markdown_caps_lists_and_titles_left_behind():
    sec = R.Section(name="data", tool="sync.promote", entries=[
        R.Entry(name="images", kind="images", added=R.IdSet(ids=list(range(1, 121))),
                overwritten=R.Overwritten(groups={i: ["core"] for i in range(1, 11)}),
                left_behind=R.LeftBehind(ids=[500, 501]))])
    md = R.render_markdown(doc([sec]), top_n=5)
    assert "1, 2, 3, 4, 5 ... (115 more in report.json)" in md
    assert "6, 7" not in md.split("added")[1].split("overwritten")[0]
    assert "(5 more in report.json)" in md  # overwritten groups list
    assert R.LEFT_BEHIND_TITLE in md
    assert "500, 501" in md and "more in report.json" in md
    full = R.render_markdown(doc([sec]), top_n=500)
    assert "more in report.json)" not in full.split("overwritten")[0]
    assert md.count("Apply command") == 1 and "--plan-hash" in md


def test_left_behind_file_names_in_markdown():
    md = R.render_markdown(doc([files_section()]))
    assert "`orphan one.jpg`" in md and "`b.png`" in md


def test_summary_has_counts_guards_hash_and_command():
    s = data_section(guards=[R.Guard("mass_change", True, 0.4, 0.25, "--allow-mass-change")])
    d = doc([s])
    out = R.render_summary(d)
    assert "[data]" in out and "added 5 (300 B)" in out
    assert "guard mass_change would trip" in out and "--allow-mass-change" in out
    assert f"plan hash: {d.plan_hash}" in out and "apply: python -m sync.promote" in out
    assert "DRY RUN" in out


# --- writing ------------------------------------------------------------------------------------------------------------------

def test_write_report_dry_marker_and_files(tmp_path: Path):
    out = R.write_report(doc(), tmp_path / "runs" / RUN_ID, json_copy=tmp_path / "copy.json")
    run = tmp_path / "runs" / RUN_ID
    assert (run / "dry").read_text() == "" and (run / "report.json").exists() and (run / "report.md").exists()
    assert (tmp_path / "copy.json").read_text() == (run / "report.json").read_text()
    assert not list(run.glob("*.tmp"))
    assert "plan hash" in out["summary"]


def test_apply_report_refuses_a_dry_folder(tmp_path: Path):
    run = tmp_path / RUN_ID
    R.write_report(doc(), run)
    with pytest.raises(R.ReportError):
        R.write_report(doc(dry=False), run)


def test_apply_folder_has_no_marker_and_injectable_writer(tmp_path: Path):
    written = {}
    R.write_report(doc(dry=False), tmp_path / "r", write=lambda p, t: written.__setitem__(p.name, t))
    assert set(written) == {"report.json", "report.md"}


def test_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(R.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        R.atomic_write_text(tmp_path / "x.json", "{}")
    assert list(tmp_path.iterdir()) == []


def test_precondition_failure_report_has_only_handshake(tmp_path: Path):
    d = R.Document(env="it", run_id=RUN_ID, tool="sync.promote",
                   handshake=[{"step": "H3", "ok": False, "detail": "environment=mismatch"}])
    j = json.loads(R.render_json(d))
    assert j["sections"] == [] and j["plan_hash"] is None and j["apply_command"] is None
    assert "FAILED: H3" in R.render_summary(d)


# --- result block ----------------------------------------------------------------------------------------------------------------

def test_result_block_in_apply_report_and_plan_comparison():
    sec = data_section()
    res = {"data": {"images": R.ResultEntry(added=3, overwritten=3, skipped={"missing_file": [3, 4], "name_problem": [8]}),
                    "tags": R.ResultEntry(added=2)}}
    # plan: images added 3 + overwritten 3 + skipped(not in plan counts) -> result adds skipped ids on top
    assert R.plan_vs_result(sec, {"images": res["data"]["images"], "tags": res["data"]["tags"]}) == ["images:count"]
    ok = R.ResultEntry(added=3, overwritten=3)
    assert R.plan_vs_result(sec, {"images": ok, "tags": R.ResultEntry(added=2)}) == []
    gate_miss = R.ResultEntry(added=2, overwritten=3, skipped={"files_gate_miss": [10]})
    assert R.plan_vs_result(sec, {"images": gate_miss, "tags": R.ResultEntry(added=2)}) == []
    assert R.plan_vs_result(sec, {"tags": R.ResultEntry(added=2)}) == ["images:missing"]
    d = doc(dry=False, result={"data": {"images": gate_miss}})
    j = json.loads(R.render_json(d))
    assert j["dry"] is False and j["result"]["data"]["images"]["skipped_with_reason"]["files_gate_miss"] == {"count": 1, "ids": [10]}
    assert "## Result" in R.render_markdown(d)


# --- combined sync.run document -----------------------------------------------------------------------------------------------------

def test_combined_document_waiting_for_files_and_note():
    files = files_section()
    data = data_section()
    R.mark_waiting_for_files(data.entries[0], [10, 7, 55])
    e = data.entries[0]
    assert e.added.ids == [20, 30] and sorted(e.overwritten.ids) == [5, 9]
    assert e.waiting_for_files["added"].ids == [10] and e.waiting_for_files["overwritten"].ids == [7]
    t = data.totals()
    assert t["added"]["count"] == 4  # images 20, 30 + tags 1, 2 minus nothing: 2 + 2 = 4
    assert t["expected_added"]["count"] == t["added"]["count"] + 1
    assert t["expected_overwritten"]["count"] == t["overwritten"]["count"] + 1
    d = R.combined_document("general", RUN_ID, [files, data], flags=["--first-seed"])
    j = json.loads(R.render_json(d))
    assert j["tool"] == "sync.run" and j["dependency_note"] == R.DEPENDENCY_NOTE
    assert j["plan_hash"] == R._sha([files.plan_hash(), data.plan_hash()])
    assert j["apply_command"] == f"python -m sync.run --env general --first-seed --apply --plan-hash {j['plan_hash']}"
    ent = j["sections"][1]["entries"][0]
    assert ent["skipped_with_reason"]["waiting_for_files"] == {"count": 2, "ids": [7, 10]}
    assert ent["waiting_for_files"]["added"]["ids"] == [10]
    assert R.DEPENDENCY_NOTE in R.render_markdown(d)
    assert "waiting_for_files" in R.render_markdown(d)
    # waiting ids are part of the hash
    other = data_section()
    R.mark_waiting_for_files(other.entries[0], [10])
    assert other.plan_hash() != data.plan_hash()
    # apply: no apply command in an applied combined doc
    assert R.combined_document("general", RUN_ID, [files, data], dry=False).apply_command is None


# --- redaction -------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("build", [
    lambda: R.IdSet(ids=[SENTINEL]),
    lambda: R.IdSet(ids=["photo name.jpg"]),
    lambda: R.Entry(name=SENTINEL),
    lambda: R.Entry(name="t", kind=SENTINEL),
    lambda: R.Entry(name="t", skipped={SENTINEL: R.IdSet()}),
    lambda: R.Entry(name="t", kind="rows", left_behind=R.LeftBehind(names=[SENTINEL])),  # names only for files
    lambda: R.Overwritten(groups={1: [SENTINEL]}),
    lambda: R.Guard(SENTINEL, False),
    lambda: R.Guard("g", False, value=SENTINEL),
    lambda: R.Guard("g", True, lifted_by=SENTINEL),
    lambda: R.Section(name=SENTINEL, tool="sync.promote"),
    lambda: R.Section(name="data", tool="sync.promote", manifest_hash=SENTINEL),
    lambda: R.Section(name="pull", tool="sync.pull", lists={"to_fetch": [SENTINEL]}),
    lambda: R.Section(name="pull", tool="sync.pull", lists={SENTINEL: [1]}),
    lambda: R.Document(env=SENTINEL, run_id=RUN_ID, tool="sync.run"),
    lambda: R.Document(env="it", run_id=SENTINEL, tool="sync.run"),
    lambda: R.ResultEntry(skipped={"files_gate_miss": [SENTINEL]}),
    lambda: R.apply_command("sync.run", "it", "ab" * 8, [SENTINEL]),
    lambda: R.LeftBehind(names=["a\nb"]),
])
def test_redaction_guard_rejects_anything_but_codes(build):
    with pytest.raises(R.ReportError):
        build()


def test_free_text_sources_are_scrubbed_not_passed_through():
    sec = R.Section(name="verify", tool="sync.verify", flags={"note": SENTINEL, "n": 3},
                    checks=[{"id": "D1", "status": "warn", "severity": "warn", "detail": SENTINEL}])
    d = R.Document(env="it", run_id=RUN_ID, tool="sync.promote", sections=[sec],
                   handshake=[{"step": "H1", "ok": False, "detail": SENTINEL}],
                   preconditions=[{"id": "C1", "ok": False, "detail": f"row value {SENTINEL}"}])
    j = json.loads(R.render_json(d))
    assert j["handshake"][0]["detail"] == R.REDACTED and j["preconditions"][0]["detail"] == R.REDACTED
    assert j["sections"][0]["flags"]["note"] == R.REDACTED and j["sections"][0]["flags"]["n"] == 3
    assert j["sections"][0]["checks"][0]["detail"] == R.REDACTED


def test_sentinel_absent_from_json_md_and_stdout(tmp_path: Path):
    """Feed sentinel text through every source (strict ones are refused, free-text ones are scrubbed); nothing may reach the outputs."""
    refused = scrubbed = 0
    # strict sources
    for build in (lambda: R.Entry(name=SENTINEL), lambda: R.IdSet(ids=[SENTINEL]),
                  lambda: R.Entry(name="t", skipped={SENTINEL: R.IdSet()}), lambda: R.Overwritten(groups={1: [SENTINEL]}),
                  lambda: R.Guard(SENTINEL, False), lambda: R.apply_command("sync.run", "it", "ab" * 8, [SENTINEL])):
        with pytest.raises(R.ReportError):
            build()
        refused += 1
    # scrubbed sources
    sec = R.Section(
        name="data", tool="sync.promote", flags={"a": SENTINEL}, checks=[{"id": "C", "detail": SENTINEL, "status": "ok"}],
        entries=[R.Entry(name="images", kind="images", added=R.IdSet(ids=[1]), left_behind=R.LeftBehind(ids=[2]))],
        guards=[R.Guard("g", True, 1, 0, "--allow-x")],
    )
    d = R.Document(env="it", run_id=RUN_ID, tool="sync.promote", sections=[sec],
                   handshake=[{"step": "H1", "ok": True, "detail": SENTINEL}],
                   preconditions=[{"id": "C1", "ok": False, "detail": SENTINEL}])
    d.apply_command = R.apply_command("sync.promote", "it", d.plan_hash)
    out = R.write_report(d, tmp_path / RUN_ID)
    texts = [(tmp_path / RUN_ID / "report.json").read_text(encoding="utf-8"),
             (tmp_path / RUN_ID / "report.md").read_text(encoding="utf-8"), out["summary"]]
    for t in texts:
        for part in ("SENTINEL", "secret", "ünï", "ключ", "text"):
            assert part not in t, part
    assert refused == 6


def test_left_behind_file_name_is_the_only_place_a_name_appears(tmp_path: Path):
    d = doc([files_section()])
    R.write_report(d, tmp_path / RUN_ID)
    js = (tmp_path / RUN_ID / "report.json").read_text()
    assert "orphan one.jpg" in js
    entry = json.loads(js)["sections"][0]["entries"][0]
    assert entry["left_behind"]["names"] == ["b.png", "orphan one.jpg"] and entry["left_behind"]["count"] == 2
    # name problems carry reason code + image id only
    s = R.Section(name="files", tool="sync.files promote",
                  entries=[R.Entry(name="meme_files", kind="files", skipped={"name_problem": R.IdSet(ids=[12])})])
    assert json.loads(R.render_json(doc([s])))["sections"][0]["entries"][0]["skipped_with_reason"]["name_problem"] == {"count": 1, "ids": [12]}


def test_input_objects_are_not_mutated_by_rendering():
    d = doc()
    before = copy.deepcopy(d.sections[0].entries[0].__dict__)
    R.render_json(d)
    R.render_markdown(d)
    assert d.sections[0].entries[0].__dict__ == before


def test_atomic_write_text_retries_a_replace_held_by_a_scanner_review_finding_l11(tmp_path, monkeypatch):
    import os

    from sync import config

    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("held")
        return real(src, dst)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(config.time, "sleep", lambda s: None)
    target = tmp_path / "report.json"
    from sync import report as report_mod
    report_mod.atomic_write_text(target, "hello")
    assert target.read_text() == "hello" and len(calls) == 3 and not list(tmp_path.glob("*.tmp"))
