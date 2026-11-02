# S7 Discovery Admin Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin page `/admin/discovery` with three tabs (Memes, Queries, Misses) over the existing discovery report functions, with thumbnails, filters mirroring the CLI options, a top-N size choice and a "Search this" link on query rows.

**Architecture:** Three read-only endpoints under `/api/admin/discovery` (router, thin service that maps the report dataclasses to shared-schema responses, unchanged `repository/discovery_report.py`). The service starts the request's transaction with `SET TRANSACTION READ ONLY`. The frontend adds typed API methods, a data hook keyed by the request, and a page whose tabs each own their filter state.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL; React/Vite/vitest with the React Compiler lint rules.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-02-s7-discovery-admin-page-design.md`

## Global Constraints

- Report functions (`memes_report`, `queries_report`, `misses_report`) and the CLI are not changed. Repositories never commit; the new service only reads.
- Endpoints: `GET /api/admin/discovery/memes`, `/queries`, `/misses`. Wire names camelCase: memes `windowDays`, `sources` (comma list), `minRedrops`, `maxImpressions`, `sort`, `limit`; queries `kind`, `maxResults`, `includeDegraded`, `sinceDays`, `minTimes`, `sort`, `limit`; misses `gapMinutes`, `kind`, `sinceDays`, `minTimes`, `explicitOnly`, `sort`, `limit`. `limit` is 1 to 200, default 50. Defaults equal the CLI's (`windowDays` default `popularity.impressions.window_days`, `sources` default every source except `adjustment`).
- A bad value is a 422 with our own message; no 422 body or log line contains search text. Nothing in the new code logs query text.
- JSON cannot carry `inf`: `per100` is `null` with `per100Infinite: true` when a meme has re-drops in the window and no impressions in it; with no impressions table `per100` is `null`, `per100Infinite` false and `impressionsAvailable` false.
- "Search this" links go to `/search?q=<query>&<category>=<value>...` built with `buildSearchParams` from `src/utils/searchParams.ts`; rows of kind `meme_text` get no link.
- No migration, no setting, no caching, no paging, no writes. The three routes are added to `docs/security/admin-permissions-todo.md`.
- Python: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe`; integration tests need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`; run test roots separately. Async tests `@pytest.mark.asyncio(loop_scope="session")` with the rolled-back `db_session`.
- Frontend (from `Frontend/memes-frontend`): `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`, `pnpm exec vitest run`. Effects must survive a double mount; refs only in handlers and effects; controls at least 44 px tall (`min-h-11`).
- Worktree gotchas: no heredocs and no `cd` combined with git; use the Edit tool to append; the worktree needs a `node_modules` junction for the frontend (`cmd /c mklink /J`), removed with `cmd /c rmdir` before the worktree is removed; fast-forward a new worktree to local `main` with `git merge --ff-only <sha>`. The Python type generator rewrites every generated file with new timestamps: restore all but the files whose content really changed.

## Review Focus

1. `per100` must stay valid JSON for all three states (finite, infinite, unavailable), and `∞`/`n/a` must show on the page (Tasks 1 and 5).
2. The read-only transaction must reject a write, including when the session already ran statements (Task 1).
3. "Search this" for a query with spaces, `&`, `#`, non-Latin text and a facet value containing `:`; no link for `meme_text` (Task 4).
4. A slow response for an older filter must never replace a newer one, and a double mount must not break the hook (Task 4).
5. A missing impressions table or missing S6 schema is a note on the page, not an error (Tasks 1 and 5).

## File Structure

- Create `shared/schemas/discoverymemes.schema.json`, `discoveryqueries.schema.json`, `discoverymisses.schema.json`; modify `all.schema.json`; regenerate types.
- Create `Backend/app/services/discovery_report_service.py`, `Backend/app/api/admin_discovery.py`; modify `Backend/app/main.py`.
- Frontend: create `src/discovery/{types,format,useDiscoveryData,fixtures}.ts`, `src/components/discovery/{fields,ResultState,MemesTab,QueriesTab,MissesTab}.tsx`, `src/pages/DiscoveryPage.tsx`; modify `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/app/router.tsx`, `src/app/AppLayout.tsx`.
- Docs: `backend_api.md`, `docs/discovery-report.md`, `docs/security/admin-permissions-todo.md`, `CLAUDE.md`, epic README.

---

### Task 1: Schemas, generated types and the mapping service

**Files:**
- Create: the three schemas, `Backend/app/services/discovery_report_service.py`, `tests/integration/test_admin_discovery_service.py`
- Modify: `shared/schemas/all.schema.json`, generated types (TS, Python, Kotlin)

**Interfaces:**
- Produces: `DiscoveryReportService(session)` with `async memes(*, window_days, sources, min_redrops, max_impressions, sort, limit) -> dict`, `async queries(*, kind, max_results, include_degraded, since_days, min_times, sort, limit) -> dict`, `async misses(*, gap_minutes, kind, since_days, min_times, explicit_only, sort, limit) -> dict` (dicts shaped like the schemas, ready for `Schema.model_validate`); generated `DiscoveryMemes`, `DiscoveryQueries`, `DiscoveryMisses` (TS) and `Backend.app.types.generated.discoverymemes|discoveryqueries|discoverymisses.Schema`.

