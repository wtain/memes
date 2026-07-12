# Generalize Trends Pipeline

## Context

`batch/trends_batch.py` currently only serves the Metal environment: it reads
`FeedSource` rows (RSS-only config: `name`, `url`, `selector`) from the DB,
scrapes each feed via `RSSScraper`, and runs every article through
`Processor` (a GLiNER zero-shot NER wrapper) with a hardcoded label list
(`["band", "music genre", "person"]`). Results land in the already-generic
`trends_runs` / `trends_run_results` tables (`label`, `name`, `value`).

We want the General environment to produce trends too, sourced from a
different kind of connector — a standalone script (`MeduzaScraper`, in a
separate repo `H:\workspace_sandbox\MeduzaScraper`) that pages through
Meduza's own JSON search API and currently just dumps article titles to a
text file, with no entity extraction and no DB integration.

Two things are hardcoded today that need to become configurable:

1. **Connector type** — only RSS is supported; Meduza needs an API-based
   connector.
2. **Extraction tuning** (entity labels, and the NER model itself, since
   Russian-language text likely needs a different GLiNER model than English
   metal-press text) — hardcoded per-process, not per-environment or
   per-source.

Each environment already has its own isolated database (metal/general/it),
so no environment column is needed on any trends table — DB-level scoping
is already environment-level scoping.

## Goals

- One shared `trends_batch.py` pipeline usable by any environment.
- Pluggable connector types (RSS today, API today for Meduza, more later)
  behind a common interface, configured per source in the DB.
- Entity labels and NER model configurable per environment (tracked YAML),
  with optional per-source override (DB).
- Port `MeduzaScraper`'s fetch logic into this repo as the first API
  connector, wired into General's DB and pipeline.

## Out of scope

- IT environment (not requested).
- A third "generic HTML scrape" connector type — no concrete need yet: the
  connector interface supports adding one later without touching existing
  connectors.
- Fetching full Meduza article bodies (today's `MeduzaScraper` only captures
  titles; the ported connector preserves that behavior). Scraping full
  article text would need a separate per-article HTML fetch, flagged as a
  future enhancement, not built now.

## Data model changes

Rename `FeedSource` (`feed_sources`) → `TrendSource` (`trend_sources`) and
restructure its columns:

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | unchanged |
| `name` | String(255) | unchanged |
| `connector_type` | String(50) NOT NULL | discriminator: `"rss"`, `"api"` (more later) |
| `config` | JSONB NOT NULL | connector-specific fields (see below) |
| `extraction` | JSONB nullable | per-source override: `{"labels": [...], "model": "..."}`, any/all keys optional |

`config` shape per connector type:
- `rss`: `{"url": str, "selector": str}` — same fields the old `url`/`selector` columns held.
- `api` (Meduza-style): `{"base_url": str, "locale": str, "per_page": int, "num_pages": int, "sleep_every_pages": int, "sleep_seconds": int}`.

`trends_runs` and `trends_run_results` are unchanged — already generic.

### Migration

One alembic revision:
1. Rename table `feed_sources` → `trend_sources`, rename FK references in
   `trends_run_results.source_id`.
2. Add `connector_type` (NOT NULL), `config` (JSONB NOT NULL), `extraction`
   (JSONB nullable).
3. Backfill existing (Metal) rows: `connector_type='rss'`,
   `config={'url': <old url>, 'selector': <old selector>}`,
   `extraction=NULL`.
4. Drop old `url`, `selector` columns.

A follow-up data step (not a migration, a one-off insert) adds a
`TrendSource` row for General pointing at Meduza with
`connector_type='api'` and the appropriate `config`.

## Config layering

New `trends` domain in tracked config, following the existing
domain-grouped Dynaconf pattern (`settings.<env>.yaml`):

```yaml
# settings.metal.yaml
trends:
  labels: ["band", "music genre", "person"]
  model: "urchade/gliner_medium-v2.1"

# settings.general.yaml
trends:
  labels: ["person", "organization", "location"]   # starting point, tune later
  model: "<multilingual-capable GLiNER model>"   # picked during implementation by testing candidate models against sample Meduza text
```

