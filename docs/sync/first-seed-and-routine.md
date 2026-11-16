# First seed and the routine sync

Both flows run by hand on the dev workstation, one environment at a time. `<env>` is `it`, `metal` or `general`; do `it` first (the
canary: smallest), then `metal`, then `general`. Commands are explained in [commands.md](commands.md); failures in
[troubleshooting.md](troubleshooting.md).

## Before either flow

- The stack of this environment is up on the VPS and released at the commit you are running from (the working tree's `git rev-parse HEAD`
  must equal the database marker `memes.revision`; step H10). The release steps are in [deploy/prod/README.md](../../deploy/prod/README.md).
- Workstation setup is done: `environments/.env.<env>.sync` (two passwords), the three `ssh` host aliases of the environment, and the
  tracked settings `sync.dev.database` and `sync.prod.api_url` filled ([prod-operations.md](prod-operations.md)).
- `python -m sync.doctor --env <env>` exits 0. (H6, the gate step, shows `skipped:no_transport` in the doctor; the first real files run
  proves the gate.)
- No dev batch job is running for this environment (a running batch blocks the data step; `--ignore-running-batches` lifts it, but the
  digests of a half-written image are then at risk of being compared mid-change).

## First seed (empty prod database)

State after provisioning: the database marker `memes.state` is `seeding` (set by `provision.sh`). The normal mode refuses `seeding`
and `--first-seed` refuses `serving`. The first seed uses the same image-batch path as a routine promote, so it is **resumable by
running the same command again**; there is no wipe mode and none is needed.

1. **Release the schema** on the empty prod database (release runbook steps 1 to 3 in `deploy/prod/README.md`): `sync.release` migrates,
   applies the grants and verifies them; then the backend starts and `memes.revision` is set.
2. **Optional, faster load (owner step on the VPS):** drop the HNSW and GIN trigram indexes first, saving their definitions. The sync
   writer cannot do DDL. Without this the load still works, just slower.
3. **Dry run on dev:** `python -m sync.run --env <env> --first-seed`. Read `Storage/sync-state/<env>/runs/<run id>/report.md`:
   - handshake H1 to H10 all ok, H9 is the `seeding` check;
   - files section: everything `added`, plus `skipped_with_reason` entries (missing file, name problem, hash mismatch) to look at;
   - data section: new images and rows per table, the completeness floor (new images without `embeddings` or `ocr_texts` are held back),
     `waiting_for_files` (data that depends on files the same run adds);
   - guards: mass change does not apply to a first seed; zero images and dev-emptied still do;
   - the estimate (about 11 minutes for the whole files part of `general` at 50 Mbit/s, measured on loopback only, so treat it as a lower bound).
4. **Apply:** `python -m sync.run --env <env> --first-seed --apply --plan-hash <hash>` (`--yes` is accepted only together with `--plan-hash`), then type `<env> <prod database>` when asked.
   Order inside the run: handshake, pull (empty prod, so nothing to pull; `sync.pull` has a guard for exactly this, see [pull.md](pull.md)), files,
   data, verify. If it is interrupted (tunnel loss, Ctrl-C, a killed laptop), run the same command again: committed file batches and image
   batches stay, the next plan continues from the digests. A failed batch rolls back and stops the run (exit 1).
   Note: on a brand-new prod the pull step sees empty class P tables. If dev's own class P tables hold rows (dev activity, which is
   disposable), the pull's `empty_source` guard is tripped and the combined apply refuses (exit 3). If you really want dev's mirror
   replaced by the empty prod, add `--allow-empty-source`, or run `sync.files promote` and then `sync.promote --first-seed` on their own, which do not involve the pull. (Unconfirmed against a real first
   seed: the first-seed behaviour of the whole `sync.run` is being covered by tests while this page was written.)
