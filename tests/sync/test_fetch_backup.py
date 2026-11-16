"""sync.fetch_backup: the dev-side off-VPS copy of the newest class P dump, against the real gate on a temporary tree."""
import hashlib
import io
import json
import os

import pytest

from sync import fetch_backup as fb
from sync.config import PreconditionError, SyncSettings, VerificationError
from sync.files.transport import LocalTransport
from tests.sync.gate_helpers import ENV_NAME, Tree

DUMP = b"PGDMP-fake-dump-bytes" * 50


def stamp_name(n: int) -> str:
    return f"memes-{ENV_NAME}-classp-202611{n:02d}T030000Z"


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "prod").mkdir()
    return Tree(tmp_path / "prod", free_floor_pct="0")


@pytest.fixture
def settings(tmp_path):
    return SyncSettings(ENV_NAME, {"state_dir": str(tmp_path / "state"), "fetch_backup": {"keep": 2}}, tmp_path)


def put_backup(tree, n, data=DUMP, *, manifest=True, **override):
    base = stamp_name(n)
    (tree.backups / f"{base}.dump").write_bytes(data)
    os.utime(tree.backups / f"{base}.dump", (1000 + n, 1000 + n))
    if manifest:
        doc = {"format": 1, "environment": ENV_NAME, "pg_restore_list": "ok",
               "dump": {"name": f"{base}.dump", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        doc.update(override)
        (tree.backups / f"{base}.meta.json").write_text(json.dumps(doc), encoding="utf-8")
    return base


def transport(tree):
    return LocalTransport(ENV_NAME, tree.conf_path, tree.incoming)


def run(settings, tree):
    return fb.fetch(settings, transport(tree), out=io.StringIO())


def test_fetch_keeps_the_dump_and_its_manifest(tree, settings):
    base = put_backup(tree, 5)
    r = run(settings, tree)
    held = fb.backups_dir(settings)
    assert r["status"] == "fetched" and (held / f"{base}.dump").read_bytes() == DUMP
    assert json.loads((held / f"{base}.meta.json").read_text())["pg_restore_list"] == "ok"
    assert sorted(p.name for p in held.iterdir()) == [f"{base}.dump", f"{base}.meta.json"]  # no .part or .raw.part left


def test_second_fetch_of_the_same_dump_does_not_call_the_dump_verb(tree, settings):
    put_backup(tree, 5)
    run(settings, tree)
    t = transport(tree)
    assert fb.fetch(settings, t, out=io.StringIO())["status"] == "current"
    assert t.calls == ["fetch-backup --meta"]
    assert fb.fetch(settings, t, force=True, out=io.StringIO())["status"] == "fetched"


def test_newest_dump_wins_and_older_ones_are_pruned_to_keep(tree, settings):
    for n in (1, 2, 3):
        put_backup(tree, n)
        run(settings, tree)
    assert [name for name, _ in fb.held(fb.backups_dir(settings), ENV_NAME)] == [stamp_name(3) + ".dump", stamp_name(2) + ".dump"]
    assert not (fb.backups_dir(settings) / (stamp_name(1) + ".meta.json")).exists()


def test_prune_leaves_files_it_does_not_own(tree, settings):
    held = fb.backups_dir(settings, create=True)
    (held / "notes.txt").write_text("mine")
    (held / "memes-it-classp-20260101T000000Z.dump").write_bytes(b"other env")
    for n in (1, 2, 3):
        put_backup(tree, n)
        run(settings, tree)
    assert (held / "notes.txt").exists() and (held / "memes-it-classp-20260101T000000Z.dump").exists()


@pytest.mark.parametrize("override", [{"pg_restore_list": "failed"}, {"environment": "metal"}, {"dump": {"name": "x.dump", "size": 1, "sha256": "0" * 64}},
                                      {"dump": {"name": stamp_name(5) + ".dump", "size": len(DUMP), "sha256": "ab" * 32}},
                                      {"dump": {"name": stamp_name(5) + ".dump", "size": len(DUMP), "sha256": "nothex"}}])
def test_a_dump_the_manifest_does_not_vouch_for_is_not_kept(tree, settings, override):
    put_backup(tree, 5, **override)
    with pytest.raises(VerificationError):
        run(settings, tree)
    assert list(fb.backups_dir(settings).iterdir()) == []


def test_a_dump_without_manifest_is_not_kept(tree, settings):
    put_backup(tree, 5, manifest=False)
    with pytest.raises(VerificationError, match="no manifest"):
        run(settings, tree)
    assert list(fb.backups_dir(settings).iterdir()) == []


def test_no_backup_on_prod_is_a_precondition_failure(tree, settings):
    with pytest.raises(PreconditionError, match="no backup"):
        run(settings, tree)


def test_a_foreign_dump_name_is_refused_before_anything_is_written(tree, settings):
    (tree.backups / "evil.dump").write_bytes(DUMP)
    with pytest.raises(VerificationError, match="not memes-"):
        run(settings, tree)
    assert list(fb.backups_dir(settings).iterdir()) == []


def test_corrupted_transfer_is_caught_by_the_hash(tree, settings):
    base = put_backup(tree, 5)
    flipped = bytearray(DUMP)
    flipped[3] ^= 1
    (tree.backups / f"{base}.dump").write_bytes(bytes(flipped))  # same size, different bytes
    with pytest.raises(VerificationError, match="sha256"):
        run(settings, tree)
    assert list(fb.backups_dir(settings).iterdir()) == []


def test_split_header_rejects_a_missing_or_oversized_header(tmp_path):
    raw = tmp_path / "r"
    raw.write_bytes(b"no newline here")
    with pytest.raises(fb.FetchFailed):
        fb.split_header(raw)
    raw.write_bytes(b"[1]\nbody")
    with pytest.raises(fb.FetchFailed):
        fb.split_header(raw)
    raw.write_bytes(b'{"name": "x"}\nbody')
    assert fb.split_header(raw) == ({"name": "x"}, 14)


def test_main_list_and_fetch_exit_codes(tree, settings, capsys):
    assert fb.main(["--env", ENV_NAME, "--list"], settings=settings) == 0
    assert fb.main(["--env", ENV_NAME], settings=settings, transport_factory=lambda s: transport(tree)) == 2  # no backup yet
    base = put_backup(tree, 5)
    assert fb.main(["--env", ENV_NAME], settings=settings, transport_factory=lambda s: transport(tree)) == 0
    assert fb.main(["--env", ENV_NAME, "--list"], settings=settings) == 0
    assert base in capsys.readouterr().out
    put_backup(tree, 6, manifest=False)
    assert fb.main(["--env", ENV_NAME], settings=settings, transport_factory=lambda s: transport(tree)) == 4
