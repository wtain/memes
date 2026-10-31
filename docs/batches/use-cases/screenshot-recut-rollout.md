# Runbook: first live rollout of the screenshot recut

Status: draft (written 2026-10-31, never run). Design: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-31-rollout-prep-design.md`.
Use case: `screenshot-recut.md`. Nothing here runs by itself: every step needs the owner's explicit go-ahead, one environment at a
time, in this order: **general, then metal, then it**. Stop at the first failing step of an environment; do not start the next one.

The commands are PowerShell from the repo root unless a step says `Storage/`. `$py` is `.\.venv311\Scripts\python.exe`.

## The migrations

A behind environment receives these seven in one `alembic upgrade head`, in this order. The popularity and saves migrations sit
between the recut ones. All seven only add tables, columns, constraints and indexes and rewrite no existing row.

1. `b8d4f1a6c935` R1: `image_cuts`, `stale` columns
2. `c5e2a9d7b140` M1: `process_events`
3. `d7a3b9e2c451` R3a: `image_cuts.failure_reason`
4. `a4f7c1e92b68` popularity: `images.popularity`, `popularity_absorptions`
5. `c8e2f5a1d093` popularity: the `adjustment` source
6. `e9c4a7b3d812` R6: the detected box on `image_cuts`
7. `d5b8e3a07c41` saves: `meme_saves`

`tools/rollout_preflight.py` reads the same chain from `Storage/alembic`; a test fails when this list and the chain differ.

## Step 0: before anything

1. Run `tests/integration/` on `main` against the test database and read the result.
2. Find out which code each always-on backend runs. They start with `--reload`; if one runs from the main checkout, the merged code
   already serves against an unmigrated database, and the preflight's revision line shows the gap. Write the answer down here.
3. Set the read-only URL for the environment in the shell (the value is never printed):
   `$env:DATABASE_URL_READONLY = (Select-String -Path environments\.env.general -Pattern '^DATABASE_URL_READONLY=').Line.Split('=',2)[1]`

## Per environment (general, then metal, then it)

Replace `general` and port `5434` (metal 5432, it 5436) as needed.

1. **Preflight before.**
   `& $py -m tools.rollout_preflight --env general --backup-dir Storage\backups`
   Expect `NOT READY` only because of the seven pending revisions (and `WARN` lines for the missing objects they create).
   Anything else that fails (a running batch job, no free space, an unwritable `BASE_PATH`) must be fixed first.
   *If this fails:* nothing changed; fix and rerun.
2. **Backup.** From `Storage/` (asks for the password):
   `pg_dump -h localhost -p 5434 -U ocr -Fc -f ./backups/ocrdb-YYYY-MM-DD-general.dump ocrdb`
   then `pg_restore --list ./backups/ocrdb-YYYY-MM-DD-general.dump | Select-Object -First 5` and note
   `(Get-Item ./backups/ocrdb-YYYY-MM-DD-general.dump).Length`. Record the revision from the preflight in your notes.
   *If this fails:* nothing changed; do not continue without a listed dump.
3. **Quiet the writers.** No batch job running (the preflight checked it), nobody on `/duplicates`, `/ingestion` or `/admin/recut`.
4. **Upgrade.** From `Storage/`, with that environment's variables loaded as in CLAUDE.md ("Database migrations"): `alembic upgrade head`.
   *If this fails midway:* Postgres DDL is transactional, so the failing revision rolls back by itself; read the error, rerun the
   preflight to see where it stopped. Never edit `alembic_version` by hand.
5. **Preflight after.** Same command as step 1. Expect `READY` and `at head d5b8e3a07c41`.
6. **Restart the backend** of that environment on matching code. Where an image is used, rebuild it first: it now needs Pillow,
   `batch/utils/{__init__,recut_cut,file_hash}.py` and write access to `BASE_PATH`. Smoke test:
   `/api/diagnostics/health`, `/api/images?limit=1`, `GET /api/admin/batches/metrics?process=recut`, and `/admin/batches` showing
   all four metric panels.
   *If the smoke test fails and nothing new was written:* `alembic downgrade` to the revision noted in step 2 (the downgrades drop
   what was added; `c8e2f5a1d093` refuses once an `adjustment` popularity row exists). If that fails, restore the dump.
   *If anything fails after the app wrote new data:* restore the dump of that environment only; the cost is the writes since the backup.

## First recut pass (general only)

`detect_recut_crops --limit 500`, then `recut_pipeline`, then review a few dozen cuts on `/admin/recut`. Two things have never run
for real and are watched: the `refresh_recut_cuts` GPU steps (OCR, descriptions), and the box-adjust UI in a real browser (a tall
and a wide image, touch). *If a `recut_pipeline` step fails:* trigger it again; each step selects only what is still waiting.

## What to record afterwards

The first live numbers, from read-only queries: the approve rate by confidence band (the R7 table), and the `ingest_tier_*` and
`library_duplicates` panels once `/ingestion` and `/duplicates` have been used. Then decide scheduling (see `screenshot-recut.md`).