- [ ] **Step 1: Schemas.** `discoverymemes.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "discoverymemes.schema.json",
  "title": "DiscoveryMemes",
  "description": "Memes that keep coming back through ingestion and how visible they are (discovery report, memes view)",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "windowDays": { "type": "integer" },
    "sources": { "type": "array", "items": { "type": "string" } },
    "impressionsAvailable": { "type": "boolean" },
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "id": { "type": "string" },
          "imageUrl": { "type": "string" },
          "filename": { "type": ["string", "null"] },
          "redrops": { "type": "integer" },
          "redropsWindow": { "type": "integer" },
          "sources": { "type": "array", "items": { "type": "object", "additionalProperties": false,
            "properties": { "source": { "type": "string" }, "count": { "type": "integer" } }, "required": ["source", "count"] } },
          "popularity": { "type": "integer" },
          "saves": { "type": "integer" },
          "impressions": { "type": ["integer", "null"] },
          "impressionsWindow": { "type": ["integer", "null"] },
          "lastBack": { "type": ["string", "null"] },
          "per100": { "type": ["number", "null"], "description": "Re-drops in the window per 100 impressions in the window; null when infinite or unavailable" },
          "per100Infinite": { "type": "boolean", "description": "True when there were re-drops in the window and no impressions in it" }
        },
        "required": ["id", "imageUrl", "filename", "redrops", "redropsWindow", "sources", "popularity", "saves",
                     "impressions", "impressionsWindow", "lastBack", "per100", "per100Infinite"]
      }
    }
  },
  "required": ["windowDays", "sources", "impressionsAvailable", "items"]
}
```
`discoveryqueries.schema.json` (title `DiscoveryQueries`): `kind` (string) and `items` of `{query: string, tags: string[], kind: string, times: int, firstAt: string, lastAt: string, minResults: int, avgResults: number, web: int, android: int, degraded: int, fallbacks: string[]}`, all required. `discoverymisses.schema.json` (title `DiscoveryMisses`): `status` (string enum `ok|no_schema|no_outcomes`), `cutoff` (string or null), `gapMinutes` (int), `kind` (string), `items` of `{query: string, tags: string[], kind: string, times: int, explicit: int, searches: number, firstAt: string, lastAt: string, web: int, android: int}`, all required. Both `additionalProperties: false`, same style. Register in `all.schema.json` definitions: `"DiscoveryMemes"`, `"DiscoveryQueries"`, `"DiscoveryMisses"` with `$ref`s.

- [ ] **Step 2: Regenerate** (from the repo root, then `Backend/`): `bash Frontend/generate-types.sh`; from `Backend/`: `datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel` (binary in `.venv311/Scripts`); `.venv311/Scripts/python.exe AndroidClient/scripts/generate_dtos.py`. Then restore every generated Python file except the three new ones (copy them aside, `git checkout -- Backend/app/types/generated/`, copy back; `__init__.py` only if its content, not just its timestamp, changed). `git status` should show only the schemas, `all.d.ts`, `Models.kt` and the three Python files.

- [ ] **Step 3: Write the failing tests** `tests/integration/test_admin_discovery_service.py`:

```python
import logging
import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

from Backend.app.services.discovery_report_service import DiscoveryReportService
from Backend.app.types.generated.discoverymemes import Schema as DiscoveryMemes
from Backend.app.types.generated.discoverymisses import Schema as DiscoveryMisses
from Backend.app.types.generated.discoveryqueries import Schema as DiscoveryQueries
from repository.discovery_report import DEFAULT_SOURCES
from Storage.models import (MemeImpression, MemeSave, PopularityAbsorption, SearchHistory, SearchHistoryTag,
                            SearchNotFound)
from tests.integration.test_popularity_repository import make_image

pytestmark = pytest.mark.asyncio(loop_scope="session")

MEME_ARGS = dict(window_days=30, sources=DEFAULT_SOURCES, min_redrops=1, max_impressions=None, sort="per_100", limit=50)


async def edge(session, winner, source="hash_cross_corpus", created_at=None):
    values = dict(kind="absorb", source=source, winner_image_id=winner.id, loser_image_id=None, weight=1)
    if created_at is not None:
        values["created_at"] = created_at
    await session.execute(insert(PopularityAbsorption).values(**values))


async def test_a_re_drop_without_impressions_is_infinite_and_validates_as_json(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    result = await DiscoveryReportService(db_session).memes(**MEME_ARGS)
    item = result["items"][0]
    assert item["id"] == str(img.id) and item["imageUrl"] == f"/api/images/{img.id}"
    assert item["per100"] is None and item["per100Infinite"] is True
    assert result["impressionsAvailable"] is True and result["windowDays"] == 30
    assert item["sources"] == [{"source": "hash_cross_corpus", "count": 1}]
    DiscoveryMemes.model_validate(result).model_dump_json()  # no Infinity anywhere


async def test_a_finite_ratio_is_a_number(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    db_session.add_all([MemeImpression(image_id=img.id, rank=1, surface="search") for _ in range(4)])
    await db_session.flush()
    item = (await DiscoveryReportService(db_session).memes(**MEME_ARGS))["items"][0]
    assert item["per100"] == 25.0 and item["per100Infinite"] is False and item["impressions"] == 4


async def test_without_the_impressions_table_the_fields_are_null_not_an_error(db_session):
    img = await make_image(db_session, "a.jpg")
    await edge(db_session, img)
    await db_session.execute(text("DROP TABLE meme_impressions"))
    result = await DiscoveryReportService(db_session).memes(**{**MEME_ARGS, "sort": "per_100"})
    item = result["items"][0]
    assert result["impressionsAvailable"] is False
    assert item["impressions"] is None and item["impressionsWindow"] is None
    assert item["per100"] is None and item["per100Infinite"] is False


async def test_queries_map_tags_and_fallbacks_to_lists(db_session):
    db_session.add(SearchHistory(searched_at=datetime.now(timezone.utc), query="Funny  Cat", client="web", result_count=0,
                                 kind="search", tags=[SearchHistoryTag(category="topic", value="work"),
                                                      SearchHistoryTag(category="emotion", value="joy")]))
    await db_session.flush()
    result = await DiscoveryReportService(db_session).queries(
        kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=50)
    item = result["items"][0]
    assert item["query"] == "funny cat" and item["tags"] == ["emotion:joy", "topic:work"] and item["fallbacks"] == []
    assert item["times"] == 1 and item["web"] == 1 and item["avgResults"] == 0.0
    DiscoveryQueries.model_validate(result)


async def test_misses_carry_status_and_cutoff(db_session):
    db_session.add(SearchNotFound(search_history_id=__import__("uuid").uuid4(),
                                  created_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    db_session.add(SearchHistory(searched_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), query="lost",
                                 client="android", result_count=7, kind="search"))
    await db_session.flush()
    result = await DiscoveryReportService(db_session).misses(
        gap_minutes=5, kind="search", since_days=None, min_times=1, explicit_only=False, sort="times", limit=50)
    assert result["status"] == "ok" and result["cutoff"].startswith("2026-09-01") and result["gapMinutes"] == 5
    assert [i["query"] for i in result["items"]] == ["lost"] and result["items"][0]["searches"] == 1.0
    DiscoveryMisses.model_validate(result)


async def test_the_status_is_reported_when_there_is_no_outcome_data(db_session):
    result = await DiscoveryReportService(db_session).misses(
        gap_minutes=5, kind="search", since_days=None, min_times=1, explicit_only=False, sort="times", limit=50)
    assert result["status"] == "no_outcomes" and result["cutoff"] is None and result["items"] == []


async def test_the_transaction_is_read_only_after_the_service_ran(db_session):
    await make_image(db_session, "a.jpg")   # statements already ran in this transaction
    await DiscoveryReportService(db_session).queries(
        kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=1)
    with pytest.raises(DBAPIError) as raised:
        await db_session.execute(text("INSERT INTO search_not_found (search_history_id) VALUES (gen_random_uuid())"))
    assert "read-only" in str(raised.value).lower()


async def test_nothing_is_logged_with_search_text(db_session, caplog):
    db_session.add(SearchHistory(searched_at=datetime.now(timezone.utc), query="secret quokka", client="web",
                                 result_count=0, kind="search"))
    await db_session.flush()
    with caplog.at_level(logging.DEBUG):
        await DiscoveryReportService(db_session).queries(
            kind="search", max_results=3, include_degraded=False, since_days=None, min_times=1, sort="times", limit=50)
    assert "secret quokka" not in caplog.text
```
Remove unused imports (`math`, `timedelta`, `MemeSave`) when linting the file.

