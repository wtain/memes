# deploy/prod: the production VPS stacks

Design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p5-prod-provisioning-and-hardening-design.md`, section P5d. Nothing here holds a secret; filled env files and passwords live on the VPS only.

## Stack map

| Project | Files | Services |
|---|---|---|
| `memes-edge` | `edge/compose.yaml`, `edge/Caddyfile`, `env/edge.prod.env` | `caddy` (the only public ports, 80 and 443) |
| `memes-metal`, `memes-general`, `memes-it` | `compose.yaml`, `env/<env>.prod.env` | `db` (pgvector Postgres 18), `backend`, `migrate` (profile `ops`, a job) |

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
| `/srv/memes/<env>/bug-reports` | backend `/data/bug-reports`, read-write | uid 1000, group `memesync-<env>`, 2775 |
| `/srv/memes/<env>/pgdata` | db `/var/lib/postgresql` (PG18 layout; `PGDATA` is below it) | postgres uid |
| rendered Postgres config directory (`PGCONF_DIR`) | db `/etc/memes`, read-only | root |
| `/srv/memes/<env>/web` | caddy `/srv/memes/<env>/web`, read-only | `memesync-<env>` |
| named volume `pgsocket` | db and migrate `/var/run/postgresql` (unix socket) | docker |
| named volumes `caddy_data`, `caddy_config` | caddy `/data`, `/config` (certificates) | docker |

The backend runs as `1000:<gid of memesync-<env>>` (`SVC_GID`), read-only root filesystem, `/tmp` tmpfs 256 MB, all capabilities dropped, `no-new-privileges`, memory/cpu/pids limits, json-file logs rotated at 10 MB x 3, two gunicorn workers, no access log, warning log level, `--forwarded-allow-ips 172.30.0.0/24`.

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
postgres/provision.sh render <env> "$PGCONF_DIR"        # writes pg_hba.conf and postgresql.conf
docker compose -p memes-<env> --env-file env/<env>.prod.env up -d db
MEMES_PW_OWNER=... MEMES_PW_APP=... MEMES_PW_SYNC_W=... MEMES_PW_SYNC_R=... MEMES_PW_RO=... postgres/provision.sh apply <env>
# the migrate service (release runbook, step 2) creates the schema, then:
postgres/provision.sh grants <env>
# by hand, after the first seed and the HNSW rebuild:
#   ALTER DATABASE memes_<env> SET memes.state = 'serving';
```

Passwords match `[A-Za-z0-9+/=_.~-]{24,}` (for example `openssl rand -base64 33`), travel on psql's standard input as a psql variable, and are never written to a file or an argument list. `apply` and `grants` are idempotent; `apply` creates the database, the `vector` and `pg_trgm` extensions (as the bootstrap superuser, `vector` is not a trusted extension), applies `sql/roles.<env>.sql`, sets the passwords and the markers `memes.environment` and `memes.state = 'seeding'` (set only when absent). The role grants file is generated by `python -m sync.roles --env <env>`.

## Release runbook

Placeholder: filled by P5d-T8 (migrate service content, grants re-apply and verification, canary order `it`, `metal`, `general`; the contract is in the design, "Release runbook").

## Files owned elsewhere

`sshd/`, `systemd/`, `backup.sh`, `restore.sh` and `memesync-gate.py` belong to P5d-T5/T7 and P3.
