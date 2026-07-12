# Generalize Trends Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `batch/trends_batch.py` usable by any environment by replacing its hardcoded RSS-only connector and hardcoded entity-label list with a pluggable connector registry (RSS + a new API connector for Meduza) and per-environment/per-source configurable entity extraction (labels + NER model).

**Architecture:** `TrendSource` rows (DB) carry a `connector_type` discriminator + a `config` JSON blob interpreted by that connector, plus an optional `extraction` JSON override for labels/model. A small connector registry maps `connector_type` to a class implementing `fetch() -> list[dict]`. `Processor` becomes a per-run model cache so different sources can use different GLiNER models without reloading. Environment-level label/model defaults live in tracked YAML (`settings.<env>.yaml`), following the existing Dynaconf domain-grouped pattern.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Alembic, Dynaconf, GLiNER (zero-shot NER), feedparser/cloudscraper/BeautifulSoup (RSS), requests (Meduza API), pytest.

**Spec:** `docs/superpowers/specs/2026-07-12-generalize-trends-pipeline-design.md`

## Global Constraints

- Target Python is 3.11 — use `.venv311` for all batch/backend commands.
- ORM models live only in `Storage/models.py` — never redefine tables elsewhere.
- `Backend/app/repositories/` classes must never call `session.commit()` (batch scripts under `batch/` are a different lifecycle and do commit explicitly — that convention is unchanged).
- `backend_api.md` must stay in sync with the actual routers/response shapes.
- Windows dev: `WATCHFILES_FORCE_POLLING=1` is required for uvicorn `--reload` (only relevant if manually smoke-testing the backend in Task 2).
- Frontend gate before considering Task 2 done: `tsc -b`, `eslint src/` (0 warnings), and `git diff` on `src/types/generated/` must be clean after regeneration (run from `Frontend/memes-frontend/`, generator run from `Frontend/`).
- `TrendSource.config` and `TrendSource.extraction` are plain `sa.JSON` columns (project convention — see `OCRText.bbox`), not `JSONB`.
- Every connector class must implement `fetch(self) -> list[dict]` returning items shaped exactly `{"source": str, "title": str, "url": str, "published": str, "text": str}` — this is the contract `trends_batch.py` and `Processor` rely on.

---

### Task 1: Data model & migration — rename `FeedSource` → `TrendSource`

**Files:**
- Modify: `Storage/models.py:320-334` (the `FeedSource` class), `Storage/models.py:383-394` (the `TrendsRunResult.source_id`/`source` FK and relationship)
- Create: `Storage/alembic/versions/d4a1f7b2c9e6_generalize_trend_sources.py`
- Modify: `repository/trends.py:1-16` (`FeedSourceRepository` → `TrendSourceRepository`)
- Modify: `Backend/app/repositories/diagnostics_repository.py:1-8,63-64` (import + `.select_from(...)`)
- Modify: `tests/integration/test_backend_trends_repository.py:1-18` (`_make_source` helper + import)
- Modify: `docs/schema.md:238-269` (Trends section)

**Interfaces:**
- Produces: `Storage.models.TrendSource` — table `trend_sources`, columns `id: int`, `name: str`, `connector_type: str`, `config: dict`, `extraction: dict | None`.
- Produces: `repository.trends.TrendSourceRepository.get_all() -> list[TrendSource]` (same shape as the old `FeedSourceRepository.get_all()`, renamed).

- [ ] **Step 1: Rename and restructure the model**

Replace `Storage/models.py:320-334`:

```python
class TrendSource(Base):
    __tablename__ = "trend_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    extraction: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="source"
    )

    def __repr__(self) -> str:
        return f"<TrendSource id={self.id} name={self.name!r} connector_type={self.connector_type!r}>"
```

Then update the FK and relationship inside `TrendsRunResult` (currently around line 383 and 393):

```python
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trend_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
```

```python
    source: Mapped["TrendSource"] = relationship("TrendSource", back_populates="results")
```

- [ ] **Step 2: Write the alembic migration**

Create `Storage/alembic/versions/d4a1f7b2c9e6_generalize_trend_sources.py`:

```python
"""Generalize trend sources: rename feed_sources, add connector config

Revision ID: d4a1f7b2c9e6
Revises: b7f3c9a2d4e1
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1f7b2c9e6'
down_revision: Union[str, Sequence[str], None] = 'b7f3c9a2d4e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('feed_sources', 'trend_sources')

    op.add_column('trend_sources', sa.Column('connector_type', sa.String(length=50), nullable=True))
    op.add_column('trend_sources', sa.Column('config', sa.JSON(), nullable=True))
    op.add_column('trend_sources', sa.Column('extraction', sa.JSON(), nullable=True))

    op.execute("""
        UPDATE trend_sources
        SET connector_type = 'rss',
            config = json_build_object('url', url, 'selector', selector)
        WHERE connector_type IS NULL
    """)

    op.alter_column('trend_sources', 'connector_type', nullable=False)
    op.alter_column('trend_sources', 'config', nullable=False)

    op.drop_column('trend_sources', 'url')
    op.drop_column('trend_sources', 'selector')


def downgrade() -> None:
    op.add_column('trend_sources', sa.Column('url', sa.Text(), nullable=True))
    op.add_column('trend_sources', sa.Column('selector', sa.Text(), nullable=True))

    op.execute("""
        UPDATE trend_sources
        SET url = config->>'url',
            selector = config->>'selector'
        WHERE connector_type = 'rss'
    """)

    op.alter_column('trend_sources', 'url', nullable=False)
    op.alter_column('trend_sources', 'selector', nullable=False)

    op.drop_column('trend_sources', 'extraction')
    op.drop_column('trend_sources', 'config')
    op.drop_column('trend_sources', 'connector_type')

    op.rename_table('trend_sources', 'feed_sources')
```