5. **Owner step on the VPS: rebuild the indexes** (the promote never does DDL): recreate the HNSW and GIN indexes from the saved
   definitions with a raised `maintenance_work_mem` (a 43k x 1024-d HNSW build took 149 s at 64 MB and 30 s at 1 GB), or `REINDEX INDEX CONCURRENTLY`
   if they were kept. The promote report for a first seed (`--first-seed`) carries one informational entry `rebuild_indexes:<table>` (detail `missing=<n>`) per table that lacks an HNSW or GIN index of
   the model, or a single `rebuild_indexes` (`all_present`) when none is missing; the console repeats it as "owner step: rebuild the HNSW and GIN indexes of <tables>".
6. **Owner step, last:** `ALTER DATABASE memes_<env> SET memes.state = 'serving'` (the exact `exec -u postgres db psql` call is in the release runbook).
   The sync tools never change this marker: the promote prints "owner step, last: ALTER DATABASE ... SET memes.state = 'serving'" after a first seed and does not run it.
7. **Verify:** `python -m sync.verify --env <env> --deep-files` (exit 0 expected; while seeding, the index check S5 is relaxed, after step 6 it is strict).
8. Confirm the backup timer has run once the data exists ([prod-operations.md](prod-operations.md)); only class P tables are backed up, class D is rebuilt by a promote.

## Routine cycle (prod is serving)

The cycle is: **pull, dry run, review, apply, verify.** `sync.run` does all of it; run the steps alone only for rehearsal or repair.

1. **Release first if code or schema changed** (migration additive, grants verified, `memes.revision` set). A promote refuses a prod whose
   `alembic_version` or `memes.revision` differs from the dev working tree (H10): check out the released commit before syncing.
2. **Dry run:** `python -m sync.run --env <env>`. It plans the pull (what prod readers did since the last pull), the files, the data and a
   verify of the drift. Exit 0, a report folder, a plan hash and the apply command are printed.
3. **Review `report.md`** (open `report.json` for complete id lists):
   - *Guards:* any tripped guard means the apply would refuse. Understand it before lifting it (mass change, regression, model change,
     shrink, growth).
   - *Overwritten:* images with the changed digest groups. A burst of one group (for example `clip` on many images) means a rebuild on dev.
   - *Skipped with reason:* files and rows held back; fix on dev and re-run later.
   - *Left behind:* informational; things you removed or flagged on dev that stay on prod because removals are deferred.
   - *Pull:* bug reports to fetch, ratings mode, truncated texts.
4. **Apply:** `python -m sync.run --env <env> --apply --plan-hash <hash>` and type `<env> <prod database>`. The hash is bound to the combined plan
   (pull part structural). A mismatch (exit 3) means something changed since the dry run: review a fresh dry run.
5. **Verify:** the last step of the run is `sync.verify`; also run `python -m sync.verify --env <env>` on its own in a daily glance (growth
   alarms A1 to A3, disk, backup age) and `--deep-files` after unusual events.
6. **Interruption:** run the same command again.

### Running the steps separately

| Step | Command | Note |
|---|---|---|
| pull | `python -m sync.pull --env <env>` then `--apply --plan-hash H` | confirmation `<env> <dev database>` |
| files | `python -m sync.files promote --env <env>` then `--apply --plan-hash H` | writes `files-promoted.json` |
| data | `python -m sync.promote --env <env>` then `--apply --plan-hash H` | needs the files report of the apply above (max age 2 h) |
| verify | `python -m sync.verify --env <env>` | read only |
| after the run | `python -m sync.promote --env <env> --verify-only` | re-checks prod against what the last promote promised |

Always files before data when adding. `sync.promote` does not run without a fresh `files-promoted.json`.

### Things the routine does not do

- It removes nothing from prod (decision 27). To take an image off prod today, nothing in this toolset does it; that waits for removals.
- It does not carry dev-made activity (searches, saves, impressions made on dev) to prod; dev's class P tables are a disposable mirror of prod's.
- It does not carry class X data (comics, statistics, duplicates, trends). A built comics reaches prod as an ordinary image.
- A taxonomy or settings change is a code deploy (release runbook), not a promote.
