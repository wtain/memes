import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from Storage.db import get_async_db

from Backend.app.api.diagnostics import router as diagnostics_router
from Backend.app.api.history import router as history_router
from Backend.app.api.images import router as images_router
from Backend.app.api.concepts import router as concepts_router
from Backend.app.api.recommendations import router as recommendations_router
from Backend.app.api.trends import router as trends_router
from Backend.app.api.uploads import router as uploads_router
from Backend.app.api.bug_reports import router as bug_reports_router
from Backend.app.api.ingestion import router as ingestion_router
from config.settings import settings

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_logging()
    yield


_extra_origins = [
    settings.get('GENERAL.FRONTEND_ORIGIN'),
    settings.get('ALTERNATIVE_FRONTEND_ORIGIN'),
]

app = FastAPI(
    title="Memes API",
    version="0.1.0",
    lifespan=lifespan,
)

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
app.include_router(concepts_router, prefix="/api")
app.include_router(recommendations_router, prefix="/api")
app.include_router(trends_router, prefix="/api")
app.include_router(uploads_router, prefix="/api")
app.include_router(bug_reports_router, prefix="/api")
app.include_router(ingestion_router, prefix="/api")


@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_async_db)):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app,
                host="127.0.0.1",
                port=8081)
