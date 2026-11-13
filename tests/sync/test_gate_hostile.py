"""The strict extractor and the stdin/command parsing of deploy/prod/memesync-gate.py against hostile input."""
import io
import tarfile
import unicodedata

import pytest

from tests.sync.gate_helpers import Tree, gate, make_tar, sha, special, work_names


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path)


def assert_rejected(tree, bid, data, reason=None):
    tree.upload(bid, data)
    r = tree.run("receive " + bid)
    assert r.code == 1, (r.code, r.err)
    if reason:
        assert reason in r.err, r.err
    # nothing left behind: no batch, no listing, nothing visible, the hostile upload is not kept
    assert work_names(tree) == []
    assert list(tree.base.iterdir()) == []
    assert not (tree.incoming / (bid + ".tar")).exists()
    return r


def test_a_clean_batch_is_extracted_verified_and_committed(tree):
    members = {"a.jpg": b"AAA", "b b.png": b"B" * 1000, "жук.jpg": b"cyr", "-dash.jpg": b"d", " lead.jpg": b"s"}
    r = tree.receive("b1", members)
    assert r.code == 0, r.err
    assert r.json() == {"id": "b1", "members": 5, "bytes": sum(map(len, members.values()))}
    assert work_names(tree) == ["b1", "b1.list"]  # the tar copy is gone after extraction
    assert not (tree.incoming / "b1.tar").exists()
    assert tree.run("verify b1", tree.verify_input(members)).code == 0
    c = tree.run("commit b1")
    assert c.code == 0 and c.json()["committed"] == 5
    assert (tree.base / "-dash.jpg").read_bytes() == b"d"
    assert work_names(tree) == ["record.tsv"]


@pytest.mark.parametrize("kind,link", [(tarfile.SYMTYPE, "/etc/passwd"), (tarfile.SYMTYPE, "../x"), (tarfile.LNKTYPE, "other.jpg"),
                                       (tarfile.FIFOTYPE, ""), (tarfile.CHRTYPE, ""), (tarfile.BLKTYPE, ""), (tarfile.DIRTYPE, ""), (tarfile.CONTTYPE, "")])
def test_non_regular_members_are_rejected(tree, kind, link):
    data = make_tar([("ok.jpg", b"1"), (special("evil.jpg", kind, link), None)])
    assert_rejected(tree, "h1", data, "member_not_regular")


