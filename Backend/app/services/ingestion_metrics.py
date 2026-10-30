"""Business metrics of the ingestion duplicate review (epic screenshot-recut, M2a). The only place the ingestion code
touches process_events. Every function runs its writes in a savepoint and swallows any Exception with a warning: a
metrics problem must never fail or roll back a review decision, a candidate search or a promotion. Never commits."""
import logging
from typing import Awaitable, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Storage.models import Image

logger = logging.getLogger(__name__)
T = TypeVar("T")

TIER_PROCESS = {"tier_a": "ingest_tier_a", "tier_b": "ingest_tier_b"}
PROPOSED, REJECTED, KEPT = "proposed", "rejected", "kept"


async def _swallowed(session: AsyncSession, label: str, call: Callable[[], Awaitable[T]]) -> T | None:
    try:
        async with session.begin_nested():
            return await call()
    except Exception as exc:
        logger.warning("ingestion metrics failed (%s): %r", label, exc)
        return None


async def record_proposed(session: AsyncSession, tier: str, batch_id, image_ids) -> None:
    async def call():
        service = BusinessMetricsService(session)
        for image_id in image_ids:
            await service.record(TIER_PROCESS[tier], PROPOSED, batch_id, image_id)
    await _swallowed(session, f"proposed {tier}", call)


async def record_tier_proposals(session: AsyncSession, tier: str, batch_id, low: float, high: float) -> None:
    """`proposed` for every pending image of the run that the tier's review queue would show (the same rows as
    IngestionRepository.get_tier_candidate_rows: in the band, not yet reviewed in this tier, neither side rejected)."""
    async def call():
        rows = await IngestionRepository(session).get_tier_candidate_rows(batch_id, tier, low, high)
        # Row layout: (image_id1, filename1, status1, image_id2, filename2, status2, distance, ...)
        pending = {row[0] for row in rows if row[2] == "pending"} | {row[3] for row in rows if row[5] == "pending"}
        if not pending:
            return
        ids = (await session.execute(select(Image.id).where(
            Image.id.in_(pending), Image.ingestion_batch_id == batch_id, Image.status == "pending"))).scalars().all()
        await record_proposed(session, tier, batch_id, ids)
    await _swallowed(session, f"tier proposals {tier}", call)


async def record_decision(session: AsyncSession, tier: str, event: str, image_id, batch_id=None) -> None:
    """`batch_id` None: the image's own ingestion run (looked up inside the savepoint, so a failed lookup is swallowed too)."""
    async def call():
        service = BusinessMetricsService(session)
        process = TIER_PROCESS[tier]
        if not await service.with_event(process, PROPOSED, [image_id]):
            return  # it never entered this tier's review: a stale or out-of-queue decision counts for nothing
        run_id = batch_id if batch_id is not None else await IngestionRepository(session).get_batch_id(image_id)
        await service.record(process, event, run_id, image_id)
    await _swallowed(session, f"{event} {tier}", call)


async def record_rejection(session: AsyncSession, image_id) -> None:
    """A rejected image leaves every review queue, so its proposals in both tiers are closed as rejected -- except a tier
    where the owner already kept it, and a tier it was never proposed in. Without this, a proposal that was freed in one
    tier and rejected in the other would stay pending for ever. `undo_reject` reopens both (retract_rejection)."""
    async def call():
        service = BusinessMetricsService(session)
        run_id = await IngestionRepository(session).get_batch_id(image_id)
        for process in TIER_PROCESS.values():
            if not await service.with_event(process, PROPOSED, [image_id]):
                continue
            if await service.with_event(process, KEPT, [image_id]):
                continue
            await service.record(process, REJECTED, run_id, image_id)
    await _swallowed(session, "rejection", call)


async def retract_rejection(session: AsyncSession, image_id) -> None:
    async def call():
        service = BusinessMetricsService(session)
        for process in TIER_PROCESS.values():
            await service.retract(process, REJECTED, image_id)
    await _swallowed(session, "retract rejection", call)


async def record_implicit_keeps(session: AsyncSession, batch_id, promoted_ids) -> None:
    """A promoted image that was proposed in a tier and never decided there was left in the library: count it as kept."""
    promoted = list(promoted_ids)
    if not promoted:
        return

    async def call():
        service = BusinessMetricsService(session)
        for process in TIER_PROCESS.values():
            for image_id in await service.undecided(process, PROPOSED, (REJECTED, KEPT), promoted):
                await service.record(process, KEPT, batch_id, image_id)
    await _swallowed(session, "implicit keeps", call)
