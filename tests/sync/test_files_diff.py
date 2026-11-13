import hashlib

from sync import report
from sync.files import diff as d
from sync.files.manifest import LeftOut, Manifest, ManifestEntry


def h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


ID = "00000000-0000-0000-0000-%012d"


def entry(i, name, size, content):
    return ManifestEntry(ID % i, name, size, h(content), True, "/dev/" + name)


def manifest(*entries, left_out=(), promotable=None):
    return Manifest(entries={e.filename: e for e in entries}, left_out=list(left_out),
                    promotable_count=promotable if promotable is not None else len(entries) + len(left_out))


def rec(name, size, sha="", typ="f", nlink=1, uid=1000, mode="644"):
    return f"{typ}\t{size}\t{sha}\t{nlink}\t{uid}\t{mode}\t{name}".encode("utf-8", "surrogateescape") + b"\0"


def test_all_categories():
    m = manifest(entry(1, "new.jpg", 10, "n"), entry(2, "same.jpg", 20, "s"), entry(3, "grown.jpg", 30, "g"),
                 entry(4, "edited.jpg", 40, "e2"), entry(5, "unknown.jpg", 50, "u"))
    data = b"".join([rec("same.jpg", 20, h("s")), rec("grown.jpg", 31, h("g")), rec("edited.jpg", 40, h("e1")),
                     rec("unknown.jpg", 50, ""), rec("old.jpg", 5, h("o"))])
    out = d.diff_manifest(m, d.parse_list(data))
    assert [e.filename for e in out.added] == ["new.jpg"]
    assert [e.filename for e, _ in out.size_changed] == ["grown.jpg"]
    assert [e.filename for e, _ in out.hash_changed] == ["edited.jpg"]
    assert [e.filename for e in out.unverified] == ["unknown.jpg"]
    assert [e.filename for e in out.unchanged] == ["same.jpg"]
    assert out.left_behind == ["old.jpg"]
    assert out.replaced_count == 2
    assert [e.filename for e in out.to_send] == ["edited.jpg", "grown.jpg", "new.jpg", "unknown.jpg"]


def test_only_regular_single_link_files_are_diffed_others_are_anomalies():
    m = manifest(entry(1, "a.jpg", 1, "a"), entry(2, "b.jpg", 1, "b"), entry(3, "c.jpg", 1, "c"))
    data = b"".join([rec("a.jpg", 1, h("a"), typ="l"), rec("b.jpg", 1, h("b"), nlink=2), rec("c.jpg", 1, h("c"), typ="d"),
                     rec("fifo", 0, "", typ="p"), rec("hard.jpg", 1, h("x"), nlink=3)])
    out = d.diff_manifest(m, d.parse_list(data))
    assert {a.name: a.reason for a in out.anomalies} == {"a.jpg": "not_regular", "b.jpg": "hardlinked", "c.jpg": "not_regular",
                                                         "fifo": "not_regular", "hard.jpg": "hardlinked"}
    assert not out.unchanged and not out.left_behind and len(out.added) == 3
    assert {a.name for a in out.blocking_anomalies} == {"c.jpg"}
    assert out.prod_regular_count == 0


def test_hostile_remote_records_do_not_raise():
    raw = b"".join([
        rec("tab\tname.jpg", 1, h("t")),            # tab inside the name
        rec("new\nline.jpg", 1, h("t")),
        rec("bad\udcff.jpg", 1, h("t")),            # non-UTF-8 byte (surrogateescape)
        rec("slash/inside.jpg", 1, h("t")),
        b"garbage-without-tabs\0",
        b"f\tnotanumber\t\t1\t0\t644\tx.jpg\0",
        rec("x.jpg", 1, h("x"), typ="invalid"),
        b"\0\0",
        rec("good.jpg", 2, h("g")),
    ])
    parsed = d.parse_list(raw)
    out = d.diff_manifest(manifest(entry(1, "good.jpg", 2, "g")), parsed)
    assert [e.filename for e in out.unchanged] == ["good.jpg"]
    assert {a.reason for a in out.anomalies} == {"invalid_name", "malformed_record"}
    assert len(out.anomalies) == 7  # tab, newline, non-UTF-8, slash, garbage, bad number, `invalid` type; the empty records are skipped