def test_symlink_then_a_regular_file_of_the_same_name_never_writes_through_the_link(tree, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    data = make_tar([(special("x.jpg", tarfile.SYMTYPE, str(outside)), None), ("x.jpg", b"PWNED")])
    assert_rejected(tree, "h2", data, "member_not_regular")
    assert outside.read_text() == "keep"


def test_hardlink_to_a_file_in_the_images_directory_is_rejected(tree):
    tree.put("secret.jpg", b"s")
    assert_rejected_but_images_kept(tree, make_tar([(special("l.jpg", tarfile.LNKTYPE, "secret.jpg"), None)]), "member_not_regular")


def assert_rejected_but_images_kept(tree, data, reason):
    tree.upload("h3", data)
    r = tree.run("receive h3")
    assert r.code == 1 and reason in r.err
    assert [p.name for p in tree.base.iterdir()] == ["secret.jpg"]
    assert work_names(tree) == []


def test_sparse_gnu_member_is_rejected(tree):
    info = tarfile.TarInfo("sparse.jpg")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        tar.addfile(info)
    assert_rejected(tree, "h4", buf.getvalue())


def test_pax_sparse_and_unknown_pax_records_are_rejected(tree):
    for key in ("GNU.sparse.major", "SCHILY.xattr.user.k", "LIBARCHIVE.creationtime"):
        info = tarfile.TarInfo("p.jpg")
        info.size = 1
        info.pax_headers = {key: "1"}
        assert_rejected(tree, "h5", make_tar([(info, b"1")]), "pax_header")


@pytest.mark.parametrize("override", ["../evil.jpg", "/etc/cron.d/x", "a/b.jpg", "..", ".hidden", "sub\\x.jpg", "ctl\x01.jpg", "a\x00b.jpg", "x" * 256])
def test_a_pax_path_override_is_validated_as_the_final_name(tree, override):
    info = tarfile.TarInfo("innocent.jpg")
    info.size = 1
    info.pax_headers = {"path": override}
    assert_rejected(tree, "h6", make_tar([(info, b"1")]))


def test_a_valid_pax_override_and_a_long_gnu_name_are_accepted(tree):
    long_name = "n" * 150 + ".jpg"
    info = tarfile.TarInfo("short.jpg")
    info.size = 1
    info.pax_headers = {"path": long_name}
    assert tree.receive("p1", [(info, b"1")]).code == 0  # sent as a PAX header
    assert tree.run("verify p1", tree.verify_input({long_name: b"1"})).code == 0
    tree.upload("g1", make_tar({"g" * 150 + ".jpg": b"2"}, fmt=tarfile.GNU_FORMAT))
    assert tree.run("receive g1").code == 0  # a GNU ././@LongLink member


def test_a_gnu_long_name_over_255_bytes_is_rejected(tree):
    assert_rejected(tree, "g2", make_tar({"g" * 300 + ".jpg": b"2"}, fmt=tarfile.GNU_FORMAT), "name")


@pytest.mark.parametrize("name,reason", [
    ("/abs.jpg", "name_pattern"), ("../up.jpg", "name_pattern"), ("a/b.jpg", "name_pattern"), (".hidden.jpg", "name_pattern"),
    ("back\\slash.jpg", "name_pattern"), ("tab\tname.jpg", "name_pattern"), ("new\nline.jpg", "name_pattern"), ("ctl\x01.jpg", "name_pattern"),
    ("del\x7f.jpg", "name_control"), ("trail.", "name_trailing"), ("trail.jpg ", "name_trailing"), ("CON", "name_reserved"), ("con.txt", "name_reserved"),
    ("Lpt1.jpg", "name_reserved"), ("x" * 256, "name_pattern"), ("ж" * 128 + ".jpg", "name_too_long"), ("lone\ud800.jpg", "name"),
])
def test_name_rule_violations(tree, name, reason):
    try:
        data = make_tar([(name, b"1")])
    except (UnicodeEncodeError, ValueError):
        pytest.skip("tarfile cannot even encode this name")
    assert_rejected(tree, "n1", data, reason)


def test_name_problem_is_the_single_rule():
    for ok in ("a.jpg", "-x.jpg", " s.jpg", "ж.jpg", "\U0001F600.jpg", "x" * 255, "a b", "UPPER.JPG"):
        assert gate.name_problem(ok) is None, ok
    for bad in ("", ".", "..", "a/b", "x" * 256, "a\x00b", "\ud800", "com1", "aux.x.y"):
        assert gate.name_problem(bad), bad


def test_duplicate_names_in_one_archive(tree):
    assert_rejected(tree, "d1", make_tar([("a.jpg", b"1"), ("a.jpg", b"2")]), "name_duplicate")


@pytest.mark.parametrize("a,b", [("A.jpg", "a.jpg"), (unicodedata.normalize("NFC", "é.jpg"), unicodedata.normalize("NFD", "é.jpg")), ("Straße.jpg", "STRASSE.jpg")])
def test_names_that_collide_after_nfc_and_casefold(tree, a, b):
    assert_rejected(tree, "c1", make_tar([(a, b"1"), (b, b"2")]), "name_duplicate")


def test_caps_member_size_count_and_batch_total(tree):
    tree.write_conf(max_member_mb="0.001", max_members="3", max_batch_mb="0.002")
    assert_rejected(tree, "s1", make_tar({"big.jpg": b"x" * 2000}), "member_too_large")  # 2000 bytes > 1048
    assert_rejected(tree, "s2", make_tar({"a%d.jpg" % i: b"x" for i in range(4)}), "too_many_members")
    assert_rejected(tree, "s3", make_tar({"a.jpg": b"x" * 1000, "b.jpg": b"x" * 1000, "c.jpg": b"x" * 1000}), "batch_too_large")


def test_a_huge_member_is_refused_before_its_data_is_read(tree):
    info = tarfile.TarInfo("huge.jpg")
    info.size = 10 * 1024 ** 3  # header only: the data does not exist
    header = info.tobuf(tarfile.PAX_FORMAT)
    assert_rejected(tree, "s4", header, "member_too_large")


def test_an_oversized_upload_file_is_refused_without_extraction(tree):
    tree.write_conf(max_batch_mb="0.001", max_members="1")
    tree.upload("o1", b"\x00" * (3 * 1024 * 1024))
    r = tree.run("receive o1")
    assert r.code == 1 and "upload_too_large" in r.err
    assert work_names(tree) == []


def test_truncated_and_garbage_archives(tree):
    good = make_tar({"a.jpg": b"x" * 5000})
    assert_rejected(tree, "t1", good[:700])
    assert_rejected(tree, "t2", b"this is not a tar file" * 100)


def test_a_missing_upload_and_a_second_receive_of_the_same_batch(tree):
    assert tree.run("receive nothere").code == 1
    assert tree.receive("r1", {"a.jpg": b"1"}).code == 0
    again = tree.receive("r1", {"a.jpg": b"1"})
    assert again.code == 1 and "batch_exists" in again.err
    assert (tree.work / "r1").is_dir()  # the existing batch is untouched and can still be verified and committed
    assert tree.run("verify r1", tree.verify_input({"a.jpg": b"1"})).code == 0
    assert tree.run("commit r1").code == 0


def test_commit_refuses_an_unverified_batch_and_a_tampered_one(tree):
    assert tree.receive("v1", {"a.jpg": b"1"}).code == 0
    r = tree.run("commit v1")
    assert r.code == 1 and "not_verified" in r.err
    assert tree.run("verify v1", tree.verify_input({"a.jpg": b"1"})).code == 0
    (tree.work / "v1" / "a.jpg").write_bytes(b"tampered after verify")
    r = tree.run("commit v1")
    assert r.code == 1 and "changed_since_verify" in r.err
    assert list(tree.base.iterdir()) == [] and not (tree.work / "v1").exists()


def test_verify_mismatch_deletes_the_batch(tree):
    assert tree.receive("v2", {"a.jpg": b"1", "b.jpg": b"2"}).code == 0
    r = tree.run("verify v2", tree.verify_input({"a.jpg": b"1", "b.jpg": b"WRONG"}))
    assert r.code == 1 and "sum_mismatch" in r.err
    assert work_names(tree) == []


def test_verify_with_a_missing_extra_or_replaced_file(tree):
    assert tree.receive("v3", {"a.jpg": b"1"}).code == 0
    assert tree.run("verify v3", tree.verify_input({"a.jpg": b"1", "extra.jpg": b"2"})).code == 1
    assert tree.receive("v4", {"a.jpg": b"1"}).code == 0
    (tree.work / "v4" / "planted.jpg").write_bytes(b"p")
    r = tree.run("verify v4", tree.verify_input({"a.jpg": b"1"}))
    assert r.code == 1 and "directory_content" in r.err


# ------------------------------------------------------------ command and stdin parsing


@pytest.mark.parametrize("command", [
    "", "list; rm -rf /", "list && id", "list | cat", "$(id)", "`id`", "nosuchverb", "LIST", "retire", "restore", "purge",
    "receive", "receive ../x", "receive a/b", "receive a b", "receive " + "a" * 65, "receive .hidden", "commit -x", "verify ", "list\x00x", "list\nlist",
    "receive --names", "hash --bogus-flag", "hash --names --names", "list extra", "sweep now", "fetch-backup --all", "disk x",
])
def test_unknown_verbs_and_command_injection_are_refused(tree, command):
    r = tree.run(command)
    assert r.code == 2, (command, r.err)
    assert list(tree.base.iterdir()) == []


def test_the_retire_restore_and_purge_verbs_do_not_exist():
    assert not {"retire", "restore", "purge"} & set(gate.VERBS)


def test_usage_and_environment_errors(tree):
    assert tree.run("list", env_name="nosuchenv").code == 2
    assert tree.run("list", env_name="../etc").code == 2
    out = tree.environ("list")
    del out["SSH_ORIGINAL_COMMAND"]
    code = gate.main(["g", "general"], out, io.BytesIO(), io.BytesIO(), io.StringIO())
    assert code == 2
    assert gate.main(["g"], tree.environ("list"), io.BytesIO(), io.BytesIO(), io.StringIO()) == 2


def test_missing_config_and_an_unsafe_platform_are_refused(tree, monkeypatch):
    env = tree.environ("list")
    env["MEMESYNC_GATE_CONF"] = str(tree.root / "absent.conf")
    assert gate.main(["g", "general"], env, io.BytesIO(), io.BytesIO(), io.StringIO()) == 2
    monkeypatch.setattr(gate, "HAVE_DIRFD", False)
    env = tree.environ("list")
    del env["MEMESYNC_GATE_ALLOW_NO_DIRFD"]
    err = io.StringIO()
    assert gate.main(["g", "general"], env, io.BytesIO(), io.BytesIO(), err) == 2 and "unsafe_platform" in err.getvalue()


@pytest.mark.skipif(not hasattr(gate.os, "getuid"), reason="POSIX permissions")
def test_a_group_writable_config_is_refused(tree):
    tree.conf_path.chmod(0o664)
    r = tree.run("list")
    assert r.code == 2 and "config_writable" in r.err


@pytest.mark.parametrize("stdin", [b"a\tb.jpg\x00", b"a\nb.jpg\x00", b"\xff\xfe.jpg\x00", b"../x.jpg\x00", b".hid\x00", b"a.jpg\x00a.jpg\x00", b"CON\x00", b"a/b\x00", b"\x00\x00"])
def test_hostile_names_on_stdin_are_refused_before_any_work(tree, stdin):
    tree.put("a.jpg")
    before = (tree.base / "a.jpg").read_bytes()
    r = tree.run("hash --names", stdin)
    assert r.code == 2, (stdin, r.err)
    assert not (tree.work / "record.tsv").exists()
    assert (tree.base / "a.jpg").read_bytes() == before


def test_error_lines_never_carry_raw_control_characters(tree):
    r = tree.run("hash --names", b"evil\nforged: gate ok\x1b[31m.jpg\x00")
    assert r.code == 2
    assert r.err.count("\n") == 1 and "\x1b" not in r.err


def test_record_hash_of_the_verified_batch_matches_what_was_extracted(tree):
    members = {"z.jpg": b"zz"}
    r = tree.promote("m1", members)
    assert r.code == 0
    assert (tree.work / "record.tsv").read_text().split("\t")[1] == sha(b"zz")
