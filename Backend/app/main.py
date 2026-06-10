import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from Backend.app.api.diagnostics import router as diagnostics_router
from Backend.app.api.images import router as images_router
from Backend.app.api.concepts import router as concepts_router
from Backend.app.api.trends import router as trends_router

load_dotenv()

FRONTEND_ORIGIN = os.getenv('FRONTEND_ORIGIN')
ALTERNATIVE_FRONTEND_ORIGIN = os.getenv('ALTERNATIVE_FRONTEND_ORIGIN')

app = FastAPI(
    title="Memes API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        FRONTEND_ORIGIN,
        ALTERNATIVE_FRONTEND_ORIGIN,
        # "http://192.168.*.*",
        # "http://192.168.*.*:*",
        # "http://localhost:5173",
        # "http://localhost:5174",
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
app.include_router(images_router, prefix="/api")
app.include_router(concepts_router, prefix="/api")
app.include_router(trends_router, prefix="/api")


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app,
                host="127.0.0.1",
                port=8081)
