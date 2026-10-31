"""rollout-prep: the read-only preflight (tools/rollout_preflight.py). Runs on the test database inside the
rolled-back test transaction; a read-only role is created and entered with SET LOCAL ROLE inside it."""
import pytest
from sqlalchemy import text

from tools import rollout_preflight as rp

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def become_readonly(session, name="rp_ro"):
    """Create a SELECT-only role inside the test transaction and switch to it (call after all setup writes)."""
    await session.execute(text(f"CREATE ROLE {name} NOLOGIN"))
    await session.execute(text(f"GRANT USAGE ON SCHEMA public TO {name}"))
    await session.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {name}"))
    await session.execute(text(f"SET LOCAL ROLE {name}"))


async def set_version(session, version):
    await session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)"))
    await session.execute(text("DELETE FROM alembic_version"))
    if version is not None:
        await session.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})


async def test_role_check_fails_for_a_writable_role(db_session):
    status, detail = await rp.role_is_read_only(db_session)
    assert status == rp.FAIL
    assert "can write" in detail


async def test_role_check_passes_for_a_select_only_role(db_session):
    await become_readonly(db_session)
    status, detail = await rp.role_is_read_only(db_session)
    assert status == rp.OK
    assert "rp_ro" in detail


async def test_unversioned_database_is_reported_with_every_revision_pending(db_session):
    await db_session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    status, detail = await rp.revision_vs_head(db_session)
    assert status == rp.FAIL
    assert "unversioned" in detail
    assert rp.FIRST_RECUT_REVISION in detail


async def test_empty_alembic_version_is_unversioned_too(db_session):
    await set_version(db_session, None)
    status, detail = await rp.revision_vs_head(db_session)
    assert status == rp.FAIL and "unversioned" in detail


async def test_at_head_is_ok(db_session):
    head = rp.chain()[-1].revision
    await set_version(db_session, head)
    status, detail = await rp.revision_vs_head(db_session)
    assert status == rp.OK
    assert head in detail


async def test_older_revision_lists_the_pending_in_chain_order_with_labels(db_session):
    revs = rp.chain()
    ids = [r.revision for r in revs]
    before_recut = ids[ids.index(rp.FIRST_RECUT_REVISION) - 1]
    await set_version(db_session, before_recut)
    status, detail = await rp.revision_vs_head(db_session)
    assert status == rp.FAIL
    listed = [line.split()[0] for line in detail.splitlines()[1:]]
    assert listed == ids[ids.index(rp.FIRST_RECUT_REVISION):]
    assert "popularity" in detail and "saves" in detail


async def test_a_revision_the_repo_does_not_know_fails(db_session):
    await set_version(db_session, "0123456789ab")
    status, detail = await rp.revision_vs_head(db_session)
    assert status == rp.FAIL
    assert "0123456789ab" in detail and "not in this repository" in detail


async def test_several_version_rows_fail(db_session):
    head = rp.chain()[-1].revision
    await set_version(db_session, head)
    await db_session.execute(text("INSERT INTO alembic_version VALUES ('0123456789ab')"))
    status, _ = await rp.revision_vs_head(db_session)
    assert status == rp.FAIL
