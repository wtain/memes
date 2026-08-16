from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import BatchRun, Image, OCRText, RunStatus, TmpDuplicates


class IngestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_run(self) -> Optional[BatchRun]:
        result = await self.session.execute(
            select(BatchRun)
            .where(BatchRun.kind == "ingestion", BatchRun.status == str(RunStatus.started))
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_run(self, run_id) -> Optional[BatchRun]:
        result = await self.session.execute(select(BatchRun).where(BatchRun.run_id == run_id))
        return result.scalar_one_or_none()

    async def list_pending_images(self, batch_id):
        result = await self.session.execute(
            select(Image.id, Image.filename, Image.created_at)
            .where(Image.ingestion_batch_id == batch_id, Image.status == "pending")
            .order_by(Image.created_at)
        )
        return result.all()

    async def list_abortable_images(self, batch_id):
        """Every pending or rejected image in this batch -- i.e. everything an abort should
        undo. Excludes active (promoted) images, which are out of scope for abort."""
        result = await self.session.execute(
            select(Image.id, Image.filename, Image.status)
            .where(Image.ingestion_batch_id == batch_id, Image.status.in_(["pending", "rejected"]))
            .order_by(Image.created_at)
        )
        return result.all()

    async def get_tier_candidate_rows(self, batch_id, tier: str, distance_low: float, distance_high: float):
        """Rows still worth showing a reviewer: within this tier's distance band, not yet
        marked reviewed for this tier, and neither side already rejected (a rejected image's
        relationships are moot -- see docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md's
        'Review-queue resumability' section). Every image returned is therefore either
        `pending` (an undecided member of this batch) or `active` (read-only corpus context)."""
        reviewed_col = TmpDuplicates.tier_a_reviewed_at if tier == "tier_a" else TmpDuplicates.tier_b_reviewed_at

        img1 = aliased(Image)
        img2 = aliased(Image)

        batch_pending_ids = (
            select(Image.id)
            .where(Image.ingestion_batch_id == batch_id, Image.status == "pending")
            .scalar_subquery()
        )

        query = (
            select(
                TmpDuplicates.image_id1, img1.filename, img1.status,
                TmpDuplicates.image_id2, img2.filename, img2.status,
                TmpDuplicates.distance, TmpDuplicates.match_source,
            )
            .join(img1, img1.id == TmpDuplicates.image_id1)
            .join(img2, img2.id == TmpDuplicates.image_id2)
            .where(
                reviewed_col.is_(None),
                img1.status != "rejected",
                img2.status != "rejected",
                TmpDuplicates.distance >= distance_low,
                TmpDuplicates.distance < distance_high,
                or_(
                    TmpDuplicates.image_id1.in_(batch_pending_ids),
                    TmpDuplicates.image_id2.in_(batch_pending_ids),
                ),
            )
        )
        result = await self.session.execute(query)
        return result.all()

    async def get_ocr_texts(self, image_ids) -> dict:
        """Concatenated OCR text per image id, low-confidence blocks dropped -- Tier B's
        primary review signal (same OCR-text-first priority the review-duplicates skill
        already used). Pending members may have none yet if OCR hasn't reached them;
        active members always do, since they're already fully enriched."""
        if not image_ids:
            return {}
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence)
            .where(OCRText.image_id.in_(image_ids))
        )
        by_image: dict = {}
        for image_id, text, confidence in result.all():
            if confidence is not None and confidence < 0.3:
                continue
            by_image.setdefault(image_id, []).append(text)
        return {image_id: " ".join(parts) for image_id, parts in by_image.items()}

    async def get_blocked_pending_ids(self, batch_id, tier_a_high: float, tier_b_high: float) -> set:
        """Pending image ids in this batch that still have at least one unresolved candidate
        pair in either tier -- not yet safe to promote. tier_a_high is the Tier A/B boundary
        (clusterize.py's PROXIMITY_THRESHOLD); tier_b_high is Tier B's outer bound
        (settings.DUPLICATES.THRESHOLD) -- callers pass these rather than this repository
        importing config, keeping it config-agnostic like the rest of this layer."""
        tier_a_rows = await self.get_tier_candidate_rows(batch_id, "tier_a", 0.0, tier_a_high)
        tier_b_rows = await self.get_tier_candidate_rows(batch_id, "tier_b", tier_a_high, tier_b_high)
        blocked = set()
        for id1, _, _, id2, _, _, _, _ in (*tier_a_rows, *tier_b_rows):
            blocked.add(id1)
            blocked.add(id2)
        return blocked

    async def promote_images(self, image_ids) -> int:
        """Flip status to active for the given image ids (already validated by the caller as
        clear of unresolved candidates). Returns the number of rows updated."""
        if not image_ids:
            return 0
        result = await self.session.execute(
            update(Image).where(Image.id.in_(image_ids)).values(status="active")
        )
        return result.rowcount

    async def reject_image(self, image_id) -> Optional[str]:
        """Flip status to rejected. Returns the filename (for the caller to move the file),
        or None if the image doesn't exist or isn't currently pending (already resolved
        elsewhere -- see
        docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md)."""
        result = await self.session.execute(
            select(Image).where(Image.id == image_id, Image.status == "pending")
        )
        image = result.scalar_one_or_none()
        if image is None:
            return None
        image.status = "rejected"
        await self.session.flush()
        return image.filename

    async def undo_reject(self, image_id) -> Optional[str]:
        """Revert a rejected image back to pending. Returns the filename (for the caller to
        move the file back), or None if the image doesn't exist or isn't rejected."""
        result = await self.session.execute(
            select(Image).where(Image.id == image_id, Image.status == "rejected")
        )
        image = result.scalar_one_or_none()
        if image is None:
            return None
        image.status = "pending"
        await self.session.flush()
        return image.filename

    async def mark_reviewed(self, image_id, tier: str) -> None:
        """Set this tier's reviewed_at on every tmp_duplicates row touching image_id that
        doesn't already have it -- a 'not a duplicate' decision settles the pair regardless
        of which member the reviewer clicked."""
        reviewed_col = "tier_a_reviewed_at" if tier == "tier_a" else "tier_b_reviewed_at"
        result = await self.session.execute(
            select(TmpDuplicates).where(
                or_(TmpDuplicates.image_id1 == image_id, TmpDuplicates.image_id2 == image_id),
                getattr(TmpDuplicates, reviewed_col).is_(None),
            )
        )
        now = datetime.now(timezone.utc)
        for row in result.scalars().all():
            setattr(row, reviewed_col, now)
        await self.session.flush()
