# [P2] No monitoring/alerting for the production service (uptime, log aggregation)

- **Priority:** P2
- **Area:** Observability
- **GitHub issue:** https://github.com/wtain/memes/issues/146
- **Source:** docs/2026-08-13-ORR.md

## Problem

Backend logging is structured (`logging` module, since the June audit fix) but goes to stdout
only with no aggregation target. There is no uptime check, no APM, and no alerting of any kind.
`SimpleMetricsListener` (batch jobs) has no persistence or export either.

## Why it matters

Without at least a basic uptime check and log aggregation, an outage or silent failure on the
VPS may go unnoticed until a user reports it.

## Pointers

- `Backend/app/main.py` (logging setup)
- `docs/audit/audit-2026-06-25.md` — flagged the same gap (no request-level metrics, no export)

## Scope

Add, at minimum, an uptime check and log shipping/aggregation for the production deployment.
Design/requirements TBD — full APM is a stretch goal, not required for launch.
