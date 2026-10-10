# Query log and gold-set jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log every fresh meme-from-text query in `search_history`, and turn `export-ratings` / `import-judgments` into tracked, admin-triggerable, scheduled batch jobs, with all affected documentation updated.

**Architecture:** `search_history` gets a `kind` column plus nullable parse/outcome columns (one additive migration). A shared best-effort recorder (extracted from `ImageService._record_history`) is used by search and by `MemeFromTextService`, which receives it by injection. The two evaluation CLI commands move their bodies into `evaluation/ratings_job.py`, which the CLI and two new batch modules (`batch/export_ratings.py`, `batch/import_judgments.py`) both call. The jobs are registered in `environments/batch_registry.yaml` and scheduled on `general` only through `settings.general.yaml`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, Dynaconf, pytest; Graphviz DOT rendered with `@viz-js/viz`.

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md` (read it first; it is the authority).

## Global Constraints

- Work only in the worktree `H:\workspace_sandbox\memes\.claude\worktrees\query-log-gold-jobs` on branch `query-log-gold-jobs`. The main checkout is shared with another session: never switch its branch and never touch its files.
- Python: the worktree has no venv. Run Python as `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe` (called `PY` below) with the worktree as the working directory. On Windows set `PYTHONIOENCODING=utf-8`.
- Never combine test roots in one pytest call. Roots: `Backend/` (run from `Backend/`), `tests/integration/` (needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`), `batch/tests/`, `tests/evaluation/`. Run the WHOLE root before the final commit of a task that touches shared code.
- Never use the live `metal` / `general` / `it` databases from tests or checks; never bind their ports. Subagents never receive the main `DATABASE_URL`.
- `Storage/models.py` is the only place ORM models are defined. Repositories never call `session.commit()`.
- The user's message text is never written to application log files and never appears in an error body. The only places it is stored are `search_history` (new) and `meme_text_ratings`.
- `backend_api.md` must match the routers in the same change.
- Hand-written response models in `Backend/app/api/history.py` are the real `response_model`; update them directly (no shared schema is involved).
- Commit messages end with these two lines:
  ```
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01WUvgSm19vfxX68CBg4XvxY
  ```
- Edit repository text files with the Edit/Write tools, not PowerShell `Get/Set-Content` (UTF-8 double-encoding).

---

### Task 1: Data layer for the query log

**Files:**
- Modify: `Storage/models.py` (class `SearchHistory`, around line 572)
- Create: `Storage/alembic/versions/f3a9c27b5d14_add_meme_text_query_log_columns.py`
- Modify: `Backend/app/repositories/history_repository.py`
- Test: `tests/integration/test_search_history_repository.py`

**Interfaces:**
- Consumes: existing `HistoryRepository.add(query, client, result_count, tags)` and `.list(limit, cursor_searched_at, cursor_id, client)`.
- Produces:
  - `HistoryRepository.add(query, client, result_count, tags, *, kind="search", language=None, query_hash=None, situation=None, degraded=None, fallback=None, parse_ms=None, retrieval_ms=None) -> None`
  - `HistoryRepository.list(limit, cursor_searched_at, cursor_id, client, kind="search")`; `kind` is `"search"`, `"meme_text"` or `"all"`.
  - `HistoryRepository.delete_by_query_hash(query_hash: str) -> int` (deletes only `kind='meme_text'` rows, returns the row count).
  - Model columns `SearchHistory.kind/language/query_hash/situation/degraded/fallback/parse_ms/retrieval_ms`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_search_history_repository.py`. Copy the module header, imports and the `@pytest.mark...` style from `tests/integration/test_build_meme_profile_embeddings.py` (same asyncio markers, `db_session` fixture).

```python
from sqlalchemy import func, select

from Backend.app.repositories.history_repository import HistoryRepository
from Storage.models import SearchHistory, SearchHistoryTag

HASH = "a" * 64
OTHER = "b" * 64


async def _add(repository, **overrides):
    values = {"query": "q", "client": "web", "result_count": 1, "tags": []}
    values.update(overrides)
    await repository.add(**values)


async def _list(repository, **overrides):
    values = {"limit": 50, "cursor_searched_at": None, "cursor_id": None, "client": None}
    values.update(overrides)
    return await repository.list(**values)


async def test_a_plain_search_row_defaults_to_kind_search(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="cats")
    await db_session.flush()
    rows = await _list(repository)
    assert [(row.query, row.kind) for row in rows] == [("cats", "search")]
    assert rows[0].query_hash is None and rows[0].degraded is None


async def test_a_meme_text_row_keeps_its_parse_and_outcome(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="boss moved the deadline", kind="meme_text", language="en", query_hash=HASH,
               situation="a deadline moved", degraded=False, fallback=None, parse_ms=700, retrieval_ms=120,
               result_count=20, tags=[("emotion", "stress"), ("topic", "work")])
    await db_session.flush()
    rows = await _list(repository, kind="meme_text")
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.language, row.query_hash, row.situation) == ("meme_text", "en", HASH, "a deadline moved")
    assert (row.degraded, row.fallback, row.parse_ms, row.retrieval_ms, row.result_count) == (False, None, 700, 120, 20)
    assert sorted((t.category, t.value) for t in row.tags) == [("emotion", "stress"), ("topic", "work")]


async def test_list_filters_by_kind_and_defaults_to_search(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="search one")
    await _add(repository, query="message one", kind="meme_text", query_hash=HASH)
    await db_session.flush()
    assert [r.query for r in await _list(repository)] == ["search one"]
    assert [r.query for r in await _list(repository, kind="meme_text")] == ["message one"]
    assert sorted(r.query for r in await _list(repository, kind="all")) == ["message one", "search one"]


async def test_delete_by_query_hash_removes_only_matching_meme_text_rows(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="mine", kind="meme_text", query_hash=HASH, tags=[("topic", "work")])
    await _add(repository, query="mine again", kind="meme_text", query_hash=HASH)
    await _add(repository, query="someone else", kind="meme_text", query_hash=OTHER)
    await _add(repository, query="search with same hash column", kind="search", query_hash=HASH)
    await db_session.flush()
    assert await repository.delete_by_query_hash(HASH) == 2
    await db_session.flush()
    remaining = sorted(r.query for r in await _list(repository, kind="all"))
    assert remaining == ["search with same hash column", "someone else"]
    assert (await db_session.execute(select(func.count()).select_from(SearchHistoryTag))).scalar_one() == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" $PY -m pytest tests/integration/test_search_history_repository.py -v`
Expected: FAIL (`kind` is an unexpected keyword / attribute).

- [ ] **Step 3: Implement the model columns**

In `Storage/models.py`, add to `SearchHistory` after `result_count`:

```python
    kind = Column(String(20), nullable=False, default="search", server_default="search")
    language = Column(String(8), nullable=True)
    query_hash = Column(String(64), nullable=True)
    situation = Column(Text, nullable=True)
    degraded = Column(Boolean, nullable=True)
    fallback = Column(String(32), nullable=True)
    parse_ms = Column(Integer, nullable=True)
    retrieval_ms = Column(Integer, nullable=True)
```

and extend `__table_args__`:

```python
    __table_args__ = (
        Index("ix_search_history_searched_at", searched_at.desc()),
        Index("ix_search_history_kind_searched_at", "kind", searched_at.desc()),
        Index("ix_search_history_query_hash", "query_hash"),
    )
```

