# P4: pull reader activity, prod to dev (design)

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (registry, built), S3 (`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md`, Q7), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`, Q4),
S5 (roles), P3 (transport and gate), decisions log in `README.md` (decisions 6, 9, 10, 11, 13).
Pairs with: P2 (reads the pull-state file this project writes before it may retire images).

## Intent

Bring the readers' activity that accumulates on prod back to the dev database (and bug reports to a dev folder), so that dev sees the real use of the library: searches,
impressions, saves, not-found reports and the owner's ratings. Prod stays the system of record; dev holds a mirror that may be replaced at any time. The pull runs before
every promote, because retiring an image on prod cascades into its ratings.

## Goals / non-goals / anti-goals

- Goals: one consistent snapshot of the six class P tables; a mirror on dev that equals the snapshot, except for ratings of images unknown on prod; bug reports pulled and
  removed on prod only after verification; a pull-state file P2 can trust; dry run by default; idempotent.
- Non-goals: pushing anything to prod, pruning prod (decision 11: prod keeps everything), merging dev-made activity (disposable, decision in the charter), uploads (disabled on
  prod, decision 9), watermark/incremental impression pull in v1 (backlog; full mirror is under 3 s per 100k rows).
- Anti-goals: writing prod; losing the only copy of a rating of an image retired on prod; wiping dev because prod answered empty; logging search text or any row content; a pull
  that half-replaces dev.

## Command

`python -m sync.pull --env {metal,general,it} [--apply] [--allow-empty-source] [--skip-files]`

Dry run by default. Exit codes as P2/P3: 0 clean, 2 precondition, 3 guard, 1 other.

### Connections

- **Prod (read):** `PROD_PULL_DATABASE_URL` in the shell environment, role `sync_reader` (S5: `SELECT` on the P tables and `alembic_version` only), through the same `ssh -L` tunnel
  the owner opens for P2. Opened as one `REPEATABLE READ, READ ONLY` transaction for the whole pull, so the six tables are one consistent snapshot even though several are
  FK-less and linked only by `search_history_id`.
- **Dev (write):** the normal path (`load_env(args.env)`, write-capable `DATABASE_URL`). One dev transaction for all six tables: a failure leaves dev unchanged.
- **Role check on prod:** refuse unless the connected role cannot write any table (checked through `has_table_privilege`) and is not the backend, owner or sync_writer role.

## Preconditions

Equal `alembic_version` on both sides (the P class columns must match); the registry's pulled tables equal the six expected tables (a P1 constant, `sync.registry.pulled_tables()`);
dev has the pgvector-free tables it needs (nothing else to check; the P tables hold no vectors).

## Mirror semantics per table

Source = the prod snapshot. Order of work on dev inside the single transaction:

| Table | Strategy | Notes |
|---|---|---|
| `search_history_tags` | delete all, then copy | child of `search_history`; deleted first, copied last |
| `search_history` | `TRUNCATE`-equivalent via `DELETE`, then `COPY` | UUID ids, no merge |
| `search_not_found` | replace | FK-less |
| `meme_saves` | replace | FK-less |
| `meme_impressions` | replace, then `setval(max(id))` | bigint serial; the dev sequence must pass prod's ids or dev's next insert collides |
| `meme_text_ratings` | staging + filtered upsert + mirror delete | see Ratings |

All via `COPY ... (FORMAT binary)` from the prod read connection into dev (S3 Q7: 400k rows in 2.5 s). `TRUNCATE` is not used (it refuses with FK children and takes a lock that blocks the dev backend);
`DELETE` in one transaction is fine at these sizes, and the 25 % case is irrelevant for a replace. The dev backend can keep running during a pull: readers see the old state until the commit.

### Ratings (the only P table with a foreign key to `images`)

`meme_text_ratings.image_id` references `images` with CASCADE, and the unique key is `(query_hash, image_id)`. The rules (from S3 Q4 and Q7):
1. Rows from prod whose `image_id` does not exist on dev are **skipped and counted** ("image unknown on dev"): dev may have deleted the image before prod learned of it. They are not an error.
2. Rows on dev whose `image_id` is **absent from prod's `images`** (an image retired on prod) are **kept**: the pull must not delete the last copy of a rating of a retired image.
   To know prod's image ids the snapshot also reads `select id from images` (the sync_reader role gets `SELECT (id)` on `images`, a P5 grant to add).
3. All other dev rows are replaced by prod's (inserted, updated where `grade` or text differ, deleted where prod no longer has them: "Forget text" on prod is mirrored, as intended).
4. Per row `IS DISTINCT FROM` upsert; a count of inserted, updated, deleted, kept-retired and skipped-unknown is reported.

## Guards

- Prod snapshot returns **zero rows in every table** while dev has rows: refuse (an empty or wrong database must not wipe dev) unless `--allow-empty-source`.
- A table whose prod count dropped by more than 50 % against the previous pull (from the pull-state file) is a warning in the report, not a refusal (prod keeps everything, so this should never happen; it is a tripwire).
- A pull of an environment is refused if another pull holds the dev-side advisory lock.

## Pull-state file (consumed by P2)

`<state dir>/pull_state.json` (same state directory as P3), written atomically after the dev transaction commits and the bug-report step finishes:

```json
{ "env": "general", "finished_at": "2026-11-12T19:55:00Z", "snapshot_txid": "...",
  "tables": {"meme_text_ratings": {"prod": 38, "inserted": 1, "updated": 0, "deleted": 0, "kept_retired": 0, "skipped_unknown": 0}, "...": {}},
  "bug_reports": {"pulled": 3, "deleted_on_prod": 3} }
