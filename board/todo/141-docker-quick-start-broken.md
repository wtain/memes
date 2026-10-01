# [P1] SETUP.md's Docker Quick Start (docker-compose up -d) does not work — no root compose file

- **Priority:** P1
- **Area:** Deployment pipeline
- **GitHub issue:** https://github.com/wtain/memes/issues/141
- **Source:** docs/2026-08-13-ORR.md

## Problem

`SETUP.md`'s "Quick Start (Docker)" tells a new user to run `docker-compose up -d` from the repo
root to bring up Postgres + Backend + Frontend together. No such file exists —
`Storage/docker-compose.yaml` only runs the database. This was already flagged in
`docs/audit/audit-2026-06-25.md` (Tier 2/3) and is still unresolved as of this review
(2026-08-13, ~7 weeks later).

## Why it matters

First-run onboarding is broken, and this is also the natural starting point for a VPS
docker-compose-based deployment (see the related VPS deployment task) — closing this gap serves
both dev onboarding and prod deployment.

## Pointers

- `SETUP.md` (Quick Start section)
- `Storage/docker-compose.yaml` (DB only)
- `Dockerfile.backend`, `Frontend/memes-frontend` (frontend has its own Dockerfile via
  `frontend-release.yml`)

## Scope

Add a working root-level compose file (or explicitly rewrite SETUP.md to stop claiming this
works). Design/requirements TBD.
