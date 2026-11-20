# Observability: metrics and logs

Design: `docs/superpowers/specs/2026-11-19-observability/2026-11-19-observability-design.md` (status line there). Prod stack details: `deploy/prod/README.md`. Dev-side Grafana: `deploy/observability/README.md`. Ports: `environments/Environments.md`.

Metrics exist only on prod. The setting `observability.metrics.enabled` is `false` in `environments/settings.yaml` (dev and tests: no metrics middleware, `prometheus_client` is not even imported) and `true` in `environments/settings.prod.yaml`. The beacon route and the log files follow their own settings (`abuse.telemetry_per_min`, `observability.log_file.dir`, `observability.slow_request_ms`).

```
backend (2 gunicorn workers, prometheus multiprocess files in volume metrics_mp)
   -> metrics sidecar  metrics-<env>:9102   (memes-edge network, no published port, no Caddy route)
Caddy  caddy:9180 (/metrics, internal site)     node-exporter:9100     postgres-exporter-<env>:9187
   -> Prometheus (project memes-obs, VPS loopback 127.0.0.1:9090, 15 s scrape, 30 d / 2 GB retention)
   -> ssh tunnel from the workstation (python -m sync.obs_tunnel, local port 19090)
   -> Grafana on the workstation (127.0.0.1:3300)
```

## Opening it

1. `python -m sync.obs_tunnel` opens `127.0.0.1:19090` to the VPS Prometheus through the ssh alias `memes-prod-obs` (the dedicated key of the account `memesync-obs`, which can forward to `127.0.0.1:9090` and nothing else). `python -m sync.obs_tunnel --stop` closes it. `--env` only picks the settings overlay that names the ssh binary; it reads no env file. The alias lives in your ssh config; no credential is in the repository.
2. In `deploy/observability/`: `cp .env.example .env`, set `GRAFANA_ADMIN_PASSWORD` (the file is git-ignored), `docker compose up -d`.
3. Open http://127.0.0.1:3300, user `admin`, the password from `deploy/observability/.env`. Three provisioned dashboards (read-only in the UI): Performance, Usage, Health. Each has an `Environment` selector (metal, general, it); host disk, host memory and Caddy panels are host-wide and ignore it.

Prometheus itself is never published beyond the VPS loopback, and the exporters and Grafana are never reachable from the internet. The only new public route is the page-visit beacon.

## Metrics of the backend

Names are the ones Prometheus sees after `prometheus_client` suffixing (counters end in `_total`; each histogram exposes `_bucket`, `_sum` and `_count`). Prometheus adds `env` (metal, general, it), `job` and `instance` to every series. Definitions: `Backend/app/observability/metrics.py`; dashboards: `deploy/observability/dashboards/*.json`.

