# Observability: metrics and logs for the production rollout

Status: draft

Tracker: `tracker.md` (same folder). Builds on `docs/superpowers/specs/2026-11-12-environment-data-sync/` (the prod stack in `deploy/prod/`, the stage policy, the ssh tunnel).

## 1. Intent

The three production environments (metal, general, it) will run on one Linux VPS. Today nothing tells the owner how they behave: there is no metrics code at all, and the logs live only in Docker's own store. The owner wants to open Grafana and see how fast things are, how much they are used, and whether the host and database are healthy; and wants the logs to be complete enough to explain an incident, kept on volumes of their own.

## 2. Goals, non-goals, anti-goals

Goals
- **Performance:** latency percentiles (p50, p95, p99) per route; search query performance (total and per stage); image serving time; database pool waiting.
- **Usage:** backend call counts per route, image-served counts, page-visit counts for the web app and the Android app.
- **Health:** host disk, memory and CPU; Postgres connections and size; Caddy request and error counts; rate-limiter rejections.
- **Logs:** audit the code and add missing log lines; prod writes rotating log files to dedicated volumes with a hard size bound.
- History survives the workstation being off (Prometheus runs on the VPS); dashboards are opened from the workstation.

Non-goals (this epic)
- **Business metrics.** Deferred on purpose; they need a separate grilling session (tracker, decision 2). No metric in this epic may be named or labelled as one.
- Alerting and Alertmanager, log aggregation (Loki or similar), distributed tracing, OpenTelemetry.
- Scraping the three dev backends (the instrumentation also runs on dev, but no dev Prometheus is set up).
- Touching the 51 `print`-based batch scripts (they run on dev only).

Anti-goals (a failure even if it works)
- Any search text, query string, image id, client address, header or body in a metric label, a metric value or a log line. A feature that leaks one is rejected.
- Anything new reachable from the internet except the one beacon route. Prometheus, the exporters and Grafana never are.
- A log flood or a label explosion that fills the disk or the VPS memory.

## 3. Constraints

- The VPS has a 4 GB floor for three environments, so the whole addition must stay near 400 MB resident: Prometheus about 150, the host exporter and three `postgres_exporter` instances about 80 together, and three metrics sidecars capped at 64 MB each (section 5.2). If measurement on the real VPS shows more, the first thing to drop is the per-environment sidecar in favour of one shared exporter.
- Prod containers have read-only root filesystems, dropped capabilities and fixed memory limits; the new ones follow the same `x-hardening` anchor.
- Gunicorn runs **two workers** per backend: counters must be aggregated across processes (prometheus_client multiprocess mode).
- Prod stage default deny: a new route needs one `ROUTE_POLICY` line (`CLAUDE.md`). The metrics endpoint is deliberately **not** a route of the public app.
- Dev initiates everything over ssh; the VPS never connects to the workstation.
- Search text is never logged and never appears in an error body (existing rule, `CLAUDE.md`).
- Windows dev workstation: the dev Grafana stack must run under Docker Desktop and must not use ports in `environments/Environments.md`.

## 4. Approaches considered

Metrics collection:
- **A (chosen):** instrument the backend with `prometheus_client`, plus Caddy's built-in metrics, `postgres_exporter` and a host exporter. True percentiles and internals (pool, search stages); about 250 MB.
- B: derive from logs and Caddy only. No app code, but no percentiles from inside the backend and no search stages. Rejected.
- C: OpenTelemetry. Heaviest, adds nothing the first scope needs. Rejected.

Where Prometheus and Grafana run:
- 1: both on the VPS. Rejected: 400 to 600 MB and a second internet-facing surface.
- **2 (chosen, owner picked):** Prometheus on the VPS, Grafana on the workstation over the ssh tunnel. The VPS keeps the history, nothing new is exposed.
- 3: both on the workstation. Rejected: gaps whenever the workstation is off.

Page visits:
- 1: derive from the capabilities call. Rejected: cannot tell which page.
- 2: Caddy counters only. Rejected: first load only, nothing from Android.
- **3 (chosen, owner picked):** a counters-only beacon from both clients.

Logs: keep stdout and Docker's store as they are, and **add** rotating files on per-environment volumes (chosen). Replacing stdout was rejected: `docker logs` must keep working.

## 5. Design: metrics

### 5.1 In-app instrumentation (`Backend/app/observability/`)
A small package, all of it behind the setting `observability.metrics.enabled` (true in `settings.prod.yaml`, false by default, so dev and tests behave as today).

