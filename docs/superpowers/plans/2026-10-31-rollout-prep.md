# Rollout prep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only `tools/rollout_preflight.py` that says whether an environment is ready for the screenshot-recut migrations, plus the runbook `docs/batches/use-cases/screenshot-recut-rollout.md`, with a doc guard that keeps the runbook's migration list equal to the alembic chain. Nothing runs against a live database.

**Architecture:** One standalone module (no `Storage.db` import) with six small check functions that each take an `AsyncSession` and return `(status, detail)`. `run_checks` wraps every check in a savepoint, so one failing check cannot hide the others, and gates on the role check first. The alembic head and pending list come from `ScriptDirectory` on `Storage/alembic`; the database side is only `alembic_version`. The CLI builds its own engine on `DATABASE_URL_READONLY` with `default_transaction_read_only=on`.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy 2 async + asyncpg, alembic `ScriptDirectory`, pytest (`tests/integration/` on the `ocrdb_test` database, `tests/docs/` without a database).

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-31-rollout-prep-design.md`

## Global Constraints

- Nothing in this project touches a live database; the tests use `ocrdb_test` only (spec decision 1).
- `DATABASE_URL_READONLY` is read from the shell only, never from an env file, and never printed (spec section 1). The only env-file read is the text line `BASE_PATH=` of `environments/.env.<env>`; its database URL is never read.
- A writable role stops the run with exit 2 before any other read (decision 3). Exit codes: 0 ready, 1 not ready, 2 usage or connection error or writable role (decision 6).
- The report is a plain checklist with `OK` / `WARN` / `FAIL`, never containing the URL or the password (decision 6).
- The only write anywhere is the probe file in `BASE_PATH` (created and removed).
- The head and the pending list come from the repo's alembic script directory; a database revision unknown to the repo is a failure (decision 4).
- Environment order in the runbook is `general`, then `metal`, then `it`; stop at the first failing step (decision 8).
- Rollback is the dump taken at the start; `alembic downgrade` only right after the upgrade and before anything new was written (decision 7).
- Test commands: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest ...`; `tests/integration/` needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`; test roots are run as separate commands. Integration async tests carry `@pytest.mark.asyncio(loop_scope="session")`. The shared test DB may deadlock: rerun.
- Do not touch the uncommitted `Storage/Readme.md` change in the main checkout.

## Review Focus

1. A database with no `alembic_version` table (or an empty one): reported as unversioned with every revision pending, never a crash (Task 1 test).
2. A role that can write: `run_checks` returns only the role result and code 2 (Task 3 test).
3. A connection or check error whose text contains the database URL: the report must not show the password (Task 3 test).
4. One check raising (for example the role cannot read `alembic_version`): the other checks still run and the failed one shows as `FAIL` (Task 3 test).
5. `environments/.env.<env>` with a quoted `BASE_PATH`, with spaces, or without the key: parsed or reported, never a crash (Task 3 test).

## File Structure

- Create `tools/rollout_preflight.py` — the checks, the report, the CLI. One responsibility: decide readiness, read-only.
- Create `tests/integration/test_rollout_preflight.py` — all preflight tests that need the database, plus the pure ones (formatter, base path, filesystem).
- Create `docs/batches/use-cases/screenshot-recut-rollout.md` — the runbook.
- Create `tests/docs/test_rollout_runbook.py` — the doc guard.
- Modify `docs/batches/use-cases/screenshot-recut.md`, the R7 spec paragraph, `docs/batches/inventory.md`, `CLAUDE.md`, the tracker.

---

### Task 1: Core, role gate and revision check

**Files:**
- Create: `tools/rollout_preflight.py`
- Create: `tests/integration/test_rollout_preflight.py`

**Interfaces:**
- Produces (used by Tasks 2 to 3):
  - `OK, WARN, FAIL: str` constants.
  - `@dataclass(frozen=True) Result(name: str, status: str, detail: str)`.
  - `REVISION_LABELS: dict[str, str]`, `FIRST_RECUT_REVISION = "b8d4f1a6c935"`, `ALEMBIC_DIR: Path`.
  - `chain() -> list[Script]` (oldest first), `class UnknownRevision(Exception)`, `pending_after(current: str | None, revs) -> list`.
  - `async current_revisions(session) -> list[str]`.
  - `async role_is_read_only(session) -> tuple[str, str]`.
  - `async revision_vs_head(session, revs=None) -> tuple[str, str]`.
  - `scrub(text, url) -> str`, `format_report(env, results, url=None) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
