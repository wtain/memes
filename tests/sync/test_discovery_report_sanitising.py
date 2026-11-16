"""tools/discovery_report.py cleans untrusted query text before printing and writing CSV (P5a-T8). No database."""
import csv
from datetime import datetime, timezone

from repository.discovery_report import QueryRow
from tools import discovery_report as cli


def test_ansi_osc_and_control_characters_are_stripped():
    hostile = ("ok\x1b[31mred\x1b[0m\x1b]0;owned title\x07\x1b]8;;http://evil\x1b\\link\x1b]8;;\x1b\\tail"
               "\x00\x07\x08\x7f‮evil⁦x")
    assert cli.sanitize_text(hostile) == "okredlinktailevilx"


def test_tabs_and_newlines_become_spaces_so_a_row_stays_one_line():
    assert cli.sanitize_text("a\nb\tc\r\nd") == "a b c  d"


def test_plain_text_in_any_script_is_untouched():
    for text in ("hello world", "привет мир", "¿qué tal? ñ", "emoji 😀 and 日本語"):
        assert cli.sanitize_text(text) == text


def test_csv_cells_that_a_spreadsheet_reads_as_formulas_are_prefixed():
    assert [cli.csv_safe(c) for c in ("=1+1", "+1", "-1", "@SUM(A1)", "plain", "", "a=b")] == [
        "'=1+1", "'+1", "'-1", "'@SUM(A1)", "plain", "", "a=b"]


def _row(query):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return QueryRow(query=query, tags="topic:\x1b[1mwork", kind="search", times=2, first_at=now, last_at=now, min_results=0,
                    avg_results=0.0, web=2, android=0, degraded=0, fallbacks="")


def test_tables_are_cleaned_before_printing():
    headers, cells = cli.query_table([_row("\x1b[2J\x1b]0;x\x07=cmd|' /C calc'!A0")])
    printed = cli.format_table(headers, cells)
    assert "\x1b" not in printed and "\x07" not in printed
    assert "=cmd|' /C calc'!A0" in printed and "topic:work" in printed


def test_csv_file_is_cleaned_and_formula_safe(tmp_path):
    headers, cells = cli.query_table([_row('=HYPERLINK("http://evil")'), _row("\x1b[31m-2+3")])
    path = tmp_path / "out.csv"
    cli.write_csv(path, headers, cells)
    rows = list(csv.reader(path.open(encoding="utf-8", newline="")))
    assert rows[1][0] == "'=HYPERLINK(\"http://evil\")"
    assert rows[2][0] == "'-2+3"
    assert "\x1b" not in path.read_text(encoding="utf-8")
