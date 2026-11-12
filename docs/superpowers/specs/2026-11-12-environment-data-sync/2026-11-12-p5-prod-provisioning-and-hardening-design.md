# P5: prod provisioning and hardening (design)

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: S5 (`2026-11-12-s5-prod-topology-and-roles.md`, the analysis this design turns into work), P1 (registry, built), decisions log in `README.md` (15 and 16 below).
Feeds: P2 and P4 (roles, grants, the gate), P3 (the `memesync` user and directories), P6, P7.
Splits into: **P5a backend stage mechanism**, **P5b web**, **P5c Android**, **P5d VPS stack and roles**. Each is built and reviewed on its own.

## Intent

Turn the dev backend image into a prod that serves readers and nothing else: routes that are not served are not reachable, writes that are not allowed are refused by the database even if
the route gate fails, and the VPS stack (containers, Postgres, proxy, users, directories, backups) is reproducible from files in the repository. The backend has no login at all (S5 headline 1), and prod is on the public
internet (decision 6), so defence in depth is the design: gate in the app, grants in Postgres, restrictions in the proxy.

## New decisions (2026-11-12, owner)

| # | Question | Decision |
|---|---|---|
| 15 | Runtime on the VPS | **Docker Compose**: one backend container per environment from `Dockerfile.backend`, one Postgres cluster (three databases, decision 7) in a container, a one-shot migration container |
| 16 | Reverse proxy | **Caddy**: TLS, hostname routing, access logs without query strings, rate limits where Caddy can, basic auth or IP allow-list if wanted later |

## Goals / non-goals / anti-goals

- Goals: a stage switch that fails safe; a route policy covering every route; read-only statistics; a capabilities endpoint; web and Android that hide what prod does not serve; roles and grants generated from the registry;
  a compose stack and Caddy config; a migration path with a fixed deploy order; backups of the only copy of reader data; prod settings tracked in git, secrets not.
- Non-goals: choosing the VPS provider, buying domains, writing the sync tools (P2 to P4), CI/CD to the VPS (manual `docker compose pull && up` is enough in v1), moving dev to containers, authentication for readers (a separate epic if ever wanted).
- Anti-goals: an admin route reachable on prod by omission; a prod backend role that can write a class D or X table; secrets in the image or the repo; a container that runs pipelines or the scheduler; message text in any log.

## P5a: backend stage mechanism

1. **Stage switch (S5 §2.1).** `APP_STAGE` (`dev` default, `prod`) read once in `config/settings.py`; when `prod`, `settings_files` gains `environments/settings.prod.yaml` and, where needed, `environments/settings.<env>.prod.yaml`. A helper
   `config.stage.is_prod()` is the only reader. `Dockerfile.backend` bakes `ENV APP_STAGE=prod`, so a forgotten variable on the VPS still means prod (the image is only ever used for prod; dev runs uvicorn from the venv).
   `settings.prod.yaml`: `stage: prod`, `scheduler.enabled: false` (Dynaconf appends lists, so the scheduler is switched off, not edited), nothing else shared. The lifespan also skips the scheduler on `is_prod()` regardless of the overlay.
2. **Route policy (S5 §2.2).** `Backend/app/stage_policy.py`: `ROUTE_POLICY[(method, path_template)] = "reader" | "dev_only"` for **every** route, and a pure-ASGI `StageGate` middleware registered only on prod that answers **404** (not 403) for
   anything not `reader`, matching routes the way Starlette does (first full match), so a removed literal route cannot fall through to `/images/{image_id}`. Pure-admin routers (`admin`, `admin_discovery`, `admin_profiles`, `ingestion`, `recut`, `comics`,
   `meme_profiles`) are also not mounted on prod; `/docs`, `/redoc`, `/openapi.json` are off on prod (they are open today, `main.py:121-125`).