- [ ] **Step 4: Run, watch fail.** `DATABASE_URL=... pytest tests/integration/test_admin_discovery_service.py -v` → FAIL (ImportError).

- [ ] **Step 5: Implement** `Backend/app/services/discovery_report_service.py`:

```python
"""Discovery report for the admin page (epic meme-popularity, S7): maps the report dataclasses to the wire shape.

Read only: the transaction is switched to READ ONLY first, so nothing here can write. Search text is never logged."""
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.discovery_report import memes_report, misses_report, queries_report


def _split(value: str) -> list[str]:
    return value.split(",") if value else []


def _stamp(value) -> str | None:
    return None if value is None else value.isoformat()


def _ratio(per_100: float | None) -> tuple[float | None, bool]:
    if per_100 is None:
        return None, False
    if math.isinf(per_100):
        return None, True
    return float(per_100), False


class DiscoveryReportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _read_only(self) -> None:
        await self.session.execute(text("SET TRANSACTION READ ONLY"))

    async def memes(self, *, window_days: int, sources: tuple[str, ...], min_redrops: int,
                    max_impressions: int | None, sort: str, limit: int) -> dict:
        await self._read_only()
        rows, available = await memes_report(
            self.session, window_days=window_days, sources=sources, min_redrops=min_redrops,
            max_impressions=max_impressions, sort=sort, limit=limit)
        items = []
        for row in rows:
            per_100, infinite = _ratio(row.per_100)
            items.append({
                "id": str(row.image_id), "imageUrl": f"/api/images/{row.image_id}", "filename": row.filename,
                "redrops": row.redrops, "redropsWindow": row.redrops_window,
                "sources": [{"source": name, "count": count} for name, count in sorted(row.sources.items())],
                "popularity": row.popularity, "saves": row.saves, "impressions": row.impressions,
                "impressionsWindow": row.impressions_window, "lastBack": _stamp(row.last_back),
                "per100": per_100, "per100Infinite": infinite})
        return {"windowDays": window_days, "sources": list(sources), "impressionsAvailable": available, "items": items}

    async def queries(self, *, kind: str, max_results: int, include_degraded: bool, since_days: int | None,
                      min_times: int, sort: str, limit: int) -> dict:
        await self._read_only()
        rows = await queries_report(
            self.session, kind=kind, max_results=max_results, include_degraded=include_degraded,
            since_days=since_days, min_times=min_times, sort=sort, limit=limit)
        return {"kind": kind, "items": [{
            "query": row.query, "tags": _split(row.tags), "kind": row.kind, "times": row.times,
            "firstAt": _stamp(row.first_at), "lastAt": _stamp(row.last_at), "minResults": row.min_results,
            "avgResults": row.avg_results, "web": row.web, "android": row.android, "degraded": row.degraded,
            "fallbacks": _split(row.fallbacks)} for row in rows]}

    async def misses(self, *, gap_minutes: int, kind: str, since_days: int | None, min_times: int,
                     explicit_only: bool, sort: str, limit: int) -> dict:
        await self._read_only()
        report = await misses_report(
            self.session, gap_minutes=gap_minutes, kind=kind, since_days=since_days, min_times=min_times,
            explicit_only=explicit_only, sort=sort, limit=limit)
        return {"status": report.status, "cutoff": _stamp(report.cutoff), "gapMinutes": gap_minutes, "kind": kind,
                "items": [{
                    "query": row.query, "tags": _split(row.tags), "kind": row.kind, "times": row.times,
                    "explicit": row.explicit, "searches": row.searches, "firstAt": _stamp(row.first_at),
                    "lastAt": _stamp(row.last_at), "web": row.web, "android": row.android} for row in report.rows]}
```
If PostgreSQL rejects `SET TRANSACTION READ ONLY` after earlier statements in the test (the test above exercises it), use `SET LOCAL transaction_read_only = on` instead and ledger a ruling; the rejected-write assertion must still pass.

- [ ] **Step 6: Run** `DATABASE_URL=... pytest tests/integration/test_admin_discovery_service.py -v` → all PASS. Mutation check: remove the `_read_only()` call from `queries` and confirm `test_the_transaction_is_read_only_after_the_service_ran` fails, then restore.