- [ ] **Step 4: Write the migration**

Open `Storage/alembic/versions/e6b1c4d8f203_add_meme_text_ratings.py` and copy its header/typing style. Create `f3a9c27b5d14_add_meme_text_query_log_columns.py` with `revision = "f3a9c27b5d14"`, `down_revision = "e6b1c4d8f203"` (this is the only current head; confirm with `$PY -m alembic heads` from `Storage/` using an `.env`-free `DATABASE_URL` pointing at the test DB).

```python
def upgrade() -> None:
    op.add_column("search_history", sa.Column("kind", sa.String(20), nullable=False, server_default="search"))
    op.add_column("search_history", sa.Column("language", sa.String(8), nullable=True))
    op.add_column("search_history", sa.Column("query_hash", sa.String(64), nullable=True))
    op.add_column("search_history", sa.Column("situation", sa.Text(), nullable=True))
    op.add_column("search_history", sa.Column("degraded", sa.Boolean(), nullable=True))
    op.add_column("search_history", sa.Column("fallback", sa.String(32), nullable=True))
    op.add_column("search_history", sa.Column("parse_ms", sa.Integer(), nullable=True))
    op.add_column("search_history", sa.Column("retrieval_ms", sa.Integer(), nullable=True))
    op.create_index("ix_search_history_kind_searched_at", "search_history",
                    ["kind", sa.text("searched_at DESC")])
    op.create_index("ix_search_history_query_hash", "search_history", ["query_hash"])


def downgrade() -> None:
    op.drop_index("ix_search_history_query_hash", table_name="search_history")
    op.drop_index("ix_search_history_kind_searched_at", table_name="search_history")
    for column in ("retrieval_ms", "parse_ms", "fallback", "degraded", "situation", "query_hash", "language", "kind"):
        op.drop_column("search_history", column)
```

- [ ] **Step 5: Implement the repository changes**

In `Backend/app/repositories/history_repository.py` change `add` and `list`, and add `delete_by_query_hash` (add `delete` to the `sqlalchemy` import):

```python
    async def add(
        self,
        query: Optional[str],
        client: str,
        result_count: int,
        tags: list[tuple[str, str]],
        *,
        kind: str = "search",
        language: Optional[str] = None,
        query_hash: Optional[str] = None,
        situation: Optional[str] = None,
        degraded: Optional[bool] = None,
        fallback: Optional[str] = None,
        parse_ms: Optional[int] = None,
        retrieval_ms: Optional[int] = None,
    ) -> None:
        record = SearchHistory(
            query=query,
            client=client,
            result_count=result_count,
            kind=kind,
            language=language,
            query_hash=query_hash,
            situation=situation,
            degraded=degraded,
            fallback=fallback,
            parse_ms=parse_ms,
            retrieval_ms=retrieval_ms,
            tags=[SearchHistoryTag(category=category, value=value) for category, value in tags],
        )
        self.session.add(record)
```

In `list` add the parameter `kind: str = "search"` after `client`, and after the `client` filter:

```python
        if kind != "all":
            stmt = stmt.where(SearchHistory.kind == kind)
```

and:

```python
    async def delete_by_query_hash(self, query_hash: str) -> int:
        """Delete the meme-from-text log rows of one message (tags go with them through the FK cascade)."""
        result = await self.session.execute(
            delete(SearchHistory).where(SearchHistory.kind == "meme_text", SearchHistory.query_hash == query_hash))
        return result.rowcount
```

- [ ] **Step 6: Run the tests, then verify the migration**

Run the new test file: expected PASS. Then run the migration on the TEST database only:
`cd Storage && DATABASE_URL=<test url> $PY -m alembic upgrade head && DATABASE_URL=<test url> $PY -m alembic downgrade -1 && DATABASE_URL=<test url> $PY -m alembic upgrade head`
Expected: all three succeed. If `ocrdb_test` is managed by `create_all` rather than alembic, create a scratch database `ocrdb_migtest` on the same server instead; never run it on `ocrdb` or a live environment database.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/f3a9c27b5d14_add_meme_text_query_log_columns.py Backend/app/repositories/history_repository.py tests/integration/test_search_history_repository.py
git commit -m "feat(history): kind and parse/outcome columns on search_history, kind filter and delete by hash"
```

---

### Task 2: Shared recorder and meme-from-text logging

**Files:**
- Create: `Backend/app/services/history_recorder.py`
- Modify: `Backend/app/services/image_service.py` (`_record_history`, around line 98-112)
- Modify: `Backend/app/services/meme_from_text_service.py`
- Modify: `Backend/app/api/meme_from_text.py`
- Modify: `environments/settings.yaml` (block `search.meme_from_text`, line ~65-76)
- Test: `Backend/tests/test_history_recorder.py`, `Backend/tests/test_meme_from_text_service.py`, `Backend/tests/test_meme_from_text_endpoint.py`, `Backend/tests/test_query_log_setting.py`

**Interfaces:**
- Consumes: `HistoryRepository.add(...)` from Task 1.
- Produces:
  - `Backend.app.services.history_recorder.record_history(**fields) -> None`: best-effort (own session, errors swallowed); `fields` are the keyword arguments of `HistoryRepository.add`.
  - `MemeFromTextService(pipeline, retrieval_repository, image_repository, history_recorder=None)`; `search(request, user_agent: str = "")`.
  - Setting `search.meme_from_text.query_log.enabled` (bool, `true` in `settings.yaml`).

- [ ] **Step 1: Write the failing tests**

`Backend/tests/test_history_recorder.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

from Backend.app.services import history_recorder


def _session_factory(repository_add):
    session = MagicMock()
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


async def test_the_fields_reach_the_repository():
    add = AsyncMock()
    with patch.object(history_recorder, "AsyncSessionLocal", _session_factory(add)), \
         patch.object(history_recorder, "HistoryRepository") as repository_class:
        repository_class.return_value.add = add
        await history_recorder.record_history(query="q", client="web", result_count=2, tags=[("topic", "work")],
                                              kind="meme_text")
    add.assert_awaited_once_with(query="q", client="web", result_count=2, tags=[("topic", "work")], kind="meme_text")


async def test_a_failing_write_is_swallowed():
    with patch.object(history_recorder, "AsyncSessionLocal", side_effect=RuntimeError("db down")):
        await history_recorder.record_history(query="q", client="web", result_count=0, tags=[])
```

In `Backend/tests/test_meme_from_text_service.py` append (reuse the file's `_service`, `_result`, `_item`, `PROFILE`, `META` helpers; copy the way the first test builds a fake pipeline whose `search` returns `_result([_item()])` and a `Request`):

```python
PARSED = {**PROFILE, "language": "ru", "situation": "boss moved the deadline",
          "emotion": [{"key": "stress", "confidence": 0.9, "source": "model"}],
          "topic": [{"key": "work", "confidence": 0.8, "source": "model"}]}


async def test_a_fresh_message_is_recorded_with_parse_and_outcome():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()], profile=PARSED, meta={**META, "queryHash": "a" * 64}))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    await service.search(Request.model_validate({"text": "boss moved the deadline"}), user_agent="okhttp/4.12")
    recorder.assert_awaited_once_with(
        kind="meme_text", query="boss moved the deadline", client="android", result_count=1,
        tags=[("emotion", "stress"), ("topic", "work")], language="ru", query_hash="a" * 64,
        situation="boss moved the deadline", degraded=False, fallback=None, parse_ms=1, retrieval_ms=3)