"""rollout-prep: the read-only preflight (tools/rollout_preflight.py). Runs on the test database inside the
rolled-back test transaction; a read-only role is created and entered with SET LOCAL ROLE inside it."""
import pytest
from sqlalchemy import text

from tools import rollout_preflight as rp

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def become_readonly(session, name="rp_ro"):
    """Create a SELECT-only role inside the test transaction and switch to it (call after all setup writes)."""
    await session.execute(text(f"CREATE ROLE {name} NOLOGIN"))
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_rollout_preflight.py -v`
Expected: collection error `ImportError: cannot import name 'rollout_preflight' from 'tools'`.

- [ ] **Step 3: Write the module**

```python
"""Read-only readiness check before the screenshot-recut rollout (docs/batches/use-cases/screenshot-recut-rollout.md).

Reads DATABASE_URL_READONLY from the shell, opens its own short-lived read-only connection and reports OK / WARN / FAIL per
check. It never imports Storage.db, never writes to the database (the only write anywhere is a probe file in BASE_PATH)
and never prints the database URL. Design: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-31-rollout-prep-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[1]
ALEMBIC_DIR = REPO / "Storage" / "alembic"

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# The recut rollout starts here; the migrations after it up to the head are what a behind environment receives.
FIRST_RECUT_REVISION = "b8d4f1a6c935"
REVISION_LABELS = {
    "b8d4f1a6c935": "R1 image_cuts",
    "c5e2a9d7b140": "M1 process_events",
    "d7a3b9e2c451": "R3a failure_reason",
    "a4f7c1e92b68": "popularity (same upgrade)",
    "c8e2f5a1d093": "popularity adjustment source (same upgrade)",
    "e9c4a7b3d812": "R6 detected box",
    "d5b8e3a07c41": "saves meme_saves (same upgrade)",
}


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str


class UnknownRevision(Exception):
    """The database is at a revision the repository's alembic chain does not contain."""


def chain() -> list:
    """The repository's revisions, oldest first."""
    return list(reversed(list(ScriptDirectory(str(ALEMBIC_DIR)).walk_revisions())))


def pending_after(current: str | None, revs: list) -> list:
    ids = [r.revision for r in revs]
    if current is None:
        return list(revs)
    if current not in ids:
        raise UnknownRevision(current)
    return revs[ids.index(current) + 1:]


async def current_revisions(session) -> list[str]:
    if (await session.execute(text("SELECT to_regclass('public.alembic_version')"))).scalar() is None:
        return []
    return list((await session.execute(text("SELECT version_num FROM alembic_version"))).scalars())


async def role_is_read_only(session) -> tuple[str, str]:
    row = (await session.execute(text(
        "SELECT current_user, has_table_privilege(current_user, 'images', 'INSERT,UPDATE,DELETE,TRUNCATE')"
    ))).one()
    user, can_write = row[0], row[1]
    if can_write:
        return FAIL, f"role {user} can write; use the read-only URL (DATABASE_URL_READONLY, role ocr_readonly)"
    return OK, f"role {user} cannot write"


