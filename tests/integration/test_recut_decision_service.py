"""RecutDecisionService: approve and reject a recut proposal (screenshot recut R5a). Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from Backend.app.services.recut_decision_service import (
    ProposalConflictError, ProposalNotFoundError, RecutDecisionService)
from repository import process_events
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.popularity import PopularityRepository
from Storage.models import (
    CutState, Image, ImageCut, ImageExtras, ImageStatus, MemeTextRating, PopularityAbsorption, PopularitySource)


async def _proposal(session, *, state=CutState.PROPOSED, source_status=ImageStatus.ACTIVE,
                    cut_status=ImageStatus.CUT_PROPOSED, with_cut=True, with_source=True, run_id=None):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=source_status)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=cut_status)
    session.add_all([source, cut])
    await session.flush()
    row = ImageCut(
        source_image_id=source.id if with_source else None, cut_image_id=cut.id if with_cut else None,
        crop_x=10, crop_y=20, crop_w=300, crop_h=200, detector="margin_trim", detector_version="1",
        confidence=0.9, state=state, detect_run_id=run_id)
    session.add(row)
    await session.flush()
    return row, source, cut


async def _status(session, image_id):
    return (await session.execute(select(Image.status).where(Image.id == image_id))).scalar_one()


async def _flag(session, image_id):
    return (await ImageExtrasRepository(session).get_flags_bulk([image_id]))[image_id]


async def _remarks(session, image_id):
    return (await session.execute(select(ImageExtras.remarks).where(ImageExtras.image_id == image_id))).scalar_one()


async def _rating(session, image_id, query_hash="h" * 64):
    row = MemeTextRating(query_hash=query_hash, image_id=image_id, grade=2, rank=1, reading="parsed",
                         parser_version="p1", taxonomy_version=1, language="en")
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_activates_the_cut_flags_the_source_and_records_the_decision(db_session):
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).approve(row.id)

    assert result["id"] == str(row.id) and result["state"] == "approved" and isinstance(result["decidedAt"], str)
    await db_session.refresh(row)
    assert row.state == CutState.APPROVED and row.decided_at is not None
    assert await _status(db_session, cut.id) == ImageStatus.ACTIVE
    assert await _status(db_session, source.id) == ImageStatus.ACTIVE
    assert await _flag(db_session, source.id) is True
    assert await _remarks(db_session, source.id) == "recut_approved"
    assert await _flag(db_session, cut.id) is False
    assert await process_events.subject_has_any_event(db_session, "recut", row.id, ("approved",))


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_flags_the_cut_and_leaves_the_source_alone(db_session):
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).reject(row.id)

    assert result["state"] == "rejected"
    await db_session.refresh(row)
    assert row.state == CutState.REJECTED and row.decided_at is not None
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED
    assert await _flag(db_session, cut.id) is True
    assert await _remarks(db_session, cut.id) == "recut_rejected"
    assert await _flag(db_session, source.id) is False
    assert await process_events.subject_has_any_event(db_session, "recut", row.id, ("rejected",))
    assert not await process_events.subject_has_any_event(db_session, "recut", row.id, ("approved",))


@pytest.mark.asyncio(loop_scope="session")
async def test_the_event_is_attributed_to_the_detect_run(db_session):
    run_id = await BatchRunRepository(db_session).create_run("detect_recut_crops", "manual")
    row, _, _ = await _proposal(db_session, run_id=run_id)

    await RecutDecisionService(db_session).approve(row.id)

    counts = await process_events.count_events(db_session, "recut", [run_id])
    assert ("approved", 1) in [(event, count) for _, event, count in counts]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_decision_is_refused_and_changes_nothing(db_session):
    row, source, cut = await _proposal(db_session)
    service = RecutDecisionService(db_session)
    await service.approve(row.id)

    for call in (service.approve, service.reject):
        with pytest.raises(ProposalConflictError) as refused:
            await call(row.id)
        assert refused.value.code == "already_decided"
    await db_session.refresh(row)
    assert row.state == CutState.APPROVED
    assert await _flag(db_session, cut.id) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_id_is_not_found(db_session):
    with pytest.raises(ProposalNotFoundError):
        await RecutDecisionService(db_session).approve(uuid.uuid4())
    with pytest.raises(ProposalNotFoundError):
        await RecutDecisionService(db_session).reject(uuid.uuid4())


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("kind", ["approve", "reject"])
@pytest.mark.parametrize("make", [
    dict(with_cut=False), dict(cut_status=ImageStatus.ACTIVE), dict(cut_status=ImageStatus.REJECTED)])
async def test_a_missing_or_wrong_status_cut_is_refused_before_any_write(db_session, kind, make):
    row, source, cut = await _proposal(db_session, **make)

    with pytest.raises(ProposalConflictError) as refused:
        await getattr(RecutDecisionService(db_session), kind)(row.id)

    assert refused.value.code == "cut_missing"
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED and row.decided_at is None
    assert await _flag(db_session, source.id) is False and await _flag(db_session, cut.id) is False


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("make, code", [
    (dict(with_source=False), "source_missing"),
    (dict(source_status=ImageStatus.PENDING), "source_not_active"),
    (dict(source_status=ImageStatus.CUT_PROPOSED), "source_not_active"),
])
async def test_approve_needs_an_active_source(db_session, make, code):
    row, source, cut = await _proposal(db_session, **make)

    with pytest.raises(ProposalConflictError) as refused:
        await RecutDecisionService(db_session).approve(row.id)

    assert refused.value.code == code
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_refuses_a_source_that_is_already_flagged(db_session):
    row, source, cut = await _proposal(db_session)
    await ImageExtrasRepository(db_session).set_flagged(source.id, True, "duplicate_review")

    with pytest.raises(ProposalConflictError) as refused:
        await RecutDecisionService(db_session).approve(row.id)

    assert refused.value.code == "source_flagged"
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_works_without_a_source(db_session):
    row, _, cut = await _proposal(db_session, with_source=False)

    result = await RecutDecisionService(db_session).reject(row.id)

    assert result["state"] == "rejected" and await _flag(db_session, cut.id) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_moves_the_source_ratings_to_the_cut(db_session):
    row, source, cut = await _proposal(db_session)
    rating = await _rating(db_session, source.id)

    await RecutDecisionService(db_session).approve(row.id)

    await db_session.refresh(rating)
    assert rating.image_id == cut.id


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_keeps_the_ratings_with_the_source(db_session):
    row, source, cut = await _proposal(db_session)
    rating = await _rating(db_session, source.id)

    await RecutDecisionService(db_session).reject(row.id)

    await db_session.refresh(rating)
    assert rating.image_id == source.id


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_transfers_the_sources_popularity_to_the_cut(db_session):
    row, source, cut = await _proposal(db_session)
    copy = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    db_session.add(copy)
    await db_session.flush()
    await PopularityRepository(db_session).absorb(copy.id, source.id, PopularitySource.FILE_DUPLICATES)
    await db_session.refresh(source)
    assert source.popularity == 2

    await RecutDecisionService(db_session).approve(row.id)

    await db_session.refresh(cut)
    await db_session.refresh(source)
    assert cut.popularity == 2 and source.popularity == 0
    edge = (await db_session.execute(select(PopularityAbsorption).where(
        PopularityAbsorption.loser_image_id == source.id, PopularityAbsorption.winner_image_id == cut.id)
    )).scalar_one()
    assert edge.source == PopularitySource.RECUT and edge.weight == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_failure_does_not_block_the_approval(db_session, monkeypatch):
    async def broken(self, *args, **kwargs):
        raise RuntimeError("popularity down")
    monkeypatch.setattr(PopularityRepository, "absorb", broken)
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).approve(row.id)

    assert result["state"] == "approved"
    assert await _status(db_session, cut.id) == ImageStatus.ACTIVE and await _flag(db_session, source.id) is True


def test_the_proposal_lock_is_a_row_lock():
    # The integration fixture runs on one savepointed connection, so a two-connection race cannot be staged here.
    from Backend.app.repositories import recut_repository
    statement = recut_repository.lock_statement(uuid.uuid4())
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
