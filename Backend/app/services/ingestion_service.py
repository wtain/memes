from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services import image_store
# Reuses clusterize.py's existing constant rather than a second "confirmed duplicate"
# threshold living in two places -- see
# docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.
from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from config.settings import settings
from graph.uf import UnionFind

TIER_BANDS = {
    "tier_a": (0.0, TIER_A_THRESHOLD),
    "tier_b": (TIER_A_THRESHOLD, None),  # upper bound resolved lazily -- settings.DUPLICATES.THRESHOLD
}


def _tier_band(tier: str) -> tuple[float, float]:
    low, high = TIER_BANDS[tier]
    return low, (high if high is not None else settings.DUPLICATES.THRESHOLD)


class IngestionService:
    def __init__(self, repo: IngestionRepository):
        self.repo = repo

    async def _resolve_batch_id(self, batch_id: Optional[UUID]) -> UUID:
        if batch_id is not None:
            return batch_id
        active_run = await self.repo.get_active_run()
        if active_run is None:
            raise HTTPException(status_code=404, detail="No ingestion run is currently in progress")
        return active_run.run_id

    async def get_run_status(self, batch_id: Optional[UUID] = None) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        run = await self.repo.get_run(resolved_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Ingestion run not found")
        return {
            "run_id": str(run.run_id),
            "status": run.status,
            "stage": run.stage,
            "stats": run.stats,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }

    async def list_pending(self, batch_id: Optional[UUID] = None) -> list[dict]:
        resolved_id = await self._resolve_batch_id(batch_id)
        rows = await self.repo.list_pending_images(resolved_id)
        return [
            {"image_id": str(image_id), "filename": filename, "created_at": created_at}
            for image_id, filename, created_at in rows
        ]

    async def list_clusters(self, tier: str, batch_id: Optional[UUID] = None) -> list[dict]:
        resolved_id = await self._resolve_batch_id(batch_id)
        low, high = _tier_band(tier)
        rows = await self.repo.get_tier_candidate_rows(resolved_id, tier, low, high)

        uf = UnionFind()
        member_info: dict[str, dict] = {}
        edges: list[dict] = []
        member_uuids: set = set()

        for id1, filename1, status1, id2, filename2, status2, distance, match_source in rows:
            uf.connect(id1, id2)
            member_info[id1] = {"image_id": str(id1), "filename": filename1, "status": status1}
            member_info[id2] = {"image_id": str(id2), "filename": filename2, "status": status2}
            member_uuids.update((id1, id2))
            edges.append({
                "image_id1": str(id1), "image_id2": str(id2),
                "distance": distance, "match_source": match_source,
            })

        # OCR text is the primary review signal for both tiers (same OCR-first priority the
        # review-duplicates skill already used) -- empirical validation (2026-07-25) found
        # Tier A's original "thumbnails alone are decisive" premise doesn't hold universally
        # (e.g. visually-similar-format-but-different-text meme cards), so the operational
        # order now runs OCR before Tier A review, not just before Tier B's. This method
        # doesn't need to know or care which tier it's serving -- it always fetches OCR text.
        ocr_texts = await self.repo.get_ocr_texts(member_uuids)
        for uid, info in member_info.items():
            info["ocr_text"] = ocr_texts.get(uid)

        clusters = []
        for root in uf.list_clusters():
            cluster_members = uf.get_cluster(root)
            member_ids = {str(m) for m in cluster_members}
            clusters.append({
                "members": [member_info[m] for m in cluster_members],
                "edges": [e for e in edges if e["image_id1"] in member_ids and e["image_id2"] in member_ids],
            })
        return clusters

    async def resolve(self, tier: str, decisions: list[dict]) -> dict:
        """Apply per-image reject/keep decisions. `decisions` is a list of
        {"image_id": UUID, "decision": "reject" | "keep"}. Partial resolution is expected --
        callers don't have to decide every member of a cluster in one call."""
        rejected, kept = [], []
        for entry in decisions:
            image_id = entry["image_id"]
            decision = entry["decision"]
            if decision == "reject":
                filename = await self.repo.reject_image(image_id)
                if filename is not None:
                    image_store.move_to_rejected(filename)
                    rejected.append(str(image_id))
            elif decision == "keep":
                await self.repo.mark_reviewed(image_id, tier)
                kept.append(str(image_id))
            else:
                raise HTTPException(status_code=422, detail=f"Unknown decision: {decision!r}")
        return {"rejected": rejected, "kept": kept}

    async def undo_reject(self, image_id: UUID) -> dict:
        filename = await self.repo.undo_reject(image_id)
        if filename is None:
            raise HTTPException(status_code=404, detail="Image not found or not currently rejected")
        image_store.move_from_rejected(filename)
        return {"image_id": str(image_id), "status": "pending"}