async def revision_vs_head(session, revs: list | None = None) -> tuple[str, str]:
    revs = revs if revs is not None else chain()
    current = await current_revisions(session)
    if len(current) > 1:
        return FAIL, f"alembic_version holds {len(current)} rows ({', '.join(current)}); fix before migrating"
    cur = current[0] if current else None
    try:
        pending = pending_after(cur, revs)
    except UnknownRevision:
        return FAIL, f"database revision {cur} is not in this repository's chain; wrong code checked out?"
    head = revs[-1].revision
    if not pending:
        return OK, f"at head {head}"
    where = f"at {cur}" if cur else "unversioned (no alembic_version row)"
    lines = [f"{where}, head is {head}; {len(pending)} pending:"]
    lines += [f"{r.revision}  {REVISION_LABELS.get(r.revision, '')}  {r.doc or ''}".rstrip() for r in pending]
    return FAIL, "\n".join(lines)


def scrub(value: str, url: str | None) -> str:
    """Remove the database URL and its password from text that is about to be shown."""
    if not url:
        return value
    out = value.replace(url, "<database url>")
    try:
        password = make_url(url).password
    except Exception:
        password = None
    return out.replace(password, "***") if password else out


def format_report(env: str, results: list[Result], url: str | None = None) -> str:
    lines = [f"Rollout preflight: {env}"]
    for r in results:
        first, *rest = r.detail.split("\n")
        lines.append(f"[{r.status:<4}] {r.name}: {first}")
        lines += [f"        {line}" for line in rest]
    lines.append("NOT READY" if any(r.status == FAIL for r in results) else "READY")
    return scrub("\n".join(lines), url)
```

Also create `tools/__init__.py` only if it does not exist (it does: `ls tools/__init__.py`).

- [ ] **Step 4: Run to verify they pass**

Run: same command as Step 2.
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/rollout_preflight.py tests/integration/test_rollout_preflight.py
git commit -m "feat(rollout): preflight core, role gate and revision check"
```

---

### Task 2: Schema, status counts and active jobs

**Files:**
- Modify: `tools/rollout_preflight.py`
- Modify: `tests/integration/test_rollout_preflight.py`

**Interfaces:**
- Consumes: Task 1's `OK/WARN/FAIL`, `chain`, `current_revisions`, `pending_after`, `UnknownRevision`.
- Produces: `EXPECTED: tuple[tuple[str, tuple[str, ...], str], ...]`, `async schema_matches_models(session, revs=None) -> tuple[str, str]`, `async image_status_counts(session) -> tuple[str, str]`, `async active_jobs(session) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from Storage.models import Base, BatchRun, Image, RunStatus


def test_every_expected_name_exists_in_the_models_and_the_chain():
    ids = {r.revision for r in rp.chain()}
    for table, columns, revision in rp.EXPECTED:
        assert table in Base.metadata.tables, table
        for column in columns:
            assert column in Base.metadata.tables[table].columns, (table, column)
        assert revision in ids, revision


async def test_schema_present_when_everything_exists(db_session):
    head = rp.chain()[-1].revision
    await set_version(db_session, head)
    status, detail = await rp.schema_matches_models(db_session)
    assert status == rp.OK, detail


async def test_missing_object_with_its_revision_pending_is_a_warning(db_session):
    revs = rp.chain()
    ids = [r.revision for r in revs]
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
```

