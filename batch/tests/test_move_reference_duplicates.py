from batch.move_reference_duplicates import _index_reference_dir
from metrics.listener import SimpleMetricsListener


def test_index_reference_dir_skips_unhashable_file_and_records_failure(tmp_path, monkeypatch):
    good = tmp_path / "good.jpg"
    good.write_bytes(b"content")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"content")

    import batch.move_reference_duplicates as module

    def fake_sha256_file(path):
        if path == str(bad):
            raise OSError(22, "Invalid argument")
        return "deadbeef"

    monkeypatch.setattr(module, "sha256_file", fake_sha256_file)

    metrics = SimpleMetricsListener()
    failures = []
    index = _index_reference_dir(str(tmp_path), metrics, failures)

    assert index == {"deadbeef": [str(good)]}
    assert failures == [("bad.jpg", "[Errno 22] Invalid argument")]