`it` omits the `trends` key entirely (not enabled), read via
`settings.get("trends.labels")` / `settings.get("trends.model")` per the
existing convention for keys absent on some environments.

**Resolution order**, computed per source in `trends_batch.py`:

```python
labels = (source.extraction or {}).get("labels") or settings.get("trends.labels", [])
model  = (source.extraction or {}).get("model")  or settings.get("trends.model")
```

## Connector abstraction (`batch/trends/connectors/`)

- `base.py` — `Connector` protocol with one method:
  `fetch(self) -> list[dict]`, returning items shaped
  `{"source": str, "title": str, "url": str, "published": str, "text": str}`
  (the contract `RSSScraper.parse()` already produces today).
- `rss.py` — `RSSConnector`: today's `RSSScraper` logic unchanged in
  behavior, reading `config["url"]` / `config["selector"]`.
- `api.py` — `MeduzaConnector`: ports `MeduzaScraper.py`'s paginated fetch
  loop (page through `config["base_url"]` with `config["locale"]`,
  `config["per_page"]`, up to `config["num_pages"]`, sleeping
  `config["sleep_seconds"]` every `config["sleep_every_pages"]` pages).
  `text` is set to the title (matching current `MeduzaScraper` behavior —
  no article body fetch).
- `registry.py` — `dict[str, type[Connector]]` keyed by `connector_type`,
  with a `get_connector(source: TrendSource) -> Connector` factory that
  instantiates the right class with `source.config`.

## Processor changes (`batch/trends/processing.py`)

`Processor` no longer binds a model/labels at construction. It becomes a
per-run cache so a single run can mix models across sources without
reloading a model already in use:

```python
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
        return [(e["text"], e["label"]) for e in entities]
```

One `Processor` instance is created per `trends_batch.py` run.

## `trends_batch.py` orchestration

- Add `config.settings.load_env()` as the first call in `main()` — currently
  missing entirely (an existing gap; the script only reads `DATABASE_URL`
  today, no tracked settings). Required now that it reads
  `settings.trends.*`.
- For each `TrendSource`: resolve its connector via
  `registry.get_connector(source)`, resolve `labels`/`model` via the
  fallback above, call `connector.fetch()`, then
  `processor.process(text, model, labels)` per item — same tally/write loop
  as today otherwise.

## Cleanup

Delete `batch/trends/all_sources_trends.py` and
`batch/trends/trend_loudwire.py` — pre-existing dead scripts with
hardcoded Metal-only sources and labels, fully superseded by
`trends_batch.py` even before this change.

## Touch points

- `Storage/models.py` — rename + restructure `TrendSource`.
- `Storage/alembic/versions/` — new migration.
- `repository/trends.py` — rename `FeedSourceRepository` references,
  update field access.
- `batch/trends/connectors/` — new package (`base.py`, `rss.py`, `api.py`,
  `registry.py`), replacing `batch/trends/scraping.py`.
- `batch/trends/processing.py` — `Processor` rewrite.
- `batch/trends_batch.py` — orchestration changes above.
- `Backend/app/repositories/diagnostics_repository.py` — update health
  check reference from `feed_sources` to `trend_sources`.
- `tests/integration/test_backend_trends_repository.py` — update for
  rename.
- `docs/schema.md` — update the Trends section (table name, new columns).
- `environments/settings.metal.yaml`, `settings.general.yaml` — add
  `trends` domain.
- Delete `batch/trends/all_sources_trends.py`,
  `batch/trends/trend_loudwire.py`.

## Testing

- Unit tests for `RSSConnector` and `MeduzaConnector` (mock HTTP/feed
  responses, assert the common item shape).
- Unit test for the connector registry factory.
- Unit test for label/model resolution fallback (source override present vs
  absent).
- Unit test for `Processor`'s model cache (same model instance reused
  across `process()` calls with the same `model_name`).
- Update `tests/integration/test_backend_trends_repository.py` for the
  rename; add coverage for `config`/`extraction` JSON round-tripping.