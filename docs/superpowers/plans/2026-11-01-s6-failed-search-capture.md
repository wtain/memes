# S6 Failed-Search Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture failed searches from the clients: the server returns a `searchId`, a save links to it, the web gets a "didn't find it" button, and a new `misses` view of the discovery report lists search sessions that ended without a save.

**Architecture:** One migration adds a nullable `meme_saves.search_history_id` and an append-only `search_not_found` table (no foreign keys). Search responses carry a nullable `searchId` (the id `record_history` already returns). Both writes are best-effort behind a `popularity.search_outcomes.enabled` switch. The outcome is derived after the fact: `misses` groups `search_history` rows into sessions by a time gap and reports sessions with no linked save.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, PostgreSQL; React/Vite/vitest; Android (Kotlin, Hilt, Retrofit, Compose, mockk).

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s6-failed-search-capture-design.md` (read its "Amendments found while planning" table: it overrides the earlier sections where they differ).

## Global Constraints

- Repositories never commit; `get_async_db` commits. The new writes run in a savepoint (`session.begin_nested()`), never fail the request, and log the exception type only (never the query).
- Append-only logs with no foreign keys: `search_not_found` and `meme_saves.search_history_id`.
- Migration id `b4c9e1f7a028`, `down_revision = 'a2f6d8c3b715'`. The owner runs it; nobody runs it against metal/general/it.
- Switch `popularity.search_outcomes.enabled` (bool, missing key = startup error). Off: no writes, `searchId` is null, endpoints still answer.
- `searchId` is null on the unfiltered browse feed (blank `q` and no facets), although a history row is written for it.
- Save link: `POST /api/images/meme/{id}/saves?searchId=<uuid>`; malformed `searchId` is ignored. Press: `POST /api/history/{searchId}/not-found` returns 204 for any id.
- `misses` defaults: `--gap-minutes 5`, `--kind search`, `--limit 50`, `--min-times 1`, sort `times`. It skips rows with no query text and no tags, ignores sessions older than the first linked save or press, and leaves out a session whose last row is newer than the gap.
- No ranking change, no change to `queries`/`memes` views.
- Python: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe`; integration tests need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`; run test roots separately (`Backend/tests`, `tests/integration`, `tests/docs`, `batch/tests`, `tests/rules`). Async tests use `@pytest.mark.asyncio(loop_scope="session")` and the rolled-back `db_session`.
- Frontend: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` (from `Frontend/memes-frontend`). Android: `gradlew :app:testDebugUnitTest --no-daemon` with `JAVA_HOME` = Android Studio JBR (and `ANDROID_HOME=C:\Users\ramiz\AppData\Local\Android\Sdk` in a fresh worktree).
- Worktree gotchas: no heredocs, no `cd` combined with git; use the Edit tool to append to files; fast-forward a new worktree to local `main` with `git merge --ff-only <sha>`.

## Review Focus

1. A blank-query browse row in `search_history` must never appear as a miss, and must not split or join real sessions (Task 4 test).
2. A session still inside the gap (last row recent) is not reported (Task 4 test).
3. Save with a `searchId` on a database where the column does not exist yet must still record the save (Task 2 test with the link forced to fail).
4. Unfiltered feed and switch-off both give `searchId: null` (Task 3 tests).
5. `/reply` refine and "show more" responses have no `searchId`; the web must keep the fresh search's id (Task 6 reducer test).

## File Structure

- Create `Storage/alembic/versions/b4c9e1f7a028_add_search_outcomes.py`: the migration.
- Modify `Storage/models.py`: `MemeSave.search_history_id` and index, new `SearchNotFound`.
- Create `repository/search_outcomes.py`: switch readers, `SearchNotFoundRepository`.
- Modify `repository/meme_saves.py`: `record(image_id, search_history_id=None)`.
- Create `Backend/app/services/outcome_recorder.py`: best-effort press writer.
- Modify `Backend/app/services/image_service.py`, `meme_from_text_service.py`, `Backend/app/api/images.py`, `history.py`, `Backend/app/main.py`: link, press, `searchId`.
- Modify `shared/schemas/memesearchresponse.schema.json`, `memefromtextresponse.schema.json` and the regenerated TS/Python/Kotlin types.
- Modify `repository/discovery_report.py`, `tools/discovery_report.py`: the `misses` view.
- Modify `tools/rollout_preflight.py`, runbook, `environments/settings.yaml`, docs.
- Web: `api/MemesApi.ts`, `api/http/HttpMemesApi.ts`, `test/mockApi.ts`, `components/NotFoundButton.tsx`, `components/MemesList.tsx`, `pages/SearchPage.tsx`, `pages/ReplyPage.tsx`, `reply/messageSearchReducer.ts`.
- Android: `MemeApiService.kt`, `MemeRepository.kt`, `SearchViewModel.kt`, `SearchScreen.kt`, `NavGraph.kt`, `MemeDetailViewModel.kt` and tests.

---

### Task 1: Schema, models, rollout guards

**Files:**
- Create: `Storage/alembic/versions/b4c9e1f7a028_add_search_outcomes.py`
- Modify: `Storage/models.py` (after `MemeSave`, ~line 247), `tools/rollout_preflight.py:36-37,140-141`, `docs/batches/use-cases/screenshot-recut-rollout.md:20-21,58`, `docs/image-derived-data.md:45-46`
- Test: `tests/integration/test_search_outcomes_migration.py`

**Interfaces:**
- Produces: `MemeSave.search_history_id` (nullable UUID), `SearchNotFound` ORM class (`id` UUID pk default uuid4, `search_history_id` UUID not null unique, `created_at` timestamptz server default now()).

- [ ] **Step 1: Write the failing test** `tests/integration/test_search_outcomes_migration.py`

```python
"""The search-outcomes migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "b4c9e1f7a028_add_search_outcomes.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_b4c9e1f7a028", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _norm(value):
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if not ch.isspace() and ch not in "()")


def _shape(connection, table):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns(table)}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)}
    uniques = sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table))
    return columns, indexes, uniques, inspector.get_foreign_keys(table)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_models_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            saves = await connection.run_sync(lambda s: _shape(s, "meme_saves"))
            outcomes = await connection.run_sync(lambda s: _shape(s, "search_not_found"))
            assert saves[3] == [] and outcomes[3] == []
            assert saves[0]["search_history_id"][1] is True  # nullable
            assert "ix_meme_saves_search_history_id" in saves[1]

            await connection.execute(text("DROP TABLE search_not_found"))
            await connection.execute(text("DROP INDEX ix_meme_saves_search_history_id"))
            await connection.execute(text("ALTER TABLE meme_saves DROP COLUMN search_history_id"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(lambda s: _shape(s, "meme_saves")) == saves
            assert await connection.run_sync(lambda s: _shape(s, "search_not_found")) == outcomes

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in inspect(sync).get_columns("meme_saves")})
            assert "search_not_found" not in tables and "search_history_id" not in columns
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "b4c9e1f7a028" and module.down_revision == "a2f6d8c3b715"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python.exe -m pytest tests/integration/test_search_outcomes_migration.py -v`
Expected: FAIL (migration file missing / model column missing).

