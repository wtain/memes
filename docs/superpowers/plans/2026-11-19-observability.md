# Observability (metrics and logs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prometheus (on the VPS) and Grafana (on the workstation) with performance and usage metrics for the three prod environments, and complete, bounded, private logs on dedicated volumes.

**Architecture:** The backend is instrumented with `prometheus_client` in multiprocess mode behind one setting; a per-environment sidecar serves the aggregated `/metrics` on the internal edge network only; Prometheus (project `memes-obs`) scrapes sidecars, Caddy, `postgres_exporter` and a host exporter; Grafana runs on the workstation through an ssh tunnel on its own key. Logs keep stdout and add size-bounded files on per-environment host directories.

**Tech Stack:** Python 3.11, FastAPI/ASGI, gunicorn+uvicorn workers, `prometheus_client`, Docker Compose, Prometheus, Grafana, `postgres_exporter`, `node_exporter`, Caddy 2.10, React/TypeScript (Vite), Kotlin (Android), bash host scripts.

**Spec:** `docs/superpowers/specs/2026-11-19-observability/2026-11-19-observability-design.md` (section 9a holds the calls made after the builder check). Tracker: `docs/superpowers/specs/2026-11-19-observability/tracker.md`.

## Global Constraints

- Business metrics are out of scope: no metric or label may be a business metric.
- A search text, query string, image id, client address, header or body never appears in a metric label or value, nor in a log line. Route labels are route templates only; anything unmatched is `unmatched`; unknown pages are `other`.
- All metric code is behind setting `observability.metrics.enabled` (default false; true in `environments/settings.prod.yaml`). Disabled means no `prometheus_client` import cost on the request path and unchanged behaviour (existing suites stay green).
- Prod: nothing new is reachable from the internet except `POST /api/telemetry/pageview`. The metrics sidecar has no published port and no Caddy route; Prometheus is published on the host loopback `127.0.0.1:9090` only; the Caddy admin API is not exposed.
- New prod containers follow the `x-hardening` anchor (read-only root, `cap_drop: [ALL]`, `no-new-privileges`, memory/cpu/pids limits, json-file logs 10m x 3); images pinned by digest.
- Memory budget about 400 MB for the addition; sidecar `mem_limit` 64m.
- Log bounds: backend per worker slot `backend-w0.log`/`backend-w1.log` 10 MB x 4 backups, `backend-master.log` 5 MB x 2 (about 100 MB per environment); Caddy file log `roll_size 10MiB`, `roll_keep 5`; Postgres `log_rotation_size = 20MB`. Prometheus `retention.time 30d`, `retention.size 2GB`, scrape interval 15 s.
- Postgres log settings: never `log_statement`, `log_min_duration_statement`, `log_connections`; set `log_min_error_statement = panic` and `log_error_verbosity = terse`.
- New prod route needs a `ROUTE_POLICY` line and `make_router`; new shared schema means regenerating the three trees (CLAUDE.md "Type generation"); `backend_api.md` stays in sync; `docs/sync-registry.md` and `deploy/prod/sql/roles.<env>.sql` are generated, never hand-edited.
- Agents never get prod credentials, `environments/.env.*`, the three live backends/DBs or their ports. Tests only on `ocrdb_test`, one pytest process at a time against it. Run the Backend, `tests/sync`, `tests/docs`, `tests/integration/sync` and frontend roots as separate pytest/vitest invocations (CLAUDE.md gotchas).
- Do not merge to main without the owner's confirmation.

## Review Focus

Each line has its test in the task named in brackets.

1. A scan of random 404 paths must produce one `unmatched` series, not one per path. [O2]
2. Beacon bodies that are huge, non-JSON, wrong content type, empty, or carry unknown `client`/`page` (unicode, 10 KB strings) answer 4xx/204 without error and never create a new label value. [O4a]
3. A backend started with a log directory that is missing, full or read-only still starts and logs to stdout. [O5]
4. A distinctive search string sent through success, 422, 404 and 500 paths never appears in captured logs, `/metrics` output or the Postgres/Caddy log files. [O5, O6c, O6b]
5. A worker killed mid-request leaves no stale in-flight gauge, and the sidecar keeps serving. [O2]
6. Prometheus or the sidecar being down never changes an API response. [O2]