3. **Verdicts.** The starting point is the S5 §1 inventory (90 routes: 31 reader, 59 dev-only). The owner's decisions change a few of them; T1 recomputes the table and the counts:

   | Route (S5) | S5 proposal | Now | Because |
   |---|---|---|---|
   | `GET /api/images/trending?window=30d`, `GET .../popularity` | reader (empty) or hide | **reader** | `popularity_absorptions` is shipped (decision 1) |
   | `GET /api/diagnostics/statistics` | disable | **reader, read-only** | statistics snapshot is shipped (decision 4); see 4 |
   | `GET /api/history` | disable | **dev_only** | decision 10 |
   | `POST /api/uploads` | reader | **dev_only** | decision 9 |
   | `POST /api/meme-from-text/search` and friends | reader | **reader, answers `available: false`** | decision 3; `parse.model` absent in the prod overlay (the `it` pattern) |
   | `POST /api/bug-reports` | reader | **reader**, size and rate limited | decision 13 |
   | trends routes | reader | **dev_only** | decision 5 (trends hidden) |
   | concepts routes | reader | **reader** | decision 5 (concepts shipped) |
   | new `GET /api/diagnostics/capabilities` | n/a | **reader** | decision 12 |
   | ratings/saves/not-found/history-recording writes | reader | **reader** | they are the class P writes |

   The ratings routes are reader routes that need the meme-from-text feature; with it off they are unreachable in practice but remain `reader` so turning the feature on later is a settings change.
4. **Read-only statistics.** On prod `GET /api/diagnostics/statistics` returns the stored `corpus` snapshot as is: it ignores `live=true` and the 2-hour staleness rule and never writes. The prod role has no write on `statistics_snapshots`, so a code path that
   tried would fail loudly. Missing row: answer 404 with a fixed detail.
5. **Capabilities.** `GET /api/diagnostics/capabilities` returns `{stage, revision, schema_head, features: {memeFromText, concepts, comics, trends, statistics, uploads, history, admin}}`. Schema in `shared/schemas/` with TypeScript, Kotlin and
   Python types regenerated (CLAUDE.md type generation); documented in `backend_api.md`. `revision` is the image's git SHA (a build argument), the value P2 compares (P2 design, preconditions).
6. **Tests.** (a) Verdict-completeness guard in `tests/docs/` style: every route of the app has a policy entry and every entry names a real route. (b) Gate behaviour (`Backend/tests/`, mocked DB, `APP_STAGE=prod`): every `dev_only` route answers 404 for its method, including
   the fall-through case; docs and OpenAPI are 404; the scheduler is not started; statistics ignores `live`. (c) The write-set test of S5 §2.5 (below, P5d).

## P5b: web

`VITE_STAGE=prod` at build time (new `.env.<env>.prod` for the prod build, static, no request at load) hides the Cleanup and Admin menu groups, the trends entries (Statistics stays: it is served as the promoted snapshot), the curator controls on `MemePage`
(`ProfileEditor`, description approve/reject and note editor, flag toggle, popularity set control, comics block), the history screens and the Upload page; their routes redirect to `/explore`. A control that still gets a 404 treats it as "feature off", never an error banner.
The web build also reads `/api/diagnostics/capabilities` when present, so a prod backend with a feature switched on later shows it without a rebuild (the build flag is the static default). Tests: `vitest` for the menu, router redirects and the hidden controls under `VITE_STAGE=prod`; `tsc -b`, `eslint`, per the repo's pre-commit list.

## P5c: Android

The app reads `capabilities` once per environment (the backends are user-defined at runtime) and hides Flagged, the flag toggle, description approve/reject, History and the album-sync/Upload entry when `stage=prod` and `features.uploads` is false. A prod entry is a user-defined
environment (a public hostname), not a new hard-coded LAN address. An older app against prod sees 404 on those taps. Unit tests as in the other Android epics; instrumented tests only in CI. **Decision for the phone sync:** with uploads off on prod, the album sync targets the dev backend (home network or VPN) as today.

## P5d: VPS stack, roles, config, backups

### Stack (`deploy/prod/`, tracked; secrets are not)

```
deploy/prod/compose.yaml            postgres, backend-{general,metal,it}, migrate (profile "ops"), caddy
deploy/prod/Caddyfile               hostnames and routes, TLS, access log format, rate limits (hostnames are placeholders read from env)
deploy/prod/memesync-gate.sh        the forced command (P3 delivers it; P5d installs and tests the installation steps)
deploy/prod/sql/roles.sql           generated: `python -m sync.roles --db NAME` (below)
deploy/prod/env/*.example           .env.<env>.prod examples with every key and no value
deploy/prod/backup.sh               nightly P-table dump (below)
deploy/prod/README.md               stack map, ports, volumes, who owns what
```

