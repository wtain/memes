"""Discovery report command against the test database, through a real SELECT-only role."""
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import insert

from Storage.models import Image, PopularityAbsorption, SearchHistory
from tools import discovery_report as cli
from tests.integration.test_rollout_preflight import become_readonly

pytestmark = pytest.mark.asyncio(loop_scope="session")


def parse(*argv):
    return cli.build_parser().parse_args(list(argv))


async def seed_re_drop(session, filename="quokka.jpg"):
    image = Image(filename=filename, status="active")
    session.add(image)
    await session.flush()
    await session.execute(insert(PopularityAbsorption).values(
        kind="absorb", source="hash_cross_corpus", winner_image_id=image.id, loser_image_id=None, weight=1))
    return image


async def test_a_writable_role_is_refused(db_session, monkeypatch, capsys):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("the report must not run with a writable role")

    monkeypatch.setattr(cli, "memes_report", must_not_run)
    code = await cli._run_session(db_session, parse("memes", "--env", "general"))
    captured = capsys.readouterr()
    assert code == 2 and "can write" in captured.err and captured.out == ""


async def test_a_readonly_role_runs_the_memes_view(db_session, capsys):
    await seed_re_drop(db_session)
    await become_readonly(db_session)
    code = await cli._run_session(db_session, parse("memes", "--env", "general"))
    out = capsys.readouterr().out
    assert code == 0
    assert "Discovery report: memes (general, window 30 days" in out and "quokka.jpg" in out
    assert "hash_cross_corpus:1" in out


async def test_the_queries_view_prints_the_text_to_stdout_only(db_session, capsys, caplog, tmp_path):
    db_session.add(SearchHistory(searched_at=datetime.now(timezone.utc), query="quokka nightmare", client="web",
                                 result_count=0, kind="search"))
    await db_session.flush()
    await become_readonly(db_session)
    csv_path = tmp_path / "queries.csv"
    with caplog.at_level(logging.DEBUG):
        code = await cli._run_session(db_session, parse("queries", "--env", "general", "--csv", str(csv_path)))
    captured = capsys.readouterr()
    assert code == 0
    assert "quokka nightmare" in captured.out and "quokka nightmare" not in caplog.text
    assert "contains search text" in captured.err
    assert "quokka nightmare" in csv_path.read_text(encoding="utf-8")


async def test_no_csv_is_written_and_no_reminder_printed_without_the_option(db_session, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await become_readonly(db_session)
    await cli._run_session(db_session, parse("queries", "--env", "general"))
    assert "contains search text" not in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


async def test_zero_rows_exits_0_and_still_writes_the_csv_header(db_session, capsys, tmp_path):
    await become_readonly(db_session)
    csv_path = tmp_path / "memes.csv"
    code = await cli._run_session(db_session, parse("memes", "--env", "general", "--limit", "5",
                                                    "--csv", str(csv_path)))
    assert code == 0 and "0 rows" in capsys.readouterr().out
    assert csv_path.read_text(encoding="utf-8").startswith("id,filename,redrops")


async def test_a_bad_combination_is_a_message_not_a_traceback(db_session, capsys):
    from sqlalchemy import text
    await db_session.execute(text("DROP TABLE meme_impressions"))
    await become_readonly(db_session)
    code = await cli._run_session(db_session, parse("memes", "--env", "general", "--max-impressions", "1"))
    assert code == 2 and "meme_impressions" in capsys.readouterr().err
