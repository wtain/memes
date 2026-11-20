# Observability: whole-branch review

Spec: docs/superpowers/specs/2026-11-19-observability/2026-11-19-observability-design.md (status `implementation`)
Plan: docs/superpowers/plans/2026-11-19-observability.md
Branch: worktree-observability (merge to main only with the owner's confirmation)
Companion audit: docs/superpowers/reviews/2026-11-19-logging-audit.md

## Method

Tasks O1-O8 ran as subagent-driven development: one implementer per task, a spec + quality review after each, scoped fix rounds for Critical/Important findings. After O8, three read-only reviewers ran in parallel over the whole 24-commit diff (privacy and cardinality; prod hardening, compose, host, ssh and rollout; correctness of the metrics, middleware, beacon, dashboards), then one fix wave (three commits) and one scoped re-review of that wave. This is the single review iteration CLAUDE.md asks for.

## Test results (implementer-reported, after the last commit)

| Command | Result |
|---|---|
| `cd Backend && pytest` | 1080 passed |
| `pytest tests/sync` | 990 passed, 11 skipped, 1 failed (`test_no_crlf_in_any_deliverable`: `sync/backup.py` is CRLF in the Windows working copy, LF in the index; pre-existing checkout artifact) |
| `pytest tests/docs` | 41 passed |
| `pytest tests/integration/sync` (ocrdb_test) | 316 passed |
| `pytest tests/integration` (ocrdb_test, whole root) | 1866 passed |
| `pytest tests/docker` | 7 passed (includes the check that gunicorn loads `/app/gunicorn.conf.py` from `/app` without `-c`) |
| Caddy container test | 5 passed (Content-Disposition passes through a real 200, absent from the file log and `docker logs`; removing the three `resp_headers delete` lines makes it fail) |
| `test_prod_obs.py` | 10 passed (incl. promtool on the pinned image) |
| `test_host_container.py` | 86 passed (incl. obs key forwards 9090 only, no shell) |
| `batch/tests`, `tests/rules`, `tests/ai` (before the fix wave) | 411, 144, 66 passed |
| Frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` (before the fix wave; the wave did not touch the frontend) | clean, clean, 1465 passed |
| Android `:app:testDebugUnitTest` | not re-run: task was UP-TO-DATE, 315 tests / 0 failures from the stored result files |

Not run anywhere: the "verify on a real VPS" list in docs/observability.md (systemd timers, the obs tunnel through docker-proxy, Prometheus scraping all eight targets, peer auth through the read-only `pgsocket` volume across projects, real memory use against the limits).

## What was fixed

Per task (details in the commits): each of O2-O7 got at least one scoped re-review; fix rounds were needed for O5 (image id still logged on get_image 404), O6a (sidecar on the wrong network; bring-up commands omitted the sidecar), O6b (client address kept in Caddy logs), O6c (log bound not real; rollout gap), O6d (malformed log facts hid a low-disk error), O7 (dashboard test did not check label names or exact exporter metric names).

Whole-branch fix wave:
- Privacy: Caddy `resp_headers` deleted in all three filters (Content-Disposition carried library filenames, sometimes client upload names); `uvicorn.access`/`gunicorn.access` can never receive the file handler; `repository/popularity.py` logs the exception class only; the exception boundary guards its own sends; the client upload name left the dev-only upload log line.
- Correctness: degraded searches are counted once, when computed (not on cache hits, "Show more", or on `it`); `ClientDisconnect` is an info-level non-event; real free-space check replaces the ineffective full-disk probe; `FileResponse` subclass hoisted; order-independent beacon test; allowlist-equality test across backend, web and Android.
- Deploy: global Caddy `metrics` option; backend runs `gunicorn` without `-c` (an old image still starts, rollback works); backend log dir is owned by the backend uid, mode 2750.
- Docs: first-start order corrected (`setup-host.sh` first), upgrade checklist, rollback rule, operating rules (no HUP, unset `PROMETHEUS_MULTIPROC_DIR` for exec, MiB wording, unauthenticated Prometheus on `memes-edge`, Docker Desktop vs Linux engine note for the Grafana datasource).

## What was not fixed, and why (acceptable deviations)

- No log-volume panel in Grafana: log sizes are host-script facts (`disk.json`), not Prometheus metrics; the signal is the `sync.verify` A3 warning. Spec section 8 amended.
- `memes-obs` network subnet not pinned; node-exporter `--path.procfs` not set (dashboards use only memory and filesystem metrics).
- `sync/obs_tunnel.py`: `start` does not wait for the port, `stop` can orphan ssh during startup; `postgresql.conf` lint does not forbid `log_parameter_max_length_on_error` (default 0).
- Deferred minors recorded in the ledger (test-strength items, `dir_bytes` unreadable directory counts as 0, obs key not compared with other keys, README tidy-ups).

## Owner decisions still open

1. The `sync.verify` A3 warning at 80% of each log bound is spec-literal but will fire persistently once rotation has filled (rotated logs sit at 80-100% of their bound). Keep it, or warn above 100-110%?
2. Caddy logs the request `path`, which can contain image ids (`/api/images/<id>`, `/memes/:id`). Accepted and documented (pre-existing behaviour, spec 6.3 keeps `path`). Confirm, or have Caddy collapse id segments.
3. Postgres primary error messages can still name a value for a rejected cast (`terse` removes DETAIL, STATEMENT, CONTEXT only). Accepted; clients must bind typed parameters.

## Intentionally ignored

- Working-copy CRLF in some existing files on this Windows checkout (the index is LF); the one failing sync test is that artifact.
