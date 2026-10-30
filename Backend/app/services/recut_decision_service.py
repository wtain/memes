"""Approve or reject one recut proposal (epic screenshot-recut, R5a). One decision is one transaction owned by the caller
(never commits); every precondition is checked before the first write, so a refusal leaves nothing half applied.
No file is moved here: removal is the existing move_flagged then unregister_deleted_images path. An approval also
copies the source file to BASE_PATH/pre-cuts/ after the commit (R6), as a backup of the original."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService, ConflictingEventError
from Backend.app.services.recut_files import copy_to_pre_cuts, file_effects
from config.settings import settings
from repository.image_extras import ImageExtrasRepository
from repository.popularity import PopularityRepository, guarded
from Storage.models import CutState, ImageCut, ImageStatus, PopularitySource


class ProposalNotFoundError(Exception):
    pass


class ProposalConflictError(Exception):
    """The proposal cannot be decided now; `code` says why (the API sends it as the 409 detail)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RecutDecisionService:
    def __init__(self, session: AsyncSession, base_path: str | None = None):
        self.session = session
        self.base_path = base_path
        self.repo = RecutRepository(session)
        self.extras = ImageExtrasRepository(session)

    async def approve(self, cut_id: uuid.UUID) -> dict:
        row, cut_image_id, images = await self._prepare(cut_id)
        source = images.get(row.source_image_id) if row.source_image_id is not None else None
        if source is None:
            raise ProposalConflictError("source_missing")
        if source.status != ImageStatus.ACTIVE:
            raise ProposalConflictError("source_not_active")
        if (await self.extras.get_flags_bulk([source.id]))[source.id]:
            raise ProposalConflictError("source_flagged")

        await self.repo.set_image_status(cut_image_id, ImageStatus.ACTIVE)
        await self.extras.set_flagged(source.id, True, "recut_approved")
        # The cut is the same meme as its source: it takes over the source's popularity (weight-0 edge).
        await guarded(self.session, "recut", lambda: PopularityRepository(self.session).absorb(
            source.id, cut_image_id, PopularitySource.RECUT, row.detect_run_id))
        await self.repo.move_ratings(source.id, cut_image_id)
        base, filename = self.base_path or settings.get("BASE_PATH"), source.filename
        if base:  # the source leaves through move_flagged: keep a copy of the original (R6)
            file_effects(self.session, on_commit=lambda: copy_to_pre_cuts(base, filename))
        return await self._finish(row, CutState.APPROVED, "approved")

    async def reject(self, cut_id: uuid.UUID) -> dict:
        row, cut_image_id, _ = await self._prepare(cut_id)
        await self.extras.set_flagged(cut_image_id, True, "recut_rejected")
        return await self._finish(row, CutState.REJECTED, "rejected")

    async def _prepare(self, cut_id: uuid.UUID):
        row = await self.repo.lock_proposal(cut_id)
        if row is None:
            raise ProposalNotFoundError(str(cut_id))
        if row.state != CutState.PROPOSED:
            raise ProposalConflictError("already_decided")
        wanted = [i for i in (row.source_image_id, row.cut_image_id) if i is not None]
        images = await self.repo.images_by_id(wanted) if wanted else {}
        cut = images.get(row.cut_image_id) if row.cut_image_id is not None else None
        if cut is None or cut.status != ImageStatus.CUT_PROPOSED:
            raise ProposalConflictError("cut_missing")
        return row, cut.id, images

    async def _finish(self, row: ImageCut, state: str, event: str) -> dict:
        decided_at = await self.repo.mark_decided(row, state)
        try:
            await BusinessMetricsService(self.session).record("recut", event, row.detect_run_id, row.id)
        except ConflictingEventError:
            raise ProposalConflictError("already_decided") from None
        return {"id": str(row.id), "state": state, "decidedAt": decided_at.isoformat()}
