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


from Storage.models import BatchRun, Image, RunStatus  # noqa: E402


async def test_schema_present_when_everything_exists(db_session):
    await set_version(db_session, rp.chain()[-1].revision)
    status, detail = await rp.schema_matches_models(db_session)
    assert status == rp.OK, detail


async def test_missing_object_with_its_revision_pending_is_a_warning(db_session):
    ids = [r.revision for r in rp.chain()]
    await set_version(db_session, ids[ids.index("d5b8e3a07c41") - 1])
    await db_session.execute(text("DROP TABLE meme_saves"))
    status, detail = await rp.schema_matches_models(db_session)
    assert status == rp.WARN
    assert "meme_saves" in detail and "missing, created by d5b8e3a07c41" in detail


async def test_missing_object_with_its_revision_applied_is_a_failure(db_session):
    await set_version(db_session, rp.chain()[-1].revision)
    await db_session.execute(text("DROP TABLE meme_saves"))
    status, detail = await rp.schema_matches_models(db_session)
    assert status == rp.FAIL
    assert "meme_saves" in detail and "revision applied" in detail


async def test_missing_column_is_named(db_session):
    await set_version(db_session, rp.chain()[-1].revision)
    await db_session.execute(text("ALTER TABLE image_cuts DROP COLUMN failure_reason"))
    status, detail = await rp.schema_matches_models(db_session)
    assert status == rp.FAIL and "image_cuts.failure_reason" in detail


async def test_image_status_counts_lists_counts(db_session):
    db_session.add_all([Image(filename="a.jpg", status="active"), Image(filename="b.jpg", status="active"),
                        Image(filename="c.jpg", status="pending")])
    await db_session.flush()
    status, detail = await rp.image_status_counts(db_session)
    assert status == rp.OK
    assert "active 2" in detail and "pending 1" in detail


async def test_cut_proposed_images_warn(db_session):
    db_session.add(Image(filename="cut.jpg", status="cut_proposed"))
    await db_session.flush()
    status, detail = await rp.image_status_counts(db_session)
    assert status == rp.WARN and "cut_proposed 1" in detail


async def test_started_batch_run_fails_and_names_its_kind(db_session):
    db_session.add(BatchRun(kind="rp_test_kind", trigger="manual", status=str(RunStatus.started)))
    await db_session.flush()
    status, detail = await rp.active_jobs(db_session)
    assert status == rp.FAIL and "rp_test_kind" in detail


async def test_no_started_run_passes(db_session):
    status, _ = await rp.active_jobs(db_session)
    assert status == rp.OK


async def test_run_checks_stops_at_a_writable_role(db_session, tmp_path):
    results, code = await rp.run_checks(db_session, base_path=str(tmp_path), backup_dir=None, min_free_gb=0.0)
    assert code == 2
    assert [r.name for r in results] == ["role_is_read_only"]


async def test_run_checks_runs_everything_for_a_read_only_role(db_session, tmp_path):
    await set_version(db_session, rp.chain()[-1].revision)
    await become_readonly(db_session)
    results, code = await rp.run_checks(db_session, base_path=str(tmp_path), backup_dir=None, min_free_gb=0.0)
    assert [r.name for r in results] == ["role_is_read_only", "revision_vs_head", "schema_matches_models",
                                         "image_status_counts", "filesystem", "active_jobs"]
    assert code == 0, rp.format_report("test", results)


async def test_run_checks_returns_one_when_not_ready(db_session, tmp_path):
    await set_version(db_session, "0123456789ab")
    await become_readonly(db_session)
    _, code = await rp.run_checks(db_session, base_path=str(tmp_path), backup_dir=None, min_free_gb=0.0)
    assert code == 1


async def test_one_failing_check_does_not_hide_the_others(db_session, tmp_path):
    await set_version(db_session, rp.chain()[-1].revision)
    await become_readonly(db_session)
    await db_session.execute(text("RESET ROLE"))
    await db_session.execute(text("REVOKE SELECT ON alembic_version FROM rp_ro"))
    await db_session.execute(text("SET LOCAL ROLE rp_ro"))
    results, code = await rp.run_checks(db_session, base_path=str(tmp_path), backup_dir=None, min_free_gb=0.0)
    by_name = {r.name: r for r in results}
    assert by_name["revision_vs_head"].status == rp.FAIL
    assert by_name["revision_vs_head"].detail.startswith("check failed")
    assert by_name["active_jobs"].status == rp.OK
    assert code == 1