async def test_pages_edits_and_filters_are_not_recorded():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    await service.search(Request.model_validate({"text": "x", "cursor": "abc"}))
    await service.search(Request.model_validate({"text": "x", "profile": PROFILE}))
    await service.search(Request.model_validate({"text": "x", "filters": {"emotion": ["joy"]}}))
    recorder.assert_not_awaited()


async def test_a_degraded_search_records_the_fallback_reason():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()], degraded=True, fallback="timeout"))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    await service.search(Request.model_validate({"text": "x"}))
    assert recorder.await_args.kwargs["degraded"] is True and recorder.await_args.kwargs["fallback"] == "timeout"


async def test_a_failing_recorder_never_fails_the_search():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    service.history_recorder = AsyncMock(side_effect=RuntimeError("boom"))
    response = await service.search(Request.model_validate({"text": "x"}))
    assert response.items


async def test_without_a_recorder_nothing_is_recorded_and_search_works():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    assert service.history_recorder is None
    assert (await service.search(Request.model_validate({"text": "x"}))).items
```

If the first test in that file shows the real fixture for items/pipeline differs from `MagicMock` + `AsyncMock`, follow the file's pattern; the assertions above are what matters. The unavailable path (`pipeline is None`) must also record nothing: add one assertion for it.

`Backend/tests/test_query_log_setting.py`:

```python
import pytest

from config.settings import _build


@pytest.mark.parametrize("environment", ["metal", "general", "it"])
def test_the_query_log_switch_is_defined_for_every_environment(environment):
    assert _build(environment).get("search.meme_from_text.query_log.enabled") is True
```

For the endpoint, add a test to `Backend/tests/test_meme_from_text_endpoint.py` (copy its client/override pattern) asserting that the `User-Agent` header reaches `service.search` as `user_agent` (the overridden service is a mock: `search.assert_awaited_once()` and `call.kwargs["user_agent"] == "okhttp/4.12"`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd Backend && $PY -m pytest tests/test_history_recorder.py tests/test_meme_from_text_service.py tests/test_query_log_setting.py tests/test_meme_from_text_endpoint.py -v`
Expected: FAIL (module missing, unknown keyword, missing setting).

- [ ] **Step 3: Implement the recorder and the ImageService delegation**

`Backend/app/services/history_recorder.py`:

```python
"""Best-effort write of one history row (search and meme-from-text). A failed write never fails the request."""
from Backend.app.repositories.history_repository import HistoryRepository
from Storage.db import AsyncSessionLocal


async def record_history(**fields) -> None:
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await HistoryRepository(session).add(**fields)
    except Exception:
        pass
```

In `image_service.py` replace the body of `_record_history` (keep its name, signature and `@staticmethod`, because `Backend/tests/test_image_service.py` patches it):

```python
        await record_history(query=q, client=client, result_count=result_count, tags=tags)
```

Add `from Backend.app.services.history_recorder import record_history` and remove the now-unused `HistoryRepository` and `AsyncSessionLocal` imports from `image_service.py` only if nothing else in the file uses them (grep first). Run `Backend/tests/test_image_service.py` and fix any patch target that pointed at the old inline session code.

- [ ] **Step 4: Implement the service change**

In `Backend/app/services/meme_from_text_service.py`:

```python
AXES = ("emotion", "stance", "topic", "tone")


def _has_filters(raw) -> bool:
    return raw is not None and any(getattr(raw, name, None) for name in (*AXES, "tags"))


def _is_fresh_message(request: MemeFromTextRequest) -> bool:
    """A new message: not a page of "Show more", not an edited reading, not a filter toggle."""
    return not request.cursor and request.profile is None and not _has_filters(request.filters)


def _history_fields(request: MemeFromTextRequest, result, item_count: int, user_agent: str) -> dict:
    profile = result.profile or {}
    meta = result.meta or {}
    return {
        "kind": "meme_text",
        "query": request.text,
        "client": "android" if "okhttp" in user_agent.lower() else "web",
        "result_count": item_count,
        "tags": [(axis, entry["key"]) for axis in AXES for entry in profile.get(axis) or []],
        "language": profile.get("language"),
        "query_hash": meta.get("queryHash"),
        "situation": profile.get("situation"),
        "degraded": result.degraded,
        "fallback": None if result.fallback in (None, "none") else result.fallback,
        "parse_ms": meta.get("parseMs"),
        "retrieval_ms": meta.get("retrievalMs"),
    }
```

Constructor gets `history_recorder=None` stored as `self.history_recorder`. Change `search` to `async def search(self, request, user_agent: str = "")` and, right after `items = [...]` and before building the payload's hydrate step finishes (anywhere after the pipeline call succeeded), add:

```python
        if self.history_recorder is not None and _is_fresh_message(request):
            try:
                await self.history_recorder(**_history_fields(request, result, len(items), user_agent))
            except Exception:
                pass
```

- [ ] **Step 5: Router and setting**

In `Backend/app/api/meme_from_text.py`: `from fastapi import APIRouter, Depends, Header, HTTPException, Response`, `from config.settings import settings`, `from Backend.app.services.history_recorder import record_history`.

```python
async def get_meme_from_text_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[MemeFromTextService, None]:
    recorder = record_history if settings.get("search.meme_from_text.query_log.enabled", False) else None
    yield MemeFromTextService(get_pipeline(), MemeRetrievalRepository(db), ImageRepository(db), recorder)


@router.post("/search", ...)
async def search_memes_from_text(
    request: MemeFromTextRequest,
    response: Response,
    user_agent: str = Header(default=""),
    service: MemeFromTextService = Depends(get_meme_from_text_service),
):
    ...
        return await service.search(request, user_agent=user_agent)
```

In `environments/settings.yaml`, inside `search.meme_from_text` (after `page`), add:

```yaml
    query_log: {enabled: true}    # record each fresh message in search_history (kind meme_text); see docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md
```

`retrieval/config.py` does not read this key (the retrieval package stays stdlib-pure), so no change there.

- [ ] **Step 6: Run the tests**

Run the four test files again (expected PASS), then the whole Backend root: `cd Backend && $PY -m pytest -q`. Also smoke-start check: `$PY -c "import Backend.app.main"` with `APP_ENV=general` and a dummy `DATABASE_URL`.

- [ ] **Step 7: Commit**

```bash
git add Backend/app/services/history_recorder.py Backend/app/services/image_service.py Backend/app/services/meme_from_text_service.py Backend/app/api/meme_from_text.py environments/settings.yaml Backend/tests/test_history_recorder.py Backend/tests/test_meme_from_text_service.py Backend/tests/test_meme_from_text_endpoint.py Backend/tests/test_query_log_setting.py
git commit -m "feat(meme-from-text): record fresh queries in search_history through a shared recorder"
```

---

### Task 3: Forget text clears the log; history endpoint `kind` filter

**Files:**
- Modify: `Backend/app/services/meme_text_rating_service.py` (class constructor and `forget_text`)
- Modify: `Backend/app/api/meme_text_ratings.py` (`get_rating_service`)
- Modify: `Backend/app/api/history.py`
- Modify: `backend_api.md` (sections "SearchHistoryItem", "SearchHistoryResponse" near line 143-180, and the `GET /api/history` endpoint section)
- Test: `Backend/tests/test_meme_text_rating_service.py`, `Backend/tests/test_meme_text_ratings_endpoint.py`, `Backend/tests/test_history_endpoint.py` (create if no history endpoint test file exists; grep `Backend/tests` for `/api/history` first and extend that file instead if it exists)

