"""Profile coverage for the admin view (P5c): live counts, the last runs, category pages."""
import uuid

from Backend.app.services.meme_profile_service import ProfileValidationError

RUN_KINDS = {"profiles": "build_meme_profiles", "embeddings": "build_meme_profile_embeddings"}


def _run(run) -> dict | None:
    if run is None:
        return None
    return {"runId": str(run.run_id), "status": str(run.status), "startedAt": run.created_at.isoformat(),
            "completedAt": run.completed_at.isoformat() if run.completed_at else None, "stats": run.stats}


class ProfileCoverageService:
    def __init__(self, repository, runs, *, taxonomy_version: int, embedding_model: str):
        self.repo, self.runs = repository, runs
        self.taxonomy_version, self.model = taxonomy_version, embedding_model

    async def coverage(self) -> dict:
        counts = await self.repo.live_counts(self.taxonomy_version, self.model)
        return {
            "active": counts["active"], "profiled": counts["profiled"], "withoutProfile": counts["without_profile"],
            "humanVerified": counts["human_verified"], "needsReview": counts["needs_review"],
            "staleTaxonomy": counts["stale_taxonomy"], "failed": counts["failed"],
            "pendingVector": counts["pending_vector"], "currentTaxonomyVersion": self.taxonomy_version,
            "lastRuns": {name: _run(await self.runs.get_most_recent_run(kind)) for name, kind in RUN_KINDS.items()},
        }

    async def images(self, category: str, cursor: str | None, limit: int) -> dict:
        after = None
        if cursor:
            try:
                after = uuid.UUID(cursor)
            except ValueError:
                raise ProfileValidationError("cursor is not valid") from None
        rows, total = await self.repo.list_category(category, self.taxonomy_version, self.model, after, limit + 1)
        more = len(rows) > limit
        items = [{"id": str(image_id), "imageUrl": f"/api/images/{image_id}", "humanVerified": human}
                 for image_id, human in rows[:limit]]
        return {"items": items, "nextCursor": items[-1]["id"] if more and items else None, "total": total}
