import hashlib
import os
import sys
import unicodedata

import pytest

from sync.files import manifest as m
from sync.files.manifest import Row, build_manifest, check_name, manifest_sha256, verify_hashes

UUIDS = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 40)]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(tmp_path, name, data=b"x"):
    (tmp_path / name).write_bytes(data)
    return sha(data)


def rows_for(tmp_path, files: dict):
    out = []
    for i, (name, data) in enumerate(files.items()):
        out.append(Row(UUIDS[i], name, put(tmp_path, name, data)))
    return out


# ---- name rules ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,rule", [
    ("a\tb.jpg", "control_character"), ("a\nb.jpg", "control_character"), ("a\x00b.jpg", "control_character"),
    ("a\x7fb.jpg", "control_character"), ("a/b.jpg", "path_separator"), ("a\\b.jpg", "path_separator"),
    ("../x.jpg", "path_separator"), ("a.jpg:stream", "path_separator"), (".hidden.jpg", "leading_dot"), (".", "leading_dot"),
    ("..", "leading_dot"), ("a.jpg.", "trailing_dot_or_space"), ("a.jpg ", "trailing_dot_or_space"),
    ("CON", "reserved_name"), ("con.jpg", "reserved_name"), ("Nul.tar.gz", "reserved_name"), ("COM1.jpg", "reserved_name"),
    ("lpt9", "reserved_name"), ("COM².png", "reserved_name"), ("aux .jpg", "reserved_name"),
    ("a" * 252 + ".jpg", "name_too_long"), ("ж" * 128, "name_too_long"), ("\ud800.jpg", "invalid_unicode"), ("", "empty"),
])
def test_bad_names(name, rule):
    assert check_name(name) == rule


@pytest.mark.parametrize("name", [
    "meme.jpg", "-leading-dash.jpg", " leading space.jpg", "мем.jpg", "\U0001f600.png", "a" * 251 + ".jpg",
    "ж" * 127, "CONSOLE.jpg", "COM10.jpg", "a&b;$`'.jpg", "x.CON",
])
def test_good_names(name):
    assert check_name(name) is None


def test_byte_limit_not_character_limit():
    assert check_name("ж" * 127 + "a") is None  # 255 bytes
    assert check_name("ж" * 127 + "ab") == "name_too_long"


# ---- manifest_sha256 ----------------------------------------------------------------------------------------------------------

def test_manifest_sha256_is_order_independent_and_exact():
    rows = [("b-id", "z.jpg", "h2"), ("a-id", "y.jpg", None)]
    expected = hashlib.sha256(b"a-id\ty.jpg\t\nb-id\tz.jpg\th2\n").hexdigest()
    assert manifest_sha256(rows) == expected
    assert manifest_sha256(list(reversed(rows))) == expected
    assert manifest_sha256([{"id": "b-id", "filename": "z.jpg", "content_hash": "h2"}, Row("a-id", "y.jpg", None)]) == expected


def test_manifest_sha256_changes_with_any_field_and_survives_hostile_names():
    base = manifest_sha256([("1", "a.jpg", "h")])
    assert manifest_sha256([("2", "a.jpg", "h")]) != base
    assert manifest_sha256([("1", "b.jpg", "h")]) != base
    assert manifest_sha256([("1", "a.jpg", "g")]) != base
    manifest_sha256([("1", "\ud800\tx\n", None)])  # no exception


def test_manifest_includes_left_out_images(tmp_path):
    rows = [Row(UUIDS[0], "missing.jpg", sha(b"x"))]
    result = build_manifest(rows, str(tmp_path))
    assert result.manifest_sha256 == manifest_sha256(rows)
    assert result.left_out and not result.entries


# ---- build --------------------------------------------------------------------------------------------------------------------

def test_build_happy_path_uses_db_name_size_and_column_hash(tmp_path):
    rows = rows_for(tmp_path, {"a.jpg": b"aaa", "b.png": b"bbbb"})
    result = build_manifest(rows, str(tmp_path))
    assert not result.left_out and result.promotable_count == 2
    assert result.entries["a.jpg"].size == 3 and result.entries["a.jpg"].sha256 == rows[0].content_hash
    assert not result.entries["a.jpg"].verified and result.total_bytes == 7
    assert not result.tripwire_tripped


def test_null_hash_is_computed_and_warned(tmp_path):
    put(tmp_path, "a.jpg", b"abc")
    result = build_manifest([Row(UUIDS[0], "a.jpg", None)], str(tmp_path))
    assert result.entries["a.jpg"].sha256 == sha(b"abc") and result.entries["a.jpg"].verified
    assert (UUIDS[0], "hash_computed") in result.warnings


