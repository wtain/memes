# [P0] No VPS deployment automation — CI publishes images but nothing deploys them

- **Priority:** P0
- **Area:** Deployment pipeline
- **GitHub issue:** https://github.com/wtain/memes/issues/136
- **Source:** docs/2026-08-13-ORR.md

## Problem

CI/CD (`backend-docker.yml`, `frontend-release.yml`, `release.yml`) builds and pushes images to
GHCR, but nothing pulls/runs them anywhere. `CICD.md`'s "Deployment" section step 4 is literally
just "Deploy Docker image" with no automation, script, or target host behind it. No reverse
proxy/TLS termination, process supervision (systemd/restart policy), or VPS provisioning is
documented or scripted anywhere in the repo.

## Why it matters

This is the core blocker for actually going live on a VPS — today there is no path from "image
built" to "service running and reachable."

## Pointers

- `CICD.md` (Deployment section)
- `Dockerfile.backend` (exposes plain HTTP on 8000, no TLS)
- `Frontend/memes-frontend/nginx.conf` (frontend-only, not wired to backend)

## Scope

Design + implement the actual deploy step: how the built image reaches the VPS, reverse
proxy/TLS termination, restart policy. Design/requirements TBD — this only tracks that the work
is needed.
