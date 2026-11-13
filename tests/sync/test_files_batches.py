import hashlib
import io
import os
import re
import tarfile

import pytest

from sync.files import batches as b
from sync.files import manifest as m
from sync.files.manifest import ManifestEntry

ID = "00000000-0000-0000-0000-%012d"
NAME_RE = re.compile(r"^[^/\\\x00-\x1f.][^/\\\x00-\x1f]{0,254}$")  # the gate's final-name regex


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make(tmp_path, files: dict):
    out = []
    for i, (name, data) in enumerate(files.items()):
        (tmp_path / name).write_bytes(data)
        out.append(ManifestEntry(ID % i, name, len(data), sha(data), False, str(tmp_path / name)))
    return out


def fake(name, size, i=0):
    return ManifestEntry(ID % i, name, size, sha(name.encode()), False, "/nonexistent/" + name)


# ---- planner ------------------------------------------------------------------------------------------------------------------

def test_batch_mb_is_limited_to_150_500():
    for bad in (0, 100, 149, 501, 1000):
        with pytest.raises(b.PlanError):
            b.plan_batches("general", [fake("a.jpg", 1)], bad)
    assert len(b.plan_batches("general", [fake("a.jpg", 1)], 150)) == 1
    assert len(b.plan_batches("general", [fake("a.jpg", 1)], 500)) == 1


def test_split_is_name_sorted_and_respects_target():
    entries = [fake(f"f{i:02d}.jpg", 40, i) for i in range(10)][::-1]
    out = b.plan_batches("general", entries, target_bytes=100)
    assert [[e.filename for e in x.entries] for x in out] == [["f00.jpg", "f01.jpg"], ["f02.jpg", "f03.jpg"], ["f04.jpg", "f05.jpg"],
                                                              ["f06.jpg", "f07.jpg"], ["f08.jpg", "f09.jpg"]]
    assert all(x.total_bytes == 80 for x in out)
    flat = [e.filename for x in out for e in x.entries]
    assert flat == sorted(flat)


def test_default_target_keeps_batches_between_the_limits():
    mb = 1024 * 1024
    entries = [fake(f"f{i:03d}.jpg", 100 * mb, i) for i in range(10)]
    out = b.plan_batches("general", entries, 300)
    assert [len(x.entries) for x in out] == [3, 3, 3, 1]
    assert all(x.total_bytes <= 300 * mb for x in out)


def test_member_count_cap_splits_small_files():
    out = b.plan_batches("general", [fake(f"f{i}.jpg", 1, i) for i in range(7)], max_members=3)
    assert [len(x.entries) for x in out] == [3, 3, 1]


def test_member_larger_than_target_gets_its_own_batch():
    out = b.plan_batches("general", [fake("a.jpg", 10), fake("b.jpg", 500), fake("c.jpg", 10)], target_bytes=100)
    assert [[e.filename for e in x.entries] for x in out] == [["a.jpg"], ["b.jpg"], ["c.jpg"]]


def test_empty_plan_and_rejections():
    assert b.plan_batches("general", []) == []
    with pytest.raises(b.PlanError):
        b.plan_batches("general", [fake("a.jpg", 201 * 1024 * 1024)])
    with pytest.raises(b.PlanError):
        b.plan_batches("general", [fake("a.jpg", 1), fake("a.jpg", 1, 1)])
    with pytest.raises(b.PlanError):
        b.plan_batches("general", [fake("a\tb.jpg", 1)])
    with pytest.raises(b.PlanError):
        b.plan_batches("general", [fake(".hidden", 1)])


# ---- ids ----------------------------------------------------------------------------------------------------------------------

def test_ids_are_deterministic_and_content_bound():
    entries = [fake(f"f{i}.jpg", 10, i) for i in range(6)]
    a = b.plan_batches("general", entries, target_bytes=25)
    again = b.plan_batches("general", list(reversed(entries)), target_bytes=25)
    assert [x.id for x in a] == [x.id for x in again]
    assert all(re.fullmatch(r"[0-9a-f]{32}", x.id) for x in a) and len({x.id for x in a}) == len(a)
    other_env = b.plan_batches("metal", entries, target_bytes=25)
    assert not {x.id for x in a} & {x.id for x in other_env}
    # a change in one file changes its batch id and (only) its batch id when the slices stay the same
    changed = entries[:]
    changed[0] = ManifestEntry(entries[0].id, entries[0].filename, 10, sha(b"different"), False, "")
    c = b.plan_batches("general", changed, target_bytes=25)
    assert c[0].id != a[0].id and [x.id for x in c[1:]] == [x.id for x in a[1:]]
    # same name and hash, different size: different id
    resized = [ManifestEntry(e.id, e.filename, e.size + 1, e.sha256, False, "") for e in entries[:2]]
    assert b.batch_id("general", resized) != b.batch_id("general", entries[:2])


