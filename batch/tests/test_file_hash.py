from batch.utils.file_hash import files_are_identical, sha256_file


def _write(path, content: bytes):
    path.write_bytes(content)
    return str(path)


def test_sha256_file_is_deterministic_and_content_sensitive(tmp_path):
    a = _write(tmp_path / "a.bin", b"same content")
    b = _write(tmp_path / "b.bin", b"same content")
    c = _write(tmp_path / "c.bin", b"different content")

    assert sha256_file(a) == sha256_file(b)
    assert sha256_file(a) != sha256_file(c)


def test_files_are_identical_true_for_matching_content(tmp_path):
    a = _write(tmp_path / "a.bin", b"x" * 200_000)  # spans multiple 64KB chunks
    b = _write(tmp_path / "b.bin", b"x" * 200_000)

    assert files_are_identical([a, b]) is True


def test_files_are_identical_false_for_differing_content(tmp_path):
    a = _write(tmp_path / "a.bin", b"x" * 200_000 + b"A")
    b = _write(tmp_path / "b.bin", b"x" * 200_000 + b"B")

    assert files_are_identical([a, b]) is False


def test_files_are_identical_false_for_differing_length(tmp_path):
    a = _write(tmp_path / "a.bin", b"short")
    b = _write(tmp_path / "b.bin", b"short but longer")

    assert files_are_identical([a, b]) is False