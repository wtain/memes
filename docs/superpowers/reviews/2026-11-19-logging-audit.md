# Logging audit (O1)

Read-only audit of what the backend, `sync.*`, the gate and the host tools log today, against the checklist of design section 6.1
(`docs/superpowers/specs/2026-11-19-observability/2026-11-19-observability-design.md`). Source files only; no database, no running server.
Feeds task O5 (logging changes). Line numbers are of this branch at the audit date.

## Setup facts that shape every row

- Logging is configured only in `Backend/app/main.py:64-85` (`_configure_logging`, called in the lifespan): one stdout `StreamHandler`, root level INFO,
  format `time level [pid] name: message`. No request id, no file handler, no per-request context.
- Prod runs `gunicorn -w 2 -k uvicorn.workers.UvicornWorker --log-level warning` (`deploy/prod/compose.yaml:72-86`; `Dockerfile.backend:107` has `-w 4`).
  No access log by design. The `warning` level affects gunicorn's own loggers; application INFO lines still appear because the lifespan sets the root to INFO.
- uvicorn logs any exception that escapes a route as a full traceback ("Exception in ASGI application"), including the exception message. There is
  no app-wide unhandled exception handler (`main.py:148` registers only `RequestValidationError`).
- `Storage/db.py:58` has `echo=False`. Good. No SQL statement logging anywhere.

## Table

Leak risk column: yes means a logged argument can carry client-supplied text (request text, free-form path/file names) or an exception whose
message/repr can embed statement parameters.