## File Structure

- `Backend/app/observability/__init__.py`: `enabled() -> bool`, re-exports.
- `Backend/app/observability/metrics.py`: every metric definition and the label allow-lists (one place).
- `Backend/app/observability/middleware.py`: `MetricsMiddleware`, `RequestIdMiddleware`, `SlowRequestMiddleware` (ASGI).
- `Backend/app/observability/exporter.py`: the sidecar app, `python -m Backend.app.observability.exporter`.
- `Backend/app/observability/logfiles.py`: bounded rotating file handlers, never raises.
- `Backend/app/api/telemetry.py`: the beacon.
- `shared/schemas/pageviewbeacon.schema.json` (+ generated trees).
- `deploy/prod/gunicorn.conf.py`, `deploy/prod/compose.yaml` (modify), `deploy/prod/obs/{compose.yaml,prometheus.yml}` (new), `deploy/prod/edge/{compose.yaml,Caddyfile}` (modify), `deploy/prod/postgres/{postgresql.conf,pg_hba.conf.template,provision.sh}` (modify), `deploy/prod/host/{setup-host.sh,memes-facts.py}`, `deploy/prod/sshd/authorized_keys.obs.template` (new), `sync/release.py` (modify, mon role check).
- `deploy/observability/{compose.yaml,provisioning/**,dashboards/*.json,README.md}`, `sync/obs_tunnel.py` (dev side).
- `docs/observability.md`, `docs/superpowers/reviews/2026-11-19-logging-audit.md`, `docs/superpowers/reviews/2026-11-19-observability-review.md`.

---

### Task O1: Logging audit (read-only report)

**Files:**
- Create: `docs/superpowers/reviews/2026-11-19-logging-audit.md`

**Interfaces:**
- Produces: a table the later logging task (O5) works from: module, what is logged today, missing items per the design 6.1 checklist, `leak risk yes/no` with the line.

- [ ] **Step 1:** Dispatch one read-only agent (no database access at all) over `Backend/app/**`, `sync/**`, `deploy/prod/memesync-gate.py`, `deploy/prod/host/*`, `retrieval/`, `parse/`, `Storage/db.py`. Checklist: startup/shutdown with stage, workers and pool bounds; unhandled exception handler; slow requests; pool timeouts and connection errors; missing image file for a known id; rate-limit and body-limit rejections; bug-report receipt; policy/capability failures; scheduler and batch spawns; gate and release steps.
- [ ] **Step 2:** For every existing log call, flag any argument that can carry request text (`q`, message, body, profile text, exception messages from validation errors).
- [ ] **Step 3:** Save the report; commit `docs(observability): logging audit`.

---

### Task O2: Metrics core (middleware, exporter, multiprocess)

**Files:**
- Create: `Backend/app/observability/{__init__,metrics,middleware,exporter}.py`, `Backend/tests/test_observability_core.py`
- Modify: `Backend/app/main.py` (add middleware after `ClientContextMiddleware`, i.e. outermost last; the request is counted even when the body limit rejects it), `environments/settings.yaml` (`observability.metrics.enabled: false`), `environments/settings.prod.yaml` (`true`), `Backend/requirements-backend.txt` (pin `prometheus_client`, full closure)

**Interfaces:**
- Produces: `observability.enabled() -> bool`; in `metrics.py`: `HTTP_REQUESTS (Counter, labels route, method, status_class)`, `HTTP_DURATION (Histogram, labels route, method; buckets 0.005,0.01,0.025,0.05,0.1,0.25,0.5,1,2.5,5,10)`, `HTTP_IN_FLIGHT (Gauge, multiprocess_mode livesum)`, `BUILD_INFO (Gauge, git_sha, stage)`, `route_label(scope) -> str`, `status_class(code:int) -> str`; `MetricsMiddleware(app)`; `exporter.app` (ASGI, `MultiProcessCollector` over `PROMETHEUS_MULTIPROC_DIR`, port 9102 via `python -m`).
- `route_label` returns `scope["route"].path` when matched, else `"unmatched"`; a prod dev-only 404 is `unmatched`.