**Interfaces:**
- Consumes: `HistoryRepository.list(..., kind)` and `.delete_by_query_hash` from Task 1.
- Produces:
  - `MemeTextRatingService(repository, max_chars, history_repository=None)`; `forget_text(hash)` also calls `history_repository.delete_by_query_hash(hash)` when a repository is given.
  - `GET /api/history?kind=search|meme_text|all` (default `search`), items gain optional `kind, language, queryHash, situation, degraded, fallback, parseMs, retrievalMs`.

- [ ] **Step 1: Write the failing tests**

Service test (mirror the file's existing fake-repository style):

```python
async def test_forget_text_also_deletes_the_query_log_rows():
    repository = MagicMock(); repository.forget_text = AsyncMock(return_value=2)
    history = MagicMock(); history.delete_by_query_hash = AsyncMock(return_value=1)
    service = MemeTextRatingService(repository, 600, history)
    await service.forget_text("a" * 64)
    repository.forget_text.assert_awaited_once_with("a" * 64)
    history.delete_by_query_hash.assert_awaited_once_with("a" * 64)


async def test_forget_text_without_a_history_repository_still_works():
    repository = MagicMock(); repository.forget_text = AsyncMock(return_value=0)
    await MemeTextRatingService(repository, 600).forget_text("a" * 64)
    repository.forget_text.assert_awaited_once()
```

Endpoint test for history (use the app's `TestClient` pattern from another Backend endpoint test and override `get_history_repo` with a mock whose `list` is an `AsyncMock(return_value=[row])`; build `row` with `SimpleNamespace(id=uuid4(), searched_at=datetime(...), query="q", client="web", result_count=3, tags=[SimpleNamespace(category="topic", value="work")], kind="meme_text", language="en", query_hash="a"*64, situation="s", degraded=False, fallback=None, parse_ms=5, retrieval_ms=7)`):

```python
def test_default_kind_is_search_and_extra_fields_are_returned():
    ...GET /api/history
    assert repo.list.await_args.kwargs["kind"] == "search"
    item = response.json()["items"][0]
    assert item["kind"] == "meme_text" and item["queryHash"] == "a" * 64 and item["parseMs"] == 5


def test_kind_meme_text_and_all_are_passed_through():
    ...GET /api/history?kind=meme_text  -> kwargs["kind"] == "meme_text"
    ...GET /api/history?kind=all        -> kwargs["kind"] == "all"


def test_an_unknown_kind_is_a_422():
    assert client.get("/api/history?kind=bogus").status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd Backend && $PY -m pytest tests/test_meme_text_rating_service.py tests/test_history_endpoint.py -v` (use the real file names). Expected: FAIL.

- [ ] **Step 3: Implement**

`MemeTextRatingService.__init__(self, repository, max_chars: int, history_repository=None)` stores `self.history_repository`. `forget_text`:

```python
    async def forget_text(self, hash_value: str) -> None:
        digest = _hash(hash_value)
        await self.repository.forget_text(digest)
        if self.history_repository is not None:
            await self.history_repository.delete_by_query_hash(digest)
```

`get_rating_service` in `meme_text_ratings.py`:

```python
    yield MemeTextRatingService(MemeTextRatingRepository(db), int(settings.get("parse.max_input_chars")),
                                HistoryRepository(db))
```
(import `HistoryRepository` from `Backend.app.repositories.history_repository`). Both repositories share the request session, so the deletes commit together.

`history.py`: add to `SearchHistoryItemResponse`:

```python
    kind: str = "search"
    language: Optional[str] = None
    queryHash: Optional[str] = None
    situation: Optional[str] = None
    degraded: Optional[bool] = None
    fallback: Optional[str] = None
    parseMs: Optional[int] = None
    retrievalMs: Optional[int] = None
```

the endpoint parameter `kind: Literal["search", "meme_text", "all"] = "search"` (import `Literal`), passed as `kind=kind` to `repo.list`, and in the item construction `kind=r.kind, language=r.language, queryHash=r.query_hash, situation=r.situation, degraded=r.degraded, fallback=r.fallback, parseMs=r.parse_ms, retrievalMs=r.retrieval_ms`.

`backend_api.md`: document the `kind` parameter (values, default, what `meme_text` rows contain: pasted message text, parse, outcome), the new optional item fields, and add a sentence under the ratings "forget text" endpoint saying it also deletes the message's rows from the query log. State plainly that the history endpoint is unauthenticated, as are all endpoints (link `docs/security/admin-permissions-todo.md`).

- [ ] **Step 4: Run tests**

Run the touched test files, then the whole Backend root `cd Backend && $PY -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/meme_text_rating_service.py Backend/app/api/meme_text_ratings.py Backend/app/api/history.py backend_api.md Backend/tests
git commit -m "feat(history): kind filter and new fields on /api/history; forget text also clears the query log"
```

---

### Task 4: Shared gold-set functions for the CLI and the jobs

**Files:**
- Create: `evaluation/ratings_job.py`
- Modify: `evaluation/__main__.py` (`cmd_import_judgments` ~line 94, `check_environment` ~line 69, `prepare_output_dir` ~line 492, `cmd_export_ratings` ~line 508)
- Test: `tests/evaluation/test_ratings_job.py`; existing `tests/evaluation/test_cli.py` and `tests/evaluation/test_pure_imports.py` must keep passing

**Interfaces:**
- Consumes: `evaluation.gold` (`load_corpus`, `load_queries`, `load_judgments`, `save_judgments`, `merge_judgments`, `parse_export`, `dump_json`, `GoldError`), `evaluation.ratings_export.build_export`, `repository.meme_text_ratings.MemeTextRatingRepository.all_ratings`.
- Produces (all in `evaluation/ratings_job.py`):
  - `check_environment(app_env, corpus_environment) -> str | None` (moved; `evaluation.__main__` re-imports the name)
  - `prepare_output_dir(out: Path, force: bool) -> str | None` (moved; re-imported)
  - `next_round(judgments) -> int`
  - `async export_ratings(session, gold: Path, out: Path, *, round_number: int, max_chars: int, today: str) -> dict` with keys `ratings, importable, files, already_judged, outside_corpus, new_messages, round`
  - `import_judgment_files(files: Sequence[Path], gold: Path, *, allow_overwrite: bool) -> dict` with keys `added, overwritten, unchanged, total`; raises `GoldError` when it would overwrite and `allow_overwrite` is false; writes `judgments.json` atomically.

- [ ] **Step 1: Write the failing tests**

`tests/evaluation/test_ratings_job.py` (reuse the `_gold_dir` helper idea from `tests/evaluation/test_cli.py`; build the corpus, one query and an empty judgments file in `tmp_path`):

```python
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import ratings_job
from evaluation.gold import (Corpus, CorpusImage, ExpectedProfile, GoldError, GoldQuery, Judgment, load_judgments,
                             save_corpus, save_judgments, save_queries)

IMG = "11111111-1111-1111-1111-111111111111"
IMG2 = "22222222-2222-2222-2222-222222222222"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _gold(tmp_path, judgments=()):
    gold = tmp_path / "gold"
    gold.mkdir()
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(IMG, "h", "ocr only"), CorpusImage(IMG2, "h2", "ocr only"))),
                gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", "boss moved the deadline", "short", "easy", EXPECTED, True)], gold / "queries.json")
    save_judgments(list(judgments), gold / "judgments.json")
    return gold


def _export_file(path, *judgments, round_number=3):
    path.write_text(json.dumps({"rater": "rating-ui", "date": "2026-10-11", "round": round_number,
                                "judgments": [{"queryId": q, "imageId": i, "grade": g} for q, i, g in judgments]}),
                    encoding="utf-8")
    return path


def test_check_environment_matches_the_old_behaviour():
    assert ratings_job.check_environment(None, "general") is None
    assert ratings_job.check_environment("general", "general") is None
    assert "general" in ratings_job.check_environment("metal", "general")


def test_next_round_is_one_after_the_highest_round():
    assert ratings_job.next_round([]) == 1
    assert ratings_job.next_round([Judgment("q001", IMG, 2, "a", "d", 2), Judgment("q001", IMG2, 1, "a", "d", 5)]) == 6


def test_prepare_output_dir_refuses_a_populated_directory_without_force(tmp_path):
    (tmp_path / "judgments-en.json").write_text("{}")
    assert "already contains" in ratings_job.prepare_output_dir(tmp_path, force=False)
    assert ratings_job.prepare_output_dir(tmp_path, force=True) is None
    assert not (tmp_path / "judgments-en.json").exists()


def test_import_adds_new_judgments_and_reports_counts(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    stats = ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert stats == {"added": 1, "overwritten": 0, "unchanged": 0, "total": 1}
    assert [(j.query_id, j.image_id, j.grade) for j in load_judgments(gold / "judgments.json")] == [("q001", IMG, 2)]
    assert not (gold / "judgments.json.tmp").exists()


def test_import_is_idempotent(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    again = ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert again == {"added": 0, "overwritten": 0, "unchanged": 1, "total": 1}


def test_import_refuses_to_overwrite_unless_allowed_and_leaves_the_file_untouched(tmp_path):
    gold = _gold(tmp_path, [Judgment("q001", IMG, 1, "owner", "2026-10-01", 1)])
    before = (gold / "judgments.json").read_bytes()
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    with pytest.raises(GoldError, match="overwrite"):
        ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert (gold / "judgments.json").read_bytes() == before
    stats = ratings_job.import_judgment_files([file], gold, allow_overwrite=True)
    assert stats["overwritten"] == 1
    assert load_judgments(gold / "judgments.json")[0].grade == 2


def test_import_rejects_an_unknown_image(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", "99999999-9999-9999-9999-999999999999", 2))
    with pytest.raises(GoldError):
        ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
```

For `export_ratings`, add an async test with a fake session patched through `MemeTextRatingRepository`: patch `ratings_job.MemeTextRatingRepository` (import it at module top of `ratings_job.py` as a module-level name) to a class whose `all_ratings` returns a list of `SimpleNamespace` rating rows with the fields `ratings_export.build_export` reads (copy a row builder from `tests/evaluation/test_ratings_export.py`). Assert that files appear in `out`, that the returned dict has the keys listed above, and that `round` equals the argument.

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/evaluation/test_ratings_job.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `evaluation/ratings_job.py`**

```python
"""Ratings to gold judgments, as callable functions (P5b CLI commands and the export_ratings / import_judgments jobs)."""
import os
from pathlib import Path
from typing import Sequence

from evaluation.gold import (
    GoldError, dump_json, load_corpus, load_judgments, load_queries, merge_judgments, parse_export, save_judgments,
)
from evaluation.ratings_export import build_export
from repository.meme_text_ratings import MemeTextRatingRepository

EXPORT_STALE_NAMES = ("candidate-queries.json", "summary.json")


def check_environment(app_env: str | None, corpus_environment: str) -> str | None:
    """A refusal message when the shell's APP_ENV names a different environment than the corpus was frozen for."""
    if app_env and app_env != corpus_environment:
        return (f"APP_ENV is {app_env!r} but the corpus was built for {corpus_environment!r}; scoring a different "
                "database than the one the gold data was judged against is meaningless")
    return None


def prepare_output_dir(out: Path, force: bool) -> str | None:
    """A refusal message when `out` holds files and force is off; with force, deletes only the files an earlier
    export owns (stale judgments-<lang>.json would otherwise be re-imported over newer judgments)."""
    if out.exists() and any(out.iterdir()):
        if not force:
            return f"{out} already contains an export (use --force to replace it)"
        for stale in [*out.glob("judgments-*.json"), *(out / name for name in EXPORT_STALE_NAMES)]:
            if stale.is_file():
                stale.unlink()
    return None


def next_round(judgments: Sequence) -> int:
    return max((judgment.round for judgment in judgments), default=0) + 1


async def export_ratings(session, gold: Path, out: Path, *, round_number: int, max_chars: int, today: str) -> dict:
    """Write the review files for the stored ratings into `out` (never merges into the gold set)."""
    ratings = await MemeTextRatingRepository(session).all_ratings()
    result = build_export(ratings, load_queries(gold / "queries.json"), load_judgments(gold / "judgments.json"),
                          set(load_corpus(gold / "corpus.json").image_ids()), max_chars=max_chars,
                          round_number=round_number, today=today)
    out.mkdir(parents=True, exist_ok=True)
    for language, data in result.judgments_by_language.items():
        (out / f"judgments-{language}.json").write_text(dump_json(data), encoding="utf-8", newline="\n")
    (out / "candidate-queries.json").write_text(dump_json(result.candidates), encoding="utf-8", newline="\n")
    (out / "summary.json").write_text(dump_json(result.report), encoding="utf-8", newline="\n")
    return {"ratings": result.report["summary"]["total"], "importable": result.report["importable"],
            "files": len(result.judgments_by_language), "already_judged": len(result.report["alreadyJudged"]),
            "outside_corpus": len(result.report["outsideCorpus"]), "new_messages": result.report["candidateMessages"],
            "round": round_number}


def import_judgment_files(files: Sequence[Path], gold: Path, *, allow_overwrite: bool) -> dict:
    """Merge exported judgment files into `<gold>/judgments.json` (atomic write; nothing is committed)."""
    import json
    queries = load_queries(gold / "queries.json")
    corpus = load_corpus(gold / "corpus.json")
    existing = load_judgments(gold / "judgments.json")
    incoming = []
    for name in files:
        with open(name, encoding="utf-8") as handle:
            data = json.load(handle)
        incoming.extend(parse_export(data, valid_query_ids={q.id for q in queries},
                                     valid_image_ids=set(corpus.image_ids())))
    result = merge_judgments(existing, incoming)
    if result.overwritten and not allow_overwrite:
        raise GoldError(f"the merge would overwrite {result.overwritten} existing judgment(s); nothing was written")
    target = gold / "judgments.json"
    temporary = gold / "judgments.json.tmp"
    save_judgments(result.merged, temporary)
    os.replace(temporary, target)
    return {"added": result.added, "overwritten": result.overwritten, "unchanged": result.unchanged,
            "total": len(result.merged)}
```

(`import json` goes at the top of the module, not inside the function; it is shown inline only to keep this block short.)

- [ ] **Step 4: Rewire the CLI**

In `evaluation/__main__.py`:
- delete the local `check_environment` and `prepare_output_dir`, and add `from evaluation.ratings_job import check_environment, export_ratings, import_judgment_files, prepare_output_dir` (tests and other callers keep using `cli.check_environment` / `cli.prepare_output_dir`).
- `cmd_import_judgments` becomes:

```python
def cmd_import_judgments(args, gold: Path) -> int:
    stats = import_judgment_files([Path(name) for name in args.files], gold, allow_overwrite=True)
    print(f"added {stats['added']}, overwritten {stats['overwritten']}, unchanged {stats['unchanged']}; "
          f"{stats['total']} judgments in total")
    return 0
```

- `cmd_export_ratings` keeps `require_readonly()`, the `prepare_output_dir` refusal, the `max_chars` read and the `AsyncSessionLocal` block, and calls:

```python
    async with AsyncSessionLocal() as session:
        stats = await export_ratings(session, gold, out, round_number=args.round, max_chars=max_chars,
                                     today=date_class.today().isoformat())
    print(f"{stats['ratings']} ratings: {stats['importable']} importable judgments in {stats['files']} file(s), "
          f"{stats['already_judged']} already judged, {stats['outside_corpus']} outside the corpus, "
          f"{stats['new_messages']} new message(s); wrote {out}")
    return 0
```

Remove imports in `__main__.py` that became unused (`build_export`, `parse_export`, `merge_judgments`, `save_judgments` if unused elsewhere in the file; grep before removing).

- [ ] **Step 5: Run the tests**

Run: `$PY -m pytest tests/evaluation -q` (the whole root). Expected: PASS, including `test_cli.py` and `test_pure_imports.py`. If the pure-imports test forbids `repository.*` in `evaluation/`, import `MemeTextRatingRepository` lazily inside `export_ratings` and patch it by module path in the test instead.

- [ ] **Step 6: Commit**

```bash
git add evaluation/ratings_job.py evaluation/__main__.py tests/evaluation/test_ratings_job.py
git commit -m "refactor(evaluation): ratings export and judgment import as shared functions with an atomic no-overwrite import"
```

---

### Task 5: `export_ratings` and `import_judgments` batch jobs

**Files:**
- Create: `batch/export_ratings.py`, `batch/import_judgments.py`
- Modify: `environments/batch_registry.yaml` (append two entries), `environments/settings.general.yaml` (scheduler jobs)
- Test: `batch/tests/test_export_ratings_main.py`, `batch/tests/test_import_judgments_main.py`, `tests/integration/test_export_ratings_job.py`, `Backend/tests/test_gold_jobs_schedule.py`

**Interfaces:**
- Consumes: `evaluation.ratings_job` functions (Task 4); `batch.run_tracking.tracked_run / finish_existing_run / record_stats`; `config.settings.load_env, settings`.
- Produces:
  - `batch.export_ratings.main(trigger="manual", run_id=None)` and `batch.import_judgments.main(trigger="manual", run_id=None)`; run kinds `export_ratings` and `import_judgments`.
  - Module constants `GOLD_DIR`, `EXPORT_DIR` (`evaluation/work/ratings-export/current`), resolved from the file location, not the working directory.
  - Stats: export `{ratings, importable, files, already_judged, outside_corpus, new_messages, round}` or `{skipped: True, reason}`; import `{added, overwritten, unchanged, total, files, changed_file, note}` or `{skipped: True, reason}`.

- [ ] **Step 1: Write the failing unit tests**

Follow `batch/tests/test_build_meme_profile_embeddings_main.py` exactly (same `_ctx` helper, `patch.object(module, "tracked_run"...)`). Cover for each module: the tracked path records the stats from `_process`; the `run_id` path uses `finish_existing_run` and records on that id. Then `_process` tests with `tmp_path`:

`batch/tests/test_import_judgments_main.py` (the `_gold` / `_export_file` helpers: copy them from `tests/evaluation/test_ratings_job.py`, they are small):

```python
async def test_process_merges_the_current_export(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    export = tmp_path / "export"; export.mkdir()
    _export_file(export / "judgments-en.json", ("q001", IMG, 2))
    monkeypatch.setenv("APP_ENV", "general")
    stats = await module._process(gold=gold, export_dir=export)
    assert (stats["added"], stats["total"], stats["files"]) == (1, 1, 1)
    assert "uncommitted" in stats["note"] and stats["changed_file"].endswith("judgments.json")


async def test_process_without_an_export_adds_nothing(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    monkeypatch.setenv("APP_ENV", "general")
    stats = await module._process(gold=gold, export_dir=tmp_path / "missing")
    assert stats["added"] == 0 and stats["files"] == 0


async def test_process_skips_other_environments(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    monkeypatch.setenv("APP_ENV", "metal")
    stats = await module._process(gold=gold, export_dir=tmp_path / "missing")
    assert stats["skipped"] is True
    assert (gold / "judgments.json").read_text() == (gold / "judgments.json").read_text()


async def test_process_fails_rather_than_overwrite(tmp_path, monkeypatch):
    gold = _gold(tmp_path, [Judgment("q001", IMG, 1, "owner", "2026-10-01", 1)])
    export = tmp_path / "export"; export.mkdir()
    _export_file(export / "judgments-en.json", ("q001", IMG, 2))
    monkeypatch.setenv("APP_ENV", "general")
    with pytest.raises(GoldError):
        await module._process(gold=gold, export_dir=export)
```

`batch/tests/test_export_ratings_main.py`: `_process` skips on another environment (returns `skipped`, writes nothing, never opens a session: patch `module.AsyncSessionLocal` with a `MagicMock` and assert not called); and on `general` it calls `ratings_job.export_ratings` (patched `AsyncMock`) with `round_number == next_round(...)` computed from the gold file, `out == export_dir`, and returns its dict.

`tests/integration/test_export_ratings_job.py` (real DB, pattern of `tests/integration/test_build_meme_profile_embeddings.py` with its `_SessionFactory`): insert two `Image` rows and `MemeTextRating` rows through `MemeTextRatingRepository.upsert` (see `repository/meme_text_ratings.py` for the signature), write a tmp gold dir whose corpus contains those image ids, run `module._process(gold=..., export_dir=...)` with `module.AsyncSessionLocal` patched to the fixture session factory, and assert the files exist and `stats["ratings"] == 2`.

`Backend/tests/test_gold_jobs_schedule.py`:

```python
from Backend.app.scheduler import _load_job_configs
from batch.registry import BatchRegistry
from config.settings import _build


def _names(environment):
    return [job["name"] for job in _load_job_configs(_build(environment))]


def test_general_schedules_the_gold_jobs_next_to_the_existing_ones():
    names = _names("general")
    assert "export_ratings" in names and "import_judgments" in names
    assert "trends_batch" in names and "build_statistics" in names


def test_other_environments_do_not_schedule_them():
    for environment in ("metal", "it"):
        assert "export_ratings" not in _names(environment)
        assert "import_judgments" not in _names(environment)


def test_both_jobs_are_registered_with_matching_kinds():
    registry = BatchRegistry()
    for name in ("export_ratings", "import_judgments"):
        assert registry.get(name) == {"module": f"batch.{name}", "kind": name}


def test_intervals():
    jobs = {job["name"]: job for job in _load_job_configs(_build("general"))}
    assert jobs["export_ratings"]["interval_minutes"] == 1440
    assert jobs["import_judgments"]["interval_minutes"] == 10080
```

- [ ] **Step 2: Run to verify they fail**

Run the three groups separately (`batch/tests/` files, then `tests/integration/test_export_ratings_job.py` with `DATABASE_URL`, then from `Backend/` the schedule test). Expected: FAIL.

- [ ] **Step 3: Implement the modules**

`batch/export_ratings.py` (structure copied from `batch/build_meme_profile_embeddings.py`):

```python
"""Export the owner's stored meme-from-text ratings as gold-set review files (general only).

Writes evaluation/work/ratings-export/current/ (replaced on every run). Never merges into the gold set:
batch.import_judgments does that. See docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md.
"""
import argparse
import asyncio
import os
import uuid
from datetime import date
from pathlib import Path

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings
from evaluation import ratings_job
from evaluation.gold import load_corpus, load_judgments
from Storage.db import AsyncSessionLocal

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = REPO_ROOT / "evaluation" / "gold"
EXPORT_DIR = REPO_ROOT / "evaluation" / "work" / "ratings-export" / "current"


async def _process(gold: Path = GOLD_DIR, export_dir: Path = EXPORT_DIR) -> dict:
    corpus = load_corpus(gold / "corpus.json")
    refusal = ratings_job.check_environment(os.environ.get("APP_ENV"), corpus.environment)
    if refusal:
        print(f"not configured for this environment: {refusal}")
        return {"skipped": True, "reason": refusal}
    round_number = ratings_job.next_round(load_judgments(gold / "judgments.json"))
    problem = ratings_job.prepare_output_dir(export_dir, force=True)
    if problem:
        raise RuntimeError(problem)
    max_chars = int(settings.get("parse.max_input_chars", 600))
    async with AsyncSessionLocal() as session:
        stats = await ratings_job.export_ratings(session, gold, export_dir, round_number=round_number,
                                                 max_chars=max_chars, today=date.today().isoformat())
    print(f"{stats['ratings']} ratings, {stats['importable']} importable, round {stats['round']}; wrote {export_dir}")
    return stats


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await record_stats(run_id, await _process())
    else:
        async with tracked_run(kind="export_ratings", trigger=trigger) as new_run_id:
            await record_stats(new_run_id, await _process())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

Note the environment guard: `check_environment` returns `None` when `APP_ENV` is unset, so a shell run without `APP_ENV` is allowed (as the CLI does). `load_env(args.env)` sets `APP_ENV`.

`batch/import_judgments.py`: same shell, kind `import_judgments`, no database:

```python
async def _process(gold: Path = GOLD_DIR, export_dir: Path = EXPORT_DIR) -> dict:
    corpus = load_corpus(gold / "corpus.json")
    refusal = ratings_job.check_environment(os.environ.get("APP_ENV"), corpus.environment)
    if refusal:
        print(f"not configured for this environment: {refusal}")
        return {"skipped": True, "reason": refusal}
    files = sorted(export_dir.glob("judgments-*.json")) if export_dir.exists() else []
    if not files:
        print("no export to import")
        return {"added": 0, "overwritten": 0, "unchanged": 0, "total": len(load_judgments(gold / "judgments.json")),
                "files": 0}
    stats = ratings_job.import_judgment_files(files, gold, allow_overwrite=False)
    stats.update({"files": len(files), "changed_file": str(gold / "judgments.json"),
                  "note": "uncommitted change to the gold judgments: review and commit it; baselines recorded "
                          "before this merge no longer match the judgments file"})
    print(f"added {stats['added']} judgment(s); {stats['total']} in total (uncommitted: review and commit)")
    return stats
```

(imports: `os, uuid, argparse, asyncio, Path`, `finish_existing_run, record_stats, tracked_run`, `load_env`, `ratings_job`, `load_corpus, load_judgments`.)

- [ ] **Step 4: Register and schedule**

Append to `environments/batch_registry.yaml`:

```yaml
export_ratings:
  module: batch.export_ratings
  kind: export_ratings
import_judgments:
  module: batch.import_judgments
  kind: import_judgments
```

In `environments/settings.general.yaml` extend the existing `scheduler:` block (Dynaconf runs with `merge_enabled=True`, so this list is appended to the common list; Step 5 proves it):

```yaml
scheduler:
  enabled: true
  jobs:
    - name: export_ratings
      script: export_ratings
      batch_run_kind: export_ratings
      interval_minutes: 1440
      max_runtime_minutes: 10
      enabled: true
    - name: import_judgments
      script: import_judgments
      batch_run_kind: import_judgments
      interval_minutes: 10080
      max_runtime_minutes: 10
      enabled: true
```

Also add a comment under the common `scheduler.jobs` list in `settings.yaml` (the "Every other job ... deliberately NOT scheduled" comment): mention that `general` additionally schedules `export_ratings` and `import_judgments` in `settings.general.yaml`.

- [ ] **Step 5: Run the tests**

Run the batch unit tests (`$PY -m pytest batch/tests -q`, whole root), the integration test and then the whole `tests/integration` root with `DATABASE_URL`, `$PY -m pytest tests/evaluation -q`, and `cd Backend && $PY -m pytest -q`. If `test_general_schedules_the_gold_jobs_next_to_the_existing_ones` shows the list was replaced instead of appended (`trends_batch` missing), switch the two entries to the common `settings.yaml` list and make the module environment guard the only thing that keeps `metal` and `it` quiet; update `test_other_environments_do_not_schedule_them` to assert the guard instead, and record the ruling in the ledger.

Then run each job once from the shell against the TEST-safe path: `APP_ENV=general` plus a `DATABASE_URL` pointing at `ocrdb_test` (never the live database): `$PY -m batch.import_judgments --env general` should print `no export to import` (or merge, if an export exists). Do not run `export_ratings` against a live database from a subagent.

- [ ] **Step 6: Commit**

```bash
git add batch/export_ratings.py batch/import_judgments.py environments/batch_registry.yaml environments/settings.yaml environments/settings.general.yaml batch/tests tests/integration/test_export_ratings_job.py Backend/tests/test_gold_jobs_schedule.py
git commit -m "feat(batch): export_ratings and import_judgments jobs, admin-triggerable and scheduled on general"
```

---

### Task 6: Documentation, diagrams and the productisation memo

**Files:**
- Modify: `docs/batches/inventory.md`, `docs/batches/execution-model.md`, `docs/batches/gaps-and-findings.md`, `docs/batches/README.md`, `docs/batches/diagrams/README.md`, `docs/batches/diagrams/job-start-paths.dot` and `.svg`
- Create: `docs/batches/use-cases/ratings-and-gold-set.md`, `docs/batches/diagrams/ratings-and-gold-set.dot` and `.svg`
- Modify: `CLAUDE.md`, `docs/security/admin-permissions-todo.md`, `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5b-rating-widget-design.md`, the P4 spec (`2026-10-07-p4-retrieval-fusion-and-api-design.md`), `docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md`, `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-10-query-log-and-gold-set-jobs-design.md` (status line and addendum)

**Interfaces:** none (documentation). Consumes the final names from Tasks 1-5: kinds `export_ratings` / `import_judgments`, directory `evaluation/work/ratings-export/current/`, setting `search.meme_from_text.query_log.enabled`, `GET /api/history?kind=`.

- [ ] **Step 1: `docs/batches/` text**

Read each file fully before editing. Make these edits, verifying every number against the files (`environments/batch_registry.yaml`, `settings*.yaml`) rather than trusting this list:
- `inventory.md`: a new section "Evaluation and ratings" with a table row for each job (what it does, tracked kind, Admin yes, Sched `general` only with the interval, notes: general-only, writes `evaluation/work/ratings-export/current/`, import edits the committed `evaluation/gold/judgments.json` and never commits, never overwrites). Update the "Summary counts" table (scheduled jobs and admin-only count) and the "Schedule per environment" section (it currently says two jobs, identical on all three environments: it is now `general` that has four). Update the "Verified" status line date to 2026-10-11.
- `execution-model.md`: the scheduler section: general has two extra jobs; mention both modules follow the wrapper contract and skip other environments.
- `gaps-and-findings.md`: find every statement that only two jobs are scheduled or that the environments are identical; correct them. Add a finding: scheduled import changes a committed file (baseline `judgmentsSha` no longer matches), with the mitigation.
- `README.md` (batches): link the new use case.
- `use-cases/ratings-and-gold-set.md`: the flow rating widget -> `meme_text_ratings` -> `export_ratings` (daily) -> `current/` -> `import_judgments` (weekly) -> `evaluation/gold/judgments.json` (uncommitted) -> person commits -> `python -m evaluation run` baselines; also the query log -> `search_history` (read through `/api/history?kind=meme_text` or SQL with the read-only role) and "Forget text". Use the file style of the other use-case pages (read `use-cases/trends-and-statistics.md` first); include a mermaid flowchart and a link to the SVG.

- [ ] **Step 2: Diagrams**

Read an existing `.dot` (`diagrams/description-pipeline.dot`) and copy its graph attributes, fonts and colours. Create `diagrams/ratings-and-gold-set.dot`: nodes for the `/reply` page, `search_history (kind meme_text)`, `meme_text_ratings`, `export_ratings (daily, general)`, `evaluation/work/ratings-export/current/`, `import_judgments (weekly, general)`, `evaluation/gold/judgments.json (uncommitted)`, `person: review and commit`, `python -m evaluation run`, with "Forget text" edges clearing the text in both tables. Update `job-start-paths.dot` so the scheduler box lists the two extra `general` jobs. Update `diagrams/README.md` index with the new diagram.

Render both with `@viz-js/viz` as `diagrams/README.md` describes (no `dot` binary exists here): in a scratch directory outside the repo run `npm init -y && npm i @viz-js/viz`, then

```js
import { instance } from "@viz-js/viz";
import fs from "node:fs";
const viz = await instance();
for (const name of process.argv.slice(2)) {
  fs.writeFileSync(name.replace(/\.dot$/, ".svg"), viz.renderString(fs.readFileSync(name, "utf8"), { format: "svg" }));
}
```

run from `docs/batches/diagrams/` on `ratings-and-gold-set.dot job-start-paths.dot`. Check the diffs: `job-start-paths.svg` should change only where the scheduler content changed; if the whole file differs in non-content ways, say so in the commit message.

- [ ] **Step 3: CLAUDE.md and spec wording**

- `CLAUDE.md`: add entries for `export_ratings` and `import_judgments` in the batch pipeline list (what they do, general only, kinds, schedule, admin-triggerable, import never commits, no overwrite); in the `ratings (P5b)` entry replace "The message text is stored ONLY because a rating exists ... is never logged" with the new rule (text is never written to application logs or error bodies; stored in `search_history` for each fresh message and in `meme_text_ratings` with a rating; "Forget text" clears both) and mention `export_ratings` / `import_judgments`; in the `retrieval` entry change "The user's text is never logged" to "is never written to the application logs (it is recorded in search_history, see ratings)"; add `search.meme_from_text.query_log.enabled` to the settings sentence. In the `parse` entry the same "never logged" phrase refers to application logs: clarify likewise. Keep the style of the surrounding entries. Also mention `GET /api/history?kind=` once.
- P5b spec (`2026-10-09-p5b-rating-widget-design.md`) and P4 spec: find each "stored only because a rating exists" / "never logged" statement (grep) and add a short dated pointer sentence to the new spec instead of silently rewriting history: "Superseded in part on 2026-10-11: fresh messages are also recorded in search_history, see <new spec>".
- `tracker.md`: add a row for this work (status `implementation`, then `done` in Step 5) following the table's format.

- [ ] **Step 4: The productisation memo**

Rewrite `docs/security/admin-permissions-todo.md`, keeping its title, "living checklist" framing, the "Related: read-only DB credentials" section and the sentence that nothing is authenticated. Replace the stale lists by an inventory built from the code, not from memory:
1. Read every router under `Backend/app/api/` (`rg -n "@router\.(get|put|post|delete|patch)" Backend/app/api`) and `Backend/app/main.py`'s `include_router` lines.
2. Classify every route as: admin-only (batch control, run history, profile coverage, curator writes `PUT/POST/DELETE /api/meme-from-text/profiles/{id}`, description-note writes, description feedback, flagging and other image mutations, ingestion, upload, concept and trend management found in the routers), personal data (`GET /api/history`, which now also holds pasted meme-from-text messages; the ratings endpoints and "Forget text"), or public read (search, browse, taxonomy, image bytes).
3. Write three tables (Admin only; Personal data, restrict to the owner or admins; Public or low risk), each row: method and path, why, and the page that calls it where one exists (frontend pages: `/admin/batches`, `/admin/profiles`, `/ingestion`, the curator panel on `MemePage`, `/upload`, `/reply` for ratings; confirm each against `Frontend/memes-frontend/src/app/router.tsx`).
4. Add a section "Second routes to the same data": `DATABASE_URL_READONLY` (role `ocr_readonly` reads `search_history` and `meme_text_ratings`), database dumps in `Storage/backups/`, and the `evaluation/work/ratings-export/` files (`candidate-queries.json` holds message text; git-ignored).
5. Add "Open items": retention for `search_history` (only manual "Forget text" and the `enabled` switch exist), and a decision whether meme-from-text rows should be hidden from `/api/history` for non-owners.
6. Update the "Current state" line (date 2026-10-11) and say the first list was extended from five endpoints to the table below.

- [ ] **Step 5: Verify and close the spec**

- Verify every link you added resolves (`rg` each referenced path) and that `docs/batches/inventory.md` counts match `environments/batch_registry.yaml` (count entries) and the scheduler entries per environment (`$PY -c "from Backend.app.scheduler import _load_job_configs; from config.settings import _build; print([[j['name'] for j in _load_job_configs(_build(e))] for e in ('metal','general','it')])"`).
- Re-render the SVGs once more and confirm `git diff --stat` shows no change (the committed SVGs match the `.dot` files).
- Set the new spec's status to `done`, add `Plan:` link, and a short "Result" addendum (what was built, deviations, handoff: run `alembic upgrade head` from `Storage/` on metal, general and it; restart the three backends; the first scheduled export on `general` appears after the next backend start plus one interval). Set the P5b spec's `Follow-ups:` line to include the new spec. Set the `tracker.md` row to `done`.
- Run the whole `Backend`, `batch/tests`, `tests/evaluation` and `tests/integration` roots one last time (separately).

- [ ] **Step 6: Commit**

```bash
git add docs CLAUDE.md
git commit -m "docs: batch inventory, execution model, diagrams, ratings use case, privacy wording and the admin-only inventory"
```
