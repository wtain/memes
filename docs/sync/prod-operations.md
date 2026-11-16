# Prod operations: bringing up and running the VPS

The release runbook, the port table, the volume table, the grants-failure table and the rollback rules live in
[deploy/prod/README.md](../../deploy/prod/README.md); this page does not repeat them. It adds the order in which a **new VPS** is brought up,
the backup and restore picture, and an honest list of what is untested. Design: P5 in
`docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p5-prod-provisioning-and-hardening-design.md`. Security: [security.md](security.md).

Facts, one table, from `interfaces.md` d (also summarised in `environments/Environments.md`):

| | metal | general | it |
|---|---|---|---|
| Compose project | `memes-metal` | `memes-general` | `memes-it` |
| Database | `memes_metal` | `memes_general` | `memes_it` |
| Postgres on VPS loopback | 6432 | 6434 | 6436 |
| Workstation tunnel port | 16432 | 16434 | 16436 |
| ssh aliases (dev ssh config) | `memes-prod-metal`, `-sync`, `-up` | `memes-prod-general`, `-sync`, `-up` | `memes-prod-it`, `-sync`, `-up` |

Shared edge project `memes-edge` (Caddy, the only public ports 80 and 443). Canary order for any rollout: `it`, then `metal`, then `general`.

## Bring-up order for a new VPS

Do these in order; each step names where its detail lives. `<env>` is one environment; steps 3 to 11 repeat per environment, `it` first.

1. **Host.** A Linux VPS (the design assumes 4 GB RAM as the floor, 8 GB recommended, a systemd Linux with Docker and Docker Compose and Python 3.9+ for the gate; the design names no distribution; the Postgres image is Debian based). Firewall: only 22, 80, 443 open; note that Docker publishes bypass `ufw`, which is why
   only Caddy publishes anything beyond loopback. An admin user with sudo and docker rights.
2. **Per-environment host setup** (as root, safe to re-run): `deploy/prod/host/setup-host.sh <env> --admin-user NAME --verb-key FILE --tunnel-key FILE --upload-key FILE`
   (add `--quota images=40g` and so on for XFS project quotas where the filesystem supports it; `--print-config` shows the fixed values and changes
   nothing). It creates the groups and users `memesync-<env>` and `memeup-<env>`, the directory tree `/srv/memes/<env>/{images,bug-reports,work,retired,staging/incoming,pgdata,backups,web}`
   with the owners and modes of `interfaces.md` d, `/opt/memes` (the gate `memesync-gate.py`, `memes-facts.py`, `backup.sh`, `restore.sh`), `/etc/memes/gate.conf` (section of this environment),
   `/etc/memes/<env>/` (0700, where you put `backup.env` and `restore.env` by hand), `/var/lib/memes/<env>`, the sshd drop-in `00-memes-sync.conf` (check with `sshd -t`, keep a
   second session open when reloading), the key files under `/etc/ssh/memes-keys/` (public keys only; the private halves stay on your workstation), the systemd timers
   (`memes-backup@<env>` daily 03:30, `memes-sweep@<env>` every 30 minutes, `memes-facts@<env>` every 5 minutes) and the docker network `memes-edge`.
3. **Provision the Postgres of the environment** (README "First provisioning of an environment"): copy `env/<env>.prod.env.example` to `env/<env>.prod.env`, fill it in and `chmod 600`;
   `postgres/provision.sh render <env> "$PGCONF_DIR"`; `docker compose -p memes-<env> --env-file env/<env>.prod.env up -d db`; then
   `provision.sh apply <env>` with the five role passwords in `MEMES_PW_*` (generated, 24+ characters from `[A-Za-z0-9+/=_.~-]`). This creates the database, the `vector` and `pg_trgm` extensions,
   the roles from `sql/roles.<env>.sql`, and the markers `memes.environment = <env>` and `memes.state = seeding`.
4. **Compose stack.** The backend and migrate images are built on the workstation, tagged with the full commit sha and loaded over ssh (README release step 1); set `BACKEND_TAG` in the env file.
5. **Edge.** Fill `env/edge.prod.env` (`ACME_EMAIL`, the three hostnames, the three `WEB_ROOT_*` directories) once, then
   `docker compose -p memes-edge --env-file ../env/edge.prod.env up -d` from `deploy/prod/edge`. Hostnames must already resolve to the VPS so Caddy can obtain certificates.
   The repo uses `https://<env>.example.invalid` placeholders; set the real hostnames in the same change in `environments/settings.<env>.prod.yaml` (`cors.origins`) and the edge env file.
   The web build (`VITE_STAGE=prod`) goes into `/srv/memes/<env>/web`; how it gets there is not scripted yet (see the untested list).
6. **First migration.** README release steps 2 and 2b: `docker compose ... --profile ops run --rm migrate` (migrate, grants, verify-grants), then
   `provision.sh grants <env>` is the same grants step; start the backend, set `memes.revision` to the full commit sha, smoke-test `/health` and
   `/api/diagnostics/capabilities` (`environment` must equal `<env>`, `stage` must be `prod`).
7. **Gate install and keys.** The gate and `gate.conf` are installed by step 2; check `/etc/memes/gate.conf` caps, that `/srv/memes/<env>/work` and `images` are on the same filesystem, and
   that the verb key runs `memesync-gate.py <env> handshake` (from the workstation: `ssh memes-prod-<env>-sync handshake`). The tunnel key must forward only to the environment's Postgres port;
   the upload account must be refused a shell.
8. **Workstation ssh config.** Three `Host` blocks per environment with the aliases above (verb key, tunnel key, upload key; separate `IdentityFile` per alias, `IdentitiesOnly yes`),
   the host key accepted once by hand so `StrictHostKeyChecking=yes` works. Keys never go in the repo or in an agent session.
