import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from Storage.db import AsyncSessionLocal, get_async_db, log_pool_bounds

from Backend.app.error_handlers import validation_error_handler
from Backend.app.api.diagnostics import router as diagnostics_router
from Backend.app.api.history import router as history_router
from Backend.app.api.images import router as images_router
from Backend.app.api.meme_from_text import router as meme_from_text_router
from Backend.app.api.concepts import router as concepts_router
from Backend.app.api.recommendations import router as recommendations_router
from Backend.app.api.bug_reports import router as bug_reports_router
from Backend.app.observability import enabled as metrics_enabled
from Backend.app.middleware import BodyLimitMiddleware, ClientContextMiddleware
from Backend.app.prod_startup import verify_environment_marker
from Backend.app.scheduler import start_scheduler, stop_scheduler
from Backend.app.services.meme_from_text_pipeline import get_pipeline
from Backend.app.services.query_parser import get_query_parser
from Backend.app.stage_policy import make_router
from config.settings import settings
from config.stage import is_prod
from repository.meme_impressions import validate_impression_settings
from repository.search_outcomes import validate_outcome_settings
from repository.search_ranking import validate_search_settings

if not is_prod():
    # Routers that exist only on dev (P5a-1, second layer): on prod they are neither imported (their modules pull
    # batch code the prod image does not carry) nor mounted, so their paths match no route at all. Their routes
    # also carry dev_only verdicts in Backend/app/stage_policy.py. Keep DEV_ONLY_ROUTER_MODULES there in step.
    from Backend.app.api.meme_text_ratings import router as meme_text_ratings_router
    from Backend.app.api.meme_profiles import router as meme_profiles_router
    from Backend.app.api.admin_profiles import router as admin_profiles_router
    from Backend.app.api.admin_discovery import router as admin_discovery_router
    from Backend.app.api.trends import router as trends_router
    from Backend.app.api.uploads import router as uploads_router
    from Backend.app.api.ingestion import router as ingestion_router
    from Backend.app.api.admin import router as admin_router
    from Backend.app.api.recut import router as recut_router
    from Backend.app.api.comics import router as comics_router

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [pid:%(process)d] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"

# uvicorn.error is uvicorn's general-purpose logger (historical name — it predates
# their use of stderr). Map it to "uvicorn" so INFO lines don't look like errors.
_LOGGER_NAME_ALIASES = {"uvicorn.error": "uvicorn"}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord(record.__dict__)
        record.name = _LOGGER_NAME_ALIASES.get(record.name, record.name)
        return super().format(record)


def _configure_logging() -> None:
    """Apply our log format to all loggers.

    Called inside the lifespan so it runs after uvicorn's dictConfig,
    which otherwise overwrites any basicConfig set at import time.
    Iterates every registered logger so we don't rely on uvicorn's
    internal logger names being stable across versions.
    """
    formatter = _Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    root = logging.getLogger()
    all_loggers = [root] + [
        lgr for lgr in logging.Logger.manager.loggerDict.values()
        if isinstance(lgr, logging.Logger)
    ]
    for lgr in all_loggers:
        for handler in lgr.handlers:
            handler.setFormatter(formatter)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)


async def _warm_models(pipeline, parser, interval_minutes: float) -> None:
    """Keep the parse and embedding models loaded (Ollama keep_alive is 30 min); never raises."""
    while True:
        try:
            if parser is not None:
                await parser.warm_up()
            await pipeline.warm_up()
        except Exception as error:
            logging.getLogger(__name__).info("model warm-up failed: %s", type(error).__name__)
        await asyncio.sleep(interval_minutes * 60)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_logging()
    validate_search_settings()  # deliberately uncaught: a bad ranking setting must stop the app from starting
    validate_impression_settings()  # likewise for popularity.impressions.*
    validate_outcome_settings()  # and popularity.search_outcomes.*
    if is_prod():
        log_pool_bounds()   # per process; the container runs several workers, so the database sees workers x this
        # A wrong DATABASE_URL (another environment's database) must stop the container; deliberately uncaught.
        await verify_environment_marker(AsyncSessionLocal)
    try:
        get_query_parser()    # builds and registers the parser (no Ollama call); a failure must not stop the app
    except Exception as error:
        logging.getLogger(__name__).warning("query parser could not be built: %s", type(error).__name__)
    warm_task = None
    try:
        pipeline = get_pipeline()
        if pipeline is not None:
            warm_task = asyncio.create_task(_warm_models(
                pipeline, get_query_parser(), float(settings.get("search.meme_from_text.warm_interval_minutes"))))
    except Exception as error:
        logging.getLogger(__name__).warning("meme-from-text pipeline not started: %s", type(error).__name__)
    # The prod container runs no pipelines and no scheduler, whatever the settings say.
    scheduler_tasks = [] if is_prod() else await start_scheduler()
    yield
    if warm_task is not None:
        warm_task.cancel()
    await stop_scheduler(scheduler_tasks)