| Metric | Type | Labels | Meaning | Dashboard panel |
|---|---|---|---|---|
| `http_requests_total` | counter | `route`, `method`, `status_class` | Requests by route template (with the mount prefix, `/api/images/{image_id}`; `unmatched` for no matching route and for the prod 404 of a dev-only route), method (`OTHER` for unusual verbs), status class `1xx`..`5xx` (`other`) | Usage: Calls per route, Calls by status class; Health: Backend 5xx rate |
| `http_request_duration_seconds` | histogram | `route`, `method` | Request duration, buckets 5 ms to 10 s | Performance: p50, p95, p99 latency per route |
| `http_requests_in_flight` | gauge (livesum) | none | Requests being served right now | Performance: Requests in flight |
| `build_info` | gauge (max) | `git_sha`, `stage` | Always 1; the running build | none (use Explore to see which sha runs) |
| `image_served_total` | counter | `status_class` | Image file responses | Usage: Images served |
| `image_served_bytes_total` | counter | none | Body bytes of image file responses. Reads 0 for a response sent through the ASGI `pathsend` extension, so treat it as a lower bound | Usage: Image bytes served |
| `image_read_seconds` | histogram | none | Time to send one image file response | Performance: Image read seconds (p50 / p95) |
| `search_seconds` | histogram | `kind` (`smart`, `clip`, `meme_from_text`), `stage` (`total`, `parse`, `embed`, `retrieve`) | Search duration. `total` is also observed when the search raised | Performance: Search p95 per kind and stage |
| `search_degraded_total` | counter | `reason` (`unavailable`, `timeout`, `invalid_output`, `breaker_open`, `embed_unavailable`, `empty_input`, `other`) | Meme-from-text searches answered degraded; a combined fallback counts each part once | Performance: Search degraded answers by reason |
| `db_pool_checked_out` | gauge (livesum) | none | Database connections checked out of the pool | Performance: DB pool checked out and overflow |
| `db_pool_overflow` | gauge (livesum) | none | Connections beyond `pool_size` | Performance: DB pool checked out and overflow |
| `db_pool_wait_seconds` | histogram | none | Time to get a connection from the pool (includes creating one when needed) | Performance: DB pool wait (p50 / p95) |
| `rate_limited_total` | counter | `scope` (`saves`, `not_found`, `bug_reports`, `recording`, `telemetry`, `body_limit`) | Requests refused by an abuse limit | Health: Rate-limited requests by scope |
| `pageviews_total` | counter | `client` (`web`, `android`, `other`), `page` (allowlist template or `other`) | Page visits reported by the clients | Usage: Page visits by client, by page, by client and page |

Metrics of the other targets used by the Health dashboard: `caddy_http_request_duration_seconds_count{code}` (Caddy 5xx rate, host-wide), `node_filesystem_avail_bytes` and `node_filesystem_size_bytes` (Host disk used), `node_memory_MemAvailable_bytes` and `node_memory_MemTotal_bytes` (Host memory available), `pg_stat_database_numbackends` (Postgres connections), `pg_database_size_bytes` (Postgres database size), and `up` (Scrape targets up).

There are no business metrics in this epic (deferred on purpose; see the tracker).

### What the page counts mean

`pageviews_total` is **advisory**. The clients send a fire-and-forget beacon (`POST /api/telemetry/pageview`, `{client, page}`, at most 1 KB, answers 204) when a page is shown. `page` must be one of the fixed templates in `PAGE_ALLOWLIST` (the web router templates such as `/`, `/search`, `/memes/:id`, and the Android destination names such as `search`, `detail`); anything else, and any other `client`, is counted under `other`. A visitor with many addresses can inflate the counts, which is accepted: the label set is bounded, so nothing else is harmed. The beacon is limited per client address by `abuse.telemetry_per_min` (120 on prod); the address is held in memory only. Visits without JavaScript, from blocked beacons, or from an older Android build are not counted. The web client sends only on a `VITE_STAGE=prod` build; Android stops sending for the rest of the session when the beacon answers 404.

## Privacy rules

No search text, query string, image id, client address, header or body ever appears in a metric label, a metric value or a log line written by the application. A new metric label must be a bounded enum or a route template. Concretely:

