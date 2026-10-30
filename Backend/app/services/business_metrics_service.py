"""Business metrics (epic screenshot-recut, ADR-002): record process outcomes and report counts and rates per batch.
Rates are computed here at read time and never stored. Importable by batch code as well as the API."""
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from repository import process_events


class UnknownProcessError(ValueError):
    pass


class UnknownEventError(ValueError):
    pass


class ConflictingEventError(ValueError):
    """The subject already carries an event that excludes this one (for example approved after rejected)."""


Metric = Callable[[Mapping[str, int]], float | int | None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


@dataclass(frozen=True)
class ProcessDefinition:
    name: str
    events: tuple[str, ...]
    metrics: Mapping[str, Metric]
    # Groups of events a subject may carry at most one of: a proposal is approved or rejected, never both.
    exclusive: tuple[tuple[str, ...], ...] = ()


def _ingestion_process(name: str) -> ProcessDefinition:
    """One ingestion review tier (M2a). Subject = the pending image; `kept` includes the winner of a true duplicate
    group, so `reject_share` is a share of decisions, not a precision."""
    return ProcessDefinition(
        name=name,
        events=("proposed", "rejected", "kept"),
        metrics={
            "reject_share": lambda c: _ratio(c["rejected"], c["rejected"] + c["kept"]),
            "decided_share": lambda c: _ratio(c["rejected"] + c["kept"], c["proposed"]),
            "pending": lambda c: c["proposed"] - c["rejected"] - c["kept"],
        },
        exclusive=(("rejected", "kept"),),
    )


PROCESSES: dict[str, ProcessDefinition] = {
    "ingest_tier_a": _ingestion_process("ingest_tier_a"),
    "ingest_tier_b": _ingestion_process("ingest_tier_b"),
    "recut": ProcessDefinition(
        name="recut",
        # "adjusted" (R6): the owner corrected the box; it is not a decision, so it is outside the exclusive group below.
        events=("proposed", "adjusted", "approved", "rejected"),
        metrics={
            "adjusted_share": lambda c: _ratio(c["adjusted"], c["proposed"]),
            # Pending proposals are reported separately and are never counted as rejections.
            "approve_rate": lambda c: _ratio(c["approved"], c["approved"] + c["rejected"]),
            "decided_share": lambda c: _ratio(c["approved"] + c["rejected"], c["proposed"]),
            "pending": lambda c: c["proposed"] - c["approved"] - c["rejected"],
        },
        exclusive=(("approved", "rejected"),),
    ),
    "library_duplicates": ProcessDefinition(
        name="library_duplicates",
        # The owner's decisions on the library /duplicates page (M2b). Images in a confirmed group (flagged copies and
        # their keeper) are `confirmed`, members of a dismissed set `dismissed`; an image may be both over time.
        events=("confirmed", "dismissed"),
        metrics={"confirm_share": lambda c: _ratio(c["confirmed"], c["confirmed"] + c["dismissed"])},
    ),
}


def _definition(process: str) -> ProcessDefinition:
    try:
        return PROCESSES[process]
    except KeyError:
        raise UnknownProcessError(process) from None


def _bucket(definition: ProcessDefinition, event_counts: Mapping[str, int]) -> dict:
    counts = {event: event_counts.get(event, 0) for event in definition.events}
    return {"counts": counts, "metrics": {name: metric(counts) for name, metric in definition.metrics.items()}}


class BusinessMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, process: str, event: str, batch_run_id: uuid.UUID | None, subject_id: uuid.UUID) -> bool:
        """Record one outcome in the caller's transaction (never commits). Returns False if this subject already
        has this event, so a retry cannot double-count. Raises ConflictingEventError if the subject already carries an
        event that excludes this one (callers decide a proposal once; concurrent writers must serialise on the subject,
        as R5 does with the image_cuts row lock)."""
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        for group in definition.exclusive:
            others = tuple(other for other in group if event in group and other != event)
            if others and await process_events.subject_has_any_event(self._session, process, subject_id, others):
                raise ConflictingEventError(f"{process}: {event} after {others}")
        return await process_events.insert_event(self._session, process, event, batch_run_id, subject_id)

    async def retract(self, process: str, event: str, subject_id: uuid.UUID) -> bool:
        """Remove one event of one subject. Only for the undo of the decision the event describes (ADR-002, M2a).
        Returns False if there was none. Never commits."""
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        return await process_events.delete_event(self._session, process, event, subject_id)

    async def with_event(self, process: str, event: str, subject_ids) -> set[uuid.UUID]:
        """Which of `subject_ids` carry this event."""
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        return await process_events.subjects_with_event(self._session, process, event, list(subject_ids))

    async def undecided(self, process: str, proposed_event: str, decided_events: tuple[str, ...],
                        subject_ids) -> set[uuid.UUID]:
        """The subjects that carry `proposed_event` and none of `decided_events`."""
        remaining = await self.with_event(process, proposed_event, subject_ids)
        for event in decided_events:
            remaining -= await self.with_event(process, event, remaining)
        return remaining

    async def batch_metrics(self, process: str, limit: int = 50) -> dict:
        """Counts and rates for the newest `limit` runs that have events, newest first, plus the unattributed
        rows (events whose run was pruned or never known); its counts are all zero when there are none."""
        definition = _definition(process)
        runs = await process_events.latest_runs_with_events(self._session, process, limit)
        rows = await process_events.count_events(self._session, process, [run_id for run_id, _ in runs])

        per_run: dict[uuid.UUID, dict[str, int]] = {}
        unattributed: dict[str, int] = {}
        for run_id, event, count in rows:
            target = unattributed if run_id is None else per_run.setdefault(run_id, {})
            target[event] = count

        items = [
            {"run_id": str(run_id), "run_created_at": created_at, **_bucket(definition, per_run.get(run_id, {}))}
            for run_id, created_at in runs
        ]
        return {
            "process": process,
            "items": items,
            "unattributed": _bucket(definition, unattributed),
        }