- **Request middleware** (pure ASGI, outermost after the body limit and client-context middlewares; the builder places it so rejected requests are counted too). Records after the response starts:
  - `memes_http_requests_total{route, method, status_class}` (status_class is `2xx`..`5xx`)
  - `memes_http_request_duration_seconds{route, method}` histogram, buckets 0.005 to 10 s
  - `memes_http_requests_in_flight` gauge (multiprocess mode `livesum`)
  - `route` is the **route template** (`/api/images/{image_id}`), read from `scope["route"].path` after routing; a request with no matching route, or the prod 404 of a dev-only route, is labelled `unmatched`. Never the raw path. This bounds cardinality to the 91 routes plus one.
- **Image serving:** `memes_image_served_total{status_class}`, `memes_image_bytes_total` and `memes_image_read_seconds` histogram (time to open and read the file), recorded in the image endpoint. No image id label.
- **Search:** for the two search endpoints (`/api/images` search and `POST /api/meme-from-text/search`), `memes_search_duration_seconds{kind, stage}` with `kind` in `smart|clip|meme_from_text` and `stage` in `total|parse|embed|retrieve` (the stages that exist today in the pipeline; the builder maps them), and `memes_search_degraded_total{reason}` using the existing degraded reasons. The query itself is never an input to any metric.
- **Database pool:** `memes_db_pool_checked_out`, `memes_db_pool_overflow` gauges and `memes_db_pool_wait_seconds` histogram (time to get a connection), from SQLAlchemy pool events in `Storage/db.py`'s existing pool hook. Query-level timing comes from `pg_stat_statements`-free exporter metrics, not from labels.
- **Limiter:** `memes_rate_limited_total{scope}` (scope in the four limiter names plus `body_limit`), incremented where `abuse.py` already rejects.
- **Build:** `memes_build_info{git_sha, stage}` gauge set to 1.
- **Page visits:** `memes_pageview_total{client, page}`. `client` is `web|android|other`; `page` must be one of a fixed allow-list of route templates kept in one shared constant (the web router paths and the Android destinations); any other value is stored as `other`.

### 5.2 Multiprocess and the exporter
- `PROMETHEUS_MULTIPROC_DIR=/metrics-mp`, a **named volume** `metrics_mp` (not tmpfs, because the sidecar must share it). A gunicorn config file (`deploy/prod/gunicorn.conf.py`, copied into the image) empties the directory in `on_starting` and calls `multiprocess.mark_process_dead(pid)` in `child_exit`. The compose `command` gains `-c`.
- A **sidecar service `metrics`** per environment: the same backend image, command `python -m Backend.app.observability.exporter`, which serves `MultiProcessCollector` on port 9102. It mounts `metrics_mp` read-only, runs hardened, `mem_limit` 64 MB, on the `edge` network with alias `metrics-<env>`, **no published port, no Caddy route**. (If the builder finds the volume sharing unreliable, the fallback is a `/metrics` route on a second gunicorn bind inside the backend container; flag it in the plan.)

### 5.3 Other scrape targets
- **Caddy:** the `metrics` global option plus a dedicated internal site (`:9180 { metrics }`) reachable only on the edge network. The Caddy admin API is **not** exposed.
- **postgres_exporter**, one instance per environment, on that environment's `internal` network and the `edge` network, connecting over the unix socket or the internal network with a **monitoring role** (new `memes_<env>_mon`, `pg_monitor` only). The role is created by `provision.sh` and checked by `sync.release verify-grants`; it must not appear in the sync registry's table grants.
- **node_exporter** for the host (disk, memory, CPU, load), read-only host mounts, only the collectors needed.
- Image digests are pinned the same way as the existing images.

### 5.4 Prometheus on the VPS
- A new compose project `memes-obs` (`deploy/prod/obs/compose.yaml`), joined to `memes-edge`, hardened, 200 MB limit.
- `prometheus.yml` with scrape interval 15 s, targets by alias, `retention.time: 30d` and `retention.size: 2GB`, `--web.listen-address=127.0.0.1:9090` published on the **host loopback only**.
- Reached from the workstation by the existing ssh pattern: a new local tunnel port **19090** forwarding to the VPS loopback 9090, using the tunnel key (`permitopen` gains the new target). The sync tool does not manage it; a small script (`python -m sync.obs_tunnel` or a documented `ssh -L`) opens it while Grafana is used. The builder picks the form and records the port in `environments/Environments.md`.

### 5.5 Grafana on the workstation
- `deploy/observability/` (dev side): a compose file for Grafana only (pinned image, a port outside `Environments.md`), provisioned datasource (`http://host.docker.internal:19090`) and three dashboards committed as JSON: **Performance** (latency percentiles per route, search stages, image serving, pool wait), **Usage** (calls per route, images served, page visits by client and page), **Health** (host disk and memory, Postgres connections and database size, Caddy errors, rate-limit rejections, log-volume use). Each has an `env` selector (the three environments).
- No Grafana credentials in the repo: the admin password comes from an untracked env file.