```

P2's `--retire` requires a pull-state file for the environment younger than `--max-pull-age` (default 24 h). A dry-run pull never writes it.

## Bug reports (file part)

Uploads are disabled on prod, so there is no inbox to pull; bug reports are the only files. They are kept on prod, size- and rate-limited (decision 13), named `<UTC timestamp>_<uuid><ext>` (unique), written to
prod's `bug_reports` directory. Using the P3 transport and gate:
1. `pull-list`: names, sizes, hashes of the flat non-hidden regular files in prod's bug-reports directory.
2. `pull-tar`: the named files streamed as a tar on the ssh **stdout** (the fast direction on Windows), read with `tarfile` in stream mode, each file's sha256 compared with prod's.
3. Publish into dev's `BUG_REPORTS_PATH` (default `BASE_PATH/bug_reports`, as the backend does) with a no-clobber rename; a same-named file with the same hash counts as received; a same name with a different
   hash (cannot normally happen) is stored with the next `_n` suffix.
4. A ledger `{name: sha256}` (in the state directory) records receipt. Only then `delete-verified` removes the file on prod, re-checking that its hash still equals the received one (a replaced file survives).
5. Crash between publish and delete: the next run finds the file in the ledger, deletes on prod without a second download.
Gate verbs added to P3's list: `pull-list`, `pull-tar`, `delete-verified` (names NUL-separated on stdin, validated like the others, restricted to the bug-reports directory).

## Failure behaviour

| Event | Result |
|---|---|
| Tunnel drops during the table copy | the dev transaction rolls back; dev unchanged; re-run |
| Dev backend writes during the pull | its rows are replaced by the commit (dev activity is disposable); the dev transaction does not lock readers |
| Crash after the dev commit, before the bug-report step | tables are final; the pull-state file is not written, so P2's freshness check fails until a re-run completes |
| Hash mismatch on a bug report | that file is not published and not deleted; the run reports it and continues with the others; exit 1 at the end |

## Privacy

Search text, message text and `meme_text_ratings.query_text` flow to dev, which is the owner's own machine. The tool never prints or logs row values: counts, table names and ids only. The
`--json` report has counts only. Prod keeps the originals; dev's copy can be wiped by the next pull.

## Tests

1. **Unit:** table plan, ratings partition (insert/update/delete/kept-retired/skipped-unknown from small in-memory fixtures), guard logic, pull-state schema and atomic write, ledger, name validation.
2. **Integration** (real Postgres, two databases or schemas, same fixture as P2 T0): the Q7 scenarios of the S3 ADR: replace with new, edited and deleted rows; ratings of an image unknown on dev; ratings of an image
   retired on prod are kept on dev; FK order (`search_history_tags`); `setval` then a default insert on `meme_impressions`; empty-source refusal and `--allow-empty-source`; idempotent second run; dev-made activity replaced;
   snapshot consistency (a writer on the source during the read does not tear the six tables); the sync_reader role cannot write (the tool's executed SQL contains no write against prod).
3. **LocalTransport** (no ssh): bug reports: pull, hash mismatch, publish collision, crash between publish and delete and resume, hidden and in-progress files ignored, replaced-file survives the delete.
4. **Docker ssh test** (marker `docker`, local only): the bug-report pull over a real ssh.
5. **Not covered:** a real network, a real VPS, millions of impression rows (S3: the watermark option is the answer, not built).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `sync/pull/plan.py`: table plan, ratings partition, guards (pure) with unit tests | P1 |
| T2 | Connections, role check, snapshot reader | P2 T0 fixture |
| T3 | Mirror apply for the five simple tables, `setval` | T2 |
| T4 | Ratings strategy (staging, filter, keep-retired) | T3 |
| T5 | Pull-state file and dry-run report | T3 |
| T6 | Gate verbs `pull-list`, `pull-tar`, `delete-verified` and the bug-report step (LocalTransport tests) | P3 T3, T4 |
| T7 | Integration scenarios; Docker ssh test | T3 to T6 |
| T8 | Docs (`docs/sync/pull.md`), CLAUDE.md pointer, P5 grant list (`SELECT (id)` on `images` for `sync_reader`) | all |

Size: medium. Mode: subagent-driven; T1, T6 can start early. One review after T7: **data loss and privacy** (can the pull destroy a rating, wipe dev, leak text into logs, write prod) and
**correctness against the S3 Q7 findings**.

## Choices made by me (to confirm)

- Full mirror every time in v1; impressions watermark is backlog. Revisit when `meme_impressions` approaches millions of rows.
- `PROD_PULL_DATABASE_URL` (role `sync_reader`) distinct from P2's `PROD_SYNC_DATABASE_URL` (role `sync_writer`): the pull cannot write prod even by mistake.
- The retired-image rating rule needs `SELECT (id)` on prod `images` for `sync_reader` (an addition to the S5 role list).
- Bug reports are deleted on prod after a verified pull (decision 13 read literally: dev pulls the files, prod copies are deleted only after verification).
- Pull-state freshness of 24 h before a retire.
- Dev's own ratings of images that exist on prod but have no prod counterpart are deleted (mirror); only ratings of images **absent** from prod survive.

## Risks and what is not settled

- A rating made on dev (disposable by decision) of an image that still exists on prod is replaced: intended, but it is the one place where the owner's dev-side rating is lost; the report counts such rows so a surprise is visible in the dry run.
- `search_history` deletions on prod ("Forget text") are mirrored, so a message deleted between pulls disappears from dev too; the pulled copy is not an archive. If the owner wants an archive, it is a different feature.
- The 50 % tripwire needs the previous pull-state file; the first pull has no baseline.

## Open for the owner

None blocking. The choices above are for review.
