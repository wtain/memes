from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select, text, update
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

    async def get_ocr_texts(
        self, image_ids, confidence_min: float, lang_score_min: float
    ) -> dict:
        """Concatenated OCR text per image id for the review UI. Drops blocks below either
        threshold, orders the survivors most-language-plausible first (so e.g. Russian text
        leads instead of the EN/ES EasyOCR readers' Latin transliteration noise), and dedupes
        identical block text. Thresholds are required, passed in by the service from
        settings.OCR.* -- this layer stays config-agnostic (mirrors get_blocked_pending_ids)
        and carries no literal defaults that could drift from environments/settings.yaml.
        Pending members may have no OCR yet if it hasn't reached them; active members always
        do, since they're already fully enriched."""
        if not image_ids:
            return {}
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(image_ids))
        )

        def _order_key(row):
            _, _, confidence, lang_score = row
            return (
                lang_score is None,                       # False (0) sorts before True (1)
                -(lang_score if lang_score is not None else 0.0),
                confidence is None,
                -(confidence if confidence is not None else 0.0),
            )

        rows = sorted(result.all(), key=_order_key)
        by_image: dict = {}
        seen: dict = {}
        for image_id, text, confidence, lang_score in rows:
            if confidence is not None and confidence < confidence_min:
                continue
            if lang_score is not None and lang_score < lang_score_min:
                continue
            block = (text or "").strip()
            if not block:
                continue
            seen_for_image = seen.setdefault(image_id, set())
            if block in seen_for_image:
                continue
            seen_for_image.add(block)
            by_image.setdefault(image_id, []).append(block)
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

    async def list_tier_b_review_page(self, batch_id, low: float, high: float, cursor, limit: int,
                                       candidate_cap: int):
        """See docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md and
        docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md.
        `cursor` is (min_distance, subject_id_str) or None. Returns (subjects, candidates):
        subjects is up to limit+1 rows ordered by (min_distance, subject_id); candidates is, per
        subject, its `candidate_cap` highest-priority rows -- not every candidate row; callers
        needing the uncapped count use `total_candidates` on the subject row. Priority is
        in_batch (the other side is a pending image, never reviewed) before cross_corpus (the
        other side is already `active`, reviewed in some earlier ingestion), tightest-distance
        first within each group -- surfacing genuinely-new comparisons ahead of a tighter match
        against an already-vetted corpus image."""
        # Each unreviewed in-band tmp_duplicates row contributes (subject_id, distance) for the
        # side that is a pending image of this batch, when the other side is not rejected.
        pair_cte = """
        WITH pair AS (
            SELECT td.image_id1 AS subject_id, td.image_id2 AS cand_id, td.distance, td.match_source
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id1
            JOIN images o ON o.id = td.image_id2
            WHERE td.tier_b_reviewed_at IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
            UNION ALL
            SELECT td.image_id2 AS subject_id, td.image_id1 AS cand_id, td.distance, td.match_source
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id2
            JOIN images o ON o.id = td.image_id1
            WHERE td.tier_b_reviewed_at IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
        )
        """
        having = ""
        params = {"low": low, "high": high, "batch_id": batch_id, "limit": limit + 1}
        if cursor is not None:
            having = "HAVING (MIN(p.distance), p.subject_id::text) > (:cur_d, :cur_s)"
            params["cur_d"] = cursor[0]
            params["cur_s"] = cursor[1]

        subjects_sql = text(pair_cte + f"""
        SELECT p.subject_id, i.filename, i.status,
               MIN(p.distance) AS min_distance, COUNT(*) AS total_candidates
        FROM pair p JOIN images i ON i.id = p.subject_id
        GROUP BY p.subject_id, i.filename, i.status
        {having}
        ORDER BY MIN(p.distance), p.subject_id::text
        LIMIT :limit
        """)
        # Scoped to this request's transaction only -- SET LOCAL never outlives it, so it can't
        # leak onto the next caller of a pooled connection (see get_async_db). Query A's
        # GroupAggregate sorts the whole unreviewed-in-band tmp_duplicates set; at Postgres's stock
        # 4MB work_mem default that sort spills to disk. 256MB keeps it in memory -- a real,
        # repeatedly measured win on live data (both in the partial-index spec's final review and
        # independently re-confirmed here via interleaved before/after runs on `general`).
        #
        # Deliberately NOT setting random_page_cost: an earlier version of this change also lowered
        # it to drive the planner onto a nested-loop plan through the pending-images index instead
        # of scanning tmp_duplicates. That looked promising in isolation but did NOT hold up under a
        # careful, interleaved, repeated live measurement -- no real improvement over the sequential-
        # scan plan, consistent with the review's own adversarial probe (which had already shown that
        # exact forced-index plan running slower, 1186ms vs 563ms, and been misread as a validated win
        # when this change was first designed). Left out rather than shipped on a reading that didn't
        # survive verification.
        await self.session.execute(text("SET LOCAL work_mem = '256MB'"))
        subjects = (await self.session.execute(subjects_sql, params)).all()
        if not subjects:
            return [], []

        page_ids = [r.subject_id for r in subjects[:limit]]
        cand_sql = text(pair_cte + """
        , ranked AS (
            SELECT p.*, ROW_NUMBER() OVER (
                PARTITION BY p.subject_id
                ORDER BY (p.match_source = 'cross_corpus'), p.distance, p.cand_id::text
                -- in_batch (never reviewed) before cross_corpus (already active/reviewed), see
                -- the docstring above; cand_id cast to text for byte-parity with the old Python
                -- key (distance, str(cand_id)) -- ensures identical ordering at the cap boundary
                -- when both match_source and distance are tied.
            ) AS rn
            FROM pair p
            WHERE p.subject_id = ANY(:page_ids)
        )
        SELECT r.subject_id, r.cand_id, c.filename AS cand_filename, c.status AS cand_status,
               r.distance, r.match_source
        FROM ranked r JOIN images c ON c.id = r.cand_id
        WHERE r.rn <= :candidate_cap
        ORDER BY r.subject_id, (r.match_source = 'cross_corpus'), r.distance, r.cand_id
        """)
        candidates = (await self.session.execute(
            cand_sql, {**params, "page_ids": page_ids, "candidate_cap": candidate_cap})).all()
        return subjects, candidates

    async def get_review_progress(
        self, batch_id, current_tier: str,
        tier_a_low: float, tier_a_high: float,
        tier_b_low: float, tier_b_high: float,
    ) -> tuple[int, int]:
        """Live progress counts for the ingestion review banner: (tier_remaining, blocked_total).
        tier_remaining is the count of distinct pending subjects with an open (unreviewed, other
        side not rejected) candidate pair in `current_tier`'s band. blocked_total is the same
        count unioned across BOTH tiers -- a subject open in both bands counts once, never a sum
        (see docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md's
        "don't reuse get_blocked_pending_ids" note), via COUNT(DISTINCT ...) FILTER instead of
        materializing id sets and unioning them in Python. One round trip; each tier's band is
        scanned once per pair direction (4 branches total) instead of 6 scans across 3 separate
        statements -- see docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md."""
        assert current_tier in ("tier_a", "tier_b"), f"unknown tier: {current_tier!r}"
        sql = text("""
            WITH open_pairs AS (
                SELECT 'tier_a' AS tier, td.image_id1 AS subject_id
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id1 JOIN images o ON o.id = td.image_id2
                WHERE td.tier_a_reviewed_at IS NULL AND td.distance >= :ta_low AND td.distance < :ta_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_a', td.image_id2
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id2 JOIN images o ON o.id = td.image_id1
                WHERE td.tier_a_reviewed_at IS NULL AND td.distance >= :ta_low AND td.distance < :ta_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_b', td.image_id1
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id1 JOIN images o ON o.id = td.image_id2
                WHERE td.tier_b_reviewed_at IS NULL AND td.distance >= :tb_low AND td.distance < :tb_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
                UNION ALL
                SELECT 'tier_b', td.image_id2
                FROM tmp_duplicates td JOIN images s ON s.id = td.image_id2 JOIN images o ON o.id = td.image_id1
                WHERE td.tier_b_reviewed_at IS NULL AND td.distance >= :tb_low AND td.distance < :tb_high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending' AND o.status <> 'rejected'
            )
            SELECT
                count(DISTINCT subject_id) FILTER (WHERE tier = :current_tier) AS tier_remaining,
                count(DISTINCT subject_id) AS blocked_total
            FROM open_pairs
        """)
        # Scoped to this request's transaction only -- SET LOCAL never outlives it (see
        # get_async_db). Same reasoning as list_tier_b_review_page: this aggregate sorts/merges
        # up to ~210k rows (both tiers, both UNION ALL directions) before the final count -- at
        # Postgres's stock 4MB work_mem that can partially spill to disk. 256MB keeps it in
        # memory -- see docs/superpowers/specs/2026-09-13-ingestion-review-progress-count-query.md's
        # "Measured outcome" section for the live before/after numbers.
        await self.session.execute(text("SET LOCAL work_mem = '256MB'"))
        row = (await self.session.execute(sql, {
            "ta_low": tier_a_low, "ta_high": tier_a_high,
            "tb_low": tier_b_low, "tb_high": tier_b_high,
            "batch_id": batch_id, "current_tier": current_tier,
        })).one()
        return row.tier_remaining, row.blocked_total

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

    async def commit(self) -> None:
        """Commits the current transaction. Repositories otherwise never commit --
        get_async_db owns that boundary -- but IngestionService.resolve() needs each decision
        durably applied before its associated file move runs, so a later decision's failure
        can't roll back an earlier decision whose file has already been physically moved. See
        docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md. Not for use
        outside resolve()."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Rolls back the current transaction -- paired with commit() above, same scope and
        same caveat."""
        await self.session.rollback()
