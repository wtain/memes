"""move_flagged moves a flagged image whatever its status, so a rejected recut cut (cut_proposed, flagged) is moved to
excluded/ like any other (screenshot recut R5b, OQ-7 audit). Requires a live PostgreSQL instance."""
import uuid

import pytest

import batch.move_flagged as move_flagged
from repository.image_extras import ImageExtrasRepository
from Storage.models import Image, ImageStatus


@pytest.mark.asyncio(loop_scope="session")
async def test_move_flagged_moves_a_flagged_cut_proposed_image(db_session, tmp_path):
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    db_session.add(cut)
    await db_session.flush()
    (tmp_path / cut.filename).write_bytes(b"x")
    await ImageExtrasRepository(db_session).set_flagged(cut.id, True, "recut_rejected")

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert not (tmp_path / cut.filename).exists()
    assert (tmp_path / "excluded" / cut.filename).exists()
    assert metrics.counters_dict().get("moved", 0) >= 1