def test_uppercase_hash_is_accepted_malformed_is_not(tmp_path):
    put(tmp_path, "a.jpg", b"abc")
    put(tmp_path, "b.jpg", b"abc")
    result = build_manifest([Row(UUIDS[0], "a.jpg", sha(b"abc").upper()), Row(UUIDS[1], "b.jpg", "zz")], str(tmp_path))
    assert result.entries["a.jpg"].sha256 == sha(b"abc")
    assert [(x.reason, x.rule) for x in result.left_out] == [("hash_mismatch", "malformed_hash")]


def test_missing_file_and_directory_and_subdirectory_name(tmp_path):
    (tmp_path / "dir.jpg").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "in.jpg").write_bytes(b"x")
    rows = [Row(UUIDS[0], "gone.jpg", sha(b"x")), Row(UUIDS[1], "dir.jpg", sha(b"x")), Row(UUIDS[2], "sub/in.jpg", sha(b"x"))]
    result = build_manifest(rows, str(tmp_path))
    got = {x.id: (x.reason, x.rule) for x in result.left_out}
    assert got[UUIDS[0]] == ("missing_file", "no_such_file")
    assert got[UUIDS[1]] == ("missing_file", "not_regular_file")
    assert got[UUIDS[2]] == ("name_problem", "path_separator")
    assert not result.entries


def test_hash_mismatch_found_by_verify_not_by_build(tmp_path):
    put(tmp_path, "a.jpg", b"abc")
    put(tmp_path, "b.jpg", b"abc")
    rows = [Row(UUIDS[0], "a.jpg", sha(b"abc")), Row(UUIDS[1], "b.jpg", sha(b"other"))]
    result = build_manifest(rows, str(tmp_path))
    assert set(result.entries) == {"a.jpg", "b.jpg"}
    verify_hashes(result, ["b.jpg"])
    assert set(result.entries) == {"a.jpg"}
    assert [(x.id, x.reason, x.rule, x.label) for x in result.left_out] == [(UUIDS[1], "hash_mismatch", "content_hash_differs", "hash_mismatch")]
    verify_hashes(result)  # all: the rest passes and is marked verified
    assert result.entries["a.jpg"].verified


def test_name_problems_left_out_with_labels(tmp_path):
    names = {"trail.jpg.": b"1", "CON.jpg": b"2", "ok.jpg": b"3"}
    rows = [Row(UUIDS[i], n, sha(d)) for i, (n, d) in enumerate(names.items())]
    put(tmp_path, "ok.jpg", b"3")
    result = build_manifest(rows, str(tmp_path))
    assert set(result.entries) == {"ok.jpg"}
    assert sorted(x.label for x in result.left_out) == ["name_reserved_name", "name_trailing_dot_or_space"]


def test_normalisation_and_case_collisions_leave_out_every_member(tmp_path):
    nfc, nfd = unicodedata.normalize("NFC", "café.jpg"), unicodedata.normalize("NFD", "café.jpg")
    assert nfc != nfd
    put(tmp_path, nfc, b"1")
    put(tmp_path, "Photo.jpg", b"2")
    put(tmp_path, "keep.jpg", b"3")
    rows = [Row(UUIDS[0], nfc, sha(b"1")), Row(UUIDS[1], nfd, sha(b"1")), Row(UUIDS[2], "Photo.jpg", sha(b"2")),
            Row(UUIDS[3], "photo.JPG", sha(b"2")), Row(UUIDS[4], "keep.jpg", sha(b"3"))]
    result = build_manifest(rows, str(tmp_path))
    assert set(result.entries) == {"keep.jpg"}
    assert {x.id for x in result.left_out} == set(UUIDS[:4])
    assert {x.rule for x in result.left_out} == {"normalisation_collision"}


def test_identical_filename_on_two_ids_is_a_duplicate(tmp_path):
    put(tmp_path, "a.jpg", b"1")
    rows = [Row(UUIDS[0], "a.jpg", sha(b"1")), Row(UUIDS[1], "a.jpg", sha(b"1"))]
    result = build_manifest(rows, str(tmp_path))
    assert not result.entries and {x.rule for x in result.left_out} == {"duplicate_name"}


def test_case_difference_on_disk_warns_and_keeps_db_name(tmp_path):
    put(tmp_path, "Disk.JPG", b"1")
    result = build_manifest([Row(UUIDS[0], "disk.jpg", sha(b"1"))], str(tmp_path))
    if "disk.jpg" in result.entries:  # case-sensitive or insensitive scan both end here
        assert result.entries["disk.jpg"].filename == "disk.jpg"
        assert result.entries["disk.jpg"].path.endswith("Disk.JPG")
        assert (UUIDS[0], "case_differs_on_disk") in result.warnings
    else:
        pytest.fail("case-only difference should be promoted with a warning")


