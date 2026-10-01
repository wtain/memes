# [P2] No process supervision / restart policy design for the VPS-hosted backend

- **Priority:** P2
- **Area:** Deployment pipeline
- **GitHub issue:** https://github.com/wtain/memes/issues/145
- **Source:** docs/2026-08-13-ORR.md

## Problem

`/health` now correctly checks DB connectivity (`Backend/app/main.py`), but nothing consumes it
yet — there's no documented process supervisor (systemd unit, Docker `restart: always`, or
orchestrator liveness probe) that would actually act on a failing health check on a bare VPS.

## Why it matters

A correct health check with nothing wired to it doesn't achieve self-healing — the service can
silently stay down after a crash.

## Pointers

- `Backend/app/main.py` (`/health` endpoint)
- `Dockerfile.backend` (`HEALTHCHECK` directive exists but only matters if something restarts
  the container on failure)

## Scope

Decide and document the supervision mechanism for the VPS (systemd vs. Docker restart policy
vs. compose). Depends on the VPS deployment automation task (#136) — coordinate rather than
duplicate. Design/requirements TBD.