- [ ] **Step 1: Write failing tests** `test_route_label_is_template_and_unmatched_is_one_series` (500 random 404 paths -> exactly one extra series), `test_status_class_and_methods_bounded` (unknown method labelled `OTHER`), `test_disabled_adds_no_middleware_and_no_import` (setting false: `/metrics` objects not created, responses identical), `test_metrics_failure_never_changes_a_response` (patch the counter to raise: response still 200), `test_two_processes_sum_in_exporter` (multiprocessing, two processes increment, exporter output sums; kill one, `mark_process_dead` leaves no in-flight residue), `test_no_query_or_id_in_any_label` (request `/api/images?q=SECRET-TEXT-42` and `/api/images/<uuid>`; exporter output contains neither).
- [ ] **Step 2:** Run `cd Backend && pytest tests/test_observability_core.py -v`; expect FAIL (module missing).
- [ ] **Step 3:** Implement per the Interfaces. Middleware is pure ASGI, wraps `send` to capture the status, observes in `finally`, all metric calls inside try/except that swallows (Review Focus 6). Metric objects are created lazily on first `enabled()` use. Exporter uses a fresh `CollectorRegistry` plus `MultiProcessCollector`.
- [ ] **Step 4:** Run the file, then the whole `Backend/tests` root (separate invocation); expect all pass.
- [ ] **Step 5:** Commit `feat(observability): request metrics, multiprocess exporter`.

---

### Task O3: Domain metrics (images, search, pool, limiter)

**Files:**
- Modify: `Backend/app/observability/metrics.py`, `Backend/app/api/images.py` (image file endpoint), `Backend/app/services/meme_from_text_service.py`, `Backend/app/api/images.py` search path, `Storage/db.py` (pool events), `Backend/app/services/abuse.py`
- Test: `Backend/tests/test_observability_domain.py`

**Interfaces:**
- Produces in `metrics.py`: `IMAGE_SERVED (Counter, status_class)`, `IMAGE_BYTES (Counter)`, `IMAGE_READ_SECONDS (Histogram)`, `SEARCH_SECONDS (Histogram, kind in {smart,clip,meme_from_text}, stage in {total,parse,embed,retrieve})`, `SEARCH_DEGRADED (Counter, reason)`, `POOL_CHECKED_OUT / POOL_OVERFLOW (Gauge, livesum)`, `POOL_WAIT_SECONDS (Histogram)`, `RATE_LIMITED (Counter, scope in {saves,not_found,bug_reports,recording,telemetry,body_limit})`; helper `record_search(kind, stage, seconds)`.
- Consumes: the meme-from-text pipeline already returns `meta` with `parseMs`, `embedMs`, `retrievalMs` and `degraded` reasons (use those, do not re-time).

- [ ] **Step 1: Write failing tests:** image endpoint increments `IMAGE_SERVED{2xx}` and bytes for a real tiny file and `4xx` for a missing id, with no id in labels; a meme-from-text search with a stub pipeline records the three stages and the degraded reason; a pool checkout wait observed (sqlite-free: use the test engine hook); `abuse.enforce` rejection increments `RATE_LIMITED{scope}`; the search string never appears in the exporter output; disabled setting records nothing.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement. Pool metrics via SQLAlchemy `checkout`/`checkin` pool events registered in the existing pool hook in `Storage/db.py` only when enabled. Search total is timed around the service call, per kind.
- [ ] **Step 4:** Run the new file and the full `Backend/tests`; pass. Run `tests/integration/` root once (shared `Storage/db.py` touched) with `DATABASE_URL` set per CLAUDE.md.
- [ ] **Step 5:** Commit `feat(observability): image, search, pool and limiter metrics`.

---

### Task O4a: Page-visit beacon (backend and schema)

**Files:**
- Create: `Backend/app/api/telemetry.py`, `shared/schemas/pageviewbeacon.schema.json`, `Backend/tests/test_telemetry_beacon.py`
- Modify: `Backend/app/main.py` (include router), `Backend/app/stage_policy.py` (`("POST", "/api/telemetry/pageview"): READER`), `Backend/app/services/abuse.py` (limiter `telemetry`, key `abuse.telemetry_per_min`), `environments/settings.yaml` (0 = off) and `settings.prod.yaml` (120), `Backend/app/observability/metrics.py` (`PAGEVIEWS Counter(client, page)`, `PAGE_ALLOWLIST` constant), `backend_api.md`, regenerated types (`./Frontend/generate-types.sh`, `./AndroidClient/scripts/generate_dtos.py`, the Python generator from `documents/generation.md`)

