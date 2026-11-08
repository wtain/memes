import uuid

import pytest
from sqlalchemy import func, select

from Backend.app.services.comics_candidates_service import ComicsCandidatesService, candidate_http_error
from Backend.app.types.generated.comicscandidatepage import Schema as CandidatePageSchema
from Backend.app.types.generated.comicsdetail import Schema as ComicsDetailSchema
from repository.comics import ComicsError, ComicsRepository
from repository.comics_candidates import CandidateError, ComicsCandidatesRepository
from Storage.models import CandidateState, Comics, ComicsPanel, Image, ImageExtras


async def _image(s, stem=None):
    img = Image(filename=f"{stem or uuid.uuid4()}.jpg", status="active", width=100, height=80,
                content_hash=str(uuid.uuid4()))
    s.add(img)
    await s.flush()
    return img


async def _candidate(s, count=3, stem=None):
    images = [await _image(s, f"{stem}-{i}" if stem else None) for i in range(count)]
    candidate_id = await ComicsCandidatesRepository(s).insert_group([i.id for i in images], 2.0, 0.1, "v1")
    return candidate_id, images


async def _comics_count(s, name):
    return (await s.execute(select(func.count()).select_from(Comics).where(Comics.name == name))).scalar_one()


async def _state(s, candidate_id):
    return (await ComicsCandidatesRepository(s).get(candidate_id)).state