- [ ] **Step 7: Commit** `git commit -m "feat(popularity): S7 discovery schemas and mapping service"`

---

### Task 2: Router, registration, API docs

**Files:**
- Create: `Backend/app/api/admin_discovery.py`, `Backend/tests/test_admin_discovery_endpoint.py`
- Modify: `Backend/app/main.py` (import near line 21, include near line 151), `backend_api.md`, `docs/security/admin-permissions-todo.md`

**Interfaces:**
- Consumes: `DiscoveryReportService` (Task 1), the `Schema` models.
- Produces: `get_discovery_service` dependency and `router`.

- [ ] **Step 1: Failing tests** `Backend/tests/test_admin_discovery_endpoint.py` (mirror `test_admin_profiles_endpoint.py`):

```python
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin_discovery import get_discovery_service, router
from repository.discovery_report import DEFAULT_SOURCES

MEMES = {"windowDays": 30, "sources": list(DEFAULT_SOURCES), "impressionsAvailable": True, "items": []}
QUERIES = {"kind": "search", "items": []}
MISSES = {"status": "ok", "cutoff": None, "gapMinutes": 5, "kind": "search", "items": []}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.memes = AsyncMock(return_value=MEMES)
    service.queries = AsyncMock(return_value=QUERIES)
    service.misses = AsyncMock(return_value=MISSES)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_discovery_service] = lambda: service
    return TestClient(app), service


def test_memes_defaults_equal_the_cli(client_and_service, monkeypatch):
    monkeypatch.setattr("Backend.app.api.admin_discovery.window_days", lambda: 30)
    client, service = client_and_service
    response = client.get("/api/admin/discovery/memes")
    assert response.status_code == 200 and response.json() == MEMES
    service.memes.assert_awaited_once_with(window_days=30, sources=DEFAULT_SOURCES, min_redrops=1,
                                           max_impressions=None, sort="per_100", limit=50)


def test_memes_parameters_reach_the_service(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/memes?windowDays=7&sources=recut,hash_cross_corpus&minRedrops=2"
               "&maxImpressions=0&sort=saves&limit=200")
    service.memes.assert_awaited_once_with(window_days=7, sources=("recut", "hash_cross_corpus"), min_redrops=2,
                                           max_impressions=0, sort="saves", limit=200)


def test_queries_and_misses_parameters_reach_the_service(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/queries?kind=all&maxResults=0&includeDegraded=true&sinceDays=3&minTimes=2"
               "&sort=min_results&limit=100")
    service.queries.assert_awaited_once_with(kind="all", max_results=0, include_degraded=True, since_days=3,
                                             min_times=2, sort="min_results", limit=100)
    client.get("/api/admin/discovery/misses?gapMinutes=10&kind=meme_text&sinceDays=5&minTimes=2&explicitOnly=true"
               "&sort=explicit&limit=25")
    service.misses.assert_awaited_once_with(gap_minutes=10, kind="meme_text", since_days=5, min_times=2,
                                            explicit_only=True, sort="explicit", limit=25)


def test_queries_and_misses_defaults(client_and_service):
    client, service = client_and_service
    client.get("/api/admin/discovery/queries")
    service.queries.assert_awaited_once_with(kind="search", max_results=3, include_degraded=False, since_days=None,
                                             min_times=1, sort="times", limit=50)
    client.get("/api/admin/discovery/misses")
    service.misses.assert_awaited_once_with(gap_minutes=5, kind="search", since_days=None, min_times=1,
                                            explicit_only=False, sort="times", limit=50)


@pytest.mark.parametrize("path", [
    "memes?limit=0", "memes?limit=201", "memes?sort=nope", "memes?windowDays=0", "memes?minRedrops=-1",
    "queries?kind=nope", "queries?sort=nope", "queries?maxResults=-1", "queries?minTimes=0",
    "misses?gapMinutes=0", "misses?sort=nope", "misses?kind=nope", "misses?limit=0",
])
def test_bad_parameters_are_422(client_and_service, path):
    client, _ = client_and_service
    assert client.get(f"/api/admin/discovery/{path}").status_code == 422


def test_an_unknown_source_is_a_422_with_our_message(client_and_service):
    client, service = client_and_service
    service.memes.side_effect = ValueError("unknown popularity source(s): nope")
    response = client.get("/api/admin/discovery/memes?sources=nope")
    assert response.status_code == 422 and "nope" in response.json()["detail"]


def test_a_service_value_error_is_a_422(client_and_service):
    client, service = client_and_service
    service.memes.side_effect = ValueError("max_impressions needs the meme_impressions table, which does not exist yet")
    response = client.get("/api/admin/discovery/memes?maxImpressions=1")
    assert response.status_code == 422 and "meme_impressions" in response.json()["detail"]


def test_an_unexpected_failure_is_a_500_without_details(client_and_service):
    client, service = client_and_service
    service.queries.side_effect = RuntimeError("SECRET-QUERY database blew up")
    response = client.get("/api/admin/discovery/queries")
    assert response.status_code == 500 and "SECRET-QUERY" not in response.text
```
Note: the TestClient re-raises server exceptions by default; construct it with `TestClient(app, raise_server_exceptions=False)` in the fixture so the 500 test can read the response (adjust the fixture accordingly).

- [ ] **Step 2: Run, watch fail** (`cd Backend && pytest tests/test_admin_discovery_endpoint.py -v` → ImportError).

- [ ] **Step 3: Implement** `Backend/app/api/admin_discovery.py`:

```python
import logging
from typing import AsyncGenerator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.cache import no_cache_headers
from Backend.app.services.discovery_report_service import DiscoveryReportService
from Backend.app.types.generated.discoverymemes import Schema as DiscoveryMemes
from Backend.app.types.generated.discoverymisses import Schema as DiscoveryMisses
from Backend.app.types.generated.discoveryqueries import Schema as DiscoveryQueries
from repository.discovery_report import DEFAULT_SOURCES
from repository.meme_impressions import window_days
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/discovery", tags=["admin"])

MemeSort = Literal["per_100", "redrops", "redrops_window", "popularity", "saves", "impressions"]
QuerySort = Literal["times", "last", "min_results"]
MissSort = Literal["times", "explicit", "last"]
Kind = Literal["search", "meme_text", "all"]
Limit = Query(50, ge=1, le=200)


async def get_discovery_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[DiscoveryReportService, None]:
    yield DiscoveryReportService(db)


def _unprocessable(error: ValueError) -> HTTPException:
    # Our own validation messages (option names and values); they never contain search text.
    return HTTPException(status_code=422, detail=str(error))


@router.get("/memes", response_model=DiscoveryMemes)
async def get_memes(
        windowDays: Optional[int] = Query(None, ge=1), sources: Optional[str] = None,
        minRedrops: int = Query(1, ge=0), maxImpressions: Optional[int] = Query(None, ge=0),
        sort: MemeSort = "per_100", limit: int = Limit,
        service: DiscoveryReportService = Depends(get_discovery_service)):
    chosen = DEFAULT_SOURCES if not sources else tuple(part for part in sources.split(",") if part)
    try:
        return await service.memes(
            window_days=windowDays if windowDays is not None else window_days(), sources=chosen,
            min_redrops=minRedrops, max_impressions=maxImpressions, sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery memes failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None


@router.get("/queries", response_model=DiscoveryQueries)
async def get_queries(
        kind: Kind = "search", maxResults: int = Query(3, ge=0), includeDegraded: bool = False,
        sinceDays: Optional[int] = Query(None, ge=1), minTimes: int = Query(1, ge=1),
        sort: QuerySort = "times", limit: int = Limit,
        service: DiscoveryReportService = Depends(get_discovery_service)):
    try:
        return await service.queries(
            kind=kind, max_results=maxResults, include_degraded=includeDegraded, since_days=sinceDays,
            min_times=minTimes, sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery queries failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None


@router.get("/misses", response_model=DiscoveryMisses)
async def get_misses(
        gapMinutes: int = Query(5, ge=1), kind: Kind = "search", sinceDays: Optional[int] = Query(None, ge=1),
        minTimes: int = Query(1, ge=1), explicitOnly: bool = False, sort: MissSort = "times", limit: int = Limit,
        service: DiscoveryReportService = Depends(get_discovery_service)):
    try:
        return await service.misses(
            gap_minutes=gapMinutes, kind=kind, since_days=sinceDays, min_times=minTimes, explicit_only=explicitOnly,
            sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery misses failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None
```
Remove the unused `no_cache_headers` import unless you add the header (do add `response.headers.update(no_cache_headers())` via a `Response` parameter on each route, as `images.py` does, and keep the import). In `Backend/app/main.py` import `from Backend.app.api.admin_discovery import router as admin_discovery_router` and add `app.include_router(admin_discovery_router, prefix="/api")` after the `admin_profiles_router` line.

- [ ] **Step 4: Run** `cd Backend && pytest tests/test_admin_discovery_endpoint.py -v` → PASS; then the whole root `pytest -q` → PASS. Smoke: the app still imports (`python -c "import Backend.app.main"` from the repo root with the env of `tests`; the Backend suite already imports it).

- [ ] **Step 5: Docs.** `backend_api.md`: add an "Admin: Discovery report" section after the profile-coverage section documenting the three routes, every parameter with its default, the response fields (including `per100`/`per100Infinite`, `impressionsAvailable`, misses `status`/`cutoff`), the 422 and 500 behavior and "read-only; search text is returned to the caller and never logged". `docs/security/admin-permissions-todo.md`: add the three routes to the table of admin-read routes that return personal data (search text) and bump the route count/date in its "current state" paragraph.

- [ ] **Step 6: Commit** `git commit -m "feat(popularity): S7 discovery admin endpoints and API docs"`

---

### Task 3: Frontend API client and types

**Files:**
- Create: `src/discovery/types.ts`, `src/discovery/fixtures.ts`, `src/api/http/HttpMemesApi.discovery.test.ts`
- Modify: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`

**Interfaces:**
- Consumes: generated `DiscoveryMemes`, `DiscoveryQueries`, `DiscoveryMisses`.
- Produces:
```ts
export type MemeSort = "per_100" | "redrops" | "redrops_window" | "popularity" | "saves" | "impressions"
export type QuerySort = "times" | "last" | "min_results"
export type MissSort = "times" | "explicit" | "last"
export type Kind = "search" | "meme_text" | "all"
export type MemesParams = { windowDays?: number; sources?: string[]; minRedrops?: number; maxImpressions?: number; sort?: MemeSort; limit?: number }
export type QueriesParams = { kind?: Kind; maxResults?: number; includeDegraded?: boolean; sinceDays?: number; minTimes?: number; sort?: QuerySort; limit?: number }
export type MissesParams = { gapMinutes?: number; kind?: Kind; sinceDays?: number; minTimes?: number; explicitOnly?: boolean; sort?: MissSort; limit?: number }
export class DiscoveryRequestError extends Error { status: number; detail: string }
MemesApi.getDiscoveryMemes(params: MemesParams, signal?: AbortSignal): Promise<DiscoveryMemes>   // and Queries, Misses
```

- [ ] **Step 1: Failing test** `HttpMemesApi.discovery.test.ts` (style of `HttpMemesApi.saves.test.ts`): `getDiscoveryMemes({windowDays: 7, sources: ["recut","hash_cross_corpus"], minRedrops: 2, maxImpressions: 0, sort: "saves", limit: 100})` fetches `http://backend/api/admin/discovery/memes?windowDays=7&sources=recut%2Chash_cross_corpus&minRedrops=2&maxImpressions=0&sort=saves&limit=100` (assert with `new URL(url).searchParams` so the order does not matter), passes the `signal`, returns the parsed body; undefined params are omitted (`getDiscoveryMemes({})` fetches the bare path); `getDiscoveryQueries({includeDegraded: true, sinceDays: 3})` sends `includeDegraded=true&sinceDays=3`; `getDiscoveryMisses({explicitOnly: true, gapMinutes: 10})` sends `explicitOnly=true&gapMinutes=10`; a 422 response `{ok:false, status:422, json: async () => ({detail: "unknown popularity source(s): nope"})}` rejects with a `DiscoveryRequestError` whose `status` is 422 and `detail` is that string; a 500 with a non-JSON body (`json` throws) rejects with `status` 500 and an empty `detail`.