**Interfaces:**
- Produces: `POST /api/telemetry/pageview`, body `{"client": "web"|"android", "page": str}`, 204; `PAGE_ALLOWLIST` is the frozenset of web route templates (`/`, `/explore`, `/search`, `/reply`, `/memes/:id`, `/concepts`, `/concepts/:id`, `/upload`, `/trends`, `/recommendations`, `/statistics`, ... exactly the non-admin router paths) plus the Android destination names; the counter stores `other` for anything else and `other` client for an unknown client.

- [ ] **Step 1: Write failing tests:** 204 and counter increment for a valid beacon; unknown page -> `other`; 10 KB body -> 413; non-JSON, wrong content type, empty body -> 422 or 204 with no new label; unicode page -> `other`; over the limiter -> 429 and `RATE_LIMITED{telemetry}`; nothing written to the database or logged (caplog empty of the body); `test_stage_policy` guard passes with the new line; on dev the route behaves the same.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; body cap enforced by `BodyLimitMiddleware` extension for this path (1 KB), router from `make_router`. Regenerate the three trees; confirm `git diff` shows the generated files.
- [ ] **Step 4:** Run `Backend/tests`, `tests/sync` (policy/docs guards), `tests/docs`; pass.
- [ ] **Step 5:** Commit `feat(observability): page-visit beacon`.

### Task O4b: Web beacon sender

**Files:**
- Create: `Frontend/memes-frontend/src/telemetry/pageview.ts`, `.../pageview.test.ts`
- Modify: `Frontend/memes-frontend/src/app/AppLayout.tsx` (call on location change)

**Interfaces:**
- Produces: `sendPageview(routeTemplate: string, deps?: {fetch, sendBeacon, stage}): void`; `templateFor(pathname: string, matches): string` returning the matched route path template (never the concrete URL).
- Consumes: `VITE_STAGE` (via `src/vite-env.d.ts`), the router's matches.

- [ ] **Step 1: Write failing tests:** sends once per location change with `{client:"web", page:"/memes/:id"}` for `/memes/abc-123` (no id in the body); no call when `VITE_STAGE !== "prod"`; uses `sendBeacon` if present else `fetch` with `keepalive`; swallows a rejected fetch; StrictMode double mount sends once per navigation.
- [ ] **Step 2-4:** Run `vitest run src/telemetry`; implement; then `tsc -b`, `eslint src/ --max-warnings 0`, full `vitest run`.
- [ ] **Step 5:** Commit `feat(observability): web page-visit beacon`.

### Task O4c: Android beacon sender

**Files:**
- Create: `AndroidClient/app/src/main/java/com/memebrowser/app/data/telemetry/PageviewReporter.kt`, test `.../PageviewReporterTest.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/NavGraph.kt` (destination-change listener), the Retrofit/Ktor API interface that holds the generated DTO

**Interfaces:**
- Produces: `class PageviewReporter(api: TelemetryApi, scope: CoroutineScope) { fun report(destination: String) }`; stops sending for the session after one 404 response.
- Consumes: the generated `PageviewBeacon` DTO.

- [ ] **Step 1: Write failing tests:** sends `{client:"android", page:<destination name>}`; a 404 disables further sends; exceptions swallowed; destination arguments (ids) are stripped.
- [ ] **Step 2-4:** `./gradlew :app:testDebugUnitTest --no-daemon --tests "*PageviewReporterTest"` with `JAVA_HOME` set per CLAUDE.md; implement; run the whole unit suite.
- [ ] **Step 5:** Commit `feat(observability): android page-visit beacon`.

---

### Task O5: Logging: request id, slow requests, bounded files, audit gaps

