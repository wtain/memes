# P5: prod provisioning and hardening (design)

Status: draft (revised after design review 2026-11-12; reconciled with `interfaces.md` 2026-11-12; decisions 26 and 27 noted 2026-11-12)
Interfaces: names, ports, roles, grants, accounts, directories, gate verbs and settings are defined in `interfaces.md` (sections b, d, g, h); this document owns how they are provisioned. Where the tables below repeat them, `interfaces.md` wins.
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Review: docs/superpowers/reviews/2026-11-12-p5-prod-provisioning-design-review.md (dispositions below)
Depends on: S5 (`2026-11-12-s5-prod-topology-and-roles.md`), P1 (registry, built), decisions log in `README.md` (decisions 1 to 25; 17 to 25 override older text, including S5 where they disagree).
Feeds: P2 and P4 (roles, grants, handshake, capabilities), P3 (host users, directories, ssh keys, the gate's environment argument), P6, P7.
Splits into: **P5a backend stage mechanism**, **P5b web**, **P5c Android**, **P5d VPS stacks and roles**. Each is built and reviewed on its own.

## Intent

Turn the dev backend image into a prod that serves readers and nothing else, once per environment: routes that are not served are unreachable, writes that are not allowed are refused by the database even if
the route gate fails, an environment cannot be mistaken for another, and the VPS stacks are reproducible from files in the repository. The backend has no login (S5 headline 1) and prod is public (decision 6), so the design is defence
in depth: route tombstones in the app, size/rate caps in app and proxy, grants and `pg_hba` in Postgres, restrictions in sshd and the filesystem.

## Decisions this design relies on

| # | Decision | Effect here |
|---|---|---|
| 3, 9, 10, 12, 13 | meme-from-text off; uploads dev-only; `GET /api/history` dev-only; capabilities endpoint; bug reports kept with limits | unchanged |
| 17 | one stack per environment | three compose projects `memes-it`, `memes-metal`, `memes-general`, each with Postgres, backend, migrate job; Caddy is the only shared piece |
| 18 | `.env.<env>.sync`, tool-owned tunnel, identity handshake | P5 provides the marker, the capabilities `environment`, the per-env key and the fixed ports (table below) |
| 19 | hardened tar transfer | upload-only chrooted account, `BASE_PATH` read-only in the backend, backend refuses paths outside the images directory |
| 22 | comics not shipped | all comics routes `dev_only`; the meme page must not break when `/api/comics/by-image/*` answers 404 |
| 23 | abuse limits | rate limits and size caps, caps on `q`/facets, bounded bug-report body, write-amplification limits, growth alarm, no pruning |
| 24 | ratings routes off on prod | `dev_only`; `meme_text_ratings` stays class P |
| 25 | statistics dropped | `dev_only`; `statistics_snapshots` is class X; no statistics read-only mode |

## Goals / non-goals / anti-goals

- Goals: a route gate that fails safe and is built on the router itself; a policy covering every route; a capabilities endpoint with an `environment` field; web and Android that hide what prod does not serve; per-environment roles and grants generated from the registry and
  re-applied at every release; three isolated stacks and an edge proxy; fixed ports/keys/markers per environment; abuse bounds on every public write; backups of the only copy of reader data; prod settings tracked in git, secrets not.
- Non-goals: choosing the VPS provider or domains, the sync tools themselves (P2 to P4), CI/CD to the VPS (manual load-and-up is enough), authentication for readers, pruning (decision 11).
- Anti-goals: an admin route reachable on prod by omission; a prod backend role that can write a class D or X table; secrets in the image or repo; a container that runs pipelines or the scheduler; message text in any log; a credential that works against two environments.

## Review disposition

| Finding | Disposition | Where / reason |
|---|---|---|
| H1 gate recipe unbuildable on FastAPI 0.141.1 | adopted | P5a-1: `route_class` tombstone, no middleware matcher; guard iterates `router.routes` |
| H2 wrong-environment mistakes | adopted (decisions 17, 18) | per-env roles/ports/keys, `memes.environment` marker, capabilities `environment`, backend self-check at start |
| H3 write amplification, shared disk | adopted | P5a-5 (limits, caps, impression cap, browse feed not recorded), per-env volumes and quotas (P5d disk layout), role limits, Docker log rotation, growth alarm (P6 interface) |
| H4 bug-report body | adopted | P5a-5: pure-ASGI body cap before multipart parsing, directory cap (507), Caddy `request_body` |
| H5 grants not re-applied after migration | adopted | release runbook step 2b, migrate job applies and verifies grants; no default privileges for the app |
| H6 Postgres reachability/auth | adopted | host-loopback publish, tracked `pg_hba.conf`, owner via socket only, `REVOKE CONNECT FROM PUBLIC`, PG18 volume path, `shm_size`, bootstrap superuser creates extensions |
| M1 CORS/OPTIONS vs gate; localhost origins | adopted | tombstones sit behind CORS (no gate middleware); `cors.origins` setting, no localhost, `allow_credentials` false on prod (P5a-3) |
| M2 pool sizes not settable | adopted | P5a-7 reads `database.pool_size`/`max_overflow`; arithmetic redone per Postgres |
| M3 logs/leaks | adopted / adapted | log table below; `/health` fixed detail; capabilities carries no SHA (adapted: revision/schema head come from the DB marker `memes.revision` and `alembic_version`, not a public endpoint); statistics dropped (decision 25) |
| M4 untrusted pulled text | adopted / adapted | public-write section: caps on prod, sanitising on dev (P5a-8), ratings off (decision 24); pull growth tripwire is a P4 interface item |
| M5 ownership and container hardening | adopted | uid/gid table, `:ro` mounts, `read_only`, `cap_drop`, root-owned `/app`, `127.0.0.1:` ports guard test |
| M6 gate/keys per environment | adopted | `authorized_keys` per env with `permitopen` and gate argument |
| M7 reader gaps (Android Trends, `/reply`, comics, capability vs build flag) | adopted | P5b/P5c hide lists; comics is `dev_only` (decision 22); effective visibility = build flag AND capability |
| M8 overlay cannot remove a key | adopted | `parse.model: ""` plus test; `APP_ENV` mandatory on prod, backend refuses to start without it |
| M9 forwarded-allow-ips, slashes | adopted | fixed edge subnet CIDR, `X-Forwarded-Proto`, `redirect_slashes=False` on prod |
| L1 to L7 | deferred or folded | see Deferred |
| Decisions 26 and 27 (2026-11-12) | noted | Gate verbs `retire`, `restore`, `purge` and their caps are deferred (`interfaces.md` g): P5d-T5 installs the gate without them; the `retired` directory is still provisioned (unused until removals are built); the ratings column grant for `<sync_w>` stays unchanged; the capabilities and growth work are unaffected; the dry-run report (`interfaces.md` k) needs nothing from P5 |
| Reconciliation 2026-11-12 | applied | `interfaces.md` adopted: gate is `memesync-gate.py`, `memes.state` marker set by `provision.sh`, no `TEMP` for `<sync_w>`, the ratings column grant stated as the single exception, two keys per `memesync-<env>`, `work`/`retired` directories, `disk`/`containment` verbs fed by a root timer |
| Review options A/B/C, connection configuration, owner questions | A adopted (decision 17); connection recommendation adopted (decision 18) | owner questions: answered by decisions 17 to 25, residue at the end |

## P5a: backend stage mechanism

1. **Policy and route class (replaces the StageGate middleware).**
   - `Backend/app/stage_policy.py`: `ROUTE_POLICY[(method, path)] = "reader" | "dev_only"` where `path` is the full path including `/api` and the router prefix, one entry per method (the policy file is plain Python, one line per route, sorted by file).
   - `StageRoute(APIRoute)`: `get_route_handler()` returns, when `is_prod()` and the policy for `(method, full path)` is not `reader`, a handler that answers **404 `{"detail":"Not Found"}`** (identical to the router's own 404); a route with no policy entry makes the import raise on prod. Every router is created with `route_class=StageRoute`
     (a `make_router()` factory; a guard test asserts every router's routes are `StageRoute`). The router itself decides which route matched, so literal-before-parameter ordering (`/images/flagged` vs `/images/{image_id}`) cannot fall through: the tombstone matches first. Verified in the review on 0.141.1 (`%66lagged` included).
   - Do not walk `app.routes`: on FastAPI 0.141.1 it holds `_IncludedRouter` objects and `matches()` does not return the matched route (review H1).
   - Behaviour table: `HEAD` is not routed by `APIRoute` (405 today, unchanged, also on `reader` GETs); `OPTIONS` plain is 405, a CORS preflight is answered by `CORSMiddleware` before routing (no policy entry needed); a method not in the table for an existing path is the router's 405 (the `Allow` header lists only methods that exist);
     trailing slash: `redirect_slashes=False` on prod, so `/api/images/flagged/` is 404 and no 307 downgrade exists; paths are matched decoded and case-sensitive (`//api/...`, `/API/...`, `%2F` all 404 or match the same route the policy names).
   - **Second layer:** the fully dev-only routers are neither imported nor mounted on prod: `admin`, `admin_discovery`, `admin_profiles`, `ingestion`, `recut`, `comics`, `meme_profiles`, `trends`, `uploads`, `meme_text_ratings` (51 routes). The imports are conditional on `is_prod()` (these modules pull batch code the prod image does not have). Mixed routers (`images`, `history`, `diagnostics`, `meme_from_text`) stay mounted with tombstones. `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` on prod.
2. **Stage switch.** `APP_STAGE` (`dev` default, `prod`) read once in `config/settings.py`; `prod` adds `environments/settings.prod.yaml` and `settings.<env>.prod.yaml`; `config.stage.is_prod()` is the only reader. `Dockerfile.backend` bakes `ENV APP_STAGE=prod`. Overlay: `stage: prod`, `scheduler.enabled: false` (lifespan also skips the scheduler on `is_prod()`),
   `parse.model: ""` for `general` and `metal` (overlays cannot delete a key; a test asserts `get_query_parser()` is `None`), CORS origins, pool sizes. On prod the backend refuses to start unless `APP_ENV` is set explicitly (no default-to-general) and, after connecting, unless `current_setting('memes.environment')` equals `APP_ENV` (a wrong `DATABASE_URL` stops the container).
3. **Verdicts, final** (recomputed from the review's route walk: 90 routes = 89 under `/api` plus `GET /health`):

   | Group | Routes | reader | dev_only |
   |---|---|---|---|
   | images | 26 | 10: list, similar, descriptions, `meme/{id}`, popularity GET, saves POST and GET, impressions, trending, `{image_id}` file | 16 |
   | diagnostics | 2 + new capabilities | 2: health, **capabilities** (new) | 1: **statistics** (decision 25) |
   | history | 2 | 1: not-found POST | 1: `GET /history` (decision 10) |
   | meme-from-text (search, taxonomy) | 2 | 2 (answer `available: false`) | 0 |
   | ratings | 4 | 0 | 4 (decision 24) |
   | concepts, recommendations, bug-reports, root `/health` | 4 + 1 + 1 + 1 | 7 | 0 |
   | trends 5, uploads 1 | 6 | 0 | 6 |
   | admin 5, discovery 3, admin_profiles 2, ingestion 6, recut 6, **comics 15**, meme_profiles 4 | 41 | 0 | 41 |
   | **Total** | **91 with capabilities** | **22** | **69** |

   Changes against the review's own recount (26 reader / 64 dev_only of 90, before decisions 22/24/25): statistics (-1 reader), ratings (-4 reader) = **21 reader / 69 dev_only of 90, 22 / 69 of 91** with capabilities. Comics were already `dev_only`, so decision 22 changes no count; it changes the frontend (below) and the registry (done). T1 reproduces the table; the guard fails on any other number.
4. **Capabilities.** `GET /api/diagnostics/capabilities` returns `{stage, environment, features: {memeFromText, concepts, trends, comics, ratings, statistics, uploads, history, admin}}`; `environment` is `APP_ENV`; every flag is derived from settings and the policy (`memeFromText` is true only if a parser is configured; `comics`, `trends`, `ratings`, `statistics`, `uploads`, `history`, `admin` are false on prod).
   **No git SHA or schema head in the public payload** (M3). The release revision is stored by the release runbook as `ALTER DATABASE ... SET memes.revision = '<sha>'` and read by P2/P6 with `current_setting`; the schema head comes from `alembic_version`. Schema in `shared/schemas/`, TS/Kotlin/Python regenerated, `backend_api.md` updated.
5. **Abuse bounds (decision 23).** All limits are settings (`abuse.*`), enforced in the app so they do not depend on a Caddy plugin:
   - a per-client limiter (client from the trusted `X-Forwarded-For`, token buckets, empty buckets evicted, bounded size) on: saves, not-found, history/impression recording, bug reports. Over the limit a **recording is skipped and the response still served** (recording is already best effort); saves, not-found and bug reports answer 429. State: process-local, so the effective limit is workers x configured rate (2 workers).
   - `GET /api/images`: `q` at most 512 characters, at most 10 facet values of at most 64 characters, else 422; impressions per call capped at 50 rows (setting; was up to 100); on prod the browse feed (no `q`, no facets) writes no `search_history` row (`history.record_browse: false`); one recording budget per client per minute for history plus impression rows together.
   - bug reports: a pure-ASGI `BodyLimit` middleware for `/api/bug-reports` counts bytes from `receive` and answers 413 at 11 MB **before multipart parsing** (also checks `Content-Length`); the endpoint then reads in chunks; the directory is capped (`bug_reports.max_files`, `max_total_mb`; over the cap answers 507).
   - DB role limits (P5d), Caddy limits (P5d), Docker log rotation (P5d). The growth alarm lives in the P6 verify report (interface section).
6. **Image path containment (decision 19).** `get_image_path` in `Backend/app/services/image_store.py` today returns `_IMAGES_DIR / filename` with no check. New behaviour: resolve the candidate (`strict=False`) and require it to be inside the resolved images directory (`relative_to`); reject absolute names, names with a separator, NUL, `..` segments and any symlink whose target leaves the directory; the file route answers 404 for a refusal. Applies to every caller (file route, `image_exists`, the other helpers).
   Tests: `..`, `%2e%2e`, absolute path, double-encoded separators, NUL, a symlink inside the directory pointing outside, a symlink pointing inside (allowed or refused: **refused**, regular files only), a name that is a directory; the same requests sent through the full app with `APP_STAGE=prod` so the `BodyLimit` middleware, CORS and the tombstone routes are in the chain (none of them rewrites the path).
7. **Settings hooks.** `Storage/db.py` reads `database.pool_size` and `database.max_overflow` (defaults 10 and 20, dev unchanged). `Dockerfile.backend` CMD keeps four workers for dev images; the compose command overrides to two workers.
8. **Dev-side hygiene for pulled text.** `tools/discovery_report.py` strips control characters and ANSI/OSC sequences from query text before printing and prefixes cells starting with `=`, `+`, `-`, `@` in CSV output; the history/discovery UIs already escape (no raw HTML sink found in the review).
9. **Tests.** (a) Completeness guard (`tests/docs/` style): every route of every router (`router.routes`, prefix + `/api`) has a policy entry and every entry a route; counts 22/69. (b) Prod behaviour (`Backend/tests/`, mocked DB, `APP_STAGE=prod`): every `dev_only` (method, path) answers 404 incl. the fall-through cases, docs/openapi 404, no scheduler, unmounted prefixes absent, HEAD/OPTIONS/slash cases, startup refusal without `APP_ENV`. (c) The write-set role test of P5d. (d) Capabilities schema and flags per overlay. (e) Abuse bounds: oversize `q`, facet caps, body 413 before parse, limiter skip-not-fail, impression cap.

## P5b: web

`VITE_STAGE=prod` at build time (static default) hides: Cleanup and Admin groups, **Statistics**, Trends, `/reply` and its menu entry, the curator controls on `MemePage` (`ProfileEditor`, description approve/reject, note editor, flag toggle, popularity set control, **`MemeComicsBlock`**), history screens, the Upload page; their routes redirect to `/explore`.
`MemeComicsBlock` and every `/api/comics/*` call are not rendered or issued on prod, and a 404 from any of them is treated as "feature off", never an error banner or a broken meme page (vitest: meme page renders with comics calls answering 404). The build also reads `/api/diagnostics/capabilities`; **effective visibility = build flag AND capability** (a capability can only
reduce what the build shows; switching a feature on later needs a rebuild). Tests: `vitest` for menu, redirects, hidden controls and the 404 case; `tsc -b`, `eslint`.

## P5c: Android

Reads `capabilities` once per environment (backends are user-defined at runtime; a prod entry is a public hostname) and hides, when `stage=prod`: Flagged, flag toggle, description approve/reject, History, **Trends**, **Statistics**, any comics entry, and the album-sync/Upload entry (`features.uploads` false; phone sync targets the dev backend as today). An older app sees 404 on those taps. Unit tests; instrumented tests only in CI.

## P5d: VPS stacks, roles, config, backups

### Layout (`deploy/prod/`, tracked; secrets are not)

```
deploy/prod/compose.yaml          parameterised: services db, backend, migrate (profile "ops"); run as  docker compose -p memes-<env> --env-file <env>.prod.env
deploy/prod/edge/compose.yaml     project memes-edge: Caddy only
deploy/prod/edge/Caddyfile        hostnames, routes, TLS, log format, body/header limits, headers
deploy/prod/postgres/             postgresql.conf overrides, pg_hba.conf (templated per env), provision.sh
deploy/prod/memesync-gate.py      forced command (P3 delivers; P5d installs it at /opt/memes/ and the key line passes the environment as its argument)
deploy/prod/sshd/                 sshd_config.d snippet and authorized_keys templates
deploy/prod/sql/roles.<env>.sql   generated by `python -m sync.roles --env <env>`; never contains a password
deploy/prod/backup.sh, restore.sh
deploy/prod/systemd/              timers: backup, `sweep`, and the root-run facts timer that writes /var/lib/memes/<env>/{disk,containment}.json for the gate verbs
deploy/prod/env/<env>.prod.env.example   every key, no value (named so the repo's `.env.*` ignore rule does not hide them)
deploy/prod/README.md             stack map, port table, volumes, who owns what, release runbook
```

### Environment table (fixed; recorded in `Environments.md` by P7)

| Env | Compose project | DB name | Postgres on host loopback | Local tunnel port (dev side) | Backend (edge alias) | Project subnet | sync ssh account / aliases (tunnel, verbs, upload) |
|---|---|---|---|---|---|---|---|
| metal | `memes-metal` | `memes_metal` | `127.0.0.1:6432` | 16432 | `api-metal:8000` | `172.31.1.0/24` | `memesync-metal` / `memes-prod-metal`, `-sync`, `-up` |
| general | `memes-general` | `memes_general` | `127.0.0.1:6434` | 16434 | `api-general:8000` | `172.31.2.0/24` | `memesync-general` / `memes-prod-general`, `-sync`, `-up` |
| it | `memes-it` | `memes_it` | `127.0.0.1:6436` | 16436 | `api-it:8000` | `172.31.3.0/24` | `memesync-it` / `memes-prod-it`, `-sync`, `-up` |

Remote ports follow dev's cluster ports plus 1000 (5432/5434/5436). The ssh aliases `memes-prod-<env>` (tunnel key), `memes-prod-<env>-sync` (verb key) and `memes-prod-<env>-up` (upload account) live in the workstation's ssh config and are the tracked settings `sync.prod.ssh_alias`, `gate_alias`, `upload_alias`. Roles are named per environment: `memes_<env>_owner`, `_app`, `_sync_w`, `_sync_r`, `_ro`; one password and one ssh key per environment, so a leaked `it` credential is useless against `general`.

### Networks and Caddy

- Each project has a private network with the fixed subnet above (Postgres and its backend, migrate job). Postgres is published **on the host loopback only** (`127.0.0.1:<port>:5432`), because sshd runs on the host and `ssh -L` needs a host listener. A guard test over the compose file requires every `ports:` entry other than Caddy's 80 and 443 to start with `127.0.0.1:` (Docker publishes bypass ufw).
- **Caddy reaches the backends over one shared external network `memes-edge`** (created once: `docker network create --subnet 172.30.0.0/24 memes-edge`); each backend joins it with the alias in the table, Postgres never does, no backend port is published. Chosen over loopback ports because the Caddy container cannot reach the host's 127.0.0.1 without host networking, and a fixed subnet gives the proxy-header CIDR.
  Uvicorn is started with `--forwarded-allow-ips 172.30.0.0/24`; Caddy sends `X-Forwarded-For` and `X-Forwarded-Proto`. Backends of different environments share this network but hold no credentials for each other's database.
- Caddy: automatic TLS; hostnames from the environment file (placeholders in the repo); SPA and API of one environment share a host, the SPA is served from `/srv/memes/<env>/web`; `Referrer-Policy: no-referrer`; `X-Content-Type-Options: nosniff` and a sandboxing `Content-Security-Policy` on `/api/images/*` file responses; `request_body max_size` 11 MB on `/api/bug-reports` and 64 KB elsewhere; header size limits; `/api/admin*`, `/api/uploads*` and the other unmounted prefixes refused with 404 as a second layer; no basic auth or allow-list by default (stanza commented in place).
  Rate limiting is enforced in the app (P5a-5); a Caddy rate-limit plugin (custom xcaddy build, pinned) or a CDN is an optional additional layer, not a dependency.

### Postgres per environment

- Image `postgres:18` pinned by digest; volume mounted at `/var/lib/postgresql` (PG18 image layout), `shm_size: 256m`; `mem_limit` and `cpus` per service. Tracked config: `max_connections = 40`, `password_encryption = scram-sha-256`, `log_statement = none`, `log_min_error_statement = panic`, `log_min_duration_statement = -1` (no statement text in logs), `ssl` not needed (loopback and private network).
- `pg_hba.conf` tracked, scram only, first match wins: `local` lines for `<owner>` (migrations and DDL over the socket; the `migrate` service and `docker exec` share the socket volume) and `<sync_r>` (backup over the socket); `host` lines: `<owner>` and `postgres` **reject**, then one line per role and database for the project subnet (`<app>`, `<sync_w>`, `<sync_r>`, `<ro>`), final `reject`. The image's trust defaults are replaced; the bootstrap superuser `postgres` is used only by `provision.sh` at first start (it creates the database owned by `<owner>`, `CREATE EXTENSION` for those the Alembic chain needs (`vector` and any other; listed by T2 from `Storage/alembic`; `vector` is not trusted, so the owner cannot create it), and sets the markers).
- **Pools, per Postgres:** backend `-w 2`, `pool_size=5, max_overflow=5` = at most 20 connections; sync tools at most 6 (role `CONNECTION LIMIT`: app 24, sync_w 3, sync_r 3, ro 2, owner 3), superuser reserve 3: `max_connections = 40` leaves headroom; a startup log line prints the computed maximum. The old shared-cluster arithmetic (60 of 100) no longer applies; an environment under attack cannot starve another. Role settings: `<app>`: `statement_timeout = 10s`, `idle_in_transaction_session_timeout = 30s`, `lock_timeout = 3s`.
- **Markers (database settings, readable by any role):** `ALTER DATABASE memes_<env> SET memes.environment = '<env>'` and `SET memes.state = 'seeding'` (both by `<owner>` in `provision.sh`; the owner flips `memes.state` to `serving` by hand after the first-seed index rebuild, P2 H7), and, at each release, `memes.revision = '<git sha>'`. No other seeding flag exists.
- RAM (estimates, unmeasured): backends about 0.4 GB each for general and metal, 0.3 GB for it; Postgres shared_buffers 256 MB / 256 MB / 64 MB plus process overhead; OS, dockerd, sshd 0.5 GB: about 3 GB steady. An HNSW rebuild (decision 8, owner step, `maintenance_work_mem` up to 1 GB) is run for one environment at a time while the others stay up. **4 GB is the floor, 8 GB recommended.**

### Roles and grants (generated by `python -m sync.roles --env <env>` from `sync.registry`)

| Role | Grants |
|---|---|
| `<owner>` | owns database and objects; migrations and index DDL; connects over the socket only; never used by a service |
| `<app>` | `USAGE` on schema; `SELECT` on every class D table; `SELECT, INSERT, UPDATE, DELETE` on exactly the six class P tables (`prod_write_set("backend_app")`; `SELECT` is needed for `ON CONFLICT` and `WHERE`); `USAGE, SELECT` on the `meme_impressions` sequence; nothing on class X, `alembic_version` or anything else |
| `<sync_w>` | `SELECT, INSERT, UPDATE, DELETE` on class D (`prod_write_set("sync_writer")`); `USAGE, SELECT, UPDATE` on sequences of D tables with integer ids; `MAINTAIN` on D tables (`REINDEX`, `VACUUM`; PG17+); `SELECT` on `alembic_version`; **no `TEMP`** (P2 uses `execute_values`, no staging table); column-level `SELECT (image_id, query_hash, updated_at)` on `meme_text_ratings` (made for the P2 retire check, which is deferred by decision 27; the grant stays so building removals later needs no grant change; no message text) **is the single exception to S5's rule that `sync_writer` has no privilege on any class P table**; no write on any class P or X table |
| `<sync_r>` | `SELECT` on the six class P tables; `SELECT` on `alembic_version`; `SELECT (id)` on `images` (P4 rating rule); no write anywhere, no D table |
| `<ro>` | `SELECT` on everything except `search_history`, `search_history_tags`, `meme_text_ratings`; never given to agents |

- Database level: `REVOKE ALL ON DATABASE ... FROM PUBLIC` (removes `CONNECT` and `TEMP`), then per-role `CONNECT`; `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
- **No default privileges for `<app>` or `<sync_r>`:** a new table gets no access until `sync.roles` runs, which makes a missed grant a loud failure at the release check, not a silent one; the registry test in CI already forces every new table to be classified. (`<sync_w>` gets no default privileges either, for symmetry.)
- Guard tests (no database): generated SQL equals the registry (each table in exactly the grants its class allows; `<app>` no write on D or X; `<sync_r>` none anywhere; no password in the SQL; **`<sync_w>` has no privilege on any class P table except the column-level `SELECT (image_id, query_hash, updated_at)` on `meme_text_ratings`, asserted exactly, and no `TEMP`**). Integration test in `ocrdb_test` (roles created there): reader routes run as `<app>` without `permission denied`, each dev-only write is refused by the database, the cascade from `images` into `meme_text_ratings` works for `<sync_w>` without a `DELETE` grant there.
- Container verification (P5d-T2, before the roles are final): `MAINTAIN` with `REINDEX`, the cascade without `DELETE`, the HNSW rebuild path, extension creation rights, PG18 data directory layout, `pg_hba` order.

### Host users, directories, ownership (uid/gid)

| Item | Owner : mode | Backend container | Notes |
|---|---|---|---|
| `/srv/memes/<env>/images` (`BASE_PATH`) | `memesync-<env>` : `755`, files `644` (the gate runs with umask 022) | **mounted `:ro`**, user `1000:<gid>` reads as "other" | the backend writes nothing here on prod (uploads, recut, comics are gated) |
| `/srv/memes/<env>/bug-reports` (`BUG_REPORTS_PATH`, outside `BASE_PATH`) | `1000` (container user) : group `memesync-<env>`, mode `2775` setgid | read-write | an entrypoint wrapper sets `umask 002`, so files are `664` and group-writable; `memesync-<env>` can `delete-verified` (P4) |
| `/srv/memes/<env>/work`, `/srv/memes/<env>/retired` | `memesync-<env>`, mode 0700, **same filesystem as `images`** (`rename(2)`; the gate `handshake` checks) | not mounted | gate batches, record, counters, flock; quarantine `<date>/` (P3) |
| `/srv/memes/<env>/staging` | chroot root owned by root; `staging/incoming` owned by `memeup-<env>`, group `memesync-<env>`, mode 2770 | not mounted | separate **upload-only** chrooted account (decision 19); P3 owns the transport and the gate; P5d creates the account and directories |
| `/srv/memes/<env>/pgdata` | postgres uid | not mounted | separate from the images and everything else |
| `/srv/memes/<env>/backups`, `/web` | `memesync-<env>` | `/web` read-only in Caddy | |

- Container user `1000:<gid of memesync-<env>>`; `Dockerfile.backend` stops `chown -R appuser /app` (code is root-owned and read-only); compose sets `read_only: true`, `tmpfs: /tmp` (256 MB, the multipart spool), `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `mem_limit`, `cpus`, `pids_limit`, healthcheck on `/health`.
- sshd: password authentication off, `AllowUsers` the six accounts plus the admin user; `memesync-<env>` holds two keys (P3 review question 2): the verb key `restrict,command="/usr/bin/python3 /opt/memes/memesync-gate.py <env>"` (no forwarding) and the tunnel key `restrict,port-forwarding,permitopen="127.0.0.1:<pgport>",command="/bin/false"` (`ssh -N -L` therefore reaches only that environment's Postgres, and the gate knows its environment from its key line, not from the client); `memeup-<env>`: chroot, `ForceCommand`/`internal-sftp` as P3 specifies, no forwarding.

### Disk layout and quotas

Each environment has its own directory tree, so one environment cannot fill another's data. Within an environment the Postgres data, images, bug reports and backups are separate filesystems or **XFS project quotas** (or provider block volumes); where the VPS offers neither, free-space alarms stand in. Caps: bug-reports directory by the app (P5a-5), backups by retention, images by the promote plan (P3 handshake). Docker `json-file` logs: `max-size 10m`, `max-file 3` on every service. Free space of **both** the Postgres volume and the files volume is reported by the gate `handshake` (interface change for P3) and, per mount of the environment, by the gate verb `disk` (which prints a file written by a root-run timer: the gate never gets the docker socket); P6 checks it (warn at 20 percent free, error at 10). The verb `containment` works the same way for the backend container's configuration (P6 S8).

### Log suppression per layer

| Layer | Setting |
|---|---|
| uvicorn/gunicorn | `--no-access-log`, `--log-level warning`; no request line with a query string is ever printed |
| Caddy | log filter deletes `request>uri` and all `request>headers` (so `Referer`, `Cookie` and `Authorization` vanish) and appends `{http.request.uri.path}` as `path` via `log_append`; no bodies; to be confirmed on the Caddy version in use |
| Docker | json-file rotation above |
| Postgres | statement logging off, `log_min_error_statement = panic` |
| App | no message text (existing rule); `/health` answers a fixed detail on prod |

### Configuration

`environments/settings.prod.yaml` (common) and `settings.<env>.prod.yaml`: `stage`, scheduler off, `cors.origins` (prod hostnames only, `allow_credentials` false), `parse.model: ""`, `popularity.impressions.enabled: true`, `popularity.search_outcomes.enabled: true`, `search.meme_from_text.query_log.enabled: true`, `abuse.*`, `history.record_browse: false`, `bug_reports.*`, `database.pool_size`/`max_overflow`. Secrets only in the VPS file `<env>.prod.env`
(`DATABASE_URL` of `<app>`, `BASE_PATH`, `BUG_REPORTS_PATH`, `APP_ENV`, `APP_STAGE`, origins). A test loads each prod overlay and runs the `validate_*_settings` functions; another asserts the tracked env examples hold no values.

### Backups (the only copy of reader data)

`backup.sh <env>` (host timer, per environment): `pg_dump -Fc` of the six class P tables through `docker exec` over the socket as `<sync_r>`, plus a tar of the bug-reports directory, into `/srv/memes/<env>/backups` with 14-day rotation. The dev pull fetches the newest dump (`fetch-backup`, read-only gate verb) so one copy lives off the VPS. Restore order: promote first (the `images` rows the ratings foreign key needs), then restore. Class D is rebuildable by a full promote.

### Release runbook (a contract; P2 refuses unequal heads)

1. Dev: migration (additive only between steps 2 and 3: new columns nullable or with a server default, indexes `CONCURRENTLY`), merge, build image tagged with the **git SHA** (never `latest`); transfer with `docker save | gzip | ssh admin docker load` (no registry in v1). A non-additive migration needs a maintenance window: stop the backend, migrate, start.
2. Per environment, in order `it`, `metal`, `general` (canary first): keep the old backend running; run the `migrate` service: `alembic upgrade head` as `<owner>`.
2b. The same job then applies `roles.<env>.sql` (idempotent) and **verifies the grants** with `has_table_privilege` against the registry (S1 of P6 as a pre-check); a mismatch fails the release before step 3.
3. Restart the backend on the new tag (`depends_on` healthy); it refuses to start on a marker mismatch. Then `ALTER DATABASE ... SET memes.revision = '<sha>'`; smoke: `/health`, capabilities `environment` equals the environment.
4. Dev: `sync.run` or the steps (pull, files, data); P2 compares the schema heads and the marker revision.
A taxonomy or settings change is a code deploy (step 3), not a promote.

## Public writes and untrusted text

Anything a stranger can POST ends in dev's database at the next pull (`search_history`, `search_not_found`, `meme_saves`, `meme_impressions`; ratings are off). Rules: (1) caps on every text field at the route (P5a-5); (2) prod stores text as given and never interprets it; (3) on dev, pulled text is untrusted: tools print and export it sanitised (P5a-8), the web UI escapes, and agents must not treat pulled text as instructions;
(4) a flood is bounded by the limits above and detected by the growth alarm; the pull's own size ceiling is a P4 interface item; (5) ratings, which could poison the evaluation set, are off (decision 24); when `/reply` returns, pulled ratings stay out of `export_ratings` unless flagged (P4 interface).

## Interfaces the other designs must mirror

- **P2/P4/P6 connections:** roles `memes_<env>_sync_w` / `_sync_r`, database `memes_<env>`, local tunnel and remote ports from the table, three ssh aliases per environment (tracked settings `sync.prod.*`, `interfaces.md` b); `memes.environment`, `memes.state` and `memes.revision` via `current_setting`; capabilities is `{stage, environment, features}` and **no** `revision`/`schema_head` (P2 reads `memes.revision` and `alembic_version`).
- **P2:** `sync_w` has column-level `SELECT (image_id, query_hash, updated_at)` on `meme_text_ratings` (the one exception) and `MAINTAIN`, no `TEMP`; statistics are neither promoted nor served (class X).
- **P3:** forced-command argument `<env>` per key line, program `memesync-gate.py`; separate `memeup-<env>` account; `:ro` images mount in the backend; `handshake` also reports free space of the Postgres volume; files created with umask 022; bug-reports directory `2775`, group `memesync-<env>`.
- **P4:** `sync_r` columns as above; ratings routes off; a growth ceiling per pull (`--allow-growth`); sanitising on dev.
- **P6:** growth alarm for the reader tables (rows per day against the trailing median, or a flat ceiling), a staleness alarm (no new row for N hours while the site has traffic), free space of both volumes, S1 run before the backend restart.

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| P5a-T1 | `stage_policy.py`, `StageRoute`, `make_router`, all verdicts (22/69 of 91), completeness guard | S5 |
| P5a-T2 | `APP_STAGE` loader, `settings.prod.yaml`, `is_prod()`, Dockerfile (env, root-owned `/app`, entrypoint umask, no access log) | none |
| P5a-T3 | conditional imports and mounts, docs off, `redirect_slashes=False`, scheduler off, CORS settings, `/health` fixed detail, startup `APP_ENV` and marker checks; prod behaviour tests | T1, T2 |
| P5a-T4 | capabilities endpoint with `environment`; schema, generated types, `backend_api.md` | T2 |
| P5a-T5 | image path containment in `image_store.py` with symlink/`..`/encoding tests through the full app | none |
| P5a-T6 | abuse bounds: limiter, `q`/facet caps, impression cap, browse feed not recorded, `BodyLimit`, directory cap | T3 |
| P5a-T7 | pool settings hook in `Storage/db.py` | none |
| P5a-T8 | `discovery_report` sanitising | none |
| P5b-T1 | web `VITE_STAGE`, AND with capabilities, hide list incl. statistics/trends/reply/comics, 404 tolerance, vitest | P5a-T4 |
| P5c-T1 | Android capabilities and hide list incl. Trends/Statistics; unit tests | P5a-T4 |
| P5d-T1 | `sync.roles --env` generator, guard test, role integration test in `ocrdb_test` | P1 |
| P5d-T2 | container verification of the uncertainties (`MAINTAIN`, cascade, extensions, PG18 layout, `pg_hba`) | T1 |
| P5d-T3 | `compose.yaml`, edge project, Caddyfile, env examples, README, ports guard test | P5a |
| P5d-T4 | Postgres config, `pg_hba`, `provision.sh` (markers, revokes, extensions) | T2 |
| P5d-T5 | host users, directories (incl. `work`, `retired`), sshd and `authorized_keys` templates (verb key and tunnel key), quotas, installs `memesync-gate.py`, root timer for `disk.json`/`containment.json` and the gate verbs `disk`, `containment` | P3 gate |
| P5d-T6 | prod overlays and validator test | P5a-T2 |
| P5d-T7 | `backup.sh`, `restore.sh`, gate verb `fetch-backup`, restore test | P3 gate |
| P5d-T8 | migrate service, grants re-apply and verify, release runbook | T1, T3 |

Size: large, four sub-deliveries. Mode: subagent-driven per sub-delivery. Reviews: **P5a security** (can a `dev_only` route answer; path containment; log leaks; body/limit bypass), **P5d security** (grants, `pg_hba`, ports, Caddy exposure, secrets in image, gate and keys, ownership), **P5b/P5c** (lifecycle, readability). Live prod is not touched until the P7 acceptance run; compose and Caddy are tested locally (loopback, `tls internal`).

## Risks

- Caddy log filter syntax and request-body limits depend on the Caddy version: verified in P5d-T3 on the pinned image.
- Process-local limiters multiply by the worker count and do not share state across restarts; the CDN or plugin layer is the real answer to a distributed flood.
- Quotas need an XFS-capable image or provider volumes; otherwise only alarms protect the disk.
- The shared `memes-edge` network lets one backend reach another's port; no credentials cross it.
- Frontend hosting assumed to be Caddy serving the build; elsewhere only the Caddyfile changes.

## Deferred (low findings)

- L1 `.gitignore`/`.dockerignore` patterns: env examples are named `<env>.prod.env.example` (done by naming); the `**/.env*` dockerignore tightening is deferred to P5d-T3.
- L2 `roles.<env>.sql` never holds passwords (psql variables / `\password`): folded into the P5d-T1 guard test.
- L3 `nosniff` and CSP on file responses: in the Caddy list above, verified in P5d-T3.
- L4 the statistics edge case is void (route dropped).
- L5 an unattended off-VPS backup copy (object storage push) beyond the dev pull: deferred; restore-after-promote is in the backup section.
- L6 image distribution is `docker save | ssh load` tagged by SHA; a registry is deferred; `migrate` is a service of the same image with Alembic added in a second stage (kept out of the backend image).
- L7 extension creation: decided (bootstrap superuser at provisioning); the list of extensions comes from T2.

## Open for the owner

Hostnames and domain; whether to add a CDN/WAF or the Caddy rate-limit plugin on top of the app limits; the VPS size (4 GB floor) and whether the provider offers XFS quotas or block volumes; an off-VPS backup target beyond the dev pull; where the sync credentials live on the workstation (decision 21 sets the file and its permissions; an OS credential store is optional).

## Revision notes

### P5d-T1 and P5d-T2 (2026-11-16): role generator built, uncertainties verified in a container

Built: `sync/roles.py` (`python -m sync.roles --env <env> [--check|--stdout]`), the committed `deploy/prod/sql/roles.{metal,general,it}.sql`, guard tests `tests/sync/test_roles.py`, role integration tests `tests/integration/sync/test_roles_db.py`.
The file is plain SQL (no psql meta commands), one transaction, no credential, and carries a header with generator version and registry digest. It is **self-verifying**: after the grants a `DO` block compares the effective
privileges (PUBLIC and memberships included, tables, columns, sequences, role attributes and limits, database owner, default ACLs, `SET` on the markers) with the registry and raises, rolling the whole file back. Re-apply means: revoke everything the four grantee roles hold in
schema public, then grant the exact list; a re-run is a no-op and drift (an extra grant, a schema `CREATE`, a default privilege, a role membership) is removed. It runs as the bootstrap superuser (also creates the five roles, sets attributes, connection limits, the `<app>` role settings, removes memberships,
makes `<owner>` the owner of the database) and as `<owner>` (release step 2b: the role part is skipped with a NOTICE, grants and verification run). On a database with no schema yet the table grants are skipped with a NOTICE; apply the file again after the first migration. It refuses any database
other than `memes_<env>`.

Verified in a throwaway `pgvector/pgvector:pg18-trixie` container (PostgreSQL 18.3, the dev image) with the real committed files, and in the local docker test server (also 18.3); the container, its volumes and network were removed afterwards. The role integration tests pass on PG 18 and skip the `MAINTAIN` test below PG 17.

| Uncertainty | Result |
|---|---|
| `MAINTAIN` | Verified: `<sync_w>` runs `REINDEX TABLE`, `REINDEX INDEX CONCURRENTLY` (HNSW and GIN trigram), `REINDEX TABLE CONCURRENTLY`, `VACUUM`, `ANALYZE` on class D tables. On a class P table `REINDEX` is refused and `VACUUM` is skipped with a warning. `<sync_w>` cannot `DROP INDEX`, `CREATE INDEX`, `TRUNCATE`, `COPY ... TO file`, create a function or table, use `TEMP`, `SET ROLE` to the owner. The HNSW rebuild after the first seed is therefore an **owner** step (`DROP INDEX`, `CREATE INDEX ... USING hnsw`, `maintenance_work_mem` raised for the session); `REINDEX` by `<sync_w>` is only a rebuild in place. |
| Cascade without `DELETE` | Verified: `DELETE FROM images` as `<sync_w>` removes the `meme_text_ratings` rows of the image although `<sync_w>` has no `DELETE`, no table-level `SELECT` and only the three-column `SELECT` on that table (the foreign key action runs as the table owner). A direct `DELETE FROM meme_text_ratings` is refused. Note: `SELECT count(*)` on `meme_text_ratings` **is** allowed for `<sync_w>` (a column privilege is enough for `count(*)`); the text column and `*` are refused. The H3 check uses table-level `has_table_privilege`, so it is unaffected. |
| Real H3 | `sync.connect.gather_role_facts` and `evaluate_role` return no problem for `<sync_w>` (as writer) and `<sync_r>` (as reader) against the generated grants (they failed on the all-powerful fixture user until now). |
| Extensions | `vector` is **not** a trusted extension (no `trusted` line in `vector.control`): as `<owner>` `CREATE EXTENSION vector` fails with `permission denied`; `pg_trgm` is trusted and the owner may create it. So `provision.sh` creates both as the bootstrap superuser, before the first migration (the migration chain's `CREATE EXTENSION IF NOT EXISTS` should then be a no-op for the owner; not run against the real migration chain here). |
| **Image** | **The design says `postgres:18`; that image has no pgvector** (`postgres:18` 18.7 ships `pg_trgm.control` but no `vector.control`; checked). Prod needs `pgvector/pgvector:pg18-trixie` (what dev uses, 18.3 today) pinned by digest, or a Dockerfile adding the package. P5d-T3/T4 must name that image. |
| PG18 data layout | Verified: data lives in `/var/lib/postgresql/18/docker` (`PGDATA`), the volume mounts at `/var/lib/postgresql`. Mounting a volume at the old `/var/lib/postgresql/data` makes the container **exit with an error** (it refuses a mount at the pre-18 path), so the compose file must use the new mount. |
| `pg_hba` order | Verified with a tracked-style file (`-c hba_file=`, loaded without errors in `pg_hba_file_rules`): `local ... postgres peer` (needed: the image entrypoint initialises over the socket as the OS user `postgres`; use `docker exec -u postgres`, plain `docker exec` runs as root and fails peer), `local` scram lines for `<owner>` and `<sync_r>`, `host postgres,<owner> all reject`, one `host` line for the project subnet and the four service roles, final `reject`. Results: the four service roles connect from the subnet to their database only (not to `postgres` or `template1`); `<owner>` and `postgres` are rejected over TCP; `<app>` has no `local` line and is refused over the socket; `<owner>` and `<sync_r>` connect over the socket with a password. Through a published port (`127.0.0.1:<port>`, which is what `ssh -L` reaches) the server sees the **bridge gateway of the project network** (`172.31.99.1/32` in the probe), which is inside the project subnet, so the subnet line admits the tunnel. Passwords can be set after the first apply with `ALTER ROLE ... PASSWORD :'pw'` fed through `psql -v pw=...` on stdin; the file itself never holds one. |
| Markers | **Finding:** since PostgreSQL 15 a database owner gets `permission denied to set parameter "memes.environment"` for `ALTER DATABASE ... SET memes.*`; only a superuser can grant `SET ON PARAMETER`. The design's "set by `<owner>` in `provision.sh` / at each release" therefore needed a grant: `roles.<env>.sql` (superuser branch, PG 15+) now runs `GRANT SET ON PARAMETER memes.environment, memes.state, memes.revision TO <owner>` and revokes any other holder; verified that the owner then sets all three, that any role reads them with `current_setting`, and that the verification block catches a stray grant. Parameter ACLs are cluster-global but the grantee is the per-environment owner role. Alternative if this grant is unwanted: set the markers as the bootstrap superuser (`docker exec -u postgres psql`) in `provision.sh` and in release step 3. |

Consequences for the other P5d items: T3/T4 use the pgvector image; T4 `provision.sh` order is database -> extensions (superuser) -> `roles.<env>.sql` (superuser) -> passwords -> markers (owner or superuser) -> first migration (owner, over the socket) -> `roles.<env>.sql` again (it is the grants step; `<owner>` may run it, release step 2b) -> flip `memes.state` after the index rebuild.
Open: `ALTER DATABASE ... OWNER` and `CREATE ROLE` are in the file, so a managed Postgres without a superuser would need the role part done by hand (not the plan: Postgres runs in our own container).
