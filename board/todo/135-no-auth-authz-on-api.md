# [P0] No authentication/authorization on any API endpoint

- **Priority:** P0
- **Area:** Security
- **GitHub issue:** https://github.com/wtain/memes/issues/135
- **Source:** docs/2026-08-13-ORR.md

## Problem

There is no authentication or authorization anywhere in the backend today. Anyone who can reach
a backend port can call any endpoint, including `/api/admin/*` (trigger arbitrary registered
batch jobs against production data, read run history).

## Why it matters

Blocking for any deployment reachable from outside a trusted network (i.e. a public VPS). This
is the single largest security gap standing between the current state and a public launch.

## Pointers

- `docs/security/admin-permissions-todo.md` — living checklist of admin endpoints needing
  guards, current interim mitigations (none are real access control)
- `Backend/app/main.py` — CORS is the only boundary today, and it's a browser-enforced check,
  not server-side auth

## Scope

Design (auth model — API keys, JWT/session, roles) + implement it, starting with
`/api/admin/*` and any mutation endpoints. Design/requirements TBD — this only tracks that the
work is needed and where to start.