### 5.6 The beacon
- `POST /api/telemetry/pageview`, body `{ "client": "web|android", "page": "<route template>" }`, at most 1 KB, answers 204. `ROUTE_POLICY` marks it `READER` and it uses `make_router`. It increments the counter and **writes nothing else** (no database row, no log line with the body). A new limiter scope `telemetry` (setting `abuse.telemetry_per_min`, prod value chosen by the builder, default generous) bounds a single client; an over-limit request answers 429 and is counted in `memes_rate_limited_total`.
- Shared JSON schema in `shared/schemas/`, the three generated trees regenerated, `backend_api.md` updated.
- Web: a hook on route change in `src/app/router.tsx` that sends the beacon fire-and-forget (`navigator.sendBeacon` when available, else `fetch` with `keepalive`), a no-op when `VITE_STAGE` is not prod or the capability says telemetry is off. Android: the same on destination change in `NavGraph.kt`. Neither client sends any identifier, query or id.

## 6. Design: logs

### 6.1 Audit (done first, as a report)
A subagent audits, read-only, every backend module and the sync tools against a checklist and writes a short table to `docs/superpowers/reviews/`: what is logged, what is missing, whether a line could leak private data. The checklist: startup and shutdown with stage, workers and pool bounds; unhandled exceptions (type and route, no message); slow requests; database pool timeouts and connection errors; missing image file for a known id; rate-limit and body-limit rejections (sampled); bug-report receipt (size, no content); capability or policy failures; the scheduler and batch spawns (dev); and for `sync.*` the gate and release steps.

### 6.2 What is added (builder decides the exact lines from the audit)
- **A request id:** generated by the server (never taken from the client), put on the ASGI scope, added to every log line of that request, and returned as `X-Request-ID`. Caddy's access log records it too.
- **Slow-request log:** one warning line `route, status, duration_ms, request_id` above `observability.slow_request_ms` (prod 2000). Never the path with its query string.
- **Gaps the audit finds** in the checklist above.
- A **test that no log line contains private text**: a request with a distinctive search string, on a stack that fails on purpose, and an assertion that the string is absent from captured logs and from `/metrics`.

### 6.3 Volumes and rotation (prod)
- One host directory per environment, `/srv/memes/<env>/logs/{backend,postgres}`, and `/srv/memes/edge/logs/caddy`, created by `setup-host.sh` with the same owner and group pattern as `backups/` and `bug-reports/` (root-owned parent, the service writes only its own subdirectory, no symlinks followed).
- **Backend:** in addition to stdout, a `RotatingFileHandler` to `/var/log/memes/backend.log` (bind mount of the directory), `observability.log_file.max_bytes` 20 MB, `backup_count` 5, so at most about 120 MB per environment. With two workers a plain rotating file is unsafe; the builder uses one file per worker pid (`backend-<pid>.log`) or a `QueueHandler` listener, and records the choice.
- **Caddy:** a second log output `file` with `roll_size 10MiB`, `roll_keep 5`, the **same filter** as the existing log (no `uri`, `headers`, `tls`), plus the request id.
- **Postgres:** `logging_collector = on`, `log_directory`, `log_rotation_size = 20MB`, `log_truncate_on_rotation`, a bounded file count; log `checkpoints`, `lock_waits`, `temp_files`, `autovacuum` durations. **Never** `log_min_duration_statement`, `log_statement`, `log_connections` with parameters, and set `log_min_error_statement = panic` and `log_error_verbosity = terse`: the default would write the failing statement **and its bind parameters** (the searched text) to the log. A container test proves it (a failing parameterised query leaves its parameter out of the log).
- The existing Docker `json-file` options stay.
- `memes-facts.py` adds the log directories' sizes to `disk.json`; `sync.verify` check S4/S8 (the builder finds the right one) warns when a log directory exceeds 80% of its bound.

