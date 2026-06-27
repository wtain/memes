import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
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

load_dotenv()

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [pid:%(process)d] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def _configure_logging() -> None:
    """Apply our log format to all loggers.

    Called inside the lifespan so it runs after uvicorn's dictConfig,
    which otherwise overwrites any basicConfig set at import time.
    """
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        lgr = logging.getLogger(name)
        for handler in lgr.handlers:
            handler.setFormatter(formatter)
    root = logging.getLogger()
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
    os.getenv('FRONTEND_ORIGIN'),
    os.getenv('ALTERNATIVE_FRONTEND_ORIGIN'),
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
