"""Adjust the box of one recut proposal and rebuild its cut (epic screenshot-recut, R6). One adjustment is one
transaction owned by the caller (never commits); every precondition is checked before the first write, so a refusal
leaves nothing half applied. The cut is a NEW cut_proposed image copied exactly like the first one (CutCopier); the old
cut image is deleted. Files follow the transaction: the old cut file goes after the commit, the new one if the
transaction rolls back."""
import asyncio
import os
import uuid

from PIL import Image as PILImage
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.recut_decision_service import ProposalConflictError, ProposalNotFoundError
from Backend.app.services.recut_files import file_effects, remove_quietly
from batch.utils.recut_cut import SOURCE_CHANGED, SOURCE_MISSING, UNREADABLE, CutError, crop_to_file
from config.settings import settings
from repository.cut_copier import CutCopier
from repository.image_extras import ImageExtrasRepository
from Storage.models import CutState, Image, ImageStatus

MIN_SIDE = 16


class InvalidBoxError(Exception):
    """The box is not acceptable (HTTP 422); `message` says why."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProposalWriteError(Exception):
    """The new cut file could not be written (HTTP 500, detail write_error)."""


def _source_size(path: str) -> tuple[int, int]:
    try:
        with PILImage.open(path) as im:
            return im.size
    except FileNotFoundError:
        raise ProposalConflictError("source_missing") from None
    except (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError):
        raise ProposalConflictError("source_unreadable") from None


class RecutAdjustService:
    def __init__(self, session: AsyncSession, base_path: str | None = None, jpeg_quality: int | None = None):
        self.session = session
        self.repo = RecutRepository(session)
        self.extras = ImageExtrasRepository(session)
        self.base_path = base_path
        self.jpeg_quality = jpeg_quality

    async def adjust(self, cut_id: uuid.UUID, x: int, y: int, w: int, h: int) -> None:
        base = self.base_path or settings.get("BASE_PATH")
        quality = self.jpeg_quality or settings.RECUT.JPEG_QUALITY
        row = await self.repo.lock_proposal(cut_id)
        if row is None:
            raise ProposalNotFoundError(str(cut_id))
        if row.state != CutState.PROPOSED:
            raise ProposalConflictError("already_decided")
        wanted = [i for i in (row.source_image_id, row.cut_image_id) if i is not None]
        images = await self.repo.images_by_id(wanted) if wanted else {}
        old_cut = images.get(row.cut_image_id) if row.cut_image_id is not None else None
        if old_cut is None or old_cut.status != ImageStatus.CUT_PROPOSED:
            raise ProposalConflictError("cut_missing")
        source = images.get(row.source_image_id) if row.source_image_id is not None else None
        if source is None:
            raise ProposalConflictError("source_missing")
        if source.status != ImageStatus.ACTIVE:
            raise ProposalConflictError("source_not_active")
        if (await self.extras.get_flags_bulk([source.id]))[source.id]:
            raise ProposalConflictError("source_flagged")

        box = (x, y, w, h)
        if (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == box:
            raise InvalidBoxError("The box is the current box")
        if x < 0 or y < 0 or w < MIN_SIDE or h < MIN_SIDE:
            raise InvalidBoxError(f"The box must start at 0 or more and be at least {MIN_SIDE} px on each side")
        src = os.path.join(base, source.filename)
        width, height = await asyncio.to_thread(_source_size, src)
        if x + w > width or y + h > height:
            raise InvalidBoxError(f"The box does not fit the source ({width}x{height})")

        dst_name = f"{uuid.uuid4()}{os.path.splitext(source.filename)[1]}"
        dst = os.path.join(base, dst_name)
        old_path = os.path.join(base, old_cut.filename)
        try:
            cut_file = await asyncio.to_thread(crop_to_file, src, dst, box, row.source_content_hash, quality)
        except CutError as error:
            if error.reason in (SOURCE_MISSING, SOURCE_CHANGED):
                raise ProposalConflictError(error.reason) from None
            if error.reason == UNREADABLE:
                raise ProposalConflictError("source_unreadable") from None
            raise ProposalWriteError(error.reason) from None
        # Registered the moment the file exists and before any SQL, so no failure after this point can orphan it.
        file_effects(self.session, on_commit=lambda: remove_quietly(old_path), on_rollback=lambda: remove_quietly(dst))

        new = Image(id=uuid.uuid4(), filename=dst_name, content_hash=cut_file.content_hash, width=cut_file.width,
                    height=cut_file.height, status=ImageStatus.CUT_PROPOSED, created_at=source.created_at)
        self.session.add(new)
        await self.session.flush()
        await CutCopier(self.session).copy(source.id, new.id, box)
        old_id = old_cut.id
        await self.repo.replace_cut(row, new.id, box)
        await self.repo.delete_image(old_id)
        await BusinessMetricsService(self.session).record("recut", "adjusted", row.detect_run_id, row.id)
