"""File side effects of a recut request, tied to the transaction (screenshot recut R6). Requires a live PostgreSQL instance."""
import pytest
from sqlalchemy import text

from Backend.app.services.recut_files import file_effects, remove_quietly


@pytest.mark.asyncio(loop_scope="session")
async def test_on_commit_runs_after_commit_and_not_before(db_session):
    calls = []
    file_effects(db_session, on_commit=lambda: calls.append("commit"), on_rollback=lambda: calls.append("rollback"))
    await db_session.flush()
    assert calls == []

    await db_session.commit()

    assert calls == ["commit"]


@pytest.mark.asyncio(loop_scope="session")
async def test_on_rollback_runs_after_rollback_and_the_commit_hook_is_cancelled(db_session):
    calls = []
    file_effects(db_session, on_commit=lambda: calls.append("commit"), on_rollback=lambda: calls.append("rollback"))
    await db_session.execute(text("SELECT 1"))  # a request has always done work by the time it fails

    await db_session.rollback()
    assert calls == ["rollback"]

    await db_session.commit()
    assert calls == ["rollback"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_savepoint_rollback_does_not_fire_the_rollback_hook(db_session):
    calls = []
    file_effects(db_session, on_rollback=lambda: calls.append("rollback"))

    savepoint = await db_session.begin_nested()
    await savepoint.rollback()
    assert calls == []

    await db_session.rollback()
    assert calls == ["rollback"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failing_callback_is_swallowed(db_session):
    def boom():
        raise RuntimeError("disk gone")

    file_effects(db_session, on_commit=boom)

    await db_session.commit()  # must not raise


def test_remove_quietly_ignores_a_missing_file(tmp_path):
    remove_quietly(str(tmp_path / "missing.jpg"))


def test_remove_quietly_removes_an_existing_file(tmp_path):
    path = tmp_path / "there.jpg"
    path.write_bytes(b"x")

    remove_quietly(str(path))

    assert not path.exists()
