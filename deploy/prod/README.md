# deploy/prod: the production VPS stacks

Design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p5-prod-provisioning-and-hardening-design.md`, section P5d. Nothing here holds a secret; filled env files and passwords live on the VPS only.

## Stack map

| Project | Files | Services |
|---|---|---|
| `memes-edge` | `edge/compose.yaml`, `edge/Caddyfile`, `env/edge.prod.env` | `caddy` (the only public ports, 80 and 443) |
| `memes-metal`, `memes-general`, `memes-it` | `compose.yaml`, `env/<env>.prod.env` | `db` (pgvector Postgres 18), `backend`, `metrics` (sidecar, port 9102, alias `metrics-<env>` on `memes-edge`), `migrate` (profile `ops`, a job) |
| `memes-obs` | `obs/compose.yaml`, `obs/prometheus.yml` | `prometheus` (published on the host loopback `127.0.0.1:9090` only), `node-exporter`, `postgres-exporter-metal`, `postgres-exporter-general`, `postgres-exporter-it` |

```
internet -> caddy (memes-edge network 172.30.0.0/24) -> backend alias api-<env>:8000 -> db (project network 172.31.<n>.0/24)
workstation -> ssh -L (host sshd) -> 127.0.0.1:<pg port> (published loopback port of db)
```

## Port and network table (fixed)

| Env | Compose project | Database | Postgres on host loopback | Workstation tunnel port | Backend alias on `memes-edge` | Project subnet |
|---|---|---|---|---|---|---|
| metal | `memes-metal` | `memes_metal` | `127.0.0.1:6432` | 16432 | `api-metal:8000` | `172.31.1.0/24` |
| general | `memes-general` | `memes_general` | `127.0.0.1:6434` | 16434 | `api-general:8000` | `172.31.2.0/24` |
| it | `memes-it` | `memes_it` | `127.0.0.1:6436` | 16436 | `api-it:8000` | `172.31.3.0/24` |

Only Caddy publishes beyond the loopback (Docker publishes bypass ufw). The backend publishes nothing; Postgres never joins `memes-edge`. `tests/sync/test_prod_compose.py` enforces both.

## Volumes and ownership

| Path (host) | Container | Owner |
|---|---|---|
| `/srv/memes/<env>/images` | backend `/data/images`, **read-only** | `memesync-<env>`, 755 (files 644) |
| `/srv/memes/<env>/bug-reports` | backend `/data/bug-reports`, read-write | uid 10001 (`SVC_UID`), group `memesync-<env>`, 2775 |
| `/srv/memes/<env>/pgdata` | db `/var/lib/postgresql` (PG18 layout; `PGDATA` is below it) | postgres uid |
| rendered Postgres config directory (`PGCONF_DIR`) | db `/etc/memes`, read-only | root |
| `/srv/memes/<env>/web` | caddy `/srv/memes/<env>/web`, read-only | `memesync-<env>` |
| `/srv/memes/<env>/logs/backend` (`LOGS_DIR`/backend) | backend `/var/log/memes`, read-write (log files, `observability.log_file.dir`) | root, group `memesync-<env>`, 2770 (setup-host.sh) |
| `/srv/memes/<env>/logs/postgres`, `/srv/memes/edge/logs/caddy` | db `/var/log/postgresql`; caddy file log | postgres uid, 0700 (`PG_UID`); root, 0755. `memes-prune-logs@<env>.timer` (hourly) runs `prune_logs.py` on the Postgres one. `memes-facts` reports `logs.{backend,postgres,caddy}_bytes` in `disk.json`; `sync.verify` A3 warns above 80% of 115 MiB / 200 MiB / 60 MiB |
| Grafana tunnel account `memesync-obs` | `setup-host.sh <env> --obs-key FILE`; key line `restrict,port-forwarding,permitopen="127.0.0.1:9090",command="/bin/false"` | root-owned key file `/etc/ssh/memes-keys/memesync-obs` |
| named volume `metrics_mp` | backend `/metrics-mp` (prometheus multiprocess files, emptied by the gunicorn master at start), metrics `/metrics-mp` read-only | docker (mode 1777 from the image) |
| named volume `pgsocket` | db and migrate `/var/run/postgresql` (unix socket) | docker |
| named volumes `caddy_data`, `caddy_config` | caddy `/data`, `/config` (certificates) | docker |

The backend runs as `10001:<gid of memesync-<env>>` (`SVC_GID`), read-only root filesystem, `/tmp` tmpfs 256 MB (`noexec,nosuid`), all capabilities dropped, `no-new-privileges`, memory/cpu/pids limits, json-file logs rotated at 10 MB x 3, two gunicorn workers (config `/app/gunicorn.conf.py` from `deploy/prod/gunicorn.conf.py`: per-worker log slots, prometheus multiprocess hooks), no access log, warning log level, `--forwarded-allow-ips 172.30.0.0/24`.

The `metrics` service (same image, `python -m Backend.app.observability.exporter`, 64 MB, read-only volume `metrics_mp`) serves `/metrics` on port 9102 as alias `metrics-<env>` on the shared `memes-edge` network only (not on the project network, so it cannot reach Postgres), where the Prometheus of the `memes-obs` project scrapes it. It publishes no port and has no Caddy route.

## Placeholder hostnames

The tracked overlays `environments/settings.<env>.prod.yaml` use `https://<env>.example.invalid` (the reserved `.invalid` TLD never resolves). The real hostnames go into two places in the same change: those overlays (`cors.origins`) and `env/edge.prod.env` (`METAL_HOST`, `GENERAL_HOST`, `IT_HOST`). Until then CORS refuses every browser origin.

