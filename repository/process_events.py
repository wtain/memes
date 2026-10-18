import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import BatchRun, ProcessEvent


async def insert_event(session: AsyncSession, process: str, event: str,
                       batch_run_id: uuid.UUID | None, subject_id: uuid.UUID) -> bool:
    """Insert one event; a subject already carrying this event is left alone. Returns True if a row was added.
    Never commits."""
    statement = (
        pg_insert(ProcessEvent)
        .values(process=process, event=event, batch_run_id=batch_run_id, subject_id=subject_id)
        .on_conflict_do_nothing(constraint="uq_process_events_subject_event")
    )
    result = await session.execute(statement)
    return result.rowcount == 1


async def subject_has_any_event(session: AsyncSession, process: str, subject_id: uuid.UUID,
                                events: tuple[str, ...]) -> bool:
    statement = select(
        select(ProcessEvent.id)
        .where(ProcessEvent.process == process, ProcessEvent.subject_id == subject_id,
               ProcessEvent.event.in_(events))
        .exists()
    )
    return bool(await session.scalar(statement))


async def latest_runs_with_events(session: AsyncSession, process: str, limit: int) -> list[tuple[uuid.UUID, datetime]]:
    """The newest `limit` batch runs that have at least one event of this process, newest first."""
    statement = (
        select(BatchRun.run_id, BatchRun.created_at)
        .join(ProcessEvent, ProcessEvent.batch_run_id == BatchRun.run_id)
        .where(ProcessEvent.process == process)
        .group_by(BatchRun.run_id, BatchRun.created_at)
        .order_by(BatchRun.created_at.desc(), BatchRun.run_id.desc())
        .limit(limit)
    )
    return [(row.run_id, row.created_at) for row in (await session.execute(statement)).all()]


async def count_events(session: AsyncSession, process: str,
                       run_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID | None, str, int]]:
    """Event counts grouped by (run, event) for the given runs, plus the unattributed rows (run None)."""
    statement = (
        select(ProcessEvent.batch_run_id, ProcessEvent.event, func.count())
        .where(ProcessEvent.process == process)
        .where(or_(ProcessEvent.batch_run_id.in_(run_ids), ProcessEvent.batch_run_id.is_(None)))
        .group_by(ProcessEvent.batch_run_id, ProcessEvent.event)
    )
    return [(row[0], row[1], row[2]) for row in (await session.execute(statement)).all()]
