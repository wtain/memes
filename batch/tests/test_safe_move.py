"""
Unit tests for batch/utils/safe_move.py -- collision-safe file moving (renames with a
numeric suffix instead of silently overwriting), matching test_file_hash.py's
flat-function style. Real tmp_path files throughout -- no mocking needed for pure
filesystem logic.

The two truncation tests monkeypatch MAX_FILENAME_LENGTH down to a small value rather
than creating a real ~255-character filename: Windows' classic 260-character MAX_PATH
applies to the full path, not just the filename component, and pytest's tmp_path is
already a fairly deep path, so a genuinely long filename risks failing at file-creation
time in the test itself (unrelated to the truncation logic under test) rather than
exercising it.
"""
from batch.utils.safe_move import MAX_FILENAME_LENGTH, move_without_overwrite


def _write(path, content: bytes = b"x") -> str:
    path.write_bytes(content)
    return str(path)


def test_moves_as_is_when_no_collision(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a.jpg"
    assert (dest_dir / "a.jpg").exists()
    assert not (src_dir / "a.jpg").exists()


def test_renames_with_suffix_on_single_collision(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg", b"new")
    _write(dest_dir / "a.jpg", b"existing")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a_1.jpg"
    assert (dest_dir / "a_1.jpg").read_bytes() == b"new"
    assert (dest_dir / "a.jpg").read_bytes() == b"existing"  # untouched, not overwritten


def test_increments_suffix_past_multiple_collisions(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg", b"newest")
    _write(dest_dir / "a.jpg", b"first")
    _write(dest_dir / "a_1.jpg", b"second")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a_2.jpg"
    assert (dest_dir / "a_2.jpg").read_bytes() == b"newest"


def test_truncates_long_filename_even_without_collision(tmp_path, monkeypatch):
    import batch.utils.safe_move as module
    monkeypatch.setattr(module, "MAX_FILENAME_LENGTH", 20)
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    long_name = "a" * 30 + ".jpg"  # comfortably exceeds the patched 20-char limit
    src = _write(src_dir / long_name)

    result = module.move_without_overwrite(src, str(dest_dir))

    assert len(result) <= 20
    assert result.endswith(".jpg")
    assert (dest_dir / result).exists()


def test_truncated_name_still_gets_a_suffix_on_collision(tmp_path, monkeypatch):
    import batch.utils.safe_move as module
    monkeypatch.setattr(module, "MAX_FILENAME_LENGTH", 20)
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    ext = ".jpg"
    max_stem_length = 20 - len(ext) - module._SUFFIX_RESERVE
    long_stem = "c" * (max_stem_length + 10)
    src = _write(src_dir / f"{long_stem}{ext}", b"new")
    truncated_stem = long_stem[:max_stem_length]
    _write(dest_dir / f"{truncated_stem}{ext}", b"existing")

    result = module.move_without_overwrite(src, str(dest_dir))

    assert result == f"{truncated_stem}_1{ext}"
    assert (dest_dir / result).read_bytes() == b"new"
    assert (dest_dir / f"{truncated_stem}{ext}").read_bytes() == b"existing"