- [ ] **Step 2: Run, watch fail** (`pnpm exec vitest run src/api/http/HttpMemesApi.discovery.test.ts`).

- [ ] **Step 3: Implement.** `src/discovery/types.ts` with the types above and:
```ts
export class DiscoveryRequestError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail || `Request failed: ${status}`)
    this.name = "DiscoveryRequestError"
  }
}
export type { DiscoveryMemes, DiscoveryQueries, DiscoveryMisses } from "../types/generated/all"
```
`MemesApi.ts`: add the three methods (`getDiscoveryMemes(params: MemesParams, signal?: AbortSignal): Promise<DiscoveryMemes>` etc.). `HttpMemesApi.ts`: a private helper
```ts
private async getDiscovery<T>(view: string, params: Record<string, string | number | boolean | string[] | undefined>, signal?: AbortSignal): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue
    query.set(key, Array.isArray(value) ? value.join(",") : String(value))
  }
  const text = query.toString()
  const response = await fetch(`${this.baseUrl}/api/admin/discovery/${view}${text ? `?${text}` : ""}`, {
    headers: { "Accept": "application/json" }, signal,
  })
  if (!response.ok) {
    let detail = ""
    try {
      const body = await response.json()
      if (typeof body?.detail === "string") detail = body.detail
    } catch { /* not JSON: keep the empty detail */ }
    throw new DiscoveryRequestError(response.status, detail)
  }
  return response.json()
}
```
and the three public methods calling it with `"memes"|"queries"|"misses"`. `mockApi.ts`: add `getDiscoveryMemes: vi.fn().mockResolvedValue(makeDiscoveryMemes())`, `getDiscoveryQueries`, `getDiscoveryMisses` with the fixtures. `src/discovery/fixtures.ts`: `makeDiscoveryMemes(overrides?)` returning `{windowDays: 30, sources: ["hash_cross_corpus"], impressionsAvailable: true, items: [makeMemeItem()]}`, `makeMemeItem(overrides?)` (id `"m1"`, imageUrl `/api/images/m1`, filename `"quokka.jpg"`, redrops 3, redropsWindow 2, sources `[{source:"hash_cross_corpus",count:3}]`, popularity 4, saves 1, impressions 10, impressionsWindow 5, lastBack `"2026-11-01T12:30:00Z"`, per100 40, per100Infinite false), `makeDiscoveryQueries(items?)` and `makeQueryItem(overrides?)` (query `"funny cat"`, tags `["topic:work"]`, kind `"search"`, times 2, firstAt/lastAt, minResults 0, avgResults 0.5, web 1, android 1, degraded 0, fallbacks `[]`), `makeDiscoveryMisses(overrides?)` (status `"ok"`, cutoff `"2026-09-01T00:00:00Z"`, gapMinutes 5, kind `"search"`, items `[makeMissItem()]`) and `makeMissItem(overrides?)` (query `"lost quokka"`, tags `[]`, kind `"search"`, times 3, explicit 1, searches 2, firstAt/lastAt, web 1, android 2).

- [ ] **Step 4: Gates** `pnpm exec vitest run src/api` → PASS; `pnpm exec tsc -b` → clean.

- [ ] **Step 5: Commit** `git commit -m "feat(popularity): S7 discovery API client and types"`

---

### Task 4: Formatting helpers and the data hook

**Files:**
- Create: `src/discovery/format.ts`, `src/discovery/format.test.ts`, `src/discovery/useDiscoveryData.ts`, `src/discovery/useDiscoveryData.test.tsx`

**Interfaces:**
- Produces:
```ts
export function per100Text(item: { per100: number | null; per100Infinite: boolean }, available: boolean): string   // "∞" | "n/a" | "40.0"
export function countText(value: number | null): string                                                          // "n/a" for null
export function stampText(value: string | null): string                                                         // "2026-11-01 12:30" or "-"
export function searchLink(query: string, tags: string[], kind: string): string | null                          // null for meme_text
export type View = { view: "memes"; params: MemesParams } | { view: "queries"; params: QueriesParams } | { view: "misses"; params: MissesParams }
export function useDiscoveryData<T>(memesApi: MemesApi, view: "memes" | "queries" | "misses", params: object): { data: T | null; error: string | null; loading: boolean; retry: () => void }
```

