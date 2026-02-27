import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.images import router as images_router

app = FastAPI(
    title="Memes API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# uvicorn app.main:app --reload --env-file app/.env --port 8081
app.include_router(images_router, prefix="/api")

if __name__ == "__main__":
    uvicorn.run(app,
                host="127.0.0.1",
                port=8081)