## Pinned images

- Postgres: `pgvector/pgvector:pg18-trixie` pinned by digest in `compose.yaml` (plain `postgres:18` has no pgvector). Update: `docker pull pgvector/pgvector:pg18-trixie`, read `docker inspect --format '{{index .RepoDigests 0}}' pgvector/pgvector:pg18-trixie`, replace the digest, run `tests/sync/`. Pinned on 2026-11-16 at PostgreSQL 18.7.
- Caddy: `caddy:2.10-alpine` pinned by digest in `edge/compose.yaml` (v2.10.2). The Caddyfile was checked with
  `docker run --rm -v <path>/Caddyfile:/etc/caddy/Caddyfile:ro -e ACME_EMAIL=... -e METAL_HOST=... -e GENERAL_HOST=... -e IT_HOST=... caddy:2.10-alpine@sha256:... caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`
  and, on a loopback port with plain-http site addresses, for the log filter (no `uri`, no `headers`, `path` appended), the 404 prefixes and the security headers. Repeat both after any Caddy bump.
- Backend and migrate images: `memes-backend:<git sha>` and `memes-migrate:<git sha>`, built on the workstation, never `latest`, loaded with `docker save | gzip | ssh <admin> docker load`.

## First provisioning of an environment (P5d-T4)

```
# once per host
docker network create --subnet 172.30.0.0/24 memes-edge

# per environment <env>, on the VPS, from deploy/prod
cp env/<env>.prod.env.example env/<env>.prod.env        # fill in, chmod 600
postgres/provision.sh render <env> "$PGCONF_DIR"        # writes pg_hba.conf, pg_ident.conf and postgresql.conf
docker compose -p memes-<env> --env-file env/<env>.prod.env up -d db
MEMES_PW_OWNER=... MEMES_PW_APP=... MEMES_PW_SYNC_W=... MEMES_PW_SYNC_R=... MEMES_PW_RO=... postgres/provision.sh apply <env>
# the migrate service (release runbook, step 2) creates the schema, then:
postgres/provision.sh grants <env>
# by hand, after the first seed and the HNSW rebuild:
#   ALTER DATABASE memes_<env> SET memes.state = 'serving';
```