(If the shared test DB already has a started `BatchRun`, the last test is environment-dependent; the `db_session` fixture rolls back, and no test commits one. Rerun if another session is mid-test.)

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL=... pytest tests/integration/test_rollout_preflight.py -v`
Expected: the new tests fail with `AttributeError: module 'tools.rollout_preflight' has no attribute 'EXPECTED'` (or `schema_matches_models`, etc.); the Task 1 tests still pass.

- [ ] **Step 3: Implement** (append to the module)

```python
# (table, columns, revision that creates them). Revisions that only change a constraint (c8e2f5a1d093) have no entry:
# the revision line already reports them. tests/integration/test_rollout_preflight.py ties every name to Storage/models.py.
EXPECTED: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("image_cuts", ("id", "source_image_id", "cut_image_id", "source_content_hash", "crop_x", "crop_y", "crop_w", "crop_h",
                    "detector", "detector_version", "confidence", "detect_run_id", "state", "created_at", "decided_at"),
     "b8d4f1a6c935"),
    ("ocr_texts", ("stale",), "b8d4f1a6c935"),
    ("ocr_lemmas", ("stale",), "b8d4f1a6c935"),
    ("image_descriptions", ("stale",), "b8d4f1a6c935"),
    ("tags", ("stale",), "b8d4f1a6c935"),
    ("process_events", ("id", "process", "event", "batch_run_id", "subject_id", "occurred_at"), "c5e2a9d7b140"),
    ("image_cuts", ("failure_reason",), "d7a3b9e2c451"),
    ("images", ("popularity",), "a4f7c1e92b68"),
    ("popularity_absorptions", ("id", "kind", "source", "loser_image_id", "winner_image_id", "weight", "dedupe_key",
                                "batch_run_id", "reverses_id", "reversed_by_id", "reason", "created_at"),
     "a4f7c1e92b68"),
    ("image_cuts", ("detected_crop_x", "detected_crop_y", "detected_crop_w", "detected_crop_h"), "e9c4a7b3d812"),
    ("meme_saves", ("id", "image_id", "created_at"), "d5b8e3a07c41"),
)


async def schema_matches_models(session, revs: list | None = None) -> tuple[str, str]:
    revs = revs if revs is not None else chain()
    current = await current_revisions(session)
    try:
        pending = {r.revision for r in pending_after(current[0] if current else None, revs)}
    except UnknownRevision:
        pending = set()  # the revision line reports it; every gap below is then a failure
    rows = (await session.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    ))).all()
    present = {(t, c) for t, c in rows}
    lines, worst = [], OK
    for table, columns, revision in EXPECTED:
        for column in columns:
            if (table, column) in present:
                continue
            if revision in pending:
                lines.append(f"{table}.{column}: missing, created by {revision}")
                worst = worst if worst == FAIL else WARN
            else:
                lines.append(f"{table}.{column}: missing although revision applied ({revision})")
                worst = FAIL
    if not lines:
        return OK, f"{sum(len(c) for _, c, _ in EXPECTED)} expected columns present"
    return worst, "\n".join(lines)


async def image_status_counts(session) -> tuple[str, str]:
    rows = (await session.execute(text("SELECT status, count(*) FROM images GROUP BY status ORDER BY status"))).all()
    detail = ", ".join(f"{status} {count}" for status, count in rows) or "no images"
    if any(status == "cut_proposed" and count for status, count in rows):
        return WARN, f"{detail}; cut_proposed images are expected only after the first recut run"
    return OK, detail


async def active_jobs(session) -> tuple[str, str]:
    kinds = list((await session.execute(text(
        "SELECT kind FROM batch_runs WHERE status = 'started' ORDER BY kind"))).scalars())
    if kinds:
        return FAIL, f"{len(kinds)} job(s) running: {', '.join(kinds)}; wait or stop them first"
    return OK, "no batch job is running"
```

- [ ] **Step 4: Run to verify they pass**

Run: same command as Step 2.
Expected: 17 passed. If `tags` is not a table name in `Storage/models.py`, `test_every_expected_name_exists...` names it: use the table the b8d4f1a6c935 migration's `STALE_TABLES` really alters (read that file) and fix the entry.

- [ ] **Step 5: Commit**

```bash
git add tools/rollout_preflight.py tests/integration/test_rollout_preflight.py
git commit -m "feat(rollout): preflight schema, status count and active job checks"
```

---

### Task 3: Filesystem check, run_checks and the CLI

**Files:**
- Modify: `tools/rollout_preflight.py`
- Modify: `tests/integration/test_rollout_preflight.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces: `filesystem(base_path: str | None, backup_dir: str | None, min_free_gb: float) -> tuple[str, str]` (sync), `read_base_path(env: str, root: Path = REPO) -> str | None`, `async run_checks(session, *, base_path, backup_dir, min_free_gb) -> tuple[list[Result], int]`, `main(argv: list[str] | None = None) -> int`, and `python -m tools.rollout_preflight`.

