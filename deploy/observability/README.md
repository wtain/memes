# Grafana on the workstation

Dev-side half of the observability design (`docs/superpowers/specs/2026-11-19-observability/`, section 5.5). Prometheus runs on the VPS (`deploy/prod/obs/`); this directory
holds a Grafana that reads it through an ssh tunnel. Nothing here is reachable from the network: Grafana is published on `127.0.0.1:3300` only.

## Use

1. `python -m sync.obs_tunnel` opens `127.0.0.1:19090` to the VPS Prometheus (ssh alias `memes-prod-obs`, a restricted key that can forward to `127.0.0.1:9090` only).
   `python -m sync.obs_tunnel --stop` closes it. The alias lives in your ssh config; no credential is in the repository.
2. `cp .env.example .env` and set `GRAFANA_ADMIN_PASSWORD` (`.env` is git-ignored).
3. `docker compose up -d` in this directory, then open http://127.0.0.1:3300 and sign in as `admin`.

The datasource (`uid memes-prometheus`) and the three dashboards (Performance, Usage, Health) are provisioned from files and read-only in the UI. Every dashboard has an
`Environment` selector (metal, general, it). Host disk/memory and Caddy panels are host-wide and ignore it. There is no log-volume panel: the log directory sizes are
doctor facts, not Prometheus metrics.

The datasource URL is `http://host.docker.internal:19090`. On Docker Desktop (the Windows workstation) that name reaches the tunnel even though
`sync.obs_tunnel` binds it to `127.0.0.1` only. On a Linux Docker engine it does not: `host-gateway` resolves to the bridge address, and a
loopback-bound port is not listening there. This setup is only made for Docker Desktop; a Linux workstation needs its own change (for example
host networking for Grafana with the datasource on `http://127.0.0.1:19090`), never a tunnel bound to `0.0.0.0`.

Ports are listed in `environments/Environments.md`. Dashboards show technical metrics only, never search text or ids.