Passwords match `[A-Za-z0-9+/=_.~-]{24,}` (for example `openssl rand -base64 33`), travel on psql's standard input as a psql variable, and are never written to a file or an argument list. `apply` and `grants` are idempotent; `apply` creates the database, the `vector` and `pg_trgm` extensions (as the bootstrap superuser, `vector` is not a trusted extension), applies `sql/roles.<env>.sql`, sets the passwords and the markers `memes.environment` and `memes.state = 'seeding'` (set only when absent). The role grants file is generated by `python -m sync.roles --env <env>`.

## Release runbook

The contract is the design's "Release runbook" (P5d); `sync.promote` refuses a prod whose `alembic_version` differs from dev's or whose `memes.revision` marker is not the dev working tree's commit (handshake H10). Tool: `python -m sync.release` (`sync/release.py`), run in the `memes-migrate` image by the `migrate` service. Exit codes: 0 ok, 1 a step failed, 2 refused (APP_ENV differs from the database marker `memes.environment`, not connected as the owner role, a roles file that differs from what `sync.roles` generates from the registry of the image, no `DATABASE_URL`), 3 a grant differs from the registry.

Below, `<env>` is `it`, `metal` or `general`, `SHA` is the **full** 40 character commit (the marker compare in H10 is against `git rev-parse HEAD`), and compose commands run on the VPS from `deploy/prod`.

**0. Dev, before anything leaves the workstation.** The migration is additive between steps 2 and 3 (new columns nullable or with a server default, indexes `CONCURRENTLY`), so the old backend keeps working on the new schema. A migration that is not additive (drops or renames a column or table, adds a NOT NULL column without a default, rewrites a table under a lock) needs a maintenance window: stop the backend, run step 2, start the new one. The tool does not analyse migrations; the author decides. `python -m sync.roles --env <env> --check` and `pytest tests/sync/` must pass: a new table needs its registry entry and a regenerated `deploy/prod/sql/roles.<env>.sql` in the same commit, or step 2b stops the release.

**1. Build and transfer (dev).** Tag with the commit, never `latest`:

```
SHA=$(git rev-parse HEAD)
docker build -f Dockerfile.backend -t memes-backend:$SHA .
docker build -f Dockerfile.migrate -t memes-migrate:$SHA .
docker save memes-backend:$SHA memes-migrate:$SHA | gzip | ssh <admin> 'gunzip | docker load'
```

Set `BACKEND_TAG=<SHA>` in `env/<env>.prod.env` on the VPS (the `migrate` service uses the same tag as the backend). The migrate image holds no secret: the owner url comes from `DATABASE_URL_OWNER` in the env file.

**2 and 2b. Migrate, re-apply grants, verify (per environment, canary order `it`, then `metal`, then `general`).** Keep the old backend running and run the job:

```
docker compose -p memes-<env> --env-file env/<env>.prod.env --profile ops run --rm migrate
```