- [ ] **Step 1: Write the failing tests** (append)

```python
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
    (env_dir / ".env.general").write_text('DATABASE_URL=x\nBASE_PATH="D:\\memes lib"\n', encoding="utf-8")
    (env_dir / ".env.it").write_text("DATABASE_URL=x\n", encoding="utf-8")
    assert rp.read_base_path("general", tmp_path) == "D:\\memes lib"
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


def test_main_without_the_read_only_url_exits_two(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    assert rp.main(["--env", "general", "--base-path", "."]) == 2
    assert "DATABASE_URL_READONLY" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**

Expected: new tests fail with `AttributeError` for `filesystem`, `read_base_path`, `run_checks`, `main`.

- [ ] **Step 3: Implement** (append; add `import argparse, asyncio, os, shutil, sys` and `from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine` at the top of the module)

```python
GB = 1024 ** 3


def filesystem(base_path: str | None, backup_dir: str | None, min_free_gb: float) -> tuple[str, str]:
    if not base_path:
        return FAIL, "BASE_PATH unknown; pass --base-path"
    base = Path(base_path)
    if not base.exists():
        return FAIL, f"BASE_PATH {base} does not exist"
    if not base.is_dir():
        return FAIL, f"BASE_PATH {base} is not a directory"
    probe = base / ".rollout_preflight_probe"
    try:
        probe.write_text("probe")
        probe.unlink()
    except OSError as exc:
        return FAIL, f"BASE_PATH {base} is not writable ({type(exc).__name__}); the backend writes cut files and pre-cuts here"
    pre_cuts = base / "pre-cuts"
    if pre_cuts.exists() and not pre_cuts.is_dir():
        return FAIL, f"{pre_cuts} exists and is not a directory"
    notes = []
    for label, path in (("BASE_PATH", base), ("backup dir", Path(backup_dir) if backup_dir else None)):
        if path is None:
            continue
        if not path.is_dir():
            return FAIL, f"{label} {path} does not exist"
        free = shutil.disk_usage(path).free / GB
        if free < min_free_gb:
            return FAIL, f"{label} {path} has {free:.1f} GB free, below {min_free_gb:g} GB"
        notes.append(f"{label} {free:.1f} GB free")
    return OK, f"{base} writable, pre-cuts creatable; " + ", ".join(notes)


def read_base_path(env: str, root: Path = REPO) -> str | None:
    """BASE_PATH from environments/.env.<env>, read as text; no other line of the file is looked at."""
    try:
        lines = (root / "environments" / f".env.{env}").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "BASE_PATH":
            return value.strip().strip('"').strip("'") or None
    return None


async def run_checks(session, *, base_path, backup_dir, min_free_gb) -> tuple[list[Result], int]:
    """Role gate first (a writable role returns only that result and code 2), then every check, each in a savepoint
    so one error cannot hide the rest. Code: 0 ready, 1 not ready, 2 writable role."""
    async def guarded(name, call):
        try:
            async with session.begin_nested():
                status, detail = await call()
        except Exception as exc:
            status, detail = FAIL, f"check failed: {type(exc).__name__}: {exc}"
        return Result(name, status, detail)

    role = await guarded("role_is_read_only", lambda: role_is_read_only(session))
    if role.status != OK:
        return [role], 2

    async def fs():
        return filesystem(base_path, backup_dir, min_free_gb)

    results = [role]
    for name, call in (
        ("revision_vs_head", lambda: revision_vs_head(session)),
        ("schema_matches_models", lambda: schema_matches_models(session)),
        ("image_status_counts", lambda: image_status_counts(session)),
        ("filesystem", fs),
        ("active_jobs", lambda: active_jobs(session)),
    ):
        results.append(await guarded(name, call))
    return results, 1 if any(r.status == FAIL for r in results) else 0


