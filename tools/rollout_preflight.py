"""Read-only readiness check before the screenshot-recut rollout (docs/batches/use-cases/screenshot-recut-rollout.md).

Reads DATABASE_URL_READONLY from the shell, opens its own short-lived read-only connection and reports OK / WARN / FAIL per
check. It never imports Storage.db, never writes to the database (the only write anywhere is a probe file in BASE_PATH)
and never prints the database URL. Design: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-31-rollout-prep-design.md.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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


# The ingestion review run stays `started` for days during human review; it is not a job to wait for.
LONG_LIVED_KINDS = {"ingestion"}


async def active_jobs(session) -> tuple[str, str]:
    rows = (await session.execute(text(
        "SELECT kind, round(extract(epoch FROM now() - created_at) / 3600)::int FROM batch_runs "
        "WHERE status = 'started' ORDER BY kind"))).all()
    jobs = [f"{kind} (started {hours} h ago)" for kind, hours in rows if kind not in LONG_LIVED_KINDS]
    review = [kind for kind, _ in rows if kind in LONG_LIVED_KINDS]
    if jobs:
        return FAIL, (f"{len(jobs)} job(s) running: {', '.join(jobs)}; wait for them, or if a row is orphaned "
                      "(its process is gone) see the runbook")
    if review:
        return WARN, "an ingestion review run is still started (normal while a review is open); do not use /ingestion during the upgrade"
    return OK, "no batch job is running"


EXPECTED_PORTS = {"metal": 5432, "general": 5434, "it": 5436}


def target_check(env: str, url: str) -> tuple[str, str]:
    """Names the database the URL points at (no secret) and warns when its port is another environment's."""
    u = make_url(url)
    detail = f"{u.host}:{u.port}/{u.database}"
    expected = EXPECTED_PORTS[env]
    if u.port != expected:
        return WARN, f"{detail}; the {env} database is on port {expected}: is DATABASE_URL_READONLY for the right environment?"
    return OK, detail



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
    engine = create_async_engine(url, connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    try:
        async with AsyncSession(engine) as session:
            results, code = await run_checks(session, **kwargs)
            results = [Result("target", *target_check(env, url))] + results
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
