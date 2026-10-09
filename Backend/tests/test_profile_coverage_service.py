import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from Backend.app.services.meme_profile_service import ProfileValidationError
from Backend.app.services.profile_coverage_service import ProfileCoverageService

COUNTS = {"active": 10, "profiled": 8, "without_profile": 2, "human_verified": 1, "needs_review": 3,
          "stale_taxonomy": 4, "failed": 5, "pending_vector": 6}


class FakeRepo:
    def __init__(self, rows=(), total=0):
        self.rows, self.total, self.calls = list(rows), total, []

    async def live_counts(self, version, model):
        self.calls.append(("counts", version, model))
        return COUNTS

    async def list_category(self, category, version, model, after, limit):
        self.calls.append(("list", category, version, model, after, limit))
        return self.rows[:limit], self.total


class FakeRuns:
    def __init__(self, runs):
        self.runs, self.asked = runs, []

    async def get_most_recent_run(self, kind):
        self.asked.append(kind)
        return self.runs.get(kind)


def _run(completed=True):
    return SimpleNamespace(run_id=uuid.uuid4(), status="completed", created_at=datetime(2026, 10, 9, 10, 0),
                           completed_at=datetime(2026, 10, 9, 11, 0) if completed else None, stats={"images": 3})


def _service(repo=None, runs=None):
    return ProfileCoverageService(repo or FakeRepo(), FakeRuns(runs or {}), taxonomy_version=2, embedding_model="m")


async def test_counts_are_camel_cased_with_the_taxonomy_version():
    repo = FakeRepo()
    result = await _service(repo).coverage()
    assert {k: v for k, v in result.items() if k != "lastRuns"} == {
        "active": 10, "profiled": 8, "withoutProfile": 2, "humanVerified": 1, "needsReview": 3,
        "staleTaxonomy": 4, "failed": 5, "pendingVector": 6, "currentTaxonomyVersion": 2}
    assert repo.calls == [("counts", 2, "m")]


async def test_last_runs_come_from_the_two_kinds():
    profiles, embeddings = _run(), _run(completed=False)
    runs = FakeRuns({"build_meme_profiles": profiles, "build_meme_profile_embeddings": embeddings})
    service = ProfileCoverageService(FakeRepo(), runs, taxonomy_version=2, embedding_model="m")
    result = (await service.coverage())["lastRuns"]
    assert result["profiles"] == {"runId": str(profiles.run_id), "status": "completed",
                                  "startedAt": "2026-10-09T10:00:00", "completedAt": "2026-10-09T11:00:00",
                                  "stats": {"images": 3}}
    assert result["embeddings"]["completedAt"] is None
    assert sorted(runs.asked) == ["build_meme_profile_embeddings", "build_meme_profiles"]


async def test_a_missing_run_is_none():
    assert (await _service().coverage())["lastRuns"] == {"profiles": None, "embeddings": None}


async def test_images_with_more_rows_gives_a_cursor():
    ids = [uuid.uuid4() for _ in range(3)]
    repo = FakeRepo([(i, i == ids[0]) for i in ids], total=9)
    result = await _service(repo).images("failed", None, 2)
    assert repo.calls == [("list", "failed", 2, "m", None, 3)]
    assert result["items"] == [{"id": str(ids[0]), "imageUrl": f"/api/images/{ids[0]}", "humanVerified": True},
                               {"id": str(ids[1]), "imageUrl": f"/api/images/{ids[1]}", "humanVerified": False}]
    assert result["nextCursor"] == str(ids[1]) and result["total"] == 9


async def test_the_last_page_has_no_cursor():
    ids = [uuid.uuid4() for _ in range(2)]
    result = await _service(FakeRepo([(i, False) for i in ids], total=2)).images("failed", None, 2)
    assert len(result["items"]) == 2 and result["nextCursor"] is None


async def test_the_cursor_is_parsed_and_a_bad_one_is_rejected():
    after = uuid.uuid4()
    repo = FakeRepo()
    await _service(repo).images("failed", str(after), 5)
    assert repo.calls[0][4] == after
    with pytest.raises(ProfileValidationError):
        await _service().images("failed", "not-a-uuid", 5)