**Files:**
- Create: `Backend/app/observability/logfiles.py`, `Backend/tests/test_logging_privacy.py`, `Backend/tests/test_logfiles.py`
- Modify: `Backend/app/observability/middleware.py` (`RequestIdMiddleware`, `SlowRequestMiddleware`), `Backend/app/main.py` (`_configure_logging` adds the file handlers and a request-id filter), `Backend/app/error_handlers.py`, modules named in the O1 report, `environments/settings.yaml` / `settings.prod.yaml` (`observability.slow_request_ms` 2000 prod, `observability.log_file.{dir,max_bytes,backup_count}`), `deploy/prod/gunicorn.conf.py` (create now: `post_fork` sets env `MEMES_LOG_SLOT`)

**Interfaces:**
- Produces: `logfiles.install(directory: str|None, slot: str, max_bytes: int, backup_count: int) -> list[logging.Handler]` (returns `[]` and logs one warning to stdout when the directory is missing, unwritable or full; never raises); `RequestIdMiddleware` sets `scope["memes.request_id"]` (server-generated uuid4 hex, any client `X-Request-ID` ignored) and the response header `X-Request-ID`; a `logging.Filter` adds `%(request_id)s` to the format; slow-request line `slow request route=<template> status=<n> duration_ms=<n> request_id=<id>`.

- [ ] **Step 1: Write failing tests:** `test_install_survives_missing_unwritable_and_full_directory` (Review Focus 3); `test_rotation_bounds_files_per_slot`; `test_request_id_is_server_generated_and_returned`; `test_slow_request_line_has_template_not_query`; `test_distinctive_search_text_absent_from_logs_and_metrics` over success, 422 (`/api/meme-from-text/search` bad body), 404 and a forced 500 (Review Focus 4); one test per gap from the O1 report (named after the report row).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; apply the O1 gaps with the type-only rule (exception class name, never its message). File names `backend-<slot>.log` and `backend-master.log` per the constraints.
- [ ] **Step 4:** Run the new tests, full `Backend/tests`, `tests/sync` + `tests/docs`.
- [ ] **Step 5:** Commit `feat(observability): request id, slow-request line, bounded log files, audit gaps`.

---

### Task O6a: Prod backend stack (gunicorn config, sidecar, volumes)

**Files:**
- Modify: `deploy/prod/gunicorn.conf.py` (`on_starting` empties `PROMETHEUS_MULTIPROC_DIR`; `child_exit` -> `multiprocess.mark_process_dead(worker.pid)`; `post_fork` slot counter), `deploy/prod/compose.yaml` (backend `-c`, env `PROMETHEUS_MULTIPROC_DIR=/metrics-mp`, volumes `metrics_mp:/metrics-mp` and `${LOGS_DIR}/backend:/var/log/memes`; new service `metrics`), `Dockerfile.backend` (copy `gunicorn.conf.py`), `.dockerignore` (re-include), `deploy/prod/env/*.prod.env.example` (`LOGS_DIR`), `tests/sync/test_prod_compose.py`, `deploy/prod/README.md`

**Interfaces:**
- Produces: service `metrics`: same image, command `python -m Backend.app.observability.exporter`, `mem_limit: 64m`, `metrics_mp` mounted read-only, network `edge` alias `metrics-${MEMES_ENV}`, no `ports`.

- [ ] **Step 1: Write failing tests** in `test_prod_compose.py`: `metrics` has no `ports`, hardened anchor, 64m, read-only volume, alias `metrics-<env>`; backend command contains `-c` the config; no service publishes 9102; log directory env is required.
- [ ] **Step 2-4:** Run `pytest tests/sync/test_prod_compose.py`; implement; `docker compose config` validation test (skip without docker).
- [ ] **Step 5:** Commit `feat(observability): prod metrics sidecar, gunicorn config, log mount`.

### Task O6b: Edge: Caddy metrics and file log; Prometheus project

**Files:**
- Modify: `deploy/prod/edge/Caddyfile` (global `metrics`, internal site `:9180 { metrics }`, second `log` output `file /var/log/caddy/access.log { roll_size 10MiB roll_keep 5 }` with the same filter plus request id, an `X-Request-ID` header passed through), `deploy/prod/edge/compose.yaml` (log volume), `tests/sync/test_prod_caddy_container.py`
- Create: `deploy/prod/obs/compose.yaml`, `deploy/prod/obs/prometheus.yml`, `tests/sync/test_prod_obs.py`