- **Loopback only.** Backends listen on `127.0.0.1:<dev port + 1000>` (8081 to 9081 and so on; recorded in `Environments.md` by P7), Postgres on `127.0.0.1:5432` inside the compose network and **not published**. The sync reaches Postgres through `ssh -L` as the `memesync` user, never through the proxy. Only Caddy publishes 80 and 443.
- **Database sizing** (S5 headline 6): gunicorn `-w 2` and `pool_size=5, max_overflow=5` per worker for each of the three backends is 60 connections, under the default 100; the values live in the prod overlay and the compose command, and a startup log line prints the computed maximum. PgBouncer is a later option.
- **Proxy headers.** Uvicorn workers run with `--forwarded-allow-ips` set to the Caddy container so the rate limiters key on the real client address.
- **Caddy:** TLS automatic; the access log drops the query string for every path (`/api/images?q=` carries search text, S5 §7.2), logs no request bodies; hostname per environment; `/api/uploads` and `/api/admin*` are also refused at the proxy as a second layer; rate limits on the reader write routes
  (saves, not-found, bug reports, history recording) where the proxy can express them; optional basic auth or IP allow-list stanza commented in place for the day you want it. Decision 6 says public, so none is on by default.
- **Image:** `Dockerfile.backend` with `APP_STAGE=prod` baked in and the revision build argument; `.dockerignore` already excludes `.env.*`. `Dockerfile.migrate` (or `docker run --entrypoint` of the same image with alembic installed) is the one-shot migration container, run with the owner role. The backend image stays free of alembic.

### Roles and grants generated from the registry

`python -m sync.roles --db NAME [--password-from-env]` prints idempotent SQL from `sync.registry` (P1 `prod_write_set`, `shipped_tables`, `pulled_tables`) so the registry and the database cannot disagree:

| Role | Grants (per database) |
|---|---|
| `memes_owner` | owns all objects and extensions; migrations, `CREATE EXTENSION`, index DDL (decision 8). Login only from the VPS itself |
| `backend_app` | `SELECT` on all class D tables, `SELECT` on `popularity_absorptions`; `INSERT/UPDATE/DELETE` exactly on the six class P tables (`prod_write_set("backend_app")`); `USAGE` on the `meme_impressions` sequence; nothing else |
| `sync_writer` | `INSERT/UPDATE/DELETE/SELECT` on the class D tables (`prod_write_set("sync_writer")`), `USAGE/UPDATE` on the integer sequences of D tables, `MAINTAIN` for `REINDEX`/`VACUUM` on PostgreSQL 18, `SELECT` on `alembic_version`; **no** privilege on any class P table |
| `sync_reader` | `SELECT` on the six class P tables, `SELECT` on `alembic_version`, `SELECT (id)` on `images` (P4 rating rule); no write anywhere |
| `ocr_readonly` (prod variant) | `SELECT` on everything except `search_history`, `search_history_tags`, `meme_text_ratings` (message text); never given to agents |

Roles are cluster-wide, so the three databases share role names with per-database grants. A guard test (no database) compares the generated SQL with the registry: every table appears in exactly the grants its class allows, `backend_app` has no write on a D or X table, `sync_reader` none anywhere.
The integration test of S5 §2.5(3) then proves routes and grants agree: it creates these roles in `ocrdb_test`, drives the reader routes through the app as `backend_app` and expects no `permission denied`, and attempts each dev-only write expecting the database to refuse it.
To verify on a container before relying on them (S5 uncertainties): `MAINTAIN` with `REINDEX`, the `ON DELETE CASCADE` from `images` into `meme_text_ratings` without a `DELETE` grant for `sync_writer`, and the unprivileged HNSW rebuild path.

### Configuration

`environments/settings.prod.yaml` (common) and `settings.<env>.prod.yaml` (per environment): `stage`, `scheduler.enabled: false`, CORS origins from the prod hostnames (and `localhost` origins dropped on prod), `parse.model` removed for prod `general` and `metal` (meme-from-text off, decision 3),
`popularity.impressions.enabled: true`, `popularity.search_outcomes.enabled: true`, `search.meme_from_text.query_log.enabled: true` (owner-confirmed in S5), worker and pool sizes. Secrets only in `.env.<env>.prod` on the VPS (`DATABASE_URL` of `backend_app`, `BASE_PATH`, `BUG_REPORTS_PATH` outside `BASE_PATH`,
the frontend origins, `APP_ENV`, `APP_STAGE`). The `it` prod overlay is nearly empty (it has no parser already). A startup validation (`validate_*_settings`) must pass; a new test loads each prod overlay and runs the validators.

### Backups (the only copy of reader data)