9. **Workstation settings and secret.** Create `environments/.env.<env>.sync` (git-ignored; `SYNC_WRITER_PASSWORD`, `SYNC_READER_PASSWORD`, restricted permissions).
   Fill the tracked `sync.dev.database` (name of the dev database of the environment) and `sync.prod.api_url` (public base URL) in `environments/settings.<env>.yaml`;
   the other `sync.prod.*` values (database, roles, aliases, ports) are already there.
10. **Doctor.** `python -m sync.doctor --env <env>` must exit 0 (H6, the gate, is skipped by the doctor). Then `python -m sync.verify --env <env>`.
11. **First seed.** [first-seed-and-routine.md](first-seed-and-routine.md). After it: owner rebuilds the indexes, sets `memes.state = serving`, `python -m sync.verify --env <env> --deep-files`.

For the next releases use the README release runbook; after each release run a dry-run sync, read the report, apply.

## Backups and restore

- **What is backed up:** only data that exists on prod and nowhere else: the six class P tables listed in `deploy/prod/backup.tables` (generated by `python -m sync.backup --write`
  from the registry) and the bug-report directory. Class D is rebuilt by a full promote from dev and is not backed up on the VPS.
- **When:** `memes-backup@<env>.timer`, daily 03:30 (persistent). `backup.sh <env>` writes into `/srv/memes/<env>/backups`: `memes-<env>-classp-<UTC stamp>.dump`
  (`pg_dump -Fc --data-only` as the `sync_r` role over the container's unix socket), `...-bugreports-<stamp>.tar.gz` and `...-classp-<stamp>.meta.json` (sizes, sha256, rows per
  table, `pg_restore_list: "ok"`), writes via `*.part` and renames, and removes sets older than 14 days. The password is in `/etc/memes/<env>/backup.env` (0600, root), never on a command line.
- **Off the VPS:** the gate verb `fetch-backup` serves the newest dump and its metadata read-only (`sync.verify` S6 reads only the metadata and warns after 36 hours).
  The design has the dev pull fetch the newest dump on each `sync.pull`; the dev-side fetch step is **not built** (open item). Until it is, copy the newest dump by hand with the verb key
  (`ssh memes-prod-<env>-sync fetch-backup`, see the gate) and keep it off the VPS. A copy on the same disk as the data is not a backup.
- **Restore:** `deploy/prod/restore.sh <env> --list`, then `restore.sh <env> <dump> [--truncate] [--with-bug-reports] [--dry-run]`. Order of a disaster recovery: schema exists (migrate), images rows exist
  (promote files and data from dev, because the ratings table has a foreign key to `images`), then restore. It checks the database marker and that the dump belongs to `<env>`, checks the sha256, prints the plan,
  and runs ONE transaction only after you type `restore memes_<env>`: optional truncate, data, sequences past the highest id, row count check against the dump (any difference rolls back everything).
  Password: `/etc/memes/<env>/restore.env` (the owner role).
- Restoring a rating from `ratings_archive.jsonl` (the local file the pull writes before overwriting a dev rating) is a manual step.

## Day-to-day on the VPS

- Sweep: `memes-sweep@<env>` clears abandoned staging batches. Facts: `memes-facts@<env>` writes `disk.json` and `containment.json` that `sync.verify` reads through the gate.
- Look at `sync.verify` (A1 to A3: growth, bug-report directory, disk per mount) after each run; the alarms are the only guard against unbounded reader-table or bug-report growth,
  because nothing is pruned automatically (decision 11).
- To lift a gate cap for a legitimate large run, edit `/etc/memes/gate.conf` on the VPS for that day and revert after (never a client flag).
- Rollback and release failures: README "Rollback" and "When verify-grants fails".

## What is untested until a real VPS exists

Everything here is built and covered by tests with fakes, containers or loopback, but has not met a real VPS, real sshd or the real internet. Treat the first bring-up as the acceptance run
(the P7 plan: a throw-away VPS or a local Linux VM first; chroot and sshd behaviour are only fully real there):

- The real SSH path on Windows OpenSSH: tunnel start and stop, host-key handling, the verb key and the upload account (`ChrootDirectory`, `internal-sftp -u 007`, `AllowUsers`, `permitopen`), the real throughput of the
  tar batches (the 11 minutes for `general` is a loopback estimate), Windows `ssh.exe` stdin capped near 12.5 MB/s through pipes.
- `setup-host.sh`, the sshd drop-in, the systemd timers and XFS quotas on a real distribution; `provision.sh`; the Caddy edge with real certificates (the Caddyfile was validated in a container only).
- Postgres 18.7 on prod (pinned image `pgvector/pgvector:pg18-trixie`) against dev's 18.3 (a minor-version gap; H10 and `sync.verify` D1 compare the major version and extension versions only).
- Gate verbs `disk`, `containment`, `fetch-backup` and the checks that read them (S4 account probe, S6, S8, A3), and the HTTP checks S3, S7, A4: mock-only.
- The numbers of the app-level rate limiter (`abuse.*` in `environments/settings.prod.yaml`) are the implementer's estimates, not measured against real traffic; there is no CDN or WAF in front.
- The complete first seed and the interrupted-and-resumed seed against a real prod; the HNSW rebuild times on the VPS size actually bought.
- The backup timer, the restore drill, and the missing off-VPS fetch step on the dev side.
- The prod web build and its deployment into `/srv/memes/<env>/web`, and a real Android build pointed at prod.
- Image-path containment on dev (a dev symlink scan for the new containment rule has not been done).
- The real hostnames, `sync.prod.api_url`, `sync.dev.database` values: not filled; `sync.doctor` fails on them until they are.
