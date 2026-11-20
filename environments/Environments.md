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

## Prod (the VPS) and the sync tunnel ports

The prod instances run on a VPS, **not on this workstation**, and occupy none of the ports above. The only prod-related ports that are ever bound on the workstation are the three
local ends of the sync tool's ssh tunnel (16432, 16434, 16436); the tool opens and closes them itself, only while a `sync.*` command runs, and they do not clash with any port in the table above.
Prod is a separate stack per environment on the VPS (design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p5-prod-provisioning-and-hardening-design.md`, "Environment table";
runbook: `deploy/prod/README.md`; operator docs: `docs/sync/`). Nothing below is secret; credentials and real hostnames are never written here.

| Env | Compose project (VPS) | Prod DB name | Postgres on the VPS loopback | Local tunnel port (workstation, only while sync runs) | Backend on the edge network | Project subnet (VPS) | ssh aliases (workstation ssh config: tunnel, verbs, upload) |
|---|---|---|---|---|---|---|---|
| metal   | `memes-metal`   | `memes_metal`   | `127.0.0.1:6432` | 16432 | `api-metal:8000`   | `172.31.1.0/24` | `memes-prod-metal`, `memes-prod-metal-sync`, `memes-prod-metal-up` |
| general | `memes-general` | `memes_general` | `127.0.0.1:6434` | 16434 | `api-general:8000` | `172.31.2.0/24` | `memes-prod-general`, `memes-prod-general-sync`, `memes-prod-general-up` |
| it      | `memes-it`      | `memes_it`      | `127.0.0.1:6436` | 16436 | `api-it:8000`      | `172.31.3.0/24` | `memes-prod-it`, `memes-prod-it-sync`, `memes-prod-it-up` |

Remote Postgres ports are the dev cluster ports plus 1000 (the workstation's 5432/5434/5436 are dev, see the table at the top). The shared edge project `memes-edge` (Caddy, ports 80 and 443 on the VPS only) fronts all three.
An agent doing manual testing must not bind 16432, 16434 or 16436 either, and never gets the sync credentials or the `memes-prod-*` ssh aliases (`CLAUDE.md`).

## Observability ports (workstation)

Grafana for the prod metrics runs on this workstation (`deploy/observability/`), reaching the VPS Prometheus (published on the VPS loopback `127.0.0.1:9090` only) through an ssh tunnel opened by `python -m sync.obs_tunnel`. Both ports are on the loopback and clash with nothing above. An agent doing manual testing must not bind them either.

| What | Port (workstation, 127.0.0.1) | Notes |
|---|---|---|
| Prometheus tunnel (local end, ssh alias `memes-prod-obs`) | 19090 | only while `python -m sync.obs_tunnel` runs; stop with `--stop` |
| Grafana (`deploy/observability/compose.yaml`) | 3300 | datasource `http://host.docker.internal:19090`; admin password from the untracked `deploy/observability/.env` |

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
active library. `POST /api/uploads` (phone and web uploads) writes into it too, so the
backend process must be able to write there (it creates `.uploading/` inside for staging);
without it, uploads fail with a 503. The path must be absolute and must not be `BASE_PATH` itself or contain it
(503), `.uploading/` must be a plain directory (not a symlink or junction), and uploads are refused with a 507 when the
volume would be left with under 1 GiB free. `INCOMING_PATH` is retired. Convention: `BASE_PATH\inbox` — a subdirectory of `BASE_PATH`, not a
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