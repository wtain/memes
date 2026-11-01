"""Discovery report command, parts that need no database (options, formatting, CSV, refusal without a URL)."""
import csv
import uuid
from datetime import datetime, timezone

import pytest

from repository.discovery_report import MemeRow, QueryRow
from tools import discovery_report as cli


def test_parser_defaults_and_choices():
    memes = cli.build_parser().parse_args(["memes", "--env", "general"])
    assert (memes.command, memes.env, memes.min_redrops, memes.limit, memes.sort) == ("memes", "general", 1, 50, "per_100")
    assert memes.sources is None and memes.max_impressions is None and memes.csv is None
    assert memes.window_days == 30
    queries = cli.build_parser().parse_args(["queries", "--env", "it"])
    assert (queries.kind, queries.max_results, queries.min_times, queries.sort, queries.limit) == (
        "search", 3, 1, "times", 50)
    assert queries.include_degraded is False and queries.since_days is None


@pytest.mark.parametrize("argv", [
    ["memes"],
    ["memes", "--env", "nope"],
    ["memes", "--env", "general", "--sort", "nope"],
    ["memes", "--env", "general", "--sources", "hash_cross_corpus,nope"],
    ["memes", "--env", "general", "--limit", "0"],
    ["memes", "--env", "general", "--window-days", "0"],
    ["queries", "--env", "general", "--kind", "nope"],
    ["queries", "--env", "general", "--sort", "nope"],
    ["queries", "--env", "general", "--min-times", "0"],
    ["queries", "--env", "general", "--max-results", "-1"],
])
def test_bad_options_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_sources_are_split_and_validated():
    args = cli.build_parser().parse_args(["memes", "--env", "general", "--sources", "recut,hash_cross_corpus"])
    assert args.sources == ("recut", "hash_cross_corpus")


def _meme(**overrides):
    values = dict(image_id=uuid.UUID(int=1), filename="a.jpg", redrops=3, redrops_window=2,
                  sources={"recut": 1, "ingest_tier_a": 2}, popularity=4, saves=1, impressions=10,
                  impressions_window=5, last_back=datetime(2026, 11, 1, 12, 30, tzinfo=timezone.utc), per_100=40.0)
    values.update(overrides)
    return MemeRow(**values)


def test_meme_table_formats_inf_na_and_sources():
    headers, rows = cli.meme_table([_meme(per_100=float("inf"))], True)
    cells = dict(zip(headers, rows[0]))
    assert cells["per_100"] == "inf"
    assert cells["sources"] == "ingest_tier_a:2,recut:1"
    assert cells["last_back"] == "2026-11-01 12:30"
    headers, rows = cli.meme_table([_meme(impressions=None, impressions_window=None, per_100=None)], False)
    cells = dict(zip(headers, rows[0]))
    assert (cells["impressions"], cells["impressions_window"], cells["per_100"]) == ("n/a", "n/a", "n/a")
    _, rows = cli.meme_table([_meme(per_100=40.04)], True)
    assert rows[0][-1] == "40.0"


def _query(**overrides):
    values = dict(query="funny cat", tags="", kind="search", times=2,
                  first_at=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
                  last_at=datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc), min_results=0, avg_results=0.5,
                  web=1, android=1, degraded=0, fallbacks="")
    values.update(overrides)
    return QueryRow(**values)


def test_query_table_shows_no_text_for_an_empty_query():
    headers, rows = cli.query_table([_query(query="", tags="animal:cat")])
    cells = dict(zip(headers, rows[0]))
    assert cells["query"] == "(no text)" and cells["tags"] == "animal:cat" and cells["avg_results"] == "0.5"


def test_misses_parser_defaults_and_options():
    args = cli.build_parser().parse_args(["misses", "--env", "metal"])
    assert (args.command, args.gap_minutes, args.kind, args.sort, args.limit, args.min_times) == (
        "misses", 5, "search", "times", 50, 1)
    assert args.explicit_only is False and args.since_days is None and args.csv is None
    args = cli.build_parser().parse_args(["misses", "--env", "metal", "--explicit-only", "--gap-minutes", "10",
                                          "--sort", "explicit"])
    assert args.explicit_only is True and args.gap_minutes == 10 and args.sort == "explicit"


@pytest.mark.parametrize("argv", [
    ["misses", "--env", "general", "--gap-minutes", "0"],
    ["misses", "--env", "general", "--sort", "nope"],
    ["misses", "--env", "general", "--kind", "nope"],
    ["misses", "--env", "general", "--min-times", "0"],
])
def test_bad_misses_options_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_miss_table_shows_no_text_for_an_empty_query_and_one_decimal():
    from repository.discovery_report import MissRow
    row = MissRow(query="", tags="animal:cat", kind="search", times=3, explicit=1, searches=2.0,
                  first_at=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
                  last_at=datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc), web=2, android=1)
    headers, rows = cli.miss_table([row])
    cells = dict(zip(headers, rows[0]))
    assert headers == ["query", "tags", "kind", "times", "explicit", "searches", "first_at", "last_at", "web",
                       "android"]
    assert cells["query"] == "(no text)" and cells["searches"] == "2.0" and cells["explicit"] == "1"


def test_format_table_aligns_columns_and_handles_zero_rows():
    text = cli.format_table(["id", "name"], [["1", "longer name"], ["22", "x"]])
    lines = text.splitlines()
    assert lines[0].startswith("id") and set(lines[1]) <= {"-", " "}
    assert len({line.index("longer name") if "longer" in line else line.index("x") if line.endswith("x") else 4
                for line in lines[2:]}) == 1
    assert cli.format_table(["id", "name"], []).splitlines()[0].startswith("id")
    assert len(cli.format_table(["id", "name"], []).splitlines()) == 2


def test_write_csv_writes_header_and_rows_utf8(tmp_path):
    path = tmp_path / "out.csv"
    cli.write_csv(path, ["query", "times"], [["смешной кот", "2"]])
    with open(path, encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [["query", "times"], ["смешной кот", "2"]]
    empty = tmp_path / "empty.csv"
    cli.write_csv(empty, ["query", "times"], [])
    with open(empty, encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [["query", "times"]]


def test_output_is_utf8_even_when_the_stream_is_not():
    import io
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")  # a redirected Windows stdout
    cli.configure_output(stream)
    stream.write("смешной кот")
    stream.flush()
    assert raw.getvalue().decode("utf-8") == "смешной кот"


def test_main_without_the_readonly_url_exits_2_and_prints_no_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    assert cli.main(["memes", "--env", "general"]) == 2
    err = capsys.readouterr().err
    assert "DATABASE_URL_READONLY" in err and "postgresql" not in err