@pytest.mark.asyncio(loop_scope="session")
async def test_list_is_a_valid_page_with_images_in_order_and_has_more(db_session):
    service = ComicsCandidatesService(db_session)
    first, images = await _candidate(db_session, 2)
    await _candidate(db_session, 2)
    page = await service.list(0, 1000)
    validated = CandidatePageSchema.model_validate(page)
    mine = next(i for i in validated.items if i.id == str(first))
    assert [x.id for x in mine.images] == [str(i.id) for i in images]
    assert (mine.maxDistance, mine.maxGapSeconds, mine.images[0].width) == (0.1, 2.0, 100)
    small = await service.list(0, 1)
    assert len(small["items"]) == 1 and small["hasMore"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_all_creates_a_named_draft_with_panels_in_order(db_session):
    service = ComicsCandidatesService(db_session)
    stem = f"IMG_{uuid.uuid4()}"
    candidate_id, images = await _candidate(db_session, 3, stem)
    result = await service.accept(candidate_id, None)
    validated = ComicsDetailSchema.model_validate(result)
    assert validated.name == f"Comics {stem}-0" and validated.state == "draft"
    assert [p.imageId for p in validated.panels] == [str(i.id) for i in images]
    stored = await ComicsCandidatesRepository(db_session).get(candidate_id)
    assert stored.state == CandidateState.ACCEPTED and str(stored.comics_id) == result["id"]
    assert candidate_id not in [uuid.UUID(c["id"]) for c in (await service.list(0, 1000))["items"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_subset_uses_the_given_order_and_names_after_its_first_image(db_session):
    service = ComicsCandidatesService(db_session)
    stem = f"S-{uuid.uuid4()}"
    candidate_id, (a, b, c) = await _candidate(db_session, 3, stem)
    result = await service.accept(candidate_id, [c.id, a.id])
    assert result["name"] == f"Comics {stem}-2"
    assert [p["imageId"] for p in result["panels"]] == [str(c.id), str(a.id)]
    assert await _state(db_session, candidate_id) == CandidateState.ACCEPTED


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("pick, reason", [("one", "too_few_images"), ("outside", "unknown_image"),
                                          ("dup", "duplicate_image")])
async def test_invalid_subsets_are_422_and_change_nothing(db_session, pick, reason):
    service = ComicsCandidatesService(db_session)
    stem = f"V-{uuid.uuid4()}"
    candidate_id, (a, b, c) = await _candidate(db_session, 3, stem)
    stranger = await _image(db_session)
    ids = {"one": [a.id], "outside": [a.id, stranger.id], "dup": [a.id, a.id, b.id]}[pick]
    with pytest.raises(CandidateError) as caught:
        await service.accept(candidate_id, ids)
    assert caught.value.code == "subset_invalid" and caught.value.detail == {"reason": reason}
    assert candidate_http_error(caught.value).status_code == 422
    assert await _state(db_session, candidate_id) == CandidateState.OPEN
    assert await _comics_count(db_session, f"Comics {stem}-0") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_name_conflict_retries_once_with_the_id_prefix(db_session):
    service = ComicsCandidatesService(db_session)
    stem = f"N-{uuid.uuid4()}"
    candidate_id, (a, b) = await _candidate(db_session, 2, stem)
    await ComicsRepository(db_session).create(f"Comics {stem}-0")
    result = await service.accept(candidate_id, None)
    assert result["name"] == f"Comics {stem}-0 {str(a.id)[:8]}"


@pytest.mark.asyncio(loop_scope="session")
async def test_second_name_conflict_is_name_taken(db_session):
    service = ComicsCandidatesService(db_session)
    stem = f"T-{uuid.uuid4()}"
    candidate_id, (a, b) = await _candidate(db_session, 2, stem)
    repo = ComicsRepository(db_session)
    await repo.create(f"Comics {stem}-0")
    await repo.create(f"Comics {stem}-0 {str(a.id)[:8]}")
    with pytest.raises(ComicsError) as caught:
        await service.accept(candidate_id, None)
    assert caught.value.code == "name_taken"
    assert await _state(db_session, candidate_id) == CandidateState.OPEN


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("problem, code, reason", [("flagged", "image_not_addable", "flagged"),
                                                   ("inactive", "image_not_addable", "not_active"),
                                                   ("in_comics", "image_in_comics", None)])
async def test_ineligible_image_refuses_and_leaves_everything_unchanged(db_session, problem, code, reason):
    service = ComicsCandidatesService(db_session)
    stem = f"E-{uuid.uuid4()}"
    candidate_id, (a, b) = await _candidate(db_session, 2, stem)
    if problem == "flagged":
        db_session.add(ImageExtras(image_id=b.id, flagged=True))
    elif problem == "inactive":
        b.status = "rejected"
    else:
        other = await ComicsRepository(db_session).create(f"Other {stem}")
        await ComicsRepository(db_session).add_image(other.id, b.id)
    await db_session.flush()
    with pytest.raises(ComicsError) as caught:
        await service.accept(candidate_id, None)
    assert caught.value.code == code and caught.value.detail.get("reason") == reason
    assert caught.value.detail["image_id"] == b.id
    assert await _state(db_session, candidate_id) == CandidateState.OPEN
    assert await _comics_count(db_session, f"Comics {stem}-0") == 0
    assert (await db_session.execute(select(func.count()).select_from(ComicsPanel)
                                     .where(ComicsPanel.image_id == a.id))).scalar_one() == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failure_after_create_rolls_back_the_comics_and_keeps_the_candidate_open(db_session, monkeypatch):
    service = ComicsCandidatesService(db_session)
    stem = f"A-{uuid.uuid4()}"
    candidate_id, _ = await _candidate(db_session, 3, stem)
    real = service.comics.add_image
    calls = []

    async def failing(comics_id, image_id):
        calls.append(image_id)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return await real(comics_id, image_id)

    monkeypatch.setattr(service.comics, "add_image", failing)
    with pytest.raises(RuntimeError):
        await service.accept(candidate_id, None)
    assert len(calls) == 2
    assert await _comics_count(db_session, f"Comics {stem}-0") == 0
    assert (await db_session.execute(select(func.count()).select_from(ComicsPanel).where(
        ComicsPanel.image_id.in_(select(Image.id).where(Image.filename.like(f"{stem}-%")))))).scalar_one() == 0
    assert await _state(db_session, candidate_id) == CandidateState.OPEN
    monkeypatch.undo()
    assert (await service.accept(candidate_id, None))["name"] == f"Comics {stem}-0"  # still acceptable afterwards


@pytest.mark.asyncio(loop_scope="session")
async def test_decided_and_unknown_candidates(db_session):
    service = ComicsCandidatesService(db_session)
    accepted_id, _ = await _candidate(db_session, 2)
    await service.accept(accepted_id, None)
    for call in (lambda: service.accept(accepted_id, None), lambda: service.dismiss(accepted_id)):
        with pytest.raises(CandidateError) as caught:
            await call()
        assert caught.value.code == "candidate_decided" and caught.value.detail == {"state": "accepted"}
        assert candidate_http_error(caught.value).status_code == 409

    dismissed_id, _ = await _candidate(db_session, 2)
    await service.dismiss(dismissed_id)
    await service.dismiss(dismissed_id)  # idempotent
    assert await _state(db_session, dismissed_id) == CandidateState.DISMISSED
    with pytest.raises(CandidateError) as caught:
        await service.accept(dismissed_id, None)
    assert caught.value.code == "candidate_decided"

    for call in (lambda: service.accept(uuid.uuid4(), None), lambda: service.dismiss(uuid.uuid4())):
        with pytest.raises(CandidateError) as caught:
            await call()
        assert candidate_http_error(caught.value).status_code == 404