- [ ] **Step 3: Models.** In `Storage/models.py` add to `MemeSave` the column `search_history_id = Column(UUID(as_uuid=True), nullable=True)` and `Index("ix_meme_saves_search_history_id", "search_history_id")` to its `__table_args__`, and update its docstring ("search_history_id links a save to the search that led to it (S6); no foreign key"). After `MemeSave` add:

```python
class SearchNotFound(Base):
    """Append-only log of "I didn't find it" presses on a search (epic meme-popularity, S6). One row per search
    (unique), never updated or deleted. No foreign key: clearing the search history must not erase the press."""
    __tablename__ = "search_not_found"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_history_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] **Step 4: Migration** `b4c9e1f7a028_add_search_outcomes.py` (header and typing imports as in `a2f6d8c3b715_add_meme_impressions.py`):

```python
def upgrade() -> None:
    op.add_column('meme_saves', sa.Column('search_history_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index('ix_meme_saves_search_history_id', 'meme_saves', ['search_history_id'])
    op.create_table(
        'search_not_found',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('search_history_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('search_history_id', name='uq_search_not_found_search_history_id'),
    )


def downgrade() -> None:
    op.drop_table('search_not_found')
    op.drop_index('ix_meme_saves_search_history_id', table_name='meme_saves')
    op.drop_column('meme_saves', 'search_history_id')
```
Set `Revises: a2f6d8c3b715`, `Create Date: 2026-11-01 15:00:00.000000`. In the model use `unique=True` only if autogenerate naming matches; if the shape test reports a unique-index vs constraint mismatch, switch the model to `UniqueConstraint("search_history_id", name="uq_search_not_found_search_history_id")` in `__table_args__` and drop `unique=True`.

- [ ] **Step 5: Run the migration test.** Expected: PASS.

- [ ] **Step 6: Rollout guards.** `tools/rollout_preflight.py`: add to `REVISION_LABELS` `"b4c9e1f7a028": "S6 search outcomes (same upgrade)"`; add to `EXPECTED`:
```python
    ("meme_saves", ("search_history_id",), "b4c9e1f7a028"),
    ("search_not_found", ("id", "search_history_id", "created_at"), "b4c9e1f7a028"),
```
Runbook: add `9. \`b4c9e1f7a028\` S6: \`search_not_found\` and \`meme_saves.search_history_id\`` after line 21, and change line 58's expected head to `b4c9e1f7a028`. `docs/image-derived-data.md`: add a `search_not_found` row after `meme_impressions` (same shape: none; not copied; no foreign keys; a cut starts with none; not applicable) and extend the `meme_saves` row's text with "its `search_history_id` links a save to a search (S6)".

- [ ] **Step 7: Run guards**
Run: `DATABASE_URL=... pytest tests/integration/test_rollout_preflight.py tests/integration/test_search_outcomes_migration.py -v` then `pytest tests/docs/ -v`
Expected: PASS (fix any "every name ties to models" or runbook-list failure by correcting the entry).

- [ ] **Step 8: Commit** `git add Storage docs tools tests && git commit -m "feat(popularity): S6 search outcomes schema, models and rollout guards"`

---

### Task 2: Server writes (link on save, press endpoint, switch)

**Files:**
- Create: `repository/search_outcomes.py`, `Backend/app/services/outcome_recorder.py`, `tests/integration/test_search_outcomes_server.py`
- Modify: `repository/meme_saves.py`, `Backend/app/services/image_service.py` (`record_save`, ~line 221), `Backend/app/api/images.py` (~line 227), `Backend/app/api/history.py`, `Backend/app/main.py` (~line 34, 92), `environments/settings.yaml` (~line 97)

**Interfaces:**
- Produces: `outcomes_enabled() -> bool`, `validate_outcome_settings() -> None`, `SearchNotFoundRepository(session).record(search_history_id: uuid.UUID) -> None`, `MemeSaveRepository.record(image_id, search_history_id=None)`, `record_not_found(session, search_id: str) -> None`, `ImageService.record_save(image_id: str, search_id: str | None = None)`.

- [ ] **Step 1: Write the failing tests** `tests/integration/test_search_outcomes_server.py`

```python
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services import outcome_recorder
from Backend.app.services.image_service import ImageService
from Storage.models import MemeSave, SearchNotFound
from tests.integration.test_popularity_repository import make_image

pytestmark = pytest.mark.asyncio(loop_scope="session")


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_save_stores_the_search_id(db_session):
    img, sid = await make_image(db_session, "a.jpg"), uuid.uuid4()
    await make_service(db_session).record_save(str(img.id), str(sid))
    row = (await db_session.execute(select(MemeSave))).scalars().one()
    assert row.search_history_id == sid


async def test_save_without_a_search_id_is_unchanged(db_session):
    img = await make_image(db_session, "a.jpg")
    await make_service(db_session).record_save(str(img.id))
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_malformed_search_id_is_ignored_and_the_save_counts(db_session):
    img = await make_image(db_session, "a.jpg")
    await make_service(db_session).record_save(str(img.id), "not-a-uuid")
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_failed_link_still_records_the_save(db_session):
    img = await make_image(db_session, "a.jpg")
    with patch("repository.meme_saves.MemeSaveRepository.record",
               side_effect=[RuntimeError("column missing"), None]) as record:
        await make_service(db_session).record_save(str(img.id), str(uuid.uuid4()))
    assert record.call_count == 2 and record.call_args_list[1].args == (img.id,)


async def test_switch_off_drops_the_link(db_session):
    img = await make_image(db_session, "a.jpg")
    with patch("Backend.app.services.image_service.outcomes_enabled", return_value=False):
        await make_service(db_session).record_save(str(img.id), str(uuid.uuid4()))
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_press_is_recorded_once(db_session):
    sid = uuid.uuid4()
    await outcome_recorder.record_not_found(db_session, str(sid))
    await outcome_recorder.record_not_found(db_session, str(sid))
    assert await count(db_session, SearchNotFound) == 1


async def test_press_for_an_unknown_id_is_stored_and_a_malformed_one_is_ignored(db_session):
    await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    await outcome_recorder.record_not_found(db_session, "not-a-uuid")
    assert await count(db_session, SearchNotFound) == 1


async def test_press_with_the_switch_off_writes_nothing(db_session):
    with patch("Backend.app.services.outcome_recorder.outcomes_enabled", return_value=False):
        await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    assert await count(db_session, SearchNotFound) == 0


async def test_a_failing_press_write_is_swallowed(db_session):
    with patch("repository.search_outcomes.SearchNotFoundRepository.record", side_effect=RuntimeError("boom")):
        await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    assert await count(db_session, SearchNotFound) == 0
```

- [ ] **Step 2: Run, watch fail.** Expected: FAIL (modules missing).

- [ ] **Step 3: Settings.** In `environments/settings.yaml` extend the `popularity:` block: `  search_outcomes: {enabled: true}` with a comment line pointing at the S6 spec. If `settings.<env>.yaml` files define `popularity`, check they do not need the key (the common file merges).

- [ ] **Step 4: `repository/search_outcomes.py`**

```python
"""Search outcomes (epic meme-popularity, S6): the 'didn't find it' log and the popularity.search_outcomes.* setting."""
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from Storage.models import SearchNotFound


def outcomes_enabled() -> bool:
    """popularity.search_outcomes.enabled: whether saves link to their search and presses are stored (bool)."""
    raw = settings.get("popularity.search_outcomes.enabled")
    if not isinstance(raw, bool):
        raise ValueError(f"popularity.search_outcomes.enabled must be a bool, got {raw!r}")
    return raw


def validate_outcome_settings() -> None:
    """Load every outcome setting so a missing or invalid key is a startup error (called from the lifespan)."""
    outcomes_enabled()


class SearchNotFoundRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, search_history_id: uuid.UUID) -> None:
        """Append one press; a second press for the same search is a no-op. Flushes, never commits."""
        await self.session.execute(
            insert(SearchNotFound).values(search_history_id=search_history_id)
            .on_conflict_do_nothing(index_elements=["search_history_id"]))
        await self.session.flush()
```

- [ ] **Step 5: `repository/meme_saves.py`** — change `record` to:

```python
    async def record(self, image_id: uuid.UUID, search_history_id: uuid.UUID | None = None) -> None:
        """Append one save row, linked to the search that led to it when known (S6). Flushes, never commits."""
        values = {"image_id": image_id}
        if search_history_id is not None:
            values["search_history_id"] = search_history_id
        await self.session.execute(insert(MemeSave).values(**values))
        await self.session.flush()
```

- [ ] **Step 6: `Backend/app/services/outcome_recorder.py`**

```python
"""Best-effort recording of a 'didn't find it' press (epic meme-popularity, S6): inside a savepoint, so a failure
never fails the request and never poisons its transaction."""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from repository.search_outcomes import SearchNotFoundRepository, outcomes_enabled

logger = logging.getLogger(__name__)


async def record_not_found(session: AsyncSession, search_id: str) -> None:
    if not outcomes_enabled():
        return
    try:
        search_uuid = uuid.UUID(str(search_id))
    except ValueError:
        return
    try:
        async with session.begin_nested():
            await SearchNotFoundRepository(session).record(search_uuid)
    except Exception as error:
        # The exception type only: nothing user-derived reaches the log.
        logger.warning("not-found write failed: error=%s", type(error).__name__)
```

- [ ] **Step 7: `ImageService.record_save`** — add `import logging`, `logger = logging.getLogger(__name__)`, `from repository.search_outcomes import outcomes_enabled` (module-level name, tests patch it), and:

```python
    async def record_save(self, image_id: str, search_id: str | None = None) -> None:
        """Append one save for an existing image of any status (S2), linked to the search that led to it when
        `search_id` is a valid id and the switch is on (S6). No de-duplication: every call counts. A link that
        cannot be stored (for example before the migration) never costs the save."""
        image_uuid = await self._existing_image_uuid(image_id)
        repository = MemeSaveRepository(self.repo.session)
        link = None
        if search_id and outcomes_enabled():
            try:
                link = uuid.UUID(search_id)
            except ValueError:
                link = None
        if link is not None:
            try:
                async with self.repo.session.begin_nested():
                    await repository.record(image_uuid, link)
                return
            except Exception as error:
                logger.warning("save link failed: error=%s", type(error).__name__)
        await repository.record(image_uuid)
```

- [ ] **Step 8: Routes.** `images.py`: `record_meme_save(image_id: str, searchId: Optional[str] = Query(None, max_length=64), service=...)` calls `service.record_save(image_id, searchId)`. `history.py`: add

```python
@router.post("/{search_id}/not-found", status_code=204)
async def record_not_found_press(search_id: str, db: AsyncSession = Depends(get_async_db)):
    await record_not_found(db, search_id)
    return Response(status_code=204, headers=no_cache_headers())
```
with imports `Response` from fastapi, `no_cache_headers` from `Backend.app.services.cache`, `record_not_found` from `Backend.app.services.outcome_recorder`. Check no existing route `/{something}` on the history router conflicts (the router has only `GET ""`, DELETE variants — verify with `grep -n "@router" Backend/app/api/history.py`). `main.py`: import `validate_outcome_settings` and call it next to `validate_impression_settings()` in the lifespan.

- [ ] **Step 9: Router-level tests.** Add to `Backend/tests/test_images_endpoints.py` (mirror its existing saves test; grep `saves`): `POST /api/images/meme/{id}/saves?searchId=<uuid>` calls `service.record_save` with that value; without the parameter it passes `None`. Add to the history endpoint test file (grep `history` in `Backend/tests`) a test that `POST /api/history/<uuid>/not-found` returns 204 and awaits `record_not_found`.

- [ ] **Step 10: Run all green**
Run: `DATABASE_URL=... pytest tests/integration/test_search_outcomes_server.py -v` then `cd Backend && pytest tests/test_images_endpoints.py tests/test_history*.py -v`
Expected: PASS.

- [ ] **Step 11: Commit** `git commit -m "feat(popularity): S6 save link, not-found press and switch"`

---

### Task 3: `searchId` on search responses

**Files:**
- Modify: `shared/schemas/memesearchresponse.schema.json`, `shared/schemas/memefromtextresponse.schema.json`, `Backend/app/services/image_service.py:121-132`, `Backend/app/services/meme_from_text_service.py:92-110`, generated types
- Test: `Backend/tests/test_image_service.py` (near line 705), `Backend/tests/test_meme_from_text_service.py` (near line 207)

**Interfaces:**
- Consumes: `outcomes_enabled()` (Task 2), `record_history` ids.
- Produces: `searchId: str | None` on `MemeSearchResponse` and `MemeFromTextResponse` (TS `searchId?: string`, Kotlin `searchId: String? = null`).

- [ ] **Step 1: Schemas.** In both files add under `properties`: `"searchId": { "type": "string", "description": "Id of the search_history row of this search, when one was stored; null/absent for the browse feed or when outcome capture is off" }` (not in `required`). For `memesearchresponse` use `"type": ["string", "null"]`? Keep plain `"string"`; the server omits it when none (Pydantic default None is serialised as null; clients accept both).

- [ ] **Step 2: Regenerate all three type trees.** `bash Frontend/generate-types.sh`; `.venv311/Scripts/python.exe AndroidClient/scripts/generate_dtos.py`; the Python generator command is in `documents/generation.md` (run from `Backend/`). The Python generator rewrites every file with new timestamps: afterwards `git checkout` every file under `Backend/app/types/generated/` except `memesearchresponse.py`, `memefromtextresponse.py` and (if changed) `__init__.py`. Check `git diff --stat` shows only the two schemas, TS `all.d.ts`, Kotlin `Models.kt` and those Python files.

- [ ] **Step 3: Write failing tests.** Read the existing search tests in `Backend/tests/test_image_service.py` around line 705 (they patch `ImageService._record_history` and `record_impressions`) and add, in their style:
  - `search(q="cat")` with `_record_history` returning `uuid X` → `result.searchId == str(X)`.
  - `search(q=None, raw_facets=None)` (browse) with `_record_history` returning X → `result.searchId is None`.
  - `search(q="cat")` with `outcomes_enabled` patched to False (patch `Backend.app.services.image_service.outcomes_enabled`) → `searchId is None`.
  - `search(q="cat")` with `_record_history` returning None → `searchId is None`.
  In `test_meme_from_text_service.py` (the test at ~207 uses `AsyncMock(return_value=history_id)`): a fresh message → `response.searchId == str(history_id)`; switch off (patch `Backend.app.services.meme_from_text_service.outcomes_enabled`) → None; recorder returning None → None.

- [ ] **Step 4: Run, watch fail.** Expected: FAIL (`searchId` attribute/assertions).

- [ ] **Step 5: Implement.** In `ImageService.search`, after the impressions block:
```python
        if search_history_id is not None and ((q or "").strip() or tags) and outcomes_enabled():
            result.searchId = str(search_history_id)
```
(`outcomes_enabled` is already imported in Task 2.) In `meme_from_text_service.py` import `from repository.search_outcomes import outcomes_enabled` and before `model_validate` add `if search_history_id is not None and outcomes_enabled(): payload["searchId"] = str(search_history_id)`.

- [ ] **Step 6: Run** `cd Backend && pytest tests/test_image_service.py tests/test_meme_from_text_service.py -v` → PASS; then `cd Backend && pytest` whole root → PASS.

- [ ] **Step 7: Commit** `git commit -m "feat(popularity): S6 searchId on search responses and regenerated types"`

---

### Task 4: `misses` report queries

**Files:**
- Modify: `repository/discovery_report.py` (append)
- Test: `tests/integration/test_discovery_report_misses.py`

**Interfaces:**
- Produces:
```python
MISS_SORTS = ("times", "explicit", "last")

@dataclass(frozen=True)
class MissRow:
    query: str; tags: str; kind: str; times: int; explicit: int; searches: float
    first_at: datetime; last_at: datetime; web: int; android: int

@dataclass(frozen=True)
class MissesReport:
    rows: list[MissRow]
    status: str          # "ok" | "no_schema" | "no_outcomes"
    cutoff: datetime | None

async def misses_report(session, *, gap_minutes: int = 5, kind: str = "search", since_days: int | None = None,
                        min_times: int = 1, explicit_only: bool = False, sort: str = "times",
                        limit: int = 50) -> MissesReport
```

- [ ] **Step 1: Write the failing tests** `tests/integration/test_discovery_report_misses.py`:

```python
"""Discovery report, misses view (epic meme-popularity, S6): search sessions that ended without a save."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from repository.discovery_report import MISS_SORTS, misses_report
from Storage.models import MemeSave, SearchHistory, SearchHistoryTag, SearchNotFound

pytestmark = pytest.mark.asyncio(loop_scope="session")

ANCHOR = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)   # earliest linked outcome: the cut-off
T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)      # all session rows are well after it and long ended


async def anchor(session):
    session.add(SearchNotFound(search_history_id=uuid.uuid4(), created_at=ANCHOR))
    await session.flush()


async def hist(session, query, at, *, client="web", kind="search", tags=(), count=5):
    row = SearchHistory(searched_at=at, query=query, client=client, result_count=count, kind=kind,
                        tags=[SearchHistoryTag(category=c, value=v) for c, v in tags])
    session.add(row)
    await session.flush()
    return row.id


async def save_for(session, search_id):
    session.add(MemeSave(image_id=uuid.uuid4(), search_history_id=search_id))
    await session.flush()


async def press_for(session, search_id):
    session.add(SearchNotFound(search_history_id=search_id))
    await session.flush()


async def report(session, **kwargs):
    result = await misses_report(session, **kwargs)
    return {(row.query, row.tags): row for row in result.rows}


async def test_an_unsaved_session_is_a_miss_and_a_saved_one_is_not(db_session):
    await anchor(db_session)
    await hist(db_session, "lost", T)
    saved = await hist(db_session, "found", T + timedelta(hours=1))
    await save_for(db_session, saved)
    assert set(await report(db_session)) == {("lost", "")}


async def test_a_save_in_any_row_resolves_the_session(db_session):
    await anchor(db_session)
    first = await hist(db_session, "cat", T)
    await hist(db_session, "cat meme", T + timedelta(minutes=2))
    await save_for(db_session, first)
    assert await report(db_session) == {}


async def test_rows_within_the_gap_form_one_session_and_the_last_query_is_reported(db_session):
    await anchor(db_session)
    await hist(db_session, "ca", T)
    await hist(db_session, "cat", T + timedelta(minutes=4, seconds=59))
    rows = await report(db_session)
    assert set(rows) == {("cat", "")} and rows[("cat", "")].times == 1 and rows[("cat", "")].searches == 2


async def test_a_pause_longer_than_the_gap_starts_a_new_session(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T)
    await hist(db_session, "cat", T + timedelta(minutes=5, seconds=1))
    assert (await report(db_session))[("cat", "")].times == 2


async def test_gap_minutes_is_an_option(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T)
    await hist(db_session, "cat", T + timedelta(minutes=8))
    assert (await report(db_session, gap_minutes=10))[("cat", "")].times == 1


async def test_clients_never_share_a_session(db_session):
    await anchor(db_session)
    await hist(db_session, "cat", T, client="web")
    await hist(db_session, "cat", T + timedelta(minutes=1), client="android")
    row = (await report(db_session))[("cat", "")]
    assert row.times == 2 and row.web == 1 and row.android == 1


async def test_browse_rows_are_not_searches_and_do_not_join_sessions(db_session):
    await anchor(db_session)
    await hist(db_session, None, T)
    await hist(db_session, "  ", T + timedelta(minutes=1))
    await hist(db_session, "cat", T + timedelta(minutes=2))
    assert set(await report(db_session)) == {("cat", "")}


async def test_a_facet_only_search_is_reported_with_its_tags(db_session):
    await anchor(db_session)
    await hist(db_session, None, T, tags=[("topic", "work"), ("emotion", "joy")])
    assert set(await report(db_session)) == {("", "emotion:joy,topic:work")}


async def test_queries_are_normalised_for_grouping(db_session):
    await anchor(db_session)
    await hist(db_session, "Grumpy  Cat", T)
    await hist(db_session, "grumpy cat", T + timedelta(hours=1))
    assert (await report(db_session))[("grumpy cat", "")].times == 2


async def test_a_press_marks_a_miss_explicit_and_a_save_wins_over_a_press(db_session):
    await anchor(db_session)
    pressed = await hist(db_session, "pressed", T)
    await press_for(db_session, pressed)
    both = await hist(db_session, "both", T + timedelta(hours=1))
    await press_for(db_session, both)
    await save_for(db_session, both)
    rows = await report(db_session)
    assert set(rows) == {("pressed", "")} and rows[("pressed", "")].explicit == 1
    assert set(await report(db_session, explicit_only=True)) == {("pressed", "")}


async def test_a_session_still_inside_the_gap_is_left_out(db_session):
    await anchor(db_session)
    now = datetime.now(timezone.utc)
    await hist(db_session, "open", now - timedelta(minutes=2))
    await hist(db_session, "ended", now - timedelta(minutes=10))
    assert set(await report(db_session)) == {("ended", "")}


async def test_sessions_before_the_first_outcome_are_ignored_and_the_cutoff_is_returned(db_session):
    await anchor(db_session)
    await hist(db_session, "old", ANCHOR - timedelta(days=1))
    await hist(db_session, "new", T)
    result = await misses_report(db_session)
    assert {r.query for r in result.rows} == {"new"} and result.cutoff == ANCHOR


async def test_without_any_outcome_the_status_says_so(db_session):
    await hist(db_session, "cat", T)
    result = await misses_report(db_session)
    assert result.rows == [] and result.status == "no_outcomes" and result.cutoff is None


async def test_missing_schema_is_reported_not_raised(db_session):
    await db_session.execute(text("DROP TABLE search_not_found"))
    result = await misses_report(db_session)
    assert result.rows == [] and result.status == "no_schema"


async def test_kind_filter(db_session):
    await anchor(db_session)
    await hist(db_session, "plain", T)
    await hist(db_session, "message", T, kind="meme_text")
    assert set(await report(db_session)) == {("plain", "")}
    assert set(await report(db_session, kind="meme_text")) == {("message", "")}
    assert set(await report(db_session, kind="all")) == {("plain", ""), ("message", "")}


async def test_since_days_min_times_and_limit(db_session):
    await anchor(db_session)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await hist(db_session, "recent", recent)
    await hist(db_session, "twice", T)
    await hist(db_session, "twice", T + timedelta(hours=1))
    assert set(await report(db_session, since_days=2)) == {("recent", "")}
    assert set(await report(db_session, min_times=2)) == {("twice", "")}
    assert len((await misses_report(db_session, limit=1)).rows) == 1


async def test_sorts(db_session):
    await anchor(db_session)
    a = await hist(db_session, "a-many", T)
    await hist(db_session, "a-many", T + timedelta(hours=1))
    b = await hist(db_session, "b-pressed", T + timedelta(hours=2))
    await press_for(db_session, b)
    await hist(db_session, "c-latest", T + timedelta(hours=3))
    order = lambda result: [r.query for r in result.rows]
    assert order(await misses_report(db_session, sort="times"))[0] == "a-many"
    assert order(await misses_report(db_session, sort="explicit"))[0] == "b-pressed"
    assert order(await misses_report(db_session, sort="last"))[0] == "c-latest"
    with pytest.raises(ValueError):
        await misses_report(db_session, sort="nope")
    assert MISS_SORTS == ("times", "explicit", "last")


async def test_option_validation(db_session):
    for bad in ({"gap_minutes": 0}, {"min_times": 0}, {"limit": 0}, {"kind": "x"}):
        with pytest.raises(ValueError):
            await misses_report(db_session, **bad)
```

- [ ] **Step 2: Run, watch fail.** `DATABASE_URL=... pytest tests/integration/test_discovery_report_misses.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** (append to `repository/discovery_report.py`; reuse the module's imports, add `from sqlalchemy import text` already present):

```python
MISS_SORTS = ("times", "explicit", "last")
_MISS_ORDER = {
    "times": "times DESC, last_at DESC",
    "explicit": "explicit DESC, times DESC, last_at DESC",
    "last": "last_at DESC, times DESC",
}

_SCHEMA_SQL = text("""
    SELECT to_regclass('public.search_not_found') IS NOT NULL
       AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'meme_saves' AND column_name = 'search_history_id')
""")
_CUTOFF_SQL = text("""
    SELECT least((SELECT min(created_at) FROM meme_saves WHERE search_history_id IS NOT NULL),
                 (SELECT min(created_at) FROM search_not_found))
""")

# Sessions: rows of one client and kind, a new session after a pause longer than :gap minutes. Rows with no query
# text and no tags are the browse feed, not searches (S6 A1). A session is open (and not reported) while its last
# row is newer than the gap (A2).
_MISSES_SQL = """
    WITH h AS (
        SELECT s.id, s.searched_at, s.kind, s.client,
               lower(regexp_replace(btrim(coalesce(s.query, '')), '\\s+', ' ', 'g')) AS qn,
               coalesce((SELECT string_agg(t.category || ':' || t.value, ',' ORDER BY t.category, t.value)
                         FROM search_history_tags t WHERE t.search_id = s.id), '') AS tag_text
        FROM search_history s
        WHERE (:kind = 'all' OR s.kind = :kind) AND s.searched_at >= :from_at
    ),
    r AS (SELECT * FROM h WHERE qn <> '' OR tag_text <> ''),
    g AS (
        SELECT r.*, CASE WHEN lag(r.searched_at) OVER w IS NULL
                           OR r.searched_at - lag(r.searched_at) OVER w > make_interval(mins => :gap) THEN 1 ELSE 0 END AS starts
        FROM r WINDOW w AS (PARTITION BY r.client, r.kind ORDER BY r.searched_at, r.id)
    ),
    sess AS (
        SELECT g.*, sum(g.starts) OVER (PARTITION BY g.client, g.kind ORDER BY g.searched_at, g.id) AS sid,
               EXISTS (SELECT 1 FROM meme_saves m WHERE m.search_history_id = g.id) AS has_save,
               EXISTS (SELECT 1 FROM search_not_found n WHERE n.search_history_id = g.id) AS has_press
        FROM g
    ),
    agg AS (
        SELECT client, kind, sid, count(*) AS searches, min(searched_at) AS first_at, max(searched_at) AS last_at,
               (array_agg(qn ORDER BY searched_at DESC, id DESC))[1] AS qn,
               (array_agg(tag_text ORDER BY searched_at DESC, id DESC))[1] AS tag_text,
               bool_or(has_save) AS saved, bool_or(has_press) AS pressed
        FROM sess GROUP BY client, kind, sid
    )
    SELECT qn AS query, tag_text AS tags, kind, count(*) AS times, count(*) FILTER (WHERE pressed) AS explicit,
           avg(searches)::float8 AS searches, min(first_at) AS first_at, max(last_at) AS last_at,
           count(*) FILTER (WHERE client = 'web') AS web, count(*) FILTER (WHERE client = 'android') AS android
    FROM agg
    WHERE NOT saved AND last_at <= now() - make_interval(mins => :gap)
      AND (NOT CAST(:explicit_only AS boolean) OR pressed)
    GROUP BY qn, tag_text, kind
    HAVING count(*) >= :min_times
    ORDER BY {order}, qn, tag_text
    LIMIT :limit
"""


@dataclass(frozen=True)
class MissRow:
    query: str
    tags: str
    kind: str
    times: int
    explicit: int
    searches: float
    first_at: datetime
    last_at: datetime
    web: int
    android: int


@dataclass(frozen=True)
class MissesReport:
    rows: list[MissRow]
    status: str            # "ok", "no_schema" (migration not run) or "no_outcomes" (no linked save or press yet)
    cutoff: datetime | None


async def misses_report(session: AsyncSession, *, gap_minutes: int = 5, kind: str = "search",
                        since_days: int | None = None, min_times: int = 1, explicit_only: bool = False,
                        sort: str = "times", limit: int = 50) -> MissesReport:
    """Search sessions that ended without a linked save, grouped by their last query and facet pairs. Reads
    search_history, meme_saves and search_not_found only."""
    if kind not in QUERY_KINDS:
        raise ValueError(f"unknown kind {kind!r}; choose one of {', '.join(QUERY_KINDS)}")
    if sort not in _MISS_ORDER:
        raise ValueError(f"unknown sort {sort!r}; choose one of {', '.join(MISS_SORTS)}")
    if gap_minutes < 1 or min_times < 1 or limit < 1:
        raise ValueError("gap_minutes, min_times and limit must be at least 1")
    if not (await session.execute(_SCHEMA_SQL)).scalar_one():
        return MissesReport([], "no_schema", None)
    cutoff = (await session.execute(_CUTOFF_SQL)).scalar_one()
    if cutoff is None:
        return MissesReport([], "no_outcomes", None)
    from_at = cutoff
    if since_days is not None:
        from_at = max(cutoff, datetime.now(timezone.utc) - timedelta(days=since_days))
    rows = (await session.execute(text(_MISSES_SQL.format(order=_MISS_ORDER[sort])), {
        "kind": kind, "from_at": from_at, "gap": gap_minutes, "explicit_only": explicit_only,
        "min_times": min_times, "limit": limit})).all()
    return MissesReport([MissRow(
        query=row.query, tags=row.tags, kind=row.kind, times=int(row.times), explicit=int(row.explicit),
        searches=float(row.searches), first_at=row.first_at, last_at=row.last_at, web=int(row.web),
        android=int(row.android)) for row in rows], "ok", cutoff)
```
If the `from_at` filter means a `since_days` window starts mid-session, that is accepted (documented).

- [ ] **Step 4: Run** `DATABASE_URL=... pytest tests/integration/test_discovery_report_misses.py -v` → all PASS. Mutation check: change `>` to `>=` in the gap comparison and confirm `test_rows_within_the_gap...` or the 5:01 test fails, then restore.

- [ ] **Step 5: Whole existing report tests** `pytest tests/integration/test_discovery_report_memes.py tests/integration/test_discovery_report_queries.py -v` → PASS.

- [ ] **Step 6: Commit** `git commit -m "feat(popularity): S6 misses view queries"`

---

### Task 5: `misses` command and docs

**Files:**
- Modify: `tools/discovery_report.py`, `docs/discovery-report.md`, `CLAUDE.md` (the `tools/discovery_report` entry)
- Test: `tests/integration/test_discovery_report_cli.py` (extend), `tests/integration/test_discovery_report_cli_db.py` (one case)

**Interfaces:**
- Consumes: `misses_report`, `MissRow`, `MISS_SORTS` (Task 4).

- [ ] **Step 1: Failing tests.** Read `test_discovery_report_cli.py` first and add, in its style: parser accepts `misses --env metal` with defaults (`gap_minutes == 5`, `kind == "search"`, `sort == "times"`, `limit == 50`, `explicit_only is False`); rejects `--gap-minutes 0`; `--explicit-only` sets the flag; `miss_table(rows)` returns headers `["query","tags","kind","times","explicit","searches","first_at","last_at","web","android"]` and renders `(no text)` for an empty query and `searches` with one decimal; `_run_session` for `misses` on a prepared session: with seeded data prints the title containing `gap 5 min` and the cut-off date, exit 0; with `status == "no_schema"` prints the one-line message and exits 0; with `no_outcomes` prints that message and exits 0; `--csv` writes the header and prints the search-text reminder on stderr. In `test_discovery_report_cli_db.py` add one run of `main(["misses", "--env", "metal"])` through the real read-only role path, mirroring the existing case for `queries`.

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Implement.** Import `misses_report, MISS_SORTS, MissRow` in `tools/discovery_report.py`; add `MISS_HEADERS = ["query","tags","kind","times","explicit","searches","first_at","last_at","web","android"]`; `miss_table(rows)`; parser:

```python
    misses = commands.add_parser("misses", parents=[common], help="search sessions that ended without a save")
    misses.add_argument("--gap-minutes", type=_positive, default=5, help="pause that ends a session (default 5)")
    misses.add_argument("--kind", choices=QUERY_KINDS, default="search")
    misses.add_argument("--since-days", type=_positive, default=None)
    misses.add_argument("--min-times", type=_positive, default=1)
    misses.add_argument("--explicit-only", action="store_true", help="only sessions with a web 'didn't find it' press")
    misses.add_argument("--sort", choices=MISS_SORTS, default="times")
```
and a third branch in `_run_session`:

```python
        elif args.command == "misses":
            report = await misses_report(
                session, gap_minutes=args.gap_minutes, kind=args.kind, since_days=args.since_days,
                min_times=args.min_times, explicit_only=args.explicit_only, sort=args.sort, limit=args.limit)
            rows = report.rows
            headers, cells = miss_table(rows)
            cutoff = "n/a" if report.cutoff is None else report.cutoff.strftime("%Y-%m-%d")
            title = (f"Discovery report: misses ({args.env}, kind {args.kind}, gap {args.gap_minutes} min, "
                     f"outcome data since {cutoff}, {len(rows)} rows)")
            note = {"no_schema": "the S6 migration has not run here yet: no outcome data to report",
                    "no_outcomes": "no linked save or 'didn't find it' press has been recorded yet",
                    "ok": "sessions are a time-gap approximation; sessions before the outcome data began are invisible"
                    }[report.status]
```
(Restructure the existing `if/else` into `if memes / elif misses / else queries`.) The CSV reminder condition becomes `args.command in ("queries", "misses")`. Keep `except ValueError` handling.

- [ ] **Step 4: Docs.** `docs/discovery-report.md`: add a "Misses view" section (columns, options, how a session and a miss are decided, A1/A2 rules, cut-off, caveats: time-gap approximation, Android sends the link only from the Search list, web press is explicit, misses before the migration invisible). `CLAUDE.md`: extend the `tools/discovery_report` entry text to "`{memes,queries,misses}`" and add "(misses: search sessions that ended without a save; see docs/discovery-report.md)".

- [ ] **Step 5: Run** `DATABASE_URL=... pytest tests/integration/test_discovery_report_cli.py tests/integration/test_discovery_report_cli_db.py -v` → PASS; then `pytest tests/docs/ -v` → PASS.

- [ ] **Step 6: Commit** `git commit -m "feat(popularity): S6 misses command and docs"`

---

### Task 6: Web — types, API, button, pages

**Files:**
- Create: `Frontend/memes-frontend/src/components/NotFoundButton.tsx`, `NotFoundButton.test.tsx`
- Modify: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/components/MemesList.tsx`, `src/pages/SearchPage.tsx`, `src/pages/ReplyPage.tsx`, `src/reply/messageSearchReducer.ts` (+ test), `backend_api.md`

**Interfaces:**
- Consumes: generated `MemeSearchResponse.searchId?`, `MemeFromTextResponse.searchId?` (Task 3).
- Produces: `MemesApi.reportNotFound(searchId: string): Promise<void>`; `<NotFoundButton memesApi searchId />`; `MemesList` prop `onSearchId?: (id: string | null) => void`; reducer state `searchId: string | null`.

- [ ] **Step 1: Failing tests.**
  - `NotFoundButton.test.tsx` (style of `ForgetTextButton.test.tsx`): renders a button "I didn't find it"; click calls `memesApi.reportNotFound("sid")` once and then shows "Noted" and no button; a rejected call shows "Couldn't send, try again" and the button again; rendering with a new `key` (new searchId) shows the button again.
  - `HttpMemesApi` test (mirror `HttpMemesApi.saves.test.ts`): `reportNotFound("abc")` POSTs `/api/history/abc/not-found` and throws on non-2xx.
  - `messageSearchReducer.test.ts`: after `findStarted` the state's `searchId` is null; `succeeded` with mode `find` and `response.searchId = "s1"` sets `"s1"`; `succeeded` with mode `refine` or `more` and no `response.searchId` keeps `"s1"`; mode `find` with no `searchId` gives null.
  - `SearchPage.test.tsx`: with `memesApi.searchMemes` resolving `{items:[...], searchId:"s1"}` the "I didn't find it" button shows; with `searchId` absent it does not.
  - `ReplyPage.test.tsx`: same two cases using `searchMemeFromText`.

- [ ] **Step 2: Run, watch fail** (`vitest run` for those files).

- [ ] **Step 3: Implement.**
  - `MemesApi.ts`: `reportNotFound(searchId: string): Promise<void>`. `HttpMemesApi.ts`: `fetch(\`${this.baseUrl}/api/history/${encodeURIComponent(searchId)}/not-found\`, {method: "POST"})`, throw `new Error(\`Failed to report: ${response.status}\`)` when not ok. `mockApi.ts`: add `reportNotFound: vi.fn().mockResolvedValue(undefined)` in the same place the other methods are defaulted.
  - `NotFoundButton.tsx`:
```tsx
import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"

type Props = { memesApi: MemesApi; searchId: string }
type Phase = "idle" | "sending" | "noted" | "failed"

export default function NotFoundButton({ memesApi, searchId }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")

  async function press() {
    setPhase("sending")
    try {
      await memesApi.reportNotFound(searchId)
      setPhase("noted")
    } catch {
      setPhase("failed")
    }
  }

  if (phase === "noted") return <p role="status" className="py-3 text-sm text-gray-600">Noted</p>
  return (
    <div className="flex flex-wrap items-center gap-2 py-3 text-sm">
      <button type="button" disabled={phase === "sending"} onClick={press} className="min-h-11 rounded border px-3">
        I didn't find it
      </button>
      {phase === "failed" && <span role="status">Couldn't send, try again</span>}
    </div>
  )
}
```
  Parents render it with `key={searchId}` so a new search resets it.
  - `MemesList.tsx`: add optional prop `onSearchId`; in `fetchPage`: in the short-filter early return call `onSearchId?.(null)`; in the `searchMemes` branch (only) after the response call `onSearchId?.(response.searchId ?? null)`; add `onSearchId` to the `useCallback` deps. Other list modes do not call it.
  - `SearchPage.tsx`: `const [searchId, setSearchId] = useState<string | null>(null)`; pass `onSearchId={setSearchId}` (wrap in `useCallback` if the React Compiler lint rule requires stable identity); under `MemesList` render `{searchId && <NotFoundButton key={searchId} memesApi={memesApi} searchId={searchId} />}`.
  - `messageSearchReducer.ts`: add `searchId: string | null` to state (initial null, and wherever the state is rebuilt from storage it starts null); `findStarted` sets null; `succeeded` sets `mode === "find" ? (response.searchId ?? null) : (response.searchId ?? state.searchId)`. `ReplyPage.tsx`: under the results grid (inside `showResults`, after the list and "Show more"), render `{state.searchId && <NotFoundButton key={state.searchId} memesApi={memesApi} searchId={state.searchId} />}`. Do not persist `searchId` to sessionStorage (a restored search runs a refine whose response has no id; it simply shows no button).
  - `backend_api.md`: document `searchId` on both search responses, the `searchId` query parameter of the save, and `POST /api/history/{searchId}/not-found` (204; best-effort; switch).

- [ ] **Step 4: Gates** (from `Frontend/memes-frontend`): `pnpm exec tsc -b`; `pnpm exec eslint src/ --max-warnings 0`; `pnpm exec vitest run` → all green. Then `git diff Frontend/memes-frontend/src/types/generated/` shows only the Task 3 change.

- [ ] **Step 5: Commit** `git commit -m "feat(popularity): S6 web not-found button on search and reply"`

---

### Task 7: Android — keep the id, pass it to the save

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt:69`, `data/repository/MemeRepository.kt:126`, `ui/search/SearchViewModel.kt`, `ui/search/SearchScreen.kt:97,231`, `ui/NavGraph.kt`, `ui/detail/MemeDetailViewModel.kt:~148`
- Test: `app/src/test/.../ui/search/SearchViewModelTest.kt`, `ui/detail/MemeDetailViewModelTest.kt`

**Interfaces:**
- Consumes: generated `MemeSearchResponse.searchId: String? = null` (Task 3).
- Produces: `MemeRepository.recordSave(id: String, searchId: String? = null)`; `SearchUiState.searchId: String?`; nav route `detail/{memeId}?source={source}&searchId={searchId}`.

- [ ] **Step 1: Failing tests.**
  - `SearchViewModelTest`: a search response with `searchId = "s1"` puts `state.searchId == "s1"`; `loadMore()` whose response has `searchId = "s2"` makes it `"s2"`, and one with no id keeps `"s1"`; a new query whose response has no id sets it null; clearing the query (blank) after a result sets it null; an environment switch clears it before the new search lands. Mirror the file's existing fake-repo/mockk style and coroutine test scheduler use.
  - `MemeDetailViewModelTest` (handle `SavedStateHandle(mapOf("memeId" to "meme-1", "searchId" to "s1"))`): after a successful `saveToGallery`, `coVerify(exactly = 1) { repo.recordSave("meme-1", "s1") }`. Existing tests with no `searchId` keep `repo.recordSave("meme-1")` verifications (default null) and must still pass unchanged.

- [ ] **Step 2: Run, watch fail** (`gradlew :app:testDebugUnitTest --no-daemon --tests "*SearchViewModelTest" --tests "*MemeDetailViewModelTest"`).

- [ ] **Step 3: Implement.**
  - `MemeApiService`: `suspend fun recordSave(@Path("id") id: String, @Query("searchId") searchId: String? = null): Response<Unit>` (keep the existing `@POST`). `MemeRepository.recordSave(id: String, searchId: String? = null)` passes it to `api.recordSave(id, searchId)`; the log line stays.
  - `SearchUiState`: add `val searchId: String? = null`. In `triggerSearch`: the start-of-search update also sets `searchId = null`; on success set `searchId = response.searchId`. In `loadMore` success: `searchId = response.searchId ?: it.searchId`. In the environment-switch branch (`isSwitch`) also set `searchId = null`.
  - `SearchScreen`: change the screen parameter to `onMemeClick: (String, String?) -> Unit` (line 97) and at the call that passes it down (line ~231) pass `onMemeClick = { id -> onMemeClick(id, state.searchId) }`; the inner grid composable (line ~487) stays `(String) -> Unit`. Use the actual local name of the collected UI state at that site.
  - `NavGraph`: search route `onMemeClick = { memeId, searchId -> navController.navigate(if (searchId != null) "detail/$memeId?searchId=$searchId" else "detail/$memeId") }`; detail route becomes `"detail/{memeId}?source={source}&searchId={searchId}"` with `navArgument("searchId") { type = NavType.StringType; nullable = true; defaultValue = null }`; the `popUpTo("detail/{memeId}?source={source}")` string must be updated to the new route pattern; `onNavigateToMeme` keeps `detail/$id?source=$source` (similar-meme hops carry no link). Flagged and Recommendations lists keep `detail/$memeId`.
  - `MemeDetailViewModel`: `private val searchId: String? = savedStateHandle["searchId"]`; the call becomes `runCatching { repo.recordSave(meme.id, searchId) }`.

- [ ] **Step 4: Run the whole Android unit suite**: `gradlew :app:testDebugUnitTest --no-daemon` (JAVA_HOME set) → green.

- [ ] **Step 5: Commit** `git commit -m "feat(popularity): S6 Android keeps the search id and sends it with a save"`

---

### Task 8: Finish — docs, status, full verification

**Files:**
- Modify: `CLAUDE.md` (the Configuration paragraph that mentions `popularity.impressions.*`), `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s6-failed-search-capture-design.md` (Status, Plan link), `.../README.md` (S6 row `done`, follow-up `(done)`), this plan's header if needed

- [ ] **Step 1:** `CLAUDE.md`: after the impressions sentence add: "Saves and failed searches are linked by `searchId` (`popularity.search_outcomes.enabled`; best effort; the `misses` view of the discovery report reads them; see `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s6-failed-search-capture-design.md`)."
- [ ] **Step 2:** Spec: `Status: planned` → leave `implementation`/`done` to the executor; add `Plan: docs/superpowers/plans/2026-11-01-s6-failed-search-capture.md` under the status line (the executor sets `done` after the merge).
- [ ] **Step 3: Full roots, each separately** from the repo root: `DATABASE_URL=... pytest tests/integration/ -q`; `cd Backend && pytest -q`; `pytest tests/docs/ -q`; `pytest batch/tests/ -q`; `pytest tests/rules/ -q`; frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; Android `testDebugUnitTest`. Record counts in the ledger. Expected: all green (baseline before S6: integration 1306, Backend 644, docs 15, batch 347, rules 144, vitest 791).
- [ ] **Step 4: Commit** `git commit -m "docs(popularity): S6 docs and plan link"`

## Self-review

- **Spec coverage:** data and API (Tasks 1–3), clients (Tasks 6–7), `misses` (Tasks 4–5), switch (Tasks 2–3), testing and docs (every task, Task 8), rollout (Task 1 guards), amendments A1–A5 (Tasks 2, 3, 4, 6).
- **Placeholders:** none; where a test must follow an existing file's style the step names the file and what to assert.
- **Type consistency:** `record_save(image_id, search_id)`, `record_not_found(session, search_id)`, `outcomes_enabled()`, `MissesReport(rows, status, cutoff)`, `reportNotFound(searchId)`, `recordSave(id, searchId)` are used identically across tasks.