# ---- tar ----------------------------------------------------------------------------------------------------------------------

def read_back(data: bytes):
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as t:
        for mem in t.getmembers():
            out[mem.name] = (mem, t.extractfile(mem).read())
    return out


def test_tar_is_regular_only_plain_names_and_roundtrips(tmp_path):
    files = {"b.jpg": b"bbb", "a.jpg": b"a" * 5000, "мем ☺.png": b"cyr", "-dash.jpg": b"", "x" * 200 + ".jpg": b"long"}
    entries = make(tmp_path, files)
    batch = b.plan_batches("general", entries, target_bytes=10**9)[0]
    buf = io.BytesIO()
    n = b.write_tar(batch, buf)
    assert n == len(buf.getvalue()) and n % 512 == 0
    got = read_back(buf.getvalue())
    assert list(got) == [e.filename for e in batch.entries] == sorted(files, key=lambda s: s.encode())
    for name, (mem, data) in got.items():
        assert mem.isreg() and not mem.issym() and not mem.islnk() and NAME_RE.match(name) and len(name.encode()) <= 255
        assert data == files[name]
        assert (mem.uid, mem.gid, mem.uname, mem.gname, mem.mode, mem.mtime) == (0, 0, "", "", 0o644, 0)


def test_tar_bytes_are_deterministic_and_independent_of_input_order(tmp_path):
    entries = make(tmp_path, {"b.jpg": b"1" * 700, "a.jpg": b"2" * 10, "ж.jpg": b"3"})
    one = b.plan_batches("general", entries, target_bytes=10**9)[0]
    two = b.plan_batches("general", list(reversed(entries)), target_bytes=10**9)[0]
    x, y = io.BytesIO(), io.BytesIO()
    b.write_tar(one, x)
    for e in entries:  # touch mtimes: the tar must not depend on them
        os.utime(e.path, (12345, 12345))
    b.write_tar(two, y)
    assert x.getvalue() == y.getvalue()


def test_tar_streams_to_an_unseekable_sink(tmp_path):
    class Sink:
        def __init__(self):
            self.data = bytearray()

        def write(self, d):
            self.data += d
            return len(d)

        def tell(self):
            raise OSError("not seekable")

        def seek(self, *a):
            raise OSError("not seekable")

    entries = make(tmp_path, {"a.jpg": b"a" * (3 * 1024 * 1024 + 7)})
    s = Sink()
    b.write_tar(b.plan_batches("general", entries, target_bytes=10**9)[0], s)
    assert read_back(bytes(s.data))["a.jpg"][1] == b"a" * (3 * 1024 * 1024 + 7)


def test_source_symlink_is_never_packed(tmp_path):
    entries = make(tmp_path, {"real.jpg": b"data", "link.jpg": b"data"})
    os.remove(tmp_path / "link.jpg")
    try:
        os.symlink(tmp_path / "real.jpg", tmp_path / "link.jpg")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    batch = b.plan_batches("general", entries, target_bytes=10**9)[0]
    with pytest.raises(b.PackError) as ei:
        b.write_tar(batch, io.BytesIO())
    assert ei.value.name == "link.jpg" and ei.value.rule == "not_regular_file"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no fifo")
def test_source_fifo_never_blocks(tmp_path):
    os.mkfifo(tmp_path / "p.jpg")
    e = ManifestEntry(ID % 1, "p.jpg", 3, sha(b"abc"), False, str(tmp_path / "p.jpg"))
    with pytest.raises(b.PackError) as ei:
        b.write_tar(b.plan_batches("general", [e], target_bytes=100)[0], io.BytesIO())
    assert ei.value.rule == "not_regular_file"