**Interfaces:**
- Produces: project `memes-obs` with services `prometheus` (digest-pinned, 200m, `--storage.tsdb.retention.time=30d --storage.tsdb.retention.size=2GB --web.listen-address=0.0.0.0:9090`, published `127.0.0.1:9090:9090`), `node-exporter`, three `postgres-exporter-<env>` (defined in O6c); `prometheus.yml` jobs `backend` (targets `metrics-metal:9102`, `metrics-general:9102`, `metrics-it:9102`, label `env`), `caddy` (`caddy:9180`), `postgres`, `node`, scrape 15 s.

- [ ] **Step 1: Write failing tests:** compose/yaml guards (loopback-only publish, pinned digests, no other `ports`, limits); `prometheus.yml` parses and names the four jobs; Caddy container test (skip without docker): `:9180` answers on the edge network and a request through the public site to `/metrics` is 404; the file log contains no query string, `Cookie`, `Authorization` for a request with a distinctive `?q=`.
- [ ] **Step 2-4:** Run; implement; validate with `caddy validate` and `promtool check config` on the pinned images (skip without docker).
- [ ] **Step 5:** Commit `feat(observability): caddy metrics and file log, prometheus project`.

### Task O6c: Postgres logging, monitoring role, exporter

**Files:**
- Modify: `deploy/prod/postgres/postgresql.conf`, `pg_hba.conf.template` (one peer line mapping the exporter uid to `memes_<env>_mon`), `provision.sh` (role `memes_<env>_mon` with `pg_monitor`, `CONNECT`; subcommand `grants` checks it), `sync/release.py` (`verify-grants`: expected extra role, exit 3 on any other privilege), `deploy/prod/obs/compose.yaml` (`postgres-exporter-<env>`, socket volume read-only), `tests/sync/test_prod_settings.py`, `tests/sync/test_release.py`, `tests/integration/sync/test_release_db.py`

**Interfaces:**
- Produces: `postgresql.conf` with `logging_collector = on`, `log_directory = '/var/log/postgresql'`, `log_filename`, `log_rotation_size = 20MB`, `log_truncate_on_rotation = on`, `log_min_error_statement = panic`, `log_error_verbosity = terse`, `log_checkpoints`, `log_lock_waits`, `log_temp_files`, `log_autovacuum_min_duration`; none of the forbidden settings.

- [ ] **Step 1: Write failing tests:** config lint (no forbidden key, required keys present); `test_mon_role_has_only_pg_monitor_and_connect` against the throwaway PG container (`test_release_db.py` pattern); `verify-grants` returns exit 3 when the role gets `SELECT` on a table; container test (skip without docker): a failing parameterised query with a distinctive value leaves the value out of the Postgres log files (Review Focus 4).
- [ ] **Step 2-4:** Run `tests/sync`, `tests/integration/sync` (one pytest process at a time); implement.
- [ ] **Step 5:** Commit `feat(observability): postgres log policy, monitoring role and exporter`.

### Task O6d: Host: log directories, obs ssh key, facts

**Files:**
- Create: `deploy/prod/sshd/authorized_keys.obs.template`
- Modify: `deploy/prod/host/setup-host.sh` (creates `/srv/memes/<env>/logs/{backend,postgres}` and `/srv/memes/edge/logs/caddy` with owners per the design 9a-7; user `memesync-obs`, `AllowUsers` merge keeps it; key line `restrict,port-forwarding,permitopen="127.0.0.1:9090",command="/bin/false"`), `deploy/prod/host/memes-facts.py` (log directory sizes into `disk.json`), `deploy/prod/memesync-gate.py` untouched, `sync/verify/` (warn at 80% of a bound; use the check id the O1-style survey of `sync/verify/runtime.py` shows for disk facts, record the id in the commit), `tests/sync/test_prod_host.py`, `tests/integration/sync/test_host_container.py`, `tests/integration/sync/ssh_container_checks.sh`

**Interfaces:**
- Produces: `disk.json` key `logs: {backend_bytes, postgres_bytes, caddy_bytes}` per environment; the verify check compares to the constants in the Global Constraints.