- [ ] **Step 3: Update the repository**

In `repository/trends.py`, change the import and class:

```python
from Storage.models import RunStatus, TrendsRun, TrendsRunResult, TrendSource


class TrendSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[TrendSource]:
        result = await self._session.execute(select(TrendSource))
        return list(result.scalars().all())
```

(Leave `TrendsRunRepository` and `TrendsRunResultRepository` untouched.)

- [ ] **Step 4: Update the diagnostics repository's model reference**

In `Backend/app/repositories/diagnostics_repository.py`, change the import:

```python
from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, OllamaDescription, TmpImageClusters, TrendsRun, TrendSource,
)
```

And change line 63 (`.select_from(FeedSource)` → `.select_from(TrendSource)`), **keep the `.label("feed_sources")` string as-is for now** — that's renamed in Task 2 together with the API contract.

- [ ] **Step 5: Update the integration test fixture**

In `tests/integration/test_backend_trends_repository.py`, change the import and helper:

```python
from Storage.models import TrendSource, TrendsRun, TrendsRunResult


async def _make_source(db_session) -> TrendSource:
    source = TrendSource(
        name="test-source",
        connector_type="rss",
        config={"url": "https://example.com", "selector": ".item"},
    )
    db_session.add(source)
    await db_session.flush()
    return source
```

- [ ] **Step 6: Add a JSON round-trip test for the new columns**

Append to `tests/integration/test_backend_trends_repository.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_trend_source_config_and_extraction_round_trip_as_json(db_session):
    source = TrendSource(
        name="round-trip-source",
        connector_type="api",
        config={"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5},
        extraction={"labels": ["person", "organization"], "model": "urchade/gliner_multi-v2.1"},
    )
    db_session.add(source)
    await db_session.flush()
    db_session.expire(source)

    reloaded = (await db_session.execute(
        select(TrendSource).where(TrendSource.id == source.id)
    )).scalar_one()

    assert reloaded.config == {"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5}
    assert reloaded.extraction == {"labels": ["person", "organization"], "model": "urchade/gliner_multi-v2.1"}
```

This needs `select` imported — add `from sqlalchemy import select` to the file's existing imports if not already present (it currently only imports `datetime`, `timedelta`, `timezone`, `pytest`, `TrendsRepository`, and the models).

- [ ] **Step 7: Run the integration test suite for this file**

Run: `pytest tests/integration/test_backend_trends_repository.py -v`
Expected: all tests PASS (this suite builds its schema straight from `Storage/models.py` metadata via `Base.metadata.create_all`, so it validates the new model shape without needing the migration to have run anywhere).

- [ ] **Step 8: Update `docs/schema.md`**

Replace `docs/schema.md:238-269` (the whole `## Trends` section) with:

```markdown
## Trends

### `trend_sources`
External sources scraped for trends data, via a pluggable connector (RSS feed, API, ...).

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK autoincrement | |
| `name` | String(255) NOT NULL | Display name |
| `connector_type` | String(50) NOT NULL | `rss` \| `api` — selects the connector implementation in `batch/trends/connectors/` |
| `config` | JSON NOT NULL | Connector-specific fields. `rss`: `{url, selector}`. `api`: `{base_url, locale, per_page, num_pages, sleep_every_pages, sleep_seconds}` |
| `extraction` | JSON nullable | Per-source override of entity extraction tuning: `{labels, model}`. Falls back to `trends.labels` / `trends.model` in `settings.<env>.yaml` when a key is absent |

### `trends_runs`
One row per execution of `trends_batch.py`.

| Column | Type | Notes |
|---|---|---|
| `run_id` | UUID PK | |
| `created_at` | DateTime(tz) NOT NULL | |
| `status` | String(20) NOT NULL | `started` \| `completed` \| `failed` |

### `trends_run_results`
Individual entries collected during a trends run.

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK autoincrement | |
| `run_id` | UUID FK → trends_runs | CASCADE delete |
| `source_id` | Integer FK → trend_sources | CASCADE delete |
| `label` | String(255) NOT NULL | Entity type extracted (e.g. `band`, `person`) |
| `name` | String(255) NOT NULL | Entity text (e.g. band or person name) |
| `value` | Integer NOT NULL | Mention count within the run |
```

- [ ] **Step 9: Apply the migration to the local Metal dev DB and verify**