- `route` is the route template, never the raw path; `page`, `client`, `kind`, `stage`, `reason` and `scope` are fixed sets with an `other` bucket.
- The beacon increments a counter and writes nothing else (no database row, no log line with the body).
- Postgres: `log_statement`, `log_min_duration_statement` and `log_connections` are never set; `log_min_error_statement = panic` and `log_error_verbosity = terse` keep statement text and bind parameters out. **Known residual:** the primary error message of a rejected cast can still name a value (for example an invalid uuid). Clients must therefore bind typed parameters (`CAST(:x AS uuid)` on validated input) rather than let the database parse free text. Never add custom `postgres_exporter` queries over `pg_stat_activity` or `pg_stat_statements`.
- Caddy: the file log and the stdout log carry the same filter (no `uri`, no request headers, no `tls`, no response headers (`resp_headers`: an image's `Content-Disposition` carries its file name), and `remote_ip`, `remote_port` and `client_ip` deleted). **Known accepted exception:** Caddy logs the request `path`, which can contain an image id (`/api/images/<id>`, `/memes/<id>`). It is not a search text and was already logged before this epic; it stays in the rotated Caddy file (60 MiB bound). This is an owner-visible decision: collapsing ids in the logged path would need Caddy `path_regexp` rewriting and was left out.
- Layered tests keep this true: config lint of `postgresql.conf` and the Caddyfile (no Docker), unit tests that capture the backend log records and `/metrics` output for a request carrying a distinctive string, and container tests (skipped without Docker, run by the owner).

## Logs

| Log | Where on the VPS | Bound |
|---|---|---|
| Backend (in addition to stdout and `docker logs`) | `/srv/memes/<env>/logs/backend/`: `backend-w0.log`, `backend-w1.log` (one per gunicorn worker slot, so restarts reuse the names), `backend-master.log` | worker slot files 10 MiB x 4 backups each (x 2 slots), master 5 MiB x 2 backups: 115 MiB per environment |
| Caddy | `/srv/memes/edge/logs/caddy/access.log` | `roll_size` 10 MiB, `roll_keep` 5: 60 MiB |
| Postgres | `/srv/memes/<env>/logs/postgres/` | a new file per rotation (20 MiB or one day); the hourly `memes-prune-logs@<env>.timer` (`postgres/prune_logs.py`) keeps the newest 10 files and 200 MiB. Between two prune runs the directory can exceed the bound by what Postgres writes in the meantime |

The Docker `json-file` logs stay as they were (10 MB x 3), so `docker logs` keeps working. The metrics sidecar logs to stdout only.

**Request id.** Every request gets a server-generated id (a client-supplied `X-Request-ID` is ignored), put on every log line of that request and returned as the `X-Request-ID` response header. Caddy records the response header as `request_id` in its access log, so a Caddy line and a backend line can be joined. A request slower than `observability.slow_request_ms` (2000 on prod, 0 = off) logs one warning with route template, status, duration and request id, never the path or query.

**Facts and the warning.** `memes-facts` writes `logs.{backend,postgres,caddy}_bytes` into `disk.json`; `python -m sync.verify` check A3 warns when a log directory exceeds 80% of its bound (bounds 115 MiB, 200 MiB, 60 MiB, so the warning starts at 92, 160 and 48 MiB).

**Open owner decision.** Rotated logs sit at 80 to 100% of their bound in steady state by design (rotated backend slots, the Caddy ring and a pruned Postgres directory all fill up to the bound and stay there). The A3 80% warning will therefore fire persistently once the logs have filled, and then no longer signals a rotation failure. Options: keep the 80% rule and accept the noise, or raise `LOG_WARN_FRACTION` (`sync/verify/runtime.py`) to warn above 100% or 110% of the bound. The design text says 80%, so it was built as written; the owner has to decide.

There is no log-volume Grafana panel: log sizes are doctor facts (A3), not Prometheus metrics.

## Operating rules

- **Prometheus inside `memes-edge` is unauthenticated.** It listens on `0.0.0.0:9090` in its container and joins `memes-edge` to scrape the sidecars and Caddy, so every backend, sidecar and Caddy container of all three environments can query it. The admin and lifecycle APIs are off (no `--web.enable-admin-api`, no `--web.enable-lifecycle`): such a container can read metrics, not delete series or reload the config. Only the VPS loopback `127.0.0.1:9090` is published.
- **Restart the backend with compose, never with `SIGHUP`.** A HUP makes gunicorn start new workers while the old ones are still being reaped, so they take the next free log slots (`w2`, `w3`) and the 115 MiB bound per environment no longer holds.
- **Ad hoc Python in the backend container** must unset the multiprocess directory: `docker compose ... exec backend sh -c 'unset PROMETHEUS_MULTIPROC_DIR; python ...'`. Otherwise the process writes its own metric files into `metrics_mp`, and the sidecar keeps adding them until the next backend restart.
- **Not counted:** CORS preflights (`OPTIONS` answered by the CORS middleware, which sits outside the metrics middleware). The image byte counter reads 0 under the ASGI `pathsend` extension (see the table). The pool metrics belong to the engine's pool object; a `dispose()` would replace it without them (the app never disposes it while serving).
- **500 body:** an unhandled exception is answered by the request-id middleware with `500` and the JSON body `{"detail":"Internal Server Error"}` plus `X-Request-ID`; the log line carries the exception class, the route template and the request id, never the message. A client that disconnected while its body was read is logged at INFO (`client disconnected type=ClientDisconnect`) and gets no response.

## Verify on a real VPS

None of this could be exercised without a VPS (agents have no prod access). Check, once, after the first deployment:

1. **Order of first start:** the host script FIRST (`setup-host.sh <env> --admin-user NAME ...` per environment, `--obs-key FILE` once): it creates the `memes-edge` network and the log directories. A stack started before it gets a `logs/postgres` that Docker created as root 0755; Postgres (`logging_collector = on`) then exits FATAL, `db` is unhealthy and the backend does not start. Then the `memes-<env>` stacks (they create the `pgsocket` volumes the exporters mount as external), then the edge project, then the `memes-obs` project last (its exporters fail to start if the volumes do not exist yet). An existing host follows the checklist "Upgrading an existing host to observability" in `deploy/prod/README.md`.
2. **Role rollout:** `postgres/provision.sh grants <env>` run once per environment before the first release that contains the monitoring role `memes_<env>_mon` (otherwise `verify-grants` exits 3 with `missing role`); `provision.sh render` and a restart of `db` so the peer line and ident map take effect.
3. **systemd:** `memes-prune-logs@<env>.timer` (hourly), `memes-facts@<env>.timer`, `memes-sweep@<env>.timer` and `memes-backup@<env>.timer` are enabled and active (`systemctl list-timers`).
4. **Obs key:** the key was given with `setup-host.sh --obs-key`; `/etc/ssh/memes-keys/memesync-obs` exists and its line starts with `restrict,port-forwarding,permitopen="127.0.0.1:9090",command="/bin/false"`; the account is in `AllowUsers`; `python -m sync.obs_tunnel` connects and `curl 127.0.0.1:19090/-/ready` answers.
5. **Prometheus:** Status > Targets shows every target up: `backend` (3), `caddy` (1), `postgres` (3), `node` (1). The Health panel "Scrape targets up" is all 1.
6. **Caddy `:9180`:** `caddy_http_request_duration_seconds_count` has samples (the `metrics` option is really live on the pinned image), and the public hostnames answer 404 to `/metrics`.
7. **postgres-exporter really connects:** over the read-only `pgsocket` volume shared across compose projects, as `memes_<env>_mon`; the default collectors are live (`pg_stat_database_numbackends` and `pg_database_size_bytes` have samples). The tests do not exercise the cross-project volume sharing.
8. **node-exporter:** filesystem metrics (`node_filesystem_avail_bytes`) appear for the host disks, not only for the container.
9. **Log directories:** ownership and modes as in the `deploy/prod/README.md` volume table (backend owned by the backend uid 10001, group `memesync-<env>` read only, 2750, Postgres owned by the postgres uid 0700, Caddy root); files appear after the first request; the filesystem (XFS or ext4) has room for the bounds above; `du` of each directory stays within its bound.
10. **Memory budget:** the whole addition should stay near 400 MB resident (Prometheus about 150, node-exporter and three postgres-exporters about 80, three sidecars up to 64 MB each; the compose limits are higher than the expected use: Prometheus 200 MB, node-exporter and each postgres-exporter 48 MB, each sidecar 64 MB). Check `docker stats`. If it is more, drop the per-environment sidecar in favour of one shared exporter first.
11. **Multiprocess:** after a backend restart `/metrics-mp` is emptied and the counters of both workers are summed (`http_requests_total` grows when you use the site).
12. **Beacon:** open a page on the production web build; `pageviews_total{client="web"}` grows in Prometheus.
