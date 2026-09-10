from collections import defaultdict
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services import image_store
from Backend.app.services.cluster_splitting import split_for_review
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


def _split_params(tier: str):
    """Threshold ladder + max size for `tier`, or None when splitting is disabled or the
    config block is absent (a stale/omitted overlay disables cleanly rather than raising)."""
    cfg = settings.get("CLUSTERING.INGESTION_REVIEW_SPLITTING")
    if not cfg or not cfg.get("enabled"):
        return None
    t = cfg.get(tier)
    if not t:
        # Block enabled but this tier's ladder omitted -- fail open, same as an absent block.
        return None
    return {
        # `start` is the tier's band top, derived -- not a duplicated literal. If
        # duplicates.threshold is raised, tier B's ladder widens with it.
        "start": _tier_band(tier)[1],
        "decrement": t["decrement"], "floor": t["floor"],
        "max_size": cfg["max_group_size"],
    }


CLUSTER_MEMBER_CAP = 60


def _members_by_tightest_edge(group, group_edges) -> list[str]:
    """The group's member ids (as strings) ordered by the tightest edge each sits on --
    used to keep the `CLUSTER_MEMBER_CAP` most-relevant members when a group is oversized.
    Members with no incident edge sort last, by id string."""
    ids = [str(m) for m in group]
    best: dict[str, float] = {i: float("inf") for i in ids}
    for e in group_edges:
        d = e["distance"]
        for side in (e["image_id1"], e["image_id2"]):
            if side in best and d < best[side]:
                best[side] = d
    return sorted(ids, key=lambda i: (best[i], i))


CURSOR_SEP = "|"


def _encode_cursor(min_distance: float, min_image_id: str) -> str:
    # repr() of a float round-trips exactly through float(); a fixed-precision format (e.g.
    # ":.6f") could collapse two distances that differ beyond the 6th decimal onto the same
    # cursor value, and the strict ">" page filter would then drop the later cluster from
    # every subsequent page -- an unreviewable candidate pair.
    return f"{min_distance!r}{CURSOR_SEP}{min_image_id}"