`deploy/prod/backup.sh` (cron or a compose `ofelia`-style job, or a timer): nightly `pg_dump` of the six class P tables per database into a dated, rotated folder (14 days), plus a copy of the bug-reports directory. Dev's pull copies the newest dump over `ssh` during the pull (one more gate verb, `fetch-backup`, read-only) so one copy
lives off the VPS. Restore is documented in P7 and rehearsed in the acceptance run. Class D is rebuildable from dev by a full promote and needs no backup. The VPS provider's snapshot is a bonus, not the plan.

### Deploy order (S5 §5.3, a contract)

1. Dev: write the migration (additive and backward compatible only between steps 2 and 3), merge, build and tag the image with its revision.
2. VPS: pull the image, **keep the old backend running**, run the migration container with `memes_owner` per database.
3. VPS: restart the backend containers on the new image (rolling per environment; the compose healthcheck uses `/health`).
4. Dev: `sync.run` or the individual steps: pull, files, data. The promote refuses on unequal alembic heads (P2 preconditions), which is what makes steps 1 to 3 a prerequisite and not a convention.
A taxonomy or settings change is a code deploy (step 3), not a promote.

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| P5a-T1 | `stage_policy.py` with all verdicts (recomputed counts), completeness guard | S5 |
| P5a-T2 | `APP_STAGE` loader, `settings.prod.yaml`, `is_prod()`, Dockerfile env and revision arg | none |
| P5a-T3 | `StageGate`, conditional routers, docs off, scheduler off; gate tests | T1, T2 |
| P5a-T4 | Statistics read-only mode and capabilities endpoint; schema + generated types; `backend_api.md` | T2 |
| P5b-T1 | Web `VITE_STAGE` and capabilities; menu, redirects, hidden controls; vitest | P5a-T4 |
| P5c-T1 | Android capabilities and hidden entries; unit tests | P5a-T4 |
| P5d-T1 | `sync.roles` generator, guard test, role integration test in `ocrdb_test` | P1 |
| P5d-T2 | Container checks of the S5 uncertainties (`MAINTAIN`, cascade without DELETE) | P5d-T1 |
| P5d-T3 | `deploy/prod/` stack: compose, Caddyfile, env examples, README | P5a |
| P5d-T4 | Prod overlays and their validator test; gunicorn/pool sizing | P5a-T2 |
| P5d-T5 | `backup.sh`, the `fetch-backup` gate verb, restore script and test | P3 gate |
| P5d-T6 | Deploy-order note and migration container (`Dockerfile.migrate`) | T3 |

Size: large; P5a, P5b, P5c and P5d are four sub-deliveries. Mode: subagent-driven per sub-delivery. Reviews: **P5a security** (can any dev-only route answer on prod; fall-through; log leaks; the statistics write), **P5d security** (grants, ports, Caddy exposure, secrets in the image, the gate), **P5b/P5c** (readability, lifecycle) as in the other epics.
Live prod: not touched until the P7 acceptance run; compose and Caddy are tested locally (compose up on loopback with a self-signed or `tls internal` Caddy).

## Choices made by me (to confirm)

- Loopback port numbering (dev port + 1000), pools of 5+5 with two workers, 14-day backup retention, `Caddy` modules limited to the core (rate limiting may need a plugin; if so, rate limits stay in the app and `deploy/prod/README.md` says so).
- Postgres runs in a container with a named volume, not on the host (decision 15 read as "Docker Compose for everything"); a host Postgres is a small compose change.
- The ratings, saves and history-recording routes stay `reader` even while meme-from-text is off.
- Statistics served as the stored snapshot with a 404 when absent.
- Capabilities carry `revision` (git SHA) and `schema_head`, so P2 and P6 do not need to read the filesystem of prod.

## Risks and what is not settled

- Caddy plugin availability for rate limiting; the gate's behaviour under the real sshd (`restrict`, `Subsystem`).
- Whether `MAINTAIN` and a cascade without `DELETE` behave as recalled for PostgreSQL 18: P5d-T2 verifies on a container before the roles are final.
- Container filesystem ownership between the backend (writes bug reports) and `memesync` (writes `BASE_PATH`); the compose volumes and uids are fixed in T3 and tested.
- The public exposure means abuse handling (bug report spam, impression noise) is rate limits only; there is no per-user control.
- Frontend hosting (the static build behind Caddy, or elsewhere) is assumed to be Caddy serving the built files; a different host changes only the Caddyfile.

## Open for the owner

Hostnames and the domain; the VPS provider and its size; whether the web build is served by the same Caddy. None blocks the work: placeholders come from the environment.
