"""Business metrics of the library duplicate review (epic screenshot-recut, M2b). The only place the /duplicates flows touch
process_events. Every function runs its writes in a savepoint and swallows any Exception with a warning: a metrics problem
must never fail or roll back a flag, an unflag, a dismissal or an undo. Never commits."""
import logging
import uuid
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository

logger = logging.getLogger(__name__)
T = TypeVar("T")

PROCESS = "library_duplicates"
CONFIRMED, DISMISSED = "confirmed", "dismissed"
_FLAG_SOURCES = {"cluster_review"}  # the popularity source of a page flag (FLAG_REASON_SOURCES["duplicate_review"])


async def _swallowed(session: AsyncSession, label: str, call: Callable[[], Awaitable[T]]) -> T | None:
    try:
        async with session.begin_nested():
            return await call()
    except Exception as exc:
        logger.warning("library metrics failed (%s): %r", label, exc)
        return None


async def _record_all(session: AsyncSession, event: str, image_ids) -> None:
    # The clusters on screen come from the newest completed clusterize run; none yet means unattributed.
    run = await BatchRunRepository(session).get_latest_completed_run("clusterize")
    run_id = run.run_id if run is not None else None
    service = BusinessMetricsService(session)
    for image_id in dict.fromkeys(image_ids):
        await service.record(PROCESS, event, run_id, image_id)


async def record_confirmed(session: AsyncSession, image_ids) -> None:
    await _swallowed(session, "confirmed", lambda: _record_all(session, CONFIRMED, list(image_ids)))


async def record_dismissed(session: AsyncSession, image_ids) -> None:
    await _swallowed(session, "dismissed", lambda: _record_all(session, DISMISSED, list(image_ids)))


async def open_keeper(session: AsyncSession, loser_id) -> uuid.UUID | None:
    return await _swallowed(session, "open keeper", lambda: PopularityRepository(session).open_winner(loser_id, _FLAG_SOURCES))


async def retract_confirmed(session: AsyncSession, loser_id, keeper_id) -> None:
    async def call():
        service = BusinessMetricsService(session)
        pop = PopularityRepository(session)
        # An image keeps its confirmation while a page flag still involves it, as a loser or as a keeper.
        for image_id in (loser_id, keeper_id):
            if image_id is None:
                continue
            if await pop.has_open_edge_to(image_id, _FLAG_SOURCES) or await pop.open_winner(image_id, _FLAG_SOURCES):
                continue
            await service.retract(PROCESS, CONFIRMED, image_id)
    await _swallowed(session, "retract confirmed", call)


async def retract_dismissed(session: AsyncSession, image_ids) -> None:
    async def call():
        ids = list(dict.fromkeys(image_ids))
        still = await DuplicateDecisionsRepository(session).images_with_decisions(ids)
        service = BusinessMetricsService(session)
        for image_id in ids:
            if image_id not in still:
                await service.retract(PROCESS, DISMISSED, image_id)
    await _swallowed(session, "retract dismissed", call)
