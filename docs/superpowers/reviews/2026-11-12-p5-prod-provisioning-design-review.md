# Review: P5 prod provisioning and hardening (design)

Status: done

Reviewed: `2026-11-12-p5-prod-provisioning-and-hardening-design.md` (draft) against the epic README (16 decisions), S5, P2, P3, P4, P6/P7,
the S3 ADR, `sync/registry.py` and the code at the worktree HEAD. Read-only review: no database, container, network or server was touched,
no `.env.*` file was opened. Throwaway scripts (route enumeration, request probes against the real app object with a dummy `DATABASE_URL`,
a `route_class` experiment) are in the session scratchpad `p5review/`. Items marked "recalled" come from knowledge of PostgreSQL 18, Caddy,
Docker and Starlette, not from a run here.

Summary: 0 critical, 6 high, 9 medium, 7 low. The architecture (default-deny gate + grants + proxy) is right. The gate as specified cannot
be built on the pinned FastAPI, nothing stops a promote or pull from talking to the wrong environment's database, and the public write
routes can fill the one disk that holds all three environments.

## Route inventory check (S5 against the code)

Enumerated by walking the pinned FastAPI's included routers (see H1 for why `app.routes` does not work): **90 routes = 89 under `/api` + `GET /health`**.
S5's 90 / 31 / 59 reproduces exactly (13 root/diagnostics/history/images + 6 meme-from-text + 1 recommendations + 4 concepts + 5 trends + 2 uploads/bug-reports = 31).
Per file: images 26, comics 15, ingestion 6, recut 6, trends 5, admin 5, meme_text_ratings 4, meme_profiles 4, concepts 4, admin_discovery 3, diagnostics 2,
history 2, meme_from_text 2, admin_profiles 2, recommendations 1, uploads 1, bug_reports 1, root health 1. Plus 8 framework routes (`/docs`, `/docs/oauth2-redirect`,
`/redoc`, `/openapi.json`, each GET and HEAD) that no router owns. No WebSocket route, no `Mount`, no static files exist.

Applying the design's verdicts (decisions 1, 3, 4, 5, 9, 10, 12, 13), my recount, which T1 should reproduce:

| | reader | dev_only |
|---|---|---|
| images (26) | 10: list, similar, descriptions, meme/{id}, popularity GET, saves POST and GET, impressions, trending, `{image_id}` | 16 |
| diagnostics (2) | 2: health, statistics (snapshot) | 0 |
| history (2) | 1: not-found POST | 1: `GET /history` |
| meme-from-text (2) + ratings (4) | 6 | 0 |
| concepts 4, recommendations 1, bug-reports 1, root health 1 | 7 | 0 |
| trends 5, uploads 1 | 0 | 6 |
| admin 5, discovery 3, admin_profiles 2, ingestion 6, recut 6, comics 15, meme_profiles 4 | 0 | 41 |
| **existing 90** | **26** | **64** |
| new `capabilities` | +1 | |
| **91** | **27** | **64** |

So S5's "31 / 59" becomes 26 / 64 (27 / 64 of 91 with capabilities), not the "a few change" the P5 text implies.

---

## Findings

### High

**H1. The StageGate matching recipe does not work on the pinned FastAPI (S5 section 2.2; P5a item 2; P5a test 6a)**
- Design: a pure-ASGI middleware that walks `app.routes`, calls `route.matches(scope)` "the way Starlette does" and looks up `(method, route.path)`; the completeness guard also enumerates `app.routes`.
- Fact: `Backend/requirements-backend.txt` pins `fastapi==0.141.1` / `starlette==1.7.0`. In that version `app.routes` holds 5 framework/health routes and **17 `_IncludedRouter` objects**, one per `include_router`
  (my script printed `total 9` flat routes). `_IncludedRouter.matches()` returns `(Match, {})`: it never says which route matched. A flat list needs the private `effective_candidates()` / `effective_route_contexts()` API.
  Nothing in the design mentions this; a literal implementation sees no `APIRoute` at all, so either the gate denies everything (fail closed, found at once) or an implementer reimplements matching by hand and diverges from the router.
- Failure scenario: a hand-written matcher disagrees with the router on a corner (partial-match order, redirect_slashes, `low_priority` routes in this FastAPI) and a dev-only route answers on prod; or the guard test passes vacuously on an empty list.
- Change: do not match in a middleware. Use a `route_class=StageRoute` (an `APIRoute` subclass whose `get_route_handler()` returns a 404 handler when `is_prod()` and the policy for `(method, route.path)` is not `reader`; a route with no policy entry raises at import on prod). I verified in a scratch app on 0.141.1 that this tombstones
  `/api/images/flagged` (404, also for `%66lagged`) while `/api/images/{image_id}` still serves, and that docs/openapi are off with `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`. Matching is then done by the router itself, so the fall-through question disappears by construction
  and there is no second matcher to drift. For the completeness guard iterate the public `router.routes` of each imported router (flat `APIRoute`, path = router prefix + path, prepend `/api`), not `app.routes`. Keep unmounting the pure-admin routers as a second layer.

