"""rollout-prep preflight: the checks that need no database (tables tie, filesystem, base path, report)."""
from Storage.models import Base
from tools import rollout_preflight as rp


def test_every_expected_name_exists_in_the_models_and_the_chain():
    ids = {r.revision for r in rp.chain()}
    for table, columns, revision in rp.EXPECTED:
        assert table in Base.metadata.tables, table
        for column in columns:
            assert column in Base.metadata.tables[table].columns, (table, column)
        assert revision in ids, revision


def test_filesystem_passes_for_a_temp_directory(tmp_path):
    status, detail = rp.filesystem(str(tmp_path), str(tmp_path), 0.0)
    assert status == rp.OK, detail
    assert list(tmp_path.iterdir()) == []  # the probe file is removed


def test_filesystem_fails_for_a_missing_path(tmp_path):
    status, detail = rp.filesystem(str(tmp_path / "nope"), None, 0.0)
    assert status == rp.FAIL and "does not exist" in detail


def test_filesystem_fails_for_a_file_instead_of_a_directory(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    assert rp.filesystem(str(f), None, 0.0)[0] == rp.FAIL


def test_filesystem_fails_when_free_space_is_below_the_threshold(tmp_path):
    status, detail = rp.filesystem(str(tmp_path), None, 10 ** 9)
    assert status == rp.FAIL and "free" in detail


def test_filesystem_fails_when_pre_cuts_is_a_file(tmp_path):
    (tmp_path / "pre-cuts").write_text("x")
    assert rp.filesystem(str(tmp_path), None, 0.0)[0] == rp.FAIL


def test_filesystem_without_a_base_path_fails():
    assert rp.filesystem(None, None, 0.0)[0] == rp.FAIL


def test_read_base_path_handles_quotes_spaces_and_a_missing_key(tmp_path):
    env_dir = tmp_path / "environments"
    env_dir.mkdir()
    (env_dir / ".env.general").write_text('DATABASE_URL=x\nBASE_PATH="D:/memes lib"\n', encoding="utf-8")
    (env_dir / ".env.it").write_text("DATABASE_URL=x\n", encoding="utf-8")
    assert rp.read_base_path("general", tmp_path) == "D:/memes lib"
    assert rp.read_base_path("it", tmp_path) is None
    assert rp.read_base_path("metal", tmp_path) is None  # no file


def test_report_never_contains_the_password():
    url = "postgresql+asyncpg://ocr_readonly:s3cretpw@localhost:5434/ocrdb"
    results = [rp.Result("connection", rp.FAIL, f"could not connect to {url}: password s3cretpw rejected")]
    report = rp.format_report("general", results, url)
    assert "s3cretpw" not in report and "ocr_readonly:" not in report
    assert report.endswith("NOT READY")


def test_report_lists_status_and_continuation_lines():
    report = rp.format_report("it", [rp.Result("a", rp.OK, "fine"), rp.Result("b", rp.WARN, "x\ny")])
    assert "[OK  ] a: fine" in report and "[WARN] b: x" in report and "        y" in report
    assert report.endswith("READY")


def test_main_without_the_read_only_url_exits_two(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    assert rp.main(["--env", "general", "--base-path", "."]) == 2
    assert "DATABASE_URL_READONLY" in capsys.readouterr().err


def test_target_names_host_port_and_database_without_the_password():
    status, detail = rp.target_check("general", "postgresql+asyncpg://ocr_readonly:s3cretpw@localhost:5434/ocrdb")
    assert status == rp.OK
    assert "localhost:5434/ocrdb" in detail and "s3cretpw" not in detail


def test_target_warns_when_the_port_belongs_to_another_environment():
    status, detail = rp.target_check("metal", "postgresql+asyncpg://u:p@localhost:5434/ocrdb")
    assert status == rp.WARN
    assert "5432" in detail and "5434" in detail
