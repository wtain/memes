# Admin Functionality Needing Permission Controls

Living checklist of backend functionality that has no authorization model today and will need one
before it's safe to expose beyond a trusted network. Not a spec — no status/lifecycle, just append
to it as new admin-only functionality ships without permission checks.

## Current state (as of the admin batch controller, 2026-07-28)

There is no authentication or authorization anywhere in this backend today. Anyone who can reach a
backend process's port can call any endpoint, including the ones below.

`environments/.env.*` (gitignored, per-environment) can configure LAN-facing origins
(`GENERAL.FRONTEND_ORIGIN` / `ALTERNATIVE_FRONTEND_ORIGIN` in `Backend/app/main.py`'s CORS setup) —
meaning a given environment's backend is not guaranteed to be reachable from localhost only. Origin
restriction is a browser-enforced CORS check, not a server-side authorization boundary: it does not
stop a direct HTTP client (curl, another service on the LAN, etc.) from calling these endpoints
regardless of `Origin`.

## Interim mitigation (accepted for now, not a substitute for real controls)

Two layers, neither of which is real access control on its own:

1. **The frontend UI never exposes these endpoints.** A casual user browsing the app has no link,
   button, or page that calls them.
2. **The batch-trigger allow-list is fixed, server-side code** (`batch/registry.py`) — a caller can
   only ever trigger one of the three registered scripts, with no way to supply an arbitrary module
   path or extra CLI arguments. This bounds *what* an unauthenticated caller could do, but does not
   stop them from doing it.

Anyone with network access to a backend port (which may include the LAN, depending on
per-environment config) can trigger any registered batch job and read run history/status for all of
them. This is accepted as a known gap for now, not resolved.

## Endpoints needing permission controls once a model exists

- `POST /api/admin/batches/{batch_name}/run` — triggers a batch job. Needs the strongest guard of
  the three (arbitrary triggering of jobs against production data).
- `GET /api/admin/batches/runs/{run_id}` — read access to run status/error detail.
- `GET /api/admin/batches/runs` — read access to run history.

(Extend this list as future admin-only functionality ships without its own permission model.)

## When a permission model exists

Revisit this file: replace "interim mitigation" above with the actual model (roles, tokens,
whatever gets chosen), and check off / remove entries above as they get real guards. Until then,
treat every endpoint under `/api/admin/*` as unauthenticated by default — don't assume a new
`/api/admin/*` endpoint is safe just because it's "just for admins" in name.