Run (from `Storage/`, with the Metal dev DB's URL — the docker-compose `db` service, `ocr`/`ocr`/`ocrdb` on port 5432):

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"
alembic upgrade head
```

Expected: migration applies with no errors. Then verify the rename and backfill:

```powershell
docker exec ocr-db psql -U ocr -d ocrdb -c "\d trend_sources"
docker exec ocr-db psql -U ocr -d ocrdb -c "SELECT name, connector_type, config FROM trend_sources LIMIT 3;"
```

Expected: `trend_sources` table exists with `connector_type`, `config`, `extraction` columns and no `url`/`selector` columns; existing rows show `connector_type='rss'` and `config` containing the old `url`/`selector` values.

- [ ] **Step 10: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/d4a1f7b2c9e6_generalize_trend_sources.py repository/trends.py Backend/app/repositories/diagnostics_repository.py tests/integration/test_backend_trends_repository.py docs/schema.md
git commit -m "refactor: rename FeedSource to TrendSource, add connector_type/config/extraction columns"
```

---

### Task 2: Statistics API field rename (`feed_sources` → `trend_sources`)

**Depends on:** Task 1 (`TrendSource` model must exist).

**Files:**
- Modify: `shared/schemas/statisticstrendsstats.schema.json`
- Modify: `Backend/app/repositories/diagnostics_repository.py:64` (the `.label(...)` call)
- Modify: `Backend/app/api/diagnostics.py:39-41,89-92` (`TrendsStats` model + constructor)
- Modify: `backend_api.md:262,753`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Regenerate: `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`

**Interfaces:**
- Produces: `GET /api/diagnostics/statistics` response `trends.trend_sources: number` (was `trends.feed_sources`).

- [ ] **Step 1: Rename the shared JSON schema property**

In `shared/schemas/statisticstrendsstats.schema.json`, change:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "statisticstrendsstats.schema.json",
  "title": "StatisticsTrendsStats",
  "type": "object",
  "properties": {
    "runs":          { "type": "integer", "description": "Total trends runs recorded" },
    "trend_sources": { "type": "integer", "description": "Configured trend sources" }
  },
  "required": ["runs", "trend_sources"]
}
```

- [ ] **Step 2: Rename the SQL label**

In `Backend/app/repositories/diagnostics_repository.py`, change:

```python
                select(func.count()).select_from(TrendSource)
                    .scalar_subquery().label("trend_sources"),
```

- [ ] **Step 3: Rename the Pydantic field**

In `Backend/app/api/diagnostics.py`, change:

```python
class TrendsStats(BaseModel):
    runs: int
    trend_sources: int
```

and:

```python
        trends=TrendsStats(
            runs=row.trends_runs,
            trend_sources=row.trend_sources,
        ),
```

- [ ] **Step 4: Regenerate Frontend types and verify**

Run:

```bash
cd Frontend && bash generate-types.sh
git diff memes-frontend/src/types/generated/all.d.ts
```

Expected: diff shows `feed_sources` replaced by `trend_sources` in the `StatisticsTrendsStats` interface, nothing else changed.

- [ ] **Step 5: Regenerate the Android DTO and verify**

Run (from repo root):

```bash
python AndroidClient/scripts/generate_dtos.py
git diff AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
```

Expected: `StatisticsTrendsStats` data class field renamed from `feed_sources` to `trend_sources` (with matching `@SerialName`), nothing else changed.

- [ ] **Step 6: Update `backend_api.md`**

Replace both occurrences (around lines 262 and 753) of:

```
    "feed_sources": "number"
```

with:

```
    "trend_sources": "number"
```

and:

```
    "feed_sources": 6
```

with:

```
    "trend_sources": 6
```

- [ ] **Step 7: Smoke-test the endpoint**

From repo root, in one terminal:

```powershell
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
```

In another:

```bash
curl http://localhost:8081/api/diagnostics/statistics
```

Expected: JSON response includes `"trends": {"runs": ..., "trend_sources": ...}`. Stop the server after confirming.

- [ ] **Step 8: Frontend type-check and lint**

Run (from `Frontend/memes-frontend/`):

```bash
tsc -b
eslint src/ --max-warnings 0
```

Expected: both PASS (no code references the old `feed_sources` field name — confirmed via repo-wide search before writing this plan).

- [ ] **Step 9: Commit**

```bash
git add shared/schemas/statisticstrendsstats.schema.json Backend/app/repositories/diagnostics_repository.py Backend/app/api/diagnostics.py backend_api.md Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
git commit -m "refactor: rename statistics field feed_sources to trend_sources end-to-end"
```

---

### Task 3: Connector abstraction (RSS + API)

**Depends on:** nothing (no DB/model dependency — connectors take plain `name: str` and `config: dict`).

**Files:**
- Create: `batch/trends/connectors/__init__.py`
- Create: `batch/trends/connectors/base.py`
- Create: `batch/trends/connectors/rss.py`
- Create: `batch/trends/connectors/api.py`
- Create: `batch/trends/connectors/registry.py`
- Delete: `batch/trends/scraping.py`
- Test: `tests/batch/test_trends_connectors.py`

**Interfaces:**
- Produces: `batch.trends.connectors.rss.RSSConnector(name: str, config: dict, delay_seconds: float = 1)` with `.fetch() -> list[dict]`.
- Produces: `batch.trends.connectors.api.MeduzaConnector(name: str, config: dict)` with `.fetch() -> list[dict]`.
- Produces: `batch.trends.connectors.registry.get_connector(name: str, connector_type: str, config: dict)` → connector instance; raises `ValueError` for unknown `connector_type`.

- [ ] **Step 1: Write the failing connector tests**

Create `tests/batch/test_trends_connectors.py`:

```python
import pytest

