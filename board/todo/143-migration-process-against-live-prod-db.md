# [P1] No documented process for applying Alembic migrations safely against a live prod DB

- **Priority:** P1
- **Area:** Data lifecycle
- **GitHub issue:** https://github.com/wtain/memes/issues/143
- **Source:** docs/2026-08-13-ORR.md

## Problem

Migrations (`Storage/alembic/`) are run manually per dev environment today (load env vars,
`alembic upgrade head`). There's no documented process for how a schema migration gets applied
to a live, continuously-serving production DB: ordering relative to the app deploy, rollback
plan, or zero-downtime considerations for what will likely be a single-VPS/single-DB setup.

## Why it matters

The expectation is that schema changes and data migration to prod should be easy/routine —
today that path is undesigned, and a migration run against a live prod DB with no plan is a
real outage/data-loss risk.

## Pointers

- `Storage/alembic/` (30+ existing migrations, all applied manually per environment)
- `CLAUDE.md`'s "Database migrations" section — dev-workflow only

## Scope

Document (and where needed, script) a safe migrate-and-deploy process for prod. Related to the
dev→prod data sync design task (#137) — coordinate rather than duplicate. Design/requirements
TBD.
