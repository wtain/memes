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