| Module | What is logged today | Missing per 6.1 checklist | Leak risk |
|---|---|---|---|
| `Backend/app/main.py` (lifespan, health) | Warnings with exception type only for parser/pipeline build failures (`:113`, `:121`), INFO for warm-up failure type (`:96`). `log_pool_bounds()` only on prod (`:107`). | Startup line with stage, worker count, pid, settings summary (pool line exists but is prod-only and has no workers); shutdown line (none); unhandled-exception handler (none); slow-request log (none); request id (none). `/health` swallows the DB exception silently (`:215-218`: no log, response detail differs dev/prod). | no (types only). `:217` puts `{exc}` in the dev response body, not a log. |
| `Storage/db.py` | One INFO line with pool_size, max_overflow, max connections, stage (`:40`). | Pool timeout (`QueuePool limit ... timed out`) and connection errors are not logged by us; they surface only as an uvicorn traceback or a 500. No pool-wait or checkout-failure line. Line is not called on dev. | no |
| `Backend/app/middleware.py` | Nothing. 429 (`:46`, `_respond_429`) and 413 (`:57`, `:84`, `:90`) rejections are silent. | Rate-limit and body-limit rejections (sampled); client context has no log. | no (nothing logged) |
| `Backend/app/services/abuse.py`, `rate_limit.py` | Nothing. `enforce` raises 429 silently (`abuse.py:143-148`); `check_search_inputs` 422s silently (`:159-171`). | Rate-limit rejections by scope (sampled, scope only). | no |
| `Backend/app/stage_policy.py` | Nothing. Prod 404 for non-READER routes is silent (`:157-159`); missing verdict raises `RuntimeError` at import (`:193`, `:199`). | Policy denial (sampled, template only, never the raw path), count of denials. Capability failures: the capabilities endpoint has no logging. | no |
| `Backend/app/prod_startup.py` | Nothing (raises `RuntimeError` including the marker and APP_ENV, `:19-21`). | Startup refusal appears only as a crash traceback; no explicit "environment marker ok/failed" line. | no (database marker and env name, no secrets) |
| `Backend/app/error_handlers.py` | Nothing logged. Strips `input` from 422 bodies for `/api/meme-from-text` (`:16-20`); other routes use FastAPI's default (echoes input in the response body, not logged). | Validation-failure count by route template. | no (no log) |
| `Backend/app/api/bug_reports.py` | Nothing. 422/507 rejections and the saved report are silent (`:30-67`). | Bug-report receipt (size, outcome, no content; the original filename is client text, so omit it); 507 storage-full line. | no (nothing logged). Do not log `original_filename` (`:33`). |
| `Backend/app/api/images.py` | `get_image` 404 for unknown id (`:453`) and file missing on disk (`:457`) at WARNING. | Id/filename lines are the right idea but unbounded and unsampled: a scan produces one line per request. No search timing. No request id. | yes: `:453` and `:457` log `image_id`, a client-controlled path segment of arbitrary length (log forging, not private text); `:457` also logs the DB filename via `%r`. Cap and escape. |
| `Backend/app/services/image_store.py` | Refused file name at WARNING (`:74`). | Missing-file for a known id is logged at the caller only. | yes (low): `:74` `%r` of `filename`; names come from the database, but the `UnsafeImagePath` refusal text may echo a path. |
| `Backend/app/repositories/image_repository.py` | Ranked search warning with match count, threshold, query LENGTH and elapsed ms (`:191`). | Slow-request line (this is the only timing today and only above a match count). | no (length only, deliberately) |
| `Backend/app/api/recommendations.py` | Seed mismatch WARNING with two ints (`:45`). | Nothing for the checklist. | no (ints; `q` is not logged) |
| `Backend/app/api/meme_from_text.py` | WARNING with exception type only (`:53`); comment explains why (DB error carries the user's lemmas). | Request id; stage timing is in `retrieval`. | no. Good pattern to copy. |
| `Backend/app/api/meme_profiles.py:42`, `meme_text_ratings.py:35`, `admin_profiles.py:37`, `admin_discovery.py:61,80,98` | WARNING with `type(error).__name__` only. | - | no |
| `Backend/app/api/diagnostics.py` | WARNING about a stale statistics snapshot shape (`:171`), no args. | Capability/policy failures; DB connection error on health. | no |
| `Backend/app/api/comics.py` | `logger.warning(..., exc_info=True)` when the pipeline start fails (`:136`). Dev only. | - | yes (low): `exc_info=True` writes the full traceback and exception message. |
| `Backend/app/api/uploads.py`, `admin.py`, `ingestion.py`, `recut.py`, `trends.py`, `history.py`, `concepts.py` | No logging of their own. `uploads.py:154-159` puts `str(error)` in HTTP details. | Upload rejections (429 `:110`, 422 `:113`) silent. | no (nothing logged) |
| `Backend/app/services/inbox_upload_service.py` | Path/space/permission errors at ERROR/WARNING (`:163-215`, `:230`), exceptions with tracebacks (`:188`, `:342`, `:385`, `:390`). Dev only. | - | yes: `:342` `%r` of the client-supplied upload `name`; `logger.exception` at `:188`, `:342`, `:385`, `:390` writes tracebacks with messages; `:163`, `:166`, `:173` log the configured inbox path (a local path, not a secret). |
| `Backend/app/services/library_metrics.py:28`, `ingestion_metrics.py:26` | WARNING `"%s: %r"` with the exception object. | - | yes: `%r` of a SQLAlchemy `DBAPIError` includes the failing statement and bind parameters (ids/labels here, not search text, but it is the exact pattern design 6.3 forbids). Log the type only. |
| `Backend/app/services/history_recorder.py:23`, `impression_recorder.py:28`, `outcome_recorder.py:25`, `image_service.py:271` | WARNING with exception type (and surface name) only. | - | no |
| `Backend/app/services/meme_profile_service.py:118` | WARNING with image id and exception type; comment says never log the text. | - | no |
| `Backend/app/services/meme_from_text_pipeline.py:40` | WARNING with exception type. | - | no |
| `Backend/app/services/comics_pipeline_trigger.py` | INFO busy (`:30`), WARNING with `error.detail` (`:32`), WARNING `exc_info=True` (`:35`). Dev only. | - | yes (low): `:35` traceback; `:32` HTTP detail text. |
| `Backend/app/services/recut_files.py` | `logger.exception` with file paths (`:23`, `:36`, `:57`). Dev only. | - | yes (low): paths and tracebacks; no request text (files are server-derived). |
| `Backend/app/services/ingestion_service.py:405` | WARNING with refusal reason and image id. | - | no |
| `Backend/app/batch_subprocess.py` | INFO launching with the full `args` list (`:118`), exit code INFO/WARNING (`:131-133`), exception in fire-and-forget task (`:143`). | Spawn and exit exist (dev only). No duration, no run id. | no (args are script name, env, trigger; no request text). `logger.exception` at `:143` writes a traceback. |
| `Backend/app/scheduler.py` | Skipped malformed job (`:40`), orphaned run marked failed (`:79`), tick failed (`:103`), initial delay failed (`:126`) with `%s` of the exception, starting N jobs (`:147`), job-loop error with traceback (`:167`). Dev only. | Per-spawn line (the job name is in `batch_subprocess` only), stop line. | no for request text (DB errors from the batch run table; no user input). `%s` of a DB exception can contain statement parameters (run ids). |
| `retrieval/pipeline.py` | INFO per search: degraded, fallback, candidate count, result count, cache_hit, parse/embed/retrieval ms (`:143`); warm-up failures with type (`:205`, `:215`); embedding failure type (`:262`). | Request id. Breaker-open transitions are not logged. | no (numbers and enums; text is never passed) |
| `parse/parser.py` | INFO per parse: model, versions, language, degraded, fallback, attempts, cache_hit, latency (`:66`); type-only warnings (`:99`, `:116`). | - | no (language is a detected code, not text) |
| `parse/client.py`, `parse/cache.py`, `retrieval/embed.py` | No logging. | Ollama connection errors are swallowed into the degraded profile with no log line (only the `fallback` field). | no |
| `sync/**` | `print()` to stdout/stderr with fixed templates: `sync: {exc}` (`config.py:338`, `doctor.py:45`, `promote/cli.py:311`, `pull/cli.py:317,487`, `files/promote.py:383`, `run/cli.py:547`, `verify/cli.py:195`); step failure with exception type (`run/cli.py:335,673`); `fetch_backup.py` size and sha prefix (`:152,181,183`). Reports under `Storage/sync-state/`. | No structured log, no timestamps, no run id in lines. Dev side only. | yes (low): `sync: {exc}` prints the exception message, which can include paths, hostnames and driver text; not request text. Prod-pulled text (class P) is untrusted; confirm no row text reaches these prints. |
| `sync/release.py` (runs on the VPS) | Step lines `migrate:`, `grants:`, `verify-grants:`, `release:` with revisions, sqlstate and `diag.message_primary` (`:255-347`). | Timestamps; a one-line start/finish with env. | yes (low): `:293` and `:347` print `diag.message_primary`, a Postgres message that can quote a value in a constraint error. |
| `deploy/prod/memesync-gate.py` | Failure lines only, on stderr to the ssh session: `gate: <reason>: <detail>`, hostile names escaped by `printable()` (`:72`); last-resort line is the exception type only (`:1223`). Success is silent. | Persistent gate log (every verb, env, result, duration), sampled; counters for denied verbs. No syslog or log file exists. | no (names are escaped and bounded to 80 chars; no content). |
| `deploy/prod/host/memes-facts.py`, `setup-host.sh` | Usage text on stderr (`:150-153`); `setup-host:` say/die lines. | Log directory sizes in `disk.json` (the design adds it in O5). | no |
| `Dockerfile.backend`, `compose.yaml` | gunicorn `--log-level warning`, no access log; Docker `json-file` driver. | Log files and rotation (design 6.3). | no |

## Checklist status

| Checklist item | State |
|---|---|
| Startup/shutdown with stage, workers, pool bounds | Partial: prod-only pool line (`Storage/db.py:40`); no shutdown, no workers, no dev line. |
| Unhandled exceptions (type and route, no message) | Missing. uvicorn writes a traceback with the message; only meme-from-text catches and sanitises (`api/meme_from_text.py:47-56`). |
| Slow requests | Missing (only the ranked-search match-count warning, `image_repository.py:191`). |
| DB pool timeouts and connection errors | Missing. `/health` swallows the error (`main.py:215`). |
| Missing image file for a known id | Present, unsampled, id unbounded (`api/images.py:457`). |
| Rate-limit and body-limit rejections (sampled) | Missing (`middleware.py`, `abuse.py`). |
| Bug-report receipt (size, no content) | Missing (`api/bug_reports.py`). |
| Capability/policy failures | Missing (`stage_policy.py:157`). |
| Scheduler and batch spawns (dev) | Present (`scheduler.py`, `batch_subprocess.py`); no durations. |
| `sync.*` gate and release steps | Release: print lines, no timestamps. Gate: failure-only stderr, no persistent log. |

## Findings for O5 (ordered)

1. Escaping exceptions on routes other than meme-from-text are logged by uvicorn with message and traceback; a SQLAlchemy error's text includes
   bind parameters, so a failing `/api/images` search or `/api/recommendations` (`q`) could write the searched text to the log. An app-level
   `Exception` handler that logs `type(exc).__name__`, route template and request id, and returns a fixed 500, closes this. (Derived from the
   code and the comment at `api/meme_from_text.py:49-51`; not verified by running.)
2. `library_metrics.py:28` and `ingestion_metrics.py:26` log `%r` of the exception: change to the type name.
3. `images.py:453,457` and `image_store.py:74` log client-controlled strings: bound and escape, or sample.
4. Dev-only modules with `logger.exception`/`exc_info=True` (`inbox_upload_service.py`, `comics.py`, `comics_pipeline_trigger.py`, `recut_files.py`) are
   not on prod and write no search text; low priority, but `inbox_upload_service.py:342` logs a client file name.
5. Gaps to add on prod: request id, slow-request line, pool timeout/connection error line, 429/413 rejections (sampled), bug-report receipt (size only),
   policy 404 (sampled, template only), shutdown and startup lines with worker count.
6. `sync` prints and `sync.release` include exception messages; acceptable for operator tools, but `diag.message_primary` must stay out of anything persisted.