**H2. Nothing prevents talking to the wrong environment (P2, P3, P4, P6 connections; P5 roles)**
- Design: one variable name per role (`PROD_SYNC_DATABASE_URL`, `PROD_PULL_DATABASE_URL`), one `PROD_SSH_HOST`, a hand-opened tunnel, and **cluster-wide role names shared by the three databases** (P5 "Roles are cluster-wide, so the three databases share role names with per-database grants").
  Every precondition in P2 phase 0 is a schema/version equality check (alembic head, Postgres major, extension versions, taxonomy version); all three environments pass all of them against each other. No spec reads `current_database()`, an environment marker or the gate's identity (grep over P2/P3/P4/P6: none).
- Scenario: shell still holds the general tunnel on local port 15432 from yesterday, or the alias points at the wrong host; the owner runs `python -m sync.promote --env metal --apply`. Preconditions pass, the "files present" gate passes (the report file is metal's own), 18,087 metal rows (new UUIDs, no conflict) are loaded into `general`'s serving database; its BASE_PATH has none of those files, so the public site serves broken images. Reversal needs a retire pass over the guard. Same for `sync.pull` mirroring the wrong environment's activity over dev (guarded only against an empty source).
- Change: see "Connection configuration: recommendation". Minimum: per-environment role names or per-environment clusters, an environment marker on the server checked on every connect, per-environment key and local port, and `environment` in the capabilities payload.

**H3. Unauthenticated write amplification with no quota, on one disk shared by all three environments (P5d Caddy; decisions 6, 11)**
- Code facts: `GET /api/images` writes a `search_history` row for every call including the unfiltered browse feed (`image_service.py:148-169`, the "browse feed is not an impression" comment applies to impressions only), one `search_history_tags` row per facet value, and up to `limit` (max 100) `meme_impressions` rows
  for any `q` or facet. `q` is `Optional[str]` with no `max_length`, `facets` has no cap on count or length, `record=false` is caller-controlled. Saves, not-found and ratings have **no limiter at all**; the only limiters are `upload_limiter` and `bug_report_limiter`, in-memory per worker, buckets keyed by IP and never evicted.
  Caddy's core has no rate limiting (needs a plugin, which the design lists as a risk and defers to "the app", which has none). Decision 11 forbids pruning. Docker's `json-file` log driver has no rotation by default.
- Scenario: one host loops `GET /api/images?q=<64 KB>&limit=100`. Each request stores 64 KB of text plus 100 impression rows; at 50 req/s that is several GB/day into `search_history` and `meme_impressions`, on the same disk as the DB volume, the image files, the dumps and the container logs. Disk full stops Postgres for all three environments; the nightly dump is the "only copy" of reader data and lives on that disk.
- Change: (1) an app-side limiter on the recording paths (impressions, history rows, saves, not-found, ratings): when over limit skip the write and serve the response (recording is already best effort, so this costs nothing); cap `q` (e.g. 512 chars), facet count and value length at the route; evict empty buckets. (2) Role-level limits in `sync.roles`: `ALTER ROLE backend_app SET statement_timeout`, `idle_in_transaction_session_timeout`, `CONNECTION LIMIT`. (3) Caddy: `request_body { max_size }`, header size limit; decide plugin vs CDN (owner question) rather than leaving it a risk. (4) Compose `logging` options with `max-size`/`max-file`. (5) Put DB volume and file tree on separate quotas or at least alert on free space (P6 S4 checks only the gate's disk). (6) Revisit decision 11 for `meme_impressions` only (aggregate or cap), or state the growth ceiling the owner accepts. (7) P4 needs a growth tripwire (see M4).

**H4. Bug-report upload: memory and disk abuse (decision 13; `bug_reports.py`)**
- Fact: `data = await file.read()` loads the whole body, then checks `len(data) > 10 MB`; Starlette spools the multipart body to a temp file first, so an arbitrary-size request reaches disk and memory before the 422. 20 requests per minute per IP per worker (two workers, three environments, any number of IPv6 addresses) of 10 MB each is about 200 MB/min per source into `BUG_REPORTS_PATH`, with no total quota. The files are only removed after a verified pull (P4).
- Change: `request_body max_size 11MB` for `/api/bug-reports` in Caddy; read in chunks and stop at the cap; refuse (507) when the bug-reports directory exceeds a file-count or byte cap; keep the directory on a quota-limited volume.

**H5. Grants are not re-applied after a migration; best-effort writes hide the failure (P5d deploy order; roles)**
- Design: roles SQL is generated from the registry and run once at provisioning; the deploy order (steps 1 to 4) migrates as `memes_owner`, restarts the backend, then promotes. No default privileges are specified for future tables.
- Scenario: a release adds table `foo` that a reader route selects. Migration creates it owned by `memes_owner`; `backend_app` has no grant. Step 3 restarts the backend; every request touching `foo` returns 500 until someone re-runs `sync.roles`. If the new table is class P, the failure is silent: recording is wrapped in savepoints and swallowed (`impression_recorder.py`, `outcome_recorder.py`, `history_recorder.py` log a type name only), so reader activity is lost with a warning line.
- Change: add a step 2b "apply `sync.roles` SQL (idempotent) for each database, then check it" before step 3; make the migrate container run it (or `ALTER DEFAULT PRIVILEGES` only where it cannot widen: SELECT on D tables, never for P writes or X). P6 should add a staleness alarm for the P tables (no new row for N hours while the site has traffic) and S1 should run before the restart in the release runbook. The design's grants table also omits `SELECT` on the six P tables for `backend_app` (UPDATE/DELETE with a WHERE and `ON CONFLICT` need it) and does not say that `INSERT` into `meme_text_ratings` needs no `SELECT` on `images` (the FK check runs as the owner of `images`).

**H6. Postgres reachability and authentication are described inconsistently (P5d stack, roles)**
- (a) "Postgres on 127.0.0.1:5432 inside the compose network and **not published**" and "the sync reaches Postgres through `ssh -L` as the `memesync` user": sshd runs on the host, so `-L 127.0.0.1:5432` from the host finds nothing unless the port is published on the host loopback (`127.0.0.1:PORT:5432`). The design must say publish on loopback only.
- (b) `memes_owner` "login only from the VPS itself" cannot be enforced by source address: every connection through a published port or the compose network reaches Postgres from a Docker bridge address. Only `pg_hba.conf` entries by role and database (plus `reject` lines) can enforce it. The official image ships `local ... trust` and `127.0.0.1/32 trust` (recalled), so anyone able to `docker exec` is superuser, and a switch to host networking or a host Postgres (offered as "a small compose change") turns `127.0.0.1` into trust for every local process.
- (c) PostgreSQL grants `CONNECT` on every database to `PUBLIC` by default, and shared cluster roles exist in all three databases: `backend_app`'s password is valid against `it`, `metal` and `general` alike; one leaked `.env.it.prod` (the least-watched environment) opens the other two as the same role, and a mis-set `DATABASE_URL` silently makes the metal backend serve the general database.
- Change: ship a tracked `pg_hba.conf` (scram only, `reject` for `memes_owner` on `host` lines, one line per role/database/subnet, a fixed compose subnet), `REVOKE CONNECT ON DATABASE ... FROM PUBLIC` then grant per role, per-environment role names (or per-environment clusters, option A), and say owner DDL runs via `docker exec` over the socket. Postgres 18 image detail (recalled): data directory moved to `/var/lib/postgresql/18/docker`, so mount the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data`; set `shm_size` (the 64 MB Docker default breaks parallel index builds).

### Medium

**M1. CORS and OPTIONS ordering relative to the gate (P5a item 2).** The gate keys on `(method, path)`; a CORS preflight is `OPTIONS` with a path, absent from the table, so a gate outside `CORSMiddleware` answers 404 and cross-origin PUT/POST (ratings, saves, not-found) break in browsers; plain `OPTIONS` currently returns 405 with `Allow`. Specify: gate inside CORS, preflight handled by CORS, and with the `route_class` approach the problem does not arise. Also `main.py:132-133` hard-codes `http://localhost` and `http://127.0.0.1` with `allow_credentials=True`; P5's "localhost origins dropped on prod" needs a code change that is not in the task list. Probe on the real app: preflight from `http://localhost` is allowed (200), from another origin 400.

**M2. Pool sizes cannot be set by an overlay (P5d "Database sizing", T4).** `Storage/db.py:23-24` hard-codes `pool_size=10, max_overflow=20`; no setting is read. The "5 + 5 per worker" arithmetic (60 connections) needs a code change (read `settings.get("database.pool_size", 10)` etc., dev unchanged) and a task for it. `Dockerfile.backend` CMD still says `-w 4`; compose must override it. With 4 workers x 30 x 3 environments the shared `max_connections=100` would be exhausted by one environment (option B).

**M3. Information leaks through logs and public endpoints (S5 7.2; P5d Caddy).**
- Referer: the SPA keeps search text in the page URL (`SearchPage` uses `useSearchParams`). If the SPA and `/api` share a host (the design serves the build from the same Caddy), the browser sends the full page URL as `Referer` on every API call; Caddy's default access log records request headers, so the query-string filter the design adds is bypassed. Delete `Referer` from the log or send `Referrer-Policy: no-referrer`.
- `uvicorn.access` inside the container prints the request line with the query string; the design does not confirm it is off (S5 left it as an uncertainty). The compose file must pass `--access-logfile` off and `--log-level warning` or `access_log=False`, and cap the log size.
- `GET /health` returns `Database unavailable: {exc}` (`main.py:171`): driver text with host, user or database name on a public route; return a fixed detail on prod. It also runs `SELECT 1` per request; fine behind a limit.
- `capabilities` publishes the git SHA and schema head; harmless for convenience but it tells an attacker exactly which build to target. Prefer returning them only on loopback or reading them from a DB marker that `sync_reader` can see.
- `GET /api/diagnostics/statistics` serves the dev snapshot, which includes pipeline backlog numbers (`pending`, `rejected`, `flagged`, `duplicate_clusters`, missing-embeddings counts). Decide that disclosure or filter the payload on prod.

**M4. Pulling untrusted prod text into dev (P4; tools; evaluation).**
- Web UI: React escapes; I found no `dangerouslySetInnerHTML` or `innerHTML` in `Frontend/memes-frontend/src`. Good.
- `tools/discovery_report.py` prints raw query text to the terminal (ANSI/OSC sequences from a public author reach the owner's terminal) and writes CSV with `csv.writer` and no formula guard (`=`, `+`, `-`, `@` cells). Before the epic only the owner's own text was in these tables; now it is anyone's. Strip control characters and prefix formula cells.
- `meme_text_ratings` are public writes (`PUT /api/meme-from-text/ratings` accepts any text, any active image id, any grade; the feature being off does not disable the route). The scheduled `export_ratings` job on `general` turns them into gold-set judgment files and `import_judgments` merges them into the working tree weekly. Anonymous ratings can poison the evaluation set; the "person reviews before commit" step is the only barrier. Either make ratings dev-only on prod while meme-from-text is off, or mark pulled ratings as untrusted and exclude them from the export unless a flag is set. The same files are read by agents (prompt injection through `candidate-queries.json`).
- Size: the mirror pull has a drop tripwire (50 percent) but no growth tripwire or byte cap; a flood (H3) makes every pull copy gigabytes into the dev database. Add a row/byte ceiling per pull that needs `--allow-growth`.
- Foreign keys and sequences: only `meme_text_ratings.image_id` has an FK (skipped when unknown on dev, per P4); `meme_saves`, `meme_impressions` and `search_not_found` carry no FK, so a mirror cannot fail on them; `setval(max(id))` is covered. Ids from prod cannot collide because dev activity is replaced. I found no mechanism by which prod data can execute on dev beyond the points above.

**M5. File ownership and container hardening (P5 risk list; P3).** The backend image runs as uid 1000 (`appuser`); `memesync` is a different user. (a) Bug reports: written by uid 1000 into a directory `memesync` must `delete-verified`; with default umask the directory is not writable by `memesync`, so P4's delete step fails. Use a shared gid, `2775` setgid directory, container user `1000:<gid>`. (b) Mount `BASE_PATH` read-only into the backend (it writes nothing there on prod: uploads and recut are gated, bug reports go elsewhere). (c) Compose should set `read_only: true`, `tmpfs: /tmp` (multipart spool), `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, memory and CPU limits; the image chowns all of `/app` to `appuser` today, so a compromised process can rewrite the code it serves. (d) A guard test: every `ports:` entry other than Caddy's is prefixed `127.0.0.1:` (Docker publishes bypass ufw, recalled).

**M6. The gate and keys do not know which environment they serve (P3, P5d).** P3 puts paths in a single gate; there are three BASE_PATHs and three Postgres ports. Specify one key per environment in `authorized_keys`: `restrict,port-forwarding,permitopen="127.0.0.1:<that env's port>",command="/opt/memes/memesync-gate.sh <env>"`, so a key can only forward to its own database and can only run the gate with its own environment fixed. Without `permitopen` the `ssh -L` the sync needs lets that key reach any loopback service on the VPS (including the other environments' databases and the Docker API if it were exposed). `restrict` also disables port forwarding unless re-enabled, so the line above is required, not optional.

**M7. Reader-facing gaps against the verdicts (P5b, P5c, decisions 5, 14).**
- Android has a Trends screen (`getTrends*` in `MemeApiService.kt`: `trends/dates`, `runs/latest`, `runs/{id}`); trends are `dev_only` (decision 5) and P5c's hide list omits Trends (it lists "History", which the app does not call). Add Trends, and hide the web `/reply` entry when `features.memeFromText` is false (a dead page otherwise).
- Decision 14 ships built comics (`comics`, `comics_panels`) but every route that reads them (`/api/comics/*`) is `dev_only` and unmounted, so these rows serve no reader. Either allow `GET /api/comics/by-image/{id}` and `GET /api/comics/{id}` as read-only reader routes (they are GET) or stop shipping them. `capabilities.features.comics` is ambiguous until this is settled.
- P5b says the web build "reads capabilities so a prod backend with a feature switched on later shows it without a rebuild", but the build flag hides components statically, so a capability can only match or further reduce what the build shows; state which wins.

**M8. Removing a setting by overlay is not possible (P5d Configuration).** Dynaconf merges dicts and appends lists (S5 says so for `scheduler.jobs`); "`parse.model` removed for prod general and metal" cannot be done by a later file. Set `parse.model: ""` (`load_config` treats falsy as absent, verified in `parse/config.py`) and add a prod-overlay test that `get_query_parser()` is `None`. Also `settings.py` builds `settings` at import from `APP_ENV` (default `general`): the compose env files must always set `APP_ENV` explicitly or a missing line runs the general overlay against another database.

**M9. Rate-limit keying behind the proxy (P5d "Proxy headers").** `--forwarded-allow-ips` takes IPs or CIDRs, not "the Caddy container"; container addresses change. Give the compose network a fixed subnet and pass its CIDR (gunicorn forwards `forwarded_allow_ips` to the uvicorn worker, recalled). Caddy must send `X-Forwarded-Proto`, otherwise Starlette's trailing-slash 307 (`Location: http://...`, observed in a probe: `/api/images/flagged/` and `/api/admin/batches/names/` redirect) downgrades to http. Prefer `redirect_slashes=False` on prod.

### Low

- L1. `.gitignore` has `.env.*` with no negation, so the planned `deploy/prod/env/.env.<env>.prod.example` files would be silently ignored; name them `<env>.prod.env.example` or add `!*.example`. `.dockerignore` patterns `.env.*` are root-level only; use `**/.env*` in case a stray file exists under `Storage/`.
- L2. `deploy/prod/sql/roles.sql` is listed as tracked and "generated"; it must never contain passwords (use psql variables or `\password`); the tracked-file test should scan for secret-shaped text.
- L3. `GET /api/images/{id}` serves files with `mimetypes` content types and `Access-Control-Allow-Origin: *`; add `X-Content-Type-Options: nosniff` and a sandboxing CSP on that route in Caddy.
- L4. The statistics route on prod: when the stored payload fails `StatisticsResponse(**payload)` (a deploy added a field) the current code falls through to recompute and write; the read-only mode must define that case (404 or a stale-marked response), not only "missing row".
- L5. Backup: the dump of the six P tables, the DB volume and the bug reports share the VPS disk; the "copy off the VPS" happens only when dev pulls. Provider snapshots are called "a bonus"; for the only copy of reader data an unattended off-host copy (object storage push) is cheap. Restore of the dump needs the `images` rows (FK of ratings) to exist first; the P7 recovery runbook should say "promote, then restore".
- L6. Image distribution is not specified (registry, or `docker save | ssh docker load`), nor tagging by revision instead of `latest`; `Dockerfile.migrate` needs `Storage/alembic` and the same pinned psycopg2.
- L7. Creating `vector` (and `pg_trgm`) needs the right to create extensions: `vector` is not necessarily a trusted extension (recalled, check the control file); the owner role may need to be superuser during provisioning only, or the extension created by the bootstrap superuser. Specify.

---

## Production shape: options A, B, C compared

Option A: one compose file, parameterised, **run as three compose projects** (`-p memes-general --env-file ...`), each with its own Postgres container and volume, backend and a one-shot migrate job; Caddy outside the projects (host package or one more project proxying to the loopback ports). This is dev's shape (three clusters on 5432/5434/5436).
Option B: the current design (one project, one cluster, three databases). Option C: one compose file and project, three Postgres services.

RAM figures are my estimates (unmeasured): backend worker about 200 MB RSS (Python, SQLAlchemy, pymorphy dictionaries), 2 workers x 3 environments; Postgres idle overhead about 90 MB per cluster plus `shared_buffers`; OS, dockerd, sshd about 0.5 GB. Sizes from S2: DBs 2.4 / 1.1 / 0.06 GB, files 3.3 / 1.9 / 0.12 GB.

| Criterion | A: three projects | B: one cluster, three DBs | C: one project, three Postgres |
|---|---|---|---|
| Blast radius of a runaway maintenance (1 GB `maintenance_work_mem` HNSW build, bad promote, lock pile-up) | Contained: per-env `mem_limit`/`cpus`; a lock or vacuum storm stays in one cluster | Shared memory, shared WAL, shared autovacuum workers, shared lock manager; cgroup limit is per cluster only | Same containment as A (per-service limits) |
| OOM kill of one Postgres backend | Postmaster restarts that cluster only | **Postmaster crash-restart drops every connection of all three environments** (recalled) | Per cluster |
| Roles and leaks | Roles per cluster, passwords per cluster; a leaked `.env.it.prod` is worthless elsewhere; same role names are fine because the port/key/password differ | Roles are cluster-wide: one `backend_app` password valid on three databases; `CONNECT` to PUBLIC; wrong `DATABASE_URL` silently works unless role names are made per environment | Like A |
| `max_connections` / pools | Per cluster (e.g. 30 each); no cross-environment starvation | One budget of 100: 3 x 2 x (5+5) = 60 plus sync tools; one environment under attack can starve the others unless `ALTER DATABASE ... CONNECTION LIMIT` is set | Like A |
| Independent deploys | `docker compose -p memes-it up -d` is its own lifecycle; `it` is a natural canary | One backend per env already independent, but the DB and `down -v` are shared | Single `up` touches everything; `down -v` removes all volumes |
| Restore one environment | Replace one volume or snapshot; others untouched | Volume snapshot is cluster-wide; per-env restore only via logical `pg_dump`/`pg_restore` | Like A |
| Major upgrade (P2 requires equal majors dev vs prod) | One environment at a time (59 MB `it` first), dev clusters upgrade independently | All three at once or the epic's precondition fails for two of them | Like A |
| Backups and the tools | Same script looped over three ports; per-env marker is physical | One dump job; one place to forget a database | Like A |
| Wrong-environment mistake | Hardest: different loopback port, key (with `permitopen`), password and local tunnel port per environment, all mechanical | Easiest: same port and key, only the database name differs | Like A for ports/passwords, shares the compose project |
| RAM floor (estimate) | about 2.9 GB steady, 3.9 GB during a general HNSW rebuild; run on 4 GB, prefer 8 GB | about 2.6 GB steady, about 3.6 GB during rebuild; 4 GB | like A, about 2.9 GB |
| Disk | identical data; +3 x ~0.2 GB of container metadata and WAL slack | identical | like A |
| Operational simplicity (one owner) | Three of the same thing driven by one parameterised file; more `docker` processes; Caddy lives outside | Least moving parts, one Postgres to learn, one tuning file | One file, three DBs, most `docker ps` lines |
| Tunnel and connection config | Three fixed remote ports (dev port + 1000: 6432, 6434, 6436), three local ports, key per env | One remote port; env only by db name and role | Three remote ports like A |

Recommendation: **A, implemented as one parameterised `deploy/prod/compose.yaml` run three times**, Caddy as a host package (or a `network_mode: host` container) proxying to the loopback backend ports so no cross-project networks are needed. Strongest reason: it makes the damage of a wrong-environment command, a crashed or OOM-killed Postgres, a leaked credential and a major upgrade a one-environment problem, at a cost of roughly 0.3 GB of RAM, which a VPS that must hold 3.5 GB of data and do 1 GB index builds needs to exceed anyway. Choose B only if the VPS has less than 4 GB RAM; then make role names per environment, revoke `CONNECT` from PUBLIC, set per-database connection limits, and accept that the postmaster is a single point of failure. C buys little over A and loses the lifecycle isolation. Decision 7 (one cluster) was taken before the roles, OOM and upgrade consequences were visible; it is worth reopening with the owner.

---

## Connection configuration: recommendation

The owner's concern is justified: environment-less variable names plus a hand-opened tunnel are the mechanism of H2. Replace them with a per-environment file the tool reads, a tunnel the tool owns, and an identity check that fails closed.

1. **File.** `environments/.env.<env>.sync`, gitignored by the existing `.env.*` rule and excluded from the image by `.dockerignore`. Loaded by a new `config.settings.load_sync_env(env)` modelled on `load_env`: explicit `env`, never ambient. Keys (no URLs, so a stale shell variable cannot be mistaken for one): `SYNC_FOR_ENV=general`, `PROD_SSH_HOST=memes-prod-general`, `PROD_DB_REMOTE_PORT=6434`, `PROD_DB_LOCAL_PORT=16434`, `PROD_DB_NAME=memes_general`, `PROD_SYNC_USER`, `PROD_SYNC_PASSWORD`, `PROD_PULL_USER`, `PROD_PULL_PASSWORD`, `PROD_API_URL`. Rules: refuse if `SYNC_FOR_ENV` differs from `--env`; ignore and warn about any `PROD_*` already in `os.environ`; never print or log values; on POSIX refuse mode wider than 600. Keep the file's permissions tight on Windows and add the path to the agent deny list (agents must never read it, same rule as `DATABASE_URL`).
2. **Tunnel owned by the tool.** A context manager spawns `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> -o ExitOnForwardFailure=yes -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o StrictHostKeyChecking=yes <alias>` (full path to Windows OpenSSH as P3 already requires, stdin `DEVNULL`, new process group), polls the local port until it accepts, and always kills the process in `finally`. One tunnel per run (Windows OpenSSH has no ControlMaster, as the S3 ADR records). **Fixed local port per environment** (16432 / 16434 / 16436), so two tools can run side by side without a clash and a tunnel to the wrong environment is detectable by port. The alias lives in `~/.ssh/config` (`HostName`, `User memesync`, `IdentityFile` one key per environment, `IdentitiesOnly yes`); P3's file transport reuses the same alias. Reverse the "tool does not spawn ssh in v1" line of P2.
3. **Credentials.** Prefer libpq's `pgpass.conf` entries `127.0.0.1:16434:memes_general:sync_writer:...` over passwords in the sync file, so a wrong local port has no password and fails at authentication; the file then holds only names and ports.
4. **Handshake before any other statement (all tools, including `verify`).** On connect run one query and require all of: `current_database() = PROD_DB_NAME`; `current_user` is the expected sync role (and not owner, backend or superuser, as P2/P4 already require); `current_setting('memes.environment', true) = SYNC_FOR_ENV` where the marker is set once at provisioning by `ALTER DATABASE memes_general SET memes.environment = 'general'` (a database-level setting readable by any role, no table, no registry entry, no schema change); the gate `handshake` verb returns the same environment (its identity comes from the forced command in `authorized_keys`, M6); `GET capabilities` returns `environment == SYNC_FOR_ENV` (add the field). For a non-empty prod add a content cross-check: at least a threshold of prod's image ids exist in dev's `images` for this environment; for an empty prod (first seed) the marker is the guard. Any mismatch exits 2 before reading or writing a row. Log environment, database and role names, never hosts or passwords.
5. **Server side.** One `authorized_keys` line per environment: `restrict,port-forwarding,permitopen="127.0.0.1:6434",command="/opt/memes/memesync-gate.sh general" ssh-ed25519 ...`.

How it interacts with the shapes: with **A** all five levers differ per environment (remote port, key, password, pg cluster, marker), so the handshake is the last of several mechanical barriers; with **C** the same, with ports published from one project; with **B** only the database name, the marker and (if made per environment) role names differ, there is one remote port and `permitopen` cannot distinguish databases, so the marker and the handshake carry the whole load and per-environment role names become mandatory.

---

## Questions only the owner can answer

Owner-only:
1. VPS size and provider: is 4 GB RAM acceptable (A or C), or must it be smaller (then B)? Disk size and whether the provider offers block-level snapshots or volumes with quotas.
2. Public exposure controls: a CDN or WAF in front (free tier) for rate limiting and DDoS absorption, or a custom Caddy build with a rate-limit plugin, or app-level limits only? Is basic auth or an IP allow-list acceptable for the day abuse starts?
3. Decision 11 against abuse: may `meme_impressions` and `search_history` have a ceiling or aggregation on prod, or does "keep everything" win even over disk exhaustion?
4. Should the public ratings routes stay open while meme-from-text is off (M4 gold-set poisoning), and should pulled ratings feed the evaluation exports?
5. Comics on prod: serve read-only comics routes, or stop shipping `comics`/`comics_panels` (M7)?
6. Statistics page on prod: is it acceptable to publish dev pipeline backlog numbers?
7. Off-VPS copy of the reader data: provider snapshot, object storage push, or only the dev pull?
8. Hostnames and domains; whether the SPA and API share a host (Referer, CORS, nosniff headers depend on it); whether Caddy is a host package or a container.
9. Where backend images live (registry account or `docker save` over ssh) and whether a short maintenance window is acceptable for a non-additive migration.
10. Is the Android Trends screen to be hidden on prod, or trends served read-only after all?
11. Where the sync credentials may live on the workstation (file with ACL, OS credential store) given that agents run on the same machine.

An implementer would otherwise guess (not owner-only):
- Exact `pg_hba.conf`, compose subnet, role names per environment, who may `CREATE EXTENSION vector`; PG18 volume path and `shm_size`.
- uid/gid scheme between `memesync` and the container user; which paths are `:ro`.
- The `StageRoute` mechanism, the policy file format, what happens to a method not in the table (HEAD, OPTIONS), and whether the unmounted routers' paths still appear in the policy test.
- The pool/worker settings hook in `Storage/db.py` and which settings keys carry them.
- Log suppression per layer (uvicorn access log, Caddy fields, Docker log driver rotation).
- What the read-only statistics route returns for an unparseable stored payload.
- Which environment the gate script belongs to when one VPS hosts three (per-key forced command argument).
- Capability schema field names and whether `revision` is public.
- The release runbook step that re-applies grants (H5) and the P6 alarm for stalled reader-activity tables.

---

## What I tried to break and could not

- **Route inventory.** S5's 90 / 31 / 59 matches the code to the route; no unlisted router, `Mount`, WebSocket or static-file route exists (`grep` and the enumeration agree). Framework routes are the only extras and the design already covers `/docs` and `/openapi.json`.
- **Fall-through from unmounted routers.** The prefixes of the seven pure-admin routers (`/admin`, `/ingestion`, `/recut`, `/comics`, `/meme-from-text/profiles`, `/admin/discovery`, `/admin/profile-coverage`) are not shadowed by any reader pattern, so unmounting them cannot reach `/api/images/{image_id}`. The real fall-through hazard is only inside the images router (`/images/flagged`, `/untagged`, `/no-ocr`, `/duplicates*`, `/trending`), which stay mounted; the tombstone approach in H1 handles them.
- **Path tricks against the real app (TestClient, no DB):** `//api/images/flagged` 404; `/api//images/flagged` 404; `/API/images/flagged` 404 (case sensitive); `%2F` and `%66` decode to the same path the router uses (so a gate keyed on the decoded path agrees with routing; a request that matches runs the handler, hence the 500 without a DB); `HEAD`, `OPTIONS` and wrong-method requests on API routes give 405 with `Allow`; trailing slash gives a 307 to the slashless URL (then subject to the gate). Caddy's matcher is case-insensitive, which can only over-block relative to the case-sensitive backend (recalled).
- **Triggers and functions.** No `CREATE TRIGGER`, function, view, materialized view, rule, RLS or `SECURITY DEFINER` in Storage; only referential-integrity triggers exist, so the only "trigger path" is the FK cascade.
- **Cascade without DELETE.** My recollection of the PostgreSQL source is that referential actions run as the owner of the referencing table, which supports the design's claim that `sync_writer` needs no `DELETE` on `meme_text_ratings`. The converse follows: `sync_writer` can destroy class P rows indirectly via `DELETE FROM images`, which the pull-first rule already addresses. Not run here; P5d-T2 should confirm.
- **`MAINTAIN` on PostgreSQL 18.** Recalled: the privilege (PG 17+) covers `VACUUM`, `ANALYZE`, `REINDEX` (including `CONCURRENTLY`), `CLUSTER`, `LOCK`; the design's use is consistent. Residual doubt: maintenance operations by a non-owner run with a restricted `search_path`; check that no index uses an expression calling an extension function (not checked here).
- **Sequences.** The only P table with a serial key is `meme_impressions` (bigserial); the other five use client-side UUIDs, so `USAGE` on one sequence is enough for `backend_app`. The D tables with integer ids are the concepts tables, as the registry says.
- **Stored XSS in the dev UI.** No raw HTML sink in the React sources; discovery and history text render as text. (The terminal and CSV sinks in M4 are real.)
- **Statistics with the grants.** The read-only route works with `SELECT` only; the only writer of `statistics_snapshots` becomes `sync_writer`; no other reader route writes a non-P table that I could find (impressions, history, saves, not-found, ratings are the only writes).
- **Scheduler on prod.** `scheduler.enabled: false` in a later overlay does override the earlier `true`, and `_load_job_configs` returns nothing when disabled; the lifespan double-check is sound.
- **Additive migration between old and new image.** Holds only if "additive" also means new columns are nullable or have server defaults and indexes on live tables are built `CONCURRENTLY`; the design says additive but not that.
