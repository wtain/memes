"""The build's start of comics_pipeline (epic multi-image-comics, C5;
docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md, "The trigger is best effort
and after the commit").

The same path as an admin trigger from /admin/batches (AdminBatchService.trigger_run: create the run row, commit it,
spawn the wrapper), in its own session so the build's session is never touched. The caller must have committed the build
first: the subprocess has to find the image row. Best effort: a pipeline that is already running is not an error (every
step is incremental, the image waits for the next run) and any other failure is logged; this never raises."""
import logging

from fastapi import HTTPException

from Backend.app.services.admin_batch_service import AdminBatchService
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from Storage.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

BATCH_NAME = "comics_pipeline"


async def start_comics_pipeline() -> str | None:
    """The new run id, or None when nothing was started."""
    try:
        async with AsyncSessionLocal() as session:
            result = await AdminBatchService(BatchRunRepository(session), session).trigger_run(BATCH_NAME)
        return result["run_id"]
    except HTTPException as error:
        if isinstance(error.__cause__, BatchAlreadyRunningError):
            logger.info("comics: %s is already running; the built image waits for the next run", BATCH_NAME)
            return None
        logger.warning("comics: could not start %s: %s", BATCH_NAME, error.detail)
        return None
    except Exception:
        logger.warning("comics: could not start %s", BATCH_NAME, exc_info=True)
        return None