from batch.trends.connectors.api import MeduzaConnector
from batch.trends.connectors.registry import get_connector
from batch.trends.connectors.rss import RSSConnector


def test_rss_connector_uses_content_encoded_without_scraping(monkeypatch):
    entry = {
        "title": "New Album Announced",
        "link": "https://example.com/article",
        "pubDate": "2026-07-01",
        "content:encoded": "Full article body already in the feed.",
    }

    class _FakeFeed:
        entries = [entry]

    monkeypatch.setattr("batch.trends.connectors.rss.feedparser.parse", lambda url: _FakeFeed())

    def _fail_scraper():
        raise AssertionError("should not scrape when content:encoded is present")

    monkeypatch.setattr("batch.trends.connectors.rss.cloudscraper.create_scraper", _fail_scraper)

    connector = RSSConnector("LoudWire", {"url": "https://example.com/feed", "selector": "div.pod-content p"})
    items = connector.fetch()

    assert items == [{
        "source": "LoudWire",
        "title": "New Album Announced",
        "url": "https://example.com/article",
        "published": "2026-07-01",
        "text": "Full article body already in the feed.",
    }]


def test_rss_connector_scrapes_article_when_no_content_encoded(monkeypatch):
    entry = {
        "title": "New Album Announced",
        "link": "https://example.com/article",
        "pubDate": "2026-07-01",
    }

    class _FakeFeed:
        entries = [entry]

    monkeypatch.setattr("batch.trends.connectors.rss.feedparser.parse", lambda url: _FakeFeed())
    monkeypatch.setattr("batch.trends.connectors.rss.time.sleep", lambda seconds: None)

    class _FakeResponse:
        status_code = 200
        text = "<div class='pod-content'><p>Scraped paragraph.</p></div>"

    class _FakeScraper:
        def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr("batch.trends.connectors.rss.cloudscraper.create_scraper", lambda: _FakeScraper())

    connector = RSSConnector("LoudWire", {"url": "https://example.com/feed", "selector": "div.pod-content p"})
    items = connector.fetch()

    assert items[0]["text"] == "Scraped paragraph."


