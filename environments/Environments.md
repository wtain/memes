# Environments

## Ports & running environments (always-on, do not reuse)

Three environments run **continuously** on this workstation — metal, general, and IT — each with its own backend, frontend, and database. Agents doing manual testing or verification (starting a dev server, running migrations against a scratch DB, etc.) must **not** bind to these ports; they are already in use by the developer's long-running sessions and binding to them will either fail or disrupt live work.

| Environment | Backend (uvicorn) | Frontend (vite) | Database (Postgres) |
|---|---|---|---|
| metal   | 8081 | 5173 | 5432 |
| general | 8082 | 5174 | 5434 |
| it      | 8083 | 5175 | 5436 |

Each database is a separate Postgres instance/port, not just a separate schema — see `.env.<environment>` (`DATABASE_URL`) for connection details; credentials are not repeated here since they're secrets (gitignored, per CLAUDE.md's Configuration section).

**For agents:** if you need to spin up a temporary backend, frontend, or database for testing, pick a port outside the table above and verify it's actually free first (e.g. `netstat -ano | findstr :<port>` on Windows) rather than assuming a fixed alternate — there's no reserved "testing" port range.

## Building environment

### Create .env.[environment-name] and fill:

```dotenv
DATABASE_URL=postgresql+asyncpg://...
BASE_PATH=...
PATH_INGESTION_SOURCE=...\inbox
VITE_BACKEND_API_URL=http://...
VITE_ENV_NAME=general
FRONTEND_ORIGIN=...
ALTERNATIVE_FRONTEND_ORIGIN=...
RULES_FILE=data/rules.[environment-name].json
TEXT_CONCEPTS_FILE=data/text-concepts.[environment-name].json
TEXT_CONCEPTS_TEMPLATES_FILE=data/text-concepts.templates.[environment-name].json
CONCEPT_IMAGES_DIR=images-[environment-name]
```

`PATH_INGESTION_SOURCE` is where new images get dropped for review before entering the
active library. Convention: `BASE_PATH\inbox` — a subdirectory of `BASE_PATH`, not a
sibling. This is safe because every script that scans `BASE_PATH` (`extract_text_from_memes.py`
and friends) does so non-recursively and already skips subdirectories (same reason
`excluded/`, and now `rejected/`, don't interfere either) — files inside `inbox/` are
invisible to the rest of the pipeline until Stage 1 moves them up into `BASE_PATH` itself.
See `docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md` for the full pipeline;
not required until you actually run `batch/ingest_hash_dedup.py` against this environment.

### Build and run database

TODO: put instructions (check SETUP.md)

### Run migrations

TODO: put instructions (check SETUP.md)