def test_two_disk_spellings_for_one_name_is_missing(tmp_path):
    if sys.platform == "win32":
        pytest.skip("NTFS cannot hold both spellings")
    put(tmp_path, "A.jpg", b"1")
    put(tmp_path, "a.JPG", b"2")
    result = build_manifest([Row(UUIDS[0], "a.jpg", sha(b"1"))], str(tmp_path))
    assert not result.entries and result.left_out[0].reason == "missing_file"


def test_symlink_source_is_not_promoted(tmp_path):
    target = tmp_path / "real.jpg"
    target.write_bytes(b"secret")
    link = tmp_path / "link.jpg"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    result = build_manifest([Row(UUIDS[0], "link.jpg", sha(b"secret"))], str(tmp_path))
    assert not result.entries and (result.left_out[0].reason, result.left_out[0].rule) == ("missing_file", "not_regular_file")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no fifo")
def test_fifo_source_is_not_read(tmp_path):
    os.mkfifo(tmp_path / "pipe.jpg")
    result = build_manifest([Row(UUIDS[0], "pipe.jpg", sha(b"x"))], str(tmp_path))
    assert not result.entries and result.left_out[0].rule == "not_regular_file"
    with pytest.raises(m.NotRegular):
        m.hash_file(str(tmp_path / "pipe.jpg"))


def test_hash_file_detects_change_during_read(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x" * 100)
    real_read = os.read
    calls = []

    def racing_read(fd, n):
        data = real_read(fd, n)
        if not calls:
            calls.append(1)
            with open(path, "ab") as f:
                f.write(b"more")
        return data

    monkeypatch.setattr(m.os, "read", racing_read)
    with pytest.raises(m.FileChanged):
        m.hash_file(str(path))


def test_null_hash_file_changing_during_read_is_left_out(tmp_path, monkeypatch):
    put(tmp_path, "a.jpg", b"x" * 10)

    def boom(path):
        raise m.FileChanged("x")

    monkeypatch.setattr(m, "hash_file", boom)
    result = build_manifest([Row(UUIDS[0], "a.jpg", None)], str(tmp_path))
    assert (result.left_out[0].reason, result.left_out[0].rule) == ("hash_mismatch", "changed_during_read")


def test_verify_marks_vanished_file_missing(tmp_path):
    put(tmp_path, "a.jpg", b"abc")
    result = build_manifest([Row(UUIDS[0], "a.jpg", sha(b"abc"))], str(tmp_path))
    os.remove(tmp_path / "a.jpg")
    verify_hashes(result)
    assert not result.entries and result.left_out[0].reason == "missing_file"


# ---- tripwire -----------------------------------------------------------------------------------------------------------------

def test_tripwire_is_above_half_a_percent(tmp_path):
    rows = []
    for i in range(200):
        name = f"f{i}.jpg"
        rows.append(Row(f"{i:08d}-0000-0000-0000-000000000000", name, put(tmp_path, name, b"%d" % i)))
    one = build_manifest(rows + [Row("ffffffff-0000-0000-0000-000000000000", "bad.jpg.", "0" * 64)], str(tmp_path))
    assert len(one.left_out) == 1 and not one.tripwire_tripped  # 1 is not above 0.005 * 201
    ok = build_manifest(rows + [Row(f"ffffffff-0000-0000-0000-00000000000{i}", f"bad{i}.jpg.", "0" * 64) for i in (1, 2)], str(tmp_path))
    assert len(ok.left_out) == 2 and ok.promotable_count == 202 and ok.tripwire_tripped  # 2 > 1.01
    clean = build_manifest(rows, str(tmp_path))
    assert not clean.tripwire_tripped
    small = build_manifest([Row(UUIDS[0], "none.jpg", "0" * 64)], str(tmp_path))
    assert small.tripwire_tripped


# ---- DB query -----------------------------------------------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, rows):
        self.rows, self.sql, self.closed = rows, None, False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self):
        return self.cur


def test_load_promotable_rows_uses_the_shared_sql():
    conn = FakeConn([("1", "a.jpg", "h"), (2, "b.jpg", None)])
    rows = m.load_promotable_rows(conn)
    assert rows == [Row("1", "a.jpg", "h"), Row("2", "b.jpg", None)]
    from sync.model import PROMOTABLE_IMAGES_SQL
    assert PROMOTABLE_IMAGES_SQL in conn.cur.sql and conn.cur.closed