- [ ] **Step 1: Failing tests.** `format.test.ts`: `per100Text({per100: 40, per100Infinite: false}, true) === "40.0"`; `{per100: 40.04}` → `"40.0"`; `{per100: null, per100Infinite: true}` → `"∞"`; any item with `available=false` → `"n/a"`; `{per100: 0, per100Infinite: false}` → `"0.0"`. `countText(null) === "n/a"`, `countText(7) === "7"`. `stampText("2026-11-01T12:30:00Z")` → `"2026-11-01 12:30"` (UTC, slice of the ISO string: use `value.slice(0, 16).replace("T", " ")`), `stampText(null) === "-"`. `searchLink`:
  - `searchLink("funny cat", [], "search") === "/search?q=funny+cat"`
  - `searchLink("", ["animal:cat"], "search") === "/search?animal=cat"`
  - `searchLink("a&b #1", ["topic:work", "topic:home"], "search") === "/search?q=a%26b+%231&topic=work&topic=home"`
  - `searchLink("смешной кот", [], "search")` equals `"/search?q=" + encodeURIComponent("смешной кот").replace(/%20/g, "+")` (URLSearchParams form)
  - a tag whose value contains a colon: `searchLink("x", ["topic:a:b"], "search") === "/search?q=x&topic=a%3Ab"` (split on the first colon only)
  - `searchLink("x", [], "meme_text") === null`.
  `useDiscoveryData.test.tsx` (a small test component or `renderHook`): calls `memesApi.getDiscoveryMemes` once with the params and an `AbortSignal`, then returns the data (`loading` false); a rejected `DiscoveryRequestError(422, "bad sort")` gives `error === "bad sort"` and a generic `Request failed` text for other errors; `retry()` refetches; changing the params object content (not identity) refetches while re-rendering with an equal-content new object does not; **a slow response for older params that resolves after the newer one is ignored** (two deferred promises resolved in reverse order; the hook ends with the newer data); under `<StrictMode>` the first fetch's late result is ignored and the final state is correct; unmount aborts (the signal passed to the call has `aborted === true`).

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Implement** `format.ts`:
```ts
import { buildSearchParams } from "../utils/searchParams"

export function per100Text(item: { per100: number | null; per100Infinite: boolean }, available: boolean): string {
  if (!available) return "n/a"
  if (item.per100Infinite) return "∞"
  return item.per100 === null ? "n/a" : item.per100.toFixed(1)
}
export function countText(value: number | null): string {
  return value === null ? "n/a" : String(value)
}
export function stampText(value: string | null): string {
  return value === null ? "-" : value.slice(0, 16).replace("T", " ")
}
export function searchLink(query: string, tags: string[], kind: string): string | null {
  if (kind === "meme_text") return null
  const filters: Record<string, string[]> = {}
  for (const tag of tags) {
    const at = tag.indexOf(":")
    if (at < 0) continue
    const category = tag.slice(0, at)
    filters[category] = [...(filters[category] ?? []), tag.slice(at + 1)]
  }
  return `/search?${buildSearchParams(query, filters).toString()}`
}
```
(If `query` is empty and there are no tags the link is `/search?`; the tab components do not render a link when both are empty.) `useDiscoveryData.ts`:
```ts
import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { DiscoveryRequestError } from "./types"

type View = "memes" | "queries" | "misses"
type State<T> = { key: string; data: T | null; error: string | null }

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

export function useDiscoveryData<T>(memesApi: MemesApi, view: View, params: object) {
  const key = JSON.stringify([view, params])
  const [reloadKey, setReloadKey] = useState(0)
  const [state, setState] = useState<State<T> | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    const [, current] = JSON.parse(key) as [View, never]
    const request: Promise<unknown> = view === "memes"
      ? memesApi.getDiscoveryMemes(current, controller.signal)
      : view === "queries" ? memesApi.getDiscoveryQueries(current, controller.signal)
      : memesApi.getDiscoveryMisses(current, controller.signal)
    request.then(
      (data) => { if (!cancelled) setState({ key, data: data as T, error: null }) },
      (error) => {
        if (cancelled || isAbort(error)) return
        setState({ key, data: null, error: error instanceof DiscoveryRequestError && error.detail ? error.detail : "Request failed" })
      },
    )
    return () => { cancelled = true; controller.abort() }
  }, [memesApi, view, key, reloadKey])

  const current = state !== null && state.key === key ? state : null
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: current === null,
    retry: () => { setState(null); setReloadKey((n) => n + 1) },
  }
}
```
(`retry` runs only in a handler. If the React Compiler lint objects to the `JSON.parse` cast, type the parsed params with a union per branch.)

- [ ] **Step 4: Run** `pnpm exec vitest run src/discovery` → PASS; `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0` → clean.

- [ ] **Step 5: Commit** `git commit -m "feat(popularity): S7 discovery formatting helpers and data hook"`

---

### Task 5: The page, its tabs, route and nav

**Files:**
- Create: `src/components/discovery/{fields,ResultState,MemesTab,QueriesTab,MissesTab}.tsx`, `src/pages/DiscoveryPage.tsx`, `src/pages/DiscoveryPage.test.tsx`
- Modify: `src/app/router.tsx` (after the `/admin/recut` route), `src/app/AppLayout.tsx` (after the Recut NavLink)

**Interfaces:**
- Consumes: Task 3 types/API/fixtures, Task 4 helpers and hook.
- Produces: `DiscoveryPage({ memesApi })` at `/admin/discovery`.

