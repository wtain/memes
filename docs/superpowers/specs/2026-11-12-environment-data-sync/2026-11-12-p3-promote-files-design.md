# P3: promote files, dev to prod (design)

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (registry, built), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`), decisions log in `README.md`.
Pairs with: P2 (consumes the report this project writes), P4 (shares the transport and the gate), P5 (provisions the prod user and installs the gate).

## Intent

Put the image files of the promotable images on prod's disk, verified and atomically visible, before P2 writes their rows; and, after P2 has
retired rows, take the files of retired images off the serving directory without destroying them at once. Resumable, safe to kill at any point,
and unable to touch anything on prod except the three directories it owns.

## Goals / non-goals / anti-goals

- Goals: a manifest from the promotable rows; a cheap diff against prod's directory; batched upload with verification; an atomic publish per file
  (`rename(2)`); a quarantine instead of deletion; a report file P2 can trust; one transport abstraction shared with P4 that can be tested without ssh.
- Non-goals: database rows (P2), the pull of bug reports (P4), creating the prod user or the directories (P5), a Windows rsync, compression.
- Anti-goals: a row visible without its file; a partial file under its final name; a file copied from a directory (only files named by promotable rows);
  an empty or truncated manifest quarantining the corpus; a gate that gives the dev key a shell.

## Components

```
sync/files/manifest.py   promotable rows -> {filename: (size, sha256)}; refuses duplicates by case or Unicode normalisation
sync/files/diff.py       manifest x remote listing x ledger -> missing / changed / orphan sets (pure)
sync/files/batches.py    plans batches (150-500 MB, name-sorted), builds the tar (stdlib tarfile, PAX, UTF-8 names)
sync/files/ledger.py     {filename: content_hash} of what the last successful run pushed; atomic JSON write
sync/files/transport.py  Transport protocol: SshTransport (ssh.exe + scp, full path to Windows OpenSSH), LocalTransport (tests)
sync/files/promote.py    the commands (below)
deploy/prod/memesync-gate.sh   the forced-command gate installed by P5 (verbs below); lives in the repo, reviewed, versioned
```

`sync/files/*` may use the standard library and `psycopg2` only for reading the promotable rows (dev, read only). Registry core stays stdlib-only (P1 test).
Location of `deploy/prod/` is a choice to check at implementation (nothing like it exists yet).

## Commands

`python -m sync.files promote --env {metal,general,it} [--apply] [--deep] [--batch-mb N] [--force-orphans]`
`python -m sync.files retire  --env ... [--apply]`

Dry run is the default of both. Configuration: the ssh host alias only (`PROD_SSH_HOST`, an entry in the user's `~/.ssh/config`, never in the repo); prod paths
live in the gate on the VPS, so the client sends names, not paths.

### Gate verbs (the entire remote surface; each reads NUL-separated names on stdin, writes a plain result on stdout)

| Verb | Does | Notes |
|---|---|---|
| `handshake` | prints device ids of STAGING and BASE_PATH, free disk, gate version; sweeps STAGING entries older than 6 h | refuses (exit 3) if the two directories are on different devices |
| `list` | `find BASE_PATH -maxdepth 1 -type f ! -name '.*' -printf '%P\t%s\n'` | flat regular non-hidden files only |
| `hash [--names]` | `sha256sum` of the named files (or all, `xargs -P4`), for `--deep` | |
| `receive <id>` | moves `STAGING/<id>.tar.part` to `.tar`, extracts into `STAGING/<id>/`, refuses names with `/`, `..`, or a leading dot | extraction is name-validated before it writes |
| `verify <id>` | `sha256sum -c --quiet` against the sums file sent on stdin | any failure aborts the batch before a rename |
| `commit <id>` | `mv -T STAGING/<id>/x BASE_PATH/x` for each file, then removes the batch directory | the only operation that makes a file visible |
| `retire` | moves named files from BASE_PATH to `RETIRED/<date>/` | refuses a path outside BASE_PATH; never `rm` |
| `purge` | deletes `RETIRED/<date>/` older than 14 days | |

The upload itself is `scp` of one tar to `STAGING/<id>.tar.part` (the gate admits `scp -t` for that directory only). SFTP is not path-restricted
(S4), so confinement is unix permissions of the `memesync` user: it owns STAGING and RETIRED, may write BASE_PATH, owns nothing else. Names are
validated on both ends.

## `promote` flow

1. **Promotable set** from dev through `PROMOTABLE_IMAGES_SQL` (read only). Resolve each file in dev `BASE_PATH` (flat; subdirectories are never scanned).
   `content_hash` NULL: hash the file. A mismatch between the column and the file, or a missing file, aborts before anything is sent (a dev data error).
2. **Remote state:** `handshake`, then `list` (0.2 s for 32,000 files). The diff is in memory (3 ms).
3. **Diff:** `missing` (not on prod), `size-changed`, `ledger-changed` (the manifest hash differs from what the last run pushed; catches same-size changes),
   `hash-changed` (only with `--deep`: remote `hash` of all files), `orphans` (on prod, not in the manifest). A manifest with duplicate names modulo case or
   NFC/NFD is rejected.
4. **Guards:** orphans above `max(50, 2 %)` of the prod listing, or a manifest smaller than half of prod's listing, or an empty manifest: stop (exit 3) unless `--force-orphans`.
   Orphans are only reported by `promote`; `retire` handles them.
5. **Dry run** prints counts, bytes, batches and the guard decisions and stops.
6. **Apply, per batch** (about 150 to 500 MB, name-sorted so a re-run reproduces the batches): build the tar locally (PAX, UTF-8 names); `scp` to `<runid>_<n>.tar.part`; `receive`;
   `verify` with a sums file generated from the manifest; `commit`. On a failed verify the batch is discarded, the run stops (exit 1), nothing was made visible. After each
   committed batch the ledger is rewritten atomically.
7. **Final check:** a fresh `list` must contain every manifest name with the manifest size. Then the **report** is written (below). If anything is missing the report is not written.
8. A killed run is repaired by the next one: staging debris older than 6 h is swept by `handshake`, and the diff skips whatever is committed. Worst case: one batch is re-sent.

### The report P2 consumes

`<state dir>/runs/<runid>/files-promoted.json`, written only after the final check:

```json
{ "run_id": "...", "env": "general", "finished_at": "2026-11-12T20:00:00Z", "gate_version": "1",
  "images": [ {"id": "<uuid>", "filename": "...", "size": 123, "content_hash": "<sha256>"} ],
  "manifest_sha256": "<hash of the sorted manifest>" }
```

It lists every promotable image whose file is verified on prod. P2 treats an image as shippable only if it is in the newest report of the environment that is not older
than `--max-report-age` (default 24 h); an image not listed is skipped and reported by P2, not committed. State directory: `SYNC_STATE_DIR`, default
`Storage/sync-state/<env>/` (git-ignored, added to `.gitignore`). The ledger lives in the same directory.

## `retire` flow (after P2 has committed the retirement of rows)

Inputs: a fresh `list`, the manifest (the promotable set), P2's retire list (ids and filenames from the plan output) if present. Files on prod that are not in the manifest are orphans.
1. Refuse unless P2 has retired the corresponding rows: the tool lists orphans and checks them against the P2 plan's retire list; an orphan not in that list needs `--force-orphans` (it may be a stray file; it is still only quarantined).
2. Same guard as above (2 %).
3. `retire` verb moves the files into `RETIRED/<date>/`. `purge` runs at the end for entries older than 14 days. Rows first, files last: this order is the mirror image of adding.

## Changed files

A file with the same name and a new content hash (a WebP converted in place, a recut) is replaced atomically by `commit`; between that moment and P2's row update the prod row still carries
the old `content_hash`, which is not used for serving. The immutable one-year cache of the image endpoint (S5) means **bytes under an existing id must not change**; the manifest builder therefore
reports every `ledger-changed` or `hash-changed` file as a warning and `--apply` requires `--allow-replace` for them (the intended route for changed bytes is a new id). Default behaviour is to refuse, not to replace.

## Failure behaviour

| Event | Result |
|---|---|
| ssh drops during `scp` | `.tar.part` stays; the next run starts that batch again (batch ids are derived from the manifest slice, not time) |
| kill between `receive` and `commit` | staging debris, swept after 6 h; no file visible |
| kill during `commit` | some files visible, no rows yet (P2 not run); next run finds the rest `missing` |
| `verify` fails | batch discarded, run exits 1, nothing visible |
| Prod disk full | `handshake` reports free space against the plan; a batch is not started that does not fit |
| Dev file changes during the run | the sums file is from the manifest hash taken at the start; `verify` fails, batch discarded |

## Tests

1. **Unit:** manifest builder (NFC/NFD, case duplicates, Cyrillic, emoji, NULL hash, missing file), diff (all sets), batch planner determinism, tar names, ledger atomicity, guards, report writing, name validation.
2. **LocalTransport integration** (no ssh): the gate script run through `bash` against temp directories (a Windows dev machine has Git Bash; the CI runner is Linux): full promote, idempotent re-run,
   interrupted batch (injected), verify failure, orphan quarantine with the 2 % guard, truncated manifest refused, changed-file refusal and `--allow-replace`, a file with a hidden name ignored.
3. **Docker ssh test** (marker `docker`, local only, throwaway container on 127.0.0.1:52222 with a throwaway key, as in S4): the same promote over a real ssh and scp.
4. **Gate test:** every verb with hostile input (`../`, absolute paths, newlines in names, a dot-file, a directory), proving it refuses.
5. **Not covered:** real WAN, real VPS, antivirus on dev, concurrent reads while renames happen (S4 "not verified").

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `manifest.py`, `diff.py`, `ledger.py` with unit tests | P1 |
| T2 | `batches.py` (planner, tar) with unit tests | T1 |
| T3 | `deploy/prod/memesync-gate.sh` and its hostile-input tests | none |
| T4 | `transport.py` (Local and Ssh), handshake and listing | T3 |
| T5 | `promote` (dry run, apply, report) | T1, T2, T4 |
| T6 | `retire` and `purge` | T5 |
| T7 | Docker ssh test, Windows path gotchas (full path to `C:\Windows\System32\OpenSSH\ssh.exe`, not Git's) | T5 |
| T8 | Docs (`docs/sync/files.md`), `.gitignore` entry, CLAUDE.md pointer | all |

Size: medium. Mode: subagent-driven (T1, T3 in parallel; T2, T4 after; T5 to T7 sequential). One review after T7 by a fresh subagent, axes **security of the gate and file handling** (path traversal, name validation, privilege of
the sync user) and **correctness against the S4 contract**. Live prod: never touched in development.

## Choices made by me (to confirm)

- Module and directory layout (`sync/files/`, `deploy/prod/`), option names, exit codes (same as P2: 0 clean, 2 precondition, 3 guard, 1 other).
- `Storage/sync-state/<env>/` as the default state directory and `PROD_SSH_HOST` as the only client setting.
- Changed bytes under an existing name refused by default (`--allow-replace`), because of the one-year immutable cache.
- The report is the P2 gate (a file, not a prod directory listing) and expires after 24 h.
- Batch ids derived from the manifest slice, so a re-run reuses a half-sent `.tar.part`'s name.

## Risks and what is not settled

- Gate hardening depends on the real sshd (`restrict`, `Subsystem`), which is not known until the VPS exists (P5).
- Whether the backend runs in a container on prod (paths, permissions, `memesync` owning BASE_PATH writes) is a P5 decision with a P3 consequence: the gate and the backend must agree on ownership.
- Cold-disk timings of `--deep` and the first seed on a real network: measured only on loopback.

## Open for the owner

None blocking. The choices above are for review.
