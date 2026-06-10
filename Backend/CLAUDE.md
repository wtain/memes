# Backend — dev notes for agents

## API documentation

All endpoints are documented in [`/backend_api.md`](../backend_api.md) at the repo root.

**Update `backend_api.md` whenever you add, remove, or change an endpoint** — its signature, query parameters, response shape, or behaviour. That file is the authoritative reference for frontend and Android clients.

---

## Architecture

The backend follows a strict three-layer pattern:

```
Router (app/api/)  →  Repository (app/repositories/)
```

There is no service layer unless business logic genuinely warrants it (transformation, aggregation across multiple repositories). For simple CRUD or diagnostic queries, the router calls the repository directly.

Each layer's responsibility:

- **Router**: request validation, dependency injection, response model construction. No raw SQL.
- **Repository**: all database queries using SQLAlchemy async ORM. Returns ORM rows or plain Python values — never Pydantic models.
- **Service** (optional): business logic that doesn't belong in either layer — e.g. cursor encoding, cross-repo joins, pagination.

Response models (Pydantic `BaseModel`) live in `app/types/generated/` if they are shared across routers, or inline in the router file if they are endpoint-specific.

---

## Database session

Sessions come from `Storage.db.get_async_db` via FastAPI `Depends`:

```python
from Storage.db import AsyncSessionLocal, get_async_db

async def get_my_repo(db: AsyncSessionLocal = Depends(get_async_db)):
    try:
        yield MyRepository(db)
    finally:
        pass  # commit/rollback handled by get_async_db
```

`get_async_db` commits on success and rolls back on exception — repositories must not call `session.commit()` themselves.

ORM models are in `Storage/models.py`. Do not redefine tables; import from there.

---

## Running the server

```powershell
# from the repo root (not the Backend/ dir)
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.metal --port 8081 --host 0.0.0.0
uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.general --port 8082 --host 0.0.0.0
```

Environment files live in `Storage/`. The two envs point to different databases (metal vs. general image set).

---

## Adding a new endpoint — checklist

1. Add the router file under `app/api/` (or extend an existing one).
2. Add the repository under `app/repositories/`.
3. Register the router in `app/main.py` with `app.include_router(..., prefix="/api")`.
4. **Update `backend_api.md`** with the new endpoint, parameters, and response shape.

---

## Before committing

There is no automated test gate for the backend yet. At minimum:

- Confirm the server starts without import errors (`uvicorn app.main:app`).
- Hit the new endpoint manually and verify the response shape matches what you documented.
- Check that existing endpoints still respond (smoke-test `/api/diagnostics/health` and `/api/images?limit=1`).