def test_meduza_connector_paginates_until_empty_page(monkeypatch):
    pages = {
        1: {"documents": {"a": {"title": "Title A"}, "b": {"title": "Title B", "second_title": "Sub B"}}},
        2: {"documents": {}},
    }

    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, headers, params):
        return _FakeResponse(pages[params["page"]])

    monkeypatch.setattr("batch.trends.connectors.api.requests.get", _fake_get)
    monkeypatch.setattr("batch.trends.connectors.api.time.sleep", lambda seconds: None)

    connector = MeduzaConnector("Meduza", {"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5})
    items = connector.fetch()

    titles = {item["title"] for item in items}
    assert titles == {"Title A", "Title B: Sub B"}
    assert all(item["source"] == "Meduza" for item in items)


def test_meduza_connector_raises_on_non_200(monkeypatch):
    class _FakeResponse:
        status_code = 429

    monkeypatch.setattr(
        "batch.trends.connectors.api.requests.get",
        lambda url, headers, params: _FakeResponse(),
    )

    connector = MeduzaConnector("Meduza", {"base_url": "https://meduza.io/api/w5/new_search"})

    with pytest.raises(RuntimeError):
        connector.fetch()


def test_registry_returns_rss_connector():
    connector = get_connector("LoudWire", "rss", {"url": "https://example.com/feed", "selector": "p"})
    assert isinstance(connector, RSSConnector)


def test_registry_returns_api_connector():
    connector = get_connector("Meduza", "api", {"base_url": "https://meduza.io/api/w5/new_search"})
    assert isinstance(connector, MeduzaConnector)


def test_registry_raises_for_unknown_connector_type():
    with pytest.raises(ValueError):
        get_connector("Mystery", "carrier-pigeon", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/batch/test_trends_connectors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.trends.connectors'`.

- [ ] **Step 3: Create the connector package**

Create `batch/trends/connectors/__init__.py` (empty file).

Create `batch/trends/connectors/base.py`:

```python
from typing import Protocol


class Connector(Protocol):
    def fetch(self) -> list[dict]:
        """Return items shaped {source, title, url, published, text}."""
        ...
```

Create `batch/trends/connectors/rss.py` (port of the old `batch/trends/scraping.py:RSSScraper`, config-driven):

```python
import time

import cloudscraper
import feedparser
from bs4 import BeautifulSoup

# todo: check https://www.reddit.com/r/webscraping/comments/187ji5f/cloudscraper_with_asyncio/
# todo: retries


class RSSConnector:

    def __init__(self, name: str, config: dict, delay_seconds: float = 1):
        self.name = name
        self.rss_feed_url = config["url"]
        self.css_selector = config["selector"]
        self.delay_seconds = delay_seconds

    def _fetch_rss(self):
        feed = feedparser.parse(self.rss_feed_url)
        return feed.entries

    def _extract_article_content(self, url):
        try:
            time.sleep(self.delay_seconds)  # be polite
            scraper = cloudscraper.create_scraper()
            r = scraper.get(url)

            if r.status_code // 100 != 2:
                raise RuntimeError(f"{url}: {r.status_code}")
            soup = BeautifulSoup(r.text, "html.parser")

            paragraphs = soup.select(self.css_selector)

            text = "\n".join(p.get_text() for p in paragraphs)
            return text.strip()

        except Exception as e:
            print(f"Error fetching article {url}: {e}")
            return ""

    def fetch(self) -> list[dict]:
        entries = self._fetch_rss()
        results = []

        for entry in entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            pub_date = entry.get("pubDate", "")

            print(f"Processing: {title}")

            content = entry.get("content:encoded", "")
            if not content:
                content = self._extract_article_content(link)

            results.append({
                "source": self.name,
                "title": title,
                "url": link,
                "published": pub_date,
                "text": content,
            })

        return results
```

Create `batch/trends/connectors/api.py` (port of the standalone `MeduzaScraper.py`, config-driven, wired into the same `fetch()` contract):

```python
import time

import requests


class MeduzaConnector:

    def __init__(self, name: str, config: dict):
        self.name = name
        self.base_url = config["base_url"]
        self.locale = config.get("locale", "ru")
        self.per_page = config.get("per_page", 100)
        self.num_pages = config.get("num_pages", 100)
        self.sleep_every_pages = config.get("sleep_every_pages", 10)
        self.sleep_seconds = config.get("sleep_seconds", 10)

    def _load_page(self, page: int) -> dict:
        print(f"Loading page {page}")
        result = requests.get(
            self.base_url,
            headers={"User-Agent": ""},
            params={
                "chrono": "news",
                "page": page,
                "per_page": self.per_page,
                "locale": self.locale,
            },
        )
        if result.status_code != 200:
            raise RuntimeError(f"{self.base_url}: {result.status_code}")
        return result.json()

    def fetch(self) -> list[dict]:
        results = []

        for page in range(1, self.num_pages + 1):
            documents = self._load_page(page)["documents"]
            print(f"Loaded {len(documents)} documents")
            if not documents:
                break

            for document in documents.values():
                title = document["title"]
                if "second_title" in document:
                    title = f"{title}: {document['second_title']}"

                results.append({
                    "source": self.name,
                    "title": title,
                    "url": document.get("url", ""),
                    "published": document.get("datetime", ""),
                    "text": title,
                })

            if page % self.sleep_every_pages == 0:
                print(f"Sleeping {self.sleep_seconds} seconds")
                time.sleep(self.sleep_seconds)

        return results
```

Note: `MeduzaConnector` extracts titles only (`text` = title), matching today's `MeduzaScraper.py` behavior — it never fetches full article bodies. Fetching full article text is out of scope (see spec's "Out of scope").

Create `batch/trends/connectors/registry.py`:

```python
from batch.trends.connectors.api import MeduzaConnector
from batch.trends.connectors.rss import RSSConnector

_CONNECTORS = {
    "rss": RSSConnector,
    "api": MeduzaConnector,
}


def get_connector(name: str, connector_type: str, config: dict):
    try:
        connector_cls = _CONNECTORS[connector_type]
    except KeyError:
        raise ValueError(f"Unknown connector_type: {connector_type!r}")
    return connector_cls(name, config)
```

- [ ] **Step 4: Delete the superseded scraping module**

```bash
git rm batch/trends/scraping.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/batch/test_trends_connectors.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add batch/trends/connectors/ tests/batch/test_trends_connectors.py
git commit -m "feat: add pluggable RSS/API connector abstraction for trends sourcing"
```

---

### Task 4: Processor rewrite (per-run model cache)

**Depends on:** nothing.

**Files:**
- Modify: `batch/trends/processing.py`
- Test: `tests/batch/test_trends_processing.py`

**Interfaces:**
- Produces: `batch.trends.processing.Processor()` with `.process(text: str, model_name: str, labels: list[str]) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/batch/test_trends_processing.py`:

```python
from batch.trends.processing import Processor


class _FakeModel:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def predict_entities(self, text, labels):
        self.calls.append((text, labels))
        return [{"text": "Sleep Token", "label": "band"}]


def test_process_loads_model_once_per_name(monkeypatch):
    created = []

    def _fake_from_pretrained(name):
        model = _FakeModel(name)
        created.append(model)
        return model

    monkeypatch.setattr("batch.trends.processing.GLiNER.from_pretrained", _fake_from_pretrained)

    processor = Processor()
    processor.process("text one", "model-a", ["band"])
    processor.process("text two", "model-a", ["band"])
    processor.process("text three", "model-b", ["person"])

    assert len(created) == 2  # model-a loaded once and reused; model-b loaded separately


def test_process_returns_text_label_tuples(monkeypatch):
    monkeypatch.setattr("batch.trends.processing.GLiNER.from_pretrained", lambda name: _FakeModel(name))

    processor = Processor()
    result = processor.process("Sleep Token released a new track.", "model-a", ["band"])

    assert result == [("Sleep Token", "band")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/batch/test_trends_processing.py -v`
Expected: FAIL — `Processor.__init__()` doesn't accept zero args the way the new test expects, and `.process()` has the wrong signature (`TypeError`).

- [ ] **Step 3: Rewrite `Processor`**

Replace the full contents of `batch/trends/processing.py`:

```python
from gliner import GLiNER


class Processor:

    def __init__(self):
        self._models: dict[str, GLiNER] = {}

    def _get_model(self, model_name: str) -> GLiNER:
        if model_name not in self._models:
            self._models[model_name] = GLiNER.from_pretrained(model_name)
        return self._models[model_name]

    def process(self, text: str, model_name: str, labels: list[str]) -> list[tuple[str, str]]:
        model = self._get_model(model_name)
        entities = model.predict_entities(text, labels)
        return [(entity["text"], entity["label"]) for entity in entities]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/batch/test_trends_processing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/trends/processing.py tests/batch/test_trends_processing.py
git commit -m "refactor: make Processor a per-run model cache instead of binding one model/labels at construction"
```

---

### Task 5: Entity extraction config (labels/model per environment + per-source override)

**Depends on:** nothing.

**Files:**
- Modify: `environments/settings.metal.yaml`
- Modify: `environments/settings.general.yaml`
- Create: `batch/trends/resolution.py`
- Test: `tests/batch/test_trends_resolution.py`

**Interfaces:**
- Produces: `batch.trends.resolution.resolve_labels(source, settings) -> list[str]`.
- Produces: `batch.trends.resolution.resolve_model(source, settings) -> str | None`.
- Consumes: `source.extraction` (a `dict | None`, from `TrendSource` — Task 1) and a Dynaconf-like `settings` object exposing `.get(key: str, default=None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/batch/test_trends_resolution.py`:

```python
from types import SimpleNamespace

from batch.trends.resolution import resolve_labels, resolve_model


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_resolve_labels_uses_source_override_when_present():
    source = SimpleNamespace(extraction={"labels": ["band"]})
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["band"]


def test_resolve_labels_falls_back_to_env_default_when_extraction_is_none():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["person"]


def test_resolve_labels_falls_back_when_extraction_has_no_labels_key():
    source = SimpleNamespace(extraction={"model": "some-model"})
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["person"]


def test_resolve_model_uses_source_override_when_present():
    source = SimpleNamespace(extraction={"model": "custom-model"})
    settings = _FakeSettings({"trends.model": "default-model"})

    assert resolve_model(source, settings) == "custom-model"


def test_resolve_model_falls_back_to_env_default():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({"trends.model": "default-model"})

    assert resolve_model(source, settings) == "default-model"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/batch/test_trends_resolution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.trends.resolution'`.

- [ ] **Step 3: Implement resolution helpers**

Create `batch/trends/resolution.py`:

```python
def resolve_labels(source, settings) -> list[str]:
    extraction = source.extraction or {}
    labels = extraction.get("labels")
    if labels:
        return labels
    return settings.get("trends.labels", [])


def resolve_model(source, settings) -> str | None:
    extraction = source.extraction or {}
    model = extraction.get("model")
    if model:
        return model
    return settings.get("trends.model")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/batch/test_trends_resolution.py -v`
Expected: PASS.

- [ ] **Step 5: Add the `trends` domain to tracked config**

In `environments/settings.metal.yaml`, add:

```yaml
trends:
  labels: ["band", "music genre", "person"]
  model: urchade/gliner_medium-v2.1
```

In `environments/settings.general.yaml`, add:

```yaml
trends:
  labels: ["person", "organization", "location"]
  model: urchade/gliner_multi-v2.1
```

(`urchade/gliner_multi-v2.1` is GLiNER's multilingual release, covering Russian among other languages — a reasonable starting point for Meduza. Validate against real Meduza sample text once the pipeline runs end-to-end in Task 6/7, and swap the model name here if it underperforms — no code change needed to do so.)

Leave `environments/settings.it.yaml` untouched — no `trends` key, consistent with trends not being enabled for IT.

- [ ] **Step 6: Verify settings load correctly**

Run (from repo root, using the Metal env as an example):

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"
.venv311\Scripts\python.exe -c "from config.settings import load_env, settings; load_env('metal'); print(settings.get('trends.labels')); print(settings.get('trends.model'))"
```

Expected: prints `['band', 'music genre', 'person']` then `urchade/gliner_medium-v2.1`.

- [ ] **Step 7: Commit**

```bash
git add environments/settings.metal.yaml environments/settings.general.yaml batch/trends/resolution.py tests/batch/test_trends_resolution.py
git commit -m "feat: add per-environment trends.labels/trends.model config with per-source override resolution"
```

---

### Task 6: `trends_batch.py` orchestration rewrite

**Depends on:** Task 1 (`TrendSource`, `TrendSourceRepository`), Task 3 (connector registry), Task 4 (`Processor`), Task 5 (resolution helpers).

**Files:**
- Modify: `batch/trends_batch.py`
- Test: `tests/batch/test_trends_batch.py`

**Interfaces:**
- Consumes: `TrendSourceRepository.get_all()` (Task 1), `get_connector(name, connector_type, config)` (Task 3), `Processor().process(text, model_name, labels)` (Task 4), `resolve_labels(source, settings)` / `resolve_model(source, settings)` (Task 5).
- Produces: `batch.trends_batch.process_source(source, connector, processor, labels, model_name) -> collections.Counter` (extracted for testability).

- [ ] **Step 1: Write the failing test for `process_source`**

Create `tests/batch/test_trends_batch.py`:

```python
from collections import Counter
from types import SimpleNamespace

from batch.trends_batch import process_source


class _FakeConnector:
    def __init__(self, items):
        self._items = items

    def fetch(self):
        return self._items


class _FakeProcessor:
    def __init__(self, entities_by_text):
        self._entities_by_text = entities_by_text

    def process(self, text, model_name, labels):
        return self._entities_by_text.get(text, [])


def test_process_source_tallies_entities_across_items():
    source = SimpleNamespace(name="LoudWire")
    connector = _FakeConnector([
        {"title": "A", "text": "Sleep Token dominate the charts"},
        {"title": "B", "text": "Sleep Token again"},
    ])
    processor = _FakeProcessor({
        "Sleep Token dominate the charts": [("Sleep Token", "band")],
        "Sleep Token again": [("Sleep Token", "band")],
    })

    trends = process_source(source, connector, processor, ["band"], "model-a")

    assert trends == Counter({"band:Sleep Token": 2})


def test_process_source_handles_entity_text_containing_colon():
    source = SimpleNamespace(name="Blabbermouth")
    connector = _FakeConnector([{"title": "A", "text": "Tribute: Vinnie Paul remembered"}])
    processor = _FakeProcessor({
        "Tribute: Vinnie Paul remembered": [("Tribute: Vinnie Paul", "person")],
    })

    trends = process_source(source, connector, processor, ["person"], "model-a")

    label, name = next(iter(trends)).split(":", 1)
    assert (label, name) == ("person", "Tribute: Vinnie Paul")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/batch/test_trends_batch.py -v`
Expected: FAIL — `process_source` doesn't exist yet in `batch/trends_batch.py`.

- [ ] **Step 3: Rewrite `batch/trends_batch.py`**

Replace the full contents:

```python
import argparse
import asyncio
from collections import Counter

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.trends.connectors.registry import get_connector
from batch.trends.processing import Processor
from batch.trends.resolution import resolve_labels, resolve_model
from repository.trends import TrendSourceRepository, TrendsRunRepository, TrendsRunResultRepository


def process_source(source, connector, processor: Processor, labels: list[str], model_name: str) -> Counter:
    trends = Counter()
    data = connector.fetch()
    print(f"Scraping {source.name}")
    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            trends[f"{label}:{entity_text}"] += 1
    return trends


async def main():
    processor = Processor()

    async with AsyncSessionLocal() as session:

        sources_repo = TrendSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = TrendsRunRepository(session)

        run_id = await runs_repo.create_run()

        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            for source in sources:
                connector = get_connector(source.name, source.connector_type, source.config)
                labels = resolve_labels(source, settings)
                model_name = resolve_model(source, settings)

                trends = process_source(source, connector, processor, labels, model_name)

                for topic, value in trends.items():
                    label, name = topic.split(":", 1)
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)

            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

Two deliberate behavior changes from the old script, both in scope since this file is being rewritten anyway:
1. `topic.split(":", 1)` instead of `topic.split(":")` — the old unbounded split would crash with `ValueError: too many values to unpack` if an extracted entity's text itself contains a colon (e.g. a person label like "Tribute: Vinnie Paul"). Covered by the second test above.
2. `load_env(args.env)` is now called — previously entirely absent, meaning `settings.trends.*` would never have resolved correctly. This is required for Task 5's config to take effect.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/batch/test_trends_batch.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the script still imports cleanly end-to-end**

Run:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"
.venv311\Scripts\python.exe -c "import batch.trends_batch"
```

Expected: no import errors (validates all the new module wiring — connectors, processing, resolution — resolves correctly).

- [ ] **Step 6: Commit**

```bash
git add batch/trends_batch.py tests/batch/test_trends_batch.py
git commit -m "refactor: wire trends_batch.py to the connector registry and per-source label/model resolution"
```

---

### Task 7: Seed General's Meduza source

**Depends on:** Task 1 (`TrendSource`, `TrendSourceRepository`), Task 3 (knows the `api` connector's `config` shape).

**Files:**
- Create: `batch/trends/seed_sources.py`
- Test: `tests/integration/test_seed_sources.py`

**Interfaces:**
- Produces: `batch.trends.seed_sources.seed_meduza_source(session) -> bool` (True if a row was added, False if it already existed).
- Produces: `batch.trends.seed_sources.MEDUZA_SOURCE: dict` — the seed data, reused by the test.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_seed_sources.py`:

```python
"""
Integration test for batch/trends/seed_sources.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import pytest

from batch.trends.seed_sources import MEDUZA_SOURCE, seed_meduza_source
from repository.trends import TrendSourceRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_meduza_source_adds_row_once(db_session):
    repo = TrendSourceRepository(db_session)

    added_first = await seed_meduza_source(db_session)
    added_second = await seed_meduza_source(db_session)

    assert added_first is True
    assert added_second is False

    sources = await repo.get_all()
    matching = [s for s in sources if s.name == MEDUZA_SOURCE["name"]]
    assert len(matching) == 1
    assert matching[0].connector_type == "api"
    assert matching[0].config["base_url"] == MEDUZA_SOURCE["config"]["base_url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_seed_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.trends.seed_sources'`.

- [ ] **Step 3: Implement the seed script**

Create `batch/trends/seed_sources.py`:

```python
import argparse
import asyncio

from config.settings import load_env
from Storage.db import AsyncSessionLocal
from Storage.models import TrendSource
from repository.trends import TrendSourceRepository

MEDUZA_SOURCE = {
    "name": "Meduza",
    "connector_type": "api",
    "config": {
        "base_url": "https://meduza.io/api/w5/new_search",
        "locale": "ru",
        "per_page": 100,
        "num_pages": 100,
        "sleep_every_pages": 10,
        "sleep_seconds": 10,
    },
}


async def seed_meduza_source(session) -> bool:
    repo = TrendSourceRepository(session)
    existing = await repo.get_all()
    if any(source.name == MEDUZA_SOURCE["name"] for source in existing):
        print(f"{MEDUZA_SOURCE['name']} source already exists, skipping")
        return False

    session.add(TrendSource(**MEDUZA_SOURCE))
    await session.flush()
    print(f"Added {MEDUZA_SOURCE['name']} source")
    return True


async def main() -> None:
    async with AsyncSessionLocal() as session:
        await seed_meduza_source(session)
        await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_seed_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Actually seed General's dev DB**

Run (General's docker-compose DB — `common_memes_db`, port 5434):

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5434/ocrdb"
.venv311\Scripts\python.exe -m batch.trends.seed_sources --env general
```

Expected: prints `Added Meduza source`. Verify:

```powershell
docker exec common-memes-db psql -U ocr -d ocrdb -c "SELECT name, connector_type, config FROM trend_sources;"
```

Expected: one row named `Meduza` with `connector_type='api'`.

Note: this requires Task 1's migration to have already been applied to the General dev DB (`common-memes-db`, port 5434) — if it hasn't, run `$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5434/ocrdb"; alembic upgrade head` (from `Storage/`) first.

- [ ] **Step 6: Commit**

```bash
git add batch/trends/seed_sources.py tests/integration/test_seed_sources.py
git commit -m "feat: seed General environment's Meduza trend source"
```

---

### Task 8: Delete superseded standalone trend scripts

**Depends on:** nothing (pure deletion, already confirmed via repo-wide search that nothing imports these three files).

**Files:**
- Delete: `batch/trends/all_sources_trends.py`
- Delete: `batch/trends/trend_loudwire.py`
- Delete: `batch/trends/trend_metalinjection.py` (a third dead prototype in the same directory, discovered during planning — same pattern as the other two: a hardcoded single-purpose script fully superseded by `trends_batch.py` + the connector/processor abstraction)

- [ ] **Step 1: Confirm nothing references them**

Run: `grep -rn "trend_metalinjection\|trend_loudwire\|all_sources_trends" --include=*.py --include=*.md .`
Expected: no matches outside the spec/plan docs themselves.

- [ ] **Step 2: Delete the files**

```bash
git rm batch/trends/all_sources_trends.py batch/trends/trend_loudwire.py batch/trends/trend_metalinjection.py
```

- [ ] **Step 3: Confirm the rest of the batch test suite still collects cleanly**

Run: `pytest tests/batch/ tests/rules/ --collect-only -q`
Expected: no collection errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove dead single-source trend prototype scripts superseded by trends_batch.py"
```

---

## Post-implementation note

Task 5 picks `urchade/gliner_multi-v2.1` as General's model based on it being GLiNER's published multilingual release, but it hasn't been validated against real Meduza article text for extraction quality. Once Task 7's seed is in place, running `trends_batch.py --env general` end-to-end against live Meduza data and eyeballing the resulting `trends_run_results` rows is the natural follow-up check — not included as a plan task here since it depends on live network access to Meduza and is closer to tuning than to implementation.
