# [P1] In-memory rate limiter doesn't work under the production gunicorn multi-worker setup

- **Priority:** P1
- **Area:** Security
- **GitHub issue:** https://github.com/wtain/memes/issues/139
- **Source:** docs/2026-08-13-ORR.md

## Problem

`Backend/app/services/rate_limit.py`'s `SlidingWindowRateLimiter` is in-memory and its own
docstring says "Not suitable for multi-process deployments." `Dockerfile.backend` runs
`gunicorn ... -w 4` (4 worker processes) — each worker gets its own independent counter, so the
configured limits (`upload_limiter`: 10/60s, `bug_report_limiter`: 20/60s) are not actually
enforced as documented once deployed via the production image.

## Why it matters

Silent gap between intended and actual behavior in exactly the deployment configuration the
repo already ships (the Dockerfile everyone will use to go to prod).

## Pointers

- `Backend/app/services/rate_limit.py`
- `Backend/app/api/uploads.py`, `Backend/app/api/bug_reports.py` (callers)
- `Dockerfile.backend` (the `-w 4` gunicorn config)

## Scope

Replace with a shared-state limiter (e.g. Redis-backed) or otherwise make it correct across
worker processes. Design/requirements TBD.