def _decode_cursor(cursor: str | None) -> tuple[float, str] | None:
    """(min_distance, min_image_id) or None for anything unparseable -- a stale bookmark just
    restarts the queue, never an error."""
    if not cursor or CURSOR_SEP not in cursor:
        return None
    head, _, tail = cursor.partition(CURSOR_SEP)
    tail = tail.strip()
    if not tail:
        return None
    try:
        return (float(head), tail)
    except ValueError:
        return None


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

    async def list_clusters(
        self, tier: str, batch_id: Optional[UUID] = None,
        cursor: Optional[str] = None, limit: int = 40,
    ) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        low, high = _tier_band(tier)
        rows = await self.repo.get_tier_candidate_rows(resolved_id, tier, low, high)

        split_cfg = _split_params(tier)  # None when splitting is disabled

        uf = UnionFind()
        member_info: dict[str, dict] = {}
        edges: list[dict] = []
        member_uuids: set = set()
        pairs_by_member: dict = defaultdict(list)

        for id1, filename1, status1, id2, filename2, status2, distance, match_source in rows:
            uf.connect(id1, id2)
            member_info[id1] = {"image_id": str(id1), "filename": filename1, "status": status1}
            member_info[id2] = {"image_id": str(id2), "filename": filename2, "status": status2}
            member_uuids.update((id1, id2))
            if split_cfg is not None:
                pairs_by_member[id1].append((id2, distance))
                pairs_by_member[id2].append((id1, distance))
            edges.append({
                "image_id1": str(id1), "image_id2": str(id2),
                "distance": distance, "match_source": match_source,
            })

        if split_cfg is not None:
            # get_tier_candidate_rows returns rows in unspecified order, so a loose member
            # with two exactly-equal-distance edges into different subgroups would otherwise
            # attach to whichever neighbour came first in row order -- non-deterministic
            # across identical requests. Sorting by (distance, str(id)) makes it stable.
            for adj in pairs_by_member.values():
                adj.sort(key=lambda p: (p[1], str(p[0])))

        # OCR text is the primary review signal for both tiers (same OCR-first priority the
        # review-duplicates skill already used) -- empirical validation (2026-07-25) found
        # Tier A's original "thumbnails alone are decisive" premise doesn't hold universally
        # (e.g. visually-similar-format-but-different-text meme cards), so the operational
        # order now runs OCR before Tier A review, not just before Tier B's. This method
        # doesn't need to know or care which tier it's serving -- it always fetches OCR text.
        ocr_texts = await self.repo.get_ocr_texts(
            member_uuids, settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN,
        )
        for uid, info in member_info.items():
            info["ocr_text"] = ocr_texts.get(uid)

        clusters = []
        for root in uf.list_clusters():
            blob = uf.get_cluster(root)
            groups = (
                split_for_review(blob, pairs_by_member, **split_cfg)
                if split_cfg is not None
                else [blob]
            )
            # Bucket this blob's edges by group in one pass (O(E)) rather than re-scanning
            # the full edge list per group -- splitting multiplies the group count ~10-20x.
            group_of = {str(m): gi for gi, g in enumerate(groups) for m in g}
            buckets: list[list[dict]] = [[] for _ in groups]
            for e in edges:
                gi = group_of.get(e["image_id1"])
                if gi is not None and gi == group_of.get(e["image_id2"]):
                    buckets[gi].append(e)

            for gi, group in enumerate(groups):
                member_ids = {str(m) for m in group}
                group_edges = buckets[gi]
                min_distance = min((e["distance"] for e in group_edges), default=1.0)
                sort_key = (min_distance, min(member_ids))

                total_members = len(group)
                members = [member_info[m] for m in group]
                if total_members > CLUSTER_MEMBER_CAP:
                    keep = set(_members_by_tightest_edge(group, group_edges)[:CLUSTER_MEMBER_CAP])
                    members = [mi for mi in members if mi["image_id"] in keep]
                    group_edges = [e for e in group_edges
                                   if e["image_id1"] in keep and e["image_id2"] in keep]

                clusters.append({
                    "members": members,
                    "edges": group_edges,
                    "total_members": total_members,
                    "_sort_key": sort_key,
                })

        clusters.sort(key=lambda c: c["_sort_key"])

        decoded = _decode_cursor(cursor)
        if decoded is not None:
            # A partial resolve between page fetches can reshape a cluster so its new
            # min_distance sorts *before* the current cursor -- it then won't reappear until
            # the next page-1 reload. Intentional and lossless: ingest_promote never promotes
            # an image that still has an unresolved candidate pair, so a briefly-skipped
            # cluster is picked back up on the reload the frontend does once the queue drains.
            clusters = [c for c in clusters if c["_sort_key"] > decoded]

        page = clusters[:limit]
        has_next = len(clusters) > limit
        next_cursor = _encode_cursor(*page[-1]["_sort_key"]) if (page and has_next) else None

        for c in page:
            c.pop("_sort_key", None)
        return {"items": page, "next_cursor": next_cursor, "has_next": has_next}

    async def resolve(self, tier: str, decisions: list[dict]) -> dict:
        """Apply per-image reject/keep decisions independently -- one decision's failure (DB or
        filesystem) does not affect any other decision in the same call. `decisions` is a list
        of {"image_id": UUID, "decision": "reject" | "keep"}. Partial resolution is expected --
        callers don't have to decide every member of a cluster in one call, and a partially
        successful batch is a normal outcome, not an error response.

        An image_id appearing in `move_failed` also appears in `rejected` -- the database write
        succeeded (the image IS durably rejected); only its physical file move failed. Callers
        must not treat `rejected` and `move_failed` as mutually exclusive.
        """
        rejected, kept, failed, move_failed = [], [], [], []
        for entry in decisions:
            image_id = entry["image_id"]
            decision = entry["decision"]
            try:
                if decision == "reject":
                    filename = await self.repo.reject_image(image_id)
                    if filename is None:
                        continue
                    await self.repo.commit()
                    try:
                        image_store.move_to_rejected(filename)
                    except Exception as move_error:
                        # Broad on purpose: shutil.move can raise shutil.Error (not an OSError
                        # subclass) as well as OSError. Anything from this call must land here,
                        # not fall through to the except below -- the DB commit already
                        # happened, so misclassifying this as `failed` would claim nothing was
                        # applied when the image is, in fact, durably rejected.
                        move_failed.append({"image_id": str(image_id), "error": str(move_error)})
                    rejected.append(str(image_id))
                elif decision == "keep":
                    await self.repo.mark_reviewed(image_id, tier)
                    await self.repo.commit()
                    kept.append(str(image_id))
                else:
                    raise HTTPException(status_code=422, detail=f"Unknown decision: {decision!r}")
            except HTTPException:
                raise
            except Exception as e:
                await self.repo.rollback()
                failed.append({"image_id": str(image_id), "decision": decision, "error": str(e)})
        return {"rejected": rejected, "kept": kept, "failed": failed, "move_failed": move_failed}

    async def undo_reject(self, image_id: UUID) -> dict:
        filename = await self.repo.undo_reject(image_id)
        if filename is None:
            raise HTTPException(status_code=404, detail="Image not found or not currently rejected")
        image_store.move_from_rejected(filename)
        return {"image_id": str(image_id), "status": "pending"}
