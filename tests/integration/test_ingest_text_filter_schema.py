"""Schema test for tmp_duplicates.tier_b_auto_kept_run (ingestion Tier B text filter, Task 1).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from Storage.models import Image, TmpDuplicates


@pytest.mark.asyncio(loop_scope="session")
async def test_tier_b_auto_kept_run_defaults_to_null_and_round_trips(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg", status="pending")
    b = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
    db_session.add_all([a, b])
    await db_session.flush()
    pair = TmpDuplicates(
        image_id1=min(a.id, b.id), image_id2=max(a.id, b.id), distance=0.07, match_source="cross_corpus",
    )
    db_session.add(pair)
    await db_session.flush()

    assert pair.tier_b_auto_kept_run is None

    pair_id = pair.id
    run_id = uuid.uuid4()
    pair.tier_b_auto_kept_run = run_id
    await db_session.flush()
    db_session.expire(pair)

    reloaded = await db_session.get(TmpDuplicates, pair_id)
    assert reloaded.tier_b_auto_kept_run == run_id