async def _run(url: str, env: str, **kwargs) -> int:
    engine = create_async_engine(
        url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}, pool_pre_ping=False)
    try:
        async with AsyncSession(engine) as session:
            results, code = await run_checks(session, **kwargs)
    finally:
        await engine.dispose()
    print(format_report(env, results, url))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only readiness check for the screenshot-recut rollout")
    parser.add_argument("--env", required=True, choices=["metal", "general", "it"])
    parser.add_argument("--base-path", help="default: BASE_PATH from environments/.env.<env>")
    parser.add_argument("--backup-dir", help="directory that will receive the dump; its free space is checked")
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL_READONLY")
    if not url:
        print("DATABASE_URL_READONLY is not set in the shell (the read-only role's URL; never the main DATABASE_URL)",
              file=sys.stderr)
        return 2
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    try:
        return asyncio.run(_run(url, args.env, base_path=args.base_path or read_base_path(args.env),
                                backup_dir=args.backup_dir, min_free_gb=args.min_free_gb))
    except Exception as exc:
        print(scrub(f"could not run the preflight: {type(exc).__name__}: {exc}", url), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

Two details to check at Step 4: `make_url` is imported in Task 1; the `guarded` lambdas return coroutines, so `await call()` is correct.

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL=... pytest tests/integration/test_rollout_preflight.py -v`
Expected: all pass (32 tests). Then the CLI smoke without a database: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m tools.rollout_preflight --env general` prints the `DATABASE_URL_READONLY is not set` line and exits 2 (run it with that variable unset; it never connects).

- [ ] **Step 5: Commit**

```bash
git add tools/rollout_preflight.py tests/integration/test_rollout_preflight.py
git commit -m "feat(rollout): preflight filesystem check, runner and CLI"
```

---

### Task 4: Runbook, doc guard and doc changes

**Files:**
- Create: `docs/batches/use-cases/screenshot-recut-rollout.md`
- Create: `tests/docs/test_rollout_runbook.py`
- Modify: `docs/batches/use-cases/screenshot-recut.md` (the "First rollout" section, line 3 status line)
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md` (the "First rollout" bullet, line 112)
- Modify: `docs/batches/inventory.md`, `CLAUDE.md`
- Modify: the spec status and the tracker row (`planned` while this plan runs, `done` at the end)

**Interfaces:**
- Consumes: `tools.rollout_preflight.chain`, `FIRST_RECUT_REVISION`, `REVISION_LABELS`.

- [ ] **Step 1: Write the failing guard**

```python
"""The rollout runbook must list exactly the migrations a behind environment receives, and be linked from the use case."""
import re
from pathlib import Path

from tools.rollout_preflight import FIRST_RECUT_REVISION, REVISION_LABELS, chain

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "batches" / "use-cases" / "screenshot-recut-rollout.md"


def recut_chain_ids():
    ids = [r.revision for r in chain()]
    return ids[ids.index(FIRST_RECUT_REVISION):]


def test_runbook_lists_exactly_the_pending_revisions():
    section = RUNBOOK.read_text(encoding="utf-8").split("## The migrations", 1)[1].split("\n## ", 1)[0]
    listed = re.findall(r"`([0-9a-f]{12})`", section)
    assert listed == recut_chain_ids()


def test_every_revision_has_a_label_in_the_tool():
    assert set(recut_chain_ids()) <= set(REVISION_LABELS)


def test_use_case_links_to_the_runbook():
    text = (ROOT / "docs" / "batches" / "use-cases" / "screenshot-recut.md").read_text(encoding="utf-8")
    assert "screenshot-recut-rollout.md" in text
    assert "## First rollout" not in text or "screenshot-recut-rollout.md" in text.split("## First rollout", 1)[1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/docs/test_rollout_runbook.py -v`
Expected: FAIL with `FileNotFoundError` for the runbook (the label test passes).

- [ ] **Step 3: Write the runbook** at `docs/batches/use-cases/screenshot-recut-rollout.md`:

````markdown
# Runbook: first live rollout of the screenshot recut

Status: draft (written 2026-10-31, never run). Design: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-31-rollout-prep-design.md`.
Use case: `screenshot-recut.md`. Nothing here runs by itself: every step needs the owner's explicit go-ahead, one environment at a
time, in this order: **general, then metal, then it**. Stop at the first failing step of an environment; do not start the next one.

The commands are PowerShell from the repo root unless a step says `Storage/`. `$py` is `.\.venv311\Scripts\python.exe`.

## The migrations

A behind environment receives these seven in one `alembic upgrade head`, in this order. The popularity and saves migrations sit
between the recut ones. All seven only add tables, columns, constraints and indexes and rewrite no existing row.

1. `b8d4f1a6c935` R1: `image_cuts`, `stale` columns
2. `c5e2a9d7b140` M1: `process_events`
3. `d7a3b9e2c451` R3a: `image_cuts.failure_reason`
4. `a4f7c1e92b68` popularity: `images.popularity`, `popularity_absorptions`
5. `c8e2f5a1d093` popularity: the `adjustment` source
6. `e9c4a7b3d812` R6: the detected box on `image_cuts`
7. `d5b8e3a07c41` saves: `meme_saves`

`tools/rollout_preflight.py` reads the same chain from `Storage/alembic`; a test fails when this list and the chain differ.

## Step 0: before anything

1. Run `tests/integration/` on `main` against the test database and read the result.
2. Find out which code each always-on backend runs. They start with `--reload`; if one runs from the main checkout, the merged code
   already serves against an unmigrated database, and the preflight's revision line shows the gap. Write the answer down here.
3. Set the read-only URL for the environment in the shell (the value is never printed):
   `$env:DATABASE_URL_READONLY = (Select-String -Path environments\.env.general -Pattern '^DATABASE_URL_READONLY=').Line.Split('=',2)[1]`

## Per environment (general, then metal, then it)

Replace `general` and port `5434` (metal 5432, it 5436) as needed.

1. **Preflight before.**
   `& $py -m tools.rollout_preflight --env general --backup-dir Storage\backups`
   Expect `NOT READY` only because of the seven pending revisions (and `WARN` lines for the missing objects they create).
   Anything else that fails (a running batch job, no free space, an unwritable `BASE_PATH`) must be fixed first.
   *If this fails:* nothing changed; fix and rerun.
2. **Backup.** From `Storage/` (asks for the password):
   `pg_dump -h localhost -p 5434 -U ocr -Fc -f ./backups/ocrdb-2026-MM-DD-general.dump ocrdb`
   then `pg_restore --list ./backups/ocrdb-2026-MM-DD-general.dump | Select-Object -First 5` and note
   `(Get-Item ./backups/ocrdb-2026-MM-DD-general.dump).Length`. Record the revision from the preflight in your notes.
   *If this fails:* nothing changed; do not continue without a listed dump.
3. **Quiet the writers.** No batch job running (the preflight checked it), nobody on `/duplicates`, `/ingestion` or `/admin/recut`.
4. **Upgrade.** From `Storage/`, with that environment's variables loaded as in CLAUDE.md ("Database migrations"): `alembic upgrade head`.
   *If this fails midway:* Postgres DDL is transactional, so the failing revision rolls back by itself; read the error, rerun the
   preflight to see where it stopped. Never edit `alembic_version` by hand.
5. **Preflight after.** Same command as step 1. Expect `READY` and `at head d5b8e3a07c41`.
6. **Restart the backend** of that environment on matching code. Where an image is used, rebuild it first: it now needs Pillow,
   `batch/utils/{__init__,recut_cut,file_hash}.py` and write access to `BASE_PATH`. Smoke test:
   `/api/diagnostics/health`, `/api/images?limit=1`, `GET /api/admin/batches/metrics?process=recut`, and `/admin/batches` showing
   all four metric panels.
   *If the smoke test fails and nothing new was written:* `alembic downgrade` to the revision noted in step 2 (the downgrades drop
   what was added; `c8e2f5a1d093` refuses once an `adjustment` popularity row exists). If that fails, restore the dump.
   *If anything fails after the app wrote new data:* restore the dump of that environment only; the cost is the writes since the backup.

## First recut pass (general only)

`detect_recut_crops --limit 500`, then `recut_pipeline`, then review a few dozen cuts on `/admin/recut`. Two things have never run
for real and are watched: the `refresh_recut_cuts` GPU steps (OCR, descriptions), and the box-adjust UI in a real browser (a tall
and a wide image, touch). *If a `recut_pipeline` step fails:* trigger it again; each step selects only what is still waiting.

## What to record afterwards

The first live numbers, from read-only queries: the approve rate by confidence band (the R7 table), and the `ingest_tier_*` and
`library_duplicates` panels once `/ingestion` and `/duplicates` have been used. Then decide scheduling (see `screenshot-recut.md`).
````

Replace `2026-MM-DD` in the commands with the real date when running; it is the only placeholder, filled at run time.

- [ ] **Step 4: Replace the "First rollout" section** of `screenshot-recut.md` (lines 101 to 114) with:

```markdown
## First rollout

The step-by-step runbook, with the order across environments, the backup, the migrations and the rollback, is
`screenshot-recut-rollout.md`. Nothing has run against a live database; every step needs the owner's explicit go-ahead. Points the
runbook covers: the box overlay and Adjust mode need a real-browser check (tall and wide image, touch; jsdom cannot test layout),
the `refresh_recut_cuts` GPU steps have never run for real, and the backend deployment must be able to write into `BASE_PATH`
(adjusting writes a new cut file, approving writes `pre-cuts/`; the backend image carries Pillow for this).
```

Change line 3 of that file to `Status: draft (written 2026-10-29, not yet run against a live database; rollout runbook: screenshot-recut-rollout.md).`

In the R7 spec, append to the "First rollout" bullet: ` The runbook and a read-only preflight are in docs/batches/use-cases/screenshot-recut-rollout.md (rollout-prep, 2026-10-31).`

In `docs/batches/inventory.md`, add under the tables a sentence: ``The shell-only readiness check `python -m tools.rollout_preflight` (rollout-prep) is not a batch job: it is read-only, not run-tracked and not registered.`` In `CLAUDE.md`, after the `refresh_recut_cuts`/`recut_pipeline` entries add one line: `tools/rollout_preflight.py → read-only readiness check before the recut migrations (shell only, not a batch job): python -m tools.rollout_preflight --env {metal,general,it}, with DATABASE_URL_READONLY in the shell. See docs/batches/use-cases/screenshot-recut-rollout.md.`

Set the spec status to `planned` with `Plan: docs/superpowers/plans/2026-10-31-rollout-prep.md` under it, and the tracker row to `planned`.

- [ ] **Step 5: Run to verify, then the docs root**

Run: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/docs/ -v`
Expected: all pass (12 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add docs/batches docs/superpowers CLAUDE.md tests/docs/test_rollout_runbook.py
git commit -m "docs(recut): rollout runbook, doc guard and links"
```

---

## Self-review

- **Spec coverage:** section 1 checks → Tasks 1 to 3 (role, revision, schema, status counts, filesystem, active jobs, CLI flags, exit codes, redaction); section 2 runbook → Task 4 (step 0, per-environment steps with failure lines, first pass, record); section 4 docs → Task 4; section 5 tests → all tasks (models tie in Task 2, doc guard in Task 4). Decision 8 order and decision 7 rollback are in the runbook.
- **Placeholders:** the only one is `2026-MM-DD` in the runbook's dump file name, filled when the owner runs it.
- **Type consistency:** `Result`, `chain`, `pending_after`, `current_revisions`, `run_checks`, `filesystem` signatures match across tasks.
- **Open question (spec section 3)** stays open: runbook step 0 asks the owner; no task depends on the answer.