## 7. Tests and verification
- Unit: route-template labelling (including `unmatched`, a dev-only route on prod, a 404 scan with random paths producing one series), the page allow-list (`other`), the label sets never contain an id or a query, the request-id generator ignores a client header.
- Multiprocess: two real processes increment counters and the exporter sums them; the dead-process hook leaves no stale gauge.
- Container (skipped without docker, like the existing prod tests): the sidecar serves `/metrics` and the public app does not (404 through Caddy); the beacon is counted; Prometheus scrapes the three target kinds; the Postgres log contains no bind parameter; the Caddy file log contains no query string.
- The existing suites stay green: `Backend/tests`, `tests/sync`, `tests/docs`, `tests/integration/sync`, frontend `tsc -b`, `eslint --max-warnings 0`, `vitest run`, Android unit tests for the beacon.
- Docs: `docs/observability.md` (what each metric means, how to open the tunnel and Grafana, log locations and bounds), `deploy/prod/README.md` additions, `environments/Environments.md` (tunnel port, Grafana port), `backend_api.md`, CLAUDE.md pointer.

## 8. Delivery plan (shape only)
- O1 audit report (read-only).
- O2 backend instrumentation, exporter, gunicorn config.
- O3 beacon, schema, web and Android senders.
- O4 logging changes from the audit, request id, slow-request line, rotating file handler.
- O5 prod stack: `obs` compose, sidecar, exporters, Caddy metrics, monitoring role, log volumes, host script and facts.
- O6 Grafana (dev side), tunnel, dashboards.
- O7 docs, the cross-cutting privacy tests, whole-branch review.

## 9. Left to the builder
Exact bucket boundaries, the stage names inside the search pipeline, the rotating-file mechanism under two workers, the form of the tunnel helper, port numbers outside `Environments.md`, the telemetry limiter value, the dashboard layouts, the exporter image versions (pinned by digest), the `x-hardening` details of the new services.

## 9a. Calls made after the builder check (marked as the writer's calls, not the owner's)

1. **Tunnel key:** a fourth, dedicated key and account `memesync-obs`, with `restrict,port-forwarding,permitopen="127.0.0.1:9090",command="/bin/false"`, its own line in `/etc/ssh/memes-keys/`, added to `authorized_keys.tunnel.template`'s sibling and to `setup-host.sh` and `AllowUsers`. It is not coupled to any environment's sync key or its rotation. Agents get no prod credentials, so only the committed templates and the container ssh test (`ssh_container_checks.sh`) can be verified here.
2. **Proof of "no private text":** layered. (a) A config-lint unit test that runs without Docker: `postgresql.conf` must not enable `log_statement`, `log_min_duration_statement`, `log_connections`, and must set `log_min_error_statement = panic` and `log_error_verbosity = terse`; the Caddyfile file log must carry the same filter as the stdout log. (b) Non-Docker unit tests that capture the backend's log records and `/metrics` output for a request with a distinctive string. (c) Container tests (skipped without Docker, like the existing prod tests) that prove the Postgres and Caddy files for real. (a) and (b) are required in the normal run; (c) is run by the owner and by the whole-branch review and its result is quoted in the report.
3. **Backend log bound:** per environment, about 100 MB in total. One file per worker **slot** (`backend-w0.log`, `backend-w1.log`, the slot taken from a counter in gunicorn's `post_fork`, so restarts reuse the same names and stale files cannot accumulate), each `RotatingFileHandler` at 10 MB with 4 backups, plus `backend-master.log` at 5 MB x 2. The metrics sidecar logs to stdout only.
4. **Beacon trust:** the limiter keys on the client address held in memory only, exactly like the existing four (`abuse.py` token buckets, IPv6 as /64); the address never reaches a label or a log line. Page counts are **advisory**: a spammer with many addresses could inflate them, and that is accepted (the label set is bounded, so it cannot hurt anything else). No same-origin check.
5. **No new capability field.** The web sender keys on `VITE_STAGE=prod` only; Android sends on every build and stops for the rest of the session when the beacon answers 404 (an older backend or a dev-only stage). The delivery plan does not touch the capabilities schema.
6. **postgres_exporter** connects over the unix socket: it mounts the existing `pgsocket` volume read-only and runs as a uid that `pg_hba.conf` maps (one added peer line for `memes_<env>_mon`, rendered by `provision.sh`). No password secret. The role `memes_<env>_mon` (`pg_monitor` only) is created by `provision.sh`; `sync.release verify-grants` learns it as an expected role outside the registry and fails if it holds anything beyond `pg_monitor` and `CONNECT`.
7. **Postgres log directory:** `/srv/memes/<env>/logs/postgres` is owned by the postgres uid of the image (`PG_UID`, default 999, the same override the data directory already uses), created by `setup-host.sh`; backend and caddy directories keep their own owners. Nothing in the tree is a symlink and the gate does not read these directories.

## 10. Open items needing the owner
- Business metrics (deferred).
- Real hostnames and VPS size (the 4 GB floor assumption is carried from the sync epic).
- Whether Prometheus retention of 30 days and 2 GB is enough.