- [ ] **Step 1: Write failing tests:** template guard (single `permitopen`, no shell, `restrict`); `setup-host.sh` idempotence and `AllowUsers` merge keeps `memesync-obs`; facts include log sizes; verify warns above 80% and is silent below; container ssh test: the obs key can forward 9090 and nothing else, cannot get a shell (skip without docker).
- [ ] **Step 2-4:** Run; implement.
- [ ] **Step 5:** Commit `feat(observability): host log directories, obs tunnel key, log-size facts`.

---

### Task O7: Grafana on the workstation, tunnel helper, dashboards

**Files:**
- Create: `deploy/observability/compose.yaml`, `deploy/observability/provisioning/datasources/prometheus.yml`, `.../provisioning/dashboards/dashboards.yml`, `deploy/observability/dashboards/{performance,usage,health}.json`, `deploy/observability/README.md`, `sync/obs_tunnel.py`, `tests/sync/test_obs_tunnel.py`, `tests/docs/test_observability_dashboards.py`
- Modify: `environments/Environments.md` (tunnel port 19090, Grafana port; pick ports not in the table), `.gitignore` (`deploy/observability/.env`)

**Interfaces:**
- Produces: `python -m sync.obs_tunnel [--env-file] [--stop]` opening `ssh -N -L 19090:127.0.0.1:9090` with alias `memes-prod-obs` (full ssh path from `sync.ssh_bin`), a pid file, no credentials in argv; Grafana compose with a pinned image and the admin password read from the untracked `deploy/observability/.env`; datasource URL `http://host.docker.internal:19090`.
- Dashboards: Performance (p50/p95/p99 per route via `histogram_quantile`, search stages, image read seconds, pool wait), Usage (calls per route, images served, page visits by client and page), Health (host disk/memory, PG connections/size, Caddy 5xx, `memes_rate_limited_total`, log-volume use); variable `env` over `metal|general|it`.

- [ ] **Step 1: Write failing tests:** `test_obs_tunnel_command_has_no_secret_and_uses_alias`; dashboards JSON valid, every panel's expression references a metric name defined in `metrics.py` or a known exporter prefix (`node_`, `pg_`, `caddy_`), every dashboard has the `env` variable and no hard-coded host; port not present in `Environments.md` elsewhere.
- [ ] **Step 2-4:** Run `pytest tests/sync/test_obs_tunnel.py tests/docs/test_observability_dashboards.py`; implement. Validate the compose file with `docker compose config` (skip without docker). Do not start Grafana on a port from `Environments.md`.
- [ ] **Step 5:** Commit `feat(observability): dev-side Grafana, tunnel helper, dashboards`.

---

### Task O8: Docs, whole-branch review

**Files:**
- Create: `docs/observability.md`, `docs/superpowers/reviews/2026-11-19-observability-review.md`
- Modify: `deploy/prod/README.md` (new services, log volumes, memory budget, first-start order: edge network, obs project, host script), `CLAUDE.md` (pointer under the sync subsection and a Key invariant: no private text in metrics or logs), `backend_api.md` (done in O4a; verify), tracker and spec status (`implementation`; nothing `done`)

- [ ] **Step 1:** Write `docs/observability.md`: every metric (name, labels, meaning, which dashboard panel), how to open the tunnel and Grafana, log locations and bounds, what the page counts mean (advisory), the privacy rules, and a "verify on a real VPS" list (systemd, the obs key, Prometheus scrape, Caddy `:9180`, XFS/log bounds, the 400 MB budget).
- [ ] **Step 2:** Run the full set as separate commands: `Backend/tests`, `tests/sync` + `tests/docs`, `tests/integration/sync`, `tests/integration/` root (shared code touched), frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, Android unit suite. Record the counts.
- [ ] **Step 3:** One whole-branch review (three read-only reviewers in parallel: privacy/leaks and cardinality; prod hardening and compose; correctness of the metrics and dashboards), then one fix pass, then the summary required by CLAUDE.md (fixed / not fixed with reason / ignored).
- [ ] **Step 4:** Commit `docs(observability): operator docs, review report`. Ask the owner before merging to main.