- [ ] **Step 1: Failing tests** `DiscoveryPage.test.tsx` (render inside `MemoryRouter initialEntries={["/admin/discovery"]}` with `makeMockApi`, `userEvent`):
  - the heading "Discovery report" and three tabs (`role="tab"`) Memes (selected), Queries, Misses; clicking a tab sets `?view=` (render with a `Routes` and read `useLocation`, or assert the selected tab) and calls the matching API method once.
  - **Memes:** the first fetch is `getDiscoveryMemes({windowDays: undefined...})` — assert it was called with an object whose `limit` is 50 and `sort` is `"per_100"` and `sources` is undefined; a row shows the thumbnail `img` with `alt` `quokka.jpg` inside a link to `/memes/m1`, the filename, `40.0`, `hash_cross_corpus: 3`; an item with `per100Infinite: true` shows `∞`; `impressionsAvailable: false` shows the banner text "impressions are n/a" and `n/a` cells; changing the sort select to "saves" calls the API again with `sort: "saves"`; choosing size 100 calls it with `limit: 100`; unchecking a source sends the remaining sources; a rejected `DiscoveryRequestError(422, "bad")` shows "bad" with a Retry button that refetches.
  - **Queries:** a row with kind `search` shows a "Search this" link whose `href` is `/search?q=funny+cat&topic=work`; a `meme_text` row has no such link; an empty-query row shows "(no text)" and a link to its tags only; a row with no query and no tags has no link; tag chips render; toggling "Include degraded" refetches with `includeDegraded: true`.
  - **Misses:** the header shows the cut-off date (`2026-09-01`); status `no_schema` shows "the S6 migration has not run here yet" and no table rows; `no_outcomes` shows "no linked save or press"; "Explicit only" refetches with `explicitOnly: true`; a row shows "Search this".
  - every select/input/button has an accessible name and the controls carry `min-h-11`.

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Implement.**
  - `fields.tsx`: `NumberField({label, value, onChange, min, optional})` (a labelled `<input type="number">`; an empty box means `undefined`), `SelectField<T extends string>({label, value, options, onChange})`, `CheckField({label, checked, onChange})`; all wrapped in `<label>` with `min-h-11`, styled like the existing admin pages (`rounded border px-2`).
  - `ResultState.tsx`: `ResultState({ loading, error, onRetry, children })` rendering "Loading…" (`role="status"`), the error text with a "Retry" button (`role="alert"`), or the children.
  - `MemesTab.tsx`: `useState<MemesParams>({ sort: "per_100", limit: 50 })` (the other fields undefined = server defaults); controls: window days, min re-drops, max impressions, sort, and a checkbox group for the sources (all `PopularitySource` names except `adjustment`: `hash_in_batch`, `hash_cross_corpus`, `ingest_tier_a`, `ingest_tier_b`, `cluster_review`, `agent_review`, `file_duplicates`, `recut`; all checked = send no `sources`; otherwise send the checked ones), size select (50/100/200). Table columns: thumbnail (`<a href="/memes/{id}"><img src={backendUrl + imageUrl} alt={filename ?? id} loading="lazy" className="h-16 w-16 object-cover"/></a>`; build the image URL with `memesApi.getImageUrlById(item.id)`), filename, re-drops (`redrops` / `redropsWindow`), sources, popularity, saves, impressions, impressions window, last re-drop (`stampText`), per 100 (`per100Text(item, data.impressionsAvailable)`). When `!data.impressionsAvailable` show the banner "impressions are n/a: the meme_impressions table does not exist here yet". Use `Link` from react-router for the meme link. Table inside `<div className="overflow-x-auto">`.
  - `QueriesTab.tsx`: params `{ kind: "search", maxResults: 3, sort: "times", limit: 50 }`; controls: kind, max results, include degraded, since days, min times, sort, size. Columns: query (`query || "(no text)"`), tag chips (`<span className="rounded bg-gray-100 px-1.5 text-xs">`), kind, times, first, last, min results, avg results (`toFixed(1)`), web, android, degraded (with `fallbacks.join(", ")`), and a "Search this" `Link` when `searchLink` is non-null and the row has query text or tags. Note under the table: "A low-result row right after a higher one from the same client is skipped (approximate)."
  - `MissesTab.tsx`: params `{ gapMinutes: 5, kind: "search", sort: "times", limit: 50 }`; controls: gap minutes, kind, since days, min times, explicit only, sort, size. Header line `Outcome data since {cutoff date or "n/a"}`; messages for `no_schema` / `no_outcomes` (same wording as the CLI); columns: query, tags, kind, sessions (`times`), explicit, searches (`toFixed(1)`), first, last, web, android, "Search this". Note: "Sessions are a time-gap approximation; sessions before the outcome data began are invisible."
  - `DiscoveryPage.tsx`: `useSearchParams` for `view` (default `memes`, unknown value falls back to `memes`); tab buttons `role="tab"` with `aria-selected`, container `role="tablist"`; renders only the active tab component (so its filter state resets on tab change); heading "Discovery report".
  - Routes: `{ path: "/admin/discovery", element: <DiscoveryPage memesApi={memesApi} /> }`; nav: a `NavLink to="/admin/discovery"` labelled "Discovery" with the same classes as its neighbours.

- [ ] **Step 4: Run** the new test file → PASS, then gates: `pnpm exec tsc -b`; `pnpm exec eslint src/ --max-warnings 0`; `pnpm exec vitest run` (whole root) → all green.

- [ ] **Step 5: Commit** `git commit -m "feat(popularity): S7 discovery admin page"`

---

### Task 6: Docs, status and full verification

**Files:**
- Modify: `docs/discovery-report.md`, `CLAUDE.md`, the spec (`Plan:` line), the epic README

- [ ] **Step 1: Docs.** `docs/discovery-report.md`: add an "Admin page" section (route, tabs, filters, size choice, "Search this", the notes the page shows, that it reads through the same functions so it matches the CLI, and that query text crosses HTTP like `GET /api/history` does). `CLAUDE.md`: extend the `tools/discovery_report` entry with "; the same three views are browsable at `/admin/discovery`". Spec: add `Plan: docs/superpowers/plans/2026-11-02-s7-discovery-admin-page.md` under the status line and set `Status: planned`; epic README: S7 row and `Follow-ups:` say `planned`.
- [ ] **Step 2: Full roots, each separately:** `DATABASE_URL=... pytest tests/integration/ -q`; `cd Backend && pytest -q`; `pytest tests/docs/ -q`; `pytest batch/tests/ -q`; `pytest tests/rules/ -q`; frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. Expected all green (baseline: integration 1345, Backend 653, docs 15, batch 347, rules 144, vitest 804).
- [ ] **Step 3: Commit** `git commit -m "docs(popularity): S7 docs and plan link"`

## Self-review

- **Spec coverage:** Q1 tabs and link (Tasks 4, 5), Q2 size choice (Task 5), Q3 memo and no logging (Tasks 1, 2), N1 to N7 (Tasks 1, 2), N8 to N10 (Task 5), N11 and N12 (no code), testing and docs (every task, Task 6).
- **Placeholders:** none; the three schemas other than the memes one are specified field by field in the same style.
- **Type consistency:** service method and router keyword names match (`window_days`, `sources`, `min_redrops`, `max_impressions`, `sort`, `limit`; `kind`, `max_results`, `include_degraded`, `since_days`, `min_times`; `gap_minutes`, `explicit_only`); TS `MemesParams`/`QueriesParams`/`MissesParams` use the camelCase wire names the router declares.