def test_directory_or_missing_source_is_refused(tmp_path):
    (tmp_path / "d.jpg").mkdir()
    for path, rule in ((tmp_path / "d.jpg", "not_regular_file"), (tmp_path / "gone.jpg", "not_regular_file")):
        e = ManifestEntry(ID % 1, "x.jpg", 3, sha(b"abc"), False, str(path))
        with pytest.raises(b.PackError) as ei:
            b.write_tar(b.plan_batches("general", [e], target_bytes=100)[0], io.BytesIO())
        assert ei.value.rule == rule


def test_same_size_content_change_is_refused(tmp_path):
    entries = make(tmp_path, {"a.jpg": b"abcdef"})
    (tmp_path / "a.jpg").write_bytes(b"ABCDEF")
    with pytest.raises(b.PackError) as ei:
        b.write_tar(b.plan_batches("general", entries, target_bytes=100)[0], io.BytesIO())
    assert ei.value.rule == "content_hash_differs"


def test_size_change_before_packing_is_refused(tmp_path):
    entries = make(tmp_path, {"a.jpg": b"abcdef"})
    (tmp_path / "a.jpg").write_bytes(b"abcdefg")
    with pytest.raises(b.PackError) as ei:
        b.write_tar(b.plan_batches("general", entries, target_bytes=100)[0], io.BytesIO())
    assert ei.value.rule == "changed_during_read"


@pytest.mark.parametrize("change", ["grow", "shrink"])
def test_file_changing_during_the_read_is_refused(tmp_path, monkeypatch, change):
    data = b"z" * (b.READ_CHUNK * 2 + 10)
    entries = make(tmp_path, {"a.jpg": data})
    real_read = b._HashingSource.read
    state = {"done": False}

    def racing(self, n=-1):
        out = real_read(self, n)
        if not state["done"]:
            state["done"] = True
            if change == "grow":
                with open(tmp_path / "a.jpg", "ab") as f:
                    f.write(b"tail")
            else:
                with open(tmp_path / "a.jpg", "r+b") as f:
                    f.truncate(len(data) // 2)
        return out

    monkeypatch.setattr(b._HashingSource, "read", racing)
    with pytest.raises(b.PackError) as ei:
        b.write_tar(b.plan_batches("general", entries, target_bytes=10**9)[0], io.BytesIO())
    assert ei.value.rule == "changed_during_read"


def test_hostile_names_cannot_reach_the_tar(tmp_path):
    for bad in ("a/../../etc/passwd", "/abs.jpg", "..", "a\nb.jpg", "\x00.jpg"):
        with pytest.raises(b.PlanError):
            b.plan_batches("general", [fake(bad, 1)], target_bytes=100)


# ---- sums ---------------------------------------------------------------------------------------------------------------------

def test_sums_payload_is_nul_delimited_and_unambiguous(tmp_path):
    entries = make(tmp_path, {"a b.jpg": b"1", "м.jpg": b"22", "-x.jpg": b"333"})
    batch = b.plan_batches("general", entries, target_bytes=100)[0]
    payload = b.sums_payload(batch)
    flat = payload.split(b"\0")
    assert flat.pop() == b"" and len(flat) == 9
    parsed = [tuple(x.decode() for x in flat[i:i + 3]) for i in range(0, 9, 3)]
    assert [p[2] for p in parsed] == [e.filename for e in batch.entries]
    assert all(re.fullmatch(r"[0-9a-f]{64}", p[0]) and p[1].isdigit() for p in parsed)
    assert parsed[0][:2] == (batch.entries[0].sha256, str(batch.entries[0].size))


def test_sums_payload_is_read_by_the_gate_as_triples(tmp_path):
    """Contract with deploy/prod/memesync-gate.py: its `verify` reads flat NUL fields in groups of three."""
    import io
    import types

    from tests.sync.gate_helpers import gate

    entries = make(tmp_path, {"a b.jpg": b"1", "м.jpg": b"22"})
    batch = b.plan_batches("general", entries, target_bytes=100)[0]
    ctx = types.SimpleNamespace(stdin=io.BytesIO(b.sums_payload(batch)))
    triples = gate.read_fields(ctx, 3)
    assert [(t[2], t[0], int(t[1])) for t in triples] == [(e.filename, e.sha256, e.size) for e in batch.entries]
    assert all(gate.SHA_RE.fullmatch(t[0]) for t in triples)
