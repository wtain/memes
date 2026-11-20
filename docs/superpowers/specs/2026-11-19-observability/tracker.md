# Observability epic: tracker

Status: implementation

Plan: `docs/superpowers/plans/2026-11-19-observability.md`. Design: `2026-11-19-observability-design.md` (same folder). Operator docs: `docs/observability.md`.

Worktree `observability`, branch `worktree-observability`. Working rules: agent-driven for complex tasks, ask before merging to main, ask rather than guess.

## Owner decisions so far

1. Metrics, Prometheus + Grafana. First scope (owner, 2026-11-19):
   - **Performance:** image load times, search query performance, latency percentiles.
   - **Usage:** page visit count, backend call count, image load count.
2. **Business metrics: deferred.** Needs a grilling session of its own before any design. Do not design or build them in the first delivery.
3. Logs: the code already logs (backend); check it, add what is missing, and give production its own log volumes.

## Constraints found

- No metrics code exists today. The "metrics" in the repo are business-metric tables, a different thing.
- Prod logs go to Docker json-file (10 MB x 3), no dedicated volume; the backend root filesystem is read-only.
- 51 of 63 batch scripts use print, 2 use logging; batches and sync tools run on dev only.
- Search text must never reach logs or metric labels. A new `/metrics` route needs a `ROUTE_POLICY` entry and must not be public.
- Prod VPS floor is 4 GB, three environments.

## Open questions

- Where Prometheus and Grafana run (VPS, or workstation over the tunnel).
- How page visits are counted (the SPA is static; the backend only sees API calls).