def test_duplicate_remote_record_is_an_anomaly_not_a_crash():
    out = d.diff_manifest(manifest(entry(1, "a.jpg", 1, "a")), d.parse_list(rec("a.jpg", 1, h("a")) + rec("a.jpg", 1, h("a"))))
    assert [a.reason for a in out.anomalies] == ["duplicate_record"] and len(out.unchanged) == 1


def test_report_names_never_carry_hostile_text():
    assert d.report_name("fine.jpg") == "fine.jpg"
    for bad in ["a\nb", "a\tb", "x\udcffy", "\ud800", "a" * 300, "a\x00"]:
        out = d.report_name(bad)
        assert out.startswith("invalid-name-") and report.check_file_name(out)
    assert d.report_name("a\nb") == d.report_name("a\nb") != d.report_name("a\tb")


def test_to_entry_feeds_the_report_and_plan_hash_is_stable():
    lo = [LeftOut(ID % 9, "bad.jpg.", "name_problem", "trailing_dot_or_space"), LeftOut(ID % 8, "gone.jpg", "missing_file", "no_such_file")]
    m = manifest(entry(1, "new.jpg", 10, "n"), entry(3, "grown.jpg", 30, "g"), entry(4, "edited.jpg", 40, "e2"),
                 entry(5, "unknown.jpg", 50, "u"), entry(2, "same.jpg", 20, "s"), left_out=lo)
    data = b"".join([rec("same.jpg", 20, h("s")), rec("grown.jpg", 31, h("g")), rec("edited.jpg", 40, h("e1")),
                     rec("unknown.jpg", 50, ""), rec("evil\nname.jpg", 5, h("o")), rec("old.jpg", 5, h("o"))])
    out = d.diff_manifest(m, d.parse_list(data))
    e = d.to_entry(m, out)
    assert e.kind == "files"
    assert e.added.ids == [ID % 1] and e.added.bytes == 10
    assert set(e.overwritten.size_changed) == {ID % 3} and set(e.overwritten.hash_changed) == {ID % 4}
    assert set(e.overwritten.ids) == {ID % 3, ID % 4, ID % 5} and e.overwritten.bytes == 120
    assert e.unchanged.n == 1
    assert set(e.skipped) == {"name_problem", "missing_file"}
    assert e.left_behind.names == sorted(["old.jpg", d.report_name("evil\nname.jpg")])
    sec = report.Section(name="files", tool="sync.files promote", entries=[e], guards=d.guards(m, out.prod_regular_count))
    assert report.section_plan_hash(sec) == report.section_plan_hash(
        report.Section(name="files", tool="sync.files promote", entries=[d.to_entry(m, d.diff_manifest(m, d.parse_list(data)))],
                       guards=d.guards(m, out.prod_regular_count)))
    text = report.render_json(report.Document(env="general", run_id="20261113T000000Z-0123abcd", tool="sync.files promote", sections=[sec])) \
        if hasattr(report, "Document") else ""
    assert "evil" not in text


def test_guards():
    many = [entry(i, f"f{i}.jpg", 1, str(i)) for i in range(10)]
    g = {x.id: x for x in d.guards(manifest(*many), 100)}
    assert g["manifest_below_half_of_prod"].tripped and not g["empty_manifest"].tripped
    assert not {x.id: x for x in d.guards(manifest(*many), 20)}["manifest_below_half_of_prod"].tripped
    assert {x.id: x for x in d.guards(manifest(), 0)}["empty_manifest"].tripped
    assert not {x.id: x for x in d.guards(manifest(*many), 0)}["manifest_below_half_of_prod"].tripped  # empty prod (first seed)
    trip = manifest(*many, left_out=[LeftOut(ID % 99, "x.", "name_problem", "trailing_dot_or_space")])
    assert {x.id: x for x in d.guards(trip, 10)}["left_out_tripwire"].tripped


def test_empty_remote_listing_means_everything_is_added():
    m = manifest(entry(1, "a.jpg", 1, "a"))
    out = d.diff_manifest(m, d.parse_list(b""))
    assert len(out.added) == 1 and not out.left_behind