`all` runs three steps and stops at the first failure: `migrate` (`alembic upgrade head` as the owner; refuses unless `APP_ENV` equals `memes.environment` and the connection is the owner role of `memes_<env>`), `grants` (applies `roles.<env>.sql` as the owner: it revokes what the four service roles hold in schema `public`, grants exactly the registry's list and checks itself; one transaction, so a failure leaves the grants as they were), `verify-grants` (compares `has_table_privilege`, column and sequence privileges of all four roles with the registry, and reports an unclassified or missing table). Single steps: `... run --rm migrate migrate|grants|verify-grants`. The job has no network (`network_mode: none`), the socket volume is its only mount, and it never prints the url. A green run ends with `release: migrate, grants and verify-grants are done`. Do not go on to step 3 for this environment on any other result.

**3. Restart the backend on the new tag, then set the marker, then smoke.**

```
docker compose -p memes-<env> --env-file env/<env>.prod.env up -d backend metrics
docker compose -p memes-<env> --env-file env/<env>.prod.env exec -u postgres db \
    psql -X -d memes_<env> -c "ALTER DATABASE memes_<env> SET memes.revision = '<SHA>'"
curl -fsS https://<host>/health
curl -fsS https://<host>/api/diagnostics/capabilities     # "environment" must equal <env>
```

`depends_on` waits for a healthy db; the backend refuses to start on a marker mismatch (`memes.environment`). The marker is set as the bootstrap superuser over the socket (peer authentication of the OS user `postgres`); the owner role may do it too (`roles.<env>.sql` grants it `SET` on the three markers). Do the next environment only after this one is healthy.

**4. Dev: sync.** `python -m sync.run --env <env>` (dry run), then with `--apply --plan-hash <hash>`, from a working tree at the released commit (H10 compares `memes.revision` with `git rev-parse HEAD` and the two `alembic_version` values). A taxonomy or settings change is a code deploy (steps 1 to 3), not a promote.

### First seed of an empty environment

State after provisioning is `memes.state = 'seeding'` (set by `provision.sh`; the only seeding marker). Order:

1. Steps 1 to 3 above on the empty database (the migration chain creates the schema and, being empty, all indexes cheaply). The `grants` step is the same as `provision.sh grants <env>`.
2. Optional, to make the bulk load faster: as the owner, drop the HNSW and GIN trigram indexes first, keeping their definitions: `select indexname, indexdef from pg_indexes where schemaname = 'public' and indexdef ~* 'using (hnsw|gin)'` (save the output), then `DROP INDEX` each. `sync_w` cannot do DDL, so this is an owner step.
3. Dev: `python -m sync.run --env <env> --first-seed` (dry run, then `--apply --plan-hash`). It is resumable: after an interruption run the same command again. The normal mode refuses `seeding`; `--first-seed` refuses `serving`. The report names the tables whose indexes the owner must rebuild.
4. Owner, one environment at a time (the others stay up): rebuild the indexes with a raised `maintenance_work_mem` (a 43k x 1024-dimensional HNSW build took 149 s at 64 MB and 30 s at 1 GB), either recreating them from the saved definitions (`SET maintenance_work_mem = '1GB'; CREATE INDEX ...`) or, if they were kept, `REINDEX INDEX CONCURRENTLY <name>`. `psql` over the socket as the OS user `postgres` is enough.
5. Last: `ALTER DATABASE memes_<env> SET memes.state = 'serving'` (same `exec -u postgres db psql` as in step 3). From then on `sync.run` works without `--first-seed`; verify with the `verify` step of `sync.run` (or `python -m sync.verify`).

### Rollback

- The image is reversible: set `BACKEND_TAG` back to the previous sha, `up -d backend metrics`, and set `memes.revision` back to that sha. The previous image works on the new schema only because the migration was additive (that is the whole reason for the rule).
- The schema is not reversed. Do not run `alembic downgrade` on prod: a downgrade can drop columns that hold data, and many migrations of this chain have no safe downgrade. A bad migration is fixed forward with a new additive migration and a new release. A non-additive migration has no rollback except the backup (`backup.sh`, class P tables only; class D is rebuilt by a full promote).
- Grants are always reversible by running `grants` again (the file is the single description of them). A failed `grants` step rolls back by itself (one transaction).
- Not reversible: data the backend wrote into a class P column that a discarded schema change removed; restore from the newest backup (promote first, then restore, as `restore.sh` documents).

### When verify-grants fails

Exit 3 prints a table `kind / role / object / privilege`. Read it by kind:

| kind | meaning | what to do |
|---|---|---|
| `missing` | a role lacks a grant the registry gives it (a new table nobody granted, or a manual `REVOKE`) | run the `grants` step; if it persists the generated file is stale: run `python -m sync.roles --env <env>` on dev, commit, rebuild the image |
| `extra` | a role holds a privilege the registry does not give it (a manual `GRANT`, a default privilege, a privilege through PUBLIC) | run the `grants` step (it revokes first); then find out who granted it, because the registry is the only source |
| `unclassified table` | a table exists in `public` that `sync/registry.py` does not know | classify it in the registry (class D/P/X), run `python -m sync.render --write` and `python -m sync.roles --env <env>`, release again. Never grant by hand |
| `missing table` | a registry table is not in the database | the migration did not create it (or it was renamed): fix the migration, release again |
| `missing role` | one of the five roles does not exist | `provision.sh apply <env>` (superuser) creates them; then `grants` |
| `missing role`, `missing` or `extra` on `memes_<env>_mon` | the exporter role is absent or drifted (the release `grants` step does not touch it) | on the VPS, as the bootstrap superuser: `postgres/provision.sh grants <env>` (re-applies the roles file and the monitor role), then release again |

If `grants` itself fails with `permission denied for table X`, a table of `public` is not owned by the owner role (created by the superuser or another role): `ALTER TABLE X OWNER TO memes_<env>_owner` as the superuser, then run the step again (seen in the release test: the owner cannot revoke on a table it does not own, and the file rolls back as a whole). A failure of `migrate` normally leaves the database unchanged: `Storage/alembic/env.py` runs the whole upgrade in one transaction (PostgreSQL DDL is transactional), except steps a migration puts in an autocommit block (for example `CREATE INDEX CONCURRENTLY`), which stay committed; read the `alembic_version` it reports at the start of the next run, fix the migration, build a new tag and run the job again.

## Log suppression, backups and host accounts (security review P5d-g)

- Caddy: the global options carry their own `log` with the same filter as the sites, because a failed proxy request (`http.log.error`) goes to the DEFAULT logger and carries `request>uri` (the searched text) and request headers. Verified on the pinned image: before the change a 502 for `/api/images?q=SECRET` put `SECRET` into the container log; after it neither the query, a `Cookie`, `Authorization` nor `Referer` appears (`tests/sync/test_prod_caddy_container.py` repeats this against the real Caddyfile; skipped without docker). The edge also answers 404 to PUT, PATCH and DELETE under `/api/*` (the reader API has none, see `Backend/app/stage_policy.py`) and sends `X-Frame-Options: DENY` plus `frame-ancestors 'none'` on the SPA.
- `/srv/memes/<env>/backups` is `root:memesync-<env>` 0750: the gate account only reads it (`fetch-backup`). `backup.sh` builds everything in a root-only work directory under `/var/lib/memes/<env>` (and keeps its lock there), then `install`s the files into `backups/` as `root:memesync-<env>` 0640. `restore.sh` copies the dump, manifest and bug-reports archive into a root-only work directory before hashing and reading them, refuses a symlinked dump, and unpacks with `tar --no-same-owner --no-overwrite-dir`.
- The backend and migrate containers run as uid `10001` (`SVC_UID`, `BACKEND_UID` in `setup-host.sh`), not 1000 (the first human account on most VPSs). The image keeps its own default user; compose overrides it, and a uid without a passwd entry works (verified: import and `gunicorn --check-config` under `--read-only`, `--cap-drop ALL`, tmpfs `/tmp` with `noexec,nosuid`). The `/tmp` tmpfs of backend and migrate is `noexec,nosuid` (only the multipart spool uses it).
- `setup-host.sh` keeps every admin user already in the sshd drop-in's `AllowUsers` and adds the current `--admin-user` (a second run with another admin no longer locks the first one out). Remove an admin by editing `/etc/ssh/sshd_config.d/00-memes-sync.conf` by hand.

## Known limits / verify on a VPS

- `POSTGRES_PASSWORD` (the bootstrap superuser, first start only) sits in the env file and in the container environment (`docker inspect` shows it to root). Accepted: the file is 0600 and the host is single-owner. The alternative is `POSTGRES_PASSWORD_FILE` with a Docker secret or a 0400 file mounted into the db container; not implemented.
- **Rollout of the monitoring role:** a database provisioned before the postgres-exporter change has no `memes_<env>_mon`, so the release `all` (migrate, grants, verify-grants) exits 3 with `missing role` (the old backend keeps running). Before the FIRST release containing that change, run `postgres/provision.sh grants <env>` once as the superuser for each environment, and re-render `pg_hba.conf`/`pg_ident.conf` (`provision.sh render`) and restart `db` so the peer line and `ident_file` take effect.
- Postgres logs to files (`${LOGS_DIR}/postgres` on the host, owned by the postgres uid, created by setup-host.sh): `log_statement`, `log_min_duration_statement` and `log_connections` are never set, `log_min_error_statement = panic` and `log_error_verbosity = terse` keep statement text and key values out; a new file at every rotation (20 MB or one day); Postgres does not limit the file count, `postgres/prune_logs.py` does (`provision.sh prune-logs <env>`, newest 10 files and 200 MB per environment; the `sync.verify` 80% warning is therefore 160 MB). Between two prune runs the directory can exceed the bound by what Postgres writes meanwhile. The metrics exporter reads the socket volume `memes-<env>_pgsocket` read-only as OS user nobody and connects as `memes_<env>_mon` (pg_monitor + CONNECT, no password; `provision.sh apply|grants` creates it, `sync.release verify-grants` checks it). Bring each environment's stack up before the obs project: the exporters mount those volumes as external.
- `pg_hba.conf` allows the three service roles from the environment's whole `/24` subnet, not only the gateway address; narrowing it to the backend's `/32` needs a real VPS test (fixed addresses in the compose network) and is not done.
- The `internal` networks are not `internal: true` (the backend needs the edge network for Caddy; the db network could be, but this needs a test of the published loopback Postgres port on a real VPS).
- `pip install` for the backend and migrate images does not use hash checking (`--require-hashes`); the requirement files pin versions only.
- The base image `python:3.11-slim` of `Dockerfile.backend` and `Dockerfile.migrate` is pinned by digest (`sha256:07d4824...`, a Debian-based 3.11 slim, 2026-11-16); apt packages inside it are not pinned. Update both together.

## Observability additions

Operator guide (metrics, dashboards, log bounds, privacy rules, verify list): `docs/observability.md`.

- **Services:** the `metrics` sidecar per environment (above); the `memes-obs` project: Prometheus (200 MB limit, retention 30 d / 2 GB, scrape 15 s), node-exporter (48 MB), one postgres-exporter per environment (48 MB each, over the read-only `memes-<env>_pgsocket` volume as the role `memes_<env>_mon`); Caddy's internal `:9180` metrics site (edge network only, never published). None of them has a public route.
- **Log volumes:** `/srv/memes/<env>/logs/backend` (115 MiB), `/srv/memes/<env>/logs/postgres` (200 MB, pruned hourly by `memes-prune-logs@<env>.timer`), `/srv/memes/edge/logs/caddy` (60 MiB); see the volume table. The Docker `json-file` logs stay.
- **Memory budget:** about 400 MB resident for the whole addition (Prometheus about 150, node-exporter and the three postgres-exporters about 80, three sidecars up to 64 MB each); the compose limits sit above that. Measure with `docker stats` on the real VPS.
- **First-start order:** (1) `docker network create --subnet 172.30.0.0/24 memes-edge`; (2) the `memes-<env>` stacks (they create the `pgsocket` volumes; run `postgres/provision.sh grants <env>` first on a database provisioned before the monitoring role existed); (3) the `memes-obs` project (`docker compose -p memes-obs up -d`, run from `obs/`); (4) the host script `host/setup-host.sh <env> --obs-key FILE`.

## Files owned elsewhere

`sshd/`, `systemd/`, `backup.sh`, `restore.sh` and `memesync-gate.py` belong to P5d-T5/T7 and P3.