_extra_origins = [
    settings.get('GENERAL.FRONTEND_ORIGIN'),
    settings.get('ALTERNATIVE_FRONTEND_ORIGIN'),
]

_app_options: dict = {}
if is_prod():
    # No interactive docs or schema, and no 307 redirect from "/path/" to "/path" (a redirect could downgrade the
    # scheme behind the proxy and would let a trailing slash reach a different route than the policy names).
    _app_options = {"docs_url": None, "redoc_url": None, "openapi_url": None, "redirect_slashes": False}

app = FastAPI(
    title="Memes API",
    version="0.1.0",
    lifespan=lifespan,
    **_app_options,
)

app.add_exception_handler(RequestValidationError, validation_error_handler)

# Pure-ASGI, added before CORS so they sit inside it: the client address for the recording budget (abuse.py) and the body bound of
# /api/bug-reports (a limit of bug_reports.max_body_mb: 0 switches it off, as on dev).
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(ClientContextMiddleware)
if metrics_enabled():
    # Added after the two above, so it sits outside them: a request the body limit rejects is still counted.
    from Backend.app.observability.middleware import MetricsMiddleware

    app.add_middleware(MetricsMiddleware)

if is_prod():
    # Production hostnames only (settings cors.origins, which an env var such as CORS__ORIGINS can also set), no
    # localhost, no credentials. _extra_origins is not used here: GENERAL.FRONTEND_ORIGIN is a localhost URL in the
    # tracked developer overlays.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in (settings.get("cors.origins") or []) if o],
        allow_credentials=bool(settings.get("cors.allow_credentials", False)),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://127.0.0.1",
            *[o for o in _extra_origins if o],
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )

# set WATCHFILES_FORCE_POLLING=1
# uvicorn app.main:app --reload --env-file app/.env --port 8081
"""
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.metal --port 8081 --host 0.0.0.0
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.general --port 8082 --host 0.0.0.0
"""
app.include_router(diagnostics_router, prefix="/api")
app.include_router(history_router, prefix="/api")
app.include_router(images_router, prefix="/api")
app.include_router(meme_from_text_router, prefix="/api")
if not is_prod():
    app.include_router(meme_text_ratings_router, prefix="/api")
    app.include_router(meme_profiles_router, prefix="/api")
    app.include_router(admin_profiles_router, prefix="/api")
    app.include_router(admin_discovery_router, prefix="/api")
app.include_router(concepts_router, prefix="/api")
app.include_router(recommendations_router, prefix="/api")
if not is_prod():
    app.include_router(trends_router, prefix="/api")
    app.include_router(uploads_router, prefix="/api")
app.include_router(bug_reports_router, prefix="/api")
if not is_prod():
    app.include_router(ingestion_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(recut_router, prefix="/api")
    app.include_router(comics_router, prefix="/api")

# The root /health lives on a router of its own, mounted without a prefix, so that it is a StageRoute like every
# other route (policy key ("GET", "/health")).
root_router = make_router(mount_prefix="")


@root_router.get("/health")
async def health_check(db: AsyncSession = Depends(get_async_db)):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        # On prod the detail is fixed: the exception text can carry the DSN host or driver internals.
        detail = "Database unavailable" if is_prod() else f"Database unavailable: {exc}"
        raise HTTPException(status_code=503, detail=detail)
    return {"status": "healthy"}


app.include_router(root_router)

if __name__ == "__main__":
    uvicorn.run(app,
                host="127.0.0.1",
                port=8081